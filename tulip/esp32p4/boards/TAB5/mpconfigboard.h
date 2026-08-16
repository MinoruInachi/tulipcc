#include "pins.h"

void tab5_board_startup(void);

#define MICROPY_HW_BOARD_NAME               "Tab5"
#define MICROPY_HW_MCU_NAME                 "ESP32P4"

#define MICROPY_PY_MACHINE_DAC              (0)

// Keep UART REPL enabled for bring-up parity with ESP32_GENERIC_P4.
// The long-term plan is to switch to native USB REPL when wired up.
#define MICROPY_HW_ENABLE_UART_REPL         (1)

// The USB-A connector is the host port, so the OTG controller behind it has to
// stay free for the USB host library.
//
// MICROPY_HW_ENABLE_USBDEV defaults to SOC_USB_OTG_SUPPORTED, and on ESP32-P4
// micropython/ports/esp32/mpconfigport.h then picks the HS PHY for TinyUSB
// ("ESP32-P4 uses the HS USB PHY (RHPORT1) for TinyUSB"). The P4 has exactly one
// UTMI/HS controller and that is the one wired to USB-A, so leaving the device
// stack on means usb_phy_init() claims the host port at boot -- which is what
// the "usb_phy: Using UTMI PHY instead of requested internal PHY" warning was.
//
// Nothing is lost: the REPL runs over USB-Serial-JTAG (a separate peripheral,
// CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y), and turning USBDEV off leaves
// MICROPY_HW_ESP_USB_SERIAL_JTAG enabled.
#define MICROPY_HW_ENABLE_USBDEV            (0)

// Tab5 wireless connectivity is handled by the companion ESP32-C6.
#define MICROPY_PY_NETWORK_WLAN             (1)
#define MICROPY_PY_BLUETOOTH                (0)
#define MICROPY_PY_ESPNOW                   (0)

#define MICROPY_BOARD_STARTUP               tab5_board_startup
