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
// Nothing in the port drives these yet. They are recorded so that whatever
// attaches first -- the CV DAC the board README sketches out, an ADC, any Grove
// unit -- takes the numbers from one place. Bringing the bus up needs its own
// i2c_master bus handle; bsp_i2c_get_handle() returns the internal bus and must
// not be repointed here. Unverified on hardware: whether the socket's 5V rail
// needs TAB5_EXT5V_EN asserted through the expander first.
#define TAB5_PORT_A_SDA         (53)
#define TAB5_PORT_A_SCL         (54)

// Audio and storage are intentionally left unassigned here until the board
// implementation is wired to the schematic and verified on hardware.
