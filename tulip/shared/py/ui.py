# ui.py
# imports into tulip for ui_x translation into lvgl
# also has keyboard and other small LVGL things
import tulip, tulip_graphics
import time
import lvgl as lv

LV_SIZE_CONTENT = getattr(lv, "SIZE_CONTENT", (1 << 30) - 1)
# LVGL 9.5 turned LV_ANIM_OFF/ON from an enum into plain macros over bool
# (lv_anim.h: "#define LV_ANIM_OFF false", "typedef bool lv_anim_enable_t"), and
# gen_mpy.py only exports enums -- so there is no lv.ANIM on a 9.5 binding. Same
# guard juno6.py and voices.py already use.
LV_ANIM_OFF = lv.ANIM.OFF if hasattr(lv, 'ANIM') else False

lv_soft_kb = None
lv_launcher = None

# The Tab5 has no mouse -- everything on it is hit with a fingertip. At 12pt the
# shared task bar buttons come out around 20px square, which is under 3mm on its
# 7" panel and easy to miss. Scale the task bar and the launcher up there only;
# every other board drives these with a mouse or the keyboard and is unchanged.
_touch_ui = tulip.board() == "TAB5"
# lvgl only builds the sizes enabled in lv_conf.h (8/12/18/24/36), and the stub
# backend carries fewer still, so fall back rather than fail to import.
_task_bar_font = getattr(lv, "font_montserrat_24", lv.font_montserrat_12) if _touch_ui else lv.font_montserrat_12
_launcher_font = getattr(lv, "font_montserrat_18", lv.font_montserrat_12) if _touch_ui else lv.font_montserrat_12
# Square touch target for the three task bar icons, and a wider launcher so the
# bigger text still fits on one line.
_task_bar_button_px = 56
_launcher_width_px = 300 if _touch_ui else 195


# ---- Layout metrics --------------------------------------------------------
# Every app under tulip/shared/py was laid out against the 1024x600 Tulip CC
# panel, with its geometry written in as literals -- voices.py even drew the
# bottom of the piano at y=599. The Tab5's panel is 1280x720, so those apps used
# well under two thirds of it and left the rest black. They ask for their
# geometry here now instead.
#
# Everything below returns the old literal on a 1024x600 screen, so Tulip CC,
# desktop and web keep the layout they have always had, to the pixel.
DESIGN_H_RES = 1024
DESIGN_V_RES = 600


def screen_extra_w():
    """How much wider this panel is than the 1024 the apps were drawn for."""
    return max(0, tulip.screen_size()[0] - DESIGN_H_RES)


def screen_extra_h():
    """How much taller this panel is than the 600 the apps were drawn for."""
    return max(0, tulip.screen_size()[1] - DESIGN_V_RES)


def touch_first():
    """True where the only pointer is a fingertip, so controls have to be big."""
    return _touch_ui


def touch_px(mouse_px, finger_px):
    """Size a control: what a mouse can hit, or what a fingertip can."""
    return finger_px if _touch_ui else mouse_px


def _style_task_bar_button(button, label):
    label.set_style_text_font(_task_bar_font, 0)
    label.set_style_text_align(lv.TEXT_ALIGN.CENTER, 0)
    if _touch_ui:
        # A fixed-size button no longer shrinks to its label, so the glyph has to
        # be centred explicitly or it sits in the top-left corner.
        button.set_size(_task_bar_button_px, _task_bar_button_px)
        label.center()


running_apps = {}
current_app_string = "repl"

# On TAB5 LVGL is composited over the BG plane instead of drawn into it (lv_overlay
# in shared/display.c), which is what stops a game's BG drawing from blacking the
# task bar buttons out and a scrolled row from dragging them sideways. The other
# side of that bargain is transparency: LVGL is now genuinely on top, so a screen
# that wants the BG plane visible has to leave its own background alone and let the
# BG plane carry the colour instead. On every other board LVGL renders straight
# into bg, where painting an opaque background is how a screen covers whatever the
# last app left behind, so none of this applies.
LV_ALPHA = 0x55
_lvgl_overlays_bg = tulip.board() == "TAB5"


def _screen_bg_color(screen):
    """The colour LVGL should paint this screen's own background in.

    Transparent for a screen with anything of its own on the BG plane -- a game,
    the REPL and its wallpaper, or an app that says so with bg_plane -- and its
    declared colour goes on the BG plane instead (see UIScreen._paint_bg_plane).
    Every other screen stays opaque, which keeps LVGL's own background doing the
    covering for the apps that only ever draw widgets.
    """
    if _lvgl_overlays_bg and (screen.game or screen.bg_plane or screen.name == 'repl'):
        return LV_ALPHA
    return screen.bg_color


_repl_background_unset = object()
_repl_background = None
_repl_background_bitmap = None


