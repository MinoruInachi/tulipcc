#pragma once

#include <stdbool.h>
#include <stdint.h>

void tab5_keyboard_start(void);
bool tab5_keyboard_deliver_key(uint16_t key);

// Queue a finished Tulip key code as if the built-in keyboard had produced it.
// A USB HID keyboard joins here (usb_host_tab5.c), after scan_ascii() has turned
// its report into the same key codes the I2C part emits, so everything
// downstream -- lvgl_keyboard_read(), the REPL, the Editor -- sees one keyboard.
void tab5_keyboard_push_key(uint16_t key);
bool tab5_keyboard_connected(void);
uint32_t tab5_keyboard_event_count(void);
uint32_t tab5_keyboard_error_count(void);
uint32_t tab5_keyboard_drop_count(void);
