#include "esp_log.h"

#include "storage_tab5.h"

static const char *TAG = "TAB5-STORAGE";

// During Tab5 bring-up, filesystem ownership is fixed to MicroPython VFS
// (flashbdev + littlefs/oofatfs path). Pulling in BSP storage mount helpers
// introduces a second FAT implementation and breaks link with duplicate ff.c.
#ifndef TAB5_ENABLE_BSP_STORAGE_MOUNTS
#define TAB5_ENABLE_BSP_STORAGE_MOUNTS (0)
#endif

#if TAB5_ENABLE_BSP_STORAGE_MOUNTS
#error "TAB5 BSP storage mounts are disabled: they conflict with MicroPython oofatfs (duplicate ff.c symbols)."
#endif

void tab5_storage_init(void)
{
    // During early bring-up we keep MicroPython's own FAT implementation
    // authoritative, so BSP storage mounts are intentionally deferred.
    ESP_LOGI(TAG, "Storage init deferred during bring-up (BSP mounts disabled)");
}
