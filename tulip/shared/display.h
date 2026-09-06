// display.h

#ifndef __DISPLAYH__
#define __DISPLAYH__
#include <stdint.h>
// Before the includes: ui.h pulls in bresenham.h, whose prototypes need the
// pixel type, and it gets here through this header's own include guard.
// The BG plane's pixel format. Every board but the Tab5 keeps the 8-bit RGB332
// palette its RGB panel scans out directly. The Tab5's MIPI-DSI panel takes
// RGB565 and its compositor was expanding the palette on the way out anyway, so
// there the plane holds the native 16-bit pixel: LVGL's antialiased text and the
// camera preview land in their own colours instead of the nearest of 256.
//
// The Python API keeps the 0-255 palette index everywhere (PX() expands one to
// the native pixel, px_to_pal() rounds one back), so a program written for the
// palette draws the same on both. On a 16-bit board a colour argument can also
// be an (r, g, b) tuple -- see px_from_rgb().
#if defined(TAB5)
#define TULIP_RGB565
#else
#define RGB332
#endif

#ifdef TULIP_RGB565
typedef uint16_t tulip_px_t;
#else
typedef uint8_t tulip_px_t;
#endif

#include <stdio.h>
#include <time.h>
#include <string.h>
#include "lodepng.h"
#include "tulip_helpers.h"
#include "polyfills.h"
#include "ui.h"
#include <inttypes.h>
#include "lvgl.h"

#if defined(ESP_PLATFORM) && !defined(TAB5)
#include "esp32s3_display.h"
#elif defined(ESP_PLATFORM)
// TAB5 (ESP32-P4) drives a MIPI-DSI panel from display_tab5.c, so none of the
// S3 RGB-panel header applies. The one thing shared/display.c needs from a
// backend on ESP is the pixel-clock hook; TAB5 implements it as a no-op in
// shared_renderer_tab5_glue.c since a DSI panel has no equivalent knob.
void esp_display_set_clock(uint8_t mhz);
#else
#define IRAM_ATTR
#endif

// A palette index: the default BG fill, and what bg_clear() falls back to.
extern uint8_t bg_pal_color;
// Native pixels, since they are what gets written into TFBfg/TFBbg.
extern tulip_px_t tfb_fg_pal_color;
extern tulip_px_t tfb_bg_pal_color;
extern tulip_px_t ansi_active_bg_color; 
extern tulip_px_t ansi_active_fg_color; 
extern int16_t ansi_active_format;

#define TULIP_TEAL 9
static const uint8_t ansi_pal[256] = {
0, 128, 16, 144, 2, 130, 18, 219, 146, 224, 28, 252, 3, 227, 31, 255, 0, 1, 2, 2, 3, 3, 8, 9, 10, 10, 
11, 11, 16, 17, 18, 18, 19, 19, 20, 21, 22, 22, 23, 23, 24, 25, 26, 26, 27, 27, 28, 29, 30, 30, 31, 31, 
64, 65, 66, 66, 67, 67, 72, 73, 74, 74, 75, 75, 80, 81, 82, 82, 83, 83, 84, 85, 86, 86, 87, 87, 88, 89, 
90, 90, 91, 91, 92, 93, 94, 94, 95, 95, 128, 129, 130, 130, 131, 131, 136, 137, 138, 138, 139, 139, 144, 
145, 146, 146, 147, 147, 148, 149, 150, 150, 151, 151, 152, 153, 154, 154, 155, 155, 156, 157, 158, 158, 
159, 159, 160, 161, 162, 162, 163, 163, 168, 169, 170, 170, 171, 171, 176, 177, 178, 178, 179, 179, 180, 
181, 182, 182, 183, 183, 184, 185, 186, 186, 187, 187, 188, 189, 190, 190, 191, 191, 192, 193, 194, 194, 
195, 195, 200, 201, 202, 202, 203, 203, 208, 209, 210, 210, 211, 211, 212, 213, 214, 214, 215, 215, 216, 
217, 218, 218, 219, 219, 220, 221, 222, 222, 223, 223, 224, 225, 226, 226, 227, 227, 232, 233, 234, 234, 
235, 235, 240, 241, 242, 242, 243, 243, 244, 245, 246, 246, 247, 247, 248, 249, 250, 250, 251, 251, 252, 
253, 254, 254, 255, 255, 0, 0, 0, 36, 36, 36, 73, 73, 73, 109, 109, 109, 146, 146, 146, 146, 182, 182, 
182, 219, 219, 219, 255, 255
};

