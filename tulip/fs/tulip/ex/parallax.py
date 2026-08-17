# A simple game demo that shows off BG and sprites and the GPU callback
import tulip

(sw,sh) = tulip.screen_size()

# The sky and the mountains are fixed-size art pinned to the top of the screen,
# so their rows are the same everywhere. The ground is a 190px block that used
# to be pinned to y=280 -- drawn for a 600px screen, which on a Tab5 left the
# bottom 120px empty and the rabbit running along a ledge in mid-air. Hang it
# off the bottom of the screen instead, keeping the gap under the water it has
# always had. The dark band between the mountains and the grass stretches to
# take up the difference.
GROUND_Y = 280 + (sh - 600)
GROUND_H = 190
# Rows within the ground block: meadow, then three more tile rows, then the
# brick the rabbit runs on and the water under it.
BRICK_Y = GROUND_Y + 128
WATER_Y = GROUND_Y + 160
# Where the rabbit's feet land on the brick.
RABBIT_Y = BRICK_Y - 28
# The two tile rows that scroll with the rabbit: the brick and the water.
SCROLL_ROWS = 64


def draw_background(app):
    pix_dir = app.app_dir+"/g/"
    tulip.bg_png(pix_dir+"mountain-bg.png",0,0)
    # Copy the background over a bunch to make it repeat, but overlap to avoid double moons
    for i in range(20):
        tulip.bg_blit(0,0,272,160,272+(i*100),0)

    # now load the mountain pics over the background
    # let's load the data to a var first as we'll load it three times
    # why not blit? because it can't copy the alpha that way, we composite on placement for BG
    mountain = open(pix_dir+'mountain.png','rb').read()
    for i in range(5):
        tulip.bg_png(mountain,544*i,50)
    mountain = None
    # And put black under the mountains, down to wherever the ground starts
    tulip.bg_rect(0,50+160,sw,GROUND_Y-(50+160),0,1)

    # and some tiles
    tulip.bg_png(pix_dir+'meadow.png',0,GROUND_Y)
    # copy it three times underneath
    tulip.bg_blit(0,GROUND_Y+20,32,32,0,GROUND_Y+32)
    tulip.bg_blit(0,GROUND_Y+20,32,32,0,GROUND_Y+64)
    tulip.bg_blit(0,GROUND_Y+20,32,32,0,GROUND_Y+96)
    tulip.bg_png(pix_dir+'brick.png',0,BRICK_Y)
    tulip.bg_png(pix_dir+'water.png',0,WATER_Y)

    # Copy this column across the screen
    for i in range((sw*2)//32):
        tulip.bg_blit(0,GROUND_Y,32,GROUND_H,i*32,GROUND_Y)

    # put some empty spots along the brick
    for i in [3, 8, 10, 14, 18, 25, 32, 33, 38]:
        tulip.bg_blit(0,GROUND_Y+32,32,32,32*i, BRICK_Y)

    # Now scroll the moon and the mountains at separate speeds
    for i in range(100):
        tulip.bg_scroll_x_speed(i, 2)
    for i in range(110):
        tulip.bg_scroll_x_speed(i+100, 5)


    # Load the rabbit sprite frames into sprite RAM
    (app.rabbit_w, app.rabbit_h) = (48, 32)
    for i in range(4):
        tulip.sprite_png(pix_dir+"rabbit_r_%d.png" % (i),(app.rabbit_w*app.rabbit_h)*i)
    for i in range(4):
        tulip.sprite_png(pix_dir+"rabbit_l_%d.png" % (i),(app.rabbit_w*app.rabbit_h)*(i+4))

    # Register the first frame, we'll swap out frames during animation
    tulip.sprite_register(0, 0, app.rabbit_w, app.rabbit_h)
    tulip.sprite_on(0)

    # Now run the game loop. First setup some variables for the game state. rabbit x and y, frame counter etc
    app.d = {"dir":0, "f":0, "rx":50, "ry":RABBIT_Y, "jump":0, "run":1, "scroll":0}
    app.rabbit_speed = 10


# This is called every frame by the GPU.
def game_loop(app):
    # This is a bit of a hack for web; the frame scheduler can happen after we quit/alttab away from this app, so we check
    if(not app.active): return

    # Increment the frame counter
    app.d["f"] = app.d["f"] + 1
    # Move the rabbit if direction is set
    if(tulip.joyk() & tulip.Joy.RIGHT):
        # Update the frame animation if we're moving (right facing rabbit)
        tulip.sprite_register(0,(app.rabbit_w*app.rabbit_h)*(app.d["f"]%4), app.rabbit_w, app.rabbit_h)
        # If we're beyond the middle of the screen, scroll the bricks instead
        if(app.d["rx"] > (sw/2)):
            if(app.d["scroll"]==0):
                app.d["scroll"] = 1
                for i in range(SCROLL_ROWS): # lower 2 tile rows
                    tulip.bg_scroll_x_speed(BRICK_Y+i, app.rabbit_speed)
        else:
            app.d["rx"] += app.rabbit_speed
    else:
        if(app.d["scroll"] == 1):
            app.d["scroll"] = 0
            for i in range(SCROLL_ROWS):
                tulip.bg_scroll_x_speed(BRICK_Y+i, 0) # stop scrolling

    if(tulip.joyk() & tulip.Joy.LEFT):
        app.d["rx"] -= app.rabbit_speed
        tulip.sprite_register(0,(app.rabbit_w*app.rabbit_h)*((app.d["f"]%4)+4), app.rabbit_w, app.rabbit_h)

    if(tulip.joyk() & tulip.Joy.B):
        if(app.d["jump"]==0):
            app.d["jump"] = app.d["f"]
    else:
        app.d["jump"] = 0

    # calculate position from jump start frame time
    if(app.d["jump"]>0):
        app.d["ry"] = RABBIT_Y-(app.d["f"]-app.d["jump"])*app.rabbit_speed
    else:
        app.d["ry"] = RABBIT_Y
    if(hasattr(tulip, "keys") and tulip.keys()[1]==0x29): # esc
        app.d["run"] = 0
    # Update the sprite position
    if(app.d["rx"] < 0):
        app.d["rx"] = 0
    tulip.sprite_move(0,app.d["rx"], app.d["ry"])


def activate_callback(app):
    draw_background(app)
    # Register the frame callback and data
    tulip.frame_callback(game_loop, app)


def deactivate_callback(app):
    tulip.bg_scroll()

def run(app):
    app.game = True
    app.activate_callback = activate_callback
    app.deactivate_callback = deactivate_callback
    app.present()





