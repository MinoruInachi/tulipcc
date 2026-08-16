import gc
import time
import vfs
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
