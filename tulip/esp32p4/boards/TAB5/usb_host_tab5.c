#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include "esp_log.h"
#include "esp_timer.h"

#include "usb/usb_host.h"
#include "usb/usb_helpers.h"

// Root-port state, straight from the DWC OTG host port register. The host
// library exposes no way to ask "is anything plugged in?" -- a client only
// hears about a device once enumeration has already succeeded -- so a failed
// enumeration and an empty connector look identical from up here. They are not
// the same fault and they need different fixes, and HPRT tells them apart:
// prtconnsts is the D+/D- pull-up the device asserts when VBUS arrives, which
// happens long before any descriptor is read. prtovrcurract is the other thing
// worth having: the connector's load switch trips on its own, and an
// overcurrent latch looks exactly like "the port has no power".
#include "soc/usb_dwc_struct.h"

#include "../../../../amy/src/amy.h"       // MAX_MESSAGE_LEN
#include "../../../../amy/src/amy_midi.h"  // convert_midi_bytes_to_messages()

#include "../../../shared/display.h"
#include "../../../shared/keyscan.h"

#include "keyboard_tab5.h"
#include "power_tab5.h"
#include "usb_host_tab5.h"

/*
 * Port of tulip/esp32s3/usb_host.c to the Tab5.
 *
 * What is the same: the descriptor walk, the MIDI event-packet coder/decoder,
 * the HID boot-protocol request, and the polling loop.
 *
 * What is different, and why:
 *
 * - Keys. The S3 build owns the only keyboard on the board, so it converts a
 *   HID report straight into Tulip key codes and calls send_key_to_micropython()
 *   plus its own LVGL buffer. The Tab5 already has a keyboard -- the I2C part in
 *   keyboard_tab5.c -- which produces finished key codes and hands them to LVGL
 *   and the REPL through one ring buffer. A USB keyboard is a second source of
 *   the same thing, so it decodes with the shared scan_ascii() and then joins
 *   that ring via tab5_keyboard_push_key(). Everything downstream stays unaware
 *   there are two keyboards.
 *
 * - MIDI out handshake. The S3 blocks the caller on a task notification aimed at
 *   tulip_mp_handle, which silently assumes the MicroPython task is the caller
 *   and deadlocks if usb_host_transfer_submit() fails (it waits before checking
 *   the error). Here the completion is a binary semaphore, the submit result is
 *   checked first, and the wait has a timeout.
 *
 * - Power. The connector's 5V comes from the PI4IOE5V6408 expander, so
 *   tab5_power_enable_usb_host() has to run before usb_host_install().
 *
 * The P4 has one UTMI/HS controller and it is the one wired to USB-A, so
 * MICROPY_HW_ENABLE_USBDEV is 0 for this board (see mpconfigboard.h) -- with
 * MicroPython's TinyUSB device stack enabled it claims that PHY at boot and
 * usb_host_install() below would get nothing.
 */

static const char *TAG = "TAB5-USB";

// Dump every descriptor of a newly attached device. Off by default: the Tab5's
// boot log is read over USB-Serial-JTAG during bring-up and a hub full of
// devices buries everything else.
#define TAB5_USB_DUMP_DESCRIPTORS 0

#define TAB5_USB_TASK_NAME "usb_task"
#define TAB5_USB_TASK_STACK_WORDS (4 * 1024 / sizeof(StackType_t))
/* Below the touch task and above the display task: USB is interrupt/bulk driven
 * with its own hardware queues, so it does not need to pre-empt a finger drag,
 * but a MIDI note should not wait out a frame composite either. */
#define TAB5_USB_TASK_PRIORITY (tskIDLE_PRIORITY + 2)
#define TAB5_USB_TASK_CORE (0)

#define MIDI_IN_BUFFERS 8
#define MIDI_OUT_TIMEOUT_MS 100

// How long the connector stays unpowered at startup before the host switches it
// on. See the comment in tab5_usb_host_start() for where the number comes from.
#define TAB5_USB_VBUS_OFF_MS 1000

/*
 * Enumeration retry.
 *
 * The host library does not retry a failed enumeration. usb_host.c's
 * enum_event_callback() turns ENUM_EVENT_CANCELED straight into
 * hub_node_disable() -- the port is switched off and nothing ever revisits it,
 * so a device that lost its first EP0 control transfer stays dark until someone
 * physically unplugs it.
 *
 * That first transfer is not reliable here: a KORG microKEY-37 measured 7/10
 * connects with the stock timings, and raising RESET_RECOVERY_MS/
 * SET_ADDR_RECOVERY_MS well past their USB 2.0 minimums did not move the number.
 * What always works is the next connect, which is why unplugging and replugging
 * "fixed" it -- so the recovery we have is a VBUS cycle, and the only thing
 * missing was something to notice and perform it.
 *
 * The window is measured from the device's connect, not from our VBUS write.
 * Measured on a microKEY-37, a successful enumeration reports in ~900ms from
 * the connect edge, so 2s is more than double what it takes; the window is
 * also the latency a user waits through on every retry, so there is no point
 * making it generous beyond that.
 */
#define TAB5_USB_ENUM_TIMEOUT_MS 2000
// Each retry costs a VBUS off period plus the verdict window, so the budget is
// the worst-case delay before a stubborn device gives up: 6 is about 20s. This
// only ever runs with a device actually connected, and the microKEY needs 1-3
// of them on a typical boot, so a budget of 6 leaves real headroom.
#define TAB5_USB_ENUM_RETRIES 6

#define KEYBOARD_BYTES 8
#define MOUSE_BYTES 8
#define DEFAULT_TIMEOUT_MS 5000

// How long a key is held before it starts repeating, and how often after that.
#define KEY_REPEAT_TRIGGER_MS 500
#define KEY_REPEAT_INTER_MS 90

#define HOST_EVENT_TIMEOUT 1
#define CLIENT_EVENT_TIMEOUT 1

static usb_host_client_handle_t s_client;

static usb_device_handle_t s_dev_midi;
static usb_device_handle_t s_dev_kb;
static usb_device_handle_t s_dev_mouse;

static uint8_t s_intf_midi;
static uint8_t s_intf_kb;
static uint8_t s_intf_mouse;

static bool s_midi_claimed, s_midi_has_in, s_midi_has_out, s_midi_ready;
static bool s_kb_claimed, s_kb_ready;
static bool s_mouse_claimed, s_mouse_ready;

static usb_transfer_t *s_midi_in[MIDI_IN_BUFFERS];
static usb_transfer_t *s_midi_out;
static SemaphoreHandle_t s_midi_out_done;

