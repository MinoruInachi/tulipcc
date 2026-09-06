"""The Tab5's camera on screen.

    run('camera')

A live preview on the BG plane, a button that saves what you see as a JPEG in
/user, and the sensor's frame counter and rate in a label. Tab5 only: the
tulip.camera_* calls do not exist on other boards.

The preview is tulip.camera_bg() called from the frame callback -- the newest
camera frame converted to the BG's RGB332 -- at a 16:9 size that fits under
the task bar. Nothing here waits: the camera keeps its own newest frame, so
each Tulip frame just draws whatever the sensor has most recently delivered.
"""

import tulip

(SW, SH) = tulip.screen_size()

# 16:9, left/right margins, room for the task bar above and the controls below.
PREVIEW_W = 960
PREVIEW_H = 540
PREVIEW_X = (SW - PREVIEW_W) // 2
PREVIEW_Y = 110


def frame(app):
    try:
        tulip.camera_bg(PREVIEW_X, PREVIEW_Y, PREVIEW_W, PREVIEW_H)
    except RuntimeError:
        return  # camera not running (quit is in progress)
    app.frames += 1
    if app.frames % 30 == 0:
        info = tulip.camera_info()
        app.status.label.set_text("%s  %d fps  %d frames  %s" % (
            info["sensor"], info["fps"], info["frames"], app.last_saved))


def save(e):
    app = tulip.running_apps["camera"]
    app.shot += 1
    filename = "/user/camera-%03d.jpg" % app.shot
    size = tulip.camera_capture(filename, quality=90)
    app.last_saved = "saved %s (%d bytes)" % (filename, size)
    app.status.label.set_text(app.last_saved)


def flip(e):
    (h, v) = tulip.camera_flip()
    tulip.camera_flip(hflip=not h)


def activate(app):
    tulip.camera_start()
    tulip.frame_callback(frame, app)


def deactivate(app):
    tulip.frame_callback()
    tulip.bg_clear()


def quit(app):
    tulip.camera_stop()


def run(screen):
    # The preview lives on the BG plane, so LVGL's own background has to be
    # see-through (on the Tab5 LVGL is an overlay on top of the BG).
    screen.bg_plane = True
    # Absolute coordinates for the widgets, so they and the BG preview agree.
    screen.offset_y = 0
    screen.bg_color = 0
    screen.frames = 0
    screen.shot = 0
    screen.last_saved = ""
    screen.activate_callback = activate
    screen.deactivate_callback = deactivate
    screen.quit_callback = quit
    screen.add(tulip.UIButton(text="Save JPEG", callback=save), x=PREVIEW_X, y=PREVIEW_Y + PREVIEW_H + 15)
    screen.add(tulip.UIButton(text="Mirror", callback=flip), x=PREVIEW_X + 180, y=PREVIEW_Y + PREVIEW_H + 15)
    screen.status = tulip.UILabel("starting camera", w=600)
    screen.add(screen.status, x=PREVIEW_X + 360, y=PREVIEW_Y + PREVIEW_H + 25)
    screen.present()
