#include <stdint.h>

#include "esp_log.h"

static const char *TAG = "TAB5-TOUCH-BRIDGE";

/*
 * Board-local fallback bridge used until esp32p4 links the shared Tulip UI
 * event path. This weak definition is overridden automatically when a strong
 * implementation is linked.
 */
void __attribute__((weak)) send_touch_to_micropython(int16_t touch_x, int16_t touch_y, uint8_t up)
{
    static uint32_t dropped_events = 0;
    (void)touch_x;
    (void)touch_y;
    (void)up;

    dropped_events++;
    if (dropped_events == 1 || (dropped_events % 200) == 0) {
        ESP_LOGW(TAG,
                 "Touch bridge fallback active (events=%lu): Tulip UI bridge is not linked on esp32p4 yet",
                 (unsigned long)dropped_events);
    }
}