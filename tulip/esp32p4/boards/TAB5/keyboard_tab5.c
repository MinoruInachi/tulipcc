#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lvgl.h"

#include "keyboard_tab5.h"

#define KEY_MOD_LCTRL 0x01

#define TAB5_KEYBOARD_I2C_PORT I2C_NUM_0
#define TAB5_KEYBOARD_I2C_ADDR 0x6d
#define TAB5_KEYBOARD_I2C_SDA GPIO_NUM_0
#define TAB5_KEYBOARD_I2C_SCL GPIO_NUM_1
#define TAB5_KEYBOARD_INT GPIO_NUM_50
#define TAB5_KEYBOARD_I2C_HZ 400000
#define TAB5_KEYBOARD_RING_SIZE 64
#define TAB5_KEYBOARD_TASK_STACK_WORDS (3 * 1024 / sizeof(StackType_t))

#define TAB5_KEYBOARD_REG_INT_CFG 0x00
#define TAB5_KEYBOARD_REG_INT_STATUS 0x01
#define TAB5_KEYBOARD_REG_EVENT_COUNT 0x02
#define TAB5_KEYBOARD_REG_MODE 0x10
#define TAB5_KEYBOARD_REG_CHAR_LENGTH 0x40
#define TAB5_KEYBOARD_REG_CHAR_EVENT 0x50
#define TAB5_KEYBOARD_REG_VERSION 0xfe

#define TAB5_KEYBOARD_INT_CHAR_EVENT 0x04
#define TAB5_KEYBOARD_MODE_CHARACTER 0x02
#define TAB5_KEYBOARD_CHAR_EVENT_MAX_BYTES 10

static const char *TAG = "TAB5-KEYBOARD";

static i2c_master_bus_handle_t s_keyboard_bus;
static i2c_master_dev_handle_t s_keyboard_device;
static bool s_keyboard_connected;
static uint8_t s_i2c_error_streak;
static uint16_t s_key_ring[TAB5_KEYBOARD_RING_SIZE];
static uint8_t s_key_ring_head;
static uint8_t s_key_ring_tail;
static portMUX_TYPE s_key_ring_lock = portMUX_INITIALIZER_UNLOCKED;
static uint32_t s_keyboard_events;
static uint32_t s_keyboard_errors;
static uint32_t s_keyboard_drops;

static bool tab5_keyboard_ring_has_data(void)
{
    bool has_data;
    portENTER_CRITICAL(&s_key_ring_lock);
    has_data = s_key_ring_head != s_key_ring_tail;
    portEXIT_CRITICAL(&s_key_ring_lock);
    return has_data;
}

static void tab5_keyboard_ring_push(uint16_t key)
{
    portENTER_CRITICAL(&s_key_ring_lock);
    const uint8_t next = (uint8_t)((s_key_ring_head + 1U) % TAB5_KEYBOARD_RING_SIZE);
    if (next == s_key_ring_tail) {
        s_keyboard_drops++;
    } else {
        s_key_ring[s_key_ring_head] = key;
        s_key_ring_head = next;
        s_keyboard_events++;
    }
    portEXIT_CRITICAL(&s_key_ring_lock);
}

static bool tab5_keyboard_ring_pop(uint16_t *key)
{
    bool available = false;
    portENTER_CRITICAL(&s_key_ring_lock);
    if (s_key_ring_head != s_key_ring_tail) {
        *key = s_key_ring[s_key_ring_tail];
        s_key_ring_tail = (uint8_t)((s_key_ring_tail + 1U) % TAB5_KEYBOARD_RING_SIZE);
        available = true;
    }
    portEXIT_CRITICAL(&s_key_ring_lock);
    return available;
}

static esp_err_t tab5_keyboard_read_register(uint8_t reg, uint8_t *data, size_t len)
{
    if (s_keyboard_device == NULL || data == NULL || len == 0) {
        return ESP_ERR_INVALID_STATE;
    }
    return i2c_master_transmit_receive(s_keyboard_device, &reg, 1, data, len, 20);
}

