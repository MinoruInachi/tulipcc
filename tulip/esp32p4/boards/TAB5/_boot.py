import gc
import os
import sys
import time
import vfs

# First statement of the first thing that runs in a MicroPython session, on
# purpose. A soft reset rebuilds the GC heap but leaves LVGL rooted in the old
# one, so this turns Ctrl-D and machine.soft_reset() into a real reset instead --
# see tulip_restart_if_soft_reset() in modtulip_tab5.c for why re-initialising
# LVGL is not the answer. It does not return on a soft reset, and does nothing on
# a cold boot. Keep it ahead of the mount and the display bring-up below: the
# frame ISR starts scheduling LVGL work again within a frame of the reset.
import _tulip

_tulip.restart_if_soft_reset()

# _boot runs with __main__'s globals (see parse_compile_execute() in pyexec.c), so
# this is what puts ls/cd/cat/mkdir/rm and friends in the REPL namespace. The shared
# _boot.py does the same for every other board; without it the Tab5 REPL had none of
# them even though upysh is frozen into the firmware.
from upysh import *

# Tab5 uses the Tulip CC flash layout: the 'system' partition holds the
# read-only /sys tree (examples and images, built by tulip/fs_create.py) and
# 'vfs' holds user files at /user. Shared code assumes exactly this -- tulip.sys(),
# tulip.add_to_bootpy() and tulip_graphics.run() all build paths from
# root_dir() + "sys/" or "user/", and root_dir() is "/" on hardware.
#
# Firmware flashed before the partition table grew those two partitions has
# neither, and MicroPython registers a single 'vfs' spanning the rest of flash at
# runtime instead (see boardctrl_startup() in ports/esp32/main.c). Those devices
# keep booting on the old whole-flash mount at /, just without /sys.
def _mount_filesystems():
    from esp32 import Partition

    sys_part = Partition.find(Partition.TYPE_DATA, label="system")
    usr_part = Partition.find(Partition.TYPE_DATA, label="vfs")
    if not sys_part or not usr_part:
        return False
    vfs.mount(sys_part[0], "/sys")
    try:
        vfs.mount(usr_part[0], "/user")
    except OSError:
        # Flashing tulip-firmware-TAB5.bin alone leaves 'vfs' unformatted.
        print("TAB5 boot: /user is unformatted, making a filesystem")
        vfs.VfsLfs2.mkfs(usr_part[0])
        vfs.mount(usr_part[0], "/user")
    os.chdir("/user")
    sys.path.append("/sys/ex")
    # Nothing is mounted at /, so the port's default /lib entry can only ever
    # miss -- drop it rather than pay a failed stat on every import. Same as
    # tulip/shared/py/_boot.py does on the ESP32-S3.
    try:
        sys.path.remove("/lib")
    except ValueError:
        pass
    # A no-op unless CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE is turned on, but
    # without it an OTA'd app would be rolled back on the next boot if it ever is.
    try:
        Partition(Partition.RUNNING).mark_app_valid_cancel_rollback()
    except OSError:
        pass
    return True


try:
    _mounted = _mount_filesystems()
except Exception as e:
    print("TAB5 boot: /sys+/user mount failed:", e)
    _mounted = False

if not _mounted:
    print("TAB5 boot: no system/vfs partitions, mounting whole flash at /")
    from flashbdev import bdev

    try:
        if bdev:
            vfs.mount(bdev, "/")
    except OSError:
        import inisetup

        inisetup.setup()

# Initialize the shared framebuffer-backed LVGL display before importing ui.py.
try:
    import _tulip

    for _ in range(150):
        if _tulip.display_ready() and _tulip.ui_init():
            break
        time.sleep_ms(20)
    else:
        print("TAB5 boot: LVGL display init timed out")
except Exception as e:
    print("TAB5 boot: LVGL display init skipped:", e)

# Complete the C boot-banner handoff before tulip imports and presents ui.py.
try:
    for _ in range(150):
        if _tulip.tfb_ready():
            time.sleep_ms(2500)
            _tulip.tfb_reset()
            _tulip.bg_reset()
            break
        time.sleep_ms(20)
except Exception as e:
    print("TAB5 boot: pre-ui status draw skipped:", e)

# Bring up Tulip UI stack early on TAB5.
try:
    import tulip
    import world
except Exception as e:
    print("TAB5 boot: tulip UI init skipped:", e)
    try:
        _tulip.boot_status(str(e))
    except Exception as fallback_err:
        print("TAB5 boot: fallback status draw failed:", fallback_err)

# Same as Tulip CC: user-installed modules live in /user/lib and are importable.
if _mounted:
    try:
        if not tulip.exists("/user/lib"):
            os.mkdir("/user/lib")
        sys.path.append("/user/lib")
    except Exception as e:
        print("TAB5 boot: /user/lib setup skipped:", e)

try:
    import amy
    import midi

    amy.AMY_SAMPLE_RATE = 44100
    amy.override_send = lambda message: tulip.amy_send(message)
    midi.setup()
except Exception as e:
    print("TAB5 boot: audio/MIDI init skipped:", e)

try:
    # Boot into Tulip's LVGL-backed REPL screen when available.
    import ui
    _tulip.ui_start()
except Exception as e:
    print("TAB5 boot: ui auto-start skipped:", e)
    try:
        import _tulip

        _tulip.boot_status("ui start skipped: %s" % e)
    except Exception:
        pass

gc.collect()
