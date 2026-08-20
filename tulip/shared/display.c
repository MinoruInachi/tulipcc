#include "display.h"
#include "jpfont.h"
#include "keyscan.h"

uint8_t bg_pal_color;
uint8_t tfb_fg_pal_color;
uint8_t tfb_bg_pal_color;
static uint8_t tfb_default_bg_pal_color = TULIP_TEAL;
uint8_t ansi_active_bg_color; 
uint8_t ansi_active_fg_color; 
int16_t ansi_active_format;

// Escape sequences arrive in whatever chunks the writer hands over -- over ssh
// that is whatever the network gave us, so one can be split across two writes
// at any byte. This state carries a half-read sequence from one write to the
// next, at file scope so display_reset_tfb() can drop it.
static unsigned char esc_pending[48];   // a CSI whose final byte has not arrived
static uint8_t esc_pending_len = 0;
static uint8_t esc_string = 0;          // 1: inside ESC ] and friends, 2: saw its ESC
static uint8_t esc_drop = 0;            // a CSI longer than anything real; swallow it
static uint8_t esc_active = 0;          // a sequence owns the next byte

int16_t last_touch_x[3];
int16_t last_touch_y[3];
uint8_t touch_held;

#ifdef ESP_PLATFORM
uint8_t mouse_pointer_status = 0;
#endif

uint8_t tfb_active;
uint8_t tfb_y_row; 
uint8_t tfb_x_col;
int32_t vsync_count;
uint8_t brightness;
float reported_fps;
float reported_gpu_usage;

/* Set by every routine that changes what the screen should look like, cleared
 * by the frame loop once the change has been presented. Backends are free to
 * ignore it; the Tab5 bridge uses it to skip recomposing an unchanged frame,
 * which is the common case at the REPL. */
volatile uint8_t display_dirty = 1;
volatile uint8_t display_rows_trackable = 1;
static volatile int16_t display_dirty_y0 = 0;
static volatile int16_t display_dirty_y1 = V_RES;

/* Producers run on the MicroPython task, the consumer on the display task, and
 * on the ESP32-P4 those are genuinely concurrent on separate cores. Expanding
 * the range and raising the flag has to be one step, or a change can be dropped
 * between the consumer's snapshot and its reset. */
#ifdef ESP_PLATFORM
#include "freertos/FreeRTOS.h"
static portMUX_TYPE display_dirty_mux = portMUX_INITIALIZER_UNLOCKED;
#define DISPLAY_DIRTY_ENTER() portENTER_CRITICAL_SAFE(&display_dirty_mux)
#define DISPLAY_DIRTY_EXIT()  portEXIT_CRITICAL_SAFE(&display_dirty_mux)
#else
#define DISPLAY_DIRTY_ENTER() do {} while (0)
#define DISPLAY_DIRTY_EXIT()  do {} while (0)
#endif

void display_mark_rows_untrackable(void) {
    display_rows_trackable = 0;
    display_mark_dirty_rows(0, V_RES);
}

void display_mark_dirty_rows(int y0, int y1) {
    if(!display_rows_trackable) { y0 = 0; y1 = V_RES; }
    if(y0 < 0) y0 = 0;
    if(y1 > V_RES) y1 = V_RES;
    if(y1 <= y0) return;

    // Unlocked fast path. The drawing primitives funnel through here once per
    // pixel, so after the first pixel of a row the pending range already covers
    // the rest -- and taking a spinlock 921600 times costs far more than the
    // drawing itself. A miss here only falls through to the locked path below.
    if(display_dirty && y0 >= display_dirty_y0 && y1 <= display_dirty_y1) return;

    DISPLAY_DIRTY_ENTER();
    if(!display_dirty) {
        display_dirty_y0 = (int16_t)y0;
        display_dirty_y1 = (int16_t)y1;
        display_dirty = 1;
    } else {
        if(y0 < display_dirty_y0) display_dirty_y0 = (int16_t)y0;
        if(y1 > display_dirty_y1) display_dirty_y1 = (int16_t)y1;
    }
    DISPLAY_DIRTY_EXIT();
}

bool display_take_dirty_rows(int *y0, int *y1) {
    bool was_dirty;
    DISPLAY_DIRTY_ENTER();
    was_dirty = display_dirty != 0;
    if(was_dirty) {
        *y0 = display_dirty_y0;
        *y1 = display_dirty_y1;
        display_dirty = 0;
        display_dirty_y0 = V_RES;
        display_dirty_y1 = 0;
    }
    DISPLAY_DIRTY_EXIT();
    return was_dirty;
}

uint8_t *collision_bitfield;
// RAM for sprites and background FB
uint8_t *sprite_ram; // in IRAM
uint8_t * bg; // in SPIRAM
uint8_t * bg_tfb;

uint8_t * sprite_ids;
uint16_t *sprite_x_px;//[SPRITES]; 
uint16_t *sprite_y_px;//[SPRITES]; 
uint16_t *sprite_w_px;//[SPRITES]; 
uint16_t *sprite_h_px;//[SPRITES]; 
uint8_t *sprite_vis;//[SPRITES];
uint32_t *sprite_mem;//[SPRITES];

// LVGL renders into this small band buffer (PARTIAL mode); lv_flush_cb_8b blits
// each finished band into bg. LVGL can't render directly into bg: on hardware the
// RGB panel scans bg out continuously, so mid-render widget states would be
// visible as flicker.
uint8_t * lv_buf;
#define LV_BUF_BYTES (((H_RES+OFFSCREEN_X_PX)*(V_RES+OFFSCREEN_Y_PX)*BYTES_PER_PIXEL)/10)

#ifdef TAB5
// LVGL's own plane, laid over the BG at composite time instead of drawn into it.
//
// LVGL used to flush straight into bg, which made the two one buffer where
// whatever was written last won. Anything redrawing the BG blacked the task bar
// buttons out and nothing ever repainted them -- they came back for a moment
// under a finger, because a touch is what invalidated them. On a scrolled row it
// was worse: bg is read through x_offsets[], so parallax's sky dragged the
// buttons sideways across the screen and off the edge. Giving LVGL a buffer in
// screen space, composited every frame, is what makes the two planes independent.
//
// ALPHA is a pixel LVGL is not drawing, so the BG plane shows through. The REPL
// background already worked that way; now any screen can, and one that wants the
// BG plane visible just leaves its own background ALPHA (see ui.py).
uint8_t *lv_overlay;
// The span of non-ALPHA pixels in each row, so compositing a mostly empty overlay
// -- a game with nothing on it but the task bar -- costs the corner rather than
// the whole width. Recomputed for the rows a flush touches, which happens far
// less often than a frame.
static uint16_t lv_overlay_x0[V_RES + OFFSCREEN_Y_PX];
static uint16_t lv_overlay_x1[V_RES + OFFSCREEN_Y_PX];  // exclusive; == x0 is empty
#define LV_OVERLAY_STRIDE (H_RES + OFFSCREEN_X_PX)
#endif

uint16_t *TFB;//[TFB_ROWS][TFB_COLS];
uint8_t *TFBfg;//[TFB_ROWS][TFB_COLS];
uint8_t *TFBbg;//[TFB_ROWS][TFB_COLS];
uint8_t *TFBf;//[TFB_ROWS][TFB_COLS];
uint16_t *TFB_pxlen;
int16_t *x_offsets;//[V_RES];
int16_t *y_offsets;//[V_RES];
int16_t *x_speeds;//[V_RES];
int16_t *y_speeds;//[V_RES];

uint32_t **bg_lines;//[V_RES];

// Defaults for runtime display params
uint16_t PIXEL_CLOCK_MHZ = DEFAULT_PIXEL_CLOCK_MHZ;
uint8_t tfb_active = 1;
uint8_t gpu_log = 0;
#ifdef TAB5
// The Tab5's 7" panel is 1280x720, so the 8x12 font puts a 160x60 console on it
// -- correct, but small at the distance you actually hold the thing. Start on
// the 12x16 font instead (106x45). tulip.tfb_font(0) switches back at runtime.
uint8_t tfb_font = TFB_FONT_12X16;
#else
uint8_t tfb_font = TFB_FONT_8X12;
#endif

int16_t lvgl_is_repl = 0;

// Set once tulip.tfb_font() has been called. The console promotes itself to the
// Japanese font the first time a codepoint arrives that CP437 cannot hold, since
// printing Japanese into a font with no Japanese in it just puts blanks on the
// screen -- but a font the user chose out loud is never second-guessed.
uint8_t tfb_font_user_set = 0;

// The Japanese fonts are the only ones where a cell is not the whole character:
// a fullwidth glyph is two cells wide, so these are the halfwidth widths.
static inline uint8_t tfb_font_width_current(void) {
    if(tfb_font == TFB_FONT_PORTFOLIO) return 6;
    if(tfb_font == TFB_FONT_12X16) return 12;
    if(tfb_font == TFB_FONT_JP32) return 16;
    return 8;
}

static inline uint8_t tfb_font_height_current(void) {
    if(tfb_font == TFB_FONT_PORTFOLIO) return 8;
    if(tfb_font == TFB_FONT_12X16) return 16;
    if(tfb_font == TFB_FONT_JP16) return 16;
    if(tfb_font == TFB_FONT_JP32) return 32;
    return 12;
}

// True where a TFB cell holds a Unicode codepoint rather than a CP437 byte.
static inline uint8_t tfb_font_is_unicode(void) {
    return tfb_font == TFB_FONT_JP16 || tfb_font == TFB_FONT_JP32;
}

uint8_t display_tfb_visible_cols(void) {
    uint8_t cols = H_RES / tfb_font_width_current();
    return MIN(cols, TFB_COLS);
}

uint8_t display_tfb_visible_rows(void) {
    uint8_t rows = V_RES / tfb_font_height_current();
    return MIN(rows, TFB_ROWS);
}

// The console's pixel rows live in bg_tfb as a ring. Scrolling used to rebuild
// every visible pixel row from glyphs and push the result back into PSRAM -- a
// megabyte of work for a change that only ever adds one line of text. Since the
// pixels of rows 0..n-2 after a scroll are exactly what rows 1..n-1 already held,
// a scroll instead advances tfb_ring_top by one font height and rasterises only
// the row that actually changed.
//
// Screen pixel row y is stored in bg_tfb/TFB_pxlen at row tfb_ring_row(y). Rows
// from tfb_ring_h up (the remainder when the font height does not divide V_RES)
// are outside the ring and map straight through. tfb_ring_top is always < tfb_ring_h.
static volatile uint16_t tfb_ring_top = 0;
static volatile uint16_t tfb_ring_h = 0;

// always_inline because display_bounce_empty may run with the flash cache off;
// an out-of-line copy of this would live in flash and could not be called there.
static inline __attribute__((always_inline))
uint16_t tfb_ring_row_in(uint16_t y, uint16_t top, uint16_t h) {
    if(h == 0 || y >= h) return y;
    uint16_t row = y + top;
    return (row >= h) ? (uint16_t)(row - h) : row;
}

static inline uint16_t tfb_ring_row(uint16_t y) {
    return tfb_ring_row_in(y, tfb_ring_top, tfb_ring_h);
}

// lookup table for Tulip's "pallete" to the 16-bit colorspace needed by LVGL and T-deck

