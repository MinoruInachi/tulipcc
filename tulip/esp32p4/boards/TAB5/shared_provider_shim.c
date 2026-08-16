#include <stdbool.h>
#include <stdint.h>

#include "bsp/display.h"

/*
 * Minimal shared-provider shim for ESP32-P4 bring-up.
 * It exports the shared symbol names so Tab5 bridge can validate runtime
 * provider switching before full Tulip renderer dependencies are integrated.
 */

static uint32_t s_shim_frame_id = 0;

void tab5_shared_provider_shim_link_anchor(void)
{
}

bool display_bounce_empty(void *bounce_buf, int pos_px, int len_bytes, void *user_ctx)
{
    (void)user_ctx;

    if (bounce_buf == NULL || len_bytes <= 0) {
        return false;
    }

    uint8_t *out = (uint8_t *)bounce_buf;
    const int row0 = pos_px / BSP_LCD_H_RES;
    const int rows = len_bytes / BSP_LCD_H_RES;

    for (int r = 0; r < rows; r++) {
        const int y = row0 + r;
        for (int x = 0; x < BSP_LCD_H_RES; x++) {
            const uint8_t bar = (uint8_t)((x * 8) / BSP_LCD_H_RES);
            const uint8_t anim = (uint8_t)((y + (int)(s_shim_frame_id & 0x1f)) & 0x1f);
            out[r * BSP_LCD_H_RES + x] = (uint8_t)((bar << 5) | anim);
        }
    }

    return true;
}

bool display_frame_done_generic(void)
{
    s_shim_frame_id++;
    return true;
}