def _draw_repl_background(_):
    if _repl_background is None:
        return
    width, height = tulip.screen_size()
    if _repl_background_bitmap is not None:
        tulip.bg_bitmap(0, 0, width, height, _repl_background_bitmap)
    else:
        image, x, y, color = _repl_background
        tulip.bg_clear(color)
        tulip.bg_png(image, x, y)
    repl_screen.group.invalidate()


def repl_background(image=_repl_background_unset, x=0, y=0, color=9):
    global _repl_background, _repl_background_bitmap
    if image is _repl_background_unset:
        return _repl_background
    if image is None:
        _repl_background = None
        _repl_background_bitmap = None
        # Opaque again: LVGL paints over bg on the REPL screen, so it can erase
        # its own widgets there without help. See _repaint_repl_background().
        # (Where LVGL is an overlay it is set_bg_color that puts the flat colour
        # back over the wallpaper, on the BG plane -- see _paint_bg_plane.)
        repl_screen.set_bg_color(UIScreen.default_repl_bg_color)
        return None
    if isinstance(image, str) and not image.startswith('/'):
        image = tulip.pwd().rstrip('/') + '/' + image
    _repl_background = (image, x, y, color)
    # Transparent, so the wallpaper below LVGL shows through the REPL screen.
    repl_screen.set_bg_color(0x55)
    _draw_repl_background(None)
    width, height = tulip.screen_size()
    _repl_background_bitmap = tulip.bg_bitmap(0, 0, width, height)
    return _repl_background



# Returns the keypad indev
def get_keypad_indev():
    if tulip.board() == "TAB5":
        return None
    nobody = lv.indev_t()
    a = nobody.get_next()
    if (a.get_type() == lv.INDEV_TYPE.KEYPAD):
        return a
    b = a.get_next()
    if (b.get_type() == lv.INDEV_TYPE.KEYPAD):
        return b
    print("Couldn't find indev of type KEYPAD")
    return None

# Convert tulip rgb332 pal idx into lv color
def pal_to_lv(pal):
    (r,g,b) = tulip_graphics.rgb(pal, wide=True) # todo -- not sure if we use wide or not
    return lv.color_make(r,g,b)

# Convert tulip rgb332 pal idx into lv color
def lv_to_pal(lvcolor):
    return tulip_graphics.color(lvcolor.red, lvcolor.green, lvcolor.blue)

# Remove padding from an LVGL object. Sometimes useful. 
def lv_depad(obj, remove_scroll = False):
    try:
        obj.set_style_pad_left(0,0)
        obj.set_style_pad_right(0,0)
        obj.set_style_pad_top(0,0)
        obj.set_style_pad_bottom(0,0)
        obj.set_style_margin_left(0,0)
        obj.set_style_margin_right(0,0)
        obj.set_style_margin_top(0,0)
        obj.set_style_margin_bottom(0,0)
    except KeyError:
        pass
    if(remove_scroll):
        obj.remove_flag(lv.obj.FLAG.SCROLLABLE)


def current_uiscreen():
    global current_app_string
    return running_apps[current_app_string]
def current_lv_group():
    return current_uiscreen().group

def hide(i):
    g = tulip.current_uiscreen().group
    to_hide = g.get_child(i)
    try:
        to_hide.add_flag(1) # hide
    except AttributeError: # we've switched too fast
        pass


def unhide(i):
    g = tulip.current_uiscreen().group
    to_unhide = g.get_child(i)
    try:
        to_unhide.remove_flag(1) # show
    except AttributeError: # we've switched too fast and the hidden thing wasn't hidden
        pass

