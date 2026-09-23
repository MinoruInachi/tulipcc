"""The four loopstudio views -- channel rack, piano roll, playlist, mixer --
drawn on the BG plane and hit-tested here.

Why not LVGL widgets: a rack is 8 x 32 step buttons and a piano roll a few
hundred cells, and on the Tab5 every LVGL style write invalidates an area that
LVGL then merges into big redraws (kanplay measured ~100 ms for a screenful).
A step-sized bg_roundrect costs 0.08 ms, and a view can repaint exactly the one
cell that changed: a step toggle is 2 ms, a fader move 13 ms, a whole view
100-150 ms (Tab5, 2026-09-21).

A view gets touch(phase, x, y) with phase DOWN / MOVE / UP, and playhead(step)
once per frame while the transport runs.
"""
import tulip

import ls_model

DOWN, MOVE, UP = 0, 1, 2

# RGB332 palette indices.
BLACK, WHITE = 0x00, 0xff
BG = 0x00
# RGB332 has no neutral dark grey: 0x25 is a blue-grey, 0x49 the nearest grey.
PANEL = 0x25
PANEL_HI = 0x49
KEY_ROW, SHARP_ROW = 0x49, 0x25     # piano roll rows under white / black keys
BEAT_LINE, BAR_LINE = 0x92, 0xdb
GREY = 0x92
ACCENT = 0xf0           # orange
ACCENT_HI = 0xf8
GREEN = 0x14
RED = 0xe0
STEP_OFF_A, STEP_OFF_B = 0x49, 0x64         # beats 1/3 grey, beats 2/4 reddish, as in FL
STEP_ON = 0xf4
STEP_OTHER = 0x1f       # a step holding piano-roll notes off the root pitch
PLAYHEAD = 0x1c
CHANNEL_COLORS = [0xe4, 0xec, 0xfc, 0x9c, 0x1d, 0x17, 0x8b, 0xe7]

FONT_S, FONT, FONT_B, FONT_L = 13, 6, 5, 15      # helvR12, helvR14, helvB14, luRS18

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
BLACK_KEYS = (1, 3, 6, 8, 10)


