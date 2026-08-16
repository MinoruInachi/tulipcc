#include "esp_log.h"

#include "bsp/m5stack_tab5.h"

#include "pins.h"
#include "power_tab5.h"

static const char *TAG = "TAB5-POWER";

void tab5_power_init(void)
{
    ESP_LOGI(TAG, "Tab5 power rail control uses the official BSP feature hooks");
}

void tab5_power_enable_display(void)
{
    esp_err_t ret = bsp_feature_enable(BSP_FEATURE_LCD, true);
    if (ret != ESP_OK) {
        ESP_LOGW(TAG, "LCD power enable failed: %s", esp_err_to_name(ret));
    }
}

void tab5_power_enable_usb_host(void)
{
    (void)tab5_power_set_usb_host(true);
}

// Last value written to the load switch. Measured on hardware,
// esp_io_expander_get_level() reports 0 for BSP_USB_EN even with the switch
// plainly on -- the PI4IOE5V6408's input register does not track this output --
// so a read-back from the part would be worse than no read-back at all.
static bool s_usb_host_power_on;

bool tab5_power_set_usb_host(bool on)
{
    esp_err_t ret = bsp_feature_enable(BSP_FEATURE_USB, on);
    if (ret != ESP_OK) {
        ESP_LOGW(TAG, "USB host power %s failed: %s", on ? "enable" : "disable",
                 esp_err_to_name(ret));
        return false;
    }
    s_usb_host_power_on = on;
    ESP_LOGI(TAG, "USB host power %s", on ? "on" : "off");
    return true;
}

bool tab5_power_get_usb_host(bool *on)
{
    *on = s_usb_host_power_on;
    return true;
}

void tab5_power_enable_wifi(void)
{
    esp_err_t ret = bsp_feature_enable(BSP_FEATURE_WIFI, true);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Wi-Fi coprocessor power enable failed: %s", esp_err_to_name(ret));
    }
}