# The entire UI is loaded into this screen, which we can swap out from "main" REPL screen
class UIScreen():
    first_run = True
    # Constants you can change
    default_bg_color = 0
    default_repl_bg_color = 9
    # Start drawing at this position, a little to the right of the edge and 100px down
    default_offset_x = 10
    default_offset_y = 100

    def __init__(self, name=None, keep_tfb = False, bg_color=default_bg_color, offset_x=default_offset_x, offset_y=default_offset_y, 
        activate_callback=None, quit_callback=None, deactivate_callback=None, handle_keyboard=False):

        # support running run(tulip.UIScreen()) in one-off temporary scripts
        if(name is None):
            name = "anon"

        self.screen = lv.obj() # a screen, will be display size (which is actually 2x H_RES)
        self.group = lv.obj(self.screen) # the group to write UI elements to in the screen
        self.group.set_width(tulip.screen_size()[0])
        self.group.set_height(tulip.screen_size()[1])
        self.group.set_style_radius(0,lv.PART.MAIN)
        self.group.set_style_border_width(0, lv.PART.MAIN)
        self.app_dir = tulip.pwd()
        lv_depad(self.group)
        self.game = False
        # Set this if the app draws on the BG plane without being a game -- it is
        # what tells LVGL to keep its hands off this screen's background so the BG
        # plane shows through (voices.py's piano is drawn that way). Only matters
        # where LVGL is composited over the BG rather than into it; see
        # _screen_bg_color().
        self.bg_plane = False
        self.hide_task_bar = False
        self.keep_tfb = keep_tfb
        self.handle_keyboard = handle_keyboard
        self.bg_color = bg_color
        self.offset_x = offset_x
        self.offset_y = offset_y
        self.last_obj_added = None
        self.name = name
        self.alttab_button = None
        self.quit_button = None
        # Only the REPL screen gets one, but draw_task_bar() has to be able to
        # ask whether it already exists on any screen.
        self.launcher_button = None
        self.alttab_cb_data = {}
        self.quit_cb_data = {}
        self.launcher_cb_data = {}
        self.running = True # is this code running 
        self.active = False # is it showing on screen 
        self.activate_callback = activate_callback
        self.deactivate_callback = deactivate_callback
        self.quit_callback = quit_callback
        self.kb_group = lv.group_create()
        if(self.name != 'repl'):
            self.kb_group.set_default()
        running_apps[self.name] = self

    def draw_task_bar(self):
        # draw whatever all screens share
        if(self.alttab_button is None):
            if( len(running_apps)>1):
                self.alttab_button = lv.button(self.group)
                self.alttab_button.set_style_bg_color(pal_to_lv(11), lv.PART.MAIN)
                self.alttab_button.set_style_radius(0,lv.PART.MAIN)
                alttab_label = lv.label(self.alttab_button)
                alttab_label.set_text(lv.SYMBOL.SHUFFLE)
                _style_task_bar_button(self.alttab_button, alttab_label)
                self.alttab_button.align_to(self.group, lv.ALIGN.TOP_RIGHT,0,0)
                self.alttab_button.add_event_cb(self.alttab_callback, lv.EVENT.CLICKED, self.alttab_cb_data)
        else:
            if(len(running_apps) == 1):
                self.alttab_button.delete()
                self.alttab_button = None

        # Each branch guards on the button it actually creates. These used to
        # share one `if self.quit_button is None` test, which the REPL branch
        # never satisfies -- the REPL gets a launcher button, never a quit
        # button -- so every present() of the REPL screen stacked another
        # launcher button on top of the last one, complete with its own click
        # handler. present() runs on boot, on every app quit and on every
        # return from a script that borrowed the screen, so they piled up.
        if(self.name != "repl"):
            if(self.quit_button is None):
                self.quit_button = lv.button(self.group)
                self.quit_button.set_style_bg_color(pal_to_lv(128), lv.PART.MAIN)
                self.quit_button.set_style_radius(0,lv.PART.MAIN)
                quit_label = lv.label(self.quit_button)
                quit_label.set_text(lv.SYMBOL.POWER)
                _style_task_bar_button(self.quit_button, quit_label)
                self.quit_button.align_to(self.alttab_button, lv.ALIGN.OUT_LEFT_MID,0,0)
                self.quit_button.add_event_cb(self.screen_quit_callback, lv.EVENT.CLICKED, self.quit_cb_data)
        else:
            if(self.launcher_button is None):
                self.launcher_button = lv.button(self.group)
                self.launcher_button.set_style_bg_color(pal_to_lv(36), lv.PART.MAIN)
                self.launcher_button.set_style_radius(0,lv.PART.MAIN)
                launcher_label = lv.label(self.launcher_button)
                launcher_label.set_text(lv.SYMBOL.LIST)
                _style_task_bar_button(self.launcher_button, launcher_label)
                self.launcher_button.align_to(self.group, lv.ALIGN.BOTTOM_RIGHT,0,0)
                self.launcher_button.add_event_cb(launcher, lv.EVENT.CLICKED, self.launcher_cb_data)



    def set_bg_color(self, bg_color):
        self.bg_color = bg_color
        self.group.set_style_bg_color(pal_to_lv(_screen_bg_color(self)), lv.PART.MAIN)
        if(self.active):
            # A see-through screen keeps its background on the BG plane, so that
            # is where a colour change has to land as well. Wipes whatever else
            # the app had drawn down there, the same as LVGL repainting its own
            # background would have.
            self._paint_bg_plane()

    def _paint_bg_plane(self):
        """Paint this screen's background where a transparent screen keeps it.

        A no-op unless LVGL is an overlay and this screen is see-through, in which
        case the colour LVGL would have painted has to go on the BG plane -- or the
        screen shows whatever the last app left down there through its own
        background until something else draws.
        """
        if _lvgl_overlays_bg and _screen_bg_color(self) == LV_ALPHA:
            if self.bg_color != LV_ALPHA:
                tulip.bg_clear(self.bg_color)


    def alttab_callback(self, e):
        if(len(running_apps)>1):
            self.active = False
            if(self.deactivate_callback is not None):
                self.deactivate_callback(self)

            if(self.game):
                tulip.frame_callback()
                tulip.Sprite.reset()
                tulip.bg_clear()
                if hasattr(tulip, "key_scan"):
                    tulip.key_scan(0)

            # Find the next app in the list (assuming dict is ordered by insertion, I think it is)
            apps = list(running_apps.items())
            for i,app in enumerate(apps):
                if(self.name == app[0]):
                    apps[(i + 1) % len(running_apps)][1].present()

    def screen_quit_callback(self, e):
        if(self.name!='repl'):
            import gc

            if(self.deactivate_callback is not None):
                self.deactivate_callback(self)

            if(self.quit_callback is not None):
                self.quit_callback(self)
            self.running = False
            self.active = False
            self.remove_items()

            if(self.game):
                tulip.frame_callback()
                tulip.collisions() # resets collision
                tulip.Sprite.reset()  # resets sprite counter
                tulip.bg_clear()
                tulip.tfb_update()
                if hasattr(tulip, "key_scan"):
                    tulip.key_scan(0)
            try:
                del running_apps[self.name]
            except KeyError:
                pass
            gc.collect()
            repl_screen.present()

    def quit(self):
        self.screen_quit_callback(None)


    # add an obj (or list of obj) to the screen, aligning by the last one added,
    # or the object relative (if you want to for example make a new line)
    # or x,y directly if you want that
    def add(self, obj, first_align=lv.ALIGN.TOP_LEFT, direction=lv.ALIGN.OUT_RIGHT_MID, relative=None, pad_x=0, pad_y=0, x=None, y=None):
        if(relative is not None):
            self.last_obj_added = relative.group

        if(type(obj) != list): obj = [obj]
        for o in obj:
            o.group.set_parent(self.group)
            o.group.set_style_bg_color(pal_to_lv(self.bg_color), lv.PART.MAIN)
            o.group.set_height(LV_SIZE_CONTENT)
            if(self.last_obj_added is None):
                o.group.align_to(self.group,first_align,self.offset_x,self.offset_y)
            else:
                try:
                    o.group.align_to(self.last_obj_added, direction,pad_x,pad_y)
                except lv.LvReferenceError:
                    self.last_obj_added = None
                    o.group.align_to(self.group,first_align,self.offset_x,self.offset_y)
            o.group.set_width(o.group.get_width()+pad_x)
            o.group.set_height(o.group.get_height()+pad_y)
            if(x is not None and y is not None): o.group.set_pos(x,y)
            self.last_obj_added = o.group

    # Show the UI on the screen. Set up the keyboard group listener. Draw the task bar. 
    def present(self):
        global current_app_string
        current_app_string = self.name
        self.active = True
        if(not self.hide_task_bar):
            self.draw_task_bar()
        self.group.set_style_bg_color(pal_to_lv(_screen_bg_color(self)), lv.PART.MAIN)

        lv.screen_load(self.screen)

        if(self.handle_keyboard):
            if tulip.board() == "TAB5":
                self.kb_group.set_default()
            else:
                keypad_indev = get_keypad_indev()
                if keypad_indev is not None:
                    keypad_indev.set_group(self.kb_group)

        if(self.name == 'repl'):
            tulip.tfb_start()
            tulip.set_screen_as_repl(1)
            tulip.tfb_update() # force redraw of tfb, maybe tfb_start should do this?
            if _repl_background is not None:
                tulip.defer(_draw_repl_background, None, 200)
            else:
                self._paint_bg_plane()

        else:
            tulip.set_screen_as_repl(0)
            if(self.keep_tfb):
                tulip.tfb_start()
            else:
                tulip.tfb_stop()
            self._paint_bg_plane()
            if(self.game):
                if hasattr(tulip, "key_scan"):
                    tulip.key_scan(1) # enter direct scan mode, keys will not hit the REPL this way

        if(self.activate_callback is not None):
            # We defer the activate callback as some apps will draw to the BG, and LVGL may get there first
            tulip.defer(self.activate_callback, self, 200)

        # These set (in modtulip/C) the python callbacks for quit and switch. 
        # This lets control-Q and control-TAB control them 
        # Only do this after another app (not the repl) has been made
        if(not UIScreen.first_run):
            if hasattr(tulip, "ui_quit_callback"):
                tulip.ui_quit_callback(self.screen_quit_callback)
            if hasattr(tulip, "ui_switch_callback"):
                tulip.ui_switch_callback(self.alttab_callback)
        UIScreen.first_run = False

    # Remove the elements you created
    def remove_items(self):
        things = self.screen.get_child_count()
        for i in range(things):
            if(self.screen.get_child(0) is not None):
                self.screen.get_child(0).delete()
        self.last_obj_added = None


