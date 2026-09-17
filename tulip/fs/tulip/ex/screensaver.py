"""A screensaver: the Tulip logo, somewhere new every couple of seconds.

    run('screensaver')

Touch the screen or press any key to quit.

The logo is a 128x128 RGBA PNG whose three petals are closed outlines over a
transparent middle, so the background colour shows through them. That is what
makes the colouring work: drop the logo somewhere, then flood fill each petal
from a point inside it, and the fill stops at the petal's own outline. Each
petal gets its own random colour, so the same picture never comes up twice.

Everything is drawn on the BG plane. On a Tab5 LVGL is composited on top of the
BG rather than into it, so a screen showing the BG has to say so with
bg_plane -- otherwise LVGL's own opaque background covers the whole thing.
"""

import tulip
import random

(SW, SH) = tulip.screen_size()

LOGO = "/g/tulipbw.png"
LOGO_W = 128
LOGO_H = 128

# The colour the screen is cleared to, and so the colour inside the petals
# before they are filled -- a flood fill replaces exactly this one.
BG_COLOR = 36
# A stand-in for whenever a random colour lands on BG_COLOR. The fill starts by
# comparing the new colour against the one already there and gives up when they
# are the same, so filling a petal with the background would silently do
# nothing at all.
BG_COLOR_ALT = 35

# Points inside each of the three petals, relative to the logo's top left. Row
# 60 crosses all three: the outlines sit at x=12-20, 33-42, 83-91 and 105-113,
# so these land in the gaps between them.
PETALS = ((30, 60), (50, 60), (95, 60))

# How long each arrangement stays up.
EVERY_MS = 2500


def draw(app):
    tulip.bg_clear(BG_COLOR)
    x = random.randint(0, SW - LOGO_W)
    y = random.randint(0, SH - LOGO_H)
    tulip.bg_png(app.app_dir + LOGO, x, y)
    for (px, py) in PETALS:
        color = random.randint(0, 255)
        if color == BG_COLOR:
            color = BG_COLOR_ALT
        tulip.bg_fill(x + px, y + py, color)


def frame(app):
    if tulip.ticks_ms() - app.last_draw > EVERY_MS:
        draw(app)
        app.last_draw = tulip.ticks_ms()


def leave():
    # Quitting tears down the screen's LVGL objects, and both of the callbacks
    # that get us here arrive on the scheduler while LVGL may be part way
    # through a frame. Going out through a defer puts the teardown back on the
    # app's own footing, which is how the other examples quit from a callback.
    app = tulip.running_apps.get("screensaver", None)
    if app is not None:
        tulip.defer(lambda a: a.quit(), app, 20)


def touch(up):
    if not up:
        leave()


def key(k):
    leave()


def activate(app):
    app.last_draw = tulip.ticks_ms() - EVERY_MS  # draw the first one right away
    tulip.frame_callback(frame, app)
    tulip.touch_callback(touch)
    tulip.keyboard_callback(key)


def deactivate(app):
    tulip.frame_callback()
    tulip.touch_callback()
    tulip.keyboard_callback()
    tulip.bg_clear()


def run(screen):
    # The picture is on the BG plane, so LVGL has to let it through.
    screen.bg_plane = True
    screen.bg_color = BG_COLOR
    screen.last_draw = 0
    screen.activate_callback = activate
    screen.deactivate_callback = deactivate
    screen.present()
