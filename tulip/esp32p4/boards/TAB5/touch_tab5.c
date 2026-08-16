#include <stdio.h>
#include <stdint.h>
#include <stdbool.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "driver/gpio.h"

#include "bsp/esp-bsp.h"
#include "esp_lcd_touch.h"
#include "esp_lcd_touch_gt911.h"
#include "esp_lcd_touch_st7123.h"
#include "esp_lcd_panel_io.h"

#include "touch_tab5.h"
#include "display_tab5.h"
#include "tab5_revision.h"

static const char *TAG = "TAB5-TOUCH";

#define TAB5_PANEL_W 720
#define TAB5_SHARED_UI_W 1280
#define TAB5_SHARED_UI_H 720

/* The touch controllers are specified well past 400kHz, and the BSP's default
 * of 100kHz put ~12ms of blocking I2C in front of every sample -- the ST7123
 * report read alone is three transactions and up to 70 bytes. */
#define TAB5_TOUCH_I2C_HZ 400000

/* Interrupt-driven: wake on TP_INT, and while a finger is down keep sampling so
 * drags track. The idle value is only a heartbeat in case an edge is missed. */
#define TAB5_TOUCH_IDLE_WAIT_MS 100
#define TAB5_TOUCH_DRAG_POLL_MS 8
/* Used when no usable interrupt line exists (v1 boards, see below). */
#define TAB5_TOUCH_POLL_ONLY_MS 10
/* The ST7123 asserts TP_INT on its own scan cadence whether or not a finger is
 * present, which had the task waking several hundred times a second to read an
 * empty report. Floor the interval between idle samples; 5ms still beats the
 * panel's own frame time, so it costs no perceptible latency. */
#define TAB5_TOUCH_MIN_IDLE_INTERVAL_US 5000

extern void send_touch_to_micropython(int16_t touch_x, int16_t touch_y, uint8_t up);
extern int16_t last_touch_x[3];
extern int16_t last_touch_y[3];

static esp_lcd_touch_handle_t s_touch = NULL;
static TaskHandle_t s_touch_task = NULL;
static bool s_touch_int_driven = false;
static bool s_touch_was_pressed = false;
static int16_t s_last_touch_x = 0;
static int16_t s_last_touch_y = 0;
static volatile uint32_t s_touch_task_entries = 0;
static volatile uint32_t s_touch_polls = 0;
static volatile uint32_t s_touch_downs = 0;
static volatile uint32_t s_touch_read_errors = 0;

int16_t touch_x_delta = 0;
int16_t touch_y_delta = 0;
float touch_y_scale = 1.0f;

static void tab5_touch_to_shared_xy(int16_t panel_x, int16_t panel_y, int16_t *shared_x, int16_t *shared_y)
{
    int16_t x = (int16_t)(TAB5_SHARED_UI_W - 1) - panel_y + touch_x_delta;
    int16_t y = (int16_t)((float)panel_x * touch_y_scale) + touch_y_delta;

    if (x < 0) {
        x = 0;
    } else if (x >= TAB5_SHARED_UI_W) {
        x = TAB5_SHARED_UI_W - 1;
    }

    if (y < 0) {
        y = 0;
    } else if (y >= TAB5_SHARED_UI_H) {
        y = TAB5_SHARED_UI_H - 1;
    }

    *shared_x = x;
    *shared_y = y;
}

static void tab5_emit_touch_to_tulip(int16_t x, int16_t y, uint8_t up)
{
    int16_t shared_x = 0;
    int16_t shared_y = 0;

    tab5_touch_to_shared_xy(x, y, &shared_x, &shared_y);

    s_last_touch_x = shared_x;
    s_last_touch_y = shared_y;
    last_touch_x[0] = shared_x;
    last_touch_y[0] = shared_y;
    if (!tab5_repl_menu_icon_touch_event(shared_x, shared_y, up != 0)) {
        send_touch_to_micropython(shared_x, shared_y, up);
    }
}

/* Runs in GPIO ISR context. */
static void tab5_touch_isr(esp_lcd_touch_handle_t tp)
{
    (void)tp;
    BaseType_t higher_priority_woken = pdFALSE;
    if (s_touch_task != NULL) {
        vTaskNotifyGiveFromISR(s_touch_task, &higher_priority_woken);
    }
    portYIELD_FROM_ISR(higher_priority_woken);
}