const uint16_t rgb332_rgb565_i[256] = {
    0x0000, 0x0a00, 0x1500, 0x1f00, 0x2001, 0x2a01, 0x3501, 0x3f01, 
    0x4002, 0x4a02, 0x5502, 0x5f02, 0x6003, 0x6a03, 0x7503, 0x7f03, 
    0x8004, 0x8a04, 0x9504, 0x9f04, 0xa005, 0xaa05, 0xb505, 0xbf05, 
    0xc006, 0xca06, 0xd506, 0xdf06, 0xe007, 0xea07, 0xf507, 0xff07, 
    0x0020, 0x0a20, 0x1520, 0x1f20, 0x2021, 0x2a21, 0x3521, 0x3f21, 
    0x4022, 0x4a22, 0x5522, 0x5f22, 0x6023, 0x6a23, 0x7523, 0x7f23, 
    0x8024, 0x8a24, 0x9524, 0x9f24, 0xa025, 0xaa25, 0xb525, 0xbf25, 
    0xc026, 0xca26, 0xd526, 0xdf26, 0xe027, 0xea27, 0xf527, 0xff27, 
    0x0048, 0x0a48, 0x1548, 0x1f48, 0x2049, 0x2a49, 0x3549, 0x3f49, 
    0x404a, 0x4a4a, 0x554a, 0x5f4a, 0x604b, 0x6a4b, 0x754b, 0x7f4b, 
    0x804c, 0x8a4c, 0x954c, 0x9f4c, 0xa04d, 0xaa4d, 0xb54d, 0xbf4d, 
    0xc04e, 0xca4e, 0xd54e, 0xdf4e, 0xe04f, 0xea4f, 0xf54f, 0xff4f, 
    0x0068, 0x0a68, 0x1568, 0x1f68, 0x2069, 0x2a69, 0x3569, 0x3f69, 
    0x406a, 0x4a6a, 0x556a, 0x5f6a, 0x606b, 0x6a6b, 0x756b, 0x7f6b, 
    0x806c, 0x8a6c, 0x956c, 0x9f6c, 0xa06d, 0xaa6d, 0xb56d, 0xbf6d, 
    0xc06e, 0xca6e, 0xd56e, 0xdf6e, 0xe06f, 0xea6f, 0xf56f, 0xff6f, 
    0x0090, 0x0a90, 0x1590, 0x1f90, 0x2091, 0x2a91, 0x3591, 0x3f91, 
    0x4092, 0x4a92, 0x5592, 0x5f92, 0x6093, 0x6a93, 0x7593, 0x7f93, 
    0x8094, 0x8a94, 0x9594, 0x9f94, 0xa095, 0xaa95, 0xb595, 0xbf95, 
    0xc096, 0xca96, 0xd596, 0xdf96, 0xe097, 0xea97, 0xf597, 0xff97, 
    0x00b0, 0x0ab0, 0x15b0, 0x1fb0, 0x20b1, 0x2ab1, 0x35b1, 0x3fb1, 
    0x40b2, 0x4ab2, 0x55b2, 0x5fb2, 0x60b3, 0x6ab3, 0x75b3, 0x7fb3, 
    0x80b4, 0x8ab4, 0x95b4, 0x9fb4, 0xa0b5, 0xaab5, 0xb5b5, 0xbfb5, 
    0xc0b6, 0xcab6, 0xd5b6, 0xdfb6, 0xe0b7, 0xeab7, 0xf5b7, 0xffb7, 
    0x00d8, 0x0ad8, 0x15d8, 0x1fd8, 0x20d9, 0x2ad9, 0x35d9, 0x3fd9, 
    0x40da, 0x4ada, 0x55da, 0x5fda, 0x60db, 0x6adb, 0x75db, 0x7fdb, 
    0x80dc, 0x8adc, 0x95dc, 0x9fdc, 0xa0dd, 0xaadd, 0xb5dd, 0xbfdd, 
    0xc0de, 0xcade, 0xd5de, 0xdfde, 0xe0df, 0xeadf, 0xf5df, 0xffdf, 
    0x00f8, 0x0af8, 0x15f8, 0x1ff8, 0x20f9, 0x2af9, 0x35f9, 0x3ff9, 
    0x40fa, 0x4afa, 0x55fa, 0x5ffa, 0x60fb, 0x6afb, 0x75fb, 0x7ffb, 
    0x80fc, 0x8afc, 0x95fc, 0x9ffc, 0xa0fd, 0xaafd, 0xb5fd, 0xbffd, 
    0xc0fe, 0xcafe, 0xd5fe, 0xdffe, 0xe0ff, 0xeaff, 0xf5ff, 0xffff 
};



uint8_t check_dim_xy(uint16_t x, uint16_t y) {
    if(x >= OFFSCREEN_X_PX + H_RES || y >= OFFSCREEN_Y_PX+V_RES) return 0;
    return 1;
}

uint8_t check_dim_xywh(uint16_t x, uint16_t y, uint16_t w, uint16_t h) {
    if(!check_dim_xy(x,y)) return 0;
    if(!check_dim_xy(x+w-1, y+h-1)) return 0;
    return 1;
}

// RRRGGGBB -> 
void unpack_rgb_332_repeat(uint8_t px0, uint8_t *r, uint8_t *g, uint8_t *b) {
    *r=0; *g=0; *b=0;
    *r = (px0 & 0xe0) | ((px0 & 0xe0)>>3) | ((px0&0xc0)>>6);
    *g = ((px0 & 0x1c) << 3) | (px0 & 0x1c) | ((px0&0x18) >> 3); 
    *b = (px0 & 0x03) | ((px0 & 0x03) << 2) | ((px0 & 0x03) << 4) | ((px0 & 0x03) << 6);
}

// Given a single uint (0-255 for RGB332, 0-65535 for RGB565), return r, g, b
void unpack_pal_idx(uint16_t pal_idx, uint8_t *r, uint8_t *g, uint8_t *b) {
    unpack_rgb_332_repeat(pal_idx & 0xff, r, g, b);
}

// Given a single uint (0-255 for RGB332, 0-65535 for RGB565), return r, g, b
void unpack_pal_idx_wide(uint16_t pal_idx, uint8_t *r, uint8_t *g, uint8_t *b) {
    unpack_rgb_332_repeat(pal_idx & 0xff, r, g, b);
}

// Given an ansi pal index (0-255 right now), return r g b
void unpack_ansi_idx(uint8_t ansi_idx, uint8_t *r, uint8_t *g, uint8_t *b) {
    unpack_rgb_332_repeat(ansi_idx, r, g, b);
}

// Return a packed 8-bit number for RRRGGGBB
uint8_t color_332(uint8_t red, uint8_t green, uint8_t blue) {
    uint8_t ret = 0;
    ret |= (red&0xe0);
    ret |= (green&0xe0) >> 3;
    ret |= (blue&0xc0) >> 6;
    return ret;
}


// RRRRRGGG GGGBBBBB -> RRRGGGBB
// >> 6

uint8_t rgb565to332(uint16_t rgb565) {
    return (rgb565 >> 8 & 0xe0) | (rgb565 >> 6 & 0x1c) | (rgb565 >> 3 & 0x3);
}

// Python callback
extern void tulip_frame_isr(); 

uint8_t spriteno_activated;

bool display_frame_done_generic() {
    // Scrolling changes the image every frame with no explicit draw call, so it
    // has to keep the frame marked dirty on its own.
    for(uint16_t i=0;i<V_RES;i++) {
        if(x_speeds[i] || y_speeds[i]) { display_mark_dirty(); break; }
    }
    // Update the scroll
    for(uint16_t i=0;i<V_RES;i++) {
        x_offsets[i] = x_offsets[i] + x_speeds[i];
        y_offsets[i] = y_offsets[i] + y_speeds[i];
        x_offsets[i] = x_offsets[i] % (H_RES+OFFSCREEN_X_PX);
        y_offsets[i] = y_offsets[i] % (V_RES+OFFSCREEN_Y_PX);
        bg_lines[i] = (uint32_t*)&bg[(H_RES+OFFSCREEN_X_PX)*BYTES_PER_PIXEL*y_offsets[i] + x_offsets[i]*BYTES_PER_PIXEL];
    }
    #ifdef ESP_PLATFORM
    #ifndef TDECK
    if(mouse_pointer_status) {
        // The pointer moves without any drawing call having damaged the screen,
        // so it has to report its own damage: ports that recompose only the
        // dirty rows (Tab5) otherwise repaint the pointer just when something
        // else happens to dirty those rows, which reads as the pointer tearing
        // and stuttering as it moves. Both the rows it is leaving and the rows
        // it is arriving at need it, or the old pointer stays on screen.
        if(sprite_x_px[0] != (uint16_t)mouse_x_pos || sprite_y_px[0] != (uint16_t)mouse_y_pos) {
            int y_from = sprite_y_px[0];
            int y_to = mouse_y_pos;
            int y0 = (y_from < y_to) ? y_from : y_to;
            int y1 = ((y_from > y_to) ? y_from : y_to) + sprite_h_px[0];
            display_mark_dirty_rows(y0, y1);
        }
        sprite_x_px[0] = mouse_x_pos;
        sprite_y_px[0] = mouse_y_pos;
    }
    #endif
    #endif
    tulip_frame_isr();
    vsync_count++; 
    return true;
}

void display_swap() {
    for(uint16_t i=0;i<V_RES;i++) x_offsets[i] = (x_offsets[i] + H_RES) % (H_RES+OFFSCREEN_X_PX);
    display_mark_dirty();
}


// Thanks dan for this code... packs a 32x32 hit matrix into 62 bytes
uint8_t collide_mask_get(uint8_t a, uint8_t b) {
    uint16_t field = 0;
    if(a==b) return 1;
    if(a>b) {
         field = a * (a - 1) / 2 + b;
    } else {
         field = b * (b - 1) / 2 + a;
    }
    if(field/8 > 61) {
        fprintf(stderr, "get bad field %d a %d b %d \n", field, a, b);
        return 0;
    }
    return collision_bitfield[field / 8] & 1 << (field % 8) ;
}

// Timers / counters for perf

// Two buffers are filled by this function, one gets filled while the other is drawn (via GDMA to the LCD.) 
// Each call fills a certain number of lines, set by BOUNCE_BUFFER_SIZE_PX in setup (it's currently 12 lines / 1 row of text)
int64_t bounce_time = 0;
uint32_t bounce_count = 1;

