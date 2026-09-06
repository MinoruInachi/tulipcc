// Tab5 built-in camera. See camera_tab5.h for the shape of the API.
//
// Bring-up is the BSP's: bsp_camera_start() powers the sensor through the
// PI4IOE5V6408 expander (BSP_CAMERA_EN), lends esp_video the BSP's I2C bus for
// SCCB, and esp_video_init() probes the sensor, picks its default format
// (RAW8 1280x720 30fps) and, with the ISP pipeline controller built in,
// starts the task that runs auto exposure / white balance against the ISP.
// After that the device is a V4L2 capture node: S_FMT to ask the ISP for
// RGB565, three MMAP buffers, STREAMON, then DQBUF/QBUF in a loop.
//
// The loop lives in its own task on core 0, and so does every step that
// allocates an interrupt: esp_video_init(), the V4L2 setup, the JPEG engine.
// An ESP-IDF interrupt is serviced on the core that allocated it, and the
// first version of this file did all of that from the MicroPython task on
// core 1 -- which put the CSI, ISP, 2D-DMA and JPEG interrupts on the same
// core that writes littlefs. A flash write stalls the other core and masks
// non-IRAM interrupts, and with those ISRs in the mix the second or third
// camera_capture() to /user in a row deadlocked in shared_intr_isr(), core 1
// spinning on the interrupt allocator's lock until the interrupt watchdog
// fired. Core 0 already owns the display, touch and USB interrupts; the
// camera's belong beside them. Twenty captures in a row pass this way.
//
// The loop always keeps the most recent DONE buffer dequeued ("held") and
// requeues the one it replaces, so a reader on the MicroPython task takes
// s_lock, copies out of the held buffer, and lets go -- it never waits on the
// sensor and never sees a buffer the DMA is still filling. Readers copy first
// and convert or encode outside the lock, because a PNG encode is seconds and
// holding the lock that long would leave the driver a single buffer to
// capture into.

#include "camera_tab5.h"

#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/time.h>

#include "esp_log.h"
#include "esp_check.h"
#include "esp_timer.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"

#include "bsp/m5stack_tab5.h"
#include "esp_video_init.h"
#include "esp_video_device.h"
#include "esp_video_ioctl.h"
#include "linux/videodev2.h"
#include "esp_cam_sensor_types.h"
#include "driver/jpeg_encode.h"

#include "../../../shared/display.h"
#include "../../../shared/lodepng.h"
#include "../../../shared/polyfills.h"

static const char *TAG = "tab5_camera";

#define CAM_DEV ESP_VIDEO_MIPI_CSI_DEVICE_NAME
// Four rather than three: one is held for readers, the driver keeps one
// reserved for itself, and the other two give a reader that hangs on to the
// held frame for a while (camera_bg() converts straight out of it, 40-100 ms)
// two frame times before the sensor has nowhere to put the next one.
#define CAM_BUFFERS 4
#define CAM_W TAB5_CAMERA_WIDTH
#define CAM_H TAB5_CAMERA_HEIGHT
#define CAM_FRAME_BYTES (CAM_W * CAM_H * 2)
#define CAM_DQBUF_TIMEOUT_MS 500
// esp_video_init() runs on this stack too: sensor probe over SCCB, format
// tables, the ISP pipeline bring-up and its logging.
#define CAM_TASK_STACK 8192
// Between the display bridge (idle) and the touch task (idle+3): a frame
// arriving is a few ioctls of bookkeeping, and it may as well not queue behind
// a full compositor pass.
#define CAM_TASK_PRIORITY (tskIDLE_PRIORITY + 2)
#define CAM_TASK_CORE 0

#if BYTES_PER_PIXEL != 1
#error "camera_tab5.c writes the BG plane as RGB332"
#endif

static bool s_video_inited = false;
static uint16_t s_chip_id = 0;
static int s_fd = -1;
static uint8_t *s_buf[CAM_BUFFERS];
static size_t s_buf_len[CAM_BUFFERS];
static int s_buf_count = 0;

static SemaphoreHandle_t s_lock = NULL;      // guards s_held and the buffer it names
static SemaphoreHandle_t s_frame_sem = NULL; // given once per new frame
static TaskHandle_t s_task = NULL;
static volatile bool s_run = false;
static volatile bool s_task_alive = false;
// Start/stop hand-off with the task: it reports how its setup went here and
// gives s_done; stop waits on the same semaphore for the teardown.
static SemaphoreHandle_t s_done = NULL;
static volatile esp_err_t s_task_err = ESP_OK;