static usb_transfer_t *s_kb_in;
static uint16_t s_kb_bytes = KEYBOARD_BYTES;
static uint8_t s_kb_interval;
static bool s_kb_polling;

static usb_transfer_t *s_mouse_in;
static uint16_t s_mouse_bytes = MOUSE_BYTES;
static uint8_t s_mouse_interval;
static bool s_mouse_polling;
static int16_t s_mouse_x, s_mouse_y;

static bool s_boot_protocol_requested;

static int64_t s_vbus_off_since;

// Enumeration retry state, all owned by the USB task. The clock that matters is
// the one started by the device's own connect, not by our VBUS write: a device
// plugged in minutes after boot has to get its full window, and measuring from
// power-on gave it none at all -- the deadline had passed before it was ever
// plugged in, so the first hot-plug got its enumeration aborted about 100 ms in
// and only succeeded on the retry.
static int64_t s_vbus_on_at_ms;
static bool s_port_was_connected;
static int64_t s_port_connected_at_ms;
static uint32_t s_attach_count_at_connect;
// Set while the disconnect we are about to see is one we caused, so that our own
// VBUS cycle does not read as a fresh device and hand itself a new budget.
static bool s_reconnect_is_ours;
static int s_enum_retries_left;
static uint32_t s_enum_retry_count;

static uint32_t s_attach_count;
// Teardown failures, surfaced through tulip.usb_status(). A device that cannot
// be released or closed keeps its bus address, and re-plugging it silently does
// nothing -- so these need to be visible without a serial cable attached.
static uint32_t s_release_errors;
static uint32_t s_close_errors;
static uint32_t s_free_errors;
static uint32_t s_detach_count;
static uint32_t s_unclaimed_count;

// Key auto-repeat, driven from the polling loop.
static uint16_t s_held_key;
static int64_t s_held_since_ms;
static int64_t s_last_repeat_ms;

static void new_enumeration_config_fn(const usb_config_desc_t *config_desc,
                                      usb_device_handle_t device_handle);

/* ------------------------------------------------------------------ MIDI in */

// USB MIDI event packets are always 4 bytes: [CN<<4 | CIN][MIDI_0][MIDI_1][MIDI_2].
// CN is the virtual cable, CIN classifies the 3 MIDI bytes (Table 4-1 of the
// MIDI 1.0 spec at usb.org).
static void midi_transfer_cb(usb_transfer_t *transfer)
{
    if (s_dev_midi != transfer->device_handle) {
        return;
    }
    const bool in_xfer = (transfer->bEndpointAddress & USB_B_ENDPOINT_ADDRESS_EP_DIR_MASK) != 0;
    if (transfer->status != 0 || !in_xfer) {
        if (transfer->status != USB_TRANSFER_STATUS_CANCELED) {
            ESP_LOGW(TAG, "MIDI in transfer status %d", transfer->status);
        }
        return;
    }

    const uint8_t *p = transfer->data_buffer;
    for (int i = 0; i + 3 < transfer->actual_num_bytes; i += 4) {
        if ((p[i] + p[i + 1] + p[i + 2] + p[i + 3]) == 0) {
            break;
        }
        // Drop byte 0, the cable/CIN header; the rest is the MIDI message.
        convert_midi_bytes_to_messages((uint8_t *)(p + i + 1), 3, 1);
    }

    const esp_err_t err = usb_host_transfer_submit(transfer);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "MIDI in resubmit failed: %s", esp_err_to_name(err));
    }
}

/* ----------------------------------------------------------------- MIDI out */

static void midi_out_transfer_cb(usb_transfer_t *transfer)
{
    (void)transfer;
    if (s_midi_out_done != NULL) {
        xSemaphoreGive(s_midi_out_done);
    }
}

// Submit whatever is already staged in s_midi_out and wait for it to land.
// Unlike the S3 version this checks the submit result before waiting, so a
// failed submit reports instead of blocking forever.
static void midi_out_transfer(void)
{
    if (!s_midi_ready || s_midi_out == NULL || s_midi_out_done == NULL) {
        return;
    }
    xSemaphoreTake(s_midi_out_done, 0); // drop a stale completion
    const esp_err_t err = usb_host_transfer_submit(s_midi_out);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "MIDI out submit failed: %s", esp_err_to_name(err));
        return;
    }
    if (xSemaphoreTake(s_midi_out_done, pdMS_TO_TICKS(MIDI_OUT_TIMEOUT_MS)) != pdTRUE) {
        ESP_LOGW(TAG, "MIDI out timed out after %d ms", MIDI_OUT_TIMEOUT_MS);
    }
}

static void send_single_midi_out_packet(const uint8_t *packet) // 4 bytes
{
    if (!s_midi_ready || s_midi_out == NULL) {
        return;
    }
    memcpy(s_midi_out->data_buffer, packet, 4);
    s_midi_out->num_bytes = 4;
    midi_out_transfer();
}

#define MAX_USB_SYSEX_BYTES (MAX_MESSAGE_LEN + 3)
static uint8_t s_sysex_out[MAX_USB_SYSEX_BYTES];
static uint16_t s_sysex_out_len;
static uint8_t s_sysex_out_active;

// Sysex goes out as CIN 0x4 continuation packets with a 0x5/0x6/0x7 terminator
// carrying the 1/2/3 trailing bytes, batched 16 packets (64 bytes) at a time.
static void usb_emit_sysex(void)
{
    if (s_sysex_out_len == 0 || s_midi_out == NULL) {
        return;
    }
    uint8_t data[64] = {0};
    uint8_t packet_count = 0;
    uint16_t i = 0;

    while (i != s_sysex_out_len) {
        if (s_sysex_out_len - i > 3) {
            data[(packet_count * 4) + 0] = 0x04;
            data[(packet_count * 4) + 1] = s_sysex_out[i++];
            data[(packet_count * 4) + 2] = s_sysex_out[i++];
            data[(packet_count * 4) + 3] = s_sysex_out[i++];
        } else {
            switch (s_sysex_out_len - i) {
                case 1:
                    data[(packet_count * 4) + 0] = 0x05;
                    data[(packet_count * 4) + 1] = s_sysex_out[i++];
                    break;
                case 2:
                    data[(packet_count * 4) + 0] = 0x06;
                    data[(packet_count * 4) + 1] = s_sysex_out[i++];
                    data[(packet_count * 4) + 2] = s_sysex_out[i++];
                    break;
                case 3:
                    data[(packet_count * 4) + 0] = 0x07;
                    data[(packet_count * 4) + 1] = s_sysex_out[i++];
                    data[(packet_count * 4) + 2] = s_sysex_out[i++];
                    data[(packet_count * 4) + 3] = s_sysex_out[i++];
                    break;
                default:
                    break;
            }
        }
        packet_count++;
        if (packet_count == 16) {
            memcpy(s_midi_out->data_buffer, data, 64);
            memset(data, 0, sizeof(data));
            s_midi_out->num_bytes = 64;
            midi_out_transfer();
            packet_count = 0;
        }
    }

    if (packet_count > 0) {
        memcpy(s_midi_out->data_buffer, data, packet_count * 4);
        s_midi_out->num_bytes = packet_count * 4;
        midi_out_transfer();
    }
}

