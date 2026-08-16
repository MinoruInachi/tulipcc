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
/* Redraw at least this often even when nothing reported a change, so a missed
 * display_mark_dirty() shows up as a stale second, not a frozen screen. */
#define TAB5_FORCED_REDRAW_FRAMES 30
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
static bool tab5_ppa_rotate_to_fb(void *dst_fb, int y_start, int rows)
{
    if (s_ppa_srm == NULL || dst_fb == NULL) {
        return false;
    }

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
        .mode = PPA_TRANS_MODE_BLOCKING,
    };

    const esp_err_t err = ppa_do_scale_rotate_mirror(s_ppa_srm, &srm);
    if (err != ESP_OK) {
        s_ppa_failures++;
        if (s_ppa_failures == 1) {
            ESP_LOGE(TAG, "PPA rotate failed (%s); falling back to CPU rotation",
                     esp_err_to_name(err));
        }
        return false;
    }
    return true;
}

/* CPU fallback for the rotation, kept only for the case where the PPA cannot
 * be registered. This is the slow path the PPA exists to replace. */
static void tab5_cpu_rotate_to_fb(uint16_t *dst_fb)
{
    for (int y = 0; y < s_provider_height; y++) {
        const uint16_t *src = s_stage565 + (size_t)y * s_provider_width;
        uint16_t *col = dst_fb + y;
        for (int x = 0; x < s_provider_width; x++) {
            col[(size_t)((s_provider_width - 1) - x) * BSP_LCD_H_RES] = src[x];
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

    /* The back buffer is one frame behind, so it also needs whatever the
     * previous frame changed -- otherwise a partial update would resurrect
     * stale pixels every time the buffers alternate. */
    if (s_dsi_fb_count > 1) {
        const int prev_y0 = s_prev_band_y0;
        const int prev_y1 = s_prev_band_y1;
        s_prev_band_y0 = band_y0;
        s_prev_band_y1 = band_y1;
        if (prev_y1 > prev_y0) {
            if (prev_y0 < band_y0) band_y0 = prev_y0;
            if (prev_y1 > band_y1) band_y1 = prev_y1;
        }
    }
    if (band_y0 < 0) band_y0 = 0;
    if (band_y1 > s_provider_height) band_y1 = s_provider_height;
    if (band_y1 <= band_y0) {
        return;
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
        if (!tab5_ppa_rotate_to_fb(target_fb, band_y0, band_y1 - band_y0)) {
            tab5_cpu_rotate_to_fb((uint16_t *)target_fb);
        }
        const int64_t t_pres = esp_timer_get_time();
        /* The buffer already lives inside the panel's framebuffer set, so this
         * is a cache write-back plus a framebuffer index switch -- no copy. */
        esp_lcd_panel_draw_bitmap(tab5_lcd_handles.panel, 0, 0, BSP_LCD_H_RES, BSP_LCD_V_RES, target_fb);
        s_us_rotate = (uint32_t)(t_pres - t_rot);
        s_us_present = (uint32_t)(esp_timer_get_time() - t_pres);
        if (s_dsi_fb_count > 1) {
            s_dsi_fb_next ^= 1u;
        }
    }

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
    BaseType_t higher_priority_woken = pdFALSE;
    xSemaphoreGiveFromISR(s_vsync_sem, &higher_priority_woken);
    return higher_priority_woken == pdTRUE;
}

void tab5_display_start(void)
{
    if (tab5_display_started) {
        return;
    }

    const bsp_display_config_t cfg = {
        .dsi_bus = {
            .phy_clk_src = 0,
            .lane_bit_rate_mbps = BSP_LCD_MIPI_DSI_LANE_BITRATE_MBPS,
        }
    };

    memset(&tab5_lcd_handles, 0, sizeof(tab5_lcd_handles));
    if (bsp_display_new_with_handles(&cfg, &tab5_lcd_handles) != ESP_OK) {
        ESP_LOGE(TAG, "Tab5 display start failed");
        return;
    }

    esp_lcd_panel_disp_on_off(tab5_lcd_handles.panel, true);

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

    const ppa_client_config_t ppa_cfg = {
        .oper_type = PPA_OPERATION_SRM,
        .max_pending_trans_num = 1,
    };
    if (ppa_register_client(&ppa_cfg, &s_ppa_srm) != ESP_OK) {
        s_ppa_srm = NULL;
        ESP_LOGW(TAG, "PPA unavailable; rotation will run on the CPU");
    }

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
    bsp_display_delete();
    tab5_render_bridge_buffers_deinit();
    s_dsi_fb[0] = NULL;
    s_dsi_fb[1] = NULL;
    s_dsi_fb_count = 0;
    s_dsi_fb_next = 0;
    memset(&tab5_lcd_handles, 0, sizeof(tab5_lcd_handles));
    tab5_display_started = false;
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
    while (1) {
        const int64_t busy_start_us = esp_timer_get_time();

        /* Recompose only when something said it changed. At the REPL nothing
         * does, and skipping frees the memory bandwidth that audio needs. */
        const bool forced = (++frames_since_forced >= TAB5_FORCED_REDRAW_FRAMES);
        int band_y0 = 0;
        int band_y1 = TAB5_SHARED_RENDER_H;
        const bool have_damage = display_take_dirty_rows(&band_y0, &band_y1);
        if (forced) {
            /* Periodic safety redraw: a mutator that forgot to report its
             * damage costs half a second of staleness, not a frozen screen.
             * The counter resets only here, not on every band redraw -- doing
             * that meant anything that damaged a few rows more often than
             * twice a second (a blinking cursor, drums.py's beat LEDs)
             * postponed the safety net forever, which is exactly when it is
             * needed. */
            band_y0 = 0;
            band_y1 = TAB5_SHARED_RENDER_H;
            frames_since_forced = 0;
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
        s_window_busy_us += esp_timer_get_time() - busy_start_us;
        tab5_bridge_update_stats();

        /* Pace to the panel. Waiting on the refresh-done interrupt keeps the
         * loop aligned to the display instead of adding a fixed delay on top
         * of however long the frame took. */
        const int64_t wait_start_us = esp_timer_get_time();
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
    out->dsi_fb_count = s_dsi_fb_count;
    out->ppa_active = (s_ppa_srm != NULL);
    out->vsync_paced = (s_vsync_sem != NULL);
}
