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


def _selector(part, state=0):
    return part | state


def _style_text(obj):
    obj.set_style_text_color(tulip.pal_to_lv(COLOR_TEXT), 0)


def redraw(app):
    # draw bg_x stuff, like the piano
    (app.screen_w, app.screen_h) = tulip.screen_size()
    # Since redraw is not within app.run() we are not guaranteed to be in the cwd of the app. 
    # luckily, tulip.run() adds the cwd of the app to the app class before starting.
    app.piano_x = 112
    app.piano_y = 330
    app.piano_w = 798
    app.piano_h = 270
    tulip.bg_rect(app.piano_x,app.piano_y,app.piano_w,app.piano_h,255,1)
    app.white_key_w = 57
    for k in range(15):
        x = app.piano_x+(app.white_key_w*k)
        tulip.bg_line(x, app.piano_y, x, 599, 36)
    app.black_key_w = 38
    app.black_key_h = 180
    app.black_key_starts = [148, 217, 315, 381, 447, 542, 612, 711, 776, 843]
    for s in app.black_key_starts:
        tulip.bg_rect(s, app.piano_y, app.black_key_w, app.black_key_h, 0, 1)    


class Settings(tulip.UIElement):
    def __init__(self, width=310, height=300):
        super().__init__()
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
        self.tempo.set_width(160)
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

        self.mode = ListColumn("mode", ["Up", "Down", "U&D", "Rand"], width=130, height=160, selected=0)
        self.mode.group.set_parent(self.rect)
        self.mode.group.set_style_bg_color(tulip.pal_to_lv(COLOR_PANEL),0)
        self.mode.group.align_to(alabel, lv.ALIGN.OUT_BOTTOM_LEFT, 0, 20)

        self.range = ListColumn("range", ["1", "2", "3"], width=130, height=160, selected=0)
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
        self.group.set_size(width,height)
        self.group.remove_flag(lv.obj.FLAG.SCROLLABLE)
        self.label = lv.label(self.group)
        #self.label.set_style_text_font(lv.font_tulip_11)
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

def play_note_from_coord(app, x, y, up):
    white_key_notes = [0,2,4,5,7,9,11,12,14,16,17,19,21,23]
    black_key_notes = [1,3,6,8, 10, 13, 15, 18, 20, 22]
    white_key = int((x-app.piano_x)/app.white_key_w)
    note_idx = None
    if y < app.piano_y + app.black_key_h:
        for i,black_x in enumerate(app.black_key_starts):
            if(x >= black_x and x < black_x+app.black_key_w):
                note_idx = black_key_notes[i]
                break
    if note_idx is None and white_key >= 0 and white_key < len(white_key_notes):
        note_idx = white_key_notes[white_key]
    if note_idx is not None:
        channel = int(app.channels.button_texts[app.channels.selected])
        note = note_idx + 48
        if(up):
            app.held_note[note] = False
            midi.config.get_synth(channel).note_off(note)
            #tulip.midi_local((128+app.channels.selected, note, 127))
        else:
            if app.held_note.get(note, False) == False:
                app.held_note[note] = True
                midi.config.get_synth(channel).note_on(note, 1)
                #tulip.midi_local((144+app.channels.selected, note, 127))



def touch(up):
    global app
    x,y = [-1,-1,-1], [-1,-1,-1]
    (x[0],y[0],x[1],y[1],x[2],y[2]) = tulip.touch()
    for i in range(3):
        if(x[i] >= app.piano_x and x[i] <= app.piano_x+app.piano_w and y[i] >= app.piano_y and y[i] <= app.piano_y+app.piano_h):
            play_note_from_coord(app, x[i], y[i], up)

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
    screen.held_note = {}

def deactivate(screen):
    # i am being switched away -- keep running but clear and close any active callbacks 
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
    app.set_bg_color(COLOR_BG)
    app.offset_y = 25
    app.offset_x = 50
    app.quit_callback = quit
    app.activate_callback = activate
    app.deactivate_callback = deactivate
    app.group.set_style_text_font(lv.font_tulip_11, 0)
    _style_text(app.group)

    # Skip 10, drums
    app.channels = ListColumn('channel',["1","2","3","4","5","6","7","8","9","11","12","13","14","15","16"], selected=0, width=100)
    app.add(app.channels, direction=lv.ALIGN.OUT_BOTTOM_LEFT)

    app.synths = ListColumn('synth', ["Juno-6", "DX7", "Misc", "Custom"])
    app.add(app.synths)

    app.patchlist = ListColumn('patches', pool_size=128)
    app.add(app.patchlist)

    app.polyphony = ListColumn('polyphony', [str(x+1) for x in range(8)], width=100)
    app.add(app.polyphony)
    
    app.settings = Settings()
    app.settings.update_from_arp(midi.arpeggiator)
    app.settings.set_tempo(tulip.seq_bpm())

    app.add(app.settings, pad_x=0)

    sync_ui_for_channel(1)

    app.present()