#define TARGET_DESKTOP_FPS 28.0

extern int16_t last_touch_x[3];
extern int16_t last_touch_y[3];
extern uint8_t touch_held;

void display_reset_sprites();
void display_reset_tfb();
void display_tfb_set_default_bg_color(uint8_t color);
void display_reset_bg();
void display_tfb_update(int8_t tfb_row_hint);
uint8_t display_tfb_visible_cols(void);
uint8_t display_tfb_visible_rows(void);
void display_set_clock(uint8_t mhz) ;
uint8_t lvgl_focused();

void display_set_bg_pixel_pal(uint16_t x, uint16_t y, uint8_t pal_idx);
void display_set_bg_pixel_px(uint16_t x, uint16_t y, tulip_px_t px);
void display_set_bg_pixel(uint16_t x, uint16_t y, uint8_t r, uint8_t g, uint8_t b);
void display_get_bg_pixel(uint16_t x, uint16_t y, uint8_t *r, uint8_t *g, uint8_t *b);
uint8_t display_get_bg_pixel_pal(uint16_t x, uint16_t y);
tulip_px_t display_get_bg_pixel_px(uint16_t x, uint16_t y);
void display_invert_bg(uint16_t x, uint16_t y, uint16_t w, uint16_t h) ;

void display_get_bg_bitmap_raw(uint16_t x, uint16_t y, uint16_t w, uint16_t h, uint8_t *data);
void display_set_bg_bitmap_rgba(uint16_t x, uint16_t y, uint16_t w, uint16_t h, uint8_t* data);
void display_set_bg_bitmap_raw(uint16_t x, uint16_t y, uint16_t w, uint16_t h, uint8_t* data);
void display_bg_bitmap_blit(uint16_t x,uint16_t y,uint16_t w,uint16_t h,uint16_t x1,uint16_t y1);
void display_bg_bitmap_blit_alpha(uint16_t x,uint16_t y,uint16_t w,uint16_t h,uint16_t x1,uint16_t y1);

void display_load_sprite_rgba(uint32_t mem_pos, uint32_t len, uint8_t* data);
void display_load_sprite_raw(uint32_t mem_pos, uint32_t len, uint8_t* data);
void display_screenshot(char * screenshot_fn, int16_t x, int16_t y, int16_t w, int16_t h);
void display_tfb_str(unsigned char*str, uint16_t len, uint8_t format, tulip_px_t fg_color, tulip_px_t bg_color);
// The console driven as a terminal: cursor addressing, a scroll region, an
// alternate screen, and answers to send back. See "The terminal" in display.c.
void display_term_start(uint8_t reset);
void display_term_stop(uint8_t reset);
uint8_t display_term_flags(void);
uint8_t display_term_take_reply(char *out, uint8_t max);
#define TERM_REPLY_BUF 40
uint8_t display_tfb_char_cells(const char *s, uint8_t *bytes, uint16_t *cp);
uint16_t display_tfb_place_str(const char *str, uint16_t x, uint16_t y);
uint16_t display_tfb_read_char(uint16_t x, uint16_t y, char *out);

void display_tfb_new_row();
void display_run();
void display_init();
void setup_lvgl();
void display_brightness(uint8_t amount);

void unpack_rgb_332_repeat(uint8_t px0, uint8_t *r, uint8_t *g, uint8_t *b);
void unpack_pal_idx(uint16_t pal_idx, uint8_t *r, uint8_t *g, uint8_t *b);
void unpack_ansi_idx(uint8_t ansi_idx, uint8_t *r, uint8_t *g, uint8_t *b);
bool display_bounce_empty(void *bounce_buf, int pos_px, int len_bytes, void *user_ctx);
bool display_frame_done_generic();
void display_swap();
uint8_t rgb565to332(uint16_t rgb565);
uint8_t color_332(uint8_t red, uint8_t green, uint8_t blue);
// Between the 0-255 palette and the plane's native pixel. On an RGB332 board
// these are the identity and r,g,b packs to 3-3-2; on a 16-bit board a palette
// index expands by unpack_rgb_332_repeat() -- the bit replication Tulip Desktop
// and the web build show the palette with -- and ui.pal_to_lv() expands it the
// same way there, so a colour picked from the palette is the same pixel whether
// Tulip or LVGL drew it.
tulip_px_t pal_to_px(uint8_t pal_idx);
uint8_t px_to_pal(tulip_px_t px);
tulip_px_t px_from_rgb(uint8_t r, uint8_t g, uint8_t b);
void px_to_rgb(tulip_px_t px, uint8_t *r, uint8_t *g, uint8_t *b);
#ifdef TULIP_RGB565
#define PX(pal_idx) pal_to_px(pal_idx)
#else
#define PX(pal_idx) ((tulip_px_t)(pal_idx))
#endif
void display_teardown(void);

