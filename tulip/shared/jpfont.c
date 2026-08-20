// jpfont.c
// The Japanese face, turned into the packed pixel rows display.c's TFB row
// builder consumes. See jpfont.h.

#include "jpfont.h"
#include "u8g2_fonts.h"
#include "keyscan.h"
#include <string.h>

#if defined(TULIP_JP_FONT)

// The TFB row builder walks pixel rows outermost and cells innermost, so it asks
// for the same glyph sixteen times in a row -- but a u8g2 glyph is run-length
// coded and only decodes whole. Decode once into here and answer the other
// fifteen asks from the cache. Direct mapped on the low bits of the codepoint: a
// collision just costs another decode, and one console row holds at most 160
// cells, so a table this size effectively never thrashes.
#define JP_CACHE_SLOTS 256

typedef struct {
    uint16_t cp;        // 0 means the slot has never been filled
    uint8_t width;      // 8 or 16
    uint8_t cp437;      // the CP437 byte for cp, or 0 if there is none
    uint16_t rows[JPFONT_CELL_ROWS];
} jp_slot_t;

static jp_slot_t jp_cache[JP_CACHE_SLOTS];

uint8_t jpfont_available(void) {
    return tulip_fonts[TULIP_FONT_JP] != NULL;
}

// The face is missing box drawing, block graphics and the Latin-1 accents, all
// of which the console has always had out of CP437. Draw those from font_8x12_r
// rather than dropping them -- both are 8px wide with a 1px stroke, so the only
// thing that has to be reconciled is where the twelve rows sit in a sixteen row
// cell.
//
// font_8x12_r puts its baseline at row 10 ('_' is row 10, 'g' and 'p' descend to
// 11) and the face puts its baseline at row 14, so text glyphs drop by four and
// the baselines line up. Block and line graphics are a different problem: they
// are meant to touch the top and bottom of the cell, and offsetting them leaves
// a four pixel gap that breaks every vertical rule on screen. Those get stretched
// over the full sixteen rows instead.
#define CP437_BASELINE_DROP 4
#define CP437_GRAPHICS_FIRST 0xb0
#define CP437_GRAPHICS_LAST 0xdf

static void jp_fill_from_cp437(jp_slot_t *slot, uint8_t code) {
    if(code >= CP437_GRAPHICS_FIRST && code <= CP437_GRAPHICS_LAST) {
        for(uint8_t r=0;r<JPFONT_CELL_ROWS;r++) {
            slot->rows[r] = ((uint16_t)font_8x12_r[code][(r * 12) / JPFONT_CELL_ROWS]) << 8;
        }
        return;
    }
    for(uint8_t r=0;r<JPFONT_CELL_ROWS;r++) {
        int16_t src = (int16_t)r - CP437_BASELINE_DROP;
        slot->rows[r] = (src >= 0 && src < 12)
            ? ((uint16_t)font_8x12_r[code][src]) << 8
            : 0;
    }
}

// The face carries 3449 CJK ideographs -- the 常用漢字 and 人名用漢字 and then
// some, but not all 6879 of JIS X 0208, so a rare one (罫, 蕑) is not in it.
// Dropping such a character silently is the worst option available: the rest of
// the line slides left and nothing says why. Draw 〓 instead, which is what
// Japanese typesetting has always used to mark a character it could not set, and
// which is in the face at the full 16px width so the column count is unaffected.
#define JP_GETA 0x3013

// Whether cp would be a fullwidth character, so a substitute for it should be
// too. The standard East Asian Wide/Fullwidth ranges, which is what decides
// whether a terminal gives a character one column or two.
static uint8_t jp_is_wide(uint16_t cp) {
    return (cp >= 0x1100 && cp <= 0x115f)   /* Hangul Jamo */
        || (cp >= 0x2e80 && cp <= 0xa4cf)   /* CJK radicals through Yi */
        || (cp >= 0xac00 && cp <= 0xd7a3)   /* Hangul syllables */
        || (cp >= 0xf900 && cp <= 0xfaff)   /* CJK compatibility ideographs */
        || (cp >= 0xfe30 && cp <= 0xfe6f)   /* CJK compatibility forms */
        || (cp >= 0xff00 && cp <= 0xff60)   /* fullwidth forms */
        || (cp >= 0xffe0 && cp <= 0xffe6);  /* fullwidth signs */
}

// One pixel per byte, which is what u8g2_DrawGlyph_target() writes: it strides by
// the font's max_char_width and is already positioned for the cell, so byte
// (row*16 + col) is cell pixel (col, row) counting down from the top.
static uint8_t jp_glyph_px[JPFONT_CELL_ROWS * 16];

