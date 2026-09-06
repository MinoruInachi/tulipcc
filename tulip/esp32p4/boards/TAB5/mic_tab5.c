// Tab5 built-in dual microphone. See mic_tab5.h for the shape of the API and
// why the rate and format are fixed.

#include "mic_tab5.h"

#include <string.h>

#include "esp_log.h"
#include "esp_codec_dev.h"
#include "esp_heap_caps.h"
#include "esp_task.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"

#include "bsp/m5stack_tab5.h"
#include "../../../../amy/src/amy.h"

static const char *TAG = "tab5_mic";

// Frames read from the codec per esp_codec_dev_read() call. One block is
// 256 * 2ch * 2 bytes = 1 KB, ~5.8 ms at 44.1 kHz -- short enough that a level
// meter feels live, long enough that the read overhead is negligible.
#define MIC_BLOCK_FRAMES 256
// Half a second of stereo history in the ring. A reader that services the mic
// even a few times a second never sees an overrun; one that vanishes just loses
// the oldest audio, which is what any live-capture buffer should do.
#define MIC_RING_FRAMES (AMY_SAMPLE_RATE / 2)
#define MIC_RING_SAMPLES (MIC_RING_FRAMES * TAB5_MIC_CHANNELS)

#define MIC_TASK_STACK 4096
// Beside the display, touch and camera tasks on core 0 (see the note in
// camera_tab5.c on why those interrupts and this reader belong off the
// MicroPython core), a touch above idle so a level meter stays responsive but
// AMY's render task and the display keep priority.
#define MIC_TASK_PRIORITY (tskIDLE_PRIORITY + 2)
#define MIC_TASK_CORE 0

#define MIC_GAIN_DEFAULT 30
#define MIC_GAIN_MAX 37.5f
#define MIC_GAIN_MIN 0.0f

static esp_codec_dev_handle_t s_mic;      // ES7210, opened for reading
static TaskHandle_t s_task;
static SemaphoreHandle_t s_lock;          // guards the ring + peaks
static SemaphoreHandle_t s_data_sem;      // producer gives it after each block
static volatile bool s_running;
static volatile bool s_stop_requested;
static int s_gain = MIC_GAIN_DEFAULT;

// Ring of interleaved L,R int16. s_head is the next write slot (in frames),
// s_count the frames held; the read tail is (s_head - s_count) mod capacity.
static int16_t *s_ring;
static uint32_t s_head;
static uint32_t s_count;

static volatile uint32_t s_blocks;
static volatile uint32_t s_read_errors;
static volatile uint32_t s_overruns;
static volatile uint32_t s_peak_left;
static volatile uint32_t s_peak_right;

static int clamp_gain(int gain_db) {
    if (gain_db < (int)MIC_GAIN_MIN) return (int)MIC_GAIN_MIN;
    if (gain_db > (int)MIC_GAIN_MAX) return (int)MIC_GAIN_MAX;
    return gain_db;
}

// Producer: append `frames` interleaved stereo frames, dropping the oldest and
// counting the overrun if the ring is full. Runs under s_lock.
static void ring_push(const int16_t *src, uint32_t frames) {
    for (uint32_t f = 0; f < frames; f++) {
        int16_t l = src[f * TAB5_MIC_CHANNELS + 0];
        int16_t r = src[f * TAB5_MIC_CHANNELS + 1];
        s_ring[s_head * TAB5_MIC_CHANNELS + 0] = l;
        s_ring[s_head * TAB5_MIC_CHANNELS + 1] = r;
        s_head = (s_head + 1) % MIC_RING_FRAMES;
        if (s_count < MIC_RING_FRAMES) {
            s_count++;
        } else {
            // Full: s_head has just overwritten the tail, so the tail moves with
            // it and we lost a frame.
            s_overruns++;
        }
    }
}

