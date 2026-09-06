// bresenham.h
#ifndef BRESENHAM_H
#define BRESENHAM_H

#include "display.h"
#include <math.h>
#include "u8g2_fonts.h"


#define swap(x,y) { x = x + y; y = x - y; x = x - y; }

// Colours here are native BG pixels (tulip_px_t): a palette index goes through
// PX() first, an (r, g, b) through px_from_rgb(). The ports do that in their
// bindings, so the primitives never see a palette index.
void plotQuadBezier(int x0, int y0, int x1, int y1, int x2, int y2, tulip_px_t color);
void plot_basic_bezier (int x0, int y0, int x1, int y1, int x2, int y2, tulip_px_t color);
void fillRect(int16_t x, int16_t y, int16_t w, int16_t h,  tulip_px_t color);
void drawCircle(short x0, short y0, short r, tulip_px_t color);
void drawCircleHelper( short x0, short y0, short r, unsigned char cornername, tulip_px_t color);
void fillCircle(short x0, short y0, short r, tulip_px_t color) ;
void fillCircleHelper(short x0, short y0, short r, unsigned char cornername, short delta, tulip_px_t color) ;
void drawLine(short x0, short y0, short x1, short y1,  tulip_px_t color);
void drawRect(short x, short y, short w, short h, tulip_px_t color) ;
void drawRoundRect(short x, short y, short w, short h, short r, tulip_px_t color);
void fillRoundRect(short x, short y, short w, short h, short r, tulip_px_t color);
void drawTriangle(short x0, short y0, short x1, short y1, short x2, short y2, tulip_px_t color);
void fillTriangle ( short x0, short y0, short x1, short y1, short x2, short y2, tulip_px_t color);
void fill(int16_t x, int16_t y, tulip_px_t color);
void drawFastHLine(int16_t x, int16_t y, int16_t w, tulip_px_t color);
void drawFastVLine(short x0, short y0, short h, tulip_px_t color);
void drawLine_scanline(short x0, short y0,short x1, short y1,tulip_px_t color, unsigned short width);
uint16_t draw_new_str(const char * str, uint16_t x, uint16_t y, tulip_px_t fg, uint8_t font_no, uint16_t w, uint16_t h, uint8_t centered);
uint16_t draw_new_char(const char c, uint16_t x, uint16_t y, tulip_px_t fg, uint8_t font_no);

#endif