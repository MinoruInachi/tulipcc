# worldui.py

import lvgl as lv
import tulip, time
(H_RES,V_RES) = tulip.screen_size()
if(tulip.board()=='WEB'):
    import world_web as world
else:
    import world

app = None
TIME_BETWEEN_CHECKS_S = 60
# LVGL 9.5 made LV_COORD_MAX a macro rather than an enum member -- gen_mpy.py
# only exports enums, so there is no lv.COORD on a 9.5 binding and this fallback
# is what actually runs on the Tab5. It has to be LVGL's own value:
# (1 << LV_COORD_TYPE_SHIFT) - 1, shift 29. The 0x7FFFFFFF it used to be was not
# merely too large -- LVGL reads bits 29-30 as a type tag, and 0x7FFFFFFF tags as
# LV_COORD_TYPE_PX_NEG, so "scroll to the bottom" below was not even a plain
# pixel count.
LV_COORD_MAX = lv.COORD.MAX if hasattr(lv, 'COORD') else (1 << 29) - 1

# The file and message panes are column-aligned text -- the timestamp, the name
# and the body only line up because every glyph is the same width -- so a bigger
# font here has to stay monospace. unscii_16 is the only one built in, and it is
# 16x16 against unscii_8's 8x8: twice the size in both directions, so the longest
# messages now wrap where they used to fit. Worth it. At 8px a line of this is
# under a millimetre tall on the Tab5's 7" panel.
_BODY_FONT = lv.font_unscii_16 if tulip.touch_first() else lv.font_unscii_8
# The headings stay on a 1-bit bitmap face for the same reason everything else
# on Tulip does: LVGL renders into RGB565 and the compositor drops that to the
# 8-bit palette, so an antialiased glyph edge comes out as blue speckle.
_LABEL_FONT = lv.font_tulip_15 if tulip.touch_first() else lv.font_tulip_13
# Pane heights, in the order they stack. The file list keeps eight rows at either
# font size, and the message log gets the height the panel has over the 600 these
# were drawn for, less what the rest of this takes. (The entry box asks for more
# than it gets -- a one-line textarea sizes itself to its font -- but the taller
# face still leaves it a 50px target, which is a fingertip.)
FILES_H = tulip.touch_px(120, 152)
ENTRY_H = tulip.touch_px(60, 76)
MESSAGES_H = (280 + tulip.screen_extra_h()
              - (FILES_H - 120) - (ENTRY_H - 60) - tulip.touch_px(0, 40))
# A heading is aligned above its pane, so it lives in the group's top padding.
# It needs 24px for the label itself -- the default padding is 20, which cropped
# the tops of the letters at the larger face, because Tulip's bitmap faces draw
# above their own box (get_lvgl_font_from_tulip() in shared/lvgl_u8g2.c leaves
# base_line at 0) -- and 8 more to clear the pane, which overflows its own box
# by a few rows when it scrolls to its newest line.
PANE_PAD_TOP = tulip.touch_px(20, 38)
HEADING_LIFT = tulip.touch_px(0, -8)
BG_COLOR = 13

def check_messages(x=None):
    global app

    def done(messages):
        messages.reverse()
        text = ""
        for i in messages:
            nt = world.nice_time(i['age_ms'])
            text = text + "\n["+ nt +"] "+ i['username'] +": " +i['content']
        app.messages.ta.set_text(text)
        app.messages.ta.scroll_to_y(LV_COORD_MAX, 0)

    # Different paths for web and normal world
    if(tulip.board()=="WEB"):
        world.grab("messages", n=25, mtype='text').then(lambda x: done(x))
    else:            
        messages = world.messages(n=25)
        done(messages)

def check_files():
    global app
    def done(files):
        text = ""
        files.reverse()
        for i in files:
            nt = world.nice_time(i['age_ms'])
            fn = i['filename']
            if(fn.endswith('.tar')): fn = fn[:-4]
            text = text + "\n["+ nt +"] "+ i['username'] +": " + fn + " (" +i['content'] + ")"
        app.files.ta.set_text(text)
        app.files.ta.scroll_to_y(LV_COORD_MAX, 0)

    if(tulip.board()=="WEB"):
        world.unique_files(count=12).then(lambda x: done(x))
    else:
        files = world.unique_files(count=12)
        done(files)


def check():
    check_files()
    check_messages()

def checker(x):
    global app
    if(tulip.ticks_ms() > app.last_check_ms + (TIME_BETWEEN_CHECKS_S*1000)):
        app.last_check_ms = tulip.ticks_ms()
        check()
        

