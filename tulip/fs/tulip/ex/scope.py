"""SCOPE -- a score-synced analyser for performance files.

    run('scope')

It opens any .perf file (perf.py holds the format and writes them) and shows
the piece playing: which part is sounding, what it is sounding, and where in
the arrangement that is. The display is the score, not an estimate from the
audio -- every note lights because AMY was told to play it, at the tick it was
told, so a chord shows exactly the pitches that were queued.

Why not an FFT of the output: on the Tab5 AMY renders in its own task and
tulip.amy_get_output_buffer() stays empty, so the only real-audio tap is the
microphone, which hears the room, rolls off the bass, and could not tell a pad
from a bass line in any case. The notes can, per part.

What is on screen:
  header      the file, the transport, the position, and what AMY costs now
  piece map   every bar at once, per part: the arrangement at a glance
  progress    where in the piece the playhead is
  track       the playhead, in a strip of its own just above the roll
  meters      per part: level, voices in use, and the pitches sounding now.
              Tapping one mutes it -- the row dims but keeps reading, so a
              muted part still shows what it would be playing
  piano roll  the current two bars, with drum lanes under the pitched rows;
              notes are dim ahead of the playhead and bright behind it

Two measured properties of the Tab5 shape all of that. The compositor charges
per frame for the band of rows a frame touched, and distant bands cost as if
everything between them had changed -- so everything that moves is stacked
into one contiguous band, with the meters beside the roll rather than above
it, and everything static is drawn once outside it. And a tall band does not
arrive in one piece: a line drawn down the whole roll reached the drum lanes a
frame after the pitched rows and looked broken between the parts, which is why
the playhead is a marker in its own short track and the roll shows its
position as the edge between bright and dim notes -- an edge that is painted
over, never erased, so a late piece of the band is invisible.

Tap a row in the file list to open a piece; tap the header (or press space) to
start and stop it; FILES goes back to the list; ESC quits.
"""

import lvgl as lv

import amy
import tulip

import perf

# ---------------------------------------------------------------- palette
# A near-black with a blue bias, so the part hues sit on a ground that was
# chosen rather than on default grey. Colours are (r, g, b): the Tab5's planes
# are native RGB565 and take tuples directly.
BG = (6, 8, 13)
PANEL = (17, 21, 30)
PANEL_HI = (27, 33, 45)
ROLL_BG = (12, 15, 22)
MAP_BG = (10, 13, 19)
GRID = (30, 36, 47)
BEAT = (44, 52, 68)
BAR = (78, 90, 112)
PROG = (88, 108, 148)
TEXT = (226, 232, 242)
DIM = (112, 124, 143)
HEAD = (245, 250, 255)
LIVE = (120, 220, 130)
STEAL = (255, 96, 96)   # more notes than voices: AMY drops the oldest

FONT_T, FONT_L, FONT_N, FONT_X = 18, 15, 15, 13     # title, label, number, tiny

# ---------------------------------------------------------------- geometry
# Everything here is worked out from the screen rather than written down: the
# same /sys ships to a Tulip CC at 1024x600 and a Tab5 at 1280x720, and the
# numbers the Tab5 was tuned at fall off the bottom of the smaller one. The
# fractions below reproduce those numbers exactly at 1280x720.
SW, SH = tulip.screen_size()
MARGIN = 10
TASK_BAR_W = 140            # the quit / switch buttons LVGL keeps at the top right
LABEL_COL = 62              # wide enough for a six-character drum name
RIGHT = SW - MARGIN - LABEL_COL     # the label column, to the right edge

HEAD_Y, HEAD_H = 6, 48
FILES_X, FILES_W = MARGIN + 8, 92           # the button back to the list
TITLE_X, TITLE_W = MARGIN + 110, 180