static volatile int s_held = -1;
static volatile uint32_t s_seq = 0;
static volatile uint32_t s_frames = 0;
static volatile uint32_t s_errors = 0;
static volatile uint32_t s_dropped = 0;  // frames requeued unread because a reader held the lock
static volatile uint32_t s_fps = 0;
static uint32_t s_fps_window_frames = 0;
static int64_t s_fps_window_start_us = 0;

static bool s_hflip = false;
static bool s_vflip = false;

// RGB565 -> RGB332 through a 64 KB table, built once from the same color_332()
// the rest of the BG drawing uses, so a camera pixel lands on the same palette
// entry a bg_png() of the same colour would.
static uint8_t *s_lut_332 = NULL;

static jpeg_encoder_handle_t s_jpeg = NULL;

static const char *sensor_name(uint16_t pid)
{
    switch (pid) {
    case 0xeb52: return "SC202CS";
    case 0: return "none";
    default: return "unknown";
    }
}

static esp_err_t ensure_lut(void)
{
    if (s_lut_332 != NULL) {
        return ESP_OK;
    }
    uint8_t *lut = malloc_caps(65536, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (lut == NULL) {
        return ESP_ERR_NO_MEM;
    }
    for (uint32_t v = 0; v < 65536; v++) {
        uint8_t r5 = (v >> 11) & 0x1f;
        uint8_t g6 = (v >> 5) & 0x3f;
        uint8_t b5 = v & 0x1f;
        lut[v] = color_332((r5 << 3) | (r5 >> 2), (g6 << 2) | (g6 >> 4), (b5 << 3) | (b5 >> 2));
    }
    s_lut_332 = lut;
    return ESP_OK;
}

// One V4L2 control by id. esp_video routes anything but its own
// V4L2_CTRL_CLASS_ESP_CAM_IOCTL class by id alone, so the class is nominal.
static esp_err_t set_ctrl(uint32_t cls, uint32_t id, int value)
{
    struct v4l2_ext_control control = {
        .id = id,
        .value = value,
    };
    struct v4l2_ext_controls controls = {
        .ctrl_class = cls,
        .count = 1,
        .controls = &control,
    };
    if (ioctl(s_fd, VIDIOC_S_EXT_CTRLS, &controls) != 0) {
        return ESP_FAIL;
    }
    return ESP_OK;
}

static int get_ctrl(int fd, uint32_t cls, uint32_t id)
{
    struct v4l2_ext_control control = { .id = id };
    struct v4l2_ext_controls controls = {
        .ctrl_class = cls,
        .count = 1,
        .controls = &control,
    };
    if (fd < 0 || ioctl(fd, VIDIOC_G_EXT_CTRLS, &controls) != 0) {
        return -1;
    }
    return control.value;
}

static esp_err_t set_flip_ctrl(uint32_t id, bool on)
{
    return set_ctrl(V4L2_CTRL_CLASS_USER, id, on ? 1 : 0);
}

static esp_err_t video_init_once(void)
{
    if (s_video_inited) {
        return ESP_OK;
    }
    esp_err_t err = bsp_camera_start(NULL);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "bsp_camera_start: %s", esp_err_to_name(err));
        return err;
    }
    s_video_inited = true;
    return ESP_OK;
}

static void release_buffers(void)
{
    for (int i = 0; i < s_buf_count; i++) {
        if (s_buf[i] != NULL) {
            munmap(s_buf[i], s_buf_len[i]);
            s_buf[i] = NULL;
        }
    }
    s_buf_count = 0;
}

// Everything from here to the end of camera_task() runs on core 0.

static esp_err_t jpeg_engine_init_once(void)
{
    if (s_jpeg != NULL) {
        return ESP_OK;
    }
    jpeg_encode_engine_cfg_t eng = {
        .intr_priority = 0,
        .timeout_ms = 1000,
    };
    esp_err_t err = jpeg_new_encoder_engine(&eng, &s_jpeg);
    if (err != ESP_OK) {
        // Stills still work through PNG; report it and carry on.
        ESP_LOGW(TAG, "jpeg engine: %s", esp_err_to_name(err));
        s_jpeg = NULL;
    }
    return ESP_OK;
}