# A base class for our UI elements -- will also move this into Tulip
class UIElement():
    # We make one temp screen for the elements to use, then add it to the UIScreen parent at add.
    temp_screen = lv.obj()

    def __init__(self, debug=False):
        self.group = lv.obj(UIElement.temp_screen)
        # Hot tip - set this to 1 if you're debugging why elements are not aligning like you think they should
        bw = 0
        if(debug): bw = 1
        self.group.set_style_border_width(bw, lv.PART.MAIN)
        self.group.remove_flag(lv.obj.FLAG.SCROLLABLE)

    def update_callbacks(self, cb):
        pass

    # Remove the elements you created (including the group)
    def remove_items(self):
        for i in range(self.group.get_child_count()):
            if(self.group.get_child(0) is not None):
                self.group.get_child(0).delete()
        self.group.delete()



# Callback for soft keyboard to send chars to Tulip.
def lv_soft_kb_cb(e):
    global lv_soft_kb, lv_last_mode
    kb = e.get_target_obj()
    button = kb.get_selected_button()
    text = kb.get_button_text(button)
    code = text[0]

    if(code==lv.SYMBOL.NEW_LINE): 
        tulip.key_send(13)
    elif(code==lv.SYMBOL.BACKSPACE): 
        if(lv_last_mode == kb.get_mode()): # there's a bug where the mode swticher sends BS
            tulip.key_send(8)
    elif(code==lv.SYMBOL.KEYBOARD):
        _close_keyboard()
        return
    elif(ord(code)==49): # special -- sends a "1" char even if just hit the mode switcher '1#'
        if(kb.get_mode() == lv_last_mode):  # only update after switching modes
            tulip.key_send(49)
    elif(len(text)==1 and ord(code)>31 and ord(code)<127): 
        tulip.key_send(ord(code))

    lv_last_mode = kb.get_mode()