// Reverse of the MIDI input parser: work out each message's code index from the
// byte stream and emit it as a USB-MIDI event packet.
void send_usb_midi_out(uint8_t *data, uint16_t len)
{
    if (!s_midi_ready || !s_midi_has_out) {
        return;
    }

    uint8_t packet[4] = {0, 0, 0, 0};
    uint8_t slot = 0;

    for (uint16_t i = 0; i < len; i++) {
        const uint8_t byte = data[i];

        if (s_sysex_out_active) {
            if (s_sysex_out_len < MAX_USB_SYSEX_BYTES) {
                s_sysex_out[s_sysex_out_len++] = byte;
            }
            if (byte == 0xF7) {
                s_sysex_out_active = 0;
                usb_emit_sysex();
                s_sysex_out_len = 0;
            }
            continue;
        }

        if (byte & 0x80) { // status byte
            packet[1] = byte;
            if (byte == 0xF4 || byte == 0xF5 || byte == 0xF6 || byte == 0xF8 ||
                byte == 0xF9 || byte == 0xFA || byte == 0xFB || byte == 0xFC ||
                byte == 0xFD || byte == 0xFE || byte == 0xFF) {
                packet[0] = 0x05; // single-byte system common
                send_single_midi_out_packet(packet);
                return;
            }
            if (byte == 0xF0) {
                s_sysex_out_len = 0;
                s_sysex_out[s_sysex_out_len++] = 0xF0;
                s_sysex_out_active = 1;
            }
            // Anything else expects at least one data byte; wait for it.
            continue;
        }

        // Data byte.
        const uint8_t status = packet[1] & 0xF0;
        if (status == 0x80 || status == 0x90 || status == 0xA0 ||
            status == 0xB0 || status == 0xE0 || packet[1] == 0xF2) {
            // Two data bytes. For channel voice messages the high nibble of the
            // status byte doubles as the code index.
            packet[0] = (packet[1] == 0xF2) ? 0x03 : (status >> 4);
            if (slot == 0) {
                packet[2] = byte;
                slot = 1;
            } else {
                packet[3] = byte;
                slot = 0;
                send_single_midi_out_packet(packet);
            }
        } else if (status == 0xC0 || status == 0xD0) {
            // One data byte.
            packet[0] = (status >> 4);
            packet[2] = byte;
            send_single_midi_out_packet(packet);
            return;
        } else if (packet[1] == 0xF3 || packet[1] == 0xF1) {
            packet[0] = 0x02;
            packet[2] = byte;
            send_single_midi_out_packet(packet);
            return;
        }
    }
}

/* ------------------------------------------------------- MIDI claim/prepare */

static bool check_interface_desc_midi(const void *p, usb_device_handle_t device_handle)
{
    const usb_intf_desc_t *intf = (const usb_intf_desc_t *)p;
    // Class-compliant USB MIDI is an Audio class device, subclass 3 (MIDIStreaming).
    if (intf->bInterfaceClass != USB_CLASS_AUDIO ||
        intf->bInterfaceSubClass != 3 ||
        intf->bInterfaceProtocol != 0) {
        return false;
    }

    s_midi_claimed = true;
    s_dev_midi = device_handle;
    s_intf_midi = intf->bInterfaceNumber;
    const esp_err_t err = usb_host_interface_claim(s_client, s_dev_midi, s_intf_midi,
                                                  intf->bAlternateSetting);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "MIDI interface claim failed: %s", esp_err_to_name(err));
        s_midi_claimed = false;
        return false;
    }
    ESP_LOGI(TAG, "Claimed USB MIDI interface %d", s_intf_midi);
    return true;
}

static void prepare_endpoint_midi(const void *p)
{
    const usb_ep_desc_t *endpoint = (const usb_ep_desc_t *)p;

    if ((endpoint->bmAttributes & USB_BM_ATTRIBUTES_XFERTYPE_MASK) != USB_BM_ATTRIBUTES_XFER_BULK) {
        ESP_LOGW(TAG, "MIDI endpoint is not bulk (0x%02x), ignoring", endpoint->bmAttributes);
        return;
    }

    if (endpoint->bEndpointAddress & USB_B_ENDPOINT_ADDRESS_EP_DIR_MASK) {
        for (int i = 0; i < MIDI_IN_BUFFERS; i++) {
            if (s_midi_in[i] == NULL) {
                const esp_err_t err = usb_host_transfer_alloc(endpoint->wMaxPacketSize, 0, &s_midi_in[i]);
                if (err != ESP_OK) {
                    s_midi_in[i] = NULL;
                    ESP_LOGE(TAG, "MIDI in transfer alloc failed: %s", esp_err_to_name(err));
                    return;
                }
            }
            s_midi_in[i]->device_handle = s_dev_midi;
            s_midi_in[i]->bEndpointAddress = endpoint->bEndpointAddress;
            s_midi_in[i]->callback = midi_transfer_cb;
            s_midi_in[i]->context = (void *)(intptr_t)i;
            s_midi_in[i]->num_bytes = endpoint->wMaxPacketSize;
            const esp_err_t err = usb_host_transfer_submit(s_midi_in[i]);
            if (err != ESP_OK) {
                ESP_LOGW(TAG, "MIDI in submit failed: %s", esp_err_to_name(err));
            }
            s_midi_has_in = true;
        }
    } else {
        if (s_midi_out == NULL) {
            const esp_err_t err = usb_host_transfer_alloc(endpoint->wMaxPacketSize, 0, &s_midi_out);
            if (err != ESP_OK) {
                s_midi_out = NULL;
                ESP_LOGE(TAG, "MIDI out transfer alloc failed: %s", esp_err_to_name(err));
                return;
            }
        }
        s_midi_out->device_handle = s_dev_midi;
        s_midi_out->bEndpointAddress = endpoint->bEndpointAddress;
        s_midi_out->callback = midi_out_transfer_cb;
        s_midi_out->context = NULL;
        s_midi_out->flags |= USB_TRANSFER_FLAG_ZERO_PACK;
        s_midi_has_out = true;
    }

    // Ready once both directions exist, matching the other Tulip targets.
    s_midi_ready = s_midi_has_in && s_midi_has_out;
    if (s_midi_ready) {
        ESP_LOGI(TAG, "USB MIDI ready");
    }
}

