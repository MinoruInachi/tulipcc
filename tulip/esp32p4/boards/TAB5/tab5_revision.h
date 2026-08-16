#pragma once

#include "esp_err.h"

typedef enum {
    TAB5_REV_UNKNOWN = 0,
    TAB5_REV_V1_GT911 = 1,
    TAB5_REV_V2_ST7123 = 2,
} tab5_board_revision_t;

tab5_board_revision_t tab5_detect_board_revision(void);
const char *tab5_board_revision_name(tab5_board_revision_t rev);