// Open the device, pick RGB565 at the sensor's size, map three buffers and
// start the stream. On failure the fd is closed and buffers released.
static esp_err_t stream_setup(void)
{
    ESP_RETURN_ON_ERROR(video_init_once(), TAG, "video init");
    jpeg_engine_init_once();

    esp_err_t err = ESP_FAIL;
    const int type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    int fd = open(CAM_DEV, O_RDONLY);
    if (fd < 0) {
        ESP_LOGE(TAG, "open %s failed", CAM_DEV);
        return ESP_ERR_NOT_FOUND;
    }
    s_fd = fd;

    // Who is on the other end of the CSI lanes.
    {
        esp_cam_sensor_id_t chip_id = {0};
        struct v4l2_ext_control control = {
            .id = ESP_CAM_SENSOR_IOC_G_CHIP_ID,
            .p_u8 = (uint8_t *)&chip_id,
            .size = sizeof(chip_id),
        };
        struct v4l2_ext_controls controls = {
            .ctrl_class = V4L2_CTRL_CLASS_ESP_CAM_IOCTL,
            .count = 1,
            .controls = &control,
        };
        if (ioctl(fd, VIDIOC_G_EXT_CTRLS, &controls) == 0) {
            s_chip_id = chip_id.pid;
        }
    }

    struct v4l2_format format = {
        .type = type,
        .fmt.pix.width = CAM_W,
        .fmt.pix.height = CAM_H,
        .fmt.pix.pixelformat = V4L2_PIX_FMT_RGB565,
    };
    if (ioctl(fd, VIDIOC_S_FMT, &format) != 0) {
        ESP_LOGE(TAG, "S_FMT RGB565 %dx%d failed", CAM_W, CAM_H);
        err = ESP_ERR_NOT_SUPPORTED;
        goto fail;
    }

    struct timeval dq_timeout = {
        .tv_sec = 0,
        .tv_usec = CAM_DQBUF_TIMEOUT_MS * 1000,
    };
    ioctl(fd, VIDIOC_S_DQBUF_TIMEOUT, &dq_timeout);

    struct v4l2_requestbuffers req;
    memset(&req, 0, sizeof(req));
    req.count = CAM_BUFFERS;
    req.type = type;
    req.memory = V4L2_MEMORY_MMAP;
    if (ioctl(fd, VIDIOC_REQBUFS, &req) != 0) {
        ESP_LOGE(TAG, "REQBUFS failed");
        err = ESP_ERR_NO_MEM;
        goto fail;
    }
    for (int i = 0; i < CAM_BUFFERS; i++) {
        struct v4l2_buffer buf;
        memset(&buf, 0, sizeof(buf));
        buf.type = type;
        buf.memory = V4L2_MEMORY_MMAP;
        buf.index = i;
        if (ioctl(fd, VIDIOC_QUERYBUF, &buf) != 0) {
            ESP_LOGE(TAG, "QUERYBUF %d failed", i);
            err = ESP_FAIL;
            goto fail;
        }
        s_buf[i] = (uint8_t *)mmap(NULL, buf.length, PROT_READ | PROT_WRITE, MAP_SHARED, fd, buf.m.offset);
        if (s_buf[i] == NULL) {
            ESP_LOGE(TAG, "mmap %d failed", i);
            err = ESP_ERR_NO_MEM;
            goto fail;
        }
        s_buf_len[i] = buf.length;
        s_buf_count = i + 1;
        if (ioctl(fd, VIDIOC_QBUF, &buf) != 0) {
            ESP_LOGE(TAG, "QBUF %d failed", i);
            err = ESP_FAIL;
            goto fail;
        }
    }

    // Mirror settings survive a stop/start.
    set_flip_ctrl(V4L2_CID_HFLIP, s_hflip);
    set_flip_ctrl(V4L2_CID_VFLIP, s_vflip);

    if (ioctl(fd, VIDIOC_STREAMON, &type) != 0) {
        ESP_LOGE(TAG, "STREAMON failed");
        err = ESP_FAIL;
        goto fail;
    }
    ESP_LOGI(TAG, "streaming %dx%d RGB565 from %s (0x%04x)", CAM_W, CAM_H,
             sensor_name(s_chip_id), s_chip_id);
    return ESP_OK;

fail:
    release_buffers();
    close(fd);
    s_fd = -1;
    return err;
}

