#pragma once

#include <stdbool.h>
#include <stdint.h>

// USB host support for the Tab5's USB-A connector: class-compliant USB MIDI
// plus HID boot-protocol keyboard and mouse. This is the ESP32-P4 counterpart
// of tulip/esp32s3/usb_host.c, with the device handling kept but the key and
// touch delivery re-pointed at the Tab5's own paths.
//
// tab5_usb_host_start() powers the connector and starts the host task. It must
// run after tab5_audio_init(), because a MIDI device can start sending the
// moment power comes up and the incoming bytes go straight into AMY.
void tab5_usb_host_start(void);

// Send raw MIDI bytes to the connected USB MIDI device. Packetises into USB-MIDI
// event packets, including sysex. Safe to call with no device connected.
void send_usb_midi_out(uint8_t *data, uint16_t len);

bool tab5_usb_midi_connected(void);
bool tab5_usb_keyboard_connected(void);
bool tab5_usb_mouse_connected(void);

// Attach/detach bookkeeping, for telling "nothing was ever plugged in" apart
// from "it attached but we could not use it". Exposed as tulip.usb_status().
uint32_t tab5_usb_attach_count(void);
uint32_t tab5_usb_detach_count(void);
uint32_t tab5_usb_unclaimed_count(void);
uint32_t tab5_usb_release_errors(void);
uint32_t tab5_usb_close_errors(void);
uint32_t tab5_usb_free_errors(void);
// VBUS cycles performed because nothing enumerated in time.
uint32_t tab5_usb_enum_retries(void);

// Root-port state. `connected` is the only way to tell an empty connector from
// a device that attached and failed to enumerate: the client API only reports
// devices that already made it through enumeration.
typedef struct {
    bool installed;    // host driver owns the port; the rest is meaningless if false
    bool connected;    // a device is asserting its D+/D- pull-up
    bool enabled;      // the port survived reset and is enabled
    bool overcurrent;  // the connector's load switch is reporting a fault
    bool powered;      // the controller believes VBUS is on
    uint8_t line_state;  // raw D-/D+ levels
    uint8_t speed;       // 0 = high, 1 = full, 2 = low
} tab5_usb_port_state_t;

void tab5_usb_port_state(tab5_usb_port_state_t *state);