# Starts or stops the soft keyboard
def keyboard():
    global lv_soft_kb, lv_last_mode
    if(lv_soft_kb is not None):
        _close_keyboard()
        return
    lv_soft_kb = lv.keyboard(current_lv_group())
    lv_soft_kb.add_event_cb(lv_soft_kb_cb, lv.EVENT.VALUE_CHANGED, None)
    lv_last_mode = lv_soft_kb.get_mode()

def _close_keyboard():
    global lv_soft_kb
    lv_soft_kb.delete()
    lv_soft_kb = None
    _repaint_repl_background()

# Paint over whatever was just removed from the REPL screen.
# The REPL screen's group is transparent whenever a repl_background() wallpaper
# is up, and lv_flush_cb_8b drops transparent pixels while that screen is loaded
# -- that is exactly what lets the wallpaper show through it. The cost is that
# LVGL can no longer erase itself there: a deleted object redraws as those same
# transparent pixels, so the ones it left in bg would stay forever. Without a
# wallpaper the group is opaque and LVGL erases itself, as on every other board.
def _repaint_repl_background():
    if _repl_background is not None:
        _draw_repl_background(None)

def _close_launcher():
    global lv_launcher
    lv_launcher.delete()
    lv_launcher = None
    _repaint_repl_background()

def launcher_cb(e):
    global lv_launcher
    if(e.get_code() == lv.EVENT.CLICKED):
        button = e.get_target_obj()
        text = button.get_child(1).get_text()
        _close_launcher()
        if(text=="Juno-6"):
            tulip.run('juno6')
        if(text=="Drums"):
            tulip.run('drums')
        if(text=='Voices'):
            tulip.run('voices')
        if(text=='Tulip World'):
            tulip.run('worldui')
        if(text=='Editor'):
            tulip.edit()
        if(text=="Keyboard"):
            keyboard()
        if(text=="Wordpad"):
            tulip.run("wordpad")
        if(text=="SSH"):
            tulip.run('sshterm')
        if(text=="Wi-Fi"):
            try:
                wifi()
            except NameError:
                print("Put a wifi() function in your boot.py")
        if(text=="Reset"):
            if(tulip.board()!="DESKTOP"):
                import machine
                machine.reset()


