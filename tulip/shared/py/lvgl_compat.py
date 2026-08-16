"""Centralized LVGL compatibility loader.

This keeps native/stub switching in one place so migrating to native LVGL
only needs updates here.
"""

try:
    import lvgl as _lv
    USING_LVGL_STUB = bool(getattr(_lv, "USING_LVGL_STUB", False))
except ImportError:
    import lvgl_stub as _lv
    USING_LVGL_STUB = True


lv = _lv

_backend_logged = False


def get_backend_name():
    """Return active LVGL backend name: 'stub' or 'native'."""
    return "stub" if USING_LVGL_STUB else "native"


def log_backend_once(prefix="LVGL backend"):
    """Print backend name once per interpreter lifetime."""
    global _backend_logged
    if _backend_logged:
        return
    print("%s: %s" % (prefix, get_backend_name()))
    _backend_logged = True


def maybe_log_backend(prefix="LVGL backend"):
    """Print backend once when TULIP_LVGL_LOG_BACKEND=1 is set."""
    try:
        import os
        if os.getenv("TULIP_LVGL_LOG_BACKEND") == "1":
            log_backend_once(prefix)
    except Exception:
        # Keep compatibility even if os/env is restricted.
        pass