uint8_t check_dim_xy(uint16_t x, uint16_t y);
uint8_t check_dim_xywh(uint16_t x, uint16_t y, uint16_t w, uint16_t h);
uint8_t collide_mask_get(uint8_t a, uint8_t b);

#ifdef ESP_PLATFORM
void enable_mouse_pointer();
void disable_mouse_pointer();
extern int16_t mouse_x_pos, mouse_y_pos;
#endif

extern const unsigned char font_8x12_r[256][12];
extern const uint16_t font_12x16_r[256][16];
extern const unsigned char portfolio_glyph_bitmap[1792];

#define TFB_FONT_8X12 0
#define TFB_FONT_PORTFOLIO 1
#define TFB_FONT_12X16 2
// The three Japanese console fonts, all drawing their Japanese from the efont
// Biwidth 16 face out of u8fontdata_jp.c. JP16 is the face at its designed size:
// halfwidth cells 8px wide, a fullwidth glyph spanning two of them, 160x45 on a
// Tab5. JP32 pixel doubles it to 16px cells and 80x22 -- the same design, twice
// the size, since a face drawn at 16 and doubled keeps the exact 1:2
// half-to-fullwidth ratio that a second face at 32 would not.
//
// JP12X16 is the odd one: it is font 2's console -- 12px cells, 106x45 on a Tab5,
// its Latin still drawn from font_12x16_r -- with Japanese added. It exists so
// that the console can start drawing Japanese without the geometry moving under
// whatever is already on screen. A fullwidth glyph is square, so at 12px cells it
// gets a 24px pair and sits centred in it rather than being stretched by half,
// and emboldened on the way -- the face strokes at 1px and the Latin beside it at
// 2. See jpfont_row_embolden().
//
// Unlike the CP437 fonts these three hold Unicode codepoints in the TFB.
#define TFB_FONT_JP16 3
#define TFB_FONT_JP32 4
#define TFB_FONT_JP12X16 5
#define TFB_FONT_MAX TFB_FONT_JP12X16

#define MAX_LINE_EMITS 60000

// We can address this many moving things on screen
#define SPRITES 32
// We assume we can store 16 unique 32x32 sprite tiles, you can swap these out from RAM
#define SPRITE_RAM_BYTES (32*32*SPRITES)

#if defined(TAB5)
#define H_RES 1280
#define V_RES 720
#elif defined(TDECK)
#define H_RES 320
#define V_RES 240
#else
#define H_RES 1024
#define V_RES 600
#endif

#ifdef TDECK
#define FONT_HEIGHT 8
#define FONT_WIDTH 6
#else
#define FONT_HEIGHT 12
#define FONT_WIDTH 8
#endif

// The margin the background plane carries past the visible screen: the scroll
// headroom, and the scratch the docs point at for offscreen blitting. It was a
// flat 128 x 100 on every board, which is 12.5% and 16.7% of a 1024x600 Tulip
// CC but only 10% and 13.9% of the Tab5's 1280x720 -- a bigger panel got
// proportionally less room to scroll into. Track the panel instead, with the
// old figures as a floor so no smaller board loses what it has. 1024x600 comes
// out at exactly 128 x 100, the T-Deck's 320x240 keeps its 128 x 100, and
// 1280x720 gets 160 x 120. Every user of these is written as
// H_RES+OFFSCREEN_X_PX / V_RES+OFFSCREEN_Y_PX, so nothing else has to change.
#define OFFSCREEN_X_PX ((H_RES)/8 > 128 ? (H_RES)/8 : 128)
#define OFFSCREEN_Y_PX ((V_RES)/6 > 100 ? (V_RES)/6 : 100)
#define DEFAULT_PIXEL_CLOCK_MHZ 28
#define BOUNCE_BUFFER_SIZE_PX (H_RES*12)
#define TFB_ROWS (V_RES/FONT_HEIGHT)
#define TFB_COLS (H_RES/FONT_WIDTH)

extern uint16_t PIXEL_CLOCK_MHZ;

#ifndef MIN
#define MIN(a, b) (((a) < (b)) ? (a) : (b))
#endif

#define CHECK_BIT(var,pos) ((var) & (1<<(pos)))


