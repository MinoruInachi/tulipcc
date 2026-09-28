Rolling **AMYStickS3** build: the AMYboard firmware for the **M5Stack StickS3**
(ESP32-S3, ES8311 codec, built-in speaker and mic, 240×135 LCD), rebuilt on
every push to `dev_tab5`. Not a tagged version.

Unofficial port on a fork; not built or supported by
[shorepine/tulipcc](https://github.com/shorepine/tulipcc).

## Assets

| File | Flash at | Notes |
|---|---|---|
| `amysticks3-full.bin` | `0x0` | Everything: bootloader, partition table, app, the Gamma9001 drum banks, `/sys` and `/user`. Erases the whole flash, sketches included. |
| `amysticks3-firmware.bin` | `0x10000` | App only; keeps `/user` (your sketches) and NVS. |
| `amysticks3-full-@DATE_CODE@.bin` | `0x0` | Date-coded copy of the full image. |

Flashing requires [esptool](https://github.com/espressif/esptool)
(`pip install esptool`). Put the StickS3 into download mode first: with USB
connected, hold the side button until the internal green LED blinks. Then:

```
esptool.py --chip esp32s3 --port /dev/cu.usbmodemXXXX --baud 921600 \
  --before no_reset write_flash 0x0 amysticks3-full.bin
```

On esptool v5 the subcommand is spelled `write-flash`. When it finishes,
single-click the side button to boot. The first install needs the full image,
since the flash layout differs from the StickS3's factory firmware (UiFlow2,
which M5Stack's tools can reinstall).

## Using it

It is AMYboard firmware: it shows up over USB as **AMYboard** (serial REPL +
USB MIDI), so the [AMYboard web editor](https://amyboard.com) finds it, and
sketches, patches and `import amyboard` work as on an AMYboard.
`amyboard.sticks3()` is `True` on this board. `amyboard.display` is the LCD,
240×135 landscape: hold the stick with the front button (KEY1) on the right.
Hold KEY1 while booting to skip the sketch.

## Known limitations

- **No CV in/out, SD card or I2C follower**: the StickS3 has no such hardware.
- **DIN MIDI** goes out the Grove port (G9 out, G10 in) and needs an external
  MIDI adapter; not tested on hardware yet.
- **No OTA**: one factory app, so the drum banks fit in the 8MB flash. Update by
  flashing `amysticks3-firmware.bin`; `amyboard.upgrade()` does not apply.
- **Flash via download mode.** Resetting into the bootloader through the USB
  serial's DTR/RTS lines (what esptool and web flashers do on their own) hangs
  the running firmware, so use the side button as above.
- **A faint high-pitched whine** from the speaker on some units while the amp
  is on. It comes up at random each time the amp is enabled, and M5Stack
  documents it for early StickS3 batches.
- `/sys` leaves out two of AMYboard's large example samples (`vlsa3.wav`,
  `vlng3.wav`) to fit.
- Built against forked `amy` and `micropython` branches, not their upstreams.

## Build provenance

| | |
|---|---|
| Commit | `@COMMIT@` |
| Branch | `dev_tab5` |
| Build | [run @RUN_ID@](@RUN_URL@) |
| ESP-IDF | `v5.5.4` |
| Board | `STICKS3` (`idf.py -DMICROPY_BOARD=STICKS3 build` in `tulip/amyboard`) |
| `amy` | `@AMY_SHA@` |
| `micropython` | `@MPY_SHA@` |