static void jp_fill(jp_slot_t *slot, uint16_t cp) {
    slot->cp = cp;
    slot->width = 0;
    memset(slot->rows, 0, sizeof(slot->rows));

    // Worked out here rather than at every ask: convert_uc16_to_cp437() walks a
    // switch for anything past U+0800, and the TFB row builder asks about the
    // same cell once per pixel row. Below 32 there is nothing to draw either way.
    uint8_t code437 = convert_uc16_to_cp437(cp);
    slot->cp437 = (code437 >= 32) ? code437 : 0;

    u8g2_font_t ufont = {0};
    ufont.font = NULL;
    ufont.font_decode.fg_color = 1;
    ufont.font_decode.is_transparent = 1;
    ufont.font_decode.dir = 0;
    u8g2_SetFont(&ufont, tulip_fonts[TULIP_FONT_JP]);

    // What actually gets drawn, which is cp unless we have to substitute for it.
    uint16_t draw = cp;
    if(!u8g2_IsGlyph(&ufont, cp)) {
        if(slot->cp437) {
            jp_fill_from_cp437(slot, slot->cp437);
            slot->width = 8;
            return;
        }
        draw = jp_is_wide(cp) ? JP_GETA : '?';
        // Decoded straight into this slot rather than through jp_slot(): the
        // substitute can hash to the slot being filled, and going through the
        // cache would overwrite it mid-fill.
        if(!u8g2_IsGlyph(&ufont, draw)) return;   // width stays 0
    }

    memset(jp_glyph_px, 0, sizeof(jp_glyph_px));
    int16_t adv = u8g2_DrawGlyph_target(&ufont, draw, jp_glyph_px);
    // The advance is the face's own answer to halfwidth versus fullwidth: 8 for
    // Latin and 16 for kana and kanji, exactly 1:2, which is the whole reason
    // this face was picked. Snap anything unexpected to a cell boundary so a
    // stray glyph can never desynchronise the grid.
    slot->width = (adv > 8) ? 16 : 8;

    for(uint8_t r=0;r<JPFONT_CELL_ROWS;r++) {
        uint16_t bits = 0;
        const uint8_t *px = jp_glyph_px + (uint16_t)r * 16;
        for(uint8_t c=0;c<16;c++) {
            if(px[c]) bits |= (uint16_t)0x8000 >> c;
        }
        slot->rows[r] = bits;
    }
}

static jp_slot_t *jp_slot(uint16_t cp) {
    jp_slot_t *slot = &jp_cache[cp & (JP_CACHE_SLOTS - 1)];
    if(slot->cp != cp) {
        jp_fill(slot, cp);
    }
    return slot;
}

uint8_t jpfont_cell_width(uint16_t cp) {
    if(cp == 0 || cp == TFB_WIDE_CONT || !jpfont_available()) return 0;
    return jp_slot(cp)->width;
}

uint16_t jpfont_cell_row(uint16_t cp, uint8_t row) {
    if(row >= JPFONT_CELL_ROWS) return 0;
    if(cp == 0 || cp == TFB_WIDE_CONT || !jpfont_available()) return 0;
    return jp_slot(cp)->rows[row];
}

uint8_t jpfont_cell_cp437(uint16_t cp) {
    if(cp == 0 || cp == TFB_WIDE_CONT) return 0;
    // ASCII is the overwhelming majority of what a console holds and is its own
    // CP437 byte, so it never reaches the cache at all.
    if(cp >= 32 && cp < 0x7f) return (uint8_t)cp;
    if(!jpfont_available()) return 0;
    return jp_slot(cp)->cp437;
}

#else  // !TULIP_JP_FONT

uint8_t jpfont_available(void) { return 0; }
uint8_t jpfont_cell_width(uint16_t cp) { (void)cp; return 0; }
uint16_t jpfont_cell_row(uint16_t cp, uint8_t row) { (void)cp; (void)row; return 0; }
uint8_t jpfont_cell_cp437(uint16_t cp) { (void)cp; return 0; }

#endif

// Doubling every bit of a row is how the 2x console font is drawn: there is no
// 32px Japanese bitmap face worth having, and pixel doubling a face designed at
// 16 keeps the proportions and the 1:2 half-to-fullwidth ratio exactly, which a
// second face at another size would not. Spread the byte halves through a table
// rather than looping sixteen times per row -- this is called once per cell per
// pixel row.
static const uint16_t jp_double_bits[16] = {
    0x0000, 0x0003, 0x000c, 0x000f, 0x0030, 0x0033, 0x003c, 0x003f,
    0x00c0, 0x00c3, 0x00cc, 0x00cf, 0x00f0, 0x00f3, 0x00fc, 0x00ff,
};

uint32_t jpfont_row_2x(uint16_t row) {
    return ((uint32_t)jp_double_bits[(row >> 12) & 0xf] << 24)
         | ((uint32_t)jp_double_bits[(row >>  8) & 0xf] << 16)
         | ((uint32_t)jp_double_bits[(row >>  4) & 0xf] <<  8)
         | ((uint32_t)jp_double_bits[(row >>  0) & 0xf]);
}

// The face is drawn with a 1px stroke and font_12x16_r with a 2px one, so in
// TFB_FONT_JP12X16 -- the one font that puts them on the same line -- the
// Japanese reads as a shade lighter than the English around it. Grow every
// stroke one pixel to the right, the usual way to bolden a bitmap face, with one
// restraint: not into a pixel that has another stroke immediately beyond it. At
// 16px a dense kanji separates its strokes by a single pixel, and a smear that
// took those gaps would turn 電 and 器 into blocks. So a stroke with room grows
// and one boxed in stays as it is, which costs a little evenness and keeps every
// character readable.
//
// There is no matching vertical growth: the cell is 16 rows and the face uses
// all of them, so downward is off the bottom. Thicker verticals than horizontals
// is what a 明朝 face does anyway.
uint32_t jpfont_row_embolden(uint32_t row) {
    return row | ((row >> 1) & ~(row << 1));
}