# Draw a lvgl list box as a little launcher
def launcher(ignore=True):
    global lv_launcher
    if(lv_launcher is not None):
        _close_launcher()
        return
    lv_launcher = lv.list(UIElement.temp_screen)
    lv_launcher.add_flag(lv.obj.FLAG.HIDDEN)
    launcher_height = tulip.screen_size()[1] * 2 // 3 if tulip.board() == "TAB5" else 140
    lv_launcher.set_size(_launcher_width_px, launcher_height)
    lv_launcher.set_align(lv.ALIGN.BOTTOM_RIGHT)
    lv_launcher.set_style_text_font(_launcher_font,0)
    b_close = lv_launcher.add_button(lv.SYMBOL.CLOSE, "Close")
    b_close.add_event_cb(launcher_cb, lv.EVENT.CLICKED, None)
    b_pattern = lv_launcher.add_button(lv.SYMBOL.HOME, "Tulip World")
    b_pattern.add_event_cb(launcher_cb, lv.EVENT.CLICKED, None)
    b_voices = lv_launcher.add_button(lv.SYMBOL.AUDIO, "Voices")
    b_voices.add_event_cb(launcher_cb, lv.EVENT.CLICKED, None)
    b_juno = lv_launcher.add_button(lv.SYMBOL.AUDIO, "Juno-6")
    b_juno.add_event_cb(launcher_cb, lv.EVENT.CLICKED, None)
    b_drums = lv_launcher.add_button(lv.SYMBOL.NEXT, "Drums")
    b_drums.add_event_cb(launcher_cb, lv.EVENT.CLICKED, None)
    b_editor = lv_launcher.add_button(lv.SYMBOL.FILE, "Editor")
    b_editor.add_event_cb(launcher_cb, lv.EVENT.CLICKED, None)
    b_keyboard = lv_launcher.add_button(lv.SYMBOL.KEYBOARD, "Keyboard")
    b_keyboard.add_event_cb(launcher_cb, lv.EVENT.CLICKED, None)
    b_wordpad = lv_launcher.add_button(lv.SYMBOL.FILE, "Wordpad")
    b_wordpad.add_event_cb(launcher_cb, lv.EVENT.CLICKED, None)
    b_wifi = lv_launcher.add_button(lv.SYMBOL.WIFI, "Wi-Fi")
    b_wifi.add_event_cb(launcher_cb, lv.EVENT.CLICKED, None)
    b_ssh = lv_launcher.add_button(lv.SYMBOL.DRIVE, "SSH")
    b_ssh.add_event_cb(launcher_cb, lv.EVENT.CLICKED, None)
    b_power = lv_launcher.add_button(lv.SYMBOL.POWER,"Reset")
    b_power.add_event_cb(launcher_cb, lv.EVENT.CLICKED, None)
    if tulip.board() == "TAB5":
        # Pad the rows out into finger-sized targets before measuring, so the
        # height below still lands on a whole button.
        for i in range(lv_launcher.get_child_count()):
            lv_launcher.get_child(i).set_style_pad_ver(10, lv.PART.MAIN)
        lv_launcher.update_layout()
        last_visible_button = lv_launcher.get_child(min(11, lv_launcher.get_child_count()) - 1)
        lv_launcher.set_height(last_visible_button.get_y() + last_visible_button.get_height())
    lv_launcher.set_parent(repl_screen.group)
    lv_launcher.set_align(lv.ALIGN.BOTTOM_RIGHT)
    lv_launcher.remove_flag(lv.obj.FLAG.HIDDEN)

# A tab view (that you can add other things to)
class TabView:
    def __init__(self, parent, tabs=[], position = lv.DIR.LEFT, size=100):
        self.parent = parent
        self.tabs = []
        self.tab_names = tabs
        self.tabview = lv.tabview(self.parent.group)
        self.tabview.set_tab_bar_position(position)
        self.tabview.set_tab_bar_size(size)

 
        for t in self.tab_names:
            self.tabs.append(self.tabview.add_tab(t))
        self.last_obj_added = None

        lv_depad(self.tabview, remove_scroll=True)
        lv_depad(self.tabview.get_content(), remove_scroll=True)
        lv_depad(self.tabview.get_tab_bar(), remove_scroll=True)
        for i in self.tabs:
            lv_depad(i, remove_scroll=True)



    def tab(self, name):
        return self.tabs[self.tab_names.index(name)]

    def add(self, name, obj, first_align=lv.ALIGN.TOP_LEFT, direction=lv.ALIGN.OUT_RIGHT_MID, relative=None, pad_x=0, pad_y=0, x=None, y=None):
        group = self.tab(name)

        if(relative is not None):
            self.last_obj_added = relative.group

        if(type(obj) != list): obj = [obj]
        for o in obj:
            o.group.set_parent(group)
            o.group.set_style_bg_color(pal_to_lv(self.parent.bg_color), lv.PART.MAIN)
            o.group.set_height(LV_SIZE_CONTENT)
            if(self.last_obj_added is None):
                o.group.align_to(group,first_align,self.parent.offset_x,self.parent.offset_y)
            else:
                try:
                    o.group.align_to(self.last_obj_added, direction,pad_x,pad_y)
                except lv.LvReferenceError:
                    self.last_obj_added = None
                    o.group.align_to(group,first_align,self.parent.offset_x,self.parent.offset_y)
            o.group.set_width(o.group.get_width()+pad_x)
            o.group.set_height(o.group.get_height()+pad_y)
            if(x is not None and y is not None): o.group.set_pos(x,y)
            self.last_obj_added = o.group