static void stream_teardown(void)
{
    const int type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    ioctl(s_fd, VIDIOC_STREAMOFF, &type);
    xSemaphoreTake(s_lock, portMAX_DELAY);
    s_held = -1;
    release_buffers();
    close(s_fd);
    s_fd = -1;
    xSemaphoreGive(s_lock);
    s_fps = 0;
}

static void camera_task(void *arg)
{
    (void)arg;
    const int type = V4L2_BUF_TYPE_VIDEO_CAPTURE;
    s_task_alive = true;

    s_task_err = stream_setup();
    if (s_task_err != ESP_OK) {
        s_run = false;
        s_task_alive = false;
        xSemaphoreGive(s_done);
        s_task = NULL;
        vTaskDelete(NULL);
        return;
    }
    s_held = -1;
    s_seq = 0;
    s_frames = 0;
    s_errors = 0;
    s_dropped = 0;
    s_fps = 0;
    s_fps_window_frames = 0;
    s_fps_window_start_us = esp_timer_get_time();
    xSemaphoreTake(s_frame_sem, 0);
    xSemaphoreGive(s_done);

    while (s_run) {
        struct v4l2_buffer buf;
        memset(&buf, 0, sizeof(buf));
        buf.type = type;
        buf.memory = V4L2_MEMORY_MMAP;
        if (ioctl(s_fd, VIDIOC_DQBUF, &buf) != 0) {
            // Timeout (see CAM_DQBUF_TIMEOUT_MS): come back round and re-check s_run.
            if (s_run) {
                s_errors++;
                vTaskDelay(pdMS_TO_TICKS(5));
            }
            continue;
        }
        if (!(buf.flags & V4L2_BUF_FLAG_DONE)) {
            s_errors++;
            ioctl(s_fd, VIDIOC_QBUF, &buf);
            continue;
        }

        // Do not wait long for a reader. One in the middle of a conversion
        // holds s_lock for tens of milliseconds, and blocking here for all of
        // it would leave the driver short of buffers and on its no-free-buffer
        // path. Half a frame time covers a reader that is nearly done; past
        // that the frame goes straight back and the reader gets the next one.
        if (xSemaphoreTake(s_lock, pdMS_TO_TICKS(15)) != pdTRUE) {
            ioctl(s_fd, VIDIOC_QBUF, &buf);
            s_dropped++;
            continue;
        }
        int old = s_held;
        s_held = buf.index;
        s_seq++;
        s_frames++;
        xSemaphoreGive(s_lock);

        if (old >= 0) {
            struct v4l2_buffer requeue;
            memset(&requeue, 0, sizeof(requeue));
            requeue.type = type;
            requeue.memory = V4L2_MEMORY_MMAP;
            requeue.index = old;
            if (ioctl(s_fd, VIDIOC_QBUF, &requeue) != 0) {
                s_errors++;
            }
        }

        int64_t now = esp_timer_get_time();
        s_fps_window_frames++;
        if (now - s_fps_window_start_us >= 1000000) {
            s_fps = s_fps_window_frames;
            s_fps_window_frames = 0;
            s_fps_window_start_us = now;
        }
        xSemaphoreGive(s_frame_sem);
    }

    stream_teardown();
    s_task_alive = false;
    s_task = NULL;
    xSemaphoreGive(s_done);
    vTaskDelete(NULL);
}

esp_err_t tab5_camera_start(void)
{
    if (s_fd >= 0 && s_run) {
        return ESP_OK;
    }
    if (s_lock == NULL) {
        s_lock = xSemaphoreCreateMutex();
        s_frame_sem = xSemaphoreCreateBinary();
        s_done = xSemaphoreCreateBinary();
        if (s_lock == NULL || s_frame_sem == NULL || s_done == NULL) {
            return ESP_ERR_NO_MEM;
        }
    }
    if (s_task_alive) {
        return ESP_ERR_INVALID_STATE; // a previous stop is still winding down
    }
    xSemaphoreTake(s_done, 0);
    s_run = true;
    s_task_err = ESP_OK;
    if (xTaskCreatePinnedToCore(camera_task, "tab5_camera", CAM_TASK_STACK, NULL,
                                CAM_TASK_PRIORITY, &s_task, CAM_TASK_CORE) != pdPASS) {
        s_run = false;
        return ESP_ERR_NO_MEM;
    }
    // Sensor probe plus ISP bring-up on the first start; a few ms after that.
    if (xSemaphoreTake(s_done, pdMS_TO_TICKS(5000)) != pdTRUE) {
        ESP_LOGE(TAG, "camera task did not report back");
        return ESP_ERR_TIMEOUT;
    }
    return s_task_err;
}

