#include <stdint.h>
#include <stdio.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "esp_timer.h"
#include "esp_log.h"

#include "esp_lcd_panel_ops.h"
#include "esp_lcd_mipi_dsi.h"
#include "esp_lcd_st7121.h"
#include "esp_ldo_regulator.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "driver/ppa.h"
#include "bsp/esp-bsp.h"
#include "bsp/m5stack_tab5.h"
#include "lvgl.h"

#include "display_tab5.h"
#include "pins.h"
#include "tab5_revision.h"

static const char *TAG = "TAB5-DISPLAY";

#define TAB5_BRIDGE_CHUNK_ROWS 12
#define TAB5_SHARED_RENDER_W 1280
#define TAB5_SHARED_RENDER_H 720
/* L1 and L2 lines are both 64B on this target (CONFIG_CACHE_*_CACHE_LINE_64B). */
#define TAB5_CACHE_LINE_BYTES 64
#define TAB5_REPL_TRANSPARENT_BG 0x55
/* Set to 1 for bring-up testing to force rotated provider path using scaffold data. */
#define TAB5_FORCE_ROTATED_PROVIDER_TEST 0

static uint32_t s_bridge_frame_id = 0;
static bool s_tulip_shared_renderer_initialized = false;

/* Shared-provider symbols are provided by TAB5 bring-up provider today and
 * will later be replaced by the full Tulip shared renderer implementation. */
extern bool display_bounce_empty(void *bounce_buf, int pos_px, int len_bytes, void *user_ctx);
extern bool display_frame_done_generic(void);
extern uint8_t brightness;
extern void display_init(void);
extern void display_set_bg_pixel_pal(uint16_t x, uint16_t y, uint8_t pal_idx);
extern void display_tfb_set_default_bg_color(uint8_t color);
extern int16_t lvgl_is_repl;
/* Shared Tulip stats, read back by tulip.fps() / tulip.gpu(). */
extern float reported_fps;
extern float reported_gpu_usage;
extern uint8_t gpu_log;
/* Set by anything that changes the framebuffer contents; see shared/display.c. */
extern volatile uint8_t display_dirty;
extern bool display_take_dirty_rows(int *y0, int *y1);
extern void display_mark_dirty_rows(int y0, int y1);

bool tab5_repl_menu_icon_touch_event(int16_t shared_x, int16_t shared_y, bool up)
{
    (void)shared_x;
    (void)shared_y;
    (void)up;
    return false;
}

static void tab5_clear_boot_background(void)
{
    // Fill with a stable solid tone to avoid test-pattern artifacts.
    for (uint16_t y = 0; y < BSP_LCD_V_RES; y++) {
        for (uint16_t x = 0; x < BSP_LCD_H_RES; x++) {
            (void)x;
            display_set_bg_pixel_pal(x, y, 9);
        }
    }
}

static bool tab5_scaffold_bounce_empty(void *bounce_buf, int pos_px, int len_bytes, void *user_ctx)
{
    (void)user_ctx;

    if (bounce_buf == NULL || len_bytes <= 0) {
        return false;
    }

    uint8_t *out = (uint8_t *)bounce_buf;
    const int row0 = pos_px / BSP_LCD_H_RES;
    const int rows = len_bytes / BSP_LCD_H_RES;

    for (int r = 0; r < rows; r++) {
        const int y = row0 + r;
        for (int x = 0; x < BSP_LCD_H_RES; x++) {
            const uint8_t bar = (uint8_t)((x * 8) / BSP_LCD_H_RES);
            const uint8_t anim = (uint8_t)((y + (int)(s_bridge_frame_id & 0x1f)) & 0x1f);
            out[r * BSP_LCD_H_RES + x] = (uint8_t)((bar << 5) | anim);
        }
    }

    return true;
}

static bool tab5_scaffold_frame_done(void)
{
    s_bridge_frame_id++;
    return true;
}

static void tab5_render_bridge_buffers_deinit(void);

static tab5_render_bounce_empty_fn_t s_render_bounce_empty = tab5_scaffold_bounce_empty;
static tab5_render_frame_done_fn_t s_render_frame_done = tab5_scaffold_frame_done;
static uint8_t *s_line332 = NULL;
static uint16_t *s_line565 = NULL;
/* Landscape RGB565 staging image, exactly what Tulip renders (1280x720).
 * The panel is physically portrait, so this gets rotated on the way out. */
static uint16_t *s_stage565 = NULL;
static int s_linebuf_width = 0;

/* rgb332 -> rgb565 lookup, built once. The arithmetic version costs three
 * integer divides per pixel, which at 921600 pixels a frame is not affordable. */
static uint16_t s_rgb332_565[256];
static bool s_rgb332_565_ready = false;

/* Hardware rotation. The ESP32-P4 PPA does scale/rotate/mirror in a DMA engine,
 * so the CPU never touches the portrait framebuffer. */
static ppa_client_handle_t s_ppa_srm = NULL;
static void *s_dsi_fb[2] = {NULL, NULL};
static uint8_t s_dsi_fb_count = 0;
static uint8_t s_dsi_fb_next = 0;
static uint32_t s_ppa_failures = 0;
/* The PPA transaction is issued non-blocking and waited for here, with a
 * timeout. In blocking mode the driver waits on the DMA2D completion forever,
 * and a completion that never arrives took the whole display task with it:
 * the screen froze while everything else kept running, and nothing could say
 * why because the task was parked inside the driver. A timeout turns that
 * into one slow frame, a counter and a log line. */
static SemaphoreHandle_t s_ppa_done_sem = NULL;
static uint32_t s_ppa_timeouts = 0;
static volatile uint8_t s_phase = TAB5_PHASE_WAIT_VSYNC;
#define TAB5_PPA_TIMEOUT_MS 250
/* Recovery after a timeout. The transaction that never finished still holds
 * the client's one pending slot, and ppa_unregister_client() refuses a
 * client with unprocessed transactions, so the client is abandoned (a few
 * hundred bytes) and a fresh one opened. Measured with the camera app: one
 * rotation out of tens of thousands stalls, some minutes in, and the next
 * client carries on at full speed. After this many the PPA is left alone
 * and rotation stays on the CPU. */
#define TAB5_PPA_MAX_RECOVERIES 3
static uint32_t s_ppa_recoveries = 0;
static int s_ppa_last_err = 0;
static int s_ppa_stuck_y = -1, s_ppa_stuck_rows = 0, s_ppa_last_y = -1, s_ppa_last_rows = 0;
/* A frame that ran this long without blocking hands the core to IDLE0 for a
 * tick before waiting for vsync, or the idle-task watchdog fires on core 0
 * (its check is deliberately on, see sdkconfig.board). Only the CPU rotation
 * fallback gets near it. */