MAP_Y = HEAD_Y + HEAD_H + 10
MAP_H = max(96, SH // 3 - 8)                # a third of the screen, at most
MAP_X, MAP_W = MARGIN, RIGHT - MARGIN - 6

PROG_Y, PROG_H = MAP_Y + MAP_H + 12, 12
RULER_Y, RULER_H = PROG_Y + PROG_H + 2, 18
HT_Y, HT_H = RULER_Y + RULER_H + 2, 8       # the playhead's own track

# Everything below here is repainted while the piece plays, as one band.
BAND_Y = HT_Y + HT_H + 2
BAND_H = SH - MARGIN - 8 - BAND_Y           # meters, roll and drums
HINT_H = 46                                 # the two lines under the meters

# The meters take a column beside the roll, not a band above it -- see the
# note at the top about what a frame costs.
COL_W = min(504, max(300, SW * 2 // 5))
MARK_X, MARK_W = MARGIN, 18         # the colour key, and the mute button
LABEL_X = MARK_X + MARK_W + 6
LABEL_W = COL_W * 22 // 100
METER_X = LABEL_X + LABEL_W + 4
METER_W = COL_W * 24 // 100
VOICE_X, VOICE_W = METER_X + METER_W + 8, 40
NOTES_X = VOICE_X + VOICE_W + 6
NOTES_W = COL_W + MARGIN - NOTES_X
NOTES_CHARS = max(6, (NOTES_W - 16) // 14)  # what fits in NOTES_W at FONT_L
ROLL_X = MARGIN + COL_W + 6
ROLL_W = RIGHT - 6 - ROLL_X
HEAD_W = 3
# The page's notes are bucketed by column so a wipe of ten pixels looks at the
# handful of notes near the head instead of all of them on a busy page.
BUCKET_W = 32
BUCKETS = (ROLL_W + BUCKET_W - 1) // BUCKET_W

ROW_H_MAX = 52              # a meter row, when there is room for it
DRUMS_H_MAX = max(28, BAND_H // 7)   # the drum lanes at the foot of the roll

FILE_Y, FILE_H = HEAD_Y + HEAD_H + 30, 58       # the file list
FILE_META_X, FILE_PATH_X = SW * 3 // 8, SW * 2 // 3


def _dim(c, f=0.34):
    return (int(c[0] * f), int(c[1] * f), int(c[2] * f))


class Lane:
    """One part's notes, and which of them are sounding at a given tick."""

    def __init__(self, part, spans):
        self.part = part
        self.is_drum = part.is_drum
        self.spans = spans
        self.base = part.color
        self.muted = False
        self.recolor()
        self.reset()

    def recolor(self):
        """A muted part keeps its place and its shape, at a quarter of the
        light: the point of an analyser is to show what is being played, and
        muting it is a thing you did to the sound, not to the score."""
        if self.muted:
            self.color, self.dim = _dim(self.base, 0.34), _dim(self.base, 0.16)
        else:
            self.color, self.dim = self.base, _dim(self.base)

    def reset(self):
        self.cursor = 0
        self.active = []
        self.pos = -(1 << 30)
        self.level = 0.0
        self.sig = None
        self.text = ""

    def at(self, pos):
        if pos < self.pos:                  # the piece restarted
            self.cursor, self.active = 0, []
        self.pos = pos
        spans = self.spans
        while self.cursor < len(spans) and spans[self.cursor][0] <= pos:
            self.active.append(spans[self.cursor])
            self.cursor += 1
        active = self.active
        if active:
            self.active = active = [s for s in active if s[1] > pos]
        # 0.8 of full scale reads as a full meter, so a chord and a single
        # lead both move it.
        target = min(1.0, sum(s[3] for s in active) / 0.8)
        self.level = target if target > self.level else max(target, self.level - 0.07)
        return active

    def names(self):
        """The notes sounding now, rebuilt only when they change."""
        sig = len(self.active)
        for s in self.active:
            sig = sig * 131 + s[2]
        if sig != self.sig:
            self.sig = sig
            name = perf.drum_name if self.is_drum else perf.note_name
            self.text = " ".join(name(s[2]) for s in
                                 sorted(self.active, key=lambda s: s[2]))
        return self.text


class Scope:
    def __init__(self, screen):
        self.screen = screen
        self.active = False
        self.closed = False
        self.touching = False
        self.frames = 0
        self.mode = "files"
        self.files = []
        self.perf = None
        self.player = None
        self.lanes = []
        self.status = ""
        self.was_playing = False
        self.path = None
        self.reset_view()

    def reset_view(self):
        self.page = -1
        self.page_notes = []
        self.buckets = [[] for _ in range(BUCKETS)]
        self.head_x = self.mark_x = ROLL_X
        self.shown_head = None
        self.shown_prog = -1
        self.shown = [None] * len(self.lanes)

    # ------------------------------------------------------------ model
    def open(self, path):
        """Load a performance file and lay the display out for it."""
        if self.player is not None:
            self.player.stop()
        try:
            p = perf.load(path)
        except (OSError, ValueError, IndexError) as e:
            self.status = "%s: %s" % (path.rsplit("/", 1)[-1], e)
            self.mode = "files"
            self.draw_all()
            return False
        self.perf = p
        self.path = path
        self.player = perf.Player(p)
        self.lanes = [Lane(part, spans) for part, spans in zip(p.parts, p.spans())]
        self.layout()
        self.mode = "scope"
        self.draw_all()
        return True

    def layout(self):
        """Fit the roll, the drum lanes and the meters to this piece.

        Everything here depends on the file -- how many parts it has, how wide
        its pitch range is, which drums it uses -- so none of it can be a
        module constant the way it was when the app knew only one piece.
        """
        p = self.perf
        self.tpb = p.ticks_per_bar()
        self.page_ticks = 2 * self.tpb
        self.px = ROLL_W / float(self.page_ticks)
        self.total = p.total()
        self.bars = p.bars()

        lo, hi = p.pitch_range() or (60, 72)
        self.lo, self.hi = lo, hi
        # The drum lanes take the foot of the band, one per drum sound used.
        # Each carries its name, so a kit that uses more sounds takes more
        # room rather than squeezing the labels on top of each other -- an 808
        # piece using six of them had them overlapping at the default height.
        drums = p.drum_notes()[:6]
        self.drum_lanes = drums
        if drums:
            want = min(BAND_H // 3, max(DRUMS_H_MAX, 12 * len(drums)))
            self.lane_h = want // len(drums)
            self.drums_h = self.lane_h * len(drums)
        else:
            self.lane_h = self.drums_h = 0
        self.roll_box = BAND_H - self.drums_h - (4 if drums else 0)
        self.drums_y = BAND_Y + self.roll_box + 4
        rows = hi - lo + 1
        self.row_h = max(2, self.roll_box // rows)
        self.roll_h = self.row_h * rows

        n = len(p.parts)
        # The hint lines sit under the meters, inside the band, so they are
        # taken off the top before the rows are shared out.
        self.row_h_meter = (min(ROW_H_MAX, max(18, (BAND_H - HINT_H) // n))
                            if n else ROW_H_MAX)
        self.hint_y = BAND_Y + n * self.row_h_meter + 22

        # The map gets the same split, at whatever height is left over.
        self.map_lane = max(2, min(8, 32 // len(drums))) if drums else 0
        self.map_drums = self.map_lane * len(drums)
        self.map_pitch = MAP_H - self.map_drums - (4 if drums else 0)
        self.map_row = max(1, self.map_pitch // rows)
        self.map_pitch = self.map_row * rows
        self.map_scale = MAP_W / float(self.total)
        step = 1                            # label every 1, 2, 4 ... bars
        while self.bars // step > 10:
            step *= 2
        self.bar_step = step

        self.reset_view()
        for lane in self.lanes:
            lane.reset()

    def position(self):
        return self.player.position() if self.player else 0

    def playing(self):
        return bool(self.player and self.player.playing)

    def drum_row(self, note):
        try:
            return self.drum_lanes.index(note)
        except ValueError:
            return len(self.drum_lanes) - 1

    # ------------------------------------------------------------ drawing
    def draw_all(self):
        if self.mode == "files":
            self.files = perf.scan()        # before the header, which counts them
        tulip.bg_clear(BG)
        self.draw_header(True)
        if self.mode == "files":
            self.draw_files()
            return
        self.draw_map()
        self.draw_progress(True)
        for i in range(len(self.lanes)):
            self.shown[i] = None
            self.draw_row(i, 0.0, "", 0)
        tulip.bg_str("tap the header or press space to start and stop",
                     MARK_X, self.hint_y, DIM, FONT_X)
        tulip.bg_str("tap a part to mute it; bright notes have played",
                     MARK_X, self.hint_y + 20, DIM, FONT_X)
        self.page = -1
        self.draw_page(max(0, self.position()))

    def redraw(self):
        """Everything that changes with the transport, leaving the piece map
        and the chrome alone: a replay must not spend a bg_clear and a
        thousand map rectangles inside the player's one-bar lookahead."""
        self.draw_header()
        self.draw_progress(True)
        for i in range(len(self.lanes)):
            self.shown[i] = None
            self.draw_row(i, 0.0, "", 0)
        self.page = -1
        self.draw_page(max(0, self.position()))

    def draw_header(self, full=False):
        hw = SW - 2 * MARGIN - TASK_BAR_W
        if full:
            tulip.bg_rect(MARGIN, HEAD_Y, hw, HEAD_H, PANEL, 1)
            tulip.bg_rect(FILES_X, HEAD_Y + 6, FILES_W, HEAD_H - 12, PANEL_HI, 1)
            tulip.bg_str("FILES", FILES_X, HEAD_Y, TEXT, FONT_L, FILES_W, HEAD_H)
            name = self.perf.title if (self.perf and self.mode == "scope") else "SCOPE"
            tulip.bg_str(name, TITLE_X, HEAD_Y, TEXT, FONT_T, TITLE_W, HEAD_H)
        x, w = TITLE_X + TITLE_W + 10, hw - (TITLE_X - MARGIN) - TITLE_W - 10
        tulip.bg_rect(x, HEAD_Y + 6, w, HEAD_H - 12, PANEL, 1)
        if self.mode == "files":
            line = self.status or "%d performance file%s" % (
                len(self.files), "" if len(self.files) == 1 else "s")
            color = DIM
        else:
            pos, p = self.position(), self.perf
            if not self.playing():
                where = "stopped -- tap to play"
            elif pos < 0:
                where = "cue %d" % (-pos // p.ppq + 1)
            else:
                bar, rest = divmod(pos, self.tpb)
                where = "bar %d/%d  beat %d" % (bar + 1, self.bars,
                                                rest // p.ppq + 1)
            diag = tulip.audio_diag()
            line = "%-18s %g BPM  load %.2f  peak %d  ovr %d" % (
                where, p.bpm, tulip.amy_render_load(), diag[8], diag[2])
            # FONT_N runs about 13 px a character; on a narrower screen the
            # full line would reach under the task bar, where nothing erases it.
            if len(line) * 13 > w - 16:
                line = "%-18s %g BPM  load %.2f" % (where, p.bpm,
                                                    tulip.amy_render_load())
            color = LIVE if self.playing() else DIM
        # Left-aligned (no box): centring a line longer than its box spills
        # over both ends, and the right-hand end is under the task bar.
        tulip.bg_str(line, x + 8, HEAD_Y + 31, color, FONT_N)

    # ------------------------------------------------------------ file list
    def draw_files(self):
        if not self.files:
            tulip.bg_str("no .perf files in %s" % " or ".join(perf.DIRS),
                         MARGIN + 20, FILE_Y + 40, DIM, FONT_L)
            tulip.bg_str("write one with perf.Recorder, or see perf.py",
                         MARGIN + 20, FILE_Y + 70, DIM, FONT_X)
            return
        for i, info in enumerate(self.files):
            y = FILE_Y + i * FILE_H
            if y + FILE_H > SH - MARGIN:
                break
            tulip.bg_rect(MARGIN, y, SW - 2 * MARGIN, FILE_H - 6, PANEL, 1)
            tulip.bg_rect(MARGIN, y, 5, FILE_H - 6, perf.COLORS[i % len(perf.COLORS)], 1)
            tulip.bg_str(info["title"], MARGIN + 20, y + 26, TEXT, FONT_T)
            tulip.bg_str("%g BPM   %d bars   %d parts" %
                         (info["bpm"], info["bars"], info["parts"]),
                         FILE_META_X, y + 26, DIM, FONT_L)
            tulip.bg_str(info["path"], FILE_PATH_X, y + 26, DIM, FONT_X)

    def file_at(self, y):
        i = (y - FILE_Y) // FILE_H
        return self.files[i] if 0 <= i < len(self.files) else None

    # ------------------------------------------------------------ piece map
    def draw_map(self):
        """The whole piece in one panel: the arrangement, part by part.

        Static -- drawn once per file, so it costs nothing per frame.
        """
        tulip.bg_rect(MAP_X, MAP_Y, MAP_W, self.map_pitch, MAP_BG, 1)
        dy = MAP_Y + self.map_pitch + 4
        if self.map_drums:
            tulip.bg_rect(MAP_X, dy, MAP_W, self.map_drums, PANEL, 1)
        for b in range(0, self.bars, self.bar_step):
            x = MAP_X + int(b * self.tpb * self.map_scale)
            tulip.bg_rect(x, MAP_Y, 1, self.map_pitch, GRID, 1)
            tulip.bg_str("%d" % (b + 1), x + 3, MAP_Y - 1, DIM, FONT_X, 30, 14)
        for lane in self.lanes:
            for on, off, note, _vel in lane.spans:
                x = MAP_X + int(on * self.map_scale)
                w = max(1, int((off - on) * self.map_scale))
                if lane.is_drum:
                    tulip.bg_rect(x, dy + self.drum_row(note) * self.map_lane,
                                  w, max(1, self.map_lane - 2), lane.dim, 1)
                else:
                    tulip.bg_rect(x, MAP_Y + (self.hi - note) * self.map_row,
                                  w, max(1, self.map_row - 1), lane.color, 1)

    def draw_progress(self, full=False):
        if full:
            tulip.bg_rect(MAP_X, PROG_Y, MAP_W, PROG_H, PANEL_HI, 1)
            for b in range(0, self.bars, self.bar_step):
                tulip.bg_rect(MAP_X + int(b * self.tpb * self.map_scale), PROG_Y,
                              1, PROG_H, BAR, 1)
            self.shown_prog = -1
        w = int(MAP_W * min(max(self.position(), 0), self.total) / float(self.total))
        if w == self.shown_prog:
            return
        if w < self.shown_prog:             # restarted: clear the old fill
            tulip.bg_rect(MAP_X, PROG_Y, MAP_W, PROG_H, PANEL_HI, 1)
        self.shown_prog = w
        if w:
            tulip.bg_rect(MAP_X, PROG_Y, w, PROG_H, PROG, 1)

    # ------------------------------------------------------------ meter rows
    def draw_row(self, i, level, notes, used):
        lane = self.lanes[i]
        color = lane.color
        px = int(METER_W * level)
        key = (px // 3, notes, used, lane.muted)
        if self.shown[i] == key:
            return
        first = self.shown[i] is None
        self.shown[i] = key
        y = BAND_Y + i * self.row_h_meter
        h = self.row_h_meter - 6
        if first:
            tulip.bg_rect(MARK_X, y, NOTES_X + NOTES_W - MARK_X, h, PANEL, 1)
            # The colour key doubles as the mute button, and says which it is.
            tulip.bg_rect(MARK_X, y, MARK_W, h, PANEL_HI if lane.muted else color, 1)
            if lane.muted:
                tulip.bg_str("M", MARK_X, y, color, FONT_L, MARK_W, h)
            # A long part name gets the smaller face rather than the chop:
            # the names come out of the file and can be anything.
            tulip.bg_str(lane.part.name[:14], LABEL_X, y, color,
                         FONT_L if len(lane.part.name) <= 7 else FONT_X,
                         LABEL_W, h)
        # The meter sits on its own trough, so a part at rest still shows the
        # scale it is read against.
        tulip.bg_rect(METER_X, y + h // 4, METER_W, h // 2, PANEL_HI, 1)
        if px:
            tulip.bg_rect(METER_X, y + h // 4, px, h // 2, color, 1)
        tulip.bg_rect(VOICE_X, y, VOICE_W, h, PANEL, 1)
        # A drum part keeps one oscillator per sound rather than a pool of
        # voices, so its voice count says nothing: show the hits alone.
        if lane.is_drum:
            if self.playing():
                tulip.bg_str("%d" % used, VOICE_X, y, color if used else DIM,
                             FONT_X, VOICE_W, h)
        else:
            n = lane.part.voices
            tulip.bg_str("%d/%d" % (used, n), VOICE_X, y,
                         STEAL if used > n else (color if used else DIM),
                         FONT_X, VOICE_W, h)
        tulip.bg_rect(NOTES_X, y, NOTES_W, h, PANEL, 1)
        if notes:
            # Left-aligned and clipped by hand: bg_str centres in its box and
            # a longer string spills out of it, where nothing ever erases it.
            # The cut falls on a space, so a pitch is never shown half-named.
            if len(notes) > NOTES_CHARS:
                notes = notes[:NOTES_CHARS].rsplit(" ", 1)[0] + "+"
            tulip.bg_str(notes, NOTES_X + 8, y + h - 16,
                         DIM if lane.muted else TEXT, FONT_L)

    def draw_rows(self, pos):
        for i, lane in enumerate(self.lanes):
            active = lane.at(pos)
            self.draw_row(i, lane.level, lane.names(), len(active))

    # ------------------------------------------------------------ piano roll
    def _row_y(self, note):
        return BAND_Y + (self.hi - note) * self.row_h

    def _collect(self, start):
        """Every note in the two-bar window, already in screen coordinates."""
        out, end = [], start + self.page_ticks
        for lane in self.lanes:
            for on, off, note, _vel in lane.spans:
                if off <= start:
                    continue
                if on >= end:
                    break
                x = ROLL_X + int((on - start) * self.px)
                w = max(2, int((off - on) * self.px) - 1)
                # A note tied across a page boundary runs off one end or the
                # other; clip it, or it paints over the pitch labels.
                if x < ROLL_X:
                    w -= ROLL_X - x
                    x = ROLL_X
                if x + w > ROLL_X + ROLL_W:
                    w = ROLL_X + ROLL_W - x
                if w <= 0:
                    continue
                if lane.is_drum:
                    out.append((x, self.drums_y + self.drum_row(note) * self.lane_h,
                                w, max(2, self.lane_h - 3), lane.color, lane.dim))
                else:
                    out.append((x, self._row_y(note), w, max(1, self.row_h - 1),
                                lane.color, lane.dim))
        return out

    def _grid(self, tick):
        if tick % self.tpb == 0:
            return BAR, 2
        if tick % self.perf.ppq == 0:
            return BEAT, 1
        return GRID, 1

    def draw_page(self, pos):
        """Repaint the two-bar window `pos` falls in, with every note dim."""
        self.page = max(0, pos) // self.page_ticks
        start = self.page * self.page_ticks
        tulip.bg_rect(ROLL_X, RULER_Y, ROLL_W, RULER_H, PANEL, 1)
        tulip.bg_rect(ROLL_X, BAND_Y, ROLL_W, self.roll_box, ROLL_BG, 1)
        if self.drums_h:
            tulip.bg_rect(ROLL_X, self.drums_y, ROLL_W, self.drums_h, PANEL, 1)
        for note in range(self.lo, self.hi + 1):
            if note % 12 == 0:
                y = self._row_y(note) + self.row_h - 1
                tulip.bg_rect(ROLL_X, y, ROLL_W, 1, GRID, 1)
                tulip.bg_str(perf.note_name(note), RIGHT, y - self.row_h, DIM,
                             FONT_X, LABEL_COL, self.row_h * 2)
        if self.lane_h >= 10:       # below that the names would collide
            for n, note in enumerate(self.drum_lanes):
                tulip.bg_str(perf.drum_name(note), RIGHT,
                             self.drums_y + n * self.lane_h, DIM, FONT_X,
                             LABEL_COL, self.lane_h)
        step = max(1, self.perf.ppq // 4)
        for t in range(0, self.page_ticks + 1, step):
            x = ROLL_X + int(t * self.px)
            c, w = self._grid(start + t)
            tulip.bg_rect(x, BAND_Y, w, self.roll_box, c, 1)
            if self.drums_h:
                tulip.bg_rect(x, self.drums_y, w, self.drums_h, c, 1)
            if (start + t) % self.tpb == 0 and t < self.page_ticks:
                tulip.bg_str("bar %d" % ((start + t) // self.tpb + 1),
                             x + 6, RULER_Y, DIM, FONT_X, 70, RULER_H)
        tulip.bg_rect(ROLL_X, HT_Y, ROLL_W, HT_H, PANEL_HI, 1)
        self.page_notes = self._collect(start)
        self.buckets = [[] for _ in range(BUCKETS)]
        for n in self.page_notes:
            tulip.bg_rect(n[0], n[1], n[2], n[3], n[5], 1)
            b0 = max(0, (n[0] - ROLL_X) // BUCKET_W)
            b1 = min(BUCKETS - 1, (n[0] + n[2] - 1 - ROLL_X) // BUCKET_W)
            for b in range(b0, b1 + 1):
                self.buckets[b].append(n)
        self.head_x = self.mark_x = ROLL_X
        self.shown_head = None

    def _wipe(self, x0, x1):
        """Light up the part of every note between x0 and x1.

        The roll shows the playhead by brightening what it has passed, and
        nothing else happens in it per frame. There was a white line down the
        whole roll here; it read well in a still, but the Tab5 recomposites a
        tall band in pieces, so in motion the line arrived in the drums a
        frame after the pitched rows and looked broken between the parts.
        Nothing here is erased and redrawn -- the bright notes paint straight
        over the dim ones -- so a piece of the band that lands a frame late is
        invisible, and the marker that does move every frame lives in the
        short track above the roll, where it is drawn in one go.
        """
        x0, x1 = max(ROLL_X, x0), min(ROLL_X + ROLL_W, x1)
        if x1 <= x0:
            return
        b0 = (x0 - ROLL_X) // BUCKET_W
        b1 = min(BUCKETS - 1, (x1 - 1 - ROLL_X) // BUCKET_W)
        for b in range(b0, b1 + 1):
            bx = ROLL_X + b * BUCKET_W
            for nx, ny, nw, nh, color, _dim in self.buckets[b]:
                if b > b0 and nx < bx:
                    continue            # already drawn from the bucket before
                if nx + nw <= x0 or nx >= x1:
                    continue
                cx = max(nx, x0)
                cw = min(nx + nw, x1) - cx
                if cw > 0:
                    tulip.bg_rect(cx, ny, cw, nh, color, 1)

    def playhead(self, pos):
        if pos < 0:
            return
        if pos // self.page_ticks != self.page:
            self.draw_page(pos)
        x = ROLL_X + int((pos - self.page * self.page_ticks) * self.px)
        if x == self.shown_head:
            return
        self._wipe(self.head_x, x)
        # Two rectangles, drawn together: the track behind the head, which
        # also covers wherever the marker just was, and the marker itself.
        # The marker straddles x so that it lines up with the bright/dim edge
        # in the roll, instead of sitting its own width to the right of it.
        m = max(ROLL_X, x - HEAD_W // 2)
        tulip.bg_rect(self.mark_x, HT_Y, m - self.mark_x, HT_H, PROG, 1)
        tulip.bg_rect(m, HT_Y, HEAD_W, HT_H, HEAD, 1)
        self.mark_x = m
        self.shown_head = self.head_x = x

    # ------------------------------------------------------------ transport
    def play(self):
        if self.player is None:
            return
        tulip.audio_diag(1)                 # per-performance load, peak, overruns
        for lane in self.lanes:
            lane.reset()
        self.reset_view()
        self.shown = [None] * len(self.lanes)
        self.player.play()
        self.redraw()

    def stop(self):
        if self.player is not None:
            self.player.stop()
        self.idle()

    def idle(self):
        for lane in self.lanes:
            lane.reset()
        self.draw_header()
        for i in range(len(self.lanes)):
            self.draw_row(i, 0.0, "", 0)

    def toggle(self):
        if self.playing():
            self.stop()
        else:
            self.play()

    def toggle_mute(self, i):
        if not (self.player and 0 <= i < len(self.lanes)):
            return
        lane = self.lanes[i]
        lane.muted = self.player.mute(i)
        lane.recolor()
        self.shown[i] = None
        self.draw_row(i, lane.level, lane.names(), len(lane.active))
        # The map and the roll are drawn from the lane colours, so they follow
        # it: about 150 ms for the pair, well inside the player's lookahead.
        self.draw_map()
        self.draw_page(max(0, self.position()))

    def show_files(self):
        self.stop()
        self.mode = "files"
        self.status = ""
        self.draw_all()

    # ------------------------------------------------------------ frame
    def frame(self, _arg):
        # A frame callback can still be queued after deactivate() cleared it.
        if self.closed or not self.active:
            return
        self.frames += 1
        if self.mode != "scope":
            return
        now = self.playing()
        if now:
            pos = self.position()
            self.draw_rows(pos)
            self.playhead(pos)
            self.draw_progress()
        elif self.was_playing:
            self.idle()                     # the player reached the end
        self.was_playing = now
        if self.frames % 30 == 0:
            self.draw_header()

    # ------------------------------------------------------------ input
    def _touch(self, up):
        # The callback says 0 for a press and for every move while held, 1 for
        # the release, which leaves point 0 at its last position.
        if self.closed or not self.active:
            return
        if not up:
            self.touching = True
            return
        if not self.touching:
            return
        self.touching = False
        x, y = tulip.touch()[0], tulip.touch()[1]
        if x >= SW - TASK_BAR_W and y < HEAD_Y + HEAD_H:
            return                          # the task bar's own buttons
        if y < HEAD_Y + HEAD_H:
            if x < FILES_X + FILES_W:
                self.show_files()
            elif self.mode == "scope":
                self.toggle()
            return
        if self.mode == "files":
            info = self.file_at(y)
            if info:
                self.open(info["path"])
            return
        if x < NOTES_X + NOTES_W and y >= BAND_Y:
            i = (y - BAND_Y) // self.row_h_meter
            if i < len(self.lanes):
                self.toggle_mute(i)

    def _key(self, c):
        if c == 32 and self.mode == "scope":
            self.toggle()

    # ------------------------------------------------------------ lifecycle
    def activate(self):
        if self.closed:
            return
        self.active = True
        self.touching = False
        self.draw_all()
        tulip.touch_callback(self._touch)
        tulip.keyboard_callback(self._key)
        tulip.frame_callback(self.frame, None)

    def deactivate(self):
        # The piece plays on from AMY's sequencer underneath another app, the
        # way a player does from the REPL; only Quit stops it.
        self.active = False
        tulip.frame_callback()
        tulip.touch_callback()
        tulip.keyboard_callback()
        tulip.bg_clear()

    def quit(self):
        self.closed = True
        if self.player is not None:
            self.player.stop()


app = None


def _activate(screen):
    app.activate()


def _deactivate(screen):
    app.deactivate()


def _quit(screen):
    app.quit()


def run(screen, path=None):
    global app
    # Everything but the task bar is on the BG plane, so the screen's own
    # background has to stay clear for it to show through.
    screen.bg_plane = True
    screen.set_bg_color(0)
    screen.group.remove_flag(lv.obj.FLAG.SCROLLABLE)
    screen.group.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
    screen.activate_callback = _activate
    screen.deactivate_callback = _deactivate
    screen.quit_callback = _quit
    app = Scope(screen)
    if path:
        app.open(path)
    screen.present()
