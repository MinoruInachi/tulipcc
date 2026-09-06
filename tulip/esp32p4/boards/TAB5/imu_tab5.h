// Tab5 built-in motion sensor. The board carries a Bosch BMI270 6-axis IMU
// (accelerometer + gyroscope) on the shared I2C bus at address 0x68. This is a
// thin wrapper over the standalone `espressif/bmi270` driver (not the BSP's
// iot_sensor_hub path, which needs an event loop and a data-acquisition task):
// the sensor is brought up lazily on the first read and then polled on demand
// from the MicroPython task, so nothing runs unless a caller actually asks for
// a sample. The i2c_master bus serialises access, so sharing it with touch and
// the keyboard is safe.
#pragma once

#include <stdbool.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

// Read one sample from the BMI270. Accelerometer values are in g, gyroscope
// values in degrees/second. Any of the six pointers may be NULL. The sensor is
// initialised on the first call (accel + gyro at 100 Hz; +/-4 g, +/-2000 dps).
// Returns ESP_OK, or an esp_err_t from the driver / I2C on failure.
esp_err_t tab5_imu_read(float *ax, float *ay, float *az,
                        float *gx, float *gy, float *gz);

// True once the BMI270 has been successfully initialised (i.e. a read has
// succeeded at least once).
bool tab5_imu_ready(void);

#ifdef __cplusplus
}
#endif
