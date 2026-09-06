// Tab5 built-in BMI270 IMU. See imu_tab5.h for the shape and rationale.

#include "imu_tab5.h"

#include "esp_check.h"
#include "esp_log.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "bsp/m5stack_tab5.h"
#include "bmi270.h"

static const char *TAG = "tab5_imu";

// NULL until the sensor comes up. bmi270_create() cleans up after itself on
// failure (it removes its own I2C device), so leaving this NULL and retrying on
// the next call is safe -- a missing sensor just returns NOT_FOUND each time.
static bmi270_handle_t *s_dev = NULL;

static esp_err_t tab5_imu_ensure(void) {
    if (s_dev != NULL) {
        return ESP_OK;
    }
    ESP_RETURN_ON_ERROR(bsp_i2c_init(), TAG, "i2c init");

    const bmi270_driver_config_t drv = {
        .addr = BMI270_I2C_ADDRESS_L,
        .interface = BMI270_USE_I2C,
        .i2c_bus = bsp_i2c_get_handle(),
    };
    bmi270_handle_t *dev = NULL;
    ESP_RETURN_ON_ERROR(bmi270_create(&drv, &dev), TAG, "create");

    // Plain orientation/motion sensing: 100 Hz is plenty, the wide ranges keep
    // full-tilt and quick flicks from clipping.
    const bmi270_config_t acq = {
        .acce_odr = BMI270_ACC_ODR_100_HZ,
        .acce_range = BMI270_ACC_RANGE_4_G,
        .gyro_odr = BMI270_GYR_ODR_100_HZ,
        .gyro_range = BMI270_GYR_RANGE_2000_DPS,
    };
    esp_err_t err = bmi270_start(dev, &acq);
    if (err != ESP_OK) {
        bmi270_delete(dev);
        return err;
    }
    s_dev = dev;
    // After the config load / enable sequence the BMI270 returns all-zero for
    // the first several reads before it starts producing data. Poll (up to
    // ~250 ms, once) for a real accelerometer sample -- at rest gravity puts
    // ~1 g on some axis, so a genuine reading is never all-zero -- so the
    // caller's first read already reflects the sensor.
    for (int i = 0; i < 25; i++) {
        vTaskDelay(pdMS_TO_TICKS(10));
        float x, y, z;
        if (bmi270_get_acce_data(dev, &x, &y, &z) == ESP_OK &&
            (x != 0.0f || y != 0.0f || z != 0.0f)) {
            break;
        }
    }
    ESP_LOGI(TAG, "BMI270 up (accel 100Hz/+-4g, gyro 100Hz/+-2000dps)");
    return ESP_OK;
}

esp_err_t tab5_imu_read(float *ax, float *ay, float *az,
                        float *gx, float *gy, float *gz) {
    ESP_RETURN_ON_ERROR(tab5_imu_ensure(), TAG, "ensure");

    float x, y, z;
    ESP_RETURN_ON_ERROR(bmi270_get_acce_data(s_dev, &x, &y, &z), TAG, "acce");
    if (ax) *ax = x;
    if (ay) *ay = y;
    if (az) *az = z;

    ESP_RETURN_ON_ERROR(bmi270_get_gyro_data(s_dev, &x, &y, &z), TAG, "gyro");
    if (gx) *gx = x;
    if (gy) *gy = y;
    if (gz) *gz = z;

    return ESP_OK;
}

bool tab5_imu_ready(void) {
    return s_dev != NULL;
}