#define TAB5_FRAME_YIELD_US 50000

/* Per-frame phase timings, in microseconds, for the last completed frame.
 * Surfaced through tulip.tab5_render_stats() so the cost of a change can be
 * attributed instead of guessed at. */
static uint32_t s_us_composite = 0;   /* Tulip compositor callbacks */
static uint32_t s_us_convert = 0;     /* rgb332 -> rgb565 into the staging image */
static uint32_t s_us_rotate = 0;      /* PPA (or CPU fallback) rotation */
static uint32_t s_us_present = 0;     /* draw_bitmap: cache write-back + fb swap */
static uint32_t s_us_wait = 0;        /* blocked waiting for the panel */
static uint32_t s_frames_skipped = 0; /* frames dropped because nothing changed */
static uint32_t s_band_rows = 0;      /* rows recomposed for the last frame */

/* Damage presented into the other DSI framebuffer last frame. */
static int s_prev_band_y0 = 0;
static int s_prev_band_y1 = TAB5_SHARED_RENDER_H;

/* Panel refresh signal, so the loop paces to the display instead of a fixed
 * vTaskDelay stacked on top of however long the frame happened to take. */
static SemaphoreHandle_t s_vsync_sem = NULL;
/* Redraw the panel even when nothing reported a change, so a missed
 * display_mark_dirty() shows up as a stale second rather than a frozen screen --
 * but a slice of it at a time. The display task is serial and a whole 720-row
 * frame costs ~130ms of composite + convert + rotate, so redrawing all of it in
 * one go parked every damage band behind a 130ms stall roughly once a second:
 * drums.py's beat LEDs stopped following the sequencer for a third of a step,
 * several times a bar. A slice is ~17ms, and eight of them one every four frames
 * cover the screen in the same ~0.9s for the same rows per second. */
#define TAB5_FORCED_SLICE_FRAMES 4
#define TAB5_FORCED_SLICES 8
#define TAB5_FORCED_SLICE_ROWS ((TAB5_SHARED_RENDER_H + TAB5_FORCED_SLICES - 1) / TAB5_FORCED_SLICES)
/* A slice waits for a frame that has no damage of its own (see below). If every
 * frame has some, it cannot wait for ever. */
#define TAB5_FORCED_SLICE_MAX_FRAMES (TAB5_FORCED_SLICE_FRAMES * 8)
static int s_provider_width = BSP_LCD_H_RES;
static int s_provider_height = BSP_LCD_V_RES;
static bool s_shared_provider_active = false;
static uint32_t s_bridge_frames = 0;
static volatile uint32_t s_display_task_entries = 0;
static uint32_t s_bridge_fallbacks = 0;
static uint32_t s_native_callback_failures = 0;
static uint32_t s_rotated_callback_failures = 0;
static int64_t s_last_stats_log_us = 0;
static uint32_t s_last_stats_frames = 0;
static int64_t s_window_busy_us = 0;

/* Refresh the numbers behind tulip.fps() / tulip.gpu().
 *
 * The esp32s3 port averages over a fixed 100 frames, which is unusable here:
 * at the rates this bridge runs that would be a 25-second window. Average over
 * a 1-second wall-clock window instead, and report GPU usage as the fraction of
 * that window actually spent inside the render tick. */
#define TAB5_STATS_WINDOW_US 1000000

static void tab5_bridge_update_stats(void)
{
    const int64_t now_us = esp_timer_get_time();
    if (s_last_stats_log_us == 0) {
        s_last_stats_log_us = now_us;
        s_last_stats_frames = s_bridge_frames;
        s_window_busy_us = 0;
        return;
    }

    const int64_t elapsed_us = now_us - s_last_stats_log_us;
    if (elapsed_us < TAB5_STATS_WINDOW_US) {
        return;
    }

    const uint32_t window_frames = s_bridge_frames - s_last_stats_frames;
    const float window_fps = (float)window_frames * 1000000.0f / (float)elapsed_us;

    reported_fps = window_fps;
    reported_gpu_usage = (float)s_window_busy_us * 100.0f / (float)elapsed_us;
    if (reported_gpu_usage > 100.0f) {
        reported_gpu_usage = 100.0f;
    }

    s_last_stats_log_us = now_us;
    s_last_stats_frames = s_bridge_frames;
    s_window_busy_us = 0;

    /* Same one-shot semantics as the esp32s3 port: tulip.gpu_reset() arms it. */
    if (!gpu_log) {
        return;
    }
    gpu_log = 0;
    ESP_LOGI(TAG,
             "bridge stats: frames=%lu window_fps=%.2f fallbacks=%lu native_fail=%lu rotated_fail=%lu provider=%s (%dx%d)",
             (unsigned long)s_bridge_frames,
             window_fps,
             (unsigned long)s_bridge_fallbacks,
             (unsigned long)s_native_callback_failures,
             (unsigned long)s_rotated_callback_failures,
             s_shared_provider_active ? "shared" : "scaffold",
             s_provider_width,
             s_provider_height);
}

static void tab5_use_scaffold_provider(const char *reason)
{
    tab5_set_render_provider_with_geometry(tab5_scaffold_bounce_empty,
                                           tab5_scaffold_frame_done,
                                           BSP_LCD_H_RES,
                                           BSP_LCD_V_RES);
    s_shared_provider_active = false;
    s_bridge_fallbacks++;
    if (reason != NULL) {
        ESP_LOGW(TAG, "Falling back to scaffold render provider: %s", reason);
    }
}

void tab5_set_render_provider(tab5_render_bounce_empty_fn_t bounce_empty_cb,
                              tab5_render_frame_done_fn_t frame_done_cb)
{
    tab5_set_render_provider_with_geometry(bounce_empty_cb, frame_done_cb, BSP_LCD_H_RES, BSP_LCD_V_RES);
}

void tab5_set_render_provider_with_geometry(tab5_render_bounce_empty_fn_t bounce_empty_cb,
                                            tab5_render_frame_done_fn_t frame_done_cb,
                                            int provider_width,
                                            int provider_height)
{
    if (bounce_empty_cb != NULL) {
        s_render_bounce_empty = bounce_empty_cb;
    }
    if (frame_done_cb != NULL) {
        s_render_frame_done = frame_done_cb;
    }

    if (provider_width > 0) {
        s_provider_width = provider_width;
    }
    if (provider_height > 0) {
        s_provider_height = provider_height;
    }
}