bool IRAM_ATTR display_bounce_empty(void *bounce_buf, int pos_px, int len_bytes, void *user_ctx) {
    if (bounce_buf == NULL || len_bytes <= 0) {
        return false;
    }
    int64_t tic=get_time_us(); // start the timer
    int16_t touch_x = last_touch_x[0];
    int16_t touch_y = last_touch_y[0];
    uint8_t touch_held_local = touch_held;

    uint16_t starting_display_row_px = pos_px / H_RES;
    uint8_t bounce_total_rows_px = len_bytes / H_RES;
    uint8_t * b = (uint8_t*)bounce_buf;
    if (bg_lines == NULL) {
        memset(b, 0, len_bytes);
        return false;
    }
    // Snapshot the ring once, so a scroll landing mid-call cannot make this
    // chunk render half of it with the old mapping and half with the new.
    const uint16_t tfb_top = tfb_ring_top;
    const uint16_t tfb_h = tfb_ring_h;
    // Copy the bg then the TFB over 
    for(uint8_t rows_relative_px=0;rows_relative_px<bounce_total_rows_px;rows_relative_px++) {
        uint8_t * b_ptr = b+(H_RES*rows_relative_px);
        uint16_t y = (starting_display_row_px + rows_relative_px) % V_RES;
        if (bg_lines[y] != NULL) {
            memcpy(b_ptr, bg_lines[y], H_RES);
        } else {
            memset(b_ptr, 0, H_RES);
        }
#ifdef TAB5
        // LVGL's plane goes over the BG and under the TFB and the sprites, which
        // is the order it had when it drew into bg itself. Unlike the BG it is
        // read straight, with no x_offsets[] -- a widget stays where it was put
        // however the rows underneath it scroll.
        if(lv_overlay != NULL && lv_overlay_x1[y] > lv_overlay_x0[y]) {
            const uint8_t *lv_line = lv_overlay + (uint32_t)y * LV_OVERLAY_STRIDE;
            const uint16_t lv_x0 = lv_overlay_x0[y];
            const uint16_t lv_len = lv_overlay_x1[y] - lv_x0;
            if(memchr(lv_line + lv_x0, ALPHA, lv_len) == NULL) {
                memcpy(b_ptr + lv_x0, lv_line + lv_x0, lv_len);
            } else {
                for(uint16_t x = lv_x0; x < lv_x0 + lv_len; x++) {
                    if(lv_line[x] != ALPHA) b_ptr[x] = lv_line[x];
                }
            }
        }
#endif
        if(tfb_active && bg_tfb != NULL && TFB_pxlen != NULL) {
            uint16_t tfb_y = tfb_ring_row_in(y, tfb_top, tfb_h);
            uint8_t *tfb_line = bg_tfb + (tfb_y * H_RES);
            uint16_t tfb_pxlen = TFB_pxlen[tfb_y];
            if(memchr(tfb_line, ALPHA, tfb_pxlen) == NULL) {
                memcpy(b_ptr, tfb_line, tfb_pxlen);
            } else {
                for(uint16_t x=0;x<tfb_pxlen;x++) {
                    if(tfb_line[x] != ALPHA) b_ptr[x] = tfb_line[x];
                }
            }
        }
    
        if(spriteno_activated && sprite_ids != NULL && sprite_ram != NULL && sprite_x_px != NULL && sprite_y_px != NULL && sprite_w_px != NULL && sprite_h_px != NULL && sprite_vis != NULL && sprite_mem != NULL && collision_bitfield != NULL) {
            memset(sprite_ids, 255, H_RES);
            if(touch_held_local && touch_y == y) {
                if(touch_x >= 0 && touch_x < H_RES) {
                    sprite_ids[touch_x] = SPRITES-1;
                }
            }
            for(uint8_t s=0;s<spriteno_activated;s++) {
                if(sprite_vis[s]==SPRITE_IS_SPRITE) {
                    if(y >= sprite_y_px[s] && y < sprite_y_px[s]+sprite_h_px[s]) {
                        // this sprite is on this line 
                        // compute x and y (relative to the sprite!)
                        uint8_t * sprite_data = &sprite_ram[sprite_mem[s]];
                        uint16_t relative_sprite_y_px = y - sprite_y_px[s];
                        for(uint16_t col_px=sprite_x_px[s]; col_px < sprite_x_px[s] + sprite_w_px[s]; col_px++) {
                            if(col_px < H_RES) {
                                uint16_t relative_sprite_x_px = col_px - sprite_x_px[s];
                                uint8_t b0 = sprite_data[relative_sprite_y_px * sprite_w_px[s] + relative_sprite_x_px  ] ;
                                if(b0 != ALPHA) {
                                    b[rows_relative_px*H_RES + col_px] = b0;
                                    // Only update collisions on non-alpha pixels
                                    uint8_t overlap_sprite = sprite_ids[col_px];
                                    if(overlap_sprite != 255 && overlap_sprite != s) {
                                        uint8_t collision_a = s < overlap_sprite ? s : overlap_sprite;
                                        uint8_t collision_b = s < overlap_sprite ? overlap_sprite : s;
                                        uint16_t field = collision_b * (collision_b - 1) / 2 + collision_a;
                                        collision_bitfield[field / 8] |= 1 << (field % 8);
                                    }
                                    sprite_ids[col_px] = s;
                                }
                            }
                        } // end for each column
                    } // end if this row has a sprite on it 
                }
            } // for each sprite
        } // end if any sprites on
    } // for each row
    bounce_time += (get_time_us() - tic); // stop timer
    bounce_count++;

    return true;
}

// One pixel row of text, built here before being published into bg_tfb.
static uint8_t tfb_scratch_row[H_RES];

// set tfb_row_hint to -1 for everything
void display_tfb_update(int8_t tfb_row_hint) {
    if(!tfb_active) { return; }

    uint8_t font_width = tfb_font_width_current();
    uint8_t font_height = tfb_font_height_current();
    uint8_t visible_cols = display_tfb_visible_cols();
    uint8_t visible_rows = display_tfb_visible_rows();

    // This function owns the ring's geometry. A full rebuild re-lays every row
    // from scratch, so it is also the moment the ring can be re-based; and if the
    // font changed under us the old mapping describes nothing, so force one.
    const uint16_t ring_h = (uint16_t)visible_rows * font_height;
    if(tfb_row_hint < 0 || tfb_ring_h != ring_h) {
        tfb_ring_h = ring_h;
        tfb_ring_top = 0;
        tfb_row_hint = -1;
    }

    uint16_t bounce_row_start = 0;
    // A full rebuild runs to the bottom of the screen, not just to the bottom of
    // the text. When the font height does not divide V_RES the rows below the
    // last text row still hold the previous font's pixels, and stopping at
    // visible_rows*font_height left them on screen -- switching to the 6x8 font
    // left a band of the old 12x16 console under the new one.
    uint16_t bounce_row_end = V_RES;
    if(tfb_row_hint >= 0) {
        bounce_row_start = tfb_row_hint * font_height;
        bounce_row_end = bounce_row_start + font_height;
        if(bounce_row_start >= V_RES) {
            return;
        }
        if(bounce_row_end > V_RES) {
            bounce_row_end = V_RES;
        }
    }
    for(uint16_t bounce_row_px=bounce_row_start;bounce_row_px<bounce_row_end;bounce_row_px++) {
        // Build into scratch rather than blanking bg_tfb in place. The frame
        // compositor runs on another core and reads these rows continuously; if
        // it catches a row between the memset and the glyph loop it renders the
        // line as fully transparent, which shows up as text flicker.
        memset(tfb_scratch_row, ALPHA, H_RES);

        // Where this screen row is actually stored right now.
        const uint16_t store_row_px = tfb_ring_row(bounce_row_px);

        uint8_t tfb_row = bounce_row_px / font_height;
        if(tfb_row >= visible_rows) {
            TFB_pxlen[store_row_px] = 0;
            memset(bg_tfb + (store_row_px*H_RES), ALPHA, H_RES);
            continue;
        }
        uint8_t tfb_row_offset_px = bounce_row_px % font_height;
        uint8_t tfb_col = 0;
        while(tfb_col < visible_cols && TFB[tfb_row*TFB_COLS+tfb_col]!=0) {
            uint8_t format = TFBf[tfb_row*TFB_COLS+tfb_col];
            uint8_t fg_color = TFBfg[tfb_row*TFB_COLS+tfb_col];
            uint8_t bg_color = TFBbg[tfb_row*TFB_COLS+tfb_col];
            uint16_t glyph = TFB[tfb_row*TFB_COLS+tfb_col];
            // 32 bits, left aligned: the widest thing drawn from one cell used to
            // be the 12px font, and is now a doubled fullwidth Japanese glyph at
            // 32. Every font shifts its row up to bit 31 so the emit loop below
            // stays one loop over one mask.
            uint32_t data = 0;
            // How many pixels this cell paints. Only Japanese ever exceeds one
            // cell, and only for a fullwidth glyph, whose second cell is a
            // TFB_WIDE_CONT that paints nothing.
            uint8_t cell_px = font_width;

            // If you're looking at this code just know the unrolled versions were 1.5x faster than loops on esp32s3
            // I'm sure there's more to do but this is the best we could get it for now
            if(tfb_font == TFB_FONT_PORTFOLIO) {
                if(glyph >= 32 && glyph <= 255 && tfb_row_offset_px < 8) {
                    data = ((uint32_t)portfolio_glyph_bitmap[(glyph - 32) * 8 + tfb_row_offset_px]) << 24;
                }
            } else if(tfb_font == TFB_FONT_12X16) {
                if(glyph <= 255 && tfb_row_offset_px < 16) {
                    // Already packed into bits 15..4 of a uint16_t.
                    data = ((uint32_t)font_12x16_r[glyph][tfb_row_offset_px]) << 16;
                }
            } else if(tfb_font == TFB_FONT_JP16 || tfb_font == TFB_FONT_JP32) {
                if(glyph == TFB_WIDE_CONT) {
                    // The right half of a fullwidth glyph, already painted by the
                    // cell to its left. It has to paint nothing at all, not even
                    // its background: doing so would erase that right half.
                    cell_px = 0;
                } else {
                    uint16_t row = (tfb_font == TFB_FONT_JP16)
                        ? jpfont_cell_row(glyph, tfb_row_offset_px)
                        : jpfont_cell_row(glyph, tfb_row_offset_px / 2);
                    data = (tfb_font == TFB_FONT_JP16)
                        ? ((uint32_t)row) << 16
                        : jpfont_row_2x(row);
                    // 16 is the face's fullwidth advance, in the face's own 16px
                    // units -- not in cells, which are 8px in JP16 and 16 in JP32.
                    if(jpfont_cell_width(glyph) > 8) cell_px = font_width * 2;
                }
            } else {
                if(glyph <= 255 && tfb_row_offset_px < 12) {
                    data = ((uint32_t)font_8x12_r[glyph][tfb_row_offset_px]) << 24;
                }
            }

            uint16_t start_px = tfb_col * font_width;
            uint8_t * bptr = tfb_scratch_row + start_px;
            uint32_t mask = 0x80000000;
            for(uint8_t bit=0; bit<cell_px && (start_px + bit) < H_RES; bit++) {
                uint8_t on = (data & mask) != 0;
                if(format & FORMAT_INVERSE) {
                    on = !on;
                }
                if(on) {
                    *(bptr + bit) = fg_color;
                } else if(bg_color != ALPHA) {
                    *(bptr + bit) = bg_color;
                }
                mask >>= 1;
            }
            tfb_col++;
        }
        uint16_t pxlen = tfb_col * font_width;
        if(pxlen > H_RES) {
            pxlen = H_RES;
        }
        // Publish. Copy far enough to also clear whatever the previous, longer
        // line left behind, then widen the visible length last.
        uint16_t copy_len = TFB_pxlen[store_row_px] > pxlen ? TFB_pxlen[store_row_px] : pxlen;
        memcpy(bg_tfb + (store_row_px*H_RES), tfb_scratch_row, copy_len);
        TFB_pxlen[store_row_px] = pxlen;
    }

    // Report the damage only once the rows actually hold their new contents.
    display_mark_dirty_rows(bounce_row_start, bounce_row_end);
}
void display_reset_bg() {
    bg_pal_color = TULIP_TEAL;
    for(int i=0;i<(H_RES+OFFSCREEN_X_PX)*(V_RES+OFFSCREEN_Y_PX);i++) { 
        bg[i] = bg_pal_color; 
    }
    
    // init the scroll pointer to the top left of the fb 
    for(int i=0;i<V_RES;i++) {
        bg_lines[i] = (uint32_t*)&bg[(H_RES+OFFSCREEN_X_PX)*i];
        x_offsets[i] = 0;
        y_offsets[i] = i;
        x_speeds[i] = 0;
        y_speeds[i] = 0;
    }

    display_mark_dirty();
}