/* -------------------------------------------------------------- HID: common */

static void boot_protocol_cb(usb_transfer_t *transfer)
{
    (void)transfer;
}

// SET_PROTOCOL(boot) on the control endpoint, so keyboards that default to
// report protocol send the 8-byte boot report decode_keyboard_report() expects.
static void request_boot_protocol(usb_device_handle_t device_handle)
{
    static const uint8_t setup[8] = {
        0x21, // host to device, class, interface
        0x0B, // SET_PROTOCOL
        0x00, 0x00, // boot protocol
        0x00, 0x00,
        0x00, 0x00,
    };

    usb_transfer_t *ctrl_transfer = NULL;
    esp_err_t err = usb_host_transfer_alloc(sizeof(setup), 0, &ctrl_transfer);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "boot protocol transfer alloc failed: %s", esp_err_to_name(err));
        return;
    }
    ctrl_transfer->device_handle = device_handle;
    ctrl_transfer->bEndpointAddress = 0;
    ctrl_transfer->callback = boot_protocol_cb;
    ctrl_transfer->context = NULL;
    memcpy(ctrl_transfer->data_buffer, setup, sizeof(setup));
    ctrl_transfer->num_bytes = sizeof(setup);

    err = usb_host_transfer_submit_control(s_client, ctrl_transfer);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "boot protocol request failed: %s", esp_err_to_name(err));
    }
    s_boot_protocol_requested = true;
}

/* ------------------------------------------------------------ HID: keyboard */

// A HID boot report is [modifier][reserved][6 scan codes]. Only codes that were
// not in the previous report are new presses; an empty report is a release.
static void decode_keyboard_report(const uint8_t *p)
{
    const uint8_t modifier = p[0];
    bool any_key_down = false;
    bool new_key = false;

    for (uint8_t i = 2; i < 8; i++) {
        if (p[i] == 0) {
            continue;
        }
        any_key_down = true;

        bool already_held = false;
        for (uint8_t j = 2; j < 8; j++) {
            if (last_scan[j] == p[i]) {
                already_held = true;
                break;
            }
        }
        if (already_held) {
            continue;
        }

        const uint16_t c = scan_ascii(p[i], modifier);
        if (c != 0) {
            new_key = true;
            s_held_key = c;
            s_held_since_ms = esp_timer_get_time() / 1000;
            s_last_repeat_ms = 0;
            tab5_keyboard_push_key(c);
        }
    }

    if (!new_key && !any_key_down) {
        s_held_key = 0;
        s_held_since_ms = 0;
        s_last_repeat_ms = 0;
    }

    memcpy(last_scan, p, 8);
}

#define BIT_IS_SET(var, pos) (((var) & (1 << (pos))) != 0)

static void keyboard_transfer_cb(usb_transfer_t *transfer)
{
    if (s_dev_kb != transfer->device_handle) {
        return;
    }
    s_kb_polling = false;
    if (transfer->status != 0) {
        if (transfer->status != USB_TRANSFER_STATUS_CANCELED) {
            ESP_LOGW(TAG, "keyboard transfer status %d", transfer->status);
        }
        return;
    }

    const uint8_t *p = transfer->data_buffer;
    const int n = transfer->actual_num_bytes;

    if (n == 8 || n == 16 || n == 18) {
        decode_keyboard_report(p);
    } else if (n == 10) {
        decode_keyboard_report(p + 1); // leading report ID
    } else if (n >= 3) {
        // Some USB FS HID keyboards (8bitdo retro, various custom ortho boards)
        // report held keys as a bitmask rather than a scan-code array. Each set
        // bit is a scan code; rebuild a boot-style report from them.
        // See https://stackoverflow.com/questions/57793525/unusual-usb-hid-reports
        uint8_t rebuilt[8] = {0};
        rebuilt[0] = p[1]; // modifier
        uint16_t bit_count = 0;
        uint8_t rollover = 2;
        for (uint8_t i = 2; i + 1 < p[0] && i < n; i++) {
            for (uint8_t bit = 0; bit < 8; bit++) {
                if (BIT_IS_SET(p[i], bit)) {
                    rebuilt[rollover++] = (uint8_t)bit_count;
                    if (rollover == 8) {
                        rollover = 2;
                    }
                }
                bit_count++;
            }
        }
        decode_keyboard_report(rebuilt);
    }
}

static bool check_interface_desc_boot_keyboard(const void *p, usb_device_handle_t device_handle)
{
    const usb_intf_desc_t *intf = (const usb_intf_desc_t *)p;
    if (intf->bInterfaceClass != USB_CLASS_HID ||
        intf->bInterfaceSubClass != 1 || // boot interface
        intf->bInterfaceProtocol != 1) { // keyboard
        return false;
    }

    s_kb_claimed = true;
    s_dev_kb = device_handle;
    s_intf_kb = intf->bInterfaceNumber;
    const esp_err_t err = usb_host_interface_claim(s_client, s_dev_kb, s_intf_kb,
                                                  intf->bAlternateSetting);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "keyboard interface claim failed: %s", esp_err_to_name(err));
        s_kb_claimed = false;
        return false;
    }
    ESP_LOGI(TAG, "Claimed USB HID keyboard interface %d", s_intf_kb);
    return true;
}

static void prepare_endpoint_hid_kb(const void *p)
{
    const usb_ep_desc_t *endpoint = (const usb_ep_desc_t *)p;

    if ((endpoint->bmAttributes & USB_BM_ATTRIBUTES_XFERTYPE_MASK) != USB_BM_ATTRIBUTES_XFER_INT) {
        ESP_LOGW(TAG, "keyboard endpoint is not interrupt (0x%02x)", endpoint->bmAttributes);
        return;
    }
    if (!(endpoint->bEndpointAddress & USB_B_ENDPOINT_ADDRESS_EP_DIR_MASK)) {
        return; // OUT endpoint (LEDs); nothing to do
    }

    s_kb_bytes = (uint16_t)usb_round_up_to_mps(KEYBOARD_BYTES, endpoint->wMaxPacketSize);
    if (s_kb_in == NULL) {
        const esp_err_t err = usb_host_transfer_alloc(s_kb_bytes, 0, &s_kb_in);
        if (err != ESP_OK) {
            s_kb_in = NULL;
            ESP_LOGE(TAG, "keyboard transfer alloc failed: %s", esp_err_to_name(err));
            return;
        }
    }
    s_kb_in->device_handle = s_dev_kb;
    s_kb_in->timeout_ms = DEFAULT_TIMEOUT_MS;
    s_kb_in->num_bytes = s_kb_bytes;
    s_kb_in->bEndpointAddress = endpoint->bEndpointAddress;
    s_kb_in->callback = keyboard_transfer_cb;
    s_kb_in->context = NULL;
    s_kb_interval = endpoint->bInterval;
    s_kb_ready = true;

    if (!s_boot_protocol_requested) {
        request_boot_protocol(s_dev_kb);
    }
    ESP_LOGI(TAG, "USB keyboard ready (%d byte reports)", s_kb_bytes);
}

