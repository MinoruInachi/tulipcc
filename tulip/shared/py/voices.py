# voices.py
# lvgl MIDI patch setting + arpeggiator for Tulip.

import math

import tulip
import midi
import synth
import lvgl as lv
import amy
from patches import patches

LV_ANIM_OFF = lv.ANIM.OFF if hasattr(lv, 'ANIM') else False
LV_FLAG_HIDDEN = getattr(lv.obj.FLAG, 'HIDDEN', 1)
# LV_STATE_PRESSED is 0x0080. The fallback used to be 0, which is
# LV_STATE_DEFAULT -- if it ever fired it would silently mean "no state at all"
# rather than "pressed".
LV_STATE_PRESSED = getattr(lv.STATE, 'PRESSED', 0x0080)

COLOR_BG = 0
COLOR_PANEL = 9
COLOR_LIST = 36
COLOR_SCROLLBAR = 109
COLOR_ACCENT = 129
COLOR_TEXT = 255

# crox1h is 11px, which is about a millimetre on the Tab5's 7" panel. luRS18 is
# the same kind of 1-bit bitmap face a size up -- the antialiased montserrat
# fonts fringe blue here, because LVGL renders RGB565 and the compositor drops
# it to Tulip's 8-bit palette.
LIST_FONT = lv.font_tulip_15 if tulip.touch_first() else lv.font_tulip_11
# The headings sit over columns as narrow as 100px -- "polyphony" does not fit
# across one at 18px -- and they only name what is already obvious from the
# rows, so they stay a size down.
HEADING_FONT = lv.font_tulip_13 if tulip.touch_first() else lv.font_tulip_11


def _use_app_font(element):
    """Put the app's font on an element before anything measures it.

    These elements are built under UIElement.temp_screen and only re-parented
    into the app's group by UIScreen.add(). A font set on that group therefore
    arrives after every align_to() in here has already frozen a position from
    the default font's metrics -- which is how the arpeggiator switches ended up
    sitting on top of their own labels. Only on the boards that change the font
    at all; elsewhere the late inheritance is what the layout was drawn against.
    """
    if tulip.touch_first():
        element.group.set_style_text_font(LIST_FONT, 0)


def _selector(part, state=0):
    return part | state


def _style_text(obj):
    obj.set_style_text_color(tulip.pal_to_lv(COLOR_TEXT), 0)


# Keyboard geometry. This used to be a block of literals measured off a 1024x600
# Tulip CC -- right down to drawing the bottom of the white keys at y=599, the
# last row of that panel. Everything here reproduces those literals exactly at
# 1024x600 and spends a bigger panel on more octaves and taller keys instead of
# on margin: the Tab5's 1280x720 gets three octaves rather than two.
WHITE_KEY_W = 57
OCTAVE_W = 7 * WHITE_KEY_W
BLACK_KEY_W = 38
# Black keys within an octave, as offsets from that octave's leftmost white key.
# A real keyboard does not space them evenly and the original list did not
# either; it also drew the first octave two to five pixels right of the second,
# so that octave keeps its own row rather than being averaged away.
_BLACK_KEYS_FIRST_OCTAVE = (36, 105, 203, 269, 335)
_BLACK_KEYS_LATER_OCTAVE = (31, 101, 200, 265, 332)
_WHITE_KEY_SEMITONES = (0, 2, 4, 5, 7, 9, 11)
_BLACK_KEY_SEMITONES = (1, 3, 6, 8, 10)
# The leftmost key. C3, where this keyboard has always started.
BASE_NOTE = 48