# A text entry widget w/ ok and cancel 
class TextEntry(UIElement):
    def __init__(self, label_text="", filled_text="", ok_callback=None, cancel_callback=None, bg_color=0, fg_color=255, **kwargs):
        super().__init__(**kwargs)
        self.group.set_size(420,80)
        self.box = lv.obj(self.group)
        self.box.set_style_bg_color(pal_to_lv(bg_color), lv.PART.MAIN)
        self.box.set_size(400,80)
        self.box.align_to(self.group, lv.ALIGN.CENTER,0,0)
        self.label = lv.label(self.box)
        self.label.set_style_text_color(pal_to_lv(fg_color),0)
        self.label.set_text(label_text)
        self.label.set_style_text_align(lv.TEXT_ALIGN.CENTER,0)
        self.label.align_to(self.box,lv.ALIGN.LEFT_MID,0,0)

        self.text = lv.textarea(self.box)
        self.text.set_size(150,45)
        self.text.add_text(filled_text)
        self.text.align_to(self.label, lv.ALIGN.OUT_RIGHT_MID,10,0)

        self.ok = lv.button(self.box)
        self.ok.set_style_bg_color(pal_to_lv(29), lv.PART.MAIN)
        ok_label = lv.label(self.ok)
        ok_label.set_style_text_font(lv.font_montserrat_12,0)
        ok_label.set_text(lv.SYMBOL.OK)
        ok_label.set_style_text_align(lv.TEXT_ALIGN.CENTER,0)
        self.ok.align_to(self.text, lv.ALIGN.OUT_RIGHT_MID,10,0)
        self.ok.add_event_cb(self.ok_callback, lv.EVENT.CLICKED, None)
        self.cancel = lv.button(self.box)
        self.cancel.set_style_bg_color(pal_to_lv(224), lv.PART.MAIN)
        cancel_label = lv.label(self.cancel)
        cancel_label.set_style_text_font(lv.font_montserrat_12,0)
        cancel_label.set_text(lv.SYMBOL.CLOSE)
        cancel_label.set_style_text_align(lv.TEXT_ALIGN.CENTER,0)
        self.cancel.align_to(self.ok, lv.ALIGN.OUT_RIGHT_MID,10,0)
        self.cancel.add_event_cb(self.cancel_callback, lv.EVENT.CLICKED, None)
        self.box.remove_flag(lv.obj.FLAG.SCROLLABLE)
        self.external_ok_callback = ok_callback
        self.external_cancel_callback = cancel_callback

    def ok_callback(self, e):
        if(self.external_ok_callback):
            self.external_ok_callback()
        self.remove_items()

    def cancel_callback(self, e):
        if(self.external_cancel_callback):
            self.external_cancel_callback()
        self.remove_items()



# bar_color - the color of the whole bar, or just the set part if using two colors
# unset_bar_color - the color of the unset side of the bar, if None will just be all one color
# handle_v_pad, h_pad -- how many px above/below / left/right of the bar it extends
# handle_radius - 0 for square 
class UISlider(UIElement):
    def __init__(self, val=0, w=None, h=None, bar_color=None, unset_bar_color=None, handle_color=None, handle_radius=None, 
        handle_v_pad=None, handle_h_pad=None, callback=None, **kwargs):

        super().__init__(**kwargs)
        self.slider = lv.slider(self.group)
        # Set opacity to full (COVER). Default is to mix the color with the BG.
        self.slider.set_style_bg_opa(lv.OPA.COVER, lv.PART.MAIN)
            
        if(bar_color is not None):
            self.slider.set_style_bg_color(pal_to_lv(bar_color), lv.PART.INDICATOR)
            if(unset_bar_color is None):
                self.slider.set_style_bg_color(pal_to_lv(bar_color), lv.PART.MAIN)
            else:
                self.slider.set_style_bg_color(pal_to_lv(unset_bar_color), lv.PART.MAIN)
        if(handle_color is not None):
            self.slider.set_style_bg_color(pal_to_lv(handle_color), lv.PART.KNOB)
        if(handle_radius is not None):
            self.slider.set_style_radius(handle_radius, lv.PART.KNOB)
        if(handle_v_pad is not None):
            self.slider.set_style_pad_ver(handle_v_pad, lv.PART.KNOB)
        if(handle_h_pad is not None):
            self.slider.set_style_pad_hor(handle_h_pad, lv.PART.KNOB)

        if(w is not None):
            self.slider.set_width(w)
            self.group.set_width(w+self.slider.get_style_pad_left(0)*2 + 20)
            self.slider.align(lv.ALIGN.CENTER,0,0)
        if(h is not None):
            self.slider.set_height(h)
            self.group.set_height(h+self.slider.get_style_pad_top(0)*2)
            self.slider.align(lv.ALIGN.CENTER,0,0)

        self.slider.set_value(int(val),LV_ANIM_OFF)

        if(callback is not None):
            self.slider.add_event_cb(callback, lv.EVENT.VALUE_CHANGED, None)

