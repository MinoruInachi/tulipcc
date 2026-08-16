#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/*
 * Temporary TAB5 shared-provider implementation for esp32p4 bring-up.
 * This exports the same symbols expected by the display bridge so the
 * rotated shared-provider path can be exercised before full Tulip shared
 * renderer integration lands on this port.
 */

#define TAB5_PROVIDER_W 1280
#define TAB5_PROVIDER_H 720

static uint32_t s_provider_frame = 0;

bool display_bounce_empty(void *bounce_buf, int pos_px, int len_bytes, void *user_ctx)
{
    (void)user_ctx;

    if (bounce_buf == NULL || len_bytes <= 0) {
        return false;
    }

    const int row = pos_px / TAB5_PROVIDER_W;
    uint8_t *out = (uint8_t *)bounce_buf;
    for (int x = 0; x < len_bytes; x++) {
        const uint8_t r = (uint8_t)(((x + (int)(s_provider_frame & 0x3f)) * 7) / TAB5_PROVIDER_W);
        const uint8_t g = (uint8_t)(((row + (int)(s_provider_frame & 0x1f)) * 7) / TAB5_PROVIDER_H);
        const uint8_t b = (uint8_t)((x / 80) & 0x03);
        out[x] = (uint8_t)((r << 5) | (g << 2) | b);
    }

    return true;
}

bool display_frame_done_generic(void)
{
    s_provider_frame++;
    return true;
}