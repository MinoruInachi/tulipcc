"""kanplay -- a one-finger chord instrument for Tulip, in the manner of
InstaChord's KANTAN Play. Press a scale degree (1..7) and the current song's
parts play that chord: strummed guitar, piano arpeggio, bass, drums.

    tulip.run("kanplay")

Keyboard (USB or built-in), laid out like the 15 KANTAN play buttons:

    7 8 9        u  i        8/9 held with a root = flat/sharp
    4 5 6        j  k        u dim   j 7    m swap(maj<->min)
    1 2 3        m  ,        i sus4  k M7   , add9
    space = stop   - / = key   [ / ] slot   < / > song   tab = manual/auto

The same pads are on screen for touch. Song files are KANTAN Play Core JSON
(see kp_song.py); drop your own in /user/kanplay/. See README.md.
"""
import os

import lvgl as lv
import tulip
from ui import UIScreen, UIElement, UILabel, UIButton, pal_to_lv

import kp_chords
import kp_engine
import kp_input
import kp_song
import kp_tones

APP_DIR = os.getcwd()
SONG_DIRS = [APP_DIR + "/songs", tulip.root_dir() + "user/kanplay"]

ROOT_LAYOUT = [["7", "8", "9"], ["4", "5", "6"], ["1", "2", "3"]]
MOD_LAYOUT = [["dim", "sus4"], ["7th", "M7"], ["swap", "add9"]]
PAD_TEXT = {"8": "8 b", "9": "9 #", "swap": "~", "7th": "7"}

# Palette indices (RGB332): pads, lit pads, text.
COL_ROOT, COL_ROOT_LIT = 0x25, 0xfc
COL_ACC, COL_ACC_LIT = 0x49, 0xfc
COL_MOD, COL_MOD_LIT = 0x06, 0xfc
COL_TEXT = 0xff
COL_PART_ON, COL_PART_OFF = 0x06, 0x49
USER_DIR = tulip.root_dir() + "user/kanplay"

app = None


class Pad(UIElement):
    """A big pad. It is drawn by LVGL but not clicked through it: LVGL's
    input device follows one finger only, so the app hit-tests every point
    of tulip.touch() against the pads itself (KanPlay._touch_pads) and can
    see a chord-type pad and a root pad held together."""

    def __init__(self, text, button_id, inputs, w, h, color, lit_color, font):
        super().__init__()
        self.button_id = button_id
        self.inputs = inputs
        self.color = color
        self.lit_color = lit_color
        self.group.set_style_pad_all(0, lv.PART.MAIN)
        self.group.set_size(w, h)
        self.button = lv.button(self.group)
        self.button.set_size(w, h)
        self.button.align(lv.ALIGN.TOP_LEFT, 0, 0)
        self.button.set_style_radius(12, lv.PART.MAIN)
        self.button.set_style_bg_color(pal_to_lv(color), lv.PART.MAIN)
        self.button.set_style_bg_color(pal_to_lv(color), lv.PART.MAIN | lv.STATE.PRESSED)
        self.label = lv.label(self.button)
        self.label.set_text(text)
        self.label.set_style_text_font(font, 0)
        self.label.set_style_text_color(pal_to_lv(COL_TEXT), 0)
        self.label.center()
        self.button.remove_flag(lv.obj.FLAG.CLICKABLE)

    def rect(self):
        """Absolute screen rectangle (x1, y1, x2, y2), valid once laid out."""
        a = lv.area_t()
        self.button.get_coords(a)
        return (a.x1, a.y1, a.x2, a.y2)

    def set_lit(self, lit):
        # Every style write invalidates the pad, so skip the ones that would
        # not change anything (see KanPlay.refresh for why that matters).
        lit = bool(lit)
        if getattr(self, "lit", None) == lit:
            return
        self.lit = lit
        c = pal_to_lv(self.lit_color if lit else self.color)
        self.button.set_style_bg_color(c, lv.PART.MAIN)
        self.button.set_style_bg_color(c, lv.PART.MAIN | lv.STATE.PRESSED)


