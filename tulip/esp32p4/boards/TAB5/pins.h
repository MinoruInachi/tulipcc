#pragma once

// Publicly documented Tab5 signals. Keep this file conservative until the
// esp32p4 port is wired to the real board schematic.

#define I2C_SDA                 (31)
#define I2C_SCL                 (32)

#define TAB5_LCD_LEDA           (22)

// Touch controller bus and interrupt line.
#define TAB5_TOUCH_INT          (-1)

// Board-level power and reset control signals exposed through the PI4IOE5V6408.
#define TAB5_LCD_RST            (-1)
#define TAB5_TOUCH_RST          (-1)
#define TAB5_EXT5V_EN           (-1)
#define TAB5_WLAN_PWR_EN        (-1)
#define TAB5_USB5V_EN           (-1)
#define TAB5_PWROFF_PULSE       (-1)

// Audio and storage are intentionally left unassigned here until the board
// implementation is wired to the schematic and verified on hardware.