def piano_geometry(screen_w, screen_h):
    """(x, y, w, h) for the keyboard: 2 octaves at 1024x600, 3 at 1280x720."""
    octaves = max(2, (screen_w - 80) // OCTAVE_W)
    w = octaves * OCTAVE_W
    # The original left 112px to the left of the keys and 114 to the right, so
    # the odd two pixels of the remainder belong on the right.
    x = (screen_w - w - 2) // 2
    # The keys have always run to the bottom edge and taken a bit under half the
    # height. A taller panel makes them taller rather than leaving a black band.
    h = 270 + max(0, screen_h - 600) // 3
    return (x, screen_h - h, w, h)


def redraw(app):
    # draw bg_x stuff, like the piano
    (app.screen_w, app.screen_h) = tulip.screen_size()
    # Since redraw is not within app.run() we are not guaranteed to be in the cwd of the app.
    # luckily, tulip.run() adds the cwd of the app to the app class before starting.
    (app.piano_x, app.piano_y, app.piano_w, app.piano_h) = piano_geometry(app.screen_w, app.screen_h)
    app.octaves = app.piano_w // OCTAVE_W
    tulip.bg_rect(app.piano_x,app.piano_y,app.piano_w,app.piano_h,255,1)
    app.white_key_w = WHITE_KEY_W
    for k in range(7 * app.octaves + 1):
        x = app.piano_x+(app.white_key_w*k)
        tulip.bg_line(x, app.piano_y, x, app.piano_y + app.piano_h - 1, 36)
    app.black_key_w = BLACK_KEY_W
    app.black_key_h = app.piano_h * 2 // 3
    app.black_key_starts = []
    for octave in range(app.octaves):
        offsets = _BLACK_KEYS_FIRST_OCTAVE if octave == 0 else _BLACK_KEYS_LATER_OCTAVE
        app.black_key_starts += [app.piano_x + octave * OCTAVE_W + o for o in offsets]
    # The note each key plays, in the same order as the keys are drawn above.
    app.white_key_notes = [s + 12 * o for o in range(app.octaves) for s in _WHITE_KEY_SEMITONES]
    app.black_key_notes = [s + 12 * o for o in range(app.octaves) for s in _BLACK_KEY_SEMITONES]
    for s in app.black_key_starts:
        tulip.bg_rect(s, app.piano_y, app.black_key_w, app.black_key_h, 0, 1)


class Settings(tulip.UIElement):
    def __init__(self, width=310, height=300):
        super().__init__()
        _use_app_font(self)
        self.group.set_size(width, height)
        self.group.remove_flag(lv.obj.FLAG.SCROLLABLE)
        self.label = lv.label(self.group)
        self.label.set_text("sequencer and arpeggiator")
        _style_text(self.label)
        self.rect = lv.obj(self.group)
        self.rect.set_style_bg_color(tulip.pal_to_lv(COLOR_PANEL), 0)
        self.rect.set_style_border_color(tulip.pal_to_lv(COLOR_LIST), 0)
        self.rect.set_style_border_width(1, 0)
        _style_text(self.rect)
        self.rect.remove_flag(lv.obj.FLAG.SCROLLABLE)

        self.rect.set_size(width-25,height-20)
        self.rect.align_to(self.label,lv.ALIGN.OUT_BOTTOM_LEFT,0,5)

        self.tempo = lv.slider(self.rect)
        self.tempo.set_style_bg_opa(lv.OPA.COVER, lv.PART.MAIN)
        # The one control on this screen you drag rather than tap, so it takes
        # whatever width the panel got over the 310 it was drawn at -- less the
        # room the BPM readout beside it needs at the larger font.
        self.tempo.set_width(160 + (width - 310) - tulip.touch_px(0, 45))
        self.tempo.set_style_bg_color(tulip.pal_to_lv(COLOR_TEXT), lv.PART.INDICATOR)
        self.tempo.set_style_bg_color(tulip.pal_to_lv(COLOR_TEXT), lv.PART.MAIN)
        self.tempo.set_style_bg_color(tulip.pal_to_lv(COLOR_ACCENT), lv.PART.KNOB)
        self.tempo.set_style_border_width(0, lv.PART.KNOB)
        self.tempo.align_to(self.rect, lv.ALIGN.TOP_LEFT,0,0)
        self.tempo_label = lv.label(self.rect)
        _style_text(self.tempo_label)
        self.tempo_label.align_to(self.tempo, lv.ALIGN.OUT_RIGHT_MID,10,0)
        self.tempo_cb_data = {}
        self.tempo.add_event_cb(self.tempo_cb, lv.EVENT.VALUE_CHANGED, self.tempo_cb_data)

        alabel = lv.label(self.rect)
        alabel.set_text("Arpeggiator:")
        _style_text(alabel)
        alabel.align_to(self.tempo, lv.ALIGN.OUT_BOTTOM_LEFT,0,30)
        self.arpegg = lv.switch(self.rect)
        self.arpegg.set_style_border_width(1, lv.PART.MAIN)
        self.arpegg.set_style_border_color(tulip.pal_to_lv(COLOR_ACCENT), lv.PART.MAIN)
        self.arpegg.align_to(alabel, lv.ALIGN.OUT_RIGHT_MID,10,0)
        self.arpegg_cb_data = {}
        self.arpegg.add_event_cb(self.arpegg_cb, lv.EVENT.VALUE_CHANGED, self.arpegg_cb_data)
        hlabel = lv.label(self.rect)
        hlabel.set_text("Hold:")
        _style_text(hlabel)
        hlabel.align_to(self.arpegg, lv.ALIGN.OUT_RIGHT_MID,10,0)
        self.hold = lv.switch(self.rect)
        self.hold.set_style_border_width(1, lv.PART.MAIN)
        self.hold.set_style_border_color(tulip.pal_to_lv(COLOR_ACCENT), lv.PART.MAIN)
        self.hold.align_to(hlabel, lv.ALIGN.OUT_RIGHT_MID,10,0)
        self.hold_cb_data = {}
        self.hold.add_event_cb(self.hold_cb, lv.EVENT.VALUE_CHANGED, self.hold_cb_data)

        for switch in (self.arpegg, self.hold):
            switch.set_style_bg_color(tulip.pal_to_lv(COLOR_LIST), lv.PART.MAIN)
            switch.set_style_bg_color(tulip.pal_to_lv(COLOR_ACCENT), lv.PART.INDICATOR)
            switch.set_style_bg_color(tulip.pal_to_lv(COLOR_TEXT), lv.PART.KNOB)
            switch.set_style_border_width(0, lv.PART.KNOB)

        self.mode = ListColumn("mode", ["Up", "Down", "U&D", "Rand"], width=130, height=160 + (height - 300), selected=0)
        self.mode.group.set_parent(self.rect)
        self.mode.group.set_style_bg_color(tulip.pal_to_lv(COLOR_PANEL),0)
        self.mode.group.align_to(alabel, lv.ALIGN.OUT_BOTTOM_LEFT, 0, 20)

        self.range = ListColumn("range", ["1", "2", "3"], width=130, height=160 + (height - 300), selected=0)
        self.range.group.set_parent(self.rect)
        self.range.group.set_style_bg_color(tulip.pal_to_lv(COLOR_PANEL),0)
        self.range.group.align_to(self.mode.group, lv.ALIGN.OUT_RIGHT_TOP, 10, 0)

        tulip.lv_depad(self.group)

    def update_from_arp(self, arp):
        """Configure arpegg UI to match current arpeggiator."""
        arpegg_state = self.arpegg.get_state() & lv.STATE.CHECKED
        if arp.active and not arpegg_state:
            self.arpegg.add_state(lv.STATE.CHECKED)
        elif not arp.active and arpegg_state:
            self.arpegg.remove_state(lv.STATE.CHECKED)
        hold_state = self.hold.get_state() & lv.STATE.CHECKED
        if arp.hold and not hold_state:
            self.hold.add_state(lv.STATE.CHECKED)
        elif not arp.hold and hold_state:
            self.hold.remove_state(lv.STATE.CHECKED)
        self.mode.select(['up', 'down', 'updown', 'rand'].index(arp.direction))
        self.range.select(arp.octaves - 1)

    @staticmethod
    def _bpm_to_percent(bpm):
        """Map 30..240 (log) to 0..100."""
        return int(round(33.33 * math.log2(bpm / 30)))

    @staticmethod
    def _percent_to_bpm(percent):
        """Map 0 to 100 to 30..240 (log)."""
        return int(round(30 * (2 ** (percent / 33.33))))

    def set_tempo(self, new_bpm):
        """Update UI when other mechanism changes bpm."""
        self.tempo.set_value(self._bpm_to_percent(new_bpm), LV_ANIM_OFF)
        self.tempo_label.set_text("%d BPM" % new_bpm)

    def tempo_cb(self, e):
        new_bpm = max(1, self._percent_to_bpm(self.tempo.get_value()))
        tulip.seq_bpm(new_bpm)
        self.tempo_label.set_text("%d BPM" % new_bpm)

    def hold_cb(self, e):
        if(self.hold.get_state()==3): 
            midi.arpeggiator.set('hold', True)
        else:
            midi.arpeggiator.set('hold', False)

    def arpegg_cb(self, e):
        if(self.arpegg.get_state()==3):
            midi.arpeggiator.set('active', True)
        else:
            midi.arpeggiator.set('active', False)


class ListColumn(tulip.UIElement):
    def __init__(self, name, items=None, selected=None, width=175, height=300, pool_size=0):
        super().__init__()
        self.name = name
        self.selected = selected
        _use_app_font(self)
        self.group.set_size(width,height)
        self.group.remove_flag(lv.obj.FLAG.SCROLLABLE)
        self.label = lv.label(self.group)
        if tulip.touch_first():
            self.label.set_style_text_font(HEADING_FONT, 0)
        self.label.set_text(name)
        _style_text(self.label)
        self.list = lv.list(self.group)
        #self.list.set_style_text_font(lv.font_tulip_11)
        self.list.set_size(width-25,height-20)
        self.list.align_to(self.label,lv.ALIGN.OUT_BOTTOM_LEFT,0,5)
        self.list.set_style_bg_color(tulip.pal_to_lv(COLOR_LIST), lv.PART.MAIN)
        self.list.set_style_border_width(0, lv.PART.MAIN)
        self.list.set_style_bg_color(tulip.pal_to_lv(COLOR_SCROLLBAR), lv.PART.SCROLLBAR)
        _style_text(self.list)
        self.buttons = []
        self.button_labels = []
        self.button_texts = []
        self.button_cb_data = {}
        tulip.lv_depad(self.list)
        tulip.lv_depad(self.group)
        tulip.lv_depad(self.label)
        self.replace_items(items)
        while len(self.buttons) < pool_size:
            button = self._add_button("")
            button.add_flag(LV_FLAG_HIDDEN)
        self.default_bg = COLOR_LIST
        if(self.selected is not None):
            self.buttons[self.selected].set_style_bg_color(tulip.pal_to_lv(COLOR_ACCENT), 0)

    def _add_button(self, text):
        button = self.list.add_button(None, text)
        label = button.get_child(0)
        label.set_long_mode(0)
        button.set_style_bg_color(tulip.pal_to_lv(COLOR_LIST), lv.PART.MAIN)
        button.set_style_bg_color(
            tulip.pal_to_lv(COLOR_ACCENT),
            _selector(lv.PART.MAIN, LV_STATE_PRESSED))
        button.set_style_border_width(0, lv.PART.MAIN)
        button.set_style_shadow_width(0, lv.PART.MAIN)
        _style_text(button)
        _style_text(label)
        button.add_event_cb(self.list_cb, lv.EVENT.CLICKED, self.button_cb_data)
        self.buttons.append(button)
        self.button_labels.append(label)
        return button

    def replace_items(self, items):
        if items is None:
            return
        items = list(items)
        while len(self.buttons) < len(items):
            self._add_button("")
        for idx, button in enumerate(self.buttons):
            if idx < len(items):
                self.button_labels[idx].set_text(items[idx])
                button.remove_flag(LV_FLAG_HIDDEN)
            else:
                button.add_flag(LV_FLAG_HIDDEN)
        self.button_texts = items
        if self.selected is not None and self.selected >= len(items):
            self.selected = None

    def select(self, index, defer=False):
        previous = self.selected
        if self.selected is not None and self.selected < len(self.buttons):
            self.buttons[self.selected].set_style_bg_color(tulip.pal_to_lv(self.default_bg), 0)
        self.selected = index
        if index is not None:
            self.buttons[self.selected].set_style_bg_color(tulip.pal_to_lv(COLOR_ACCENT), 0)
            if(self.name=='channel'):
                sync_ui_for_channel(int(self.button_texts[self.selected]))
            elif(self.name=='synth'):
                if self.selected != previous:
                    app.patchlist.select(None)
                    update_patches(self.button_texts[self.selected])
            elif(self.name=='mode'):
                mode = ['up', 'down', 'updown', 'rand'][index]
                midi.arpeggiator.set('direction', mode)
            elif(self.name=='range'):
                midi.arpeggiator.set('octaves', index + 1)
            else:
                if not defer:
                    update_map()

    def list_cb(self, e):
        button = e.get_target_obj()
        self.select(button.get_index())

def note_from_coord(app, x, y):
    """The note under (x, y), or None if that point is not on a key."""
    white_key_notes = app.white_key_notes
    black_key_notes = app.black_key_notes
    white_key = int((x-app.piano_x)/app.white_key_w)
    note_idx = None
    if y < app.piano_y + app.black_key_h:
        for i,black_x in enumerate(app.black_key_starts):
            if(x >= black_x and x < black_x+app.black_key_w):
                note_idx = black_key_notes[i]
                break
    if note_idx is None and white_key >= 0 and white_key < len(white_key_notes):
        note_idx = white_key_notes[white_key]
    if note_idx is None:
        return None
    return note_idx + BASE_NOTE


def hold_notes(app, notes):
    """Make `notes` exactly the set of notes the keyboard is holding down.

    A set difference rather than a note_on or note_off per touch event, because
    no touch driver ever says "this key was released". It reports the points
    that are currently down and renumbers them when one lifts, so letting go of
    one of two fingers, or sliding along the keys, arrives as an ordinary hold
    with a different set -- and anything held that is no longer in that set has
    to be turned off here or it sounds forever. Playing chords is exactly the
    case that used to leave notes stuck: only the last point to lift got its
    note_off, and every other finger's note was left on.

    held_note remembers which channel each note_on went to, so a note started
    before a channel change is still released on the synth that is playing it.

    Only channel 1 (and the drums on 10) has a synth until a patch is picked for
    it, so selecting any other channel and touching a key found no synth at all:
    get_synth() returns None and every touch raised AttributeError. A channel
    with nothing on it stays silent instead, and a note is only recorded as held
    once it has actually started, so letting go of it has nothing to release.
    The note_off side checks too, in case the synth went away while held.
    """
    for note in [n for n in app.held_note if n not in notes]:
        synth = midi.config.get_synth(app.held_note.pop(note))
        if synth is not None:
            synth.note_off(note)
        #tulip.midi_local((128+app.channels.selected, note, 127))
    if notes:
        channel = int(app.channels.button_texts[app.channels.selected])
        synth = midi.config.get_synth(channel)
        if synth is None:
            return
        for note in notes:
            if note not in app.held_note:
                app.held_note[note] = channel
                synth.note_on(note, 1)
                #tulip.midi_local((144+app.channels.selected, note, 127))


def touch(up):
    global app
    notes = set()
    if not up:
        coords = tulip.touch()
        for i in range(3):
            # An unused touch slot reads -1, which is left of the keyboard and
            # so fails the bounds test below on its own.
            (x, y) = (coords[i*2], coords[i*2+1])
            if(x >= app.piano_x and x <= app.piano_x+app.piano_w and y >= app.piano_y and y <= app.piano_y+app.piano_h):
                note = note_from_coord(app, x, y)
                if note is not None:
                    notes.add(note)
    hold_notes(app, notes)

def process_key(key):
    global app
    # play kb notes from keyboard?
    pass


def deferred_bg_redraw(t):
    global app
    redraw(app)

def quit(screen):
    pass
    
def activate(screen):
    # Synchronize the patch selector item in case editor changed patch.
    if app.channels.selected is not None:
        sync_ui_for_channel(int(app.channels.button_texts[app.channels.selected]))
    if tulip.board() == "TAB5":
        lv.refr_now(lv.display_get_default())
        redraw(app)
    else:
        tulip.defer(deferred_bg_redraw, None, 250)
    # start listening to the keyboard again
    tulip.keyboard_callback(process_key)
    tulip.touch_callback(touch)
    hold_notes(screen, set())

def deactivate(screen):
    # i am being switched away -- keep running but clear and close any active callbacks
    # Whatever was down when the switch happened has no touch up coming, so it
    # gets its note_off here instead. Runs on the quit path too: screen_quit_callback()
    # in ui.py calls deactivate before quit.
    hold_notes(screen, set())
    tulip.bg_clear()
    tulip.keyboard_callback()
    tulip.touch_callback()

# actually make the change in our midi map
def update_map():
    global app
    # channels guaranteed to always be selected
    if(app.patchlist.selected is not None and app.polyphony.selected is not None and app.synths.selected is not None):
        patch_no = app.patchlist.selected
        if(app.synths.selected == 1): patch_no += 128
        if(app.synths.selected == 2): patch_no += 256
        if(app.synths.selected == 3): patch_no += 1024
        channel = int(app.channels.button_texts[app.channels.selected])
        polyphony = app.polyphony.selected + 1
        # Check if this is a new thing
        channel_patch, channel_polyphony = midi.config.channel_info(channel)
        if (channel_patch, channel_polyphony) != (patch_no, polyphony):
            midi.config.add_synth(channel=channel, synth=synth.PatchSynth(patch=patch_no, num_voices=polyphony))


# populate the patches dialog from patches.py
def update_patches(synth):
    global app
    if(synth=='DX7'):
        app.patchlist.replace_items(patches[128:256])
    if(synth=='Juno-6'):
        app.patchlist.replace_items(patches[0:128])
    if(synth=='Custom'):
        app.patchlist.replace_items([("Custom %d" % x) for x in range(32)])
    if(synth=='Misc'):
        app.patchlist.replace_items([patches[256]])
    app.patchlist.label.set_text("%s patches" % (synth))

def sync_ui_for_channel(channel):
    """Synchronize the UI to the state for this channel per midi.py."""
    global app
    channel_patch, polyphony = midi.config.channel_info(channel)
    if channel_patch is not None:
        if channel_patch < 128:
            # We defer here so that setting the UI component doesn't trigger an update before it updates
            app.synths.select(0, defer=True)
            app.patchlist.select(channel_patch, defer=True)
        elif channel_patch < 256:
            app.synths.select(1, defer=True)
            app.patchlist.select(channel_patch - 128, defer=True)
        elif channel_patch < 1024:
            app.synths.select(2, defer=True)
            app.patchlist.select(channel_patch - 256, defer=True)
        else:
            app.synths.select(3, defer=True)
            app.patchlist.select(channel_patch - 1024, defer=True)
        app.polyphony.select(polyphony - 1, defer=True)
    else:
        # no patch set for this chanel
        app.patchlist.select(None)
        app.polyphony.select(None)


def run(screen):
    global app 
    app = screen # we can use the screen obj passed in as a general "store stuff here" class, as well as inspect the UI
    # note -> the channel its note_on went to. Set up before present(), which
    # activates the screen and so calls hold_notes().
    app.held_note = {}
    # The keyboard along the bottom is drawn on the BG plane, so where LVGL is
    # composited on top of it (the Tab5) this screen's background has to stay out
    # of the way -- UIScreen paints COLOR_BG on the BG plane instead. Set before
    # set_bg_color, which is what applies it.
    app.bg_plane = True
    app.set_bg_color(COLOR_BG)
    app.offset_y = 25
    app.offset_x = 50
    app.quit_callback = quit
    app.activate_callback = activate
    app.deactivate_callback = deactivate
    app.group.set_style_text_font(LIST_FONT, 0)
    _style_text(app.group)

    # The columns fill whatever is above the keyboard, and the width this panel
    # has over 1024 is split between the two columns that can use it: the patch
    # names, the only ones that ever run out of room, and the sequencer box.
    (_, piano_y, _, _) = piano_geometry(*tulip.screen_size())
    list_h = piano_y - app.offset_y - 5
    extra_w = tulip.screen_extra_w()

    # Skip 10, drums
    app.channels = ListColumn('channel',["1","2","3","4","5","6","7","8","9","11","12","13","14","15","16"], selected=0, width=100, height=list_h)
    app.add(app.channels, direction=lv.ALIGN.OUT_BOTTOM_LEFT)

    app.synths = ListColumn('synth', ["Juno-6", "DX7", "Misc", "Custom"], height=list_h)
    app.add(app.synths)

    app.patchlist = ListColumn('patches', pool_size=128, width=175 + extra_w // 2, height=list_h)
    app.add(app.patchlist)

    app.polyphony = ListColumn('polyphony', [str(x+1) for x in range(8)], width=100, height=list_h)
    app.add(app.polyphony)

    app.settings = Settings(width=310 + extra_w // 2, height=list_h)
    app.settings.update_from_arp(midi.arpeggiator)
    app.settings.set_tempo(tulip.seq_bpm())

    app.add(app.settings, pad_x=0)

    sync_ui_for_channel(1)

    app.present()
