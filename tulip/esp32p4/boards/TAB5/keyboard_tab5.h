#pragma once

#include <stdbool.h>
#include <stdint.h>

void tab5_keyboard_start(void);
bool tab5_keyboard_deliver_key(uint16_t key);

// Queue a finished Tulip key code as if the built-in keyboard had produced it.
// A USB HID keyboard joins here (usb_host_tab5.c); both keyboards report HID scan
// codes and both run them through scan_ascii(), so everything downstream --
// lvgl_keyboard_read(), the REPL, the Editor -- sees one keyboard.
void tab5_keyboard_push_key(uint16_t key);

// Global brightness of the keyboard's two indicator LEDs, 0-100. Applied by the
// keyboard task, so this is safe to call from MicroPython, and re-applied if the
// keyboard is unplugged and comes back.
void tab5_keyboard_set_brightness(uint8_t percent);
uint8_t tab5_keyboard_get_brightness(void);

bool tab5_keyboard_connected(void);
uint32_t tab5_keyboard_event_count(void);
uint32_t tab5_keyboard_error_count(void);
uint32_t tab5_keyboard_drop_count(void);
