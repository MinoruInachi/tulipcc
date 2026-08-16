"""Legacy LVGL compatibility alias.

Use `from lvgl_compat import lv` in app code.
"""

from lvgl_stub import *  # noqa: F401,F403

# Marker used by lvgl_compat to distinguish native lvgl from this alias.
USING_LVGL_STUB = True
