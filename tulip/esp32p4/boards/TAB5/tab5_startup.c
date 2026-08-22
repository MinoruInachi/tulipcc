#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_hosted.h"
#include "py/mpconfig.h"

#include "display_tab5.h"
#include "audio_tab5.h"
#include "power_tab5.h"
#include "storage_tab5.h"
#include "touch_tab5.h"
#include "tab5_revision.h"
#include "keyboard_tab5.h"
#include "usb_host_tab5.h"
#include "tab5_startup.h"

static const char *TAG = "TAB5-STARTUP";

#define TAB5_DISPLAY_TASK_NAME "display_task"
#define TAB5_DISPLAY_TASK_STACK_WORDS (6 * 1024 / sizeof(StackType_t))
#define TAB5_DISPLAY_TASK_PRIORITY (tskIDLE_PRIORITY + 1)
#define TAB5_DISPLAY_TASK_CORE (0)
#define TAB5_TOUCH_TASK_NAME "touch_task"
#define TAB5_TOUCH_TASK_STACK_WORDS (4 * 1024 / sizeof(StackType_t))
/* Above the display task: a touch sample is a few hundred microseconds of I2C,
 * and making it queue behind a frame is the difference between a responsive
 * drag and a laggy one. */
#define TAB5_TOUCH_TASK_PRIORITY (tskIDLE_PRIORITY + 3)
#define TAB5_TOUCH_TASK_CORE (0)

void tab5_board_startup(void)
{
    boardctrl_startup();

    tab5_power_init();
    tab5_power_enable_wifi();

    esp_err_t hosted_err = esp_hosted_init();
    if (hosted_err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to initialize ESP-Hosted: %s", esp_err_to_name(hosted_err));
    }

    ESP_LOGI(TAG, "Starting Tab5 board scaffold");

    tab5_audio_init();
    tab5_storage_init();
    tab5_keyboard_start();

    // After tab5_audio_init(): a USB MIDI device can start sending as soon as
    // the connector is powered, and those bytes go straight into AMY.
    tab5_usb_host_start();

    /* Settle which board this is before either task wants the answer, so the
     * two of them cannot race to it and whatever the probe costs is spent here
     * rather than out of the three seconds _boot.py gives the display to come
     * up. Both tasks then read the cached result. */
    const tab5_board_revision_t rev = tab5_detect_board_revision();
    ESP_LOGI(TAG, "Board is %s", tab5_board_revision_name(rev));

    BaseType_t display_task_ok = xTaskCreatePinnedToCore(run_tab5_display,
                                                          TAB5_DISPLAY_TASK_NAME,
                                                          TAB5_DISPLAY_TASK_STACK_WORDS,
                                                          NULL,
                                                          TAB5_DISPLAY_TASK_PRIORITY,
                                                          NULL,
                                                          TAB5_DISPLAY_TASK_CORE);
    if (display_task_ok != pdPASS) {
        ESP_LOGE(TAG, "Failed to create display task");
    }

    BaseType_t touch_task_ok = xTaskCreatePinnedToCore(run_tab5_touch,
                                                        TAB5_TOUCH_TASK_NAME,
                                                        TAB5_TOUCH_TASK_STACK_WORDS,
                                                        NULL,
                                                        TAB5_TOUCH_TASK_PRIORITY,
                                                        NULL,
                                                        TAB5_TOUCH_TASK_CORE);
    if (touch_task_ok != pdPASS) {
        ESP_LOGE(TAG, "Failed to create touch task");
    }

    ESP_LOGI(TAG, "Tab5 board scaffold startup sequence complete");
}
