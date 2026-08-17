#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lvgl.h"

#include "../../../shared/keyscan.h"

#include "keyboard_tab5.h"

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
#define TAB5_KEYBOARD_REG_RGB_BRIGHTNESS 0x03
#define TAB5_KEYBOARD_REG_MODE 0x10
#define TAB5_KEYBOARD_REG_HID_EVENT 0x30
#define TAB5_KEYBOARD_REG_VERSION 0xfe

// The keyboard's two RGB LEDs (mode and caps indicators) are driven by the part
// itself; all the host gets is one global brightness, 0-100. The part powers up
// at 20, which is bright enough to be distracting in a dark room -- start lower
// and let tulip.keyboard_brightness() move it.
#define TAB5_KEYBOARD_BRIGHTNESS_MAX 100
#define TAB5_KEYBOARD_BRIGHTNESS_DEFAULT 5

// The keyboard part (an STM32 on I2C) has three reporting modes, selected by the
// mode register: 0 raw matrix row/col, 1 HID, 2 character. Character mode is the
// friendliest -- it hands over finished text like "ESC" or "a" -- but it only
// reports the press, never the release, so nothing downstream can know what is
// currently held down. HID mode reports (modifier, scan code) on press and
// (modifier, 0) on release, which is what last_scan[] and so tulip.keys() and
// tulip.joyk() need, and it goes through the same scan_ascii() decoder the USB
// keyboard uses. See github.com/m5stack/M5Tab5-Keyboard-Internal-FW.
#define TAB5_KEYBOARD_INT_HID_EVENT 0x02
#define TAB5_KEYBOARD_MODE_HID 0x01
#define TAB5_KEYBOARD_HID_EVENT_BYTES 2
// A HID boot report holds six scan codes besides the modifier byte.
#define TAB5_KEYBOARD_MAX_HELD 6
// Held-key auto-repeat, matching the USB keyboard's (usb_host_tab5.c). The part
// only reports the two edges, so the repeat has to come from this side.
#define TAB5_KEYBOARD_REPEAT_TRIGGER_MS 500
#define TAB5_KEYBOARD_REPEAT_INTER_MS 90

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
// The scan codes currently held down, oldest first, mirrored into last_scan[].
static uint8_t s_held[TAB5_KEYBOARD_MAX_HELD];
static uint8_t s_held_count;
// The key auto-repeat is chasing, and the scan code it came from: a release does
// not say which key went up, so the only way to know the repeat is still valid
// is to look for its scan code in s_held[].
static uint16_t s_repeat_key;
static uint8_t s_repeat_code;
static int64_t s_repeat_since_ms;
static int64_t s_repeat_last_ms;
// Brightness is written by the keyboard task, never by whoever asked for it: the
// I2C master driver has no lock of its own, so the MicroPython thread leaves the
// value here and the task picks it up on its next pass.
static uint8_t s_brightness = TAB5_KEYBOARD_BRIGHTNESS_DEFAULT;
static volatile bool s_brightness_pending = true;

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

// Publish the held keys where the rest of Tulip looks for them: last_scan[] is a
// HID boot report, [0] the modifier byte, [1] reserved, [2..7] the rollover
// slots. tulip.keys() hands it to Python and tulip.joyk() reads the joystick out
// of it. The USB keyboard writes the same array (usb_host_tab5.c), so the last
// keyboard touched wins -- pressing keys on both at once is the one case that
// confuses this, and it costs nothing to leave it that way.
static void tab5_keyboard_publish_held(uint8_t modifier)
{
    last_scan[0] = modifier;
    last_scan[1] = 0;
    for (uint8_t i = 0; i < TAB5_KEYBOARD_MAX_HELD; i++) {
        last_scan[i + 2] = (i < s_held_count) ? s_held[i] : 0;
    }
}

static bool tab5_keyboard_is_held(uint8_t code)
{
    for (uint8_t i = 0; i < s_held_count; i++) {
        if (s_held[i] == code) {
            return true;
        }
    }
    return false;
}

static void tab5_keyboard_hold(uint8_t code)
{
    if (!tab5_keyboard_is_held(code) && s_held_count < TAB5_KEYBOARD_MAX_HELD) {
        s_held[s_held_count++] = code;
    }
}