/* --------------------------------------------------------------- HID: mouse */

// Standard HID boot mouse report.
typedef struct {
    union {
        struct {
            uint8_t button1 : 1;
            uint8_t button2 : 1;
            uint8_t button3 : 1;
            uint8_t reserved : 5;
        };
        uint8_t val;
    } buttons;
    int8_t x_displacement;
    int8_t y_displacement;
} __attribute__((packed)) hid_mouse_input_report_boot_t;

static void mouse_transfer_cb(usb_transfer_t *transfer)
{
    if (s_dev_mouse != transfer->device_handle) {
        return;
    }
    s_mouse_polling = false;
    if (transfer->status != 0) {
        if (transfer->status != USB_TRANSFER_STATUS_CANCELED) {
            ESP_LOGW(TAG, "mouse transfer status %d", transfer->status);
        }
        return;
    }
    if (transfer->actual_num_bytes < (int)sizeof(hid_mouse_input_report_boot_t) + 1) {
        return;
    }

    // Byte 0 is the report ID; the boot report follows.
    const hid_mouse_input_report_boot_t *report =
        (const hid_mouse_input_report_boot_t *)(transfer->data_buffer + 1);

    s_mouse_x += report->x_displacement;
    s_mouse_y += report->y_displacement;
    if (s_mouse_x < 0) s_mouse_x = 0;
    if (s_mouse_y < 0) s_mouse_y = 0;
    if (s_mouse_x >= H_RES) s_mouse_x = H_RES - 1;
    if (s_mouse_y >= V_RES) s_mouse_y = V_RES - 1;

    last_touch_x[0] = s_mouse_x;
    last_touch_y[0] = s_mouse_y;
    enable_mouse_pointer(); // no-op once installed

    // A held button 1 is a touch down, everything else is a touch up. That is
    // the same mapping the S3 uses, and it is what lets the mouse drive the same
    // UI the panel does.
    send_touch_to_micropython(s_mouse_x, s_mouse_y, report->buttons.button1 ? 0 : 1);
}

static bool check_interface_desc_boot_mouse(const void *p, usb_device_handle_t device_handle)
{
    const usb_intf_desc_t *intf = (const usb_intf_desc_t *)p;
    if (intf->bInterfaceClass != USB_CLASS_HID) {
        return false;
    }

    if (intf->bInterfaceSubClass == 1 && intf->bInterfaceProtocol == 2) {
        s_dev_mouse = device_handle;
    } else if (intf->bInterfaceSubClass == 0 && intf->bInterfaceProtocol == 0 && s_kb_claimed) {
        // Combo keyboard/mouse dongles expose the pointer as a plain HID
        // interface on the device that already gave us the keyboard.
        s_dev_mouse = s_dev_kb;
    } else {
        return false;
    }

    s_mouse_claimed = true;
    s_intf_mouse = intf->bInterfaceNumber;
    const esp_err_t err = usb_host_interface_claim(s_client, s_dev_mouse, s_intf_mouse,
                                                  intf->bAlternateSetting);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "mouse interface claim failed: %s", esp_err_to_name(err));
        s_mouse_claimed = false;
        return false;
    }
    ESP_LOGI(TAG, "Claimed USB HID mouse interface %d", s_intf_mouse);
    return true;
}

static void prepare_endpoint_hid_mouse(const void *p)
{
    const usb_ep_desc_t *endpoint = (const usb_ep_desc_t *)p;

    if ((endpoint->bmAttributes & USB_BM_ATTRIBUTES_XFERTYPE_MASK) != USB_BM_ATTRIBUTES_XFER_INT) {
        ESP_LOGW(TAG, "mouse endpoint is not interrupt (0x%02x)", endpoint->bmAttributes);
        return;
    }
    if (!(endpoint->bEndpointAddress & USB_B_ENDPOINT_ADDRESS_EP_DIR_MASK)) {
        return;
    }

    s_mouse_bytes = (uint16_t)usb_round_up_to_mps(MOUSE_BYTES, endpoint->wMaxPacketSize);
    if (s_mouse_in == NULL) {
        const esp_err_t err = usb_host_transfer_alloc(s_mouse_bytes, 0, &s_mouse_in);
        if (err != ESP_OK) {
            s_mouse_in = NULL;
            ESP_LOGE(TAG, "mouse transfer alloc failed: %s", esp_err_to_name(err));
            return;
        }
    }
    s_mouse_in->device_handle = s_dev_mouse;
    s_mouse_in->bEndpointAddress = endpoint->bEndpointAddress;
    s_mouse_in->callback = mouse_transfer_cb;
    s_mouse_in->context = NULL;
    s_mouse_interval = endpoint->bInterval;
    s_mouse_x = H_RES / 2;
    s_mouse_y = V_RES / 2;
    s_mouse_ready = true;

    if (!s_boot_protocol_requested) {
        request_boot_protocol(s_dev_mouse);
    }
    ESP_LOGI(TAG, "USB mouse ready");
}

/* --------------------------------------------------------- enumeration walk */

#if TAB5_USB_DUMP_DESCRIPTORS
static void show_interface_desc(const void *p)
{
    const usb_intf_desc_t *intf = (const usb_intf_desc_t *)p;
    ESP_LOGI(TAG, "  interface %d alt %d: class 0x%02x sub 0x%02x proto 0x%02x, %d endpoints",
             intf->bInterfaceNumber, intf->bAlternateSetting, intf->bInterfaceClass,
             intf->bInterfaceSubClass, intf->bInterfaceProtocol, intf->bNumEndpoints);
}

static void show_endpoint_desc(const void *p)
{
    static const char *const kinds[] = {"control", "isochronous", "bulk", "interrupt"};
    const usb_ep_desc_t *ep = (const usb_ep_desc_t *)p;
    ESP_LOGI(TAG, "    endpoint 0x%02x %s %s, mps %d, interval %d",
             ep->bEndpointAddress,
             (ep->bEndpointAddress & USB_B_ENDPOINT_ADDRESS_EP_DIR_MASK) ? "in" : "out",
             kinds[ep->bmAttributes & USB_BM_ATTRIBUTES_XFERTYPE_MASK],
             ep->wMaxPacketSize, ep->bInterval);
}
#else
#define show_interface_desc(p) do { } while (0)
#define show_endpoint_desc(p) do { } while (0)
#endif