static void tab5_build_color_lut(void)
{
    if (s_rgb332_565_ready) {
        return;
    }
    for (int i = 0; i < 256; i++) {
        const uint8_t r3 = (uint8_t)((i >> 5) & 0x07);
        const uint8_t g3 = (uint8_t)((i >> 2) & 0x07);
        const uint8_t b2 = (uint8_t)(i & 0x03);
        const uint16_t r5 = (uint16_t)((r3 * 31) / 7);
        const uint16_t g6 = (uint16_t)((g3 * 63) / 7);
        const uint16_t b5 = (uint16_t)((b2 * 31) / 3);
        s_rgb332_565[i] = (uint16_t)((r5 << 11) | (g6 << 5) | b5);
    }
    s_rgb332_565_ready = true;
}

static bool tab5_render_bridge_buffers_init(void)
{
    const bool needs_stage_frame = (s_provider_width != BSP_LCD_H_RES || s_provider_height != BSP_LCD_V_RES);
    const size_t row_bytes = (size_t)s_provider_width;
    const size_t chunk_bytes = (size_t)s_provider_width * TAB5_BRIDGE_CHUNK_ROWS;
    const size_t px_count = row_bytes > chunk_bytes ? row_bytes : chunk_bytes;

    tab5_build_color_lut();

    if (s_line332 != NULL && s_line565 != NULL) {
        const bool linebuf_matches = (s_linebuf_width == s_provider_width);
        const bool framebuf_matches = (!needs_stage_frame || s_stage565 != NULL);
        if (linebuf_matches && framebuf_matches) {
            if (!needs_stage_frame) {
                heap_caps_free(s_stage565);
                s_stage565 = NULL;
            }
            return true;
        }

        tab5_render_bridge_buffers_deinit();
    }

    /* Scratch line buffers are touched per pixel by the CPU; keep them in
     * internal SRAM so the compositor is not paying PSRAM latency per row. */
    s_line332 = heap_caps_malloc(px_count, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (s_line332 == NULL) {
        s_line332 = heap_caps_malloc(px_count, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    }
    s_line565 = heap_caps_malloc(px_count * sizeof(uint16_t), MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (s_line565 == NULL) {
        s_line565 = heap_caps_malloc(px_count * sizeof(uint16_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    }
    if (s_line332 == NULL || s_line565 == NULL) {
        heap_caps_free(s_line332);
        heap_caps_free(s_line565);
        s_line332 = NULL;
        s_line565 = NULL;
        s_linebuf_width = 0;
        ESP_LOGE(TAG, "No memory for render bridge buffers");
        return false;
    }
    s_linebuf_width = s_provider_width;

    if (needs_stage_frame) {
        const size_t stage_px = (size_t)s_provider_width * s_provider_height;
        /* Cache-line aligned so the PPA input window sync stays cheap. */
        s_stage565 = heap_caps_aligned_alloc(TAB5_CACHE_LINE_BYTES,
                                             stage_px * sizeof(uint16_t),
                                             MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (s_stage565 == NULL) {
            ESP_LOGE(TAG, "No memory for %dx%d staging frame buffer",
                     s_provider_width, s_provider_height);
            tab5_render_bridge_buffers_deinit();
            return false;
        }
    } else {
        heap_caps_free(s_stage565);
        s_stage565 = NULL;
    }

    return true;
}

static void tab5_render_bridge_buffers_deinit(void)
{
    heap_caps_free(s_line332);
    heap_caps_free(s_line565);
    heap_caps_free(s_stage565);
    s_line332 = NULL;
    s_line565 = NULL;
    s_stage565 = NULL;
    s_linebuf_width = 0;
}

static bool tab5_display_started = false;
/* Kept because the board tasks' log output does not reliably survive the
 * USB-Serial-JTAG console once MicroPython owns it -- a black screen with a
 * happily refreshing DSI is otherwise mute about which step failed. */
static esp_err_t s_display_new_err = ESP_ERR_INVALID_STATE;
static esp_err_t s_disp_on_err = ESP_ERR_INVALID_STATE;
/* Panel refreshes, counted so the live refresh rate can be measured from the
 * REPL -- which is the only way from here to tell which DPI timing the BSP
 * actually installed (the two board revisions differ by about 10Hz). */
static volatile uint32_t s_vsync_count = 0;
/* Set when the ST7121 panel was brought up here rather than by the BSP, so
 * the teardown path knows whose handles these are. */
static bool s_display_owned_locally = false;
static esp_ldo_channel_handle_t s_dsi_phy_pwr_chan = NULL;
static bsp_lcd_handles_t tab5_lcd_handles;

/* Convert one compositor row (rgb332) into the staging image (rgb565).
 * Straight-line, contiguous destination writes: one cache line fill per 32 px
 * instead of the one-per-pixel that the old in-place rotation forced. */
static inline void tab5_row332_to_stage565(const uint8_t *src, uint16_t *dst, int width)
{
    const uint16_t *lut = s_rgb332_565;
    int x = 0;
    for (; x + 4 <= width; x += 4) {
        dst[x + 0] = lut[src[x + 0]];
        dst[x + 1] = lut[src[x + 1]];
        dst[x + 2] = lut[src[x + 2]];
        dst[x + 3] = lut[src[x + 3]];
    }
    for (; x < width; x++) {
        dst[x] = lut[src[x]];
    }
}

/* Rotate the landscape staging image onto a portrait panel framebuffer.
 *
 * Tulip's (x, y) maps to panel (y, W-1-x), which is a 90 degree counter-
 * clockwise rotation -- exactly PPA_SRM_ROTATION_ANGLE_90. Returns false if
 * the PPA is unavailable or the transaction failed, so the caller can fall
 * back to the CPU path.
 *
 * `y_start`/`rows` select a horizontal band of the staging image. After a
 * 90 CCW rotation that band lands as a vertical column of the panel, at
 * panel x == y_start, so the destination block offset is on x, not y. */
static bool IRAM_ATTR tab5_ppa_trans_done(ppa_client_handle_t client, ppa_event_data_t *edata, void *user)
{
    (void)edata; (void)user;
    /* An abandoned client finishing late must not wake the current wait. */
    if (client != s_ppa_srm) {
        return false;
    }
    BaseType_t woken = pdFALSE;
    xSemaphoreGiveFromISR(s_ppa_done_sem, &woken);
    return woken == pdTRUE;
}

/* Register a PPA scale-rotate-mirror client with its completion callback.
 * Leaves s_ppa_srm NULL, and rotation on the CPU, if either step fails. */
static void tab5_ppa_client_open(void)
{
    const ppa_client_config_t ppa_cfg = {
        .oper_type = PPA_OPERATION_SRM,
        .max_pending_trans_num = 1,
    };
    ppa_client_handle_t client = NULL;
    if (ppa_register_client(&ppa_cfg, &client) != ESP_OK) {
        s_ppa_srm = NULL;
        ESP_LOGW(TAG, "PPA unavailable; rotation will run on the CPU");
        return;
    }
    if (s_ppa_done_sem == NULL) {
        s_ppa_done_sem = xSemaphoreCreateBinary();
    }
    const ppa_event_callbacks_t ppa_cbs = { .on_trans_done = tab5_ppa_trans_done };
    if (s_ppa_done_sem == NULL || ppa_client_register_event_callbacks(client, &ppa_cbs) != ESP_OK) {
        ESP_LOGW(TAG, "PPA completion callback unavailable; rotation will run on the CPU");
        ppa_unregister_client(client);
        s_ppa_srm = NULL;
        return;
    }
    s_ppa_srm = client;
}

static bool tab5_ppa_rotate_to_fb(void *dst_fb, int y_start, int rows)
{
    if (s_ppa_srm == NULL || dst_fb == NULL || s_ppa_done_sem == NULL) {
        return false;
    }
    /* A transaction that timed out earlier may have completed since; its
     * give must not count for this one. */
    xSemaphoreTake(s_ppa_done_sem, 0);
    s_ppa_last_y = y_start;
    s_ppa_last_rows = rows;

    const ppa_srm_oper_config_t srm = {
        .in = {
            .buffer = s_stage565,
            .pic_w = (uint32_t)s_provider_width,
            .pic_h = (uint32_t)s_provider_height,
            .block_w = (uint32_t)s_provider_width,
            .block_h = (uint32_t)rows,
            .block_offset_x = 0,
            .block_offset_y = (uint32_t)y_start,
            .srm_cm = PPA_SRM_COLOR_MODE_RGB565,
        },
        .out = {
            .buffer = dst_fb,
            .buffer_size = (uint32_t)BSP_LCD_H_RES * BSP_LCD_V_RES * sizeof(uint16_t),
            .pic_w = BSP_LCD_H_RES,
            .pic_h = BSP_LCD_V_RES,
            .block_offset_x = (uint32_t)y_start,
            .block_offset_y = 0,
            .srm_cm = PPA_SRM_COLOR_MODE_RGB565,
        },
        .rotation_angle = PPA_SRM_ROTATION_ANGLE_90,
        .scale_x = 1.0f,
        .scale_y = 1.0f,
        .mode = PPA_TRANS_MODE_NON_BLOCKING,
    };

    const esp_err_t err = ppa_do_scale_rotate_mirror(s_ppa_srm, &srm);
    if (err != ESP_OK) {
        /* Also where a timed-out transaction lands us afterwards: the client
         * allows one pending transaction, and the stuck one still holds it. */
        s_ppa_failures++;
        s_ppa_last_err = (int)err;
        if (s_ppa_failures == 1) {
            ESP_LOGE(TAG, "PPA rotate failed (%s); falling back to CPU rotation",
                     esp_err_to_name(err));
        }
        return false;
    }
    if (xSemaphoreTake(s_ppa_done_sem, pdMS_TO_TICKS(TAB5_PPA_TIMEOUT_MS)) != pdTRUE) {
        s_ppa_timeouts++;
        if (s_ppa_stuck_y < 0) {
            s_ppa_stuck_y = y_start;
            s_ppa_stuck_rows = rows;
        }
        if (s_ppa_recoveries < TAB5_PPA_MAX_RECOVERIES) {
            s_ppa_recoveries++;
            ESP_LOGE(TAG, "PPA rotate did not complete within %d ms (band y=%d rows=%d); "
                     "opening a new PPA client (%u of %d)", TAB5_PPA_TIMEOUT_MS, y_start, rows,
                     (unsigned)s_ppa_recoveries, TAB5_PPA_MAX_RECOVERIES);
            s_ppa_srm = NULL; /* abandoned, see TAB5_PPA_MAX_RECOVERIES */
            tab5_ppa_client_open();
        } else {
            ESP_LOGE(TAG, "PPA rotate did not complete within %d ms again; "
                     "rotation stays on the CPU from here", TAB5_PPA_TIMEOUT_MS);
            s_ppa_srm = NULL;
        }
        return false;
    }
    return true;
}

/* CPU fallback for the rotation: the PPA could not be registered, or one of
 * its transactions never completed (see TAB5_PPA_TIMEOUT_MS). */
/* Rotate staging rows [y0, y1) onto the panel, 32x32 blocks at a time so that
 * both the reads (one staging row segment per x) and the writes (32
 * consecutive panel pixels, one cache line) stay in cache. The pixel-at-a-time
 * version this replaces wrote every pixel to a different cache line and took
 * 100 ms for a full frame; this does a full frame in a third of that and a
 * typical band in less. Still the fallback: the PPA does the same in 40 ms
 * without touching the CPU. */
static void tab5_cpu_rotate_band(uint16_t *dst_fb, int y0, int y1)
{
    const int W = s_provider_width;
    for (int by = y0; by < y1; by += 32) {
        const int ye = (by + 32 < y1) ? by + 32 : y1;
        for (int bx = 0; bx < W; bx += 32) {
            const int xe = (bx + 32 < W) ? bx + 32 : W;
            for (int x = bx; x < xe; x++) {
                uint16_t *drow = dst_fb + (size_t)(W - 1 - x) * BSP_LCD_H_RES + by;
                const uint16_t *src = s_stage565 + (size_t)by * W + x;
                for (int y = by; y < ye; y++) {
                    *drow++ = *src;
                    src += W;
                }
            }
        }
    }
}

static void tab5_render_bridge_tick_native(void)
{
    if (!tab5_render_bridge_buffers_init()) {
        return;
    }

    for (int y = 0; y < BSP_LCD_V_RES; y += TAB5_BRIDGE_CHUNK_ROWS) {
        const int rows = (y + TAB5_BRIDGE_CHUNK_ROWS <= BSP_LCD_V_RES) ? TAB5_BRIDGE_CHUNK_ROWS : (BSP_LCD_V_RES - y);
        const int pixels = BSP_LCD_H_RES * rows;
        if (!s_render_bounce_empty(s_line332, y * BSP_LCD_H_RES, pixels, NULL)) {
            ESP_LOGW(TAG, "render bounce callback returned false at y=%d", y);
            s_native_callback_failures++;
            if (s_shared_provider_active) {
                tab5_use_scaffold_provider("native callback returned false");
            }
            break;
        }

        tab5_row332_to_stage565(s_line332, s_line565, pixels);
        esp_lcd_panel_draw_bitmap(tab5_lcd_handles.panel, 0, y, BSP_LCD_H_RES, y + rows, s_line565);
    }

    (void)s_render_frame_done();
    s_bridge_frames++;
}

static void tab5_render_bridge_tick_rotated(int band_y0, int band_y1)
{
    if (!tab5_render_bridge_buffers_init()) {
        return;
    }

    if (s_stage565 == NULL) {
        ESP_LOGE(TAG, "Rotated path requires a staging frame buffer");
        return;
    }
    s_phase = TAB5_PHASE_COMPOSITE;

    if (s_provider_width != TAB5_SHARED_RENDER_W || s_provider_height != TAB5_SHARED_RENDER_H) {
        ESP_LOGW(TAG, "Unsupported provider geometry %dx%d", s_provider_width, s_provider_height);
        if (s_shared_provider_active) {
            tab5_use_scaffold_provider("unsupported shared provider geometry");
        }
        return;
    }

    /* With two DSI framebuffers we compose into the one that is not on screen
     * and hand it over with a pointer swap; with one we have no choice but to
     * write the live buffer. */
    void *target_fb = (s_dsi_fb_count > 1) ? s_dsi_fb[s_dsi_fb_next] : s_dsi_fb[0];

    if (band_y0 < 0) band_y0 = 0;
    if (band_y1 > s_provider_height) band_y1 = s_provider_height;
    if (band_y1 <= band_y0) {
        return;
    }

    /* The framebuffer that is not on screen is a frame behind, so it also needs
     * the rows the previous frame changed, or a partial update resurrects stale
     * pixels every time the two alternate. Only the rotate has to be repeated:
     * the stage buffer is a persistent full-screen image, and nothing has
     * touched those rows in it since they were composed. Keeping the two runs
     * apart rather than unioning them is the point -- a safety slice at the top
     * of the screen and a beat LED at the bottom used to redraw everything in
     * between, 130ms of work for 130 rows of it. */
    int prev_y0 = 0;
    int prev_y1 = 0;
    if (s_dsi_fb_count > 1) {
        prev_y0 = s_prev_band_y0;
        prev_y1 = s_prev_band_y1;
        s_prev_band_y0 = band_y0;
        s_prev_band_y1 = band_y1;
        if (prev_y0 < 0) prev_y0 = 0;
        if (prev_y1 > s_provider_height) prev_y1 = s_provider_height;
        /* Touching or overlapping runs cost less as one rotate than two. */
        if (prev_y1 > prev_y0 && prev_y0 <= band_y1 && prev_y1 >= band_y0) {
            if (prev_y0 < band_y0) band_y0 = prev_y0;
            if (prev_y1 > band_y1) band_y1 = prev_y1;
            prev_y0 = prev_y1 = 0;
        }
    }
    s_band_rows = (uint32_t)(band_y1 - band_y0);

    uint32_t composite_us = 0;
    uint32_t convert_us = 0;
    int64_t t0 = esp_timer_get_time();

    /* Work a band of rows at a time. Composing in chunks amortises the
     * per-call overhead of the Tulip compositor, and converting into an
     * internal-RAM band before a single bulk copy keeps the scalar LUT loop
     * off PSRAM -- a per-pixel store straight to PSRAM stalls on the cache
     * line fetch and was the single most expensive phase of the frame. */
    for (int y = band_y0; y < band_y1; y += TAB5_BRIDGE_CHUNK_ROWS) {
        const int rows = (y + TAB5_BRIDGE_CHUNK_ROWS <= band_y1)
                             ? TAB5_BRIDGE_CHUNK_ROWS
                             : (band_y1 - y);
        const int pixels = s_provider_width * rows;

        if (!s_render_bounce_empty(s_line332, y * s_provider_width, pixels, NULL)) {
            ESP_LOGW(TAG, "render bounce callback returned false at src y=%d", y);
            s_rotated_callback_failures++;
            if (s_shared_provider_active) {
                tab5_use_scaffold_provider("rotated callback returned false");
            }
            break;
        }
        const int64_t t1 = esp_timer_get_time();

        tab5_row332_to_stage565(s_line332, s_line565, pixels);
        memcpy(s_stage565 + (size_t)y * s_provider_width, s_line565,
               (size_t)pixels * sizeof(uint16_t));
        const int64_t t2 = esp_timer_get_time();

        composite_us += (uint32_t)(t1 - t0);
        convert_us += (uint32_t)(t2 - t1);
        t0 = t2;
    }
    s_us_composite = composite_us;
    s_us_convert = convert_us;

    if (target_fb != NULL) {
        const int64_t t_rot = esp_timer_get_time();
        s_phase = TAB5_PHASE_ROTATE;
        if (!tab5_ppa_rotate_to_fb(target_fb, band_y0, band_y1 - band_y0)) {
            tab5_cpu_rotate_band((uint16_t *)target_fb, band_y0, band_y1);
        }
        if (prev_y1 > prev_y0 && !tab5_ppa_rotate_to_fb(target_fb, prev_y0, prev_y1 - prev_y0)) {
            tab5_cpu_rotate_band((uint16_t *)target_fb, prev_y0, prev_y1);
        }
        const int64_t t_pres = esp_timer_get_time();
        s_phase = TAB5_PHASE_PRESENT;
        /* The buffer already lives inside the panel's framebuffer set, so this
         * is a cache write-back plus a framebuffer index switch -- no copy. */
        esp_lcd_panel_draw_bitmap(tab5_lcd_handles.panel, 0, 0, BSP_LCD_H_RES, BSP_LCD_V_RES, target_fb);
        s_us_rotate = (uint32_t)(t_pres - t_rot);
        s_us_present = (uint32_t)(esp_timer_get_time() - t_pres);
        if (s_dsi_fb_count > 1) {
            s_dsi_fb_next ^= 1u;
        }
    }

    s_phase = TAB5_PHASE_FRAME_DONE;
    (void)s_render_frame_done();
    s_bridge_frames++;
}

static void tab5_render_bridge_tick_rows(int y0, int y1)
{
    if (s_provider_width == BSP_LCD_H_RES && s_provider_height == BSP_LCD_V_RES) {
        tab5_render_bridge_tick_native();
        return;
    }

    tab5_render_bridge_tick_rotated(y0, y1);
}

static bool tab5_on_refresh_done(esp_lcd_panel_handle_t panel,
                                 esp_lcd_dpi_panel_event_data_t *edata,
                                 void *user_ctx)
{
    (void)panel;
    (void)edata;
    (void)user_ctx;
    s_vsync_count++;
    BaseType_t higher_priority_woken = pdFALSE;
    xSemaphoreGiveFromISR(s_vsync_sem, &higher_priority_woken);
    return higher_priority_woken == pdTRUE;
}

/* The BSP knows the ILI9881C and the ST7123, but Tab5 units built from
 * 2026-04-28 carry an ST7121 that it has no path for -- and the ST7123 path
 * leaves that panel lit and blank, because its unlock command carries the
 * controller's own part number and an ST7121 quietly ignores an ST7123's. So
 * this panel gets brought up here, with the numbers M5's own BSP uses for it:
 * a slower DSI lane rate than the ST7123 wants and a different vertical
 * blanking. Everything either panel needs afterwards is identical, so only the
 * creation differs. */
/* M5's own firmware runs both ST712x panels at this rate; the BSP's 1000 is
 * the number for the v1 panel it was written against. */
#define TAB5_ST712X_LANE_BITRATE_MBPS 965
#define TAB5_ST7121_DPI_CLOCK_MHZ 70
/* DCS sleep-out, and the settle the datasheet asks for after it. */
#define TAB5_LCD_CMD_SLPOUT 0x11
#define TAB5_SLPOUT_SETTLE_MS 120

static esp_err_t tab5_display_new_st7121(bsp_lcd_handles_t *out)
{
    ESP_RETURN_ON_ERROR(bsp_feature_enable(BSP_FEATURE_LCD, true), TAG, "LCD power failed");
    ESP_RETURN_ON_ERROR(bsp_display_brightness_init(), TAG, "Brightness init failed");

    if (s_dsi_phy_pwr_chan == NULL) {
        const esp_ldo_channel_config_t ldo_cfg = {
            .chan_id = BSP_MIPI_DSI_PHY_PWR_LDO_CHAN,
            .voltage_mv = BSP_MIPI_DSI_PHY_PWR_LDO_VOLTAGE_MV,
        };
        ESP_RETURN_ON_ERROR(esp_ldo_acquire_channel(&ldo_cfg, &s_dsi_phy_pwr_chan), TAG,
                            "DSI PHY power failed");
    }

    const esp_lcd_dsi_bus_config_t bus_config = {
        .bus_id = 0,
        .num_data_lanes = BSP_LCD_MIPI_DSI_LANE_NUM,
        .phy_clk_src = MIPI_DSI_PHY_CLK_SRC_DEFAULT,
        .lane_bit_rate_mbps = TAB5_ST712X_LANE_BITRATE_MBPS,
    };
    ESP_RETURN_ON_ERROR(esp_lcd_new_dsi_bus(&bus_config, &out->mipi_dsi_bus), TAG,
                        "New DSI bus failed");

    const esp_lcd_dbi_io_config_t dbi_config = {
        .virtual_channel = 0,
        .lcd_cmd_bits = 8,
        .lcd_param_bits = 8,
    };
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_io_dbi(out->mipi_dsi_bus, &dbi_config, &out->io), TAG,
                        "New panel IO failed");

    const esp_lcd_dpi_panel_config_t dpi_config = {
        .virtual_channel = 0,
        .dpi_clk_src = MIPI_DSI_DPI_CLK_SRC_DEFAULT,
        .dpi_clock_freq_mhz = TAB5_ST7121_DPI_CLOCK_MHZ,
        .in_color_format = LCD_COLOR_FMT_RGB565,
        .num_fbs = CONFIG_BSP_LCD_DPI_BUFFER_NUMS,
        .video_timing = {
            .h_size = BSP_LCD_H_RES,
            .v_size = BSP_LCD_V_RES,
            .hsync_pulse_width = 2,
            .hsync_back_porch = 40,
            .hsync_front_porch = 40,
            .vsync_pulse_width = 20,
            .vsync_back_porch = 24,
            .vsync_front_porch = 200,
        },
#if CONFIG_BSP_LCD_USE_DMA2D && (ESP_IDF_VERSION < ESP_IDF_VERSION_VAL(6, 0, 0))
        .flags.use_dma2d = true,
#endif
    };

    /* NULL init_cmds means the driver's own sequence, which is the one written
     * for this panel. */
    const st7121_vendor_config_t vendor_config = {
        .init_cmds = NULL,
        .init_cmds_size = 0,
        .mipi_config = {
            .dsi_bus = out->mipi_dsi_bus,
            .dpi_config = &dpi_config,
        },
    };
    const esp_lcd_panel_dev_config_t panel_config = {
        .reset_gpio_num = BSP_LCD_RST,   /* not wired: the driver software-resets */
        .rgb_ele_order = BSP_LCD_COLOR_SPACE,
        .bits_per_pixel = BSP_LCD_BITS_PER_PIXEL,
        .vendor_config = (void *)&vendor_config,
    };
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_st7121(out->io, &panel_config, &out->panel), TAG,
                        "New ST7121 panel failed");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_reset(out->panel), TAG, "Panel reset failed");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_init(out->panel), TAG, "Panel init failed");

    ESP_LOGI(TAG, "ST7121 display initialized with resolution %dx%d",
             BSP_LCD_H_RES, BSP_LCD_V_RES);
    return ESP_OK;
}

void tab5_display_start(void)
{
    if (tab5_display_started) {
        return;
    }

    const tab5_board_revision_t rev = tab5_detect_board_revision();
    const bsp_display_config_t cfg = {
        .dsi_bus = {
            .phy_clk_src = 0,
            .lane_bit_rate_mbps = (rev == TAB5_REV_V2_ST7123)
                                      ? TAB5_ST712X_LANE_BITRATE_MBPS
                                      : BSP_LCD_MIPI_DSI_LANE_BITRATE_MBPS,
        }
    };

    memset(&tab5_lcd_handles, 0, sizeof(tab5_lcd_handles));
    s_display_owned_locally = (rev == TAB5_REV_V2_ST7121);
    s_display_new_err = s_display_owned_locally
                            ? tab5_display_new_st7121(&tab5_lcd_handles)
                            : bsp_display_new_with_handles(&cfg, &tab5_lcd_handles);
    if (s_display_new_err != ESP_OK) {
        ESP_LOGE(TAG, "Tab5 display start failed: %s", esp_err_to_name(s_display_new_err));
        return;
    }

    if (rev == TAB5_REV_V2_ST7123 && tab5_lcd_handles.io != NULL) {
        /* The BSP's ST7123 table sends SLPOUT with no settle time and DISPON
         * straight after it, where M5's copy of the same table waits 100ms and
         * the panel driver's own default waits 120. A panel told to turn its
         * display on while it is still waking is exactly the failure this port
         * spent a day on, so give it that time. SLPOUT on an already-awake
         * panel is a no-op, and the cost is 120ms of boot on a v2 board.
         *
         * Untested: there is no ST7123 unit here, only the ST7121 that
         * replaced it in Tab5 units built from 2026-04-28. */
        esp_lcd_panel_io_tx_param(tab5_lcd_handles.io, TAB5_LCD_CMD_SLPOUT, NULL, 0);
        vTaskDelay(pdMS_TO_TICKS(TAB5_SLPOUT_SETTLE_MS));
    }

    s_disp_on_err = esp_lcd_panel_disp_on_off(tab5_lcd_handles.panel, true);

    if (s_vsync_sem == NULL) {
        s_vsync_sem = xSemaphoreCreateBinary();
    }
    if (s_vsync_sem != NULL) {
        const esp_lcd_dpi_panel_event_callbacks_t cbs = {
            .on_refresh_done = tab5_on_refresh_done,
        };
        if (esp_lcd_dpi_panel_register_event_callbacks(tab5_lcd_handles.panel, &cbs, NULL) != ESP_OK) {
            ESP_LOGW(TAG, "No panel refresh callback; falling back to timed pacing");
            vSemaphoreDelete(s_vsync_sem);
            s_vsync_sem = NULL;
        }
    }

    /* The vendor panel drivers hand back the DPI panel handle itself, so the
     * DSI framebuffers can be claimed directly and composed into in place. */
    s_dsi_fb_count = 0;
    if (esp_lcd_dpi_panel_get_frame_buffer(tab5_lcd_handles.panel, 2,
                                           &s_dsi_fb[0], &s_dsi_fb[1]) == ESP_OK) {
        s_dsi_fb_count = 2;
    } else if (esp_lcd_dpi_panel_get_frame_buffer(tab5_lcd_handles.panel, 1,
                                                  &s_dsi_fb[0]) == ESP_OK) {
        s_dsi_fb[1] = NULL;
        s_dsi_fb_count = 1;
        ESP_LOGW(TAG, "Only one DSI frame buffer; expect tearing "
                      "(raise CONFIG_BSP_LCD_DPI_BUFFER_NUMS to 2)");
    } else {
        ESP_LOGE(TAG, "Could not obtain any DSI frame buffer");
    }
    s_dsi_fb_next = (s_dsi_fb_count > 1) ? 1u : 0u;

    tab5_ppa_client_open();

    bsp_display_backlight_on();
    brightness = 5;
    tab5_display_brightness(brightness);
    tab5_display_started = true;
    ESP_LOGI(TAG, "Tab5 display initialized (fbs=%u, rotation=%s)",
             (unsigned)s_dsi_fb_count, s_ppa_srm ? "PPA" : "CPU");
}

void tab5_display_stop(void)
{
    if (!tab5_display_started) {
        return;
    }

    if (s_ppa_srm != NULL) {
        ppa_unregister_client(s_ppa_srm);
        s_ppa_srm = NULL;
    }
    if (s_display_owned_locally) {
        if (tab5_lcd_handles.panel != NULL) {
            esp_lcd_panel_del(tab5_lcd_handles.panel);
        }
        if (tab5_lcd_handles.io != NULL) {
            esp_lcd_panel_io_del(tab5_lcd_handles.io);
        }
        if (tab5_lcd_handles.mipi_dsi_bus != NULL) {
            esp_lcd_del_dsi_bus(tab5_lcd_handles.mipi_dsi_bus);
        }
        s_display_owned_locally = false;
    } else {
        bsp_display_delete();
    }
    tab5_render_bridge_buffers_deinit();
    s_dsi_fb[0] = NULL;
    s_dsi_fb[1] = NULL;
    s_dsi_fb_count = 0;
    s_dsi_fb_next = 0;
    memset(&tab5_lcd_handles, 0, sizeof(tab5_lcd_handles));
    tab5_display_started = false;
}

/* Raw DCS access to the panel, for working out on a live board what a panel
 * that refuses to light is actually doing -- 0x0A (RDDPM) says whether it is
 * awake and whether its display is on, which no counter here can. */
int tab5_display_panel_cmd(int cmd, const unsigned char *data, unsigned int len)
{
    if (tab5_lcd_handles.io == NULL) {
        return (int)ESP_ERR_INVALID_STATE;
    }
    return (int)esp_lcd_panel_io_tx_param(tab5_lcd_handles.io, cmd,
                                          (len > 0) ? data : NULL, len);
}

int tab5_display_panel_read(int cmd, unsigned char *out, unsigned int len)
{
    if (tab5_lcd_handles.io == NULL) {
        return (int)ESP_ERR_INVALID_STATE;
    }
    return (int)esp_lcd_panel_io_rx_param(tab5_lcd_handles.io, cmd, out, len);
}

uint32_t tab5_display_vsync_count(void)
{
    return s_vsync_count;
}

void tab5_display_init_errors(int *new_err, int *on_err)
{
    if (new_err != NULL) {
        *new_err = (int)s_display_new_err;
    }
    if (on_err != NULL) {
        *on_err = (int)s_disp_on_err;
    }
}

void tab5_display_brightness(unsigned char amount)
{
    /* Tulip uses a 1-9 scale today; map that onto the BSP 0-100 brightness range. */
    uint8_t percent = 0;
    if (amount > 0) {
        percent = (uint8_t)(amount * 11);
        if (percent > 100) {
            percent = 100;
        }
    }
    bsp_display_brightness_set(percent);
}

void run_tab5_display(void *arg)
{
    (void)arg;
    s_display_task_entries++;

    tab5_board_revision_t rev = tab5_detect_board_revision();
    ESP_LOGI(TAG, "Display startup on %s", tab5_board_revision_name(rev));

    tab5_display_start();
    if (!tab5_display_started) {
        ESP_LOGE(TAG, "Display task exiting because display start failed");
        vTaskDelete(NULL);
        return;
    }

    if (!s_tulip_shared_renderer_initialized) {
        display_init();
        display_tfb_set_default_bg_color(TAB5_REPL_TRANSPARENT_BG);
        tab5_clear_boot_background();
        s_tulip_shared_renderer_initialized = true;
        ESP_LOGI(TAG, "Initialized Tulip shared renderer state");
    }

    if (TAB5_FORCE_ROTATED_PROVIDER_TEST) {
        tab5_set_render_provider_with_geometry(tab5_scaffold_bounce_empty,
                                               tab5_scaffold_frame_done,
                                               TAB5_SHARED_RENDER_W,
                                               TAB5_SHARED_RENDER_H);
        s_shared_provider_active = false;
        ESP_LOGW(TAG, "Tab5 rotated provider self-test is enabled (%dx%d)",
                 TAB5_SHARED_RENDER_W,
                 TAB5_SHARED_RENDER_H);
    } else {
        ESP_LOGI(TAG,
                 "Shared provider symbols detected: bounce=%p frame_done=%p",
                 (void *)(uintptr_t)display_bounce_empty,
                 (void *)(uintptr_t)display_frame_done_generic);
        tab5_set_render_provider_with_geometry(display_bounce_empty,
                                               display_frame_done_generic,
                                               TAB5_SHARED_RENDER_W,
                                               TAB5_SHARED_RENDER_H);
        s_shared_provider_active = true;
        ESP_LOGI(TAG, "Tab5 display using shared Tulip render provider (%dx%d)",
                 TAB5_SHARED_RENDER_W,
                 TAB5_SHARED_RENDER_H);
    }

    if (!tab5_render_bridge_buffers_init()) {
        ESP_LOGE(TAG, "Display task exiting because bridge buffers are unavailable");
        vTaskDelete(NULL);
        return;
    }

    ESP_LOGI(TAG, "Tab5 display task is running (Tulip render bridge mode)");
    uint32_t frames_since_forced = 0;
    int forced_slice = 0;
    while (1) {
        const int64_t busy_start_us = esp_timer_get_time();

        /* Recompose only when something said it changed. At the REPL nothing
         * does, and skipping frees the memory bandwidth that audio needs. */
        frames_since_forced++;
        int band_y0 = 0;
        int band_y1 = TAB5_SHARED_RENDER_H;
        const bool have_damage = display_take_dirty_rows(&band_y0, &band_y1);
        /* Periodic safety redraw, one slice of the screen per turn. It waits
         * for a frame with no damage of its own, because merging a slice with a
         * band at the other end of the screen redraws everything in between --
         * which is the whole-frame cost this is here to avoid. The counter
         * resets only when a slice is actually drawn, so a busy screen delays
         * the net rather than postponing it forever (which is what resetting on
         * every band redraw used to do); past TAB5_FORCED_SLICE_MAX_FRAMES it
         * merges anyway and accepts the one wide frame. */
        bool forced = false;
        if (frames_since_forced >= TAB5_FORCED_SLICE_FRAMES &&
            (!have_damage || frames_since_forced >= TAB5_FORCED_SLICE_MAX_FRAMES)) {
            const int slice_y0 = forced_slice * TAB5_FORCED_SLICE_ROWS;
            int slice_y1 = slice_y0 + TAB5_FORCED_SLICE_ROWS;
            if (slice_y1 > TAB5_SHARED_RENDER_H) slice_y1 = TAB5_SHARED_RENDER_H;
            if (have_damage) {
                if (slice_y0 < band_y0) band_y0 = slice_y0;
                if (slice_y1 > band_y1) band_y1 = slice_y1;
            } else {
                band_y0 = slice_y0;
                band_y1 = slice_y1;
            }
            forced_slice = (forced_slice + 1) % TAB5_FORCED_SLICES;
            frames_since_forced = 0;
            forced = true;
        }
        if (have_damage || forced) {
            tab5_render_bridge_tick_rows(band_y0, band_y1);
        } else {
            s_frames_skipped++;
            /* Still run the end-of-frame hook. It drives tulip_frame_isr(),
             * which is what schedules lv_task_handler() and the Python frame
             * callback -- skipping it would stall the UI instead of idling it,
             * and LVGL would never get the chance to mark anything dirty. */
            (void)s_render_frame_done();
        }
        const int64_t busy_us = esp_timer_get_time() - busy_start_us;
        s_window_busy_us += busy_us;
        tab5_bridge_update_stats();
        if (busy_us > TAB5_FRAME_YIELD_US) {
            vTaskDelay(1);
        }

        /* Pace to the panel. Waiting on the refresh-done interrupt keeps the
         * loop aligned to the display instead of adding a fixed delay on top
         * of however long the frame took. */
        const int64_t wait_start_us = esp_timer_get_time();
        s_phase = TAB5_PHASE_WAIT_VSYNC;
        if (s_vsync_sem != NULL) {
            xSemaphoreTake(s_vsync_sem, pdMS_TO_TICKS(100));
        } else {
            vTaskDelay(pdMS_TO_TICKS(33));
        }
        s_us_wait = (uint32_t)(esp_timer_get_time() - wait_start_us);
    }
}

uint32_t tab5_display_task_entries(void)
{
    return s_display_task_entries;
}

uint32_t tab5_display_bridge_frames(void)
{
    return s_bridge_frames;
}

void tab5_display_render_stats(tab5_render_stats_t *out)
{
    if (out == NULL) {
        return;
    }
    out->composite_us = s_us_composite;
    out->convert_us = s_us_convert;
    out->rotate_us = s_us_rotate;
    out->present_us = s_us_present;
    out->wait_us = s_us_wait;
    out->frames_skipped = s_frames_skipped;
    out->band_rows = s_band_rows;
    out->ppa_failures = s_ppa_failures;
    out->ppa_timeouts = s_ppa_timeouts;
    out->phase = s_phase;
    out->ppa_last_err = s_ppa_last_err;
    out->ppa_recoveries = s_ppa_recoveries;
    out->ppa_stuck_y = s_ppa_stuck_y;
    out->ppa_stuck_rows = s_ppa_stuck_rows;
    out->ppa_last_y = s_ppa_last_y;
    out->ppa_last_rows = s_ppa_last_rows;
    out->dsi_fb_count = s_dsi_fb_count;
    out->ppa_active = (s_ppa_srm != NULL);
    out->vsync_paced = (s_vsync_sem != NULL);
}
