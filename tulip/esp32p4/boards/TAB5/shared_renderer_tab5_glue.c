#include <stdint.h>

/*
 * Glue symbols needed by tulip/shared/display.c on TAB5 during staged
 * integration. These are intentionally minimal and can be replaced by full
 * runtime plumbing once esp32p4 links the complete Tulip UI/runtime path.
 */

// Pointer sprite position. display_frame_done_generic() reads these every frame
// to place sprite 0; usb_host_tab5.c's mouse_transfer_cb() accumulates into them.
int16_t mouse_x_pos = 0;
int16_t mouse_y_pos = 0;

#include "modtulip_tab5.h"

void tulip_touch_isr(uint8_t up)
{
    tab5_schedule_touch_callback(up);
}

void esp_display_set_clock(uint8_t mhz)
{
    (void)mhz;
}

void esp32s3_display_start(void)
{
}

void esp32s3_display_stop(void)
{
}