static esp_err_t tab5_keyboard_write_register(uint8_t reg, uint8_t value)
{
    if (s_keyboard_device == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    const uint8_t tx[] = {reg, value};
    return i2c_master_transmit(s_keyboard_device, tx, sizeof(tx), 20);
}

static bool tab5_keyboard_name_is(const uint8_t *name, size_t len, const char *expected)
{
    const size_t expected_len = strlen(expected);
    if (len != expected_len) {
        return false;
    }
    for (size_t i = 0; i < len; i++) {
        uint8_t actual = name[i];
        uint8_t wanted = (uint8_t)expected[i];
        if (actual >= 'a' && actual <= 'z') {
            actual = (uint8_t)(actual - 'a' + 'A');
        }
        if (wanted >= 'a' && wanted <= 'z') {
            wanted = (uint8_t)(wanted - 'a' + 'A');
        }
        if (actual != wanted) {
            return false;
        }
    }
    return true;
}

static uint16_t tab5_keyboard_decode_character(uint8_t modifier, const uint8_t *text, size_t len)
{
    uint16_t key = 0;
    if (len == 1) {
        key = text[0];
    } else if (tab5_keyboard_name_is(text, len, "ESC")) {
        key = 27;
    } else if (tab5_keyboard_name_is(text, len, "DEL")) {
        key = 262;
    } else if (tab5_keyboard_name_is(text, len, "TAB")) {
        key = 9;
    } else if (tab5_keyboard_name_is(text, len, "BACKSPACE")) {
        key = 8;
    } else if (tab5_keyboard_name_is(text, len, "UP")) {
        key = 259;
    } else if (tab5_keyboard_name_is(text, len, "DOWN")) {
        key = 258;
    } else if (tab5_keyboard_name_is(text, len, "LEFT")) {
        key = 260;
    } else if (tab5_keyboard_name_is(text, len, "RIGHT")) {
        key = 261;
    } else if (tab5_keyboard_name_is(text, len, "ENTER")) {
        key = 13;
    }

    if ((modifier & KEY_MOD_LCTRL) != 0 && key != 0) {
        if (key >= 'a' && key <= 'z') {
            key = (uint16_t)(key - 'a' + 1);
        } else if (key >= 'A' && key <= 'Z') {
            key = (uint16_t)(key - 'A' + 1);
        } else if (key == 9) {
            key = 263;
        }
    }
    return key;
}

static bool tab5_keyboard_configure_device(void)
{
    if (i2c_master_probe(s_keyboard_bus, TAB5_KEYBOARD_I2C_ADDR, 50) != ESP_OK) {
        return false;
    }

    if (s_keyboard_device == NULL) {
        const i2c_device_config_t device_config = {
            .dev_addr_length = I2C_ADDR_BIT_LEN_7,
            .device_address = TAB5_KEYBOARD_I2C_ADDR,
            .scl_speed_hz = TAB5_KEYBOARD_I2C_HZ,
        };
        if (i2c_master_bus_add_device(s_keyboard_bus, &device_config, &s_keyboard_device) != ESP_OK) {
            return false;
        }
    }

    uint8_t version = 0;
    if (tab5_keyboard_read_register(TAB5_KEYBOARD_REG_VERSION, &version, 1) != ESP_OK ||
        tab5_keyboard_write_register(TAB5_KEYBOARD_REG_MODE, TAB5_KEYBOARD_MODE_CHARACTER) != ESP_OK ||
        tab5_keyboard_write_register(TAB5_KEYBOARD_REG_EVENT_COUNT, 0) != ESP_OK ||
        tab5_keyboard_write_register(TAB5_KEYBOARD_REG_INT_STATUS, 0) != ESP_OK ||
        tab5_keyboard_write_register(TAB5_KEYBOARD_REG_INT_CFG, TAB5_KEYBOARD_INT_CHAR_EVENT) != ESP_OK) {
        return false;
    }

    s_i2c_error_streak = 0;
    ESP_LOGI(TAG, "Keyboard connected, firmware version 0x%02x", version);
    return true;
}

static bool tab5_keyboard_drain_events(void)
{
    uint8_t status = 0;
    if (tab5_keyboard_read_register(TAB5_KEYBOARD_REG_INT_STATUS, &status, 1) != ESP_OK) {
        return false;
    }
    if ((status & TAB5_KEYBOARD_INT_CHAR_EVENT) == 0) {
        return true;
    }

    uint8_t event_count = 0;
    if (tab5_keyboard_read_register(TAB5_KEYBOARD_REG_EVENT_COUNT, &event_count, 1) != ESP_OK) {
        return false;
    }
    if (event_count > 32) {
        event_count = 32;
    }

    for (uint8_t event_index = 0; event_index < event_count; event_index++) {
        uint8_t event_len = 0;
        if (tab5_keyboard_read_register(TAB5_KEYBOARD_REG_CHAR_LENGTH, &event_len, 1) != ESP_OK) {
            return false;
        }
        if (event_len == 0) {
            break;
        }
        if (event_len > TAB5_KEYBOARD_CHAR_EVENT_MAX_BYTES) {
            s_keyboard_errors++;
            return false;
        }

        uint8_t event[TAB5_KEYBOARD_CHAR_EVENT_MAX_BYTES] = {0};
        if (tab5_keyboard_read_register(TAB5_KEYBOARD_REG_CHAR_EVENT, event, event_len) != ESP_OK) {
            return false;
        }
        const uint16_t key = tab5_keyboard_decode_character(event[0], &event[1], event_len - 1U);
        if (key != 0) {
            tab5_keyboard_ring_push(key);
        }
    }

    return tab5_keyboard_write_register(TAB5_KEYBOARD_REG_INT_STATUS, 0) == ESP_OK;
}

static void run_tab5_keyboard(void *params)
{
    (void)params;
    TickType_t next_probe = 0;

    for (;;) {
        if (!s_keyboard_connected) {
            const TickType_t now = xTaskGetTickCount();
            if (now >= next_probe) {
                s_keyboard_connected = tab5_keyboard_configure_device();
                next_probe = now + pdMS_TO_TICKS(1000);
            }
            vTaskDelay(pdMS_TO_TICKS(20));
            continue;
        }

        if (gpio_get_level(TAB5_KEYBOARD_INT) == 0) {
            if (tab5_keyboard_drain_events()) {
                s_i2c_error_streak = 0;
            } else {
                s_keyboard_errors++;
                if (++s_i2c_error_streak >= 3) {
                    s_keyboard_connected = false;
                    ESP_LOGW(TAG, "Keyboard disconnected");
                }
            }
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}

void tab5_keyboard_start(void)
{
    const i2c_master_bus_config_t bus_config = {
        .i2c_port = TAB5_KEYBOARD_I2C_PORT,
        .sda_io_num = TAB5_KEYBOARD_I2C_SDA,
        .scl_io_num = TAB5_KEYBOARD_I2C_SCL,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    esp_err_t err = i2c_new_master_bus(&bus_config, &s_keyboard_bus);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Failed to create keyboard I2C bus: %s", esp_err_to_name(err));
        return;
    }

    const gpio_config_t int_config = {
        .pin_bit_mask = 1ULL << TAB5_KEYBOARD_INT,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    gpio_config(&int_config);

    if (xTaskCreatePinnedToCore(run_tab5_keyboard, "keyboard_task",
                                TAB5_KEYBOARD_TASK_STACK_WORDS, NULL,
                                tskIDLE_PRIORITY + 1, NULL, 0) != pdPASS) {
        ESP_LOGE(TAG, "Failed to create keyboard task");
    }
}

void lvgl_keyboard_read(lv_indev_t *indev, lv_indev_data_t *data)
{
    (void)indev;
    static bool release_pending;

    lv_group_t *default_group = lv_group_get_default();
    if (default_group != NULL && lv_indev_get_group(indev) != default_group) {
        lv_indev_set_group(indev, default_group);
    }

    data->state = LV_INDEV_STATE_RELEASED;
    data->continue_reading = false;
    if (release_pending) {
        release_pending = false;
        data->continue_reading = tab5_keyboard_ring_has_data();
        return;
    }

    uint16_t key = 0;
    if (!tab5_keyboard_ring_pop(&key)) {
        return;
    }

    if (tab5_keyboard_deliver_key(key)) {
        data->continue_reading = tab5_keyboard_ring_has_data();
        return;
    }

    uint32_t lv_key = key;
    switch (key) {
        case 258: lv_key = LV_KEY_DOWN; break;
        case 259: lv_key = LV_KEY_UP; break;
        case 260: lv_key = LV_KEY_LEFT; break;
        case 261: lv_key = LV_KEY_RIGHT; break;
        case 262: lv_key = LV_KEY_DEL; break;
        case 27: lv_key = LV_KEY_ESC; break;
        case 8: lv_key = LV_KEY_BACKSPACE; break;
        case 13: lv_key = LV_KEY_ENTER; break;
        case 9: lv_key = LV_KEY_NEXT; break;
        default: break;
    }
    data->key = lv_key;
    data->state = LV_INDEV_STATE_PRESSED;
    data->continue_reading = true;
    release_pending = true;
}

void tab5_keyboard_push_key(uint16_t key)
{
    if (key != 0) {
        tab5_keyboard_ring_push(key);
    }
}

bool tab5_keyboard_connected(void)
{
    return s_keyboard_connected;
}

uint32_t tab5_keyboard_event_count(void)
{
    return s_keyboard_events;
}

uint32_t tab5_keyboard_error_count(void)
{
    return s_keyboard_errors;
}

uint32_t tab5_keyboard_drop_count(void)
{
    return s_keyboard_drops;
}