esp_err_t tab5_camera_stop(void)
{
    if (!s_task_alive) {
        return ESP_OK;
    }
    xSemaphoreTake(s_done, 0);
    s_run = false;
    // The task is parked in DQBUF for at most CAM_DQBUF_TIMEOUT_MS, then it
    // tears the stream down itself and reports.
    if (xSemaphoreTake(s_done, pdMS_TO_TICKS(CAM_DQBUF_TIMEOUT_MS + 1000)) != pdTRUE) {
        ESP_LOGW(TAG, "capture task did not stop");
        return ESP_ERR_TIMEOUT;
    }
    return ESP_OK;
}

bool tab5_camera_running(void)
{
    return s_fd >= 0 && s_run && s_task_alive;
}

void tab5_camera_info(tab5_camera_info_t *out)
{
    memset(out, 0, sizeof(*out));
    out->initialized = s_video_inited;
    out->running = tab5_camera_running();
    out->chip_id = s_chip_id;
    out->sensor = sensor_name(s_chip_id);
    out->width = CAM_W;
    out->height = CAM_H;
    out->frames = s_frames;
    out->errors = s_errors;
    out->dropped = s_dropped;
    out->fps = s_fps;
    out->hflip = s_hflip;
    out->vflip = s_vflip;
    out->gain = out->exposure = out->red_balance = out->blue_balance = -1;
    if (out->running) {
        // What the ISP pipeline controller (AE/AWB in isp_task) has settled on.
        out->gain = get_ctrl(s_fd, V4L2_CTRL_CLASS_USER, V4L2_CID_GAIN);
        out->exposure = get_ctrl(s_fd, V4L2_CTRL_CLASS_CAMERA, V4L2_CID_EXPOSURE);
        int isp_fd = open(ESP_VIDEO_ISP1_DEVICE_NAME, O_RDWR);
        if (isp_fd >= 0) {
            out->red_balance = get_ctrl(isp_fd, V4L2_CTRL_CLASS_USER, V4L2_CID_RED_BALANCE);
            out->blue_balance = get_ctrl(isp_fd, V4L2_CTRL_CLASS_USER, V4L2_CID_BLUE_BALANCE);
            close(isp_fd);
        }
    }
}

uint32_t tab5_camera_seq(void)
{
    return s_seq;
}

uint32_t tab5_camera_wait(uint32_t seq, uint32_t timeout_ms)
{
    if (!tab5_camera_running()) {
        return 0;
    }
    int64_t deadline = esp_timer_get_time() + (int64_t)timeout_ms * 1000;
    while (s_seq == seq || s_held < 0) {
        int64_t left_us = deadline - esp_timer_get_time();
        if (left_us <= 0) {
            return 0;
        }
        xSemaphoreTake(s_frame_sem, pdMS_TO_TICKS((left_us + 999) / 1000));
    }
    return s_seq;
}