class PartCell(UIElement):
    """One part of the current slot: an on/off button carrying the
    instrument name, and < > to change the instrument."""

    def __init__(self, index, app, w, font):
        super().__init__()
        self.index = index
        self.group.set_style_pad_all(0, lv.PART.MAIN)
        self.group.set_size(w, 84)
        self.toggle = lv.button(self.group)
        self.toggle.set_size(w, 40)
        self.toggle.align(lv.ALIGN.TOP_LEFT, 0, 0)
        self.toggle.set_style_radius(6, lv.PART.MAIN)
        self.toggle.set_style_pad_left(4, lv.PART.MAIN)
        self.toggle.set_style_pad_right(4, lv.PART.MAIN)
        self.label = lv.label(self.toggle)
        self.label.set_style_text_font(lv.font_tulip_13, 0)
        self.label.set_style_text_color(pal_to_lv(COL_TEXT), 0)
        self.label.center()
        self.toggle.add_event_cb(lambda e: app.on_button("part%d" % index, True), lv.EVENT.CLICKED, None)
        half = (w - 4) // 2
        for k, (text, name) in enumerate((("<", "tone%d-" % index), (">", "tone%d+" % index))):
            b = lv.button(self.group)
            b.set_size(half, 36)
            b.align(lv.ALIGN.TOP_LEFT, k * (half + 4), 46)
            l = lv.label(b)
            l.set_text(text)
            l.set_style_text_font(font, 0)
            l.center()
            b.add_event_cb((lambda n: lambda e: app.on_button(n, True))(name), lv.EVENT.CLICKED, None)

    def show(self, part, voices=None, wanted=None):
        state = "" if part.plays() else (" (off)" if not part.enabled else " (-)")
        if part.plays() and voices and wanted and voices < wanted:
            state = " %d/%dv" % (voices, wanted)
        text = "%d %s%s" % (self.index + 1, kp_tones.name_for(part.tone), state)
        c = COL_PART_ON if part.plays() else COL_PART_OFF
        if (text, c) == getattr(self, "shown", None):
            return
        self.shown = (text, c)
        self.label.set_text(text)
        self.toggle.set_style_bg_color(pal_to_lv(c), lv.PART.MAIN)


class SongPicker(UIElement):
    """The song list as an LVGL dropdown. Its list opens over the button
    column, clear of the pads, so the app's own pad hit-testing never sees
    a tap meant for it."""

    def __init__(self, app, w, h, font, list_h):
        super().__init__()
        self.group.set_style_pad_all(0, lv.PART.MAIN)
        self.group.set_size(w, h)
        self.dropdown = lv.dropdown(self.group)
        # Fonts before sizing: a dropdown measures itself against its font,
        # and the list is what a fingertip then has to hit. The arrow is a
        # symbol glyph the Tulip fonts do not have, so it keeps a Montserrat.
        self.dropdown.set_style_text_font(font, 0)
        self.dropdown.set_style_text_font(font, lv.PART.SELECTED)
        self.dropdown.set_style_text_font(lv.font_montserrat_18, lv.PART.INDICATOR)
        lst = self.dropdown.get_list()
        lst.set_style_text_font(font, 0)
        lst.set_style_max_height(list_h, 0)     # the theme's 260 px shows six songs
        self.dropdown.set_size(w, h)
        self.dropdown.align(lv.ALIGN.TOP_LEFT, 0, 0)
        self.dropdown.set_dir(lv.DIR.BOTTOM)
        self.dropdown.add_event_cb(lambda e: app.on_song_picked(self.dropdown.get_selected()),
                                   lv.EVENT.VALUE_CHANGED, None)
        self.shown = None

    def set_songs(self, names):
        self.dropdown.set_options("\n".join(names) if names else "(no songs)")
        self.shown = None

    def select(self, index):
        # set_selected redraws the box, so only when it changed.
        if index != self.shown:
            self.shown = index
            self.dropdown.set_selected(index)