esp_err_t tab5_touch_init(void)
{
    if (s_touch != NULL) {
        return ESP_OK;
    }

    esp_err_t err = bsp_i2c_init();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "bsp_i2c_init failed: %s", esp_err_to_name(err));
        return err;
    }
    err = bsp_feature_enable(BSP_FEATURE_TOUCH, true);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Could not power the touch controller: %s", esp_err_to_name(err));
        return err;
    }

    const tab5_board_revision_t rev = tab5_detect_board_revision();
    ESP_LOGI(TAG, "Initializing touch for %s", tab5_board_revision_name(rev));

    esp_lcd_touch_config_t tp_cfg = {
        .x_max = BSP_LCD_H_RES,
        .y_max = BSP_LCD_V_RES,
        .rst_gpio_num = GPIO_NUM_NC,   /* shared with the LCD reset */
        .int_gpio_num = BSP_LCD_TOUCH_INT,
        .levels = {
            .reset = 0,
            .interrupt = 0,
        },
        .flags = {
            .swap_xy = false,
            .mirror_x = false,
            .mirror_y = false,
        },
        .interrupt_callback = tab5_touch_isr,
    };

    esp_lcd_panel_io_handle_t tp_io = NULL;

    if (rev == TAB5_REV_V1_GT911) {
        /* v1 wires TP_INT through a pull-up to 3V3 that stops the GT911 from
         * responding, so the pin has to be held low as an output. That costs us
         * the interrupt on this revision; fall back to plain polling. */
        const gpio_config_t int_gpio_config = {
            .mode = GPIO_MODE_OUTPUT,
            .intr_type = GPIO_INTR_DISABLE,
            .pull_down_en = 0,
            .pull_up_en = 1,
            .pin_bit_mask = BIT64(BSP_LCD_TOUCH_INT),
        };
        gpio_config(&int_gpio_config);
        gpio_set_level(BSP_LCD_TOUCH_INT, 0);

        tp_cfg.int_gpio_num = GPIO_NUM_NC;
        tp_cfg.interrupt_callback = NULL;

        esp_lcd_panel_io_i2c_config_t io_cfg = ESP_LCD_TOUCH_IO_I2C_GT911_CONFIG();
        io_cfg.dev_addr = ESP_LCD_TOUCH_IO_I2C_GT911_ADDRESS_BACKUP;
        io_cfg.scl_speed_hz = TAB5_TOUCH_I2C_HZ;
        err = esp_lcd_new_panel_io_i2c(bsp_i2c_get_handle(), &io_cfg, &tp_io);
        if (err == ESP_OK) {
            err = esp_lcd_touch_new_i2c_gt911(tp_io, &tp_cfg, &s_touch);
        }
        s_touch_int_driven = false;
    } else {
        esp_lcd_panel_io_i2c_config_t io_cfg = ESP_LCD_TOUCH_IO_I2C_ST7123_CONFIG();
        io_cfg.scl_speed_hz = TAB5_TOUCH_I2C_HZ;
        err = esp_lcd_new_panel_io_i2c(bsp_i2c_get_handle(), &io_cfg, &tp_io);
        if (err == ESP_OK) {
            err = esp_lcd_touch_new_i2c_st7123(tp_io, &tp_cfg, &s_touch);
        }
        s_touch_int_driven = true;
    }

    if (err != ESP_OK || s_touch == NULL) {
        ESP_LOGE(TAG, "Touch controller init failed: %s", esp_err_to_name(err));
        s_touch = NULL;
        return (err == ESP_OK) ? ESP_FAIL : err;
    }

    ESP_LOGI(TAG, "Tab5 touch ready (%s, I2C %d kHz)",
             s_touch_int_driven ? "interrupt driven" : "polled",
             TAB5_TOUCH_I2C_HZ / 1000);
    return ESP_OK;
}

void run_tab5_touch(void *param)
{
    (void)param;
    s_touch_task_entries++;
    /* Publish the handle before init so the interrupt cannot fire into a NULL. */
    s_touch_task = xTaskGetCurrentTaskHandle();

    if (tab5_touch_init() != ESP_OK) {
        ESP_LOGE(TAG, "Touch task exiting because touch initialization failed");
        s_touch_task = NULL;
        vTaskDelete(NULL);
        return;
    }

    int64_t last_sample_us = 0;
    while (1) {
        const uint32_t wait_ms = s_touch_was_pressed
                                     ? TAB5_TOUCH_DRAG_POLL_MS
                                     : (s_touch_int_driven ? TAB5_TOUCH_IDLE_WAIT_MS
                                                           : TAB5_TOUCH_POLL_ONLY_MS);
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(wait_ms));

        if (!s_touch_was_pressed) {
            const int64_t now_us = esp_timer_get_time();
            const int64_t since_us = now_us - last_sample_us;
            if (since_us < TAB5_TOUCH_MIN_IDLE_INTERVAL_US) {
                vTaskDelay(pdMS_TO_TICKS(
                    (TAB5_TOUCH_MIN_IDLE_INTERVAL_US - (uint32_t)since_us + 999) / 1000));
            }
        }
        last_sample_us = esp_timer_get_time();

        s_touch_polls++;
        if (esp_lcd_touch_read_data(s_touch) != ESP_OK) {
            /* A failed read says nothing about the finger. Treating it as a
             * release is what made touches drop out mid-drag. */
            s_touch_read_errors++;
            continue;
        }

        uint16_t touch_x[1] = {0};
        uint16_t touch_y[1] = {0};
        uint16_t touch_strength[1] = {0};
        uint8_t touch_cnt = 0;
        const bool pressed = esp_lcd_touch_get_coordinates(s_touch,
                                                           touch_x,
                                                           touch_y,
                                                           touch_strength,
                                                           &touch_cnt,
                                                           1);

        if (pressed && touch_cnt > 0) {
            s_touch_downs++;
            tab5_emit_touch_to_tulip((int16_t)touch_x[0], (int16_t)touch_y[0], 0);
            s_touch_was_pressed = true;
        } else if (s_touch_was_pressed) {
            last_touch_x[0] = s_last_touch_x;
            last_touch_y[0] = s_last_touch_y;
            if (!tab5_repl_menu_icon_touch_event(s_last_touch_x, s_last_touch_y, true)) {
                send_touch_to_micropython(s_last_touch_x, s_last_touch_y, 1);
            }
            s_touch_was_pressed = false;
        }
    }
}

uint32_t tab5_touch_task_entries(void)
{
    return s_touch_task_entries;
}

uint32_t tab5_touch_poll_count(void)
{
    return s_touch_polls;
}

uint32_t tab5_touch_down_count(void)
{
    return s_touch_downs;
}

uint32_t tab5_touch_read_errors(void)
{
    return s_touch_read_errors;
}
