#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"

#include "mpconfigboard.h"

#include "tab5_startup.h"
#include "tasks.h"

TaskHandle_t display_handle;
TaskHandle_t usb_handle;
TaskHandle_t touchscreen_handle;
TaskHandle_t tulip_mp_handle;
TaskHandle_t idle_0_handle;
TaskHandle_t idle_1_handle;
TaskHandle_t sequencer_handle;

unsigned long last_task_counters[MAX_TASKS];

static const char *TAG = "TAB5-MAIN";

#ifndef MICROPY_PY_NETWORK_WLAN
#define MICROPY_PY_NETWORK_WLAN (-1)
#endif

#ifndef MICROPY_PY_BLUETOOTH
#define MICROPY_PY_BLUETOOTH (-1)
#endif

#ifndef MICROPY_HW_ENABLE_UART_REPL
#define MICROPY_HW_ENABLE_UART_REPL (-1)
#endif

void app_main(void)
{
    ESP_LOGI(TAG, "Starting Tab5 ESP32-P4 scaffold");
    ESP_LOGI(TAG,
             "Feature flags: MICROPY_PY_NETWORK_WLAN=%d MICROPY_PY_BLUETOOTH=%d",
             MICROPY_PY_NETWORK_WLAN,
             MICROPY_PY_BLUETOOTH);
    ESP_LOGI(TAG,
             "REPL policy: MICROPY_HW_ENABLE_UART_REPL=%d",
             MICROPY_HW_ENABLE_UART_REPL);
    ESP_LOGW(TAG,
             "Scaffold build note: MP task wiring is active; interactive MicroPython REPL needs runtime integration");
    tab5_board_startup();

    while (1) {
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}
