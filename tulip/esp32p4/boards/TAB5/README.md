# M5Stack Tab5 board note

This directory is the intended home for the first Tulip ESP32-P4 board port.

## Hardware baseline

- SoC: ESP32-P4NRW32
- Wireless module: ESP32-C6-MINI-1U
- Display: 5-inch 1280×720 MIPI-DSI panel
- Touch: GT911 on the early public BSP path; ST7123/ST7121 on newer product revisions
- Audio: ES8388 codec and ES7210 AEC front end

## Control and power signals

- `LEDA` on GPIO 22 for panel power/backlight
- `LCD_RST` and `TP_RST` through the PI4IOE5V6408 IO expander
- `TP_INT` from the touch controller
- `EXT5V_EN` for rear expansion and side port power
- `WLAN_PWR_EN` for the ESP32-C6 module rail
- `USB5V_EN` for USB-A host power
- `PWROFF_PLUSE` for power-off control

## Expected software split

- Keep the display backend revision-aware so Tab5 units with different panel stacks can share the same Tulip graphics API.
- Keep touch init separate from display init so the controller can be swapped without changing higher-level Tulip input code.
- Keep audio and storage setup behind board-specific startup helpers.

## Next implementation files

- `mpconfigboard.cmake`
- `mpconfigboard.h`
- `boards/manifest.py`
- board-specific display bring-up source
- board-specific touch bring-up source
- board-specific power-control or IO-expander helper

## Notes

This is documentation for the first scaffolded Tab5 port. The display helper now streams Tulip frames to the official M5Stack Tab5 panel path, and the ES8388 speaker path renders the in-process AMY engine at 44.1 kHz stereo. Storage and power helpers are still being refined. The file names and startup order are fixed now so the remaining ESP32-P4 work can land behind them without reshaping the tree.

## Audio policy (current)

- `audio_tab5.c` owns the BSP I2S and ES8388 speaker setup.
- AMY runs in-process with its event and synth allocations in PSRAM and a dedicated render task feeding `esp_codec_dev_write()`.
- `_boot.py` connects the Python `amy` package to native `tulip.amy_send`, initializes the default MIDI synths, and enables the Voices patch selector and touch piano.
- LVGL uses a Tab5 allocator that prefers internal RAM and falls back to PSRAM, avoiding the default 64 KiB LVGL heap limit while keeping full-screen rendering responsive.

## Storage policy (current)

- Filesystem ownership is currently fixed to MicroPython VFS in this port.
- `storage_tab5.c` intentionally does not call `bsp_spiffs_mount()` or `bsp_sdcard_mount()` during bring-up.
- Reason: BSP mount helpers pull ESP-IDF FATFS symbols that conflict with MicroPython `lib/oofatfs` (`ff.c` duplicate symbols at link).
- Any future BSP storage re-enable must first move the port to a single filesystem authority.

## Touch and revision policy (current)

- `touch_tab5.c` now owns a real polling loop through `bsp_touch_new()` and `esp_lcd_touch_get_coordinates()`.
- The board layer probes touch controller address at boot and caches the result:
	- `0x14` (GT911) -> Tab5 revision v1 path
	- `0x55` (ST7123) -> Tab5 revision v2 path
- Touch events are always routed through `send_touch_to_micropython(...)`.
- On esp32p4 today, a board-local weak fallback bridge logs dropped events until the shared Tulip UI bridge is linked.

## Display provider policy (current)

- The Tab5 display task no longer force-links the scaffold shared-provider shim.
- Real shared provider symbols (`display_bounce_empty`, `display_frame_done_generic`) are adopted only when actually linked.
- For esp32p4 bring-up, `shared_provider_tab5.c` currently provides a temporary implementation of these symbols (1280x720 source) so the rotated shared-provider path is exercised end-to-end.
- This temporary provider is a transition step; it will be replaced by the actual Tulip shared renderer integration.

## Tulip API status

The Tab5 native module currently exposes the common display APIs used by
`docs/tulip_api.md`: BG pixels, bitmaps, PNGs, drawing primitives, scrolling,
RGB332 color conversion, screenshots, TFB access, brightness, frame/touch
callbacks, and sprite PNG/bitmap/register/move/visibility/collision operations.
These paths use the shared Tulip renderer and are tested on hardware through
`mpremote ... resume` so attaching does not soft-reset the running board.
The module also exposes `amy_send`, `amy_ticks_ms`, and `midi_callback` for the
in-process AMY engine and Python MIDI routing.

The following APIs are intentionally not exposed yet:

- `keys`, `key_wait`, `key`, `key_scan`, and `key_remap`: the Tab5 keyboard's
	character-event mode reports characters but not HID release events or held
	scan-code state. `keyboard_callback` remains available.
- `cpu`: the current ESP32-P4 FreeRTOS build does not enable runtime task stats.
- `display_clock`, `display_start`, `display_stop`, and `display_restart`: the
	MIPI display task does not yet support safe runtime teardown and recreation.
- Hardware MIDI APIs: no Tab5 MIDI transport backend is integrated yet.