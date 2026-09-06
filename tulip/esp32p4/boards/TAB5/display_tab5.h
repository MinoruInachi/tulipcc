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
	uint32_t ppa_timeouts;  /* PPA rotations that did not finish in time */
	uint8_t phase;          /* what the display task is doing right now, see TAB5_PHASE_* */
	int ppa_last_err;       /* esp_err_t of the last failed PPA call (the log does not reach the console) */
	uint32_t ppa_recoveries;
	int ppa_stuck_y;        /* band of the first rotation that timed out: y_start, rows */
	int ppa_stuck_rows;
	int ppa_last_y;         /* band of the most recent rotation */
	int ppa_last_rows;
} tab5_render_stats_t;

/* Where the display task is. Read through tulip.tab5_render_stats() -- if the
 * screen has stopped, this says which step it never came back from. */
#define TAB5_PHASE_WAIT_VSYNC 0
#define TAB5_PHASE_COMPOSITE 1
#define TAB5_PHASE_ROTATE 2
#define TAB5_PHASE_PRESENT 3
#define TAB5_PHASE_FRAME_DONE 4

void tab5_display_render_stats(tab5_render_stats_t *out);

void run_tab5_display(void *arg);
void tab5_display_start(void);
void tab5_display_stop(void);
void tab5_display_brightness(unsigned char amount);

/* Raw DCS access to the panel and the return codes from display start-up,
 * both reachable from the REPL through tulip.tab5_lcd_*(). */
int tab5_display_panel_cmd(int cmd, const unsigned char *data, unsigned int len);
int tab5_display_panel_read(int cmd, unsigned char *out, unsigned int len);
void tab5_display_init_errors(int *new_err, int *on_err);
uint32_t tab5_display_vsync_count(void);
bool tab5_repl_menu_icon_touch_event(int16_t shared_x, int16_t shared_y, bool up);
uint32_t tab5_display_task_entries(void);
uint32_t tab5_display_bridge_frames(void);
