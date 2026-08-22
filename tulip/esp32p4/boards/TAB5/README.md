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

- `touch_tab5.c` owns a real polling loop through `esp_lcd_touch_get_coordinates()`.
- `tab5_board_startup()` settles the board revision once, before the display and
  touch tasks exist, and both then read the cached answer. Doing it there keeps
  the wait out of the three seconds `_boot.py` gives the display to come up.
- The probe reads the touch controller's firmware version register rather than
  probing its address: a cold ST712x never acks the zero-length write
  `i2c_master_probe()` sends, and treating that as "nothing there" is what left
  a v2 board on the wrong panel.
- It also releases **both** IO-expander reset lines first (P4 `LCD_RST` and P5
  `TP_RST`, which the BSP names `BSP_LCD_EN` / `BSP_TOUCH_EN`). On an ST712x
  those land on one die -- touch and display are the same chip -- so releasing
  only the touch half leaves the controller in reset and silent. This is only
  visible from a cold start: the expander keeps its outputs across a chip reset,
  so a warm reboot finds the line already released.
- Three revisions, all decided by what answers on I2C:
	- `0x14` (GT911) -> v1: ILI9881C display, GT911 touch, BSP display path
	- `0x55` + touch firmware version 3 -> v2 ST7123: BSP display path
	- `0x55` + touch firmware version 1 -> v2 ST7121: local display path
- The ST7121 arrived in Tab5 units built from 2026-04-28 and the BSP has no path
  for it (1.2.0~1 knows only the ST7123 it replaced), so `display_tab5.c` brings
  that panel up itself through `espressif/esp_lcd_st7121`: 965Mbps lanes rather
  than the ST7123's, and its own vertical blanking. The failure it fixes is
  silent -- an ST7123 unlock command carries that part number, an ST7121 ignores
  it, and the panel ends up lit and blank with the DSI refreshing happily.
- Touch is the same driver for both ST712x panels.
- The ST7123 path still goes through the BSP, with two corrections taken from
  M5's own firmware and **not yet tried on hardware** (no ST7123 unit here):
  965Mbps lanes rather than the BSP's 1000, and a 120ms settle after SLPOUT
  before the display is turned on -- the BSP's table asks for none where M5's
  copy of the same table waits 100ms.
- Touch events are always routed through `send_touch_to_micropython(...)`.
- On esp32p4 today, a board-local weak fallback bridge logs dropped events until the shared Tulip UI bridge is linked.

## Reading the board from the REPL

Log output from the display and touch tasks does not reliably survive the
USB-Serial-JTAG console once MicroPython owns it, so the state that matters is
readable instead:

- `tulip.tab5_diag()` -> display task entries, bridge frames, touch task
  entries, touch polls, touch downs, touch read errors, frame callbacks, LVGL
  handlers, touch init error, touch init attempts, revision probe ms, vsync
  count. A touch controller that never initialized reads exactly like a screen
  nobody touched without those last four.
- `tulip.tab5_render_stats()` -> per-frame render phase timings.
- `tulip.tab5_lcd_errors()` -> what display start-up returned.
- `tulip.tab5_lcd_cmd(cmd, data)` / `tulip.tab5_lcd_read(cmd, len)` -> raw DCS
  to the panel. **These disturb the running video stream**: a read is enough to
  blank a working screen until the next reboot. They are for a panel that is
  already misbehaving, not for a healthy one.

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

## ulab (numpy / scipy)

The firmware links [ulab](https://github.com/v923z/micropython-ulab) as a
MicroPython user C module, so `numpy`- and `scipy`-style array maths are
available on the board:

```python
from ulab import numpy as np
from ulab import scipy as sp

x = np.sin(np.linspace(0, 2 * 3.14159, num=256, endpoint=False))
X = np.fft.fft(x)              # complex ndarray -- this build has complex on
np.linalg.inv(np.array([[4, 1], [1, 3]], dtype=np.float))
sp.optimize.newton(lambda v: v * v - 2.0, 1.0, tol=1e-6, rtol=1e-6)
```

`ulab` lives in the `ulab` submodule at the repo root and is wired in by
`mpconfigboard.cmake`, which points `USER_C_MODULES` at
`ulab/code/micropython.cmake`. That is a default, not a hard-code:
`idf.py -DUSER_C_MODULES=... build` still wins, and the build falls back to no
user modules when the submodule is not checked out. It costs about 150 KB of
app flash.

ulab's own defaults apply (`ULAB_MAX_DIMS` 2, complex on, scipy on); override
them with `add_compile_definitions()` in `mpconfigboard.cmake` if a sketch
needs more dimensions or a smaller build. Note ulab's numpy is a subset --
there is no `np.abs`, for instance.

## matplotlib

A small `matplotlib.pyplot` is frozen into the firmware and draws through the
`tulip.bg_*` primitives, so it pairs directly with ulab -- ulab does the
arrays, matplotlib draws them:

```python
from ulab import numpy as np
import matplotlib.pyplot as plt

x = np.linspace(0, 4 * np.pi, 300)
plt.plot(x, np.sin(x), label='sin')
plt.plot(x, np.cos(x), 'r--', label='cos')
plt.grid(True); plt.legend()
plt.full_screen(True)     # take the whole screen; full_screen(False) gives it back
plt.show()
```

`tulip.run('plotdemo')` is a four-page tour. Full reference, including the
documented deviations from upstream matplotlib, is in
[docs/matplotlib.md](../../../../docs/matplotlib.md). The package lives in
`tulip/shared/py/matplotlib/` and is named in this board's
`boards/manifest.py`, which -- unlike the other ports -- lists its frozen
files individually. It costs about 42 KB of app flash.