// Walk a newly attached device's configuration and claim what we recognise.
// Interfaces come before their endpoints, so by the time an endpoint shows up
// the *_claimed flags already say who it belongs to.
static void new_enumeration_config_fn(const usb_config_desc_t *config_desc,
                                      usb_device_handle_t device_handle)
{
    if (config_desc == NULL) {
        return;
    }

    // The full configuration -- interface and endpoint descriptors included --
    // follows the 9-byte header in memory, so the walk starts at the header
    // itself. Addressed through the struct pointer rather than a `val[]` union
    // member because this board resolves usb/usb_types_ch9.h to the
    // espressif__usb managed component (a dependency of usb_host_uvc, pulled in
    // by the BSP), whose usb_config_desc_t is a plain struct with no such member.
    const uint8_t *p = (const uint8_t *)config_desc;
    uint8_t bLength;

    for (int i = 0; i < config_desc->wTotalLength; i += bLength, p += bLength) {
        bLength = *p;
        if (bLength == 0 || (i + bLength) > config_desc->wTotalLength) {
            ESP_LOGW(TAG, "malformed descriptor at offset %d", i);
            return;
        }
        switch (*(p + 1)) {
            case USB_B_DESCRIPTOR_TYPE_INTERFACE:
                show_interface_desc(p);
                if (!s_midi_claimed) { check_interface_desc_midi(p, device_handle); }
                if (!s_kb_claimed) { check_interface_desc_boot_keyboard(p, device_handle); }
                if (!s_mouse_claimed) { check_interface_desc_boot_mouse(p, device_handle); }
                break;
            case USB_B_DESCRIPTOR_TYPE_ENDPOINT:
                show_endpoint_desc(p);
                if (s_kb_claimed && !s_kb_ready) { prepare_endpoint_hid_kb(p); }
                if (s_mouse_claimed && !s_mouse_ready) { prepare_endpoint_hid_mouse(p); }
                if (s_midi_claimed && !s_midi_ready) { prepare_endpoint_midi(p); }
                break;
            default:
                // Configuration, HID (0x21), class-specific MIDI (0x24/0x25) and
                // friends carry nothing we act on.
                break;
        }
    }
}

/* --------------------------------------------------------------- host plumbing */

/*
 * Every transfer allocated for a device has to be handed back before
 * usb_host_device_close() will let go of it. Skip that and the close fails with
 * ESP_ERR_INVALID_STATE, the host library keeps the device object and its bus
 * address forever, and re-plugging that same device never enumerates again --
 * which reads as "the port died", because a *different* device gets a different
 * address and still works.
 *
 * Order matters: release the interface first so the library halts and flushes
 * the pipes, then free the transfers, then close the device.
 */
static void free_transfer(usb_transfer_t **transfer)
{
    if (*transfer == NULL) {
        return;
    }
    const esp_err_t err = usb_host_transfer_free(*transfer);
    if (err != ESP_OK) {
        s_free_errors++;
        ESP_LOGW(TAG, "transfer free: %s", esp_err_to_name(err));
    }
    *transfer = NULL;
}

static void release_midi(void)
{
    esp_err_t err = usb_host_interface_release(s_client, s_dev_midi, s_intf_midi);
    if (err != ESP_OK) {
        s_release_errors++;
        ESP_LOGW(TAG, "MIDI interface release: %s", esp_err_to_name(err));
    }
    s_midi_claimed = s_midi_ready = s_midi_has_in = s_midi_has_out = false;
    s_sysex_out_active = 0;
    s_sysex_out_len = 0;

    for (int i = 0; i < MIDI_IN_BUFFERS; i++) {
        free_transfer(&s_midi_in[i]);
    }
    free_transfer(&s_midi_out);

    err = usb_host_device_close(s_client, s_dev_midi);
    if (err != ESP_OK) {
        s_close_errors++;
        ESP_LOGW(TAG, "MIDI device close: %s", esp_err_to_name(err));
    }
    s_dev_midi = NULL;
}

static void release_keyboard(void)
{
    esp_err_t err = usb_host_interface_release(s_client, s_dev_kb, s_intf_kb);
    if (err != ESP_OK) {
        s_release_errors++;
        ESP_LOGW(TAG, "keyboard interface release: %s", esp_err_to_name(err));
    }
    s_kb_claimed = s_kb_ready = s_kb_polling = false;
    s_held_key = 0;
    memset(last_scan, 0, sizeof(last_scan));
    free_transfer(&s_kb_in);
    // So a keyboard plugged back in is asked for boot protocol again rather than
    // being left in whatever report mode it powers up in.
    s_boot_protocol_requested = false;
    err = usb_host_device_close(s_client, s_dev_kb);
    if (err != ESP_OK) {
        s_close_errors++;
        ESP_LOGW(TAG, "keyboard device close: %s", esp_err_to_name(err));
    }
    s_dev_kb = NULL;
}

static void release_mouse(void)
{
    esp_err_t err = usb_host_interface_release(s_client, s_dev_mouse, s_intf_mouse);
    if (err != ESP_OK) {
        s_release_errors++;
        ESP_LOGW(TAG, "mouse interface release: %s", esp_err_to_name(err));
    }
    const bool shared_with_keyboard = (s_dev_mouse == s_dev_kb);
    s_mouse_claimed = s_mouse_ready = s_mouse_polling = false;
    disable_mouse_pointer();
    free_transfer(&s_mouse_in);
    s_boot_protocol_requested = false;
    if (!shared_with_keyboard) {
        err = usb_host_device_close(s_client, s_dev_mouse);
        if (err != ESP_OK) {
            s_close_errors++;
        ESP_LOGW(TAG, "mouse device close: %s", esp_err_to_name(err));
        }
    }
    s_dev_mouse = NULL;
}

static void client_event_callback(const usb_host_client_event_msg_t *event_msg, void *arg)
{
    (void)arg;

    switch (event_msg->event) {
        case USB_HOST_CLIENT_EVENT_NEW_DEV: {
            s_attach_count++;
            // Warning level on purpose: this is the line that says whether a
            // device reached us at all, and it has to survive any log filtering
            // while the port is being brought up.
            ESP_LOGW(TAG, "New device attached at address %d", event_msg->new_dev.address);

            usb_device_handle_t device_handle = NULL;
            esp_err_t err = usb_host_device_open(s_client, event_msg->new_dev.address, &device_handle);
            if (err != ESP_OK) {
                ESP_LOGE(TAG, "device open failed: %s", esp_err_to_name(err));
                return;
            }

            usb_device_info_t dev_info;
            if (usb_host_device_info(device_handle, &dev_info) == ESP_OK) {
                ESP_LOGW(TAG, "  speed %d, mps0 %d, config %d",
                         dev_info.speed, dev_info.bMaxPacketSize0, dev_info.bConfigurationValue);
            }

            const usb_device_desc_t *dev_desc = NULL;
            if (usb_host_get_device_descriptor(device_handle, &dev_desc) == ESP_OK && dev_desc != NULL) {
                ESP_LOGW(TAG, "  VID 0x%04x PID 0x%04x class 0x%02x",
                         dev_desc->idVendor, dev_desc->idProduct, dev_desc->bDeviceClass);
            }

            const usb_config_desc_t *config_desc = NULL;
            err = usb_host_get_active_config_descriptor(device_handle, &config_desc);
            if (err != ESP_OK) {
                ESP_LOGE(TAG, "config descriptor read failed: %s", esp_err_to_name(err));
                usb_host_device_close(s_client, device_handle);
                return;
            }

            new_enumeration_config_fn(config_desc, device_handle);

            // Nothing we handle: give the device straight back, otherwise the
            // host library keeps it open forever and a later replug is ignored.
            if (device_handle != s_dev_midi && device_handle != s_dev_kb &&
                device_handle != s_dev_mouse) {
                s_unclaimed_count++;
                ESP_LOGW(TAG, "  no MIDI or HID boot interface here, releasing it");
                usb_host_device_close(s_client, device_handle);
            }
            break;
        }

        case USB_HOST_CLIENT_EVENT_DEV_GONE: {
            const usb_device_handle_t gone = event_msg->dev_gone.dev_hdl;
            s_detach_count++;
            ESP_LOGW(TAG, "Device gone: %p", gone);
            // Mouse first: on a combo device it shares the keyboard's handle and
            // must be released before that handle is closed.
            if (s_mouse_claimed && s_dev_mouse == gone) { release_mouse(); }
            if (s_midi_claimed && s_dev_midi == gone) { release_midi(); }
            if (s_kb_claimed && s_dev_kb == gone) { release_keyboard(); }

            // Real activity on the port. The window itself is restarted by the
            // next connect edge; this just makes sure the budget is not still
            // drained from whatever happened to the device that just left.
            s_enum_retries_left = TAB5_USB_ENUM_RETRIES;
            break;
        }

        default:
            ESP_LOGW(TAG, "Unhandled USB client event %d", event_msg->event);
            break;
    }
}

static bool usbh_setup(void)
{
    const usb_host_client_config_t client_config = {
        .is_synchronous = false,
        .max_num_event_msg = 5,
        .async = {
            .client_event_callback = client_event_callback,
            .callback_arg = NULL,
        },
    };
    // Registered from the task that will call usb_host_client_handle_events().
    const esp_err_t err = usb_host_client_register(&client_config, &s_client);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "usb_host_client_register failed: %s", esp_err_to_name(err));
        return false;
    }

    // Only now is it safe to power the connector: everything downstream of the
    // connect edge -- enumeration, then the NEW_DEV event -- has somewhere to go.
    // Serve out whatever is left of the off period first; running it here rather
    // than in tab5_board_startup() keeps it off the boot path, because the rest
    // of the board carries on initialising while this task waits.
    const int64_t off_for_ms = (esp_timer_get_time() / 1000) - s_vbus_off_since;
    if (off_for_ms < TAB5_USB_VBUS_OFF_MS) {
        vTaskDelay(pdMS_TO_TICKS(TAB5_USB_VBUS_OFF_MS - off_for_ms));
    }
    tab5_power_set_usb_host(true);
    s_vbus_on_at_ms = esp_timer_get_time() / 1000;
    s_enum_retries_left = TAB5_USB_ENUM_RETRIES;

    ESP_LOGI(TAG, "USB host client registered, USB-A connector powered");
    return true;
}

// Give the port another go. A device that is plainly connected at the root port
// but has produced no NEW_DEV event within the verdict window lost its
// enumeration, and the host library does not retry one: usb_host.c maps
// ENUM_EVENT_CANCELED straight to hub_node_disable(), so the device sits there
// disabled until VBUS is cycled. Nobody else will do that, so we do.
//
// The connect bit is what makes this safe to arm. Without it "nothing
// enumerated" also matches an empty connector, and this ran its whole budget of
// VBUS blips on a port with nothing in it -- pointless I2C traffic to the load
// switch during boot, exactly when the rest of the board is still starting up.
// The budget is bounded, and refilled by a connect that we did not cause.
static void retry_enumeration_if_stuck(int64_t now_ms)
{
    tab5_usb_port_state_t port;
    tab5_usb_port_state(&port);

    if (!port.connected) {
        s_port_was_connected = false;
        return;  // empty connector: nothing to rescue, and blipping it costs us
    }
    if (!s_port_was_connected) {
        // Rising edge: start this device's window here. A device that we just
        // power-cycled keeps the budget it had, or the two would feed each other.
        s_port_was_connected = true;
        s_port_connected_at_ms = now_ms;
        s_attach_count_at_connect = s_attach_count;
        if (s_reconnect_is_ours) {
            s_reconnect_is_ours = false;
        } else {
            s_enum_retries_left = TAB5_USB_ENUM_RETRIES;
        }
        return;
    }
    if (s_enum_retries_left <= 0 || s_attach_count != s_attach_count_at_connect) {
        return;
    }
    if ((now_ms - s_port_connected_at_ms) < TAB5_USB_ENUM_TIMEOUT_MS) {
        return;
    }

    s_enum_retries_left--;
    s_enum_retry_count++;
    ESP_LOGW(TAG, "device connected but not enumerated in %d ms, cycling VBUS "
             "(%d attempt%s left)", TAB5_USB_ENUM_TIMEOUT_MS, s_enum_retries_left,
             s_enum_retries_left == 1 ? "" : "s");

    s_reconnect_is_ours = true;
    s_port_was_connected = false;  // the reconnect restarts the window
    tab5_power_set_usb_host(false);
    vTaskDelay(pdMS_TO_TICKS(TAB5_USB_VBUS_OFF_MS));
    tab5_power_set_usb_host(true);
    s_vbus_on_at_ms = esp_timer_get_time() / 1000;
}

