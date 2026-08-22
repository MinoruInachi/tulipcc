#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "esp_log.h"
#include "esp_timer.h"

#include "bsp/esp-bsp.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_touch_st7123.h"

#include "tab5_revision.h"

#define TAB5_GT911_ADDR_BACKUP 0x14

/* Tab5 units built from 2026-04-28 ship an ST7121 in place of the ST7123.
 * Both answer at 0x55 and take the same touch driver, but their display
 * halves need different init sequences and different DSI timings, and
 * getting that wrong leaves the panel lit and blank rather than erroring.
 * M5's own BSP tells them apart by the touch controller's firmware version:
 * 1 is an ST7121, 3 is an ST7123, and anything else is treated as an
 * ST7123. Follow it exactly -- there is no other marking to go on. */
#define TAB5_ST712X_FW_VERSION_REG 0x0000
#define TAB5_ST7121_FW_VERSION 1

/* Neither controller answers the instant it comes out of reset, and this used
 * to ask once after a flat 50ms -- which a v1 board met and a cold v2 board did
 * not. Poll to a deadline instead. Timing out is not a degraded answer but a
 * wrong one: the caller then treats the board as a v1 or an ST7123 and brings
 * up the wrong panel, which fails silently. A released ST7121 answers in about
 * 75ms from cold, so the ceiling below is slack, not a budget. */
#define TAB5_REV_PROBE_TIMEOUT_MS 3000
#define TAB5_REV_PROBE_INTERVAL_MS 25
#define TAB5_REV_PROBE_I2C_TIMEOUT_MS 50

static const char *TAG = "TAB5-REV";
static tab5_board_revision_t s_cached_revision = TAB5_REV_UNKNOWN;
static uint32_t s_probe_ms = 0;

const char *tab5_board_revision_name(tab5_board_revision_t rev)
{
    switch (rev) {
        case TAB5_REV_V1_GT911:
            return "v1 (ILI9881C display, GT911 touch)";
        case TAB5_REV_V2_ST7123:
            return "v2 (ST7123 display and touch)";
        case TAB5_REV_V2_ST7121:
            return "v2 (ST7121 display and touch)";
        default:
            return "unknown";
    }
}

uint32_t tab5_board_revision_probe_ms(void)
{
    return s_probe_ms;
}

/* Which of the two 0x55 panels this is, and whether it is answering yet. One
 * register read settles both questions, so there is nothing for a separate
 * address probe to add. */
static bool tab5_try_st712x(esp_lcd_panel_io_handle_t io, tab5_board_revision_t *rev)
{
    uint8_t fw_version = 0;
    if (esp_lcd_panel_io_rx_param(io, TAB5_ST712X_FW_VERSION_REG, &fw_version, 1) != ESP_OK) {
        return false;
    }

    *rev = (fw_version == TAB5_ST7121_FW_VERSION) ? TAB5_REV_V2_ST7121 : TAB5_REV_V2_ST7123;
    ESP_LOGI(TAG, "Touch controller firmware version %u", (unsigned)fw_version);
    return true;
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

    /* Both of these are reset lines on the IO expander, not supplies -- P4 is
     * LCD_RST and P5 is TP_RST. On an ST712x they land on one die, because
     * touch and display are the same chip, so releasing only the touch half
     * leaves the whole controller held in reset and silent on I2C. That is why
     * this used to work on a warm reboot and not on a cold one: the expander
     * keeps its outputs across a chip reset, so LCD_RST was already released
     * from the previous run. */
    if (bsp_feature_enable(BSP_FEATURE_LCD, true) != ESP_OK) {
        ESP_LOGW(TAG, "Could not release the display reset during board revision probe");
    }
    if (bsp_feature_enable(BSP_FEATURE_TOUCH, true) != ESP_OK) {
        ESP_LOGW(TAG, "Could not enable touch feature during board revision probe");
        return TAB5_REV_UNKNOWN;
    }

    const int64_t start_us = esp_timer_get_time();
    const int64_t deadline_us = start_us + (int64_t)TAB5_REV_PROBE_TIMEOUT_MS * 1000;

    esp_lcd_panel_io_i2c_config_t io_cfg = ESP_LCD_TOUCH_IO_I2C_ST7123_CONFIG();
    io_cfg.scl_speed_hz = 100000;
    esp_lcd_panel_io_handle_t st712x_io = NULL;
    if (esp_lcd_new_panel_io_i2c(bsp_i2c_get_handle(), &io_cfg, &st712x_io) != ESP_OK) {
        ESP_LOGW(TAG, "Could not open the ST712x touch address for probing");
        st712x_io = NULL;
    }

    do {
        if (st712x_io != NULL && tab5_try_st712x(st712x_io, &s_cached_revision)) {
            break;
        }
        /* The GT911 on a v1 board answers a plain address probe straight away. */
        if (i2c_master_probe(bsp_i2c_get_handle(), TAB5_GT911_ADDR_BACKUP,
                             TAB5_REV_PROBE_I2C_TIMEOUT_MS) == ESP_OK) {
            s_cached_revision = TAB5_REV_V1_GT911;
            break;
        }
        vTaskDelay(pdMS_TO_TICKS(TAB5_REV_PROBE_INTERVAL_MS));
    } while (esp_timer_get_time() < deadline_us);

    if (st712x_io != NULL) {
        esp_lcd_panel_io_del(st712x_io);
    }

    s_probe_ms = (uint32_t)((esp_timer_get_time() - start_us) / 1000);

    ESP_LOGI(TAG, "Board revision probe result: %s (answered after %ums)",
             tab5_board_revision_name(s_cached_revision), (unsigned)s_probe_ms);
    return s_cached_revision;
}
