#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "esp_log.h"

#include "bsp/esp-bsp.h"

#include "tab5_revision.h"

#define TAB5_GT911_ADDR_BACKUP 0x14
#define TAB5_ST7123_ADDR 0x55

static const char *TAG = "TAB5-REV";
static tab5_board_revision_t s_cached_revision = TAB5_REV_UNKNOWN;

const char *tab5_board_revision_name(tab5_board_revision_t rev)
{
    switch (rev) {
        case TAB5_REV_V1_GT911:
            return "v1 (GT911 touch)";
        case TAB5_REV_V2_ST7123:
            return "v2 (ST7123 touch)";
        default:
            return "unknown";
    }
}

tab5_board_revision_t tab5_detect_board_revision(void)
{
    if (s_cached_revision != TAB5_REV_UNKNOWN) {
        return s_cached_revision;
    }

    if (bsp_i2c_init() != ESP_OK) {
        ESP_LOGW(TAG, "I2C init failed during board revision probe");
        return TAB5_REV_UNKNOWN;
    }

    if (bsp_feature_enable(BSP_FEATURE_TOUCH, true) != ESP_OK) {
        ESP_LOGW(TAG, "Could not enable touch feature during board revision probe");
        return TAB5_REV_UNKNOWN;
    }

    vTaskDelay(pdMS_TO_TICKS(50));

    if (i2c_master_probe(bsp_i2c_get_handle(), TAB5_ST7123_ADDR, 100) == ESP_OK) {
        s_cached_revision = TAB5_REV_V2_ST7123;
    } else if (i2c_master_probe(bsp_i2c_get_handle(), TAB5_GT911_ADDR_BACKUP, 100) == ESP_OK) {
        s_cached_revision = TAB5_REV_V1_GT911;
    }

    ESP_LOGI(TAG, "Board revision probe result: %s", tab5_board_revision_name(s_cached_revision));
    return s_cached_revision;
}
