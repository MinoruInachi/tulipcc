#pragma once

#include <stdint.h>

#include "esp_err.h"

typedef enum {
    TAB5_REV_UNKNOWN = 0,
    TAB5_REV_V1_GT911 = 1,
    TAB5_REV_V2_ST7123 = 2,
    TAB5_REV_V2_ST7121 = 3,
} tab5_board_revision_t;

tab5_board_revision_t tab5_detect_board_revision(void);
const char *tab5_board_revision_name(tab5_board_revision_t rev);

/* How long the probe took to get an answer, in milliseconds. Reported through
 * tulip.tab5_diag() because log output from the board tasks does not reliably
 * survive the USB-Serial-JTAG console. */
uint32_t tab5_board_revision_probe_ms(void);