class UIButton(UIElement):
    def __init__(self, text=None, w=None, h=None, bg_color=None, fg_color=None, font=None, radius=None, callback=None, **kwargs):
        super().__init__(**kwargs)
        self.button = lv.button(self.group)
        if(w is not None):
            self.button.set_width(w)
            self.group.set_width(w+self.button.get_style_pad_left(0)*2)
            self.button.align(lv.ALIGN.CENTER,0,0)
        if(h is not None):
            self.button.set_height(h)
        if(radius is not None):
            self.button.set_style_radius(radius, lv.PART.MAIN)
        if(bg_color is not None):
            self.button.set_style_bg_color(pal_to_lv(bg_color), lv.PART.MAIN)
        if(text is not None):
            self.label = lv.label(self.button)
            self.label.set_text(text)
        if(font is not None):
            self.label.set_style_text_font(font, 0)
        self.label.set_style_text_align(lv.TEXT_ALIGN.CENTER,0)
        # if button width was manually set, we need to re-pad the text so it is centered
        if(w is not None):
            self.label.set_width(w-(self.button.get_style_pad_left(0)*2))
        if(fg_color is not None):
            self.label.set_style_text_color(pal_to_lv(fg_color), 0)
        if(callback is not None):
            self.button.add_event_cb(callback, lv.EVENT.CLICKED, None)

class UILabel(UIElement):
    def __init__(self, text, fg_color=None, w=None, font=None, **kwargs):
        super().__init__(**kwargs) 
        self.label = lv.label(self.group)
        self.label.set_text(text)
        if(w is not None):
            self.label.set_width(w)
            self.group.set_width(w)
        self.label.set_style_text_align(lv.TEXT_ALIGN.CENTER,0)
        if(font is not None):
            self.label.set_style_text_font(font, 0)
        if(fg_color is not None):
            self.label.set_style_text_color(pal_to_lv(fg_color),0)
        lv_depad(self.label)
        lv_depad(self.group)


# TODO -- get the kb_group back involved
class UIText(UIElement):
    def __init__(self, text=None, placeholder=None, w=None, h=None, bg_color=None, fg_color=None, font=None, one_line=True, callback=None, **kwargs):
        super().__init__(**kwargs)
        self.ta = lv.textarea(self.group)
        if(w is not None):
            self.ta.set_width(w)
        if(h is not None):
            self.ta.set_height(h)
        if(font is not None):
            self.ta.set_style_text_font(font, 0)
        if(bg_color is not None):
            self.ta.set_style_bg_color(pal_to_lv(bg_color), lv.PART.MAIN)
        if(fg_color is not None):
            self.ta.set_style_text_color(pal_to_lv(fg_color),0)
        if placeholder is not None:
            self.ta.set_placeholder_text(placeholder)
        if text is not None:
            self.ta.set_text(text)
        if(one_line): self.ta.set_one_line(True)
        if(callback is not None):
            self.ta.add_event_cb(callback, lv.EVENT.VALUE_CHANGED, None)
        if(w is not None):
            self.group.set_width(w+20)

class UICheckbox(UIElement):
    def __init__(self, text=None, val=False, bg_color=None, fg_color=None, callback=None, **kwargs):
        super().__init__(**kwargs)
        self.cb = lv.checkbox(self.group)
        if(text is not None):
            self.cb.set_text(text)
        if(bg_color is not None):
            self.cb.set_style_bg_color(pal_to_lv(bg_color), lv.PART.INDICATOR)
            self.cb.set_style_bg_color(pal_to_lv(bg_color), lv.PART.INDICATOR | lv.STATE.CHECKED)
        if(fg_color is not None):
            self.cb.set_style_border_color(pal_to_lv(fg_color), lv.PART.INDICATOR)
        self.cb.set_state(lv.STATE.CHECKED, val)
        if(callback is not None):
            self.cb.add_event_cb(callback, lv.EVENT.VALUE_CHANGED, None)


repl_screen = UIScreen("repl", bg_color=UIScreen.default_repl_bg_color, handle_keyboard=True)
repl_screen.present()