#ifdef TULIP_RGB565
#define BYTES_PER_PIXEL 2
// The transparent pixel of the overlay planes (TFB, sprites, LVGL). It is what
// palette entry 0x55 expands to (see pal_to_px), so the 0x55 that programs and
// ui.py already use for "see through" keeps meaning that -- and it is exactly
// the RGB565 LVGL paints for ui.pal_to_lv(0x55), which is how a transparent
// LVGL screen is told apart from a painted one in lv_flush_cb_8b().
#define ALPHA 0x4daa
#else
#define BYTES_PER_PIXEL 1
#define ALPHA 0x55
#endif

#define FLASH_FRAMES 12

#define FORMAT_INVERSE 0x80 
#define FORMAT_UNDERLINE 0x40
#define FORMAT_FLASH 0x20
#define FORMAT_BOLD 0x10 
#define FORMAT_STRIKE 0x08

#define SPRITE_IS_SPRITE 0x80
#define SPRITE_IS_WIREFRAME 0x40
#define SPRITE_IS_BEZIER 0x20
#define SPRITE_IS_ELLIPSE 0x10

extern uint8_t gpu_log;
extern uint8_t tfb_font;
extern uint8_t tfb_font_user_set;
extern uint8_t tfb_active;
extern uint8_t tfb_y_row; 
extern uint8_t tfb_x_col;
void display_tfb_refresh_cursor(void);
// The cursor colour while the IME holds the keyboard. Shared by the console and
// the editor so the indicator is the same wherever the typing is going.
#define IME_CURSOR_COLOR px_from_rgb(255,160,0) 
extern int32_t vsync_count;
extern uint8_t brightness;
extern float reported_fps;
extern float reported_gpu_usage;
/* Damage tracking. Backends may ignore this entirely; the Tab5 bridge uses it
 * to recompose only the rows that changed, which is the difference between a
 * ~90ms full frame and a ~2ms one-text-row update on that hardware. */
extern volatile uint8_t display_dirty;
/* Cleared when a caller does something whose damage cannot be expressed as a
 * range of display rows -- currently any non-identity background scroll, since
 * that breaks the bg-row to display-row correspondence. */
extern volatile uint8_t display_rows_trackable;
void display_mark_dirty_rows(int y0, int y1);
void display_mark_rows_untrackable(void);
/* Snapshot and clear the pending damage. Returns false when nothing changed. */
bool display_take_dirty_rows(int *y0, int *y1);
#define display_mark_dirty() display_mark_dirty_rows(0, V_RES)
extern uint8_t *collision_bitfield;

extern const uint16_t rgb332_rgb565_i[256];
// RAM for sprites and background FB
extern uint8_t *sprite_ids;  // IRAM
// Pixels, not bytes: sprite_mem[] and the mem_pos the Python side juggles index
// this by pixel, and SPRITE_RAM_BYTES counts pixels too (the name predates the
// 16-bit plane). Allocated at SPRITE_RAM_BYTES*sizeof(tulip_px_t).
extern tulip_px_t *sprite_ram; // in IRAM
extern tulip_px_t * bg; // in SPIRAM
extern tulip_px_t * bg_tfb; // in SPIRAM
extern uint16_t *sprite_x_px;//[SPRITES]; 
extern uint16_t *sprite_y_px;//[SPRITES]; 
extern uint16_t *sprite_w_px;//[SPRITES]; 
extern uint16_t *sprite_h_px;//[SPRITES]; 
extern uint8_t *sprite_vis;//[SPRITES];
extern uint32_t *sprite_mem;//[SPRITES];
// A Unicode codepoint per cell, not a byte: the Japanese console fonts need more
// than 256 of them, and TFB_WIDE_CONT marks the right half of a fullwidth cell.
// 0 still means "nothing here", which is also how a row's end is found.
extern uint16_t *TFB;//[TFB_ROWS][TFB_COLS];
extern tulip_px_t *TFBfg;//[TFB_ROWS][TFB_COLS];
extern tulip_px_t *TFBbg;//[TFB_ROWS][TFB_COLS];
extern uint8_t *TFBf;//[TFB_ROWS][TFB_COLS];
extern int16_t *x_offsets;//[V_RES];
extern int16_t *y_offsets;//[V_RES];
extern int16_t *x_speeds;//[V_RES];
extern int16_t *y_speeds;//[V_RES];
extern uint32_t **bg_lines;//[V_RES];
extern uint16_t *TFB_pxlen;
extern uint8_t *lines_bitmap;

#endif
