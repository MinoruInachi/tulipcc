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
#define TAB5_EXT5V_EN           (-1)   // not a GPIO: see TAB5_EXT5V_EN_PIN
#define TAB5_WLAN_PWR_EN        (-1)
#define TAB5_USB5V_EN           (-1)
#define TAB5_PWROFF_PULSE       (-1)

// Port A -- the HY2.0-4P ("Grove") socket on the side, and the Tab5's only one.
//
// This is a SEPARATE I2C bus from the internal one above. GPIO 31/32 are shared
// by the touch controller, the BMI270, the ES8388/ES7210 codecs and the
// PI4IOE5V6408 expander; these two go nowhere but the socket. M5Stack's Arduino
// support calls this bus Wire1 and the internal one Wire.
//
// Wiring, from M5Stack's Tab5 pinmap: black GND, red 5V, yellow G53 = SDA,
// white G54 = SCL. Cross-checked against the same table's M5-Bus rows, which
// give the internal bus as pin 17 SDA = G31 / pin 18 SCL = G32 and so agree
// with BSP_I2C_SDA/BSP_I2C_SCL above -- note that means a device hung off
// M5-Bus I2C lands on the *internal* bus, not this one.
//
// Both hardware I2C controllers are taken -- 0 by the keyboard (GPIO 0/1), 1 by
// the internal bus -- so Python reaches this socket with machine.SoftI2C on
// these pins (tulip.grove_i2c()). machine.I2C(0) or (1) aborts the chip: the
// port's i2c_new_master_bus() fails on the busy controller inside
// ESP_ERROR_CHECK. bsp_i2c_get_handle() returns the internal bus and must not
// be repointed here.
#define TAB5_PORT_A_SDA         (53)
#define TAB5_PORT_A_SCL         (54)
// The socket's 5V rail, switched by P2 of IO expander 0x43 (not a GPIO); off
// out of reset. tab5_power_enable_ext5v() turns it on at boot.
#define TAB5_EXT5V_EN_PIN       (IO_EXPANDER_PIN_NUM_2)

// Audio and storage are intentionally left unassigned here until the board
// implementation is wired to the schematic and verified on hardware.