// A release event carries keycode 0, so it does not say which key came up. Drop
// the most recent one: releases usually happen in the reverse order of presses
// (hold right, tap jump, let go of jump), and anything the guess gets wrong
// clears itself as soon as every key is up.
static void tab5_keyboard_release_one(void)
{
    if (s_held_count > 0) {
        s_held_count--;
    }
}

static void tab5_keyboard_forget_held(void)
{
    s_held_count = 0;
    s_repeat_key = 0;
    s_repeat_code = 0;
    tab5_keyboard_publish_held(0);
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
        tab5_keyboard_write_register(TAB5_KEYBOARD_REG_MODE, TAB5_KEYBOARD_MODE_HID) != ESP_OK ||
        tab5_keyboard_write_register(TAB5_KEYBOARD_REG_EVENT_COUNT, 0) != ESP_OK ||
        tab5_keyboard_write_register(TAB5_KEYBOARD_REG_INT_STATUS, 0) != ESP_OK ||
        tab5_keyboard_write_register(TAB5_KEYBOARD_REG_INT_CFG, TAB5_KEYBOARD_INT_HID_EVENT) != ESP_OK) {
        return false;
    }

    // A keyboard that was just plugged in is back at its own default brightness,
    // so re-apply ours every time we (re)configure it.
    s_brightness_pending = true;

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
    if ((status & TAB5_KEYBOARD_INT_HID_EVENT) == 0) {
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
        uint8_t event[TAB5_KEYBOARD_HID_EVENT_BYTES] = {0};
        if (tab5_keyboard_read_register(TAB5_KEYBOARD_REG_HID_EVENT, event,
                                       TAB5_KEYBOARD_HID_EVENT_BYTES) != ESP_OK) {
            return false;
        }
        const uint8_t modifier = event[0];
        const uint8_t code = event[1];
        // Both bytes 0xff is how the part says the queue ran dry.
        if (modifier == 0xff && code == 0xff) {
            break;
        }

        if (code == 0) {
            tab5_keyboard_release_one();
            if (!tab5_keyboard_is_held(s_repeat_code)) {
                s_repeat_key = 0;
                s_repeat_code = 0;
            }
            tab5_keyboard_publish_held(modifier);
            continue;
        }

        tab5_keyboard_hold(code);
        tab5_keyboard_publish_held(modifier);

        // The same decoder the USB keyboard goes through, so a key types the same
        // character whichever keyboard it came from, and tulip.key_remap() covers
        // both. The modifier keys themselves (shift, ctrl, alt, sym, Aa) do not
        // produce an event of their own -- they only show up in this byte.
        const uint16_t key = scan_ascii(code, modifier);
        if (key != 0) {
            tab5_keyboard_ring_push(key);
            s_repeat_key = key;
            s_repeat_code = code;
            s_repeat_since_ms = esp_timer_get_time() / 1000;
            s_repeat_last_ms = 0;
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

        if (s_brightness_pending) {
            s_brightness_pending = false;
            if (tab5_keyboard_write_register(TAB5_KEYBOARD_REG_RGB_BRIGHTNESS, s_brightness) != ESP_OK) {
                s_keyboard_errors++;
            }
        }

        if (gpio_get_level(TAB5_KEYBOARD_INT) == 0) {
            if (tab5_keyboard_drain_events()) {
                s_i2c_error_streak = 0;
            } else {
                s_keyboard_errors++;
                if (++s_i2c_error_streak >= 3) {
                    s_keyboard_connected = false;
                    tab5_keyboard_forget_held();
                    ESP_LOGW(TAG, "Keyboard disconnected");
                }
            }
        }

        if (s_repeat_key != 0) {
            const int64_t now_ms = esp_timer_get_time() / 1000;
            if ((now_ms - s_repeat_since_ms) > TAB5_KEYBOARD_REPEAT_TRIGGER_MS &&
                (now_ms - s_repeat_last_ms) > TAB5_KEYBOARD_REPEAT_INTER_MS) {
                tab5_keyboard_ring_push(s_repeat_key);
                s_repeat_last_ms = now_ms;
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

void tab5_keyboard_set_brightness(uint8_t percent)
{
    if (percent > TAB5_KEYBOARD_BRIGHTNESS_MAX) {
        percent = TAB5_KEYBOARD_BRIGHTNESS_MAX;
    }
    s_brightness = percent;
    s_brightness_pending = true;
}

uint8_t tab5_keyboard_get_brightness(void)
{
    return s_brightness;
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
