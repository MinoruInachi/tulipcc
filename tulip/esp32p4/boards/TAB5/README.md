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
- The built-in microSD slot **does** work, via MicroPython's `machine.SDCard`
  block device (not the BSP FAT mount, which is what causes the `ff.c` clash
  above). `tulip.sd_mount()` / `sd_unmount()` / `sd_mounted()` / `sd_info()`
  (Python wrappers in `tulip/shared/py/tulip.py`) bring it up at `/sd`. Pins are
  CLK=43, CMD=44, D0-3=39/40/41/42; the card's I/O rail is the P4 on-chip LDO
  channel 4 (`ldo=4` is mandatory -- omitting it resets the board; the display
  MIPI-DSI PHY uses LDO ch3, so no conflict). Verified on the v2 board with a
  32 GB FAT32 card: 1-bit and 4-bit, mount + read/write + statvfs, no reset.
- The partition table is `esp32p4/partitions-8MiBplus-ota.csv`: two 4.5 MiB
  app slots (`ota_0` at 0x10000, `ota_1` at 0x490000), a 3 MiB `system`
  (/sys) at 0x910000 and a 3.94 MiB `vfs` (/user) at 0xc10000. The slots grew
  from 0x3f0000 in September 2026, when the camera stack left 72 KB of app
  flash; `vfs` paid for it. A device on the old table has to be flashed whole
  and its /user put back by hand, since the old littlefs sits at an offset the
  new table does not know.

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
- `tulip.tab5_render_stats()` -> per-frame render phase timings: composite,
  convert, rotate, present and vsync-wait microseconds, frames skipped, band
  rows, PPA failures, DSI frame buffer count, PPA active, vsync paced, PPA
  timeouts, the phase the display task is in right now (0 waiting for
  vsync, 1 compositing, 2 rotating, 3 presenting, 4 frame-done hook), the
  last PPA error code, the PPA client recoveries, the band (y, rows) of the
  first rotation that timed out, and the band of the latest one. If the
  screen has stopped, the phase says which step it never came back from; a
  PPA timeout count above zero says the 2D-DMA rotation stalled (see "The
  PPA stall" under Camera).
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

## Display colour (current)

The BG plane, the TFB plane, the sprites and LVGL's overlay are all native
RGB565 here (`TULIP_RGB565` in `shared/display.h`; every other board keeps
RGB332). The compositor hands the bridge finished 16-bit rows, so the
332-to-565 lookup the bridge used to run per pixel is gone, and so is the
64 KB table the camera used to drop its RGB565 frames to the palette --
`camera_bg()` is a row copy now. LVGL's antialiased fonts no longer fringe
blue, because its RGB565 goes into the overlay untouched instead of being
rounded to the nearest of 256 colours.

The Python API is unchanged for palette programs: every colour argument still
takes the 0-255 index, expanded by replicating its bits down (the same palette
Tulip Desktop shows; `ui.pal_to_lv()` does the same sum for LVGL here), so
palette entry 0x55 is still the transparent colour (`ALPHA`, 0x4daa as a
pixel). Colour arguments also take an `(r, g, b)` tuple; `bg_pixel_rgb()`
reads a pixel exactly; `bg_bitmap()` / `sprite_bitmap()` bytes are two a pixel;
`screenshot()` writes an RGB PNG. See "Tab5" under BG in `docs/tulip_api.md`.

Cost: the BG plane is 2.4 MB of PSRAM instead of 1.2, the TFB plane 1.8 instead
of 0.9, the LVGL overlay 2.4 instead of 1.2, and sprite RAM 64 KB of internal
RAM instead of 32 (its 32768 entries are pixels, so Python's sprite accounting
is unchanged).

## Camera (current)

The back camera is an SC2356 on the P4's MIPI-CSI port; it answers to the
SC202CS driver in `esp_cam_sensor` (chip id 0xeb52), which is why
`sdkconfig.board` turns that sensor on. `camera_tab5.c` brings it up through
the BSP (`bsp_camera_start()`: sensor power via the IO expander, SCCB on the
BSP's I2C bus, `esp_video_init()` with the ISP pipeline controller doing auto
exposure and white balance) and reads it as a V4L2 device: RAW8 1280x720 at
30 fps from the sensor, RGB565 out of the ISP, three MMAP buffers (5.5 MB of
PSRAM while streaming, given back on stop).

A task on core 0 owns the stream and keeps the newest frame dequeued, so the
Python calls never block on the sensor. It also owns every step that
allocates an interrupt -- `esp_video_init()`, the V4L2 setup, the JPEG
engine -- because interrupts are serviced on the core that allocated them,
and with the CSI/ISP/JPEG interrupts on the MicroPython core a
`camera_capture()` to `/user` deadlocked against the littlefs flash write
(interrupt watchdog on core 1, spinning in `shared_intr_isr()`).

The Python API (`tulip.camera_*`, see `docs/tulip_api.md`, Tab5 only):
`camera_start()`, `camera_stop()`, `camera_running()`, `camera_info()`,
`camera_wait()`, `camera_frame()` (RGB565, the same layout as LVGL's 16-bit
colour here, so it drops into an `lv.image_dsc_t`), `camera_bg()` (the
frame copied onto the RGB565 BG plane, the cheap live preview),
`camera_capture()` (JPEG through the P4's hardware encoder, or PNG through
lodepng), `camera_flip()` and `camera_test_pattern()` (the sensor's black-to-white
ramp, for checking the path without a scene; its colour follows the ISP's
white balance, so read it for orientation and byte order, not tint).

Measured on the v2 board: start 130 ms, 30 fps sustained while the display
runs, `camera_bg()` 100 ms full screen / 40 ms at 640x360, `camera_frame()`
70 ms into a caller-supplied buffer, JPEG 140 ms in memory (plus the littlefs
write, 1-1.5 s for a 150-200 KB file), PNG about 17 s. Twenty JPEG captures
to `/user` in a row and five stop/start cycles leak nothing. A reader that
keeps the newest frame for longer than half a frame time (a full-screen
`camera_bg()` does) has the frames that arrive meanwhile handed straight
back to the driver; `camera_info()['dropped']` counts them and `fps` counts
the frames accepted.

`camera_info()` also reports `gain`, `exposure` (sensor, via V4L2_CID_GAIN /
V4L2_CID_EXPOSURE) and `red_balance`, `blue_balance` (ISP, x1000) so you can see
whether the ISP pipeline controller (`isp_task`, AE/AWB from
`sc202cs_default.json`) is doing anything. On both boards it settles within
2 s to roughly gain 129, exposure 1125, red 1.6-1.8, blue 1.55 for an indoor
scene. The sensor defaults are 10 / 750 / 1.000 / 1.000; the one time they
stayed there (first bring-up on the v1 board after a cold start) the picture
was dark and green and `camera_stop()`/`camera_start()` did not recover it,
only a reset did. Not reproduced in nine warm restarts (an RTS reset leaves
the camera power on through the IO expander), so a power-cycled start is the
case still to watch.

Verified on both boards: v1 (ILI9881C, P4 rev v1.0) and v2 (ST7121, P4 rev
v1.3). Same API results, same ~9 fps in the camera app, and no PPA timeouts
in a 3-minute run on v1.

### The PPA stall

With the camera app (`/sys/ex/camera.py`) running for a few minutes, one of
the display's PPA rotations -- the 2D-DMA scale-rotate-mirror transaction
that turns the landscape staging image into the portrait panel framebuffer
-- never completes. Before this was understood, `ppa_do_scale_rotate_mirror()`
was called in blocking mode, which waits on the DMA2D completion forever, so
the display task parked inside the driver: the screen froze while the REPL,
touch, audio and the camera all kept running, and nothing could report why.

What is known, from runs of 10-15 minutes each: it needs the camera
streaming *and* Python reading frames (`camera_bg()`); the same PPA load
without the camera, or with the camera streaming but the band drawn from a
static bitmap, never stalled. It is not the chunked cache msync option, not
the driver running out of buffers (a non-blocking capture task with four
buffers stalls the same way), and not a particular band geometry -- a run
that logged every band's offset and height stalled on the same shapes it had
rotated thousands of times, and an identical run went 15 minutes without.
Once stalled, the SRM engine is wedged: a fresh PPA client's transactions
queue behind the stuck one and time out too.

So the display task defends itself instead (`tab5_ppa_rotate_to_fb()`):
non-blocking transactions with a completion callback and a 250 ms wait; on
a timeout it abandons the client (the driver refuses to unregister one with
a pending transaction) and opens another, up to three times, then stays on
the CPU rotation -- band-limited and block-transposed, about 80 ms for a
full-screen band instead of the 40 ms the PPA takes, and a frame that runs
long yields a tick so IDLE0 keeps the task watchdog fed. The screen slows
down; it no longer stops. `tulip.tab5_render_stats()` shows the phase, the
timeout and recovery counts, and the band of the first rotation that stalled.

## Microphone (dual mic)

The two built-in mics reach the P4 through the ES7210 ADC, which shares the same
full-duplex I2S controller as the ES8388 speaker. `bsp_audio_init()` (from
`tab5_audio_init()`) already brings up both the TX and RX halves of that
controller at `AMY_SAMPLE_RATE` / 16-bit / stereo for AMY playback, so the mic
path is clocked off the very same BCLK/WS. That fixes the capture format: the
rate is `AMY_SAMPLE_RATE` (44.1 kHz) and the two mics are the two 16-bit I2S
slots of every read. Reconfiguring the clock to record at some other rate would
pull it out from under the speaker mid-block, so software decimates a copy
instead if it wants a lower rate.

`mic_tab5.c` programs the ES7210 lazily -- only on the first `mic_start()`, so
the shared bus is untouched until a caller actually asks to record -- opens the
codec (`bsp_audio_codec_microphone_init()` + `esp_codec_dev_open()`) and runs a
capture task on core 0, beside the display, touch and camera tasks. The task
blocks in `esp_codec_dev_read()` and pushes each 256-frame block into a PSRAM
ring (half a second, drop-oldest with an overrun counter) under a lock; a reader
on the MicroPython task copies out of the ring and never waits on the ADC.

The Python API (`tulip.mic_*`, see `docs/tulip_api.md`, Tab5 only):
`mic_start(gain=)`, `mic_stop()`, `mic_running()`, `mic_info()`, `mic_read()`
(interleaved L,R int16 bytes), `mic_level()` (per-mic peak for a meter),
`mic_gain()` and `mic_record()` (blocks for N seconds and returns raw PCM, or
writes a WAV -- stereo, or `mono=True` to downmix).

Verified on the v2 board (ST7123, P4 rev v1.3): both mics capture (peak/RMS
comparable on left and right), `mic_read()` and `mic_record()` return the
expected byte counts, the WAV header round-trips (44.1 kHz / 16-bit / 2 ch), and
recording runs concurrently with AMY playback. Playback is unaffected by opening
the mic.

## Motion sensor (IMU)

The board carries a Bosch BMI270 6-axis IMU (accelerometer + gyroscope) on the
shared I2C bus at 0x68. `imu_tab5.c` reads it through the standalone
`espressif/bmi270` driver -- not the BSP's `bsp_sensor_init()` /
`iot_sensor_hub` path, which needs an event loop and its own acquisition task.
The sensor is brought up lazily on the first read (`bmi270_create()` +
`bmi270_start()` at accel/gyro 100 Hz, +/-4 g / +/-2000 dps) using
`bsp_i2c_get_handle()`, then polled on demand from the MicroPython task, so
nothing runs unless a caller asks for a sample. The i2c_master bus serialises
access, so sharing it with touch and the keyboard is safe. `bmi270`'s enable
sequence returns all-zero for the first few reads, so `imu_tab5.c` polls (up to
~250 ms, once, at init) for a real sample before returning, so the caller's very
first read already reflects the sensor.

The Python API (`docs/tulip_api.md`, Tab5 only) is a single call:
`tulip.imu()` returns `(ax, ay, az, gx, gy, gz)` -- accel in g, gyro in
degrees/second -- and raises `RuntimeError` if the sensor can't be reached. The
`espressif__bmi270` component (a BSP dependency) is named in `main/CMakeLists.txt`
`IDF_COMPONENTS` so `main` links against it.

Verified on the v2 board (ST7123, P4 rev v1.3): at rest `sqrt(ax^2+ay^2+az^2)`
is ~1.0 g and the gyro is near zero; the first read after boot already returns
real data.

## Tulip API status

The Tab5 native module currently exposes the common display APIs used by
`docs/tulip_api.md`: BG pixels, bitmaps, PNGs, drawing primitives, scrolling,
palette and (r, g, b) colours, screenshots, TFB access, brightness, frame/touch
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
