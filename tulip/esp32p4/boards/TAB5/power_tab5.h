#pragma once

#include <stdbool.h>

void tab5_power_init(void);
void tab5_power_enable_display(void);
void tab5_power_enable_usb_host(void);

// Drive and read back the USB-A connector's 5V load switch (BSP_USB_EN on IO
// expander 1). Reading the pin back separates "the expander is not driving the
// switch" from "the switch is on but the rail is not holding up", which is
// otherwise guesswork when a bus-powered device fails to start.
// Returns false if the I2C transaction failed.
bool tab5_power_set_usb_host(bool on);
bool tab5_power_get_usb_host(bool *on);
void tab5_power_enable_wifi(void);