// Consumer: copy up to `max_frames` from the tail into dst. Runs under s_lock.
static uint32_t ring_pop(int16_t *dst, uint32_t max_frames) {
    uint32_t n = s_count < max_frames ? s_count : max_frames;
    uint32_t tail = (s_head + MIC_RING_FRAMES - s_count) % MIC_RING_FRAMES;
    for (uint32_t f = 0; f < n; f++) {
        dst[f * TAB5_MIC_CHANNELS + 0] = s_ring[tail * TAB5_MIC_CHANNELS + 0];
        dst[f * TAB5_MIC_CHANNELS + 1] = s_ring[tail * TAB5_MIC_CHANNELS + 1];
        tail = (tail + 1) % MIC_RING_FRAMES;
    }
    s_count -= n;
    return n;
}

static void mic_task(void *ignored) {
    (void)ignored;
    // One block's worth of interleaved stereo, off the stack.
    static int16_t block[MIC_BLOCK_FRAMES * TAB5_MIC_CHANNELS];
    const int block_bytes = sizeof(block);
    while (!s_stop_requested) {
        int r = esp_codec_dev_read(s_mic, block, block_bytes);
        if (r != ESP_CODEC_DEV_OK) {
            s_read_errors++;
            vTaskDelay(pdMS_TO_TICKS(5));
            continue;
        }
        // Per-mic peak for the level meter, computed outside the ring lock.
        uint32_t peak_l = 0, peak_r = 0;
        for (uint32_t f = 0; f < MIC_BLOCK_FRAMES; f++) {
            int32_t l = block[f * TAB5_MIC_CHANNELS + 0];
            int32_t r = block[f * TAB5_MIC_CHANNELS + 1];
            uint32_t al = (uint32_t)(l < 0 ? -l : l);
            uint32_t ar = (uint32_t)(r < 0 ? -r : r);
            if (al > peak_l) peak_l = al;
            if (ar > peak_r) peak_r = ar;
        }
        xSemaphoreTake(s_lock, portMAX_DELAY);
        ring_push(block, MIC_BLOCK_FRAMES);
        s_peak_left = peak_l;
        s_peak_right = peak_r;
        xSemaphoreGive(s_lock);
        s_blocks++;
        xSemaphoreGive(s_data_sem);  // wake any blocked reader
    }
    s_running = false;
    s_task = NULL;
    vTaskDelete(NULL);
}

