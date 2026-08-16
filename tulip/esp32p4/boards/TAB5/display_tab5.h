#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef bool (*tab5_render_bounce_empty_fn_t)(void *bounce_buf, int pos_px, int len_bytes, void *user_ctx);
typedef bool (*tab5_render_frame_done_fn_t)(void);

void tab5_set_render_provider(tab5_render_bounce_empty_fn_t bounce_empty_cb,
							  tab5_render_frame_done_fn_t frame_done_cb);
void tab5_set_render_provider_with_geometry(tab5_render_bounce_empty_fn_t bounce_empty_cb,
											tab5_render_frame_done_fn_t frame_done_cb,
											int provider_width,
											int provider_height);

typedef struct {
	uint32_t composite_us;
	uint32_t convert_us;
	uint32_t rotate_us;
	uint32_t present_us;
	uint32_t wait_us;
	uint32_t frames_skipped;
	uint32_t band_rows;
	uint32_t ppa_failures;
	uint8_t dsi_fb_count;
	bool ppa_active;
	bool vsync_paced;
} tab5_render_stats_t;

void tab5_display_render_stats(tab5_render_stats_t *out);

void run_tab5_display(void *arg);
void tab5_display_start(void);
void tab5_display_stop(void);
void tab5_display_brightness(unsigned char amount);
bool tab5_repl_menu_icon_touch_event(int16_t shared_x, int16_t shared_y, bool up);
uint32_t tab5_display_task_entries(void);
uint32_t tab5_display_bridge_frames(void);
