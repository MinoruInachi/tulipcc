# M5Stack Tab5 porting note

This repository currently supports Tulip on ESP32-S3 boards. The M5Stack Tab5 is a different target class: it is based on the ESP32-P4 and uses a 5-inch 1280×720 MIPI-DSI display, so it does not fit the existing TulipCC board definitions.

## Tab5 specs relevant to a Tulip port

- Main controller: ESP32-P4NRW32, dual-core RISC-V, 360 MHz
- Wireless module: ESP32-C6-MINI-1U
- Flash: 16 MB
- PSRAM: 32 MB octal
- Display: 5-inch IPS, 1280×720, MIPI-DSI
- Touch: integrated display/touch stack on newer units, ST7123 or ST7121; early units may expose GT911
- Audio: ES8388 codec, ES7210 AEC front end, dual microphones, speaker, headphone jack
- Expansion: USB host, USB OTG, RS-485, microSD, M5-Bus, HY2.0-4P, GPIO_EXT

## Public pin-map details worth carrying into a port

- LCD power/backlight: G22 drives LEDA on the display side
- Touch bus: SDA, SCL, and TP_INT are routed for the touch controller
- Touch controller revision: public docs list GT911 0x14 / ST7123 0x55 / ST7121 0x55, and note a screen-driver change in late 2025 from an ILI9881C + GT911 split to the integrated ST7123 path
- Panel reset/power rails: LCD_RST and TP_RST are controlled through the PI4IOE5V6408 IO expander
- External power: EXT5V_EN switches 5V to the rear M5-Bus, side expansion, and HY2.0-4P
- Internal wireless power: WLAN_PWR_EN controls the ESP32-C6 module rail
- USB-A power: USB5V_EN gates USB-A host power
- Device power-off pulse: PWROFF_PLUSE is used for power control

## What is missing from TulipCC today

- No esp32p4 build target in the Tulip firmware tree
- No MIPI-DSI panel bring-up code in the current board layer
- No Tab5-specific touch driver integration for the ST7123/ST7121 path
- No Tab5 board definition or pin map in the current esp32s3 board set

## Official BSP baseline

The public M5Stack ESP-IDF BSP for Tab5 is a useful starting point, but it is not a drop-in match for TulipCC. The published BSP dependency set currently centers on:

- `espressif/esp_lcd_ili9881c` for display
- `espressif/esp_lcd_touch_gt911` for touch
- `espressif/esp_lvgl_port` for the UI layer
- `espressif/esp_codec_dev` and `espressif/esp_codec` support for audio
- ESP-IDF 5.4.x on the `esp32p4` target

That baseline also conflicts with the newer product-page documentation, which describes an integrated ST7123/ST7121 screen stack. For a Tulip port, that means screen revision detection or a dual-path display/touch bring-up is likely required.

## Practical porting steps

1. Add an esp32p4 build target and board directory.
2. Start from the official Tab5 BSP and separate the older ILI9881C/GT911 path from the newer ST7123/ST7121 path.
3. Bring up the MIPI-DSI display pipeline for 1280×720.
4. Add Tab5 touch initialization for the active screen driver revision.
5. Map GPIO, audio, storage, and power-control pins from the Tab5 schematic.
6. Validate boot, framebuffer, touch, USB, and audio on real hardware.

## Concrete bring-up plan

### Phase 0: baseline inventory

- Confirm the exact Tab5 revision on hand and whether the panel is the older ILI9881C/GT911 design or the newer ST7123/ST7121 integrated revision.
- Capture the public M5Stack BSP revision that matches the board revision you want to support.
- Confirm whether Tulip will target the M5Stack BSP as a dependency or vendor the needed LCD/touch components directly.

### Phase 1: board scaffold

- Add a new esp32p4 board family beside the current esp32s3 tree.
- Add a Tab5 board definition, board name, and MicroPython board metadata.
- Mirror the existing Tulip board split between common shared code and board-specific source files.

### Phase 2: display pipeline

- Start from the official Tab5 display path and make the panel backend swappable by revision.
- Keep the display stack separate from Tulip's framebuffer logic so the DSI panel can be driven without rewriting the higher-level graphics APIs.
- Verify that the final orientation, color order, and refresh rate match Tulip's existing rendering assumptions.

### Phase 3: touch input

- Add touch bring-up for the active controller revision.
- Route touch coordinates into Tulip's existing touch API rather than creating a new input surface.
- Make the touch path tolerant of early GT911 hardware and later ST7123/ST7121 hardware.

### Phase 4: peripherals and power

- Add audio init for ES8388 and ES7210.
- Add storage init for the microSD slot.
- Wire the power and rail-control GPIOs so display, USB host, wireless, and expansion power can be enabled safely.

### Phase 5: Tulip integration

- Hook the board into Tulip startup so the REPL, display, and touch are initialized in the same order as the current boards.
- Keep non-Tab5 features behind board checks so the new target does not disturb the current esp32s3 builds.
- Add a first-boot verification checklist for display, touch, audio, USB, SD card, and power events.

## Likely file areas once implementation starts

- New board directory under the Tulip hardware tree for esp32p4
- A board config file equivalent to the current esp32s3 `mpconfigboard.cmake`
- A board header equivalent to the current `mpconfigboard.h`
- Tab5-specific display and touch source files
- Any shared Tulip display abstraction that needs an esp32p4 branch