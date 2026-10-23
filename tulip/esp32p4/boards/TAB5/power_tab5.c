#include "esp_check.h"
#include "esp_log.h"

#include "driver/i2c_master.h"

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

void tab5_power_enable_ext5v(void)
{
    // Same expander the BSP drives the speaker, LCD, touch and camera enables
    // through (P1, P4, P5, P6); M5Stack's pinmap gives its P2 as EXT5V_EN.
    esp_io_expander_handle_t io_expander = bsp_io_expander_init();
    esp_err_t ret = ESP_FAIL;
    if (io_expander != NULL) {
        ret = esp_io_expander_set_dir(io_expander, TAB5_EXT5V_EN_PIN, IO_EXPANDER_OUTPUT);
        ret |= esp_io_expander_set_level(io_expander, TAB5_EXT5V_EN_PIN, 1);
        ret |= esp_io_expander_set_output_mode(io_expander, TAB5_EXT5V_EN_PIN,
                                               IO_EXPANDER_OUTPUT_MODE_PUSH_PULL);
    }
    if (ret != ESP_OK) {
        ESP_LOGW(TAG, "Port A 5V enable failed: %s", esp_err_to_name(ret));
    }
}

static bool s_charge_on;

bool tab5_power_set_charge(bool on)
{
    esp_io_expander_handle_t io_expander = bsp_io_expander1_init();
    esp_err_t ret = ESP_FAIL;
    if (io_expander != NULL) {
        ret = esp_io_expander_set_dir(io_expander, TAB5_CHG_EN_PIN, IO_EXPANDER_OUTPUT);
        ret |= esp_io_expander_set_level(io_expander, TAB5_CHG_EN_PIN, on);
        ret |= esp_io_expander_set_output_mode(io_expander, TAB5_CHG_EN_PIN,
                                               IO_EXPANDER_OUTPUT_MODE_PUSH_PULL);
    }
    if (ret != ESP_OK) {
        ESP_LOGW(TAG, "Battery charge %s failed: %s", on ? "enable" : "disable",
                 esp_err_to_name(ret));
        return false;
    }
    s_charge_on = on;
    ESP_LOGI(TAG, "Battery charge %s", on ? "on" : "off");
    return true;
}

bool tab5_power_get_charge(bool *on)
{
    *on = s_charge_on;
    return true;
}

#define INA226_REG_CONFIG   (0x00)
#define INA226_REG_SHUNT_V  (0x01)
#define INA226_REG_BUS_V    (0x02)
#define INA226_REG_MFG_ID   (0xFE)
#define INA226_REG_DIE_ID   (0xFF)
#define INA226_MFG_ID       (0x5449)  // "TI"
#define INA226_DIE_ID       (0x2260)
// 16-sample averaging, 1.1 ms shunt and bus conversions, both continuous: one
// fresh reading every ~35 ms, which is what M5Unified configures too.
#define INA226_CONFIG       (0x4527)

static i2c_master_dev_handle_t s_ina226;

static esp_err_t ina226_read(i2c_master_dev_handle_t dev, uint8_t reg, uint16_t *value)
{
    uint8_t buf[2];
    ESP_RETURN_ON_ERROR(i2c_master_transmit_receive(dev, &reg, 1, buf, 2, 50), TAG, "ina226 read");
    *value = ((uint16_t)buf[0] << 8) | buf[1];
    return ESP_OK;
}

static esp_err_t ina226_ensure(void)
{
    if (s_ina226 != NULL) {
        return ESP_OK;
    }
    ESP_RETURN_ON_ERROR(bsp_i2c_init(), TAG, "i2c init");
    i2c_master_bus_handle_t bus = bsp_i2c_get_handle();
    // The INA226's address straps span 0x40-0x4F. The ES7210 sits at 0x40 and
    // the expanders at 0x43/0x44, so only an ID match counts, and only reads
    // are sent until one does.
    for (uint8_t addr = 0x40; addr <= 0x4F; addr++) {
        if (i2c_master_probe(bus, addr, 50) != ESP_OK) {
            continue;
        }
        const i2c_device_config_t cfg = {
            .dev_addr_length = I2C_ADDR_BIT_LEN_7,
            .device_address = addr,
            .scl_speed_hz = 400000,
        };
        i2c_master_dev_handle_t dev = NULL;
        if (i2c_master_bus_add_device(bus, &cfg, &dev) != ESP_OK) {
            continue;
        }
        uint16_t mfg = 0, die = 0;
        if (ina226_read(dev, INA226_REG_MFG_ID, &mfg) == ESP_OK && mfg == INA226_MFG_ID &&
            ina226_read(dev, INA226_REG_DIE_ID, &die) == ESP_OK && die == INA226_DIE_ID) {
            uint8_t w[3] = {INA226_REG_CONFIG, INA226_CONFIG >> 8, INA226_CONFIG & 0xFF};
            esp_err_t err = i2c_master_transmit(dev, w, sizeof(w), 50);
            if (err != ESP_OK) {
                i2c_master_bus_rm_device(dev);
                return err;
            }
            s_ina226 = dev;
            ESP_LOGI(TAG, "INA226 at 0x%02x", addr);
            return ESP_OK;
        }
        i2c_master_bus_rm_device(dev);
    }
    return ESP_ERR_NOT_FOUND;
}

esp_err_t tab5_power_read_battery(float *volts, float *amps)
{
    ESP_RETURN_ON_ERROR(ina226_ensure(), TAG, "ina226");
    uint16_t bus = 0, shunt = 0;
    ESP_RETURN_ON_ERROR(ina226_read(s_ina226, INA226_REG_BUS_V, &bus), TAG, "bus");
    ESP_RETURN_ON_ERROR(ina226_read(s_ina226, INA226_REG_SHUNT_V, &shunt), TAG, "shunt");
    // 1.25 mV/LSB on the bus, 2.5 uV/LSB (signed) across the shunt. The shunt
    // reads negative while the pack charges and positive while it runs the
    // board, measured with USB-C plugged and pulled; flipped so that charging
    // is positive, as M5Unified's getBatteryCurrent() reports it.
    *volts = bus * 0.00125f;
    *amps = -(int16_t)shunt * 0.0000025f / TAB5_INA226_SHUNT_OHMS;
    return ESP_OK;
}

void tab5_power_enable_wifi(void)
{
    esp_err_t ret = bsp_feature_enable(BSP_FEATURE_WIFI, true);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Wi-Fi coprocessor power enable failed: %s", esp_err_to_name(ret));
    }
}