static void usbh_poll_events(void)
{
    uint32_t event_flags = 0;
    esp_err_t err = usb_host_lib_handle_events(HOST_EVENT_TIMEOUT, &event_flags);
    if (err != ESP_OK && err != ESP_ERR_TIMEOUT) {
        ESP_LOGW(TAG, "usb_host_lib_handle_events: %s", esp_err_to_name(err));
    }

    err = usb_host_client_handle_events(s_client, CLIENT_EVENT_TIMEOUT);
    if (err != ESP_OK && err != ESP_ERR_TIMEOUT) {
        ESP_LOGW(TAG, "usb_host_client_handle_events: %s", esp_err_to_name(err));
    }
}

static void run_tab5_usb(void *params)
{
    (void)params;

    if (!usbh_setup()) {
        vTaskDelete(NULL);
        return;
    }

    for (;;) {
        usbh_poll_events();

        const int64_t now_ms = esp_timer_get_time() / 1000;

        retry_enumeration_if_stuck(now_ms);

        // Auto-repeat. The HID keyboard keeps reporting the same scan code while
        // held, but decode_keyboard_report() only emits a key on the first one,
        // so the repeat has to come from here.
        if (s_held_key != 0 && (now_ms - s_held_since_ms) > KEY_REPEAT_TRIGGER_MS &&
            (now_ms - s_last_repeat_ms) > KEY_REPEAT_INTER_MS) {
            tab5_keyboard_push_key(s_held_key);
            s_last_repeat_ms = now_ms;
        }

        if (s_kb_ready && !s_kb_polling) {
            s_kb_in->num_bytes = s_kb_bytes;
            const esp_err_t err = usb_host_transfer_submit(s_kb_in);
            if (err == ESP_OK) {
                s_kb_polling = true;
            } else {
                ESP_LOGW(TAG, "keyboard poll submit failed: %s", esp_err_to_name(err));
            }
        }

        if (s_mouse_ready && !s_mouse_polling) {
            s_mouse_in->num_bytes = s_mouse_bytes;
            const esp_err_t err = usb_host_transfer_submit(s_mouse_in);
            if (err == ESP_OK) {
                s_mouse_polling = true;
            } else {
                ESP_LOGW(TAG, "mouse poll submit failed: %s", esp_err_to_name(err));
            }
        }
    }
}

void tab5_usb_host_start(void)
{
    if (s_midi_out_done == NULL) {
        s_midi_out_done = xSemaphoreCreateBinary();
        if (s_midi_out_done == NULL) {
            ESP_LOGE(TAG, "Failed to create MIDI out semaphore");
            return;
        }
    }

    /*
     * Order matters here, and getting it wrong is invisible until you cold-boot
     * with a device already plugged in.
     *
     * A USB device announces itself once, by pulling D+ up when VBUS arrives. If
     * the connector is already powered when usb_host_install() runs, a device
     * that was plugged in before boot has already made that announcement into a
     * port that was not listening, and nothing ever repeats it -- the device
     * sits there unenumerated and, having never been configured, does not even
     * light its power LED. Hot-plugging worked, which is what made this look
     * intermittent rather than ordered.
     *
     * So: hold the connector off, bring the host up, then switch VBUS on (in
     * usbh_setup(), once the client is registered). Every device -- already
     * attached or not -- then gets a fresh connect edge into a port that is
     * watching.
     *
     * How long VBUS stays off matters, and more than the USB spec suggests.
     * Measured on a KORG microKEY-37, which has enough bulk capacitance to ride
     * out a short gap and come back up half-initialised, failing its first EP0
     * control transfer ("USBH: Dev 0 EP 0 Error"):
     *
     *     130 ms off -> fails      300 ms off -> fails      1000 ms off -> works
     *
     * TAB5_USB_VBUS_OFF_MS is measured from here, and usb_host_install() runs
     * inside it, so most of the wait is work we had to do anyway.
     */
    tab5_power_set_usb_host(false);
    s_vbus_off_since = esp_timer_get_time() / 1000;

    // Installed here rather than in the task, the way bsp_usb_host_start() does
    // it: the host library brings up the PHY and the root port synchronously,
    // and doing that before the event task exists keeps the ordering obvious.
    const usb_host_config_t config = {
        .skip_phy_setup = false,
        .intr_flags = ESP_INTR_FLAG_LEVEL1,
    };
    const esp_err_t err = usb_host_install(&config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "usb_host_install failed: %s", esp_err_to_name(err));
        tab5_power_set_usb_host(true); // leave the connector usable regardless
        return;
    }
    ESP_LOGI(TAG, "USB host installed on the USB-A connector");
    // VBUS goes on in usbh_setup(), once the client is registered -- a client
    // that registers after a device has already enumerated does not get a
    // retroactive NEW_DEV event.

    if (xTaskCreatePinnedToCore(run_tab5_usb, TAB5_USB_TASK_NAME,
                                TAB5_USB_TASK_STACK_WORDS, NULL,
                                TAB5_USB_TASK_PRIORITY, NULL,
                                TAB5_USB_TASK_CORE) != pdPASS) {
        ESP_LOGE(TAG, "Failed to create USB task");
        tab5_power_set_usb_host(true); // at least keep the connector powered
    }
}

bool tab5_usb_midi_connected(void) { return s_midi_ready; }
bool tab5_usb_keyboard_connected(void) { return s_kb_ready; }
bool tab5_usb_mouse_connected(void) { return s_mouse_ready; }

uint32_t tab5_usb_attach_count(void) { return s_attach_count; }
uint32_t tab5_usb_detach_count(void) { return s_detach_count; }
uint32_t tab5_usb_unclaimed_count(void) { return s_unclaimed_count; }
uint32_t tab5_usb_release_errors(void) { return s_release_errors; }
uint32_t tab5_usb_close_errors(void) { return s_close_errors; }
uint32_t tab5_usb_free_errors(void) { return s_free_errors; }
uint32_t tab5_usb_enum_retries(void) { return s_enum_retry_count; }

void tab5_usb_port_state(tab5_usb_port_state_t *state)
{
    // Reading HPRT is only meaningful once the host driver owns the peripheral;
    // before that the register block is unclocked and would fault.
    if (s_client == NULL) {
        *state = (tab5_usb_port_state_t){ 0 };
        return;
    }
    const usb_dwc_hprt_reg_t hprt = { .val = USB_DWC_HS.hprt_reg.val };
    state->installed = true;
    state->connected = hprt.prtconnsts;
    state->enabled = hprt.prtena;
    state->overcurrent = hprt.prtovrcurract;
    state->powered = hprt.prtpwr;
    state->line_state = hprt.prtlnsts;
    state->speed = hprt.prtspd;
}
