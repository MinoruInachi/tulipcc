#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"

static const char *TAG = "TAB5-MP";

void tab5_mp_task(void *pv_parameter)
{
    (void)pv_parameter;
    ESP_LOGW(TAG,
             "MP task wiring is active, but MicroPython runtime is not yet linked for esp32p4");

    while (1) {
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}