// Take the lock and hand back the held frame, waiting briefly for the first
// one after a start. Caller gives the lock back with frame_release().
static esp_err_t frame_acquire(const uint16_t **src)
{
    if (!tab5_camera_running()) {
        return ESP_ERR_INVALID_STATE;
    }
    if (s_held < 0 && tab5_camera_wait(s_seq, 500) == 0) {
        return ESP_ERR_TIMEOUT;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    if (s_held < 0) {
        xSemaphoreGive(s_lock);
        return ESP_ERR_TIMEOUT;
    }
    *src = (const uint16_t *)s_buf[s_held];
    return ESP_OK;
}

static void frame_release(void)
{
    xSemaphoreGive(s_lock);
}

// Nearest-neighbour: a column map once per call, a row pointer per row.
static uint16_t *make_column_map(int w)
{
    uint16_t *map = malloc_caps((size_t)w * sizeof(uint16_t), MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (map == NULL) {
        map = malloc_caps((size_t)w * sizeof(uint16_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    }
    if (map != NULL) {
        for (int i = 0; i < w; i++) {
            map[i] = (uint16_t)(((uint32_t)i * CAM_W) / (uint32_t)w);
        }
    }
    return map;
}

esp_err_t tab5_camera_read_rgb565(uint16_t *dst, int w, int h)
{
    if (dst == NULL || w <= 0 || h <= 0 || w > CAM_W || h > CAM_H) {
        return ESP_ERR_INVALID_ARG;
    }
    const uint16_t *src;
    ESP_RETURN_ON_ERROR(frame_acquire(&src), TAG, "no frame");
    if (w == CAM_W && h == CAM_H) {
        memcpy(dst, src, CAM_FRAME_BYTES);
        frame_release();
        return ESP_OK;
    }
    uint16_t *xmap = make_column_map(w);
    if (xmap == NULL) {
        frame_release();
        return ESP_ERR_NO_MEM;
    }
    for (int j = 0; j < h; j++) {
        const uint16_t *row = src + ((size_t)j * CAM_H / (size_t)h) * CAM_W;
        uint16_t *out = dst + (size_t)j * w;
        if (w == CAM_W) {
            memcpy(out, row, CAM_W * 2);
        } else {
            for (int i = 0; i < w; i++) {
                out[i] = row[xmap[i]];
            }
        }
    }
    frame_release();
    free_caps(xmap);
    return ESP_OK;
}

esp_err_t tab5_camera_draw_bg(int x, int y, int w, int h)
{
    if (bg == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    if (w <= 0 || h <= 0 || x < 0 || y < 0 ||
        x + w > H_RES + OFFSCREEN_X_PX || y + h > V_RES + OFFSCREEN_Y_PX) {
        return ESP_ERR_INVALID_ARG;
    }
    ESP_RETURN_ON_ERROR(ensure_lut(), TAG, "lut");
    const uint16_t *src;
    ESP_RETURN_ON_ERROR(frame_acquire(&src), TAG, "no frame");
    uint16_t *xmap = NULL;
    if (w != CAM_W) {
        xmap = make_column_map(w);
        if (xmap == NULL) {
            frame_release();
            return ESP_ERR_NO_MEM;
        }
    }
    const size_t stride = (size_t)(H_RES + OFFSCREEN_X_PX);
    const uint8_t *lut = s_lut_332;
    for (int j = 0; j < h; j++) {
        const uint16_t *row = src + ((size_t)j * CAM_H / (size_t)h) * CAM_W;
        uint8_t *out = bg + ((size_t)(y + j) * stride + (size_t)x);
        if (xmap == NULL) {
            for (int i = 0; i < w; i++) {
                out[i] = lut[row[i]];
            }
        } else {
            for (int i = 0; i < w; i++) {
                out[i] = lut[row[xmap[i]]];
            }
        }
    }
    frame_release();
    if (xmap != NULL) {
        free_caps(xmap);
    }
    display_mark_dirty_rows(y, y + h);
    return ESP_OK;
}

// A private copy of the frame, aligned the way the JPEG DMA wants its input
// (and, being a whole number of cache lines, the way the cache sync wants it).
static uint8_t *copy_frame_aligned(void)
{
    const uint16_t *src;
    if (frame_acquire(&src) != ESP_OK) {
        return NULL;
    }
    uint8_t *copy = heap_caps_aligned_alloc(128, CAM_FRAME_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (copy != NULL) {
        memcpy(copy, src, CAM_FRAME_BYTES);
    }
    frame_release();
    return copy;
}

esp_err_t tab5_camera_encode_jpeg(int quality, uint8_t **out, size_t *out_len)
{
    if (out == NULL || out_len == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (quality < 1) quality = 1;
    if (quality > 100) quality = 100;
    if (!tab5_camera_running()) {
        return ESP_ERR_INVALID_STATE;
    }
    if (s_jpeg == NULL) {
        return ESP_ERR_NOT_SUPPORTED;
    }
    uint8_t *in = copy_frame_aligned();
    if (in == NULL) {
        return ESP_ERR_NO_MEM;
    }
    // Output no larger than the raw frame: even quality 100 compresses a
    // photograph well below that.
    jpeg_encode_memory_alloc_cfg_t mem_cfg = { .buffer_direction = JPEG_ENC_ALLOC_OUTPUT_BUFFER };
    size_t cap = 0;
    uint8_t *enc = jpeg_alloc_encoder_mem(CAM_FRAME_BYTES, &mem_cfg, &cap);
    if (enc == NULL) {
        heap_caps_free(in);
        return ESP_ERR_NO_MEM;
    }
    jpeg_encode_cfg_t cfg = {
        .width = CAM_W,
        .height = CAM_H,
        .src_type = JPEG_ENCODE_IN_FORMAT_RGB565,
        .sub_sample = JPEG_DOWN_SAMPLING_YUV420,
        .image_quality = (uint32_t)quality,
    };
    uint32_t size = 0;
    esp_err_t err = jpeg_encoder_process(s_jpeg, &cfg, in, CAM_FRAME_BYTES, enc, (uint32_t)cap, &size);
    heap_caps_free(in);
    if (err != ESP_OK) {
        heap_caps_free(enc);
        ESP_LOGE(TAG, "jpeg encode: %s", esp_err_to_name(err));
        return err;
    }
    // Hand back a plain malloc_caps() block so the caller frees it like a PNG.
    uint8_t *result = malloc_caps(size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (result == NULL) {
        heap_caps_free(enc);
        return ESP_ERR_NO_MEM;
    }
    memcpy(result, enc, size);
    heap_caps_free(enc);
    *out = result;
    *out_len = size;
    return ESP_OK;
}

esp_err_t tab5_camera_encode_png(uint8_t **out, size_t *out_len)
{
    if (out == NULL || out_len == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!tab5_camera_running()) {
        return ESP_ERR_INVALID_STATE;
    }
    uint8_t *frame = copy_frame_aligned();
    if (frame == NULL) {
        return ESP_ERR_NO_MEM;
    }
    uint8_t *rgb = malloc_caps(CAM_W * CAM_H * 3, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (rgb == NULL) {
        heap_caps_free(frame);
        return ESP_ERR_NO_MEM;
    }
    const uint16_t *px = (const uint16_t *)frame;
    uint8_t *p = rgb;
    for (size_t i = 0; i < (size_t)CAM_W * CAM_H; i++) {
        uint16_t v = px[i];
        uint8_t r5 = (v >> 11) & 0x1f;
        uint8_t g6 = (v >> 5) & 0x3f;
        uint8_t b5 = v & 0x1f;
        *p++ = (r5 << 3) | (r5 >> 2);
        *p++ = (g6 << 2) | (g6 >> 4);
        *p++ = (b5 << 3) | (b5 >> 2);
    }
    heap_caps_free(frame);
    unsigned char *png = NULL;
    size_t png_len = 0;
    unsigned lerr = lodepng_encode24(&png, &png_len, rgb, CAM_W, CAM_H);
    free_caps(rgb);
    if (lerr != 0) {
        free_caps(png);
        ESP_LOGE(TAG, "png encode: %u", lerr);
        return ESP_FAIL;
    }
    *out = png;
    *out_len = png_len;
    return ESP_OK;
}

esp_err_t tab5_camera_set_flip(int hflip, int vflip)
{
    if (hflip >= 0) {
        s_hflip = hflip != 0;
    }
    if (vflip >= 0) {
        s_vflip = vflip != 0;
    }
    if (s_fd < 0) {
        return ESP_OK; // applied on the next start
    }
    esp_err_t err = ESP_OK;
    if (hflip >= 0 && set_flip_ctrl(V4L2_CID_HFLIP, s_hflip) != ESP_OK) {
        err = ESP_ERR_NOT_SUPPORTED;
    }
    if (vflip >= 0 && set_flip_ctrl(V4L2_CID_VFLIP, s_vflip) != ESP_OK) {
        err = ESP_ERR_NOT_SUPPORTED;
    }
    return err;
}

esp_err_t tab5_camera_set_test_pattern(bool on)
{
    if (!tab5_camera_running()) {
        return ESP_ERR_INVALID_STATE;
    }
    if (set_ctrl(V4L2_CTRL_CLASS_IMAGE_PROC, V4L2_CID_TEST_PATTERN, on ? 1 : 0) != ESP_OK) {
        return ESP_ERR_NOT_SUPPORTED;
    }
    return ESP_OK;
}