void display_reset_tfb() {
    // Clear out the TFB
    tfb_fg_pal_color = color_332(255,255,255);
    tfb_bg_pal_color = tfb_default_bg_pal_color;
    for(uint i=0;i<TFB_ROWS*TFB_COLS;i++) {
        TFB[i]=0;
        TFBfg[i]=tfb_fg_pal_color;
        TFBbg[i]=tfb_bg_pal_color;
        TFBf[i]=0;
    }
    for(uint16_t i=0;i<V_RES;i++) TFB_pxlen[i] = 0;
    tfb_y_row = 0;
    tfb_x_col = 0;
    ansi_active_format = -1; // no override
    ansi_active_fg_color = tfb_fg_pal_color; 
    ansi_active_bg_color = tfb_bg_pal_color;
    esc_pending_len = 0; esc_string = 0; esc_drop = 0; esc_active = 0;
    tfb_active = 1;
    display_mark_dirty();
}

void display_tfb_set_default_bg_color(uint8_t color) {
    tfb_default_bg_pal_color = color;
    tfb_bg_pal_color = color;
    ansi_active_bg_color = color;
    if(TFBbg != NULL) {
        memset(TFBbg, color, TFB_ROWS*TFB_COLS);
        display_tfb_update(-1);
    }
    display_mark_dirty();
}

void display_reset_sprites() {
    // Set the sprite positions and speeds to 0
    for(int i=0;i<SPRITES;i++) { 
        sprite_mem[i] = 0;
        sprite_x_px[i] = 0; 
        sprite_y_px[i] = 0; 
        sprite_w_px[i] = 0; 
        sprite_h_px[i] = 0; 
        sprite_vis[i] = 0;
    }
    for(uint8_t i=0;i<62;i++) collision_bitfield[i] = 0;
    for(uint32_t i=0;i<SPRITE_RAM_BYTES;i++) sprite_ram[i] = 0;
    spriteno_activated = 0;
    display_mark_dirty();
}


void display_reset_touch() {
    for(uint8_t i=0;i<3;i++) {
        last_touch_x[i] = -1;
        last_touch_y[i] = -1;
    }
}


void display_invert_bg(uint16_t x, uint16_t y, uint16_t w, uint16_t h) {
    if(check_dim_xywh(x,y,w,h)) {
        for (int j = y; j < y+h; j++) {
            for (int i = x; i < x+w; i++) {
                if(j<V_RES+OFFSCREEN_Y_PX && i < H_RES+OFFSCREEN_X_PX) {
                    (bg)[(((j*(H_RES+OFFSCREEN_X_PX) + i)*BYTES_PER_PIXEL) + 0)] = 255 - (bg)[(((j*(H_RES+OFFSCREEN_X_PX) + i)*BYTES_PER_PIXEL) + 0)];
                }
            }
        }
    } else { 
        //fprintf(stderr, "invert_bg %d %d %d %d\n", x,y,w,h); 
    }
    display_mark_dirty_rows(y, y+h);
}

void display_set_bg_bitmap_rgba(uint16_t x, uint16_t y, uint16_t w, uint16_t h, uint8_t* data) {
    if(1) { // check_dim_xywh(x,y,w,h)) {
        for (int j = y; j < y+h; j++) {
            for (int i = x; i < x+w; i++) {
                uint8_t r = *data++;
                uint8_t g = *data++;
                uint8_t b = *data++;
                uint8_t a = *data++; 
                if(j<V_RES+OFFSCREEN_Y_PX && i < H_RES+OFFSCREEN_X_PX) {
                    if(a!=0) {
                        (bg)[(((j*(H_RES+OFFSCREEN_X_PX) + i)*BYTES_PER_PIXEL))] = color_332(r,g,b);
                    }
                }
            }
        }
    } else { 
        //fprintf(stderr, "bg_bitmap_rgba %d %d %d %d\n", x,y,w,h); 
    }
    display_mark_dirty_rows(y, y+h);
}

void display_set_bg_bitmap_raw(uint16_t x, uint16_t y, uint16_t w, uint16_t h, uint8_t* data) {
    if(check_dim_xywh(x,y,w,h)) {
        uint32_t c = 0;
        for (int j = y; j < y+h; j++) {
            for (int i = x; i < x+w; i++) {
                uint8_t pixel = data[c++];
                if(j<V_RES+OFFSCREEN_Y_PX && i < H_RES+OFFSCREEN_X_PX) {
                    if(pixel != ALPHA) {
                        (bg)[(((j*(H_RES+OFFSCREEN_X_PX) + i)*BYTES_PER_PIXEL) + 0)] = pixel;
                    }
                }
            }
        }
    } else { 
        //fprintf(stderr, "bg_bitmap_raw %d %d %d %d\n", x,y,w,h); 
    }
    display_mark_dirty_rows(y, y+h);
}

void display_get_bg_bitmap_raw(uint16_t x, uint16_t y, uint16_t w, uint16_t h, uint8_t * data) {
    if(check_dim_xywh(x,y,w,h)) {
        uint32_t c = 0;
        for (int j = y; j < y+h; j++) {
            for (int i = x; i < x+w; i++) {
                data[c++] = (bg)[(((j*(H_RES+OFFSCREEN_X_PX) + i)*BYTES_PER_PIXEL) + 0)];
            }
        }
    } else { 
        //fprintf(stderr, "get_bitmap_raw %d %d %d %d\n", x,y,w,h); 
    }
}

void display_bg_bitmap_blit(uint16_t x,uint16_t y,uint16_t w,uint16_t h,uint16_t x1,uint16_t y1) {
    if(check_dim_xywh(x,y,w,h)) {
        for (uint16_t j = y1; j < y1+h; j++) {
            for (uint16_t i = x1; i < x1+w; i++) {
                uint16_t src_y = y+(j-y1);
                uint16_t src_x = x+(i-x1);
                if(j<V_RES+OFFSCREEN_Y_PX && i < H_RES+OFFSCREEN_X_PX) {
                    (bg)[(((j*(H_RES+OFFSCREEN_X_PX) + i)*BYTES_PER_PIXEL) + 0)] = (bg)[(((src_y*(H_RES+OFFSCREEN_X_PX) + src_x)*BYTES_PER_PIXEL) + 0)];
                }
            }
        }    
    } else { 
     //fprintf(stderr, "bg_bitmap_blit %d %d %d %d %d %d\n", x,y,w,h, x1, y1); 
    }
    display_mark_dirty_rows(y1, y1+h);
}

void display_bg_bitmap_blit_alpha(uint16_t x,uint16_t y,uint16_t w,uint16_t h,uint16_t x1,uint16_t y1) {
    if(check_dim_xywh(x,y,w,h) && check_dim_xywh(x1,y1, w, h)) {
        for (uint16_t j = y1; j < y1+h; j++) {
            for (uint16_t i = x1; i < x1+w; i++) {
                uint16_t src_y = y+(j-y1);
                uint16_t src_x = x+(i-x1);
                if(j<V_RES+OFFSCREEN_Y_PX && i < H_RES+OFFSCREEN_X_PX) {
                    uint8_t c = (bg)[(((src_y*(H_RES+OFFSCREEN_X_PX) + src_x)*BYTES_PER_PIXEL) + 0)];
                    if(c != ALPHA) {
                        (bg)[(((j*(H_RES+OFFSCREEN_X_PX) + i)*BYTES_PER_PIXEL) + 0)] = (bg)[(((src_y*(H_RES+OFFSCREEN_X_PX) + src_x)*BYTES_PER_PIXEL) + 0)];
                    }
                }
            }
        }    
    } else { 
        //fprintf(stderr, "bg_bitmap_blit_alpha %d %d %d %d %d %d\n", x,y,w,h, x1, y1); 
    }
    display_mark_dirty_rows(y1, y1+h);
}



//mem_len = sprite_load(bitmap, mem_pos, [x,y,w,h]) # returns mem_len (w*h*2)
// load a bitmap into fast sprite ram
void display_load_sprite_rgba(uint32_t mem_pos, uint32_t len, uint8_t* data) {
    if(mem_pos < SPRITE_RAM_BYTES && mem_pos+len < SPRITE_RAM_BYTES) {
        for (uint32_t j = mem_pos; j < mem_pos + len; j=j+BYTES_PER_PIXEL) {
            uint8_t r = *data++;
            uint8_t g = *data++;
            uint8_t b = *data++;
            uint8_t a = *data++;
            if(a==0) { // only full transparent counts
                sprite_ram[j] = ALPHA;
            } else {
                sprite_ram[j] = color_332(r,g,b);
            }
        }
    }
    display_mark_dirty();
}

void display_load_sprite_raw(uint32_t mem_pos, uint32_t len, uint8_t* data) {
    if(mem_pos < SPRITE_RAM_BYTES && mem_pos+len < SPRITE_RAM_BYTES) {
        for (uint32_t j = mem_pos; j < mem_pos + len; j=j+BYTES_PER_PIXEL) {
            sprite_ram[j] = *data++;
        }
    }    
    display_mark_dirty();
}

#ifdef ESP_PLATFORM
const uint8_t pointer_bitmap_xys[96] = {
    0,0, 
    0,1, 1,1, 0,2, 2,2, 0,3, 3,3, 0,4, 4,4, 0,5, 5,5, 0,6, 6,6, 0,7, 7,7, 0,8, 8,8, 0,9, 9,9, 0,10, 10,10, 0,11, 11,11, 
    0,12, 7,12, 8,12, 9,12, 10,12, 11,12, 
    0,13, 4,13, 7,13,
    0,14, 3,14, 5,14, 8,14, 
    0,15, 2,15, 5,15, 8,15, 
    0,16, 1,16, 6,16, 9,16, 
    6,17, 9,17, 
    7,18, 8,18
};


void enable_mouse_pointer() {
    // just overwrite sprite ram for this, near the end of the ram slice ? 
    if(mouse_pointer_status==0) {
        uint8_t w=12;
        uint8_t h=19;
        for(uint32_t i=SPRITE_RAM_BYTES-(w*h); i<SPRITE_RAM_BYTES; i++) {
            sprite_ram[i] = ALPHA;
        }
        for(uint8_t i=0;i<96;i=i+2) {
            uint8_t x0 = pointer_bitmap_xys[i];
            uint8_t y0 = pointer_bitmap_xys[i+1];
            if(i<93 && pointer_bitmap_xys[i+3]==y0) {
                if(x0!=3 && x0!=2 && x0!=1) { // skip the end of the tail
                    for(uint8_t j=x0; j<pointer_bitmap_xys[i+2]; j++) {
                        sprite_ram[(SPRITE_RAM_BYTES-(w*h)) + (y0*w + j)] = 244;
                    }
                }
            }
            sprite_ram[(SPRITE_RAM_BYTES-(w*h)) + (y0*w + x0)] = 162;
        }
        uint8_t spriteno = 0;
        spriteno_activated++;
        sprite_w_px[spriteno] = w;
        sprite_h_px[spriteno] = h;
        sprite_mem[spriteno] = SPRITE_RAM_BYTES-(w*h);
        sprite_vis[spriteno] = SPRITE_IS_SPRITE;
        mouse_pointer_status = 1;
    }
}

void disable_mouse_pointer() {
    if(mouse_pointer_status==1) {
        mouse_pointer_status = 0;
        sprite_vis[0] = 0;
        spriteno_activated--;
    }
}
#endif

