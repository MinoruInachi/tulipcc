// jpfont.h
//
// The Japanese face, packed the way display.c's TFB row builder wants it: one
// uint16_t per pixel row, MSB leftmost, exactly like font_12x16_r. Everything
// underneath is the u8g2 engine decoding u8fontdata_jp.c on demand, behind a
// cache, because the row builder asks for one pixel row of one cell at a time
// and a u8g2 glyph only decodes whole.
//
// Boards opt in through TULIP_JP_FONT (see u8fontdata.h). Where it is off these
// all still exist and report nothing, so display.c needs no #ifdef of its own.

#ifndef __JPFONTH__
#define __JPFONTH__

#include <stdint.h>

// A Japanese console cell is 16 pixel rows tall. Halfwidth glyphs live in bits
// 15..8 of each row, fullwidth ones use all 16.
#define JPFONT_CELL_ROWS 16

// Stored in the TFB cell to the right of a fullwidth glyph, so a fullwidth
// character occupies two cells and the console stays a grid. U+FFFF is a
// permanent noncharacter, so no real codepoint can collide with it.
#define TFB_WIDE_CONT 0xFFFF

// False on boards built without the Japanese font.
uint8_t jpfont_available(void);

// 16 for a fullwidth glyph, 8 for a halfwidth one, 0 if nothing can draw cp.
// Codepoints the face itself lacks -- box drawing, Latin-1 accents -- fall back
// to font_8x12_r via CP437 and come back 8.
uint8_t jpfont_cell_width(uint16_t cp);

// One pixel row of cp's cell, MSB leftmost. Rows outside 0..15 read as blank.
uint16_t jpfont_cell_row(uint16_t cp, uint8_t row);

// The CP437 byte a CP437 face -- font_12x16_r, font_8x12_r -- would draw for cp,
// or 0 where only the Japanese face can. TFB_FONT_JP12X16 keeps its Latin in the
// 12x16 face this way, so a console that has just promoted itself to Unicode goes
// on drawing English exactly as it did.
uint8_t jpfont_cell_cp437(uint16_t cp);

// Double every bit of a 16px row into a 32px one, for the 2x console font.
uint32_t jpfont_row_2x(uint16_t row);

// Thicken a pixel row that has already been placed in the 32 bit, MSB leftmost
// field the TFB row builder draws from. For TFB_FONT_JP12X16, where the face's
// 1px stroke sits next to font_12x16_r's 2px one.
uint32_t jpfont_row_embolden(uint32_t row);

#endif