esp_err_t tab5_mic_start(int gain_db) {
    s_gain = clamp_gain(gain_db);
    if (s_running) {
        // Already up: just re-apply the requested gain.
        return tab5_mic_set_gain(s_gain);
    }

    if (s_lock == NULL) {
        s_lock = xSemaphoreCreateMutex();
        s_data_sem = xSemaphoreCreateBinary();
        if (s_lock == NULL || s_data_sem == NULL) {
            return ESP_ERR_NO_MEM;
        }
    }
    if (s_ring == NULL) {
        s_ring = heap_caps_malloc(MIC_RING_SAMPLES * sizeof(int16_t),
                                  MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (s_ring == NULL) {
            return ESP_ERR_NO_MEM;
        }
    }

    if (s_mic == NULL) {
        // Programs the ES7210 over the BSP's shared I2C and hands back a device
        // bound to the RX half of the already-running full-duplex I2S.
        s_mic = bsp_audio_codec_microphone_init();
        if (s_mic == NULL) {
            ESP_LOGE(TAG, "microphone codec init failed");
            return ESP_FAIL;
        }
    }

    esp_codec_dev_sample_info_t sample_info = {
        .sample_rate = AMY_SAMPLE_RATE,
        .channel = TAB5_MIC_CHANNELS,
        .bits_per_sample = TAB5_MIC_BITS,
    };
    int rc = esp_codec_dev_open(s_mic, &sample_info);
    if (rc != ESP_CODEC_DEV_OK) {
        ESP_LOGE(TAG, "microphone open failed: %d", rc);
        return ESP_FAIL;
    }
    rc = esp_codec_dev_set_in_gain(s_mic, (float)s_gain);
    if (rc != ESP_CODEC_DEV_OK) {
        ESP_LOGW(TAG, "microphone gain set failed: %d", rc);
    }

    // Fresh counters and an empty ring for this session.
    xSemaphoreTake(s_lock, portMAX_DELAY);
    s_head = 0;
    s_count = 0;
    xSemaphoreGive(s_lock);
    s_blocks = 0;
    s_read_errors = 0;
    s_overruns = 0;
    s_peak_left = 0;
    s_peak_right = 0;
    s_stop_requested = false;
    s_running = true;

    if (xTaskCreatePinnedToCore(mic_task, "tab5_mic", MIC_TASK_STACK, NULL,
                                MIC_TASK_PRIORITY, &s_task, MIC_TASK_CORE) != pdPASS) {
        s_running = false;
        esp_codec_dev_close(s_mic);
        ESP_LOGE(TAG, "failed to create mic task");
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "microphone started: %d Hz, %d ch, gain %d dB",
             AMY_SAMPLE_RATE, TAB5_MIC_CHANNELS, s_gain);
    return ESP_OK;
}

esp_err_t tab5_mic_stop(void) {
    if (!s_running) {
        return ESP_OK;
    }
    s_stop_requested = true;
    // The task may be parked in esp_codec_dev_read(); wait for it to notice the
    // flag on its next block and delete itself.
    for (int i = 0; i < 100 && s_task != NULL; i++) {
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    esp_codec_dev_close(s_mic);
    return ESP_OK;
}

bool tab5_mic_running(void) {
    return s_running;
}

void tab5_mic_info(tab5_mic_info_t *out) {
    memset(out, 0, sizeof(*out));
    out->initialized = (s_mic != NULL);
    out->running = s_running;
    out->sample_rate = AMY_SAMPLE_RATE;
    out->channels = TAB5_MIC_CHANNELS;
    out->bits = TAB5_MIC_BITS;
    out->gain = s_gain;
    out->blocks = s_blocks;
    out->read_errors = s_read_errors;
    out->overruns = s_overruns;
    out->capacity = MIC_RING_FRAMES;
    out->peak_left = s_peak_left;
    out->peak_right = s_peak_right;
    if (s_lock != NULL) {
        xSemaphoreTake(s_lock, portMAX_DELAY);
        out->available = s_count;
        xSemaphoreGive(s_lock);
    }
}

size_t tab5_mic_read(int16_t *dst, size_t max_frames, uint32_t timeout_ms) {
    if (!s_running || s_lock == NULL || max_frames == 0) {
        return 0;
    }
    // Loop until there is something to hand back or the deadline passes. A single
    // wait is not enough: the binary semaphore can carry a stale token from a
    // block that arrived while nobody was waiting (or one consumed just after a
    // flush), which wakes us with the ring still momentarily empty. Re-checking
    // under the lock and waiting again with the remaining time makes a return of
    // 0 mean a real timeout -- which is what callers like mic_record() rely on to
    // tell "capture stalled" from "no data this instant".
    TickType_t deadline = xTaskGetTickCount() + pdMS_TO_TICKS(timeout_ms);
    for (;;) {
        xSemaphoreTake(s_lock, portMAX_DELAY);
        if (s_count > 0) {
            uint32_t n = ring_pop(dst, (uint32_t)max_frames);
            xSemaphoreGive(s_lock);
            return n;
        }
        xSemaphoreGive(s_lock);
        if (timeout_ms == 0) {
            return 0;
        }
        TickType_t now = xTaskGetTickCount();
        if ((int32_t)(deadline - now) <= 0) {
            return 0;
        }
        xSemaphoreTake(s_data_sem, deadline - now);
    }
}

size_t tab5_mic_available_frames(void) {
    if (s_lock == NULL) {
        return 0;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    uint32_t n = s_count;
    xSemaphoreGive(s_lock);
    return n;
}

void tab5_mic_flush(void) {
    if (s_lock == NULL) {
        return;
    }
    xSemaphoreTake(s_lock, portMAX_DELAY);
    s_count = 0;
    xSemaphoreGive(s_lock);
}

void tab5_mic_levels(float *out_left, float *out_right) {
    if (out_left) *out_left = (float)s_peak_left / 32768.0f;
    if (out_right) *out_right = (float)s_peak_right / 32768.0f;
}

esp_err_t tab5_mic_set_gain(int gain_db) {
    s_gain = clamp_gain(gain_db);
    if (s_mic == NULL) {
        return ESP_OK;  // remembered; applied at open
    }
    int rc = esp_codec_dev_set_in_gain(s_mic, (float)s_gain);
    return rc == ESP_CODEC_DEV_OK ? ESP_OK : ESP_FAIL;
}
