"""The Tab5's camera on screen.

    run('camera')

A live preview on the BG plane, a button that saves what you see as a JPEG in
/user, and the sensor's frame counter and rate in a label. Tab5 only: the
tulip.camera_* calls do not exist on other boards.

The preview is tulip.camera_bg() called from the frame callback -- the newest
camera frame copied onto the BG plane, which is RGB565 like the sensor's own
output, so nothing is converted. Nothing here waits either: the camera keeps
its own newest frame, so each Tulip frame just draws whatever the sensor has
most recently delivered.
"""

import tulip

(SW, SH) = tulip.screen_size()

# 16:9, room for the task bar above and the controls below -- and half the
# sensor's 1280x720, which is what the size is really chosen by.
#
# Drawing the preview is pure memory traffic: the frame is in PSRAM and so is
# the BG plane, and the compositor is reading that same plane at the same time.
# Nothing makes the copy cheaper (the PPA is busy rotating the screen, the
# sensor has no mode below 1280x720, and the capture node refuses any other
# size), so the only way to spend less is to copy less. Measured on a Tab5, at
# 110 rows down the screen:
#
#     960x540   59.1ms a frame, 16.8 draws/s, panel 12.2 fps
#     800x450   45.6ms           21.6/s              14.8
#     640x360   31.1ms           32.4/s              16.2
#     480x270   19.7ms           49.6/s              18.4
#
# The sensor runs at 30fps, so anything past ~30 draws/s is drawing the same
# frame twice: 640x360 is the smallest size that still keeps up with it, and
# going smaller throws away half the picture for frames that do not exist. It
# is also exactly half of 1280x720, so the horizontal scale is a clean 2:1.
PREVIEW_W = 640
PREVIEW_H = 360
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
    # The controls get their own left margin rather than the preview's: the row
    # is wider than the preview now, and hanging it off PREVIEW_X would push the
    # status label off the right-hand edge.
    controls_x = 160
    row_y = PREVIEW_Y + PREVIEW_H + 15
    screen.add(tulip.UIButton(text="Save JPEG", callback=save), x=controls_x, y=row_y)
    screen.add(tulip.UIButton(text="Mirror", callback=flip), x=controls_x + 180, y=row_y)
    screen.status = tulip.UILabel("starting camera", w=600)
    screen.add(screen.status, x=controls_x + 360, y=row_y + 10)
    screen.present()