// Palletized version of screenshot. about 3x as fast, RGB332 only
void display_screenshot(char * screenshot_fn, int16_t x, int16_t y, int16_t w, int16_t h) {
    if(x<0 || x>=H_RES) x = 0;
    if(y<0 || y>=V_RES) y = 0;
    if(w<0 || w>=H_RES) w = H_RES;
    if(h<0 || h>=V_RES) h = V_RES;
    // Blank the display
    display_stop();

    uint8_t * screenshot_bb = (uint8_t *) malloc_caps(FONT_HEIGHT*H_RES*BYTES_PER_PIXEL,MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    // The capture used to land straight in bg_tfb to save an allocation, but that
    // aliases the console's own pixels: display_bounce_empty() below is still
    // reading them while the loop overwrites them. It only ever worked because
    // screen row N lived at bg_tfb row N; it no longer does (see tfb_ring_row),
    // and the capture came back with the wrapped rows showing stale text. Take a
    // buffer of our own -- and if there is no room for one, flatten the ring back
    // to row order first so the old aliasing is safe again.
    uint8_t * shot = (uint8_t *) malloc_caps((uint32_t)w*(uint32_t)h, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if(shot == NULL) {
        display_tfb_update(-1);
        shot = bg_tfb;
    }
    uint8_t r,g,b,a;

    LodePNGState state;
    lodepng_state_init(&state);
    a = 255; // todo, we could use BG alpha colors? but it doesn't matter
    int err;
    for(uint16_t i=0;i<256;i++) {
        unpack_pal_idx(i, &r, &g, &b);
        // You make the same entry in both the input image and the output image 
        err = lodepng_palette_add(&state.info_png.color, r,g,b,a);
        err = lodepng_palette_add(&state.info_raw, r,g,b,a);        
    }

    (void)err;
    state.info_png.color.colortype = LCT_PALETTE; 
    state.info_png.color.bitdepth = 8;
    state.info_raw.colortype = LCT_PALETTE;
    state.info_raw.bitdepth = 8;
    state.encoder.auto_convert = 0;

    uint16_t y_counter = 0;
    for(uint16_t scan_y=y;scan_y<y+h;scan_y=scan_y+FONT_HEIGHT) {
        display_bounce_empty(screenshot_bb, scan_y*H_RES, H_RES*FONT_HEIGHT*BYTES_PER_PIXEL, NULL);
        for(uint8_t ly=0;ly<FONT_HEIGHT;ly++) {
            uint16_t x_counter = 0;
            for(uint16_t scan_x=x;scan_x<x+w;scan_x++) {
                if(y_counter<h) {
                    shot[y_counter*w + x_counter++] = screenshot_bb[ly*H_RES + scan_x];
                }
            }
            y_counter++;
        }
    }
    // now shot has rendered sprites/tfb/etc on screen

    // encode png
    uint32_t outsize = 0;
    uint8_t *out;
    err = lodepng_encode(&out, (size_t*)&outsize,shot, w, h, &state);
    write_file(screenshot_fn, out, outsize, 1);
    free_caps(out);
    free_caps(screenshot_bb);
    if(shot != bg_tfb) free_caps(shot);

    // redraw the tfb
    display_tfb_update(-1);
    // Restart the display
    display_start();
}

void display_set_bg_pixel_pal(uint16_t x, uint16_t y, uint8_t pal_idx) {
    if(check_dim_xy(x,y)) {
        bg[y*(H_RES+OFFSCREEN_X_PX)*BYTES_PER_PIXEL + x*BYTES_PER_PIXEL] = pal_idx;    
    }
    display_mark_dirty_rows(y, y+1);
}

void display_set_bg_pixel(uint16_t x, uint16_t y, uint8_t r, uint8_t g, uint8_t b) {
    if(check_dim_xy(x,y)) {
        bg[y*(H_RES+OFFSCREEN_X_PX)*BYTES_PER_PIXEL + x*BYTES_PER_PIXEL] = color_332(r,g,b);
    }
    display_mark_dirty_rows(y, y+1);
}


void display_get_bg_pixel(uint16_t x, uint16_t y, uint8_t *r, uint8_t *g, uint8_t *b) {
    if(check_dim_xy(x,y)) {
        uint8_t px0 = bg[y*(H_RES+OFFSCREEN_X_PX)*BYTES_PER_PIXEL + x*BYTES_PER_PIXEL + 0];
        unpack_rgb_332_repeat(px0, r, g, b);
    } else {
        *r = 0; *g =0; *b = 0;
    }
}

uint8_t display_get_bg_pixel_pal(uint16_t x, uint16_t y) {
    if(check_dim_xy(x,y)) {
        return bg[y*(H_RES+OFFSCREEN_X_PX)*BYTES_PER_PIXEL + x*BYTES_PER_PIXEL + 0];
    }
    return 0;
}


void display_tfb_cursor(uint16_t x, uint16_t y) {
    if(x >= TFB_COLS || y >= TFB_ROWS) return;
    // The right half of a fullwidth character paints nothing of its own, so
    // inverting it would show no cursor at all. Invert the character instead.
    if(x > 0 && TFB[y*TFB_COLS+x] == TFB_WIDE_CONT) x--;
    // Only this character cell's pixel rows change when the row is next rebuilt.
    display_mark_dirty_rows(y * tfb_font_height_current(), (y + 1) * tfb_font_height_current());
    // Put a space char in the TFB if there's nothing here; makes the system draw it
    if(TFB[y*TFB_COLS+x] == 0) TFB[y*TFB_COLS+x] = 32;
    uint8_t f = TFBf[y*TFB_COLS + x];
    f = f | FORMAT_FLASH;
    f = f | FORMAT_INVERSE;
    TFBf[y*TFB_COLS + x] = f;
    // A different colour while the IME holds the keyboard. It needs an
    // always-on indicator and there is nowhere on screen to put one: the 変換
    // strip exists only while you are composing, the bottom row is where the
    // console itself types once it has scrolled full, and a badge anywhere but
    // column 0 is not even drawn, because a row stops rendering at its first
    // empty cell. The cursor costs no space at all and is where the eye already
    // is. Inverse video paints the block from the foreground colour.
    TFBfg[y*TFB_COLS + x] = ime_active ? IME_CURSOR_COLOR : tfb_fg_pal_color;
    TFBbg[y*TFB_COLS + x] = tfb_bg_pal_color;
}

// Repaint the cursor where it already is. Its colour is decided when it is
// painted and the console only paints it when something is written, so switching
// the IME on or off left the old colour up until the next keystroke echoed.
// Main-task only: display_tfb_update() builds through a single shared scratch
// row, so this must not run against a console write on another task.
void display_tfb_refresh_cursor(void) {
    if(!tfb_active) return;
    display_tfb_cursor(tfb_x_col, tfb_y_row);
    display_tfb_update(tfb_y_row);
}

void display_tfb_uncursor(uint16_t x, uint16_t y) {
    if(x > 0 && x < TFB_COLS && y < TFB_ROWS && TFB[y*TFB_COLS+x] == TFB_WIDE_CONT) x--;
    if(x < TFB_COLS && y < TFB_ROWS) {
        uint8_t f = TFBf[y*TFB_COLS + x];
        if(f & FORMAT_FLASH) f = f - FORMAT_FLASH;
        if(f & FORMAT_INVERSE) f = f - FORMAT_INVERSE;
        TFBf[y*TFB_COLS + x] = f;
        // The cursor overwrote this cell's colour on the way in, so put the
        // console's back: without it the character the IME cursor was last on
        // stays orange after the cursor has moved off it.
        TFBfg[y*TFB_COLS + x] = tfb_fg_pal_color;
    }
    display_mark_dirty_rows(y * tfb_font_height_current(), (y + 1) * tfb_font_height_current());
}

void display_tfb_new_row() {
    display_mark_dirty();
    uint8_t visible_rows = display_tfb_visible_rows();
    uint8_t visible_cols = display_tfb_visible_cols();
    if(visible_rows == 0 || visible_cols == 0) {
        tfb_x_col = 0;
        tfb_y_row = 0;
        return;
    }
    display_tfb_uncursor(tfb_x_col, tfb_y_row);
    // Move the pointer to a new row, and scroll the view if necessary
    if(tfb_y_row >= visible_rows-1) {
        tfb_y_row = visible_rows-1;
        // We were in the last row, let's scroll the buffer up by moving the TFB up
        for(uint8_t i=0;i<visible_rows-1;i++) {
            memcpy(&TFB[i*TFB_COLS], &TFB[(i+1)*TFB_COLS], TFB_COLS*sizeof(uint16_t));
            memcpy(&TFBf[i*TFB_COLS], &TFBf[(i+1)*TFB_COLS], TFB_COLS);
            memcpy(&TFBfg[i*TFB_COLS], &TFBfg[(i+1)*TFB_COLS], TFB_COLS);
            memcpy(&TFBbg[i*TFB_COLS], &TFBbg[(i+1)*TFB_COLS], TFB_COLS);
        }
        for(uint8_t i=0;i<visible_cols;i++) {
            TFB[tfb_y_row*TFB_COLS+i] = 0;
            TFBf[tfb_y_row*TFB_COLS+i] = 0;
            TFBfg[tfb_y_row*TFB_COLS+i] = tfb_fg_pal_color;
            TFBbg[tfb_y_row*TFB_COLS+i] = tfb_bg_pal_color;
        }
        // Only the bottom row's pixels are new: every other row keeps exactly the
        // pixels it already has in bg_tfb, one text row higher up the screen. Turn
        // the ring instead of re-rendering them. Rebuilding the whole screen here
        // cost 28ms per scrolled line on a half-full 1280x720 console and 58ms on
        // a full one, which is what made ordinary console output block MicroPython
        // for seconds at a time.
        const uint8_t font_height = tfb_font_height_current();
        const uint16_t ring_h = (uint16_t)visible_rows * font_height;
        if(ring_h > 0 && tfb_ring_h == ring_h) {
            uint16_t top = tfb_ring_top + font_height;
            tfb_ring_top = (top >= ring_h) ? (uint16_t)(top - ring_h) : top;
            display_tfb_update(visible_rows-1);
            // Everything moved, so re-report the damage now that it actually has.
            // (The display_mark_dirty() at the top of this function ran before the
            // rows changed; the compositor may have consumed it already.)
            display_mark_dirty();
        } else {
            // Geometry we do not have a ring for -- rebuild the slow way.
            display_tfb_update(-1);
        }
    } else {
        // Still got space, just increase the row counter
        display_tfb_update(tfb_y_row);
        tfb_y_row++;
    }
    // No matter what, go back to 0 on cols
    tfb_x_col = 0;
}

uint8_t ansi_parse_digits( unsigned char*str, uint16_t j, uint16_t k, uint16_t * digits) {
    uint8_t d = 0;
    uint16_t last_pos = j;
    for(uint16_t i=j; i<k; i++) {
        if(str[i]==';' || i == k-1) {
            if(i==k-1) i++; // this is to make a pretend delimeter at the end
            if(i-last_pos == 3) {
                digits[d++] = ((str[i-3]-'0') * 100) + ((str[i-2]-'0') * 10) + ((str[i-1]-'0'));
            } else if(i-last_pos == 2) {
                digits[d++] = ((str[i-2]-'0') * 10) + ((str[i-1]-'0'));
            } else if(i-last_pos == 1) {
                digits[d++] = (str[i-1]-'0');
            }
            if(d==5) { fprintf(stderr,"Warning, more than 5 ANSI format commands in a row\n"); d = 4; }
            last_pos = i+1;
        }
    }
    return d;
}

// New things to suppport
// ESC[?25l -- hide cursor
// ESC[{line};{column}H -- moves to that position

uint32_t utf8_esc = 0;
uint8_t supress_lf = 0;

// Act on one complete CSI. seq[0] is the ESC, seq[1] the '[', and seq[n-1] the
// final byte that ended it.
static void ansi_csi(unsigned char *seq, uint8_t n) {
    if(TFB == NULL) return;
    uint8_t visible_cols = display_tfb_visible_cols();
    uint8_t visible_rows = display_tfb_visible_rows();
    if(visible_cols == 0 || visible_rows == 0) return;
    uint16_t digits[5] = {0};
    uint16_t j = 2, k = n - 1;
    unsigned char F = seq[k];
    if(n > 2 && seq[2] == '?') {
        // A private mode: bracketed paste, cursor visibility, the alternate
        // screen. None of them are implemented here and a shell sets several at
        // every prompt, so drop them without a word to stderr.
        return;
    }
    if(F == 'K') { // clear to end of line
        for(uint8_t col=tfb_x_col;col<visible_cols;col++) {
            TFB[tfb_y_row*TFB_COLS+col] = 0; 
            TFBf[tfb_y_row*TFB_COLS+col] = 0; 
            TFBfg[tfb_y_row*TFB_COLS+col] = tfb_fg_pal_color; 
            TFBbg[tfb_y_row*TFB_COLS+col] = tfb_bg_pal_color ;
        }    
    } else if(F=='D') { // move cursor backwards
        uint8_t d = ansi_parse_digits(seq, j, k, digits);
        if(d==1) { 
            tfb_x_col = (digits[0] > tfb_x_col) ? 0 : (tfb_x_col - digits[0]);
        }
    } else if(F=='J') { // erase in display
        uint8_t d = ansi_parse_digits(seq, j, k, digits);
        uint16_t code = (d == 0) ? 0 : digits[0];
        if(code == 2) { // erase the whole screen
            display_reset_tfb();
        } else {
            // 0: the cursor to the end of the screen. 1: the start of the
            // screen to the cursor. A shell redraws its prompt with a bare
            // ESC [ J, which is 0, so leaving this unimplemented left the tail
            // of whatever was on screen sitting underneath the new prompt.
            uint8_t from_row = (code == 0) ? tfb_y_row : 0;
            uint8_t to_row   = (code == 0) ? (visible_rows - 1) : tfb_y_row;
            for(uint8_t row=from_row; row<=to_row; row++) {
                uint8_t first = (code == 0 && row == tfb_y_row) ? tfb_x_col : 0;
                uint8_t last  = (code == 1 && row == tfb_y_row) ? tfb_x_col : (visible_cols - 1);
                for(uint8_t col=first; col<=last && col<visible_cols; col++) {
                    uint32_t off = (uint32_t)row*TFB_COLS + col;
                    TFB[off] = 0;
                    TFBf[off] = 0;
                    TFBfg[off] = tfb_fg_pal_color;
                    TFBbg[off] = tfb_bg_pal_color;
                }
                display_tfb_update(row);
            }
        }
    } else if(F=='H') { 
        uint8_t d = ansi_parse_digits(seq, j, k, digits); 
        if(d==2) {
            // move cursor to line digits[0] and column digits[1]
            // these are 1 indexed i think ?? 
            tfb_x_col = digits[1];
            tfb_y_row = digits[0];
        } else if(d==0) {
            // move cursor to 0,0
            tfb_x_col = 0;
            tfb_y_row = 0;
            // Perhaps supress the oncoming LF too? 
            supress_lf = 1;
        }
        if(tfb_x_col >= visible_cols) tfb_x_col = visible_cols - 1;
        if(tfb_y_row >= visible_rows) tfb_y_row = visible_rows - 1;
        // I guess because of the drawing optimization, we need to add 32s to the TFB if col is nonzero and there's a 0 col to its left
        if(tfb_x_col!=0) {
            if(TFB[tfb_y_row*TFB_COLS+(tfb_x_col-1)]==0) {
                for(uint16_t c=0;c<tfb_x_col;c++) TFB[tfb_y_row*TFB_COLS+c] = 32;
            }
        }
    } else if(F=='m') { // formatting
        uint8_t d = ansi_parse_digits(seq, j, k, digits);
        uint8_t ansi_color_idx = 0;
        // Check to see if the message is a 256 color setting, as it will confuse the other codes below
        uint8_t c256 = 0;
        if(digits[0] == 38 && digits[1] == 5) c256 = 1;
        if(digits[0] == 48 && digits[1] == 5) c256 = 2;
        for(uint8_t l=0;l<d;l++) {
            uint8_t code = digits[l];
            // 256 color mode was sent, so just get the last number in the digits and set color
            if(c256==1) { 
                if(ansi_active_format < 0) ansi_active_format = 0;
                if(l==2) {
                    ansi_active_fg_color = ansi_pal[code];
                }
            } else if(c256==2) {
                if(ansi_active_format < 0) ansi_active_format = 0;
                if(l==2) {
                    ansi_active_bg_color = ansi_pal[code];                                    
                }
            } else if(code==0)  { 
                // Everything off
                ansi_active_format = -1; 
                ansi_active_bg_color = tfb_bg_pal_color;  
                ansi_active_fg_color = tfb_fg_pal_color; 
            } else {
                // Get ready
                if(ansi_active_format < 0) ansi_active_format = 0;
                if(code==1)  if (ansi_color_idx < 8) ansi_color_idx += 8; // "bold" color (not font!)
                if(code==4)  ansi_active_format = ansi_active_format | FORMAT_UNDERLINE;
                if(code==5)  { if(d==1) { ansi_active_format = ansi_active_format | FORMAT_FLASH; } } // check d=1 because of 256 color guy
                if(code==6)  ansi_active_format = ansi_active_format | FORMAT_BOLD; // hidden
                if(code==7)  ansi_active_format = ansi_active_format | FORMAT_INVERSE;
                if(code==9)  ansi_active_format = ansi_active_format | FORMAT_STRIKE;
                if(code==22) if (ansi_color_idx >= 8) ansi_color_idx = ansi_color_idx - 8;
                if(code==24) if(ansi_active_format | FORMAT_UNDERLINE) ansi_active_format =- FORMAT_UNDERLINE;
                if(code==25) if(ansi_active_format | FORMAT_FLASH) ansi_active_format =- FORMAT_FLASH;
                if(code==26) if(ansi_active_format | FORMAT_BOLD) ansi_active_format =- FORMAT_BOLD;
                if(code==27) if(ansi_active_format | FORMAT_INVERSE) ansi_active_format =- FORMAT_INVERSE;
                if(code==29) if(ansi_active_format | FORMAT_STRIKE) ansi_active_format =- FORMAT_STRIKE;

                if(code>=30 && code<=37)  ansi_active_fg_color = ansi_pal[ansi_color_idx + (code-30)]; // color, not including bold color
                if(code==39) ansi_active_fg_color = tfb_fg_pal_color;

                if(code>=40 && code<=47) ansi_active_bg_color = ansi_pal[ansi_color_idx + (code-40)];
                if(code==49) ansi_active_bg_color = tfb_bg_pal_color; // reset   
            }
        }
    } else if(F=='h' || F=='l') {
        // Set or reset a mode. Nothing here keeps any of them, and saying so on
        // stderr at every prompt would bury the log.
    } else {
        fprintf(stderr,"Unsupported ANSI code %c\n", F);
    }
}

// Consume one byte of an escape sequence, starting with the ESC itself. Returns
// 1 while the sequence is still running, so the caller knows the next byte
// belongs to it too -- even when that byte arrives in the following write.
//
// Whatever this swallows never reaches the screen, which is the point: a
// sequence the console cannot act on is still a sequence, and printing its
// bytes is worse than ignoring it. ESC ] 0 ; title BEL used to land as
// "]0;user@host: ~" at the head of every prompt for exactly that reason.
static uint8_t esc_feed(unsigned char c) {
    if(esc_drop) {
        if(c >= 0x40 && c <= 0x7e) esc_drop = 0;
        return esc_drop;
    }
    if(esc_string) {
        // ESC ] and its relatives run until a BEL or an ESC \. The payload is
        // free text -- a window title, usually -- and none of it means anything
        // here, so all of it goes.
        if(esc_string == 2) { esc_string = 0; return 0; }   // the ST after that ESC
        if(c == 7) { esc_string = 0; return 0; }            // BEL
        if(c == 27) esc_string = 2;
        return 1;
    }
    if(esc_pending_len < sizeof(esc_pending)) esc_pending[esc_pending_len++] = c;
    if(esc_pending_len < 2) return 1;                       // only the ESC so far
    if(esc_pending_len == 2) {                              // the second byte says what this is
        if(c == ']' || c == 'P' || c == 'X' || c == '^' || c == '_') {
            esc_pending_len = 0; esc_string = 1; return 1;  // a string, ended by BEL or ST
        }
        if(c == '[') return 1;                              // a CSI: parameters, then a final byte
        // ESC ( B and friends designate a character set and take one more byte.
        // Everything else -- ESC =, ESC 7, ESC M -- is two bytes and done.
        if(c=='(' || c==')' || c=='*' || c=='+' || c=='-' || c=='.' || c=='/' || c=='#' || c=='%') return 1;
        esc_pending_len = 0;
        return 0;
    }
    if(esc_pending[1] != '[') { esc_pending_len = 0; return 0; }  // the last byte of a charset
    if(c >= 0x40 && c <= 0x7e) {                            // the final byte of the CSI
        ansi_csi(esc_pending, esc_pending_len);
        esc_pending_len = 0;
        return 0;
    }
    if(esc_pending_len >= sizeof(esc_pending)) {            // nothing real is this long
        esc_pending_len = 0;
        esc_drop = 1;
    }
    return 1;
}

void display_tfb_str(unsigned char*str, uint16_t len, uint8_t format, uint8_t fg_color, uint8_t bg_color) {
    if(TFB == NULL || TFBf == NULL || TFBfg == NULL || TFBbg == NULL) {
        return;
    }
    // The row this write starts on. A wrap into display_tfb_new_row() widens the
    // damage to the whole screen on its own, since scrolling moves every row.
    const uint16_t tfb_str_start_row = tfb_y_row;

    uint8_t visible_cols = display_tfb_visible_cols();
    uint8_t visible_rows = display_tfb_visible_rows();
    if(visible_cols == 0 || visible_rows == 0) {
        return;
    }

    //fprintf(stderr,"str len %d format %d is ### ", len, format);
    //for(uint16_t i=0;i<len;i++) fprintf(stderr, "[%c/%d] ", str[i], str[i]);
    //fprintf(stderr, "###\n");
    // For each character incoming from micropython
    for(uint16_t i=0;i<len;i++) {
        // Wider than a byte now: in the Japanese fonts this carries a
        // codepoint, not a CP437 character.
        uint16_t ch = str[i];
        // A sequence still running -- possibly one that started in an earlier
        // write -- takes this byte before anything else looks at it.
        if(esc_active) { esc_active = esc_feed((unsigned char)ch); continue; }
        if(ch == 8)  { // backspace , go backwards (don't delete)
            display_tfb_uncursor(tfb_x_col, tfb_y_row);
            // Exactly one cell, the same as ESC [ 1 D -- callers count columns and
            // use whichever is shorter. Stepping over the right half of a
            // fullwidth character as well was wrong for that reason: readline
            // sends one \b per column and takes the \b path only up to four of
            // them, so a Japanese line moved back twice as far as it asked to and
            // the ESC [ K behind it ate the prompt. The cursor never lands on a
            // continuation cell anyway -- display_tfb_cursor() snaps off it.
            if(tfb_x_col > 0) tfb_x_col--;
        }
        if(ch > 127) { // unicode
            // Decode to the codepoint and decide what to do with it, rather than
            // folding straight to CP437 the way this used to: folding is exactly
            // the step that throws Japanese away.
            uint16_t ucs = 0;
            uint8_t got = convert_utf8_to_ucs((uint8_t)ch, &utf8_esc, &ucs);
            while(!got && (i + 1) < len) {
                i++;
                got = convert_utf8_to_ucs(str[i], &utf8_esc, &ucs);
            }
            if(!got) {
                // The sequence runs past the end of this write. utf8_esc carries
                // the partial state into the next one.
                continue;
            }
            uint8_t cp437 = convert_uc16_to_cp437(ucs);
            if(cp437 == 0 && !tfb_font_is_unicode() && !tfb_font_user_set && jpfont_available()) {
                // Nothing in the current font can draw this. Switch to the one
                // that can, and re-lay the console at its geometry.
                tfb_font = TFB_FONT_JP16;
                visible_cols = display_tfb_visible_cols();
                visible_rows = display_tfb_visible_rows();
                if(visible_cols == 0 || visible_rows == 0) return;
                if(tfb_x_col >= visible_cols) tfb_x_col = visible_cols - 1;
                if(tfb_y_row >= visible_rows) tfb_y_row = visible_rows - 1;
                display_tfb_update(-1);
            }
            ch = tfb_font_is_unicode() ? ucs : cp437;
            if(ch == 0) continue;   // no font here can draw it
        }
        if(ch == 27) { // ANSI
            // What kind of sequence this is, and where it ends, is decided a
            // byte at a time in esc_feed(). That is what lets one straddle a
            // write boundary instead of spilling its tail onto the screen.
            esc_active = esc_feed(27);
        } else if(ch == 10) {
            // If an LF, start a new row
            if(!supress_lf) {
                display_tfb_new_row();
            } else { supress_lf = 0; }
        } else if(ch == 13) {
            // Carriage return: back to column 0 on the same row. The REPL never
            // needed this -- it sends \r\n, and display_tfb_new_row() zeroes the
            // column anyway -- so a lone \r used to fall into the "ignore other
            // control characters" branch below and do nothing. A remote shell on
            // a pty does need it: \r on its own is how a prompt or a progress
            // line redraws itself over what it already printed.
            display_tfb_uncursor(tfb_x_col, tfb_y_row);
            tfb_x_col = 0;
        } else if(ch < 32) {
            // do nothing with other non-printable chars
        } else { // printable chars
            // Fullwidth Japanese takes two cells, the second a TFB_WIDE_CONT that
            // the row builder paints nothing for. Keeping the console a grid of
            // uniform cells this way is what lets scrolling, the cursor, the ANSI
            // codes and the editor's column arithmetic all stay as they were.
            uint8_t cells = (tfb_font_is_unicode() && jpfont_cell_width(ch) > 8) ? 2 : 1;
            // Wrap before splitting a character across the right edge, not after.
            if(tfb_x_col + cells > visible_cols) {
                display_tfb_new_row();
            }
            for(uint8_t cell=0; cell<cells; cell++) {
                uint32_t off = (uint32_t)tfb_y_row*TFB_COLS + tfb_x_col + cell;
                TFB[off] = (cell == 0) ? ch : TFB_WIDE_CONT;
                if(ansi_active_format >= 0 ) {
                    TFBf[off] = ansi_active_format;
                    TFBfg[off] = ansi_active_fg_color;
                    TFBbg[off] = ansi_active_bg_color;
                } else {
                    TFBf[off] = format;
                    TFBfg[off] = fg_color;
                    TFBbg[off] = bg_color;
                }
            }
            tfb_x_col += cells;
            if(tfb_x_col >= visible_cols) {
                display_tfb_new_row();
            }
        }
    }
    // Update the cursor 
    display_tfb_cursor(tfb_x_col, tfb_y_row);  
    display_tfb_update(tfb_y_row);
    // display_tfb_update() only rebuilt the row we ended on; if the write began
    // on an earlier row (without wrapping into a scroll) that row changed too.
    if(tfb_str_start_row != tfb_y_row) {
        display_tfb_update(tfb_str_start_row);
    }
}


// tulip.tfb_str()'s two halves, shared so the Tab5's own module table and the
// stock one cannot drift apart on something as easy to get wrong as how many
// cells a character takes.

// Measure one character at the head of a UTF-8 string in the current TFB font.
// *bytes gets its UTF-8 length, always at least 1 so a caller in a loop always
// advances; *cp gets the value the TFB would store for it. Returns the cells it
// occupies: 2 for fullwidth Japanese, 1 for everything drawable, 0 for a
// character the current font has no glyph for at all.
//
// Every column count in the console and the editor goes through here, so they
// cannot disagree about how wide a character is.
uint8_t display_tfb_char_cells(const char *s, uint8_t *bytes, uint16_t *cp) {
    uint32_t esc = 0;
    uint16_t got = 0;
    uint8_t n = 0;
    for(const unsigned char *p = (const unsigned char *)s; *p; p++) {
        n++;
        if(convert_utf8_to_ucs(*p, &esc, &got)) break;
        // A lead byte promising more continuation bytes than UTF-8 allows, or a
        // sequence cut off by the end of the string. Stop rather than run on.
        if(n >= 4) { got = 0; break; }
    }
    *bytes = n;
    *cp = 0;
    if(n == 0) return 0;
    if(!tfb_font_is_unicode()) got = convert_uc16_to_cp437(got);
    if(got == 0) return 0;
    *cp = got;
    return (tfb_font_is_unicode() && jpfont_cell_width(got) > 8) ? 2 : 1;
}

// Place a UTF-8 string starting at (x,y). Returns the number of cells written,
// which is not strlen(): a fullwidth Japanese character is one codepoint, several
// UTF-8 bytes and two cells. Attributes are the caller's business -- it applies
// them across the range this returns.
uint16_t display_tfb_place_str(const char *str, uint16_t x, uint16_t y) {
    if(TFB == NULL || y >= TFB_ROWS || x >= TFB_COLS) return 0;
    uint16_t col = x;
    for(const char *p = str; *p; ) {
        uint8_t bytes = 0;
        uint16_t cp = 0;
        uint8_t cells = display_tfb_char_cells(p, &bytes, &cp);
        p += bytes ? bytes : 1;
        if(cells == 0) continue;
        if(col + cells > TFB_COLS) break;
        TFB[y*TFB_COLS+col] = cp;
        if(cells == 2) TFB[y*TFB_COLS+col+1] = TFB_WIDE_CONT;
        col += cells;
    }
    return col - x;
}

// Read the character at (x,y) back out as UTF-8. out needs room for 4 bytes.
// Returns the raw cell value, and lands on the character when x names the right
// half of a fullwidth one.
uint16_t display_tfb_read_char(uint16_t x, uint16_t y, char *out) {
    out[0] = 0;
    if(TFB == NULL || x >= TFB_COLS || y >= TFB_ROWS) return 0;
    if(x > 0 && TFB[y*TFB_COLS+x] == TFB_WIDE_CONT) x--;
    uint16_t cp = TFB[y*TFB_COLS+x];
    if(cp == TFB_WIDE_CONT) return 0;
    if(tfb_font_is_unicode()) {
        convert_ucs_to_utf8(cp, out);
    } else {
        // The other three fonts hold a CP437 byte, and there is no CP437 to
        // Unicode table in this build to widen it with, so hand back the byte this
        // call has always handed back rather than inventing a codepoint for it.
        out[0] = (char)(cp & 0xff);
        out[1] = 0;
    }
    return cp;
}

extern void unix_display_set_clock(uint8_t mhz);
void display_set_clock(uint8_t mhz) {  
    if(mhz > 1 && mhz < 50) {
#ifdef ESP_PLATFORM
        esp_display_set_clock(mhz);
#else
        unix_display_set_clock(mhz);
#endif
    }
}

void display_teardown(void) {
    free_caps(bg); bg = NULL;
    free_caps(bg_tfb); bg_tfb = NULL;
#ifdef TAB5
    free_caps(lv_overlay); lv_overlay = NULL;
#endif
    free_caps(TFB_pxlen); TFB_pxlen = NULL;
    free_caps(sprite_ids); sprite_ids = NULL;
    free_caps(lv_buf); lv_buf = NULL;
    free_caps(sprite_ram); sprite_ram = NULL; 
    free_caps(sprite_x_px); sprite_x_px = NULL;
    free_caps(sprite_y_px); sprite_y_px = NULL;
    free_caps(sprite_w_px); sprite_w_px = NULL;
    free_caps(sprite_h_px); sprite_h_px = NULL;
    free_caps(sprite_vis); sprite_vis = NULL;
    free_caps(sprite_mem); sprite_mem = NULL;
    free_caps(collision_bitfield); collision_bitfield = NULL;
    free_caps(TFB); TFB = NULL;
    free_caps(TFBf); TFBf = NULL; 
    free_caps(TFBfg); TFBfg = NULL;
    free_caps(TFBbg); TFBbg = NULL;
    free_caps(x_offsets); x_offsets = NULL;
    free_caps(y_offsets); y_offsets = NULL;
    free_caps(x_speeds); x_speeds = NULL;
    free_caps(y_speeds); y_speeds = NULL;
    free_caps(bg_lines); bg_lines = NULL;
}


void lv_flush_cb_8b(lv_display_t * display, const lv_area_t * area, unsigned char * px_map)
{
#ifdef TAB5
    const int32_t area_width = lv_area_get_width(area);
    const uint16_t *src = (const uint16_t *)px_map;

    // Into LVGL's own plane, ALPHA and all: a widget that has just been deleted
    // flushes as the screen's background, and on a transparent screen that is the
    // ALPHA which has to land here for the BG plane to show through again.
    if(lv_overlay != NULL) {
        for(int32_t y = area->y1; y <= area->y2; y++) {
            if(y < 0 || y >= V_RES + OFFSCREEN_Y_PX) continue;
            uint8_t *row = lv_overlay + (uint32_t)y * LV_OVERLAY_STRIDE;
            for(int32_t x = area->x1; x <= area->x2; x++) {
                if(x < 0 || x >= H_RES + OFFSCREEN_X_PX) continue;
                row[x] = rgb565to332(src[(y - area->y1) * area_width + (x - area->x1)]);
            }
            // Rescan the row rather than just widening the span with the flushed
            // area: the span has to be able to shrink again when a widget goes
            // away, or the composite keeps paying for one that is not there.
            uint16_t x0 = 0;
            while(x0 < H_RES && row[x0] == ALPHA) x0++;
            uint16_t x1 = H_RES;
            while(x1 > x0 && row[x1 - 1] == ALPHA) x1--;
            lv_overlay_x0[y] = x0;
            lv_overlay_x1[y] = x1;
        }
    }
#else
    // In PARTIAL mode px_map holds just this band, rows packed at the band's width
    // (LV_DRAW_BUF_STRIDE_ALIGN is 1). Copy it into bg, whose rows the display is
    // scanning out.
    uint32_t bg_stride = (H_RES+OFFSCREEN_X_PX)*BYTES_PER_PIXEL;
    uint32_t w = (area->x2 - area->x1 + 1)*BYTES_PER_PIXEL;
    for(int32_t y = area->y1; y <= area->y2; y++) {
        memcpy(bg + y*bg_stride + area->x1*BYTES_PER_PIXEL, px_map + (y - area->y1)*w, w);
    }
#endif
    // Report the damage only now that the pixels are actually in place. On TAB5
    // this callback runs on the MicroPython task while the display task is
    // compositing on the other core: marking first let the compositor take the
    // range, redraw the rows from their old contents and clear the flag before
    // the loop above had written the new ones, and nothing would mark them
    // again. With two DSI framebuffers that lost band survived in whichever
    // buffer missed it, so the screen alternated between the new content and
    // whatever had been underneath -- the REPL background flickering under an
    // app's screen. Boards that render straight into bg have already written it
    // by the time LVGL calls this, so the move is a no-op for them.
    display_mark_dirty_rows(area->y1, area->y2 + 1);
    // Inform LVGL that you are ready with the flushing and buf is not used anymore
    lv_display_flush_ready(display);
}


// Shim for lvgl to read ticks
uint32_t u32_ticks_ms() {
    return (uint32_t) get_ticks_ms();
}

void lvgl_keyboard_read(lv_indev_t * indev_drv, lv_indev_data_t * data);


void lvgl_input_kb_read_cb(lv_indev_t * indev, lv_indev_data_t*data) {
    lvgl_keyboard_read(indev, data);
}

void lvgl_input_read_cb(lv_indev_t * indev, lv_indev_data_t*data) {
    if(touch_held) {
        // Clamp to the screen instead of dropping the point -- touch calibration can
        // map edge touches slightly out of range, and dropping them makes the
        // launcher / app switcher buttons at the screen corners miss taps
        int16_t x = last_touch_x[0];
        int16_t y = last_touch_y[0];
        if(x < 0) x = 0;
        if(x >= H_RES) x = H_RES-1;
        if(y < 0) y = 0;
        if(y >= V_RES) y = V_RES-1;
        data->point.x = x;
        data->point.y = y;
        data->state = LV_INDEV_STATE_PRESSED;
    } else {
        data->state = LV_INDEV_STATE_RELEASED;
    }
}


void my_log_cb(lv_log_level_t level, const char * buf)
{
    fprintf(stderr, "%s\n", buf);
}


extern void get_lvgl_font_from_tulip(uint8_t font_no, lv_font_t *outfont);

lv_font_t lv_font_tulip_0;
lv_font_t lv_font_tulip_1;
lv_font_t lv_font_tulip_2;
lv_font_t lv_font_tulip_3;
lv_font_t lv_font_tulip_4;
lv_font_t lv_font_tulip_5;
lv_font_t lv_font_tulip_6;
lv_font_t lv_font_tulip_7;
lv_font_t lv_font_tulip_8;
lv_font_t lv_font_tulip_9;
lv_font_t lv_font_tulip_10;
lv_font_t lv_font_tulip_11;
lv_font_t lv_font_tulip_12;
lv_font_t lv_font_tulip_13;
lv_font_t lv_font_tulip_14;
lv_font_t lv_font_tulip_15;
lv_font_t lv_font_tulip_16;
lv_font_t lv_font_tulip_17;
lv_font_t lv_font_tulip_18;
lv_font_t lv_font_tulip_19;   // Japanese, where the board has it


lv_indev_t * indev;
lv_indev_t * indev_kb;
lv_display_t * lv_display;

void setup_lvgl() {
    // Setup LVGL for UI etc
    lv_init();

    //lv_log_register_print_cb(my_log_cb);
    lv_display = lv_display_create(H_RES+OFFSCREEN_X_PX, V_RES+OFFSCREEN_Y_PX);
    lv_display_set_physical_resolution(lv_display, H_RES, V_RES); // for touchpad
    lv_display_set_offset(lv_display,0,0);
    lv_display_set_antialiasing(lv_display, 0);
#ifdef TAB5
    const uint32_t lvgl_buffer_pixels = (H_RES + OFFSCREEN_X_PX) * (V_RES + OFFSCREEN_Y_PX);
    lv_buf = (uint8_t *)calloc_caps(32, lvgl_buffer_pixels, sizeof(uint16_t), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if(lv_buf == NULL) {
        fprintf(stderr, "Unable to allocate TAB5 LVGL draw buffer\n");
        return;
    }
    lv_display_set_color_format(lv_display, LV_COLOR_FORMAT_RGB565);
    lv_display_set_flush_cb(lv_display, lv_flush_cb_8b);
    lv_display_set_buffers(lv_display, lv_buf, NULL, lvgl_buffer_pixels * sizeof(uint16_t), LV_DISPLAY_RENDER_MODE_PARTIAL);
#else
    lv_display_set_color_format(lv_display, LV_COLOR_FORMAT_RGB332);
    lv_display_set_flush_cb(lv_display, lv_flush_cb_8b);
    lv_display_set_buffers(lv_display, lv_buf, NULL, LV_BUF_BYTES, LV_DISPLAY_RENDER_MODE_PARTIAL);
#endif
    
    lv_tick_set_cb(u32_ticks_ms);

    // Create a input device (uses tulip.touch())
    indev = lv_indev_create();
    lv_indev_set_type(indev, LV_INDEV_TYPE_POINTER);   
    lv_indev_set_read_cb(indev, lvgl_input_read_cb);  

    // Also create a keyboard input device 
    indev_kb = lv_indev_create();
    lv_indev_set_type(indev_kb, LV_INDEV_TYPE_KEYPAD);
    lv_indev_set_read_cb(indev_kb, lvgl_input_kb_read_cb);  

    get_lvgl_font_from_tulip(0, &lv_font_tulip_0);
    get_lvgl_font_from_tulip(1, &lv_font_tulip_1);
    get_lvgl_font_from_tulip(2, &lv_font_tulip_2);
    get_lvgl_font_from_tulip(3, &lv_font_tulip_3);
    get_lvgl_font_from_tulip(4, &lv_font_tulip_4);
    get_lvgl_font_from_tulip(5, &lv_font_tulip_5);
    get_lvgl_font_from_tulip(6, &lv_font_tulip_6);
    get_lvgl_font_from_tulip(7, &lv_font_tulip_7);
    get_lvgl_font_from_tulip(8, &lv_font_tulip_8);
    get_lvgl_font_from_tulip(9, &lv_font_tulip_9);
    get_lvgl_font_from_tulip(10, &lv_font_tulip_10);
    get_lvgl_font_from_tulip(11, &lv_font_tulip_11);
    get_lvgl_font_from_tulip(12, &lv_font_tulip_12);
    get_lvgl_font_from_tulip(13, &lv_font_tulip_13);
    get_lvgl_font_from_tulip(14, &lv_font_tulip_14);
    get_lvgl_font_from_tulip(15, &lv_font_tulip_15);
    get_lvgl_font_from_tulip(16, &lv_font_tulip_16);
    get_lvgl_font_from_tulip(17, &lv_font_tulip_17);
    get_lvgl_font_from_tulip(18, &lv_font_tulip_18);
    // Leaves lv_font_tulip_19 zeroed on a board without the Japanese font,
    // which is what lv.font_tulip_19 would then hand to LVGL -- so nothing
    // that uses it silently draws with another face instead.
    get_lvgl_font_from_tulip(TULIP_FONT_JP, &lv_font_tulip_19);
    
}




void display_init(void) {
    // 12 divides into 600, 480, 240
    // Create the background FB
    // 1536000 bytes, plus one row of slack past the plane. display_bounce_empty()
    // reads a scrolled line as a flat memcpy of H_RES from bg_lines[y], so an
    // x_offset past OFFSCREEN_X_PX runs that read off the end of its row and into
    // the next one. On the last row of the plane there is no next one, and
    // tulip.bg_scroll(V_RES-1, H_RES+OFFSCREEN_X_PX-1, V_RES+OFFSCREEN_Y_PX-1, 0, 0)
    // is enough to put a screen width of whatever followed bg in PSRAM along the
    // bottom of the display. The registers reach that far because they wrap modulo
    // the whole plane, so the read is what has to stay inside the allocation.
    // Nothing addressable moves: check_dim_xy() still stops at the plane and
    // display_reset_bg() still fills only the plane, so the slack stays the black
    // it was allocated as.
    bg = (uint8_t*)calloc_caps(32, 1, (H_RES+OFFSCREEN_X_PX)*(V_RES+OFFSCREEN_Y_PX)*BYTES_PER_PIXEL + H_RES*BYTES_PER_PIXEL, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
#ifndef TAB5
    // LVGL's band render buffer (see lv_flush_cb_8b). TAB5 allocates its own in
    // lv_start(): it renders RGB565 into a full-size buffer and composites from
    // there, so the band buffer would only be a second, unused allocation.
    lv_buf = (uint8_t*)calloc_caps(32, 1, LV_BUF_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
#endif
    // 614400 bytes
    bg_tfb = (uint8_t*)calloc_caps(32, 1, (H_RES*V_RES), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);

#ifdef TAB5
    // LVGL's plane, the same shape as bg so a flush needs no coordinate change,
    // and cleared to ALPHA so nothing covers the BG plane until LVGL draws.
    lv_overlay = (uint8_t*)malloc_caps(LV_OVERLAY_STRIDE*(V_RES+OFFSCREEN_Y_PX), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if(lv_overlay != NULL) memset(lv_overlay, ALPHA, LV_OVERLAY_STRIDE*(V_RES+OFFSCREEN_Y_PX));
    for(uint16_t i=0;i<V_RES+OFFSCREEN_Y_PX;i++) { lv_overlay_x0[i] = 0; lv_overlay_x1[i] = 0; }
#endif

    // And various ptrs
    sprite_ids = (uint8_t*)malloc_caps(H_RES *  sizeof(uint8_t), MALLOC_CAP_INTERNAL);
    sprite_ram = (uint8_t*)malloc_caps(SPRITE_RAM_BYTES*sizeof(uint8_t), MALLOC_CAP_INTERNAL);
    sprite_x_px = (uint16_t*)malloc_caps(SPRITES*sizeof(uint16_t), MALLOC_CAP_INTERNAL);
    sprite_y_px = (uint16_t*)malloc_caps(SPRITES*sizeof(uint16_t), MALLOC_CAP_INTERNAL);
    sprite_w_px = (uint16_t*)malloc_caps(SPRITES*sizeof(uint16_t), MALLOC_CAP_INTERNAL);
    sprite_h_px = (uint16_t*)malloc_caps(SPRITES*sizeof(uint16_t), MALLOC_CAP_INTERNAL);
    sprite_vis = (uint8_t*)malloc_caps(SPRITES*sizeof(uint8_t), MALLOC_CAP_INTERNAL);
    sprite_mem = (uint32_t*)malloc_caps(SPRITES*sizeof(uint32_t), MALLOC_CAP_INTERNAL);
    collision_bitfield = (uint8_t*)malloc_caps(128, MALLOC_CAP_INTERNAL);
    TFB_pxlen = (uint16_t*)malloc_caps(V_RES*sizeof(uint16_t), MALLOC_CAP_INTERNAL);


    TFB = (uint16_t*)malloc_caps(TFB_ROWS*TFB_COLS*sizeof(uint16_t), MALLOC_CAP_INTERNAL);
    TFBf = (uint8_t*)malloc_caps(TFB_ROWS*TFB_COLS*sizeof(uint8_t), MALLOC_CAP_INTERNAL);
    TFBfg = (uint8_t*)malloc_caps(TFB_ROWS*TFB_COLS*sizeof(uint8_t), MALLOC_CAP_INTERNAL);
    TFBbg = (uint8_t*)malloc_caps(TFB_ROWS*TFB_COLS*sizeof(uint8_t), MALLOC_CAP_INTERNAL);


    x_offsets = (int16_t*)malloc_caps(V_RES*sizeof(uint16_t), MALLOC_CAP_INTERNAL);
    y_offsets = (int16_t*)malloc_caps(V_RES*sizeof(uint16_t), MALLOC_CAP_INTERNAL);
    x_speeds = (int16_t*)malloc_caps(V_RES*sizeof(int16_t), MALLOC_CAP_INTERNAL);
    y_speeds = (int16_t*)malloc_caps(V_RES*sizeof(int16_t), MALLOC_CAP_INTERNAL);

    bg_lines = (uint32_t**)malloc_caps(V_RES*sizeof(uint32_t*), MALLOC_CAP_INTERNAL);


    // Init the BG, TFB and sprite and UI layers
    display_reset_bg();
    display_reset_tfb();
    display_reset_sprites();
    display_reset_touch();

    vsync_count = 1;
    reported_fps = TARGET_DESKTOP_FPS;
    reported_gpu_usage = 0;
    touch_held = 0;


}
