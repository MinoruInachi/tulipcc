# ESP32-P4 port scaffold

This directory is reserved for a future Tulip hardware port on ESP32-P4 boards.

## Current status

TulipCC today is still an ESP32-S3-only tree. The ESP32-P4 path is not wired into the build yet, and the M5Stack Tab5 needs that new target before it can run Tulip natively.

## Intended contents

- Board-specific build files for ESP32-P4 targets
- Board metadata and pin definitions for Tab5-style hardware
- Display bring-up for MIPI-DSI panels
- Touch bring-up for the active Tab5 panel revision
- Audio, storage, USB, and power-control hooks
- A Tab5 startup sequence that sequences power, audio, storage, display, and touch
- A local MicroPython board manifest for the ESP32-P4 Python surface

For the first board implementation, see [boards/TAB5/README.md](boards/TAB5/README.md).

## Tab5 target notes

The M5Stack Tab5 is the first planned consumer of this scaffold. See [../../docs/tab5_porting.md](../../docs/tab5_porting.md) for the current porting notes, hardware facts, and phased bring-up plan.