class KanPlay:
    def __init__(self, screen):
        self.screen = screen
        # No synth.PatchSynth.reset() here (technopop does one): its
        # amy.reset() would also wipe the synths and sequences of whatever
        # else is running, such as drums. The parts build their own synths on
        # fixed numbers (kp_tones.SYNTH_BASE), and the overload failsafe
        # resets AMY by itself, so nothing needs a clean slate.
        self.player = kp_engine.Player()
        self.player.on_change = self._mark_dirty
        self.inputs = kp_input.Inputs(listener=self.on_button)
        self.pads = {}
        self.pad_rects = {}
        self.cells = []
        self.frames = 0
        self.status = ""
        self.touch_down = False
        self.shown_text = {}       # widget id -> last text written (see _set_text)
        self.songs = self._find_songs()
        self.song_index = 0
        self.dirty = True
        self.closed = False
        self._build()
        self._load_song(0)

    # --- songs ----------------------------------------------------------

    def _find_songs(self):
        out = []
        for d in SONG_DIRS:
            try:
                names = sorted(os.listdir(d))
            except OSError:
                continue
            out += [d + "/" + n for n in names if n.endswith(".json")]
        return out

    def _song_names(self):
        """Dropdown entries: the file names, user songs marked (user) since
        Save writes them under the same name as the bundled one."""
        names = []
        for path in self.songs:
            d, n = path.rsplit("/", 1)
            n = n[:-5]
            if d == USER_DIR:        # not APP_DIR/songs, which is under it when run from /user
                n += " (user)"
            names.append(n)
        return names

    def _load_song(self, index):
        if not self.songs:
            self.player.load_song(kp_song.blank())
            return
        self.song_index = index % len(self.songs)
        path = self.songs[self.song_index]
        try:
            song = kp_song.load(path)
        except Exception as e:
            print("kanplay: cannot load %s: %s" % (path, e))
            song = kp_song.blank()
        self.player.load_song(song)
        self.dirty = True

    # --- UI -------------------------------------------------------------

    def _build(self):
        W, H = tulip.screen_size()
        big = H >= 700
        pad = 118 if big else 100
        gap = 10 if big else 8
        x0, y0 = 40, 100 if big else 90
        font_pad = lv.font_montserrat_36 if big else lv.font_montserrat_24
        font_ui = lv.font_tulip_15
        s = self.screen
        # A drag anywhere on the screen would otherwise scroll the whole page
        # (LVGL's elastic over-scroll shows even when everything fits).
        s.group.remove_flag(lv.obj.FLAG.SCROLLABLE)
        s.group.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)

        for r, row in enumerate(ROOT_LAYOUT):
            for c, b in enumerate(row):
                col, lit = (COL_ACC, COL_ACC_LIT) if b in ("8", "9") else (COL_ROOT, COL_ROOT_LIT)
                p = Pad(PAD_TEXT.get(b, b), b, self.inputs, pad, pad, col, lit, font_pad)
                s.add(p, x=x0 + c * (pad + gap), y=y0 + r * (pad + gap))
                self.pads[b] = p
        mx0 = x0 + 3 * (pad + gap) + 3 * gap
        for r, row in enumerate(MOD_LAYOUT):
            for c, b in enumerate(row):
                p = Pad(PAD_TEXT.get(b, b), b, self.inputs, pad, pad, COL_MOD, COL_MOD_LIT, font_pad)
                s.add(p, x=mx0 + c * (pad + gap), y=y0 + r * (pad + gap))
                self.pads[b] = p

        # Right-hand column: chord display, info lines, three-wide button rows.
        rx = mx0 + 2 * (pad + gap) + 2 * gap
        lw = W - rx - 24
        bw, bh = (lw - 16) // 3 - 8, 40     # UIButton groups add ~10 px padding each side
        big_font = lv.font_montserrat_36 if big else lv.font_montserrat_24
        self.chord_label = UILabel("--", w=lw, font=big_font, fg_color=COL_TEXT)
        s.add(self.chord_label, x=rx, y=y0)
        self.key_label = UILabel("Key C", w=lw, font=font_ui, fg_color=COL_TEXT)
        s.add(self.key_label, x=rx, y=y0 + 48)
        self.song_picker = SongPicker(self, lw, bh, font_ui, H - (y0 + 76 + bh) - 16)
        s.add(self.song_picker, x=rx, y=y0 + 76)
        self.song_picker.set_songs(self._song_names())
        self.slot_label = self.key_label          # slot is shown on the key line
        rows = [
            (("Key -", "key-"), ("Key +", "key+"), ("Stop", "stop")),
            (("Slot -", "slot-"), ("Slot +", "slot+"), ("Reset", "reset")),
        ]
        by = y0 + 76 + bh + 12
        for row in rows:
            for k, (text, name) in enumerate(row):
                bg = 0xe0 if name == "stop" else None
                s.add(UIButton(text, w=bw, h=bh, font=font_ui, bg_color=bg,
                               callback=(lambda n: lambda e: self.on_button(n, True))(name)),
                      x=rx + k * (bw + 8), y=by)
            by += bh + 20            # UIButton groups carry ~10 px of padding
        self.mode_button = UIButton("Manual", w=bw, h=bh, font=font_ui, callback=lambda e: self.on_button("mode", True))
        s.add(self.mode_button, x=rx, y=by)
        self.offbeat_button = UIButton("Fill: off", w=bw, h=bh, font=font_ui, callback=lambda e: self.on_button("offbeat", True))
        s.add(self.offbeat_button, x=rx + bw + 8, y=by)
        s.add(UIButton("Save", w=bw, h=bh, font=font_ui, callback=lambda e: self.on_button("save", True)),
              x=rx + 2 * (bw + 8), y=by)
        by += bh + 20
        self.tempo_label = UILabel("120 BPM", w=lw, font=font_ui, fg_color=COL_TEXT)
        s.add(self.tempo_label, x=rx, y=by + 4)
        self.title = UILabel("kanplay", w=W - 2 * x0, font=font_ui, fg_color=COL_TEXT)
        s.add(self.title, x=x0, y=y0 - 34)
        for l in (self.chord_label, self.key_label, self.tempo_label, self.title):
            l.label.set_style_text_align(lv.TEXT_ALIGN.LEFT, 0)

        # Part panel under the pads: six cells and Save.
        py = y0 + 3 * (pad + gap) + 6
        cw = 172 if big else 136
        cgap = 8
        for i in range(kp_song.NUM_PARTS):
            cell = PartCell(i, self, cw, font_ui)
            s.add(cell, x=x0 + i * (cw + cgap), y=py)
            self.cells.append(cell)

    def _mark_dirty(self):
        self.dirty = True

    def _set_text(self, widget, text):
        """lv_label_set_text invalidates the label even when the text is the
        same, and LVGL merges the invalid areas of one frame into a few big
        rectangles: rewriting every label on each press turned a 6 ms chord
        label redraw into a ~100 ms full-screen one, during which touch
        events were dropped. So write only what changed."""
        key = id(widget)
        if self.shown_text.get(key) == text:
            return
        self.shown_text[key] = text
        widget.set_text(text)

    def refresh(self):
        if not self.dirty:
            return
        self.dirty = False
        p = self.player
        chord = p.chord
        self._set_text(self.chord_label.label, chord.name() if chord else "--")
        self._set_text(self.key_label.label, "Key %s / %s     Slot %d/%d" % (
            kp_chords.KEY_NAMES[p.key], kp_chords.MINOR_KEY_NAMES[p.key], p.slot_index + 1, len(p.song.slots)))
        self.song_picker.select(self.song_index)
        bpm = tulip.seq_bpm()
        tempo = "%d BPM" % bpm
        if p.mode == kp_engine.MANUAL and p.onbeat_cycle:
            # onbeat_cycle is in sequencer ticks at the song tempo.
            tempo = "tap %d BPM" % (bpm * kp_engine.PPQ // p.onbeat_cycle)
        self._set_text(self.tempo_label.label, "%s  sw %d  %d/beat" % (tempo, p.song.swing, p.step_per_beat))
        self._set_text(self.mode_button.label, "Manual" if p.mode == kp_engine.MANUAL else "Auto")
        self._set_text(self.offbeat_button.label, "Fill: on" if p.offbeat_auto else "Fill: off")
        for cell, part, pp in zip(self.cells, p.slot.parts, p.parts):
            cell.show(part, pp.synth_voices, pp.wanted)
        self._show_status()

    def _show_status(self):
        # The title spans the screen, so its redraw is the dearest (~16 ms):
        # show the load in steps of 0.05 so it is rewritten only when it moves.
        load = tulip.amy_render_load()
        text = "kanplay   AMY load %.2f" % (round(load * 20) / 20)
        if load > 0.9:
            text += "  TOO HIGH: turn a part off"
        if self.status:
            text += "   " + self.status
        self._set_text(self.title.label, text)

    # --- input ----------------------------------------------------------

    def _chord_args(self):
        return dict(modifier=self.inputs.held_modifier(),
                    semitone_shift=self.inputs.semitone_shift(),
                    minor_swap=self.inputs.is_down("swap"))

    def on_button(self, b, down):
        pad = self.pads.get(b)
        if pad is not None:
            pad.set_lit(down)
        if b in kp_input.ROOTS:
            # Press = on-beat, release = off-beat (see kp_engine).
            if down:
                self.player.press(int(b), **self._chord_args())
            else:
                self.player.release(int(b))
            return
        if b in kp_input.MODIFIERS or b in (kp_input.FLAT, kp_input.SHARP):
            # Chord-type keys change what the next beat plays; they never
            # restart the pattern.
            self.player.set_modifier(**self._chord_args())
            return
        if not down:
            return
        if b == "stop":
            self.player.stop()
        elif b == "reset":
            self.player.reset_steps()
        elif b == "offbeat":
            self.player.set_offbeat_auto(not self.player.offbeat_auto)
        elif b == "key-":
            self.player.set_key_shift(self.player.key_shift - 1)
        elif b == "key+":
            self.player.set_key_shift(self.player.key_shift + 1)
        elif b == "slot-":
            self.player.set_slot(self.player.slot_index - 1)
        elif b == "slot+":
            self.player.set_slot(self.player.slot_index + 1)
        elif b == "song-":
            self._load_song(self.song_index - 1)
        elif b == "song+":
            self._load_song(self.song_index + 1)
        elif b == "mode":
            self.player.set_mode(kp_engine.AUTO if self.player.mode == kp_engine.MANUAL else kp_engine.MANUAL)
        elif b.startswith("part"):
            i = int(b[4:])
            self.player.set_part_enabled(i, not self.player.slot.parts[i].enabled)
        elif b.startswith("tone"):
            i = int(b[4:-1])
            part = self.player.slot.parts[i]
            self.player.set_part_tone(i, kp_tones.next_tone(part.tone, 1 if b.endswith("+") else -1))
        elif b == "save":
            self._save_song()

    def on_song_picked(self, index):
        if index != self.song_index:
            self._load_song(index)

    def _save_song(self):
        try:
            os.mkdir(USER_DIR)
        except OSError:
            pass
        path = USER_DIR + "/" + self.player.song.name + ".json"
        try:
            kp_song.save(self.player.song, path)
            self.status = "saved"
            if path not in self.songs:
                self.songs.append(path)
                self.song_picker.set_songs(self._song_names())
        except OSError as e:
            self.status = "save failed: %s" % e
        self.dirty = True

    def on_midi(self, m):
        if len(m) >= 3 and (m[0] & 0xf0) in (0x80, 0x90):
            self.inputs.midi_note(m[1], (m[0] & 0xf0) == 0x90 and m[2] > 0)

    # --- touch ----------------------------------------------------------

    def _layout_pads(self):
        try:
            self.pad_rects = {b: p.rect() for b, p in self.pads.items()}
        except lv.LvReferenceError:
            self.pad_rects = {}

    def _touch_pads(self):
        """Pads under any of the (up to three) fingers."""
        pts = tulip.touch()
        down = set()
        for i in range(3):
            x, y = pts[2 * i], pts[2 * i + 1]
            if x < 0:
                continue
            for b, (x1, y1, x2, y2) in self.pad_rects.items():
                if x1 <= x <= x2 and y1 <= y <= y2:
                    down.add(b)
                    break
        return down

    def _touch(self, up):
        # The driver leaves point 0 at its last position after a release (the
        # release event carries it), so tulip.touch() alone cannot tell "held"
        # from "just lifted": only hit-test while the callback says down.
        if self.closed:
            return
        self.touch_down = not up
        self.inputs.set_touch(set() if up else self._touch_pads())

    # --- lifecycle ------------------------------------------------------

    def frame(self, arg):
        # A frame callback is scheduled from the display interrupt, so one can
        # still be queued after deactivate() cleared it -- and after quit()
        # the widgets it would touch are gone.
        if self.closed:
            return
        if self.touch_down:
            self.inputs.set_touch(self._touch_pads())
        self.inputs.poll()
        self.frames += 1
        if self.frames % 30 == 0:
            self.dirty = True          # keep the AMY load reading live
        try:
            self.refresh()
        except lv.LvReferenceError:
            self.closed = True

    def activate(self):
        self._layout_pads()
        self.inputs.start()
        tulip.touch_callback(self._touch)
        tulip.frame_callback(self.frame, None)
        try:
            import midi
            midi.add_callback(self.on_midi)
        except Exception:
            pass
        self.dirty = True

    def deactivate(self):
        self.player.stop()
        tulip.frame_callback()
        tulip.touch_callback()
        self.inputs.stop()
        try:
            import midi
            midi.remove_callback(self.on_midi)
        except Exception:
            pass

    def quit(self):
        self.closed = True
        self.player.close()


def _activate(screen):
    app.activate()


def _deactivate(screen):
    app.deactivate()


def _quit(screen):
    app.quit()


def run(screen):
    global app
    screen.activate_callback = _activate
    screen.deactivate_callback = _deactivate
    screen.quit_callback = _quit
    app = KanPlay(screen)
    screen.present()