def note_name(n):
    return "%s%d" % (NOTE_NAMES[n % 12], n // 12 - 1)


def box(x, y, w, h, c):
    if w > 0 and h > 0:
        tulip.bg_rect(x, y, w, h, c, 1)


def rbox(x, y, w, h, c, r=6):
    if w > 0 and h > 0:
        tulip.bg_roundrect(x, y, w, h, r, c, 1)


def text(s, x, y, w, h, c, font=FONT):
    """`s` centred in the box."""
    tulip.bg_str(s, x, y, c, font, w, h)


class Button:
    """A BG-plane button. `label` and `color` may be callables, read at draw
    time, so a toggle shows its state without anyone telling it."""
    repeat = False              # auto-repeat while held (the app's top bar)

    def __init__(self, x, y, w, h, label, action, color=PANEL_HI, fg=WHITE, font=FONT_B):
        self.x, self.y, self.w, self.h = x, y, w, h
        self.label, self.action, self.color, self.fg, self.font = label, action, color, fg, font

    def hit(self, x, y):
        return self.x <= x < self.x + self.w and self.y <= y < self.y + self.h

    def draw(self, clear=True):
        c = self.color() if callable(self.color) else self.color
        s = self.label() if callable(self.label) else self.label
        if clear:               # the corners, when it is redrawn over itself
            box(self.x, self.y, self.w, self.h, BG)
        rbox(self.x, self.y, self.w, self.h, c)
        text(s, self.x, self.y, self.w, self.h, self.fg, self.font)


class View:
    name = ""

    def __init__(self, app, x, y, w, h):
        self.app = app
        self.x, self.y, self.w, self.h = x, y, w, h
        self.buttons = []
        self.fresh = False          # inside draw(): the area is already clear
        self.head = -1              # playhead step last drawn

    @property
    def song(self):
        return self.app.song

    @property
    def pattern(self):
        return self.app.song.patterns[self.app.engine.pattern_index]

    def draw(self):
        """Draw the whole view onto an area the caller has just cleared.

        Filling is what costs on the BG plane -- the Tab5 measured 40 ms for
        the view area (280 ms before fillRect wrote rows directly), against
        well under a millisecond for a line or a label -- so nothing is
        painted twice: while `fresh` is set, clear() is a no-op."""
        self.fresh = True
        try:
            for b in self.buttons:
                b.draw(False)
            self.head = -1
            self.draw_content()
        finally:
            self.fresh = False

    def clear(self, x, y, w, h):
        if not self.fresh:
            box(x, y, w, h, BG)

    def draw_content(self):
        pass

    def touch(self, phase, x, y):
        if phase == DOWN:
            for b in self.buttons:
                if b.hit(x, y):
                    b.action()
                    b.draw()
                    return
        self.touch_content(phase, x, y)

    def touch_content(self, phase, x, y):
        pass

    def playhead(self, step):
        pass

    def _marker(self, step, x0, cell_w, y, n):
        """Move the playhead marker along a ruler strip."""
        if step == self.head:
            return
        if 0 <= self.head < n:
            box(x0 + self.head * cell_w, y, cell_w - 2, 5, BG)
        if 0 <= step < n:
            box(x0 + step * cell_w, y, cell_w - 2, 5, PLAYHEAD)
        self.head = step


# --- channel rack ------------------------------------------------------------

class RackView(View):
    name = "RACK"
    RULER_H = 26
    LEFT_W = 372

    def __init__(self, app, x, y, w, h):
        super().__init__(app, x, y, w, h)
        self.row_h = (h - self.RULER_H) // ls_model.NUM_CHANNELS
        self.gx = x + self.LEFT_W
        self.gw = w - self.LEFT_W
        self.paint = None           # (row, state) while a finger drags along a row
        self.kit_button = Button(x, y, 150, self.RULER_H - 4, lambda: "Kit: " + self.song.kit_name(),
                                 self._next_kit, font=FONT_S)
        self.buttons = [self.kit_button]

    def _next_kit(self):
        self.song.next_kit(1)
        self.app.engine.apply_sounds()
        self.app.engine.audition(0, self.song.channels[0].root)

    def _cell_w(self):
        return self.gw // self.pattern.steps

    def _row_y(self, row):
        return self.y + self.RULER_H + row * self.row_h

    def draw_content(self):
        n = self.pattern.steps
        cw = self._cell_w()
        for s in range(0, n, 4):
            text(str(s // 4 + 1), self.gx + s * cw, self.y, cw, self.RULER_H - 8, GREY, FONT_S)
        for row in range(ls_model.NUM_CHANNELS):
            self.draw_row(row)

    def draw_row(self, row):
        self.draw_strip(row)
        # One pass over the row's notes, not one per step (draw_step's way):
        # 256 steps of that was most of a 2-bar rack's redraw on the Tab5.
        root = self.song.channels[row].root
        lit = {}
        for n in self.pattern.notes[row]:
            if n[2] == root or n[0] not in lit:
                lit[n[0]] = STEP_ON if n[2] == root else STEP_OTHER
        cw = self._cell_w()
        y, h = self._row_y(row) + 4, self.row_h - 8
        for s in range(self.pattern.steps):
            c = lit.get(s) or (STEP_OFF_A if (s // 4) % 2 == 0 else STEP_OFF_B)
            tulip.bg_roundrect(self.gx + s * cw + 2, y, cw - 4, h, 5, c, 1)

    def draw_strip(self, row, name_only=False):
        """The channel's controls: mute, solo, < name >. Every part repaints
        over itself, so nothing is cleared first."""
        c = self.song.channels[row]
        y, h = self._row_y(row) + 4, self.row_h - 8
        x = self.x
        selected = row == self.app.channel
        rbox(x + 140, y, 180, h, PANEL_HI if selected else PANEL)
        box(x + 140, y + 6, 6, h - 12, CHANNEL_COLORS[row])
        text(c.name, x + 148, y, 170, h, WHITE if selected else 0xdb, FONT_B if selected else FONT)
        if name_only:
            return
        rbox(x, y, 44, h, GREEN if not c.mute else PANEL)
        text("M", x, y, 44, h, BLACK if not c.mute else GREY, FONT_B)
        rbox(x + 48, y, 44, h, ACCENT if c.solo else PANEL)
        text("S", x + 48, y, 44, h, BLACK if c.solo else GREY, FONT_B)
        rbox(x + 96, y, 40, h, PANEL_HI)
        text("<", x + 96, y, 40, h, WHITE, FONT_B)
        rbox(x + 324, y, 40, h, PANEL_HI)
        text(">", x + 324, y, 40, h, WHITE, FONT_B)

    def draw_step(self, row, s):
        p = self.pattern
        cw = self._cell_w()
        x, y = self.gx + s * cw, self._row_y(row) + 4
        here = p.starts_at(row, s)
        if not here:
            c = STEP_OFF_A if (s // 4) % 2 == 0 else STEP_OFF_B
        elif any(n[2] == self.song.channels[row].root for n in here):
            c = STEP_ON
        else:
            c = STEP_OTHER
        rbox(x + 2, y, cw - 4, self.row_h - 8, c, 5)

    def touch_content(self, phase, x, y):
        if phase == UP:
            self.paint = None
            return
        row = (y - self.y - self.RULER_H) // self.row_h
        if not (0 <= row < ls_model.NUM_CHANNELS) or y < self.y + self.RULER_H:
            return
        if x < self.gx:
            if phase == DOWN:
                self._touch_strip(row, x - self.x)
            return
        s = (x - self.gx) // self._cell_w()
        if not (0 <= s < self.pattern.steps):
            return
        root = self.song.channels[row].root
        if phase == DOWN:
            on = self.pattern.toggle_step(row, s, root)
            self.app.engine.changed(row)
            self.paint = (row, on, s)
            if on and not self.app.engine.playing:
                self.app.engine.audition(row, root)
            self.draw_step(row, s)
            self.app.select_channel(row)
        elif self.paint and self.paint[0] == row and self.paint[2] != s:
            # Dragging along a row paints the state the first tap set.
            on = self.paint[1]
            if bool(self.pattern.starts_at(row, s)) != on:
                self.pattern.toggle_step(row, s, root)
                self.app.engine.changed(row)
                self.draw_step(row, s)
            self.paint = (row, on, s)

    def _touch_strip(self, row, dx):
        c = self.song.channels[row]
        if dx < 46:
            c.mute = not c.mute
            self.app.engine.changed()
        elif dx < 94:
            c.solo = not c.solo
            self.app.engine.changed()
        elif dx < 138 or dx >= 322:
            self.song.change_sound(row, -1 if dx < 138 else 1)
            self.app.engine.apply_sounds()
            self.app.engine.changed(row)        # a drum channel's steps follow the sound
            self.app.engine.audition(row, c.root)
            self.app.select_channel(row)
        else:
            self.app.select_channel(row)
            self.app.engine.audition(row, c.root)
        self.draw_strip(row)

    def channel_changed(self, old, new):
        self.draw_strip(old, True)
        self.draw_strip(new, True)

    def playhead(self, step):
        self._marker(step, self.gx, self._cell_w(), self.y + self.RULER_H - 6, self.pattern.steps)


# --- piano roll --------------------------------------------------------------

class RollView(View):
    name = "ROLL"
    TOOL_H = 48
    RULER_H = 22
    KEYS_W = 84
    ROWS = 12

    def __init__(self, app, x, y, w, h):
        super().__init__(app, x, y, w, h)
        self.row_h = (h - self.TOOL_H - self.RULER_H) // self.ROWS
        self.gx = x + self.KEYS_W + 4
        self.gw = w - self.KEYS_W - 4
        self.gy = y + self.TOOL_H + self.RULER_H
        self.low = [None] * ls_model.NUM_CHANNELS      # lowest visible note, per channel
        self.length = 2
        self.drag = None            # [note, created, moved] under the finger
        bh = self.TOOL_H - 8
        bx = x

        def add(w_, label, action, **kw):
            nonlocal bx
            self.buttons.append(Button(bx, y, w_, bh, label, action, **kw))
            bx += w_ + 6
        add(60, "<", lambda: self._channel(-1))
        add(230, lambda: "%d  %s" % (self.app.channel + 1, self.song.channels[self.app.channel].name),
            lambda: self.app.engine.audition(self.app.channel, self.song.channels[self.app.channel].root),
            color=lambda: CHANNEL_COLORS[self.app.channel], fg=BLACK)
        add(60, ">", lambda: self._channel(1))
        bx += 20
        add(90, "Oct -", lambda: self._scroll(-12))
        add(70, "Dn", lambda: self._scroll(-4))
        add(70, "Up", lambda: self._scroll(4))
        add(90, "Oct +", lambda: self._scroll(12))
        bx += 20
        add(150, lambda: "Length %d" % self.length, self._next_length)
        self.length_button = self.buttons[-1]
        bx += 20
        add(110, "Clear", self._clear, color=RED)

    # toolbar actions

    def _channel(self, step):
        self.app.select_channel((self.app.channel + step) % ls_model.NUM_CHANNELS)

    def channel_changed(self, old, new):
        self.app.draw_all()

    def _low(self):
        ch = self.app.channel
        if self.low[ch] is None:
            root = self.song.channels[ch].root
            notes = self.pattern.notes[ch]
            base = min(n[2] for n in notes) if notes else root
            self.low[ch] = max(ls_model.NOTE_MIN, min(base - base % 12, ls_model.NOTE_MAX - self.ROWS + 1))
        return self.low[ch]

    def _scroll(self, semis):
        low = self._low() + semis
        self.low[self.app.channel] = max(ls_model.NOTE_MIN, min(low, ls_model.NOTE_MAX - self.ROWS + 1))
        self.draw_content()

    def _next_length(self):
        self.length = {1: 2, 2: 4, 4: 8, 8: 16, 16: 1}.get(self.length, 1)

    def _clear(self):
        self.pattern.clear_channel(self.app.channel)
        self.app.engine.changed(self.app.channel)
        self.draw_content()

    # drawing

    def _cell_w(self):
        return self.gw // self.pattern.steps

    def _row_of(self, pitch):
        return self.ROWS - 1 - (pitch - self._low())

    def draw_content(self):
        # The key and grid rows repaint over themselves (a scroll redraws
        # only them); just the ruler needs clearing.
        self.clear(self.x, self.gy - self.RULER_H, self.w, self.RULER_H)
        self.head = -1
        n = self.pattern.steps
        cw = self._cell_w()
        low = self._low()
        for s in range(0, n, 4):
            text("%d.%d" % (s // 16 + 1, s // 4 % 4 + 1), self.gx + s * cw, self.gy - self.RULER_H,
                 cw, self.RULER_H - 8, GREY, FONT_S)
        for r in range(self.ROWS):
            pitch = low + self.ROWS - 1 - r
            y = self.gy + r * self.row_h
            black = pitch % 12 in BLACK_KEYS
            box(self.x, y + 1, self.KEYS_W, self.row_h - 2, BLACK if black else 0xdb)
            text(note_name(pitch), self.x, y, self.KEYS_W, self.row_h, WHITE if black else BLACK,
                 FONT_B if pitch % 12 == 0 else FONT_S)
            box(self.gx, y + 1, cw * n, self.row_h - 2, SHARP_ROW if black else KEY_ROW)
        for s in range(n + 1):
            c = BAR_LINE if s % 16 == 0 else BEAT_LINE if s % 4 == 0 else BG
            tulip.bg_line(self.gx + s * cw, self.gy, self.gx + s * cw, self.gy + self.ROWS * self.row_h - 1, c)
        for note in self.pattern.notes[self.app.channel]:
            self.draw_note(note)

    def draw_note(self, note, erase=False):
        r = self._row_of(note[2])
        if not (0 <= r < self.ROWS):
            return
        cw = self._cell_w()
        x, y = self.gx + note[0] * cw, self.gy + r * self.row_h
        if erase:
            black = note[2] % 12 in BLACK_KEYS
            box(x, y + 1, note[1] * cw, self.row_h - 2, SHARP_ROW if black else KEY_ROW)
            for s in range(note[0], note[0] + note[1] + 1):
                c = BAR_LINE if s % 16 == 0 else BEAT_LINE if s % 4 == 0 else BG
                tulip.bg_line(self.gx + s * cw, y, self.gx + s * cw, y + self.row_h - 1, c)
            return
        rbox(x + 1, y + 2, note[1] * cw - 2, self.row_h - 4, CHANNEL_COLORS[self.app.channel], 4)
        box(x + 1, y + 2, 4, self.row_h - 4, WHITE)

    # touch

    def touch_content(self, phase, x, y):
        ch = self.app.channel
        p = self.pattern
        if phase == UP:
            d, self.drag = self.drag, None
            if d and not d[1] and not d[2]:
                # A tap on an existing note that never moved: delete it.
                self.draw_note(d[0], erase=True)
                p.remove(ch, d[0])
                self.app.engine.changed(ch)
            return
        if y < self.gy:
            return
        r = (y - self.gy) // self.row_h
        if not (0 <= r < self.ROWS):
            return
        pitch = self._low() + self.ROWS - 1 - r
        if x < self.gx:
            if phase == DOWN:
                self.app.engine.audition(ch, pitch)      # the keyboard
            return
        s = (x - self.gx) // self._cell_w()
        if not (0 <= s < p.steps):
            return
        if phase == DOWN:
            note = p.find(ch, s, pitch)
            if note is not None:
                self.drag = [note, False, False]
                return
            note = p.add(ch, s, pitch, self.length)
            if note is None:
                return
            # add() clears what it overlaps on this pitch; repaint the row's
            # notes rather than track which.
            self.drag = [note, True, False]
            if not self.app.engine.playing:
                self.app.engine.audition(ch, pitch)
            self.app.engine.changed(ch)
            self._redraw_pitch(pitch)
        elif self.drag:
            # Dragging right or left of the note's start sets its length.
            note = self.drag[0]
            want = max(1, s - note[0] + 1)
            if want != note[1]:
                self.draw_note(note, erase=True)
                p.resize(ch, note, want)
                self.app.engine.changed(ch)
                self.draw_note(note)
                self.drag[2] = True
                self.length = note[1]
                self.length_button.draw()

    def _redraw_pitch(self, pitch):
        r = self._row_of(pitch)
        cw = self._cell_w()
        n = self.pattern.steps
        y = self.gy + r * self.row_h
        black = pitch % 12 in BLACK_KEYS
        box(self.gx, y + 1, cw * n, self.row_h - 2, SHARP_ROW if black else KEY_ROW)
        for s in range(n + 1):
            c = BAR_LINE if s % 16 == 0 else BEAT_LINE if s % 4 == 0 else BG
            tulip.bg_line(self.gx + s * cw, y, self.gx + s * cw, y + self.row_h - 1, c)
        for note in self.pattern.notes[self.app.channel]:
            if note[2] == pitch:
                self.draw_note(note)

    def playhead(self, step):
        self._marker(step, self.gx, self._cell_w(), self.gy - 7, self.pattern.steps)


# --- playlist ----------------------------------------------------------------

class PlaylistView(View):
    name = "SONG"
    TOOL_H = 48
    RULER_H = 22
    LEFT_W = 132
    PAGE_BARS = 16

    def __init__(self, app, x, y, w, h):
        super().__init__(app, x, y, w, h)
        self.row_h = (h - self.TOOL_H - self.RULER_H) // ls_model.NUM_PATTERNS
        self.gx = x + self.LEFT_W
        self.cw = (w - self.LEFT_W) // self.PAGE_BARS
        self.gy = y + self.TOOL_H + self.RULER_H
        self.page = 0
        bh = self.TOOL_H - 8
        self.buttons = [
            Button(x, y, 200, bh, lambda: "Bars %d-%d" % (self.page * 16 + 1, self.page * 16 + 16), self._next_page),
            Button(x + 210, y, 140, bh, "Clear all", self._clear, color=RED),
        ]

    def _next_page(self):
        self.page = (self.page + 1) % (ls_model.SONG_BARS // self.PAGE_BARS)
        self.draw_content()

    def _clear(self):
        self.song.clips = [[] for _ in range(ls_model.NUM_PATTERNS)]
        self.draw_content()

    def draw_hint(self):
        self.clear(self.x + 370, self.y, self.w - 370, self.TOOL_H - 8)
        bars = self.song.song_bars()
        hint = "%d bars" % bars if bars else "empty: tap a cell to place a pattern"
        if self.app.engine.mode != "song":
            hint += "   (SONG mode plays this arrangement)"
        text(hint, self.x + 370, self.y, self.w - 370, self.TOOL_H - 8, GREY, FONT)

    def draw_content(self):
        self.clear(self.x, self.gy - self.RULER_H, self.w, self.RULER_H)
        self.head = -1
        self.draw_hint()
        first = self.page * self.PAGE_BARS
        for b in range(self.PAGE_BARS):
            text(str(first + b + 1), self.gx + b * self.cw, self.gy - self.RULER_H, self.cw,
                 self.RULER_H - 8, WHITE if (first + b) % 4 == 0 else GREY, FONT_S)
        for p in range(ls_model.NUM_PATTERNS):
            self.draw_row(p)

    def draw_row(self, p):
        y = self.gy + p * self.row_h
        pat = self.song.patterns[p]
        current = p == self.app.engine.pattern_index
        rbox(self.x, y + 3, self.LEFT_W - 8, self.row_h - 6, ACCENT if current else PANEL)
        label = "P%d  %s" % (p + 1, "empty" if pat.is_empty() else "%d bar" % pat.bars)
        text(label, self.x, y + 3, self.LEFT_W - 8, self.row_h - 6, BLACK if current else WHITE, FONT_B)
        first = self.page * self.PAGE_BARS
        for b in range(self.PAGE_BARS):
            box(self.gx + b * self.cw + 1, y + 3, self.cw - 2, self.row_h - 6,
                PANEL_HI if ((first + b) // 4) % 2 == 0 else PANEL)
        for start in self.song.clips[p]:
            b0, b1 = max(start, first), min(start + pat.bars, first + self.PAGE_BARS)
            if b0 < b1:
                x = self.gx + (b0 - first) * self.cw
                rbox(x + 2, y + 5, (b1 - b0) * self.cw - 4, self.row_h - 10, CHANNEL_COLORS[p], 5)
                text("P%d" % (p + 1), x + 2, y + 5, (b1 - b0) * self.cw - 4, self.row_h - 10, BLACK, FONT_B)

    def touch_content(self, phase, x, y):
        if phase != DOWN or y < self.gy:
            return
        p = (y - self.gy) // self.row_h
        if not (0 <= p < ls_model.NUM_PATTERNS):
            return
        if x < self.gx:
            self.app.select_pattern(p)
            return
        b = (x - self.gx) // self.cw
        if 0 <= b < self.PAGE_BARS:
            self.song.toggle_clip(p, self.page * self.PAGE_BARS + b)
            if self.app.engine.mode == "song":
                self.app.engine.restart_queue()     # the song's length may have changed
            self.draw_row(p)            # cells and clips repaint over themselves
            self.draw_hint()

    def pattern_changed(self):
        for p in range(ls_model.NUM_PATTERNS):
            self.draw_row(p)

    def playhead(self, bar):
        first = self.page * self.PAGE_BARS
        self._marker(bar - first if bar >= 0 else -1, self.gx, self.cw, self.gy - 7, self.PAGE_BARS)


# --- mixer -------------------------------------------------------------------

class MixerView(View):
    name = "MIXER"
    STRIPS = ls_model.NUM_CHANNELS + 4          # channels, master, reverb, chorus, echo
    FX = ("master", "reverb", "chorus", "echo")

    def __init__(self, app, x, y, w, h):
        super().__init__(app, x, y, w, h)
        self.sw = w // self.STRIPS
        self.fy = y + 40                         # fader top
        self.fh = h - 40 - 150                   # fader height
        self.pan_y = self.fy + self.fh + 16
        self.ms_y = self.pan_y + 52
        self.drag = None                         # (strip, "vol" | "pan")

    def _get(self, i):
        if i < ls_model.NUM_CHANNELS:
            return self.song.channels[i].volume
        return getattr(self.song, self.FX[i - ls_model.NUM_CHANNELS])

    def _set(self, i, v):
        v = max(0.0, min(1.0, v))
        if i < ls_model.NUM_CHANNELS:
            self.song.channels[i].volume = v
        else:
            setattr(self.song, self.FX[i - ls_model.NUM_CHANNELS], v)

    def draw_content(self):
        for i in range(self.STRIPS):
            self.draw_strip(i)

    def draw_strip(self, i):
        x = self.x + i * self.sw
        self.clear(x, self.y, self.sw, self.h)
        chan = i < ls_model.NUM_CHANNELS
        if chan:
            c = self.song.channels[i]
            name, color = c.name, CHANNEL_COLORS[i]
        else:
            name, color = self.FX[i - ls_model.NUM_CHANNELS].upper(), ACCENT if i == ls_model.NUM_CHANNELS else 0x5f
        box(x + 8, self.y + 6, self.sw - 16, 4, color)
        text(name[:11], x + 3, self.y + 12, self.sw - 6, 24, WHITE, FONT_S)
        self.draw_fader(i)
        if chan:
            self.draw_pan(i)
            w2 = (self.sw - 22) // 2
            rbox(x + 8, self.ms_y, w2, 56, GREEN if not c.mute else PANEL_HI)
            text("M", x + 8, self.ms_y, w2, 56, BLACK if not c.mute else GREY, FONT_B)
            rbox(x + 14 + w2, self.ms_y, w2, 56, ACCENT if c.solo else PANEL_HI)
            text("S", x + 14 + w2, self.ms_y, w2, 56, BLACK if c.solo else GREY, FONT_B)

    def draw_fader(self, i):
        x = self.x + i * self.sw + self.sw // 2
        v = self._get(i)
        ky = self.fy + int((1.0 - v) * (self.fh - 28))
        self.clear(x - 30, self.fy - 2, 60, self.fh + 4)
        box(x - 4, self.fy, 8, ky + 14 - self.fy, PANEL_HI)
        box(x - 4, ky + 14, 8, self.fy + self.fh - ky - 14,
            CHANNEL_COLORS[i] if i < ls_model.NUM_CHANNELS else ACCENT)
        rbox(x - 28, ky, 56, 28, 0xdb, 5)
        box(x - 22, ky + 13, 44, 2, BLACK)

    def draw_pan(self, i):
        x = self.x + i * self.sw + 8
        w = self.sw - 16
        pan = self.song.channels[i].pan
        self.clear(x, self.pan_y, w, 40)
        box(x, self.pan_y + 17, w, 6, PANEL_HI)
        box(x + w // 2 - 1, self.pan_y + 10, 2, 20, GREY)
        rbox(x + int(pan * (w - 20)), self.pan_y + 4, 20, 32, 0xdb, 4)

    def touch_content(self, phase, x, y):
        if phase == UP:
            if self.drag and self.drag[0] >= ls_model.NUM_CHANNELS:
                self.app.engine.apply_fx()
            self.drag = None
            return
        i = (x - self.x) // self.sw
        if phase == DOWN:
            if not (0 <= i < self.STRIPS):
                return
            chan = i < ls_model.NUM_CHANNELS
            if self.fy - 10 <= y <= self.fy + self.fh + 8:
                self.drag = (i, "vol")
            elif chan and self.pan_y <= y < self.pan_y + 44:
                self.drag = (i, "pan")
            elif chan and self.ms_y <= y < self.ms_y + 56:
                c = self.song.channels[i]
                if x - (self.x + i * self.sw) < self.sw // 2:
                    c.mute = not c.mute
                else:
                    c.solo = not c.solo
                self.app.engine.changed()
                self.draw_strip(i)
                return
        if not self.drag:
            return
        i, what = self.drag
        if what == "vol":
            self._set(i, 1.0 - (y - self.fy - 14) / (self.fh - 28))
            if i < ls_model.NUM_CHANNELS:
                self.app.engine.changed(i)          # levels are baked into the queue
            elif i == ls_model.NUM_CHANNELS:
                self.app.engine.changed()           # master
            self.draw_fader(i)
        else:
            sx = self.x + i * self.sw + 8
            pan = max(0.0, min(1.0, (x - sx - 10) / (self.sw - 36)))
            if abs(pan - 0.5) < 0.06:
                pan = 0.5                       # a detent at centre
            self.song.channels[i].pan = pan
            self.app.engine.changed(i)
            self.draw_pan(i)