def activate(screen):
    tulip.frame_callback(checker)

def deactivate(screen):
    tulip.frame_callback()

def quit(screen):
    pass

def enter_cb(e):
    global app
    def done(x):
        app.entry.ta.set_text("")
        tulip.defer(check_messages, None, 5000)

    text = app.entry.ta.get_text()
    if(len(text)>1 and world.username is not None):
        if(tulip.board()=="WEB"):
            world.post_message(text).then(lambda x:done(x))
        else:
            world.post_message(text)
            done(None)


class TextEntry(tulip.UIElement):
    def __init__(self, h, bgcolor=255):
        super().__init__()
        self.ta = lv.textarea(self.group)
        self.group.set_size(H_RES-40,h)
        self.ta.set_size(H_RES-80, h)
        self.ta.set_style_text_font(_LABEL_FONT, 0)
        self.ta.set_style_bg_color(tulip.pal_to_lv(bgcolor), lv.PART.MAIN)
        self.ta.set_style_text_color(tulip.pal_to_lv(255),0)
        self.ta.set_style_border_color(tulip.pal_to_lv(255), lv.PART.CURSOR | lv.STATE.FOCUSED)
        self.ta.set_placeholder_text("Type a message....")
        self.ta.remove_flag(lv.obj.FLAG.SCROLLABLE)
        self.ta.set_one_line(True)
        self.enter_cb_data = {}
        self.ta.add_event_cb(enter_cb, lv.EVENT.READY, self.enter_cb_data)
        lv.group_focus_obj(self.ta)

class TextSection(tulip.UIElement):
    def __init__(self, h, name, bgcolor=255):
        super().__init__()
        self.ta = lv.label(self.group)
        if tulip.touch_first():
            # Only where the heading needs the room -- elsewhere leave the theme
            # to supply the padding it always has, rather than assert a number.
            self.group.set_style_pad_top(PANE_PAD_TOP, 0)
        self.group.set_size(H_RES-40,h)
        self.ta.set_size(H_RES-80, h)
        self.ta.set_style_text_font(_BODY_FONT, 0)
        self.ta.set_style_bg_color(tulip.pal_to_lv(bgcolor), lv.PART.MAIN)
        self.ta.set_style_text_color(tulip.pal_to_lv(255),0)
        self.ta.set_style_border_color(tulip.pal_to_lv(0), lv.PART.CURSOR | lv.STATE.FOCUSED)

        self.label = lv.label(self.group)
        self.label.set_style_text_font(_LABEL_FONT, 0)
        if tulip.touch_first():
            # The pane scrolls to its newest line, so its top row is cut part
            # way through wherever the wrapped lines above it happen to end, and
            # that sliver lands right behind this heading. Paint over it rather
            # than leave half a message crossing the words.
            self.label.set_style_bg_color(tulip.pal_to_lv(BG_COLOR), 0)
            self.label.set_style_bg_opa(lv.OPA.COVER, 0)
            # Across the whole pane, not just behind the words: the sliver runs
            # the full width and the heading is only a few characters of it.
            self.label.set_width(H_RES - 80)
        self.label.set_text(name)
        self.label.set_style_text_color(tulip.pal_to_lv(255),0)
        self.label.align_to(self.ta, lv.ALIGN.OUT_TOP_LEFT, 0, HEADING_LIFT)

def run(screen):
    global app
    if tulip.ip() is None:
        print("Needs wifi")
        screen.quit()
        return
    if(world.username is None):
        print("Type world.username='username' first.")
        screen.quit()
        return

    app = screen

    app.last_check_ms = -(TIME_BETWEEN_CHECKS_S*1000)
    

    screen.set_bg_color(BG_COLOR)
    screen.quit_callback = quit
    screen.activate_callback = activate
    screen.deactivate_callback = deactivate
    screen.handle_keyboard=True
    screen.offset_y = 30
    app.files = TextSection(FILES_H, "Latest files. Use world.download(name) in the REPL to get them.", bgcolor=35)
    app.messages = TextSection(MESSAGES_H, "Latest messages", bgcolor=0)
    app.entry = TextEntry(ENTRY_H, bgcolor=0)

    screen.add(app.files, direction=lv.ALIGN.OUT_BOTTOM_LEFT)
    screen.add(app.messages, direction=lv.ALIGN.OUT_BOTTOM_LEFT, pad_y=0)
    screen.add(app.entry, direction=lv.ALIGN.OUT_BOTTOM_LEFT, pad_y=0)
    screen.present()




