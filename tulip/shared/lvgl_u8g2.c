// lvgl_u8g2.c
// render u8g2 fonts in lvgl 
#include "lvgl.h"
#include "u8g2_fonts.h"


// will make this less nasty asap
#define MAX_FONT_W 50
#define MAX_FONT_H 80
uint8_t databuf[MAX_FONT_W*MAX_FONT_H];


/* Get info about glyph of `unicode_letter` in `font` font.
 * Store the result in `dsc_out`.
 * The next letter (`unicode_letter_next`) might be used to calculate the width required by this glyph (kerning)
 */
bool my_get_glyph_dsc_cb(const lv_font_t * font, lv_font_glyph_dsc_t * dsc_out, uint32_t unicode_letter, uint32_t unicode_letter_next)
{
    // Even though this callback is just to get font/glyph info, u8g2 can't really know everything about the glyph
    // until u8g2 draws it. so i draw it, save the bitmap to a temp part of RAM , extract the info, and the
    // drawing CB will use the same bit of ram without having to re-draw. 
    // unfortunately this function seems to be called 3 times per glyph, so maybe try to not draw so much

    // Eighteen of the nineteen ASCII faces really are ASCII only, but the
    // Japanese one is not, and rejecting everything over 127 outright is what
    // stopped an lv.label() from ever holding Japanese. Ask the font instead:
    // codepoints past the BMP are beyond what u8g2's 16-bit encoding can index,
    // and past that u8g2_IsGlyph() is the authority.
    if(unicode_letter > 0xffff) {
        return false;
    }

    uint32_t font_no = *((uint32_t*)(font->user_data));
    if(tulip_fonts[font_no] == NULL) {
        return false;
    }
    u8g2_font_t ufont;
    ufont.font = NULL; 
    ufont.font_decode.fg_color = 1; 
    ufont.font_decode.is_transparent = 1; 
    ufont.font_decode.dir = 0;
    u8g2_SetFont(&ufont, tulip_fonts[font_no]);
    // Returning false lets LVGL fall back rather than drawing a blank box.
    if(!u8g2_IsGlyph(&ufont, (uint16_t)unicode_letter)) {
        return false;
    }
    for(uint16_t i=0;i<(MAX_FONT_H*MAX_FONT_W);i++) { databuf[i] = 0; }
    int16_t adv = u8g2_DrawGlyph_target(&ufont, unicode_letter, databuf);
    //u8g2_font_decode_t font_decode = u8g2_GetGlyphInfo(&ufont, unicode_letter);
    dsc_out->adv_w = adv;        /*Horizontal space required by the glyph in [px]*/

    // ufont height/width are swapped
    dsc_out->box_w = ufont.font_info.max_char_width;      /*Width of the bitmap in [px]*/
    dsc_out->box_h = ufont.font_info.max_char_height;       /*Width of the bitmap in [px]*/

    dsc_out->ofs_x = 0;                            /*X offset of the bitmap in [pf]*/
    dsc_out->ofs_y = 0;
    dsc_out->format= LV_FONT_GLYPH_FORMAT_A1;
    dsc_out->gid.index = unicode_letter; 
    // Every other lv_font backend sets this, and lv_font_get_glyph_dsc() never
    // clears it -- the lv_font_glyph_dsc_t at each call site is uninitialized
    // stack. Leaving it alone was invisible while these faces had no fallback
    // (LVGL re-asked the same font and got the same answer), but with one set
    // below a stale `true` would push a perfectly good ASCII glyph onto the
    // fallback font.
    dsc_out->is_placeholder = false;
    return true;                /*true: glyph found; false: glyph was not found*/
}

const void * my_get_glyph_bitmap_cb(lv_font_glyph_dsc_t * g_dsc, lv_draw_buf_t * draw_buf)
{
    memcpy(draw_buf->data, databuf, g_dsc->box_w*g_dsc->box_h);
    return draw_buf;
}

// LVGL draws its own symbols -- a dropdown's LV_SYMBOL_DOWN, a checkbox tick, a
// message box's close -- from the FontAwesome private use area, and every u8g2
// face in tulip_fonts[] is a `_tr`: U+0020..U+007E and nothing else. With no
// fallback LVGL falls through to LV_USE_FONT_PLACEHOLDER and draws a hollow box,
// which is the tofu that showed up where the Drums dropdowns' "v" should be.
// The montserrat faces carry those codepoints and are all generated at --bpp 1
// in this tree, so they stay crisp through the RGB565 -> palette step the same
// way these bitmap faces do.
//
// Pick the largest enabled montserrat that still fits the face's own bbox
// height, so the symbol never overflows the line box it is drawn into (and the
// smallest available one for a face shorter than any of them). Enabling another
// LV_FONT_MONTSERRAT_* size in lv_conf.h means adding it here too.
static const lv_font_t * symbol_fallback_for_height(uint8_t height) {
    const lv_font_t * f = NULL;
#if LV_FONT_MONTSERRAT_8
    if(f == NULL || height >= 8)  f = &lv_font_montserrat_8;
#endif
#if LV_FONT_MONTSERRAT_12
    if(f == NULL || height >= 12) f = &lv_font_montserrat_12;
#endif
#if LV_FONT_MONTSERRAT_14
    if(f == NULL || height >= 14) f = &lv_font_montserrat_14;
#endif
#if LV_FONT_MONTSERRAT_16
    if(f == NULL || height >= 16) f = &lv_font_montserrat_16;
#endif
#if LV_FONT_MONTSERRAT_18
    if(f == NULL || height >= 18) f = &lv_font_montserrat_18;
#endif
#if LV_FONT_MONTSERRAT_24
    if(f == NULL || height >= 24) f = &lv_font_montserrat_24;
#endif
#if LV_FONT_MONTSERRAT_36
    if(f == NULL || height >= 36) f = &lv_font_montserrat_36;
#endif
    return f;
}

void get_lvgl_font_from_tulip(uint32_t font_no, lv_font_t * outfont) {
    // A board built without the Japanese font leaves a NULL in tulip_fonts, and
    // u8g2_SetFont() would read the header straight off it.
    if(tulip_fonts[font_no] == NULL) return;
    u8g2_font_t ufont = {0};
    ufont.font = NULL; 
    ufont.font_decode.fg_color = 1; 
    ufont.font_decode.is_transparent = 1; 
    ufont.font_decode.dir = 0;
    u8g2_SetFont(&ufont, tulip_fonts[font_no]);
    
    outfont->get_glyph_dsc = my_get_glyph_dsc_cb;        /*Set a callback to get info about glyphs*/
    outfont->get_glyph_bitmap = my_get_glyph_bitmap_cb;  /*Set a callback to get bitmap of a glyph*/
    outfont->line_height = ufont.font_info.max_char_width;                       /*The real line height where any text fits*/
    outfont->base_line = 0;//abs(ufont.font_info.y_offset); // base_line;                      /*Base line measured from the top of line_height*/
    // max_char_height, not the max_char_width line_height is (wrongly, but
    // load-bearingly -- every app's layout is measured against it) built from:
    // byte 10 of the u8g2 header is the real bbox height.
    outfont->fallback = symbol_fallback_for_height(ufont.font_info.max_char_height);
    void *ptr = malloc(sizeof(uint32_t));
    *((uint32_t*)ptr) = font_no;
    outfont->user_data = ptr;
}

