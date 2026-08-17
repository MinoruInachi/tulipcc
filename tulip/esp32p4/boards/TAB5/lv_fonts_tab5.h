#pragma once

extern const lv_font_t lv_font_unscii_8;

// gen_mpy.py exports an lv.font_X for every font it sees declared, and this
// header is the only thing it is given beyond lvgl's own. display.c builds all
// nineteen Tulip faces at boot regardless (setup_lvgl -> get_lvgl_font_from_tulip),
// so declaring the rest costs nothing but a Python global each -- and without
// them the Tab5 had only the 11px and 13px faces to draw with, which is why the
// apps reached for the antialiased lv_font_montserrat_* instead. Those fringe
// badly here: LVGL renders into RGB565 and the compositor drops it to the 8-bit
// palette, so a proportional antialiased glyph edge turns into blue speckle.
// These are 1-bit bitmap faces and stay crisp. Index is the tulip.tfb_font()
// number; the typeface each one carries is in tulip/shared/u8g2_fonts.c.
extern lv_font_t lv_font_tulip_0;   // t0_22               22px
extern lv_font_t lv_font_tulip_1;   // t0_22b              22px bold
extern lv_font_t lv_font_tulip_2;   // crox4t
extern lv_font_t lv_font_tulip_3;   // lubI12
extern lv_font_t lv_font_tulip_4;   // calibration_gothic_nbp
extern lv_font_t lv_font_tulip_5;   // helvB14             14px bold
extern lv_font_t lv_font_tulip_6;   // helvR14             14px
extern lv_font_t lv_font_tulip_7;   // logisoso16          16px
extern lv_font_t lv_font_tulip_8;   // 6x13                monospace
extern lv_font_t lv_font_tulip_9;   // 8x13                monospace
extern lv_font_t lv_font_tulip_10;  // profont15           monospace
extern lv_font_t lv_font_tulip_11;  // crox1h
extern lv_font_t lv_font_tulip_12;  // fewture
extern lv_font_t lv_font_tulip_13;  // helvR12             12px
extern lv_font_t lv_font_tulip_14;  // luRS10              10px
extern lv_font_t lv_font_tulip_15;  // luRS18              18px
extern lv_font_t lv_font_tulip_16;  // osb18               18px
extern lv_font_t lv_font_tulip_17;  // logisoso24          24px
extern lv_font_t lv_font_tulip_18;  // lubB24              24px bold
