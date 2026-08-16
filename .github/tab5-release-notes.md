Rolling Tulip build for the **M5Stack Tab5** (ESP32-P4, 1280×720 MIPI-DSI),
rebuilt on every push to `dev_tab5`. Not a tagged version — for a snapshot with
full notes, see the tagged releases.

Unofficial port on a fork; not built or supported by
[shorepine/tulipcc](https://github.com/shorepine/tulipcc).

## Assets

| File | Flash at | Notes |
|---|---|---|
| `tulip-full-TAB5.bin` | `0x0` | Everything: bootloader, partition table, otadata, app, `/sys` and `/user`. Erases the whole flash. |
| `tulip-firmware-TAB5.bin` | `0x10000` | App only; keeps `/user` and NVS. |
| `tulip-sys.bin` | — | The `/sys` littlefs image, fetched by `tulip.upgrade()`. |
| `tulip-full-TAB5-@DATE_CODE@.bin` | `0x0` | Date-coded copy of the full image. |

Flashing requires [esptool](https://github.com/espressif/esptool)
(`pip install esptool`):

```
esptool.py --chip esp32p4 --port /dev/cu.usbmodemXXXX --baud 921600 \
  write_flash 0x0 tulip-full-TAB5.bin
```

On esptool v5 the subcommand is spelled `write-flash`. If the port does not
enumerate, put the Tab5 into download mode first.

Coming from a build made before `/sys` existed needs the full image once: the
flash layout changed, and anything kept on the old whole-flash filesystem is
lost.

## Upgrading

`tulip.upgrade()` OTAs from this release: on a Tab5 it reads
`releases/tags/tab5` on this fork, not the upstream repo. It needs wifi, and
offers the firmware and `/sys` separately.

## Known limitations

- **No `drums` partition**, so AMY's gamma9001 banks are not on flash.
- Storage helpers (SPIFFS, microSD) are not mounted — see
  `tulip/esp32p4/boards/TAB5/README.md` for why (ESP-IDF FATFS conflicts with
  MicroPython's `oofatfs`).
- Built against forked `amy` and `micropython` branches, not their upstreams.

## Build provenance

| | |
|---|---|
| Commit | `@COMMIT@` |
| Branch | `dev_tab5` |
| Build | [run @RUN_ID@](@RUN_URL@) |
| ESP-IDF | `v5.5.4` |
| Board | `TAB5` (`idf.py -DMICROPY_BOARD=TAB5 build`) |
| `amy` | `@AMY_SHA@` |
| `micropython` | `@MPY_SHA@` |
