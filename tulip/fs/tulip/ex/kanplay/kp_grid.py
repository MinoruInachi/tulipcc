"""The current slot's step sequence, drawn as one small grid per part.

Each lane is a part of the slot, laid out under its part cell: columns are
the steps 0..loop_step (sub-steps included, so with step_per_beat 2 every
other column is an on-beat), rows are the KANTAN arpeggio rows -- row 0 the
highest chord tone down to row 5 the lowest, row 6 the drum-only row -- and a
cell's brightness is the step's velocity. A thin strip under the rows marks
the stroke style of each step (D down, U up, M mute). The column a part
last played is framed, so you can see the pattern position move as you tap.

Everything is painted on the BG plane through the `rect(x, y, w, h, color)`
function handed in (tulip.bg_rect on the device, a recorder in the tests), so
this module imports nothing from the board: it runs under CPython as is.
"""
import kp_song

NUM_ROWS = kp_song.NUM_ROWS
MAX_STEPS = kp_song.MAX_STEPS

# RGB332 palette indices: (r<<5)|(g<<2)|b with r,g 0..7 and b 0..3.
COL_BG = 0x00                   # the lane background, the gap between cells
COL_EMPTY = 0x01                # a silent sub-step cell
COL_EMPTY_ONBEAT = 0x25         # a silent on-beat cell, a little lighter
COL_PLAYHEAD = 0xfc             # frame around the column last played
# Velocity shades, dark to bright, for a pitched part, a drum part, and a
# part that is off (drawn in grey so the pattern is still there to read).
SHADES_PITCH = (0x0a, 0x13, 0x1b, 0x5f)
SHADES_DRUM = (0x64, 0xa8, 0xcc, 0xf5)
SHADES_OFF = (0x25, 0x49, 0x6d, 0x92)
VEL_STEPS = (40, 80, 110)       # velocity thresholds between the shades
STYLE_COLORS = {"D": 0x5e, "U": 0xf4, "M": 0xe0}


def shade(velocity, shades):
    """The shade for a velocity > 0."""
    k = 0
    for t in VEL_STEPS:
        if velocity >= t:
            k += 1
    return shades[k]


class Lane:
    """Geometry and content of one part's grid."""

    def __init__(self, part, step_per_beat, x, lane_w):
        self.part = part
        self.step_per_beat = max(1, step_per_beat)
        self.x = x
        self.steps = max(1, min(MAX_STEPS, part.loop_step + 1))
        self.col_w = max(2, lane_w // self.steps)
        self.inset = 1 if self.col_w >= 4 else 0
        if part.is_drum:
            self.shades = SHADES_DRUM
        elif part.plays():
            self.shades = SHADES_PITCH
        else:
            self.shades = SHADES_OFF

    def col_x(self, step):
        return self.x + step * self.col_w

    def cell_color(self, row, step):
        v = self.part.velocity(row, step)
        if v > 0:
            return shade(v, self.shades)
        return COL_EMPTY_ONBEAT if step % self.step_per_beat == 0 else COL_EMPTY


class StepGrid:
    def __init__(self, rect, x, y, lane_w, lane_gap, row_h, style_h=6, lanes=kp_song.NUM_PARTS):
        self.rect = rect
        self.x = x
        self.y = y
        self.lane_w = lane_w
        self.lane_gap = lane_gap
        self.row_h = max(2, row_h)
        self.style_h = style_h
        self.num_lanes = lanes
        self.lanes = [None] * lanes
        self.heads = [None] * lanes      # the step each lane shows framed

    @property
    def height(self):
        return NUM_ROWS * self.row_h + self.style_h

    def lane_x(self, i):
        return self.x + i * (self.lane_w + self.lane_gap)

    # --- drawing ----------------------------------------------------------

    def show(self, slot, steps):
        """Draw the whole slot: every lane from scratch, with `steps` (one
        per part, -1 for a part that is not playing) framed."""
        for i in range(self.num_lanes):
            part = slot.parts[i] if i < len(slot.parts) else None
            self.rect(self.lane_x(i), self.y, self.lane_w, self.height, COL_BG)
            if part is None or not part.active_rows():
                # No pattern in this slot ("(-)" in the cell): an empty lane
                # says so better than a grid of silent cells would.
                self.lanes[i] = None
                self.heads[i] = None
                continue
            lane = Lane(part, slot.step_per_beat, self.lane_x(i), self.lane_w)
            self.lanes[i] = lane
            head = steps[i] if i < len(steps) else -1
            self.heads[i] = head
            self._fill(lane)
            if 0 <= head < lane.steps:
                self._column(lane, head, True)

    def _fill(self, lane):
        """Paint a lane in areas rather than cells: the silent grid is one
        rectangle, the on-beat columns, and the gap lines, so only the cells
        that sound cost a rect each. Six 64-step lanes drawn cell by cell
        took 190 ms on the Tab5; this way they take a fraction of that."""
        x, y = lane.x, self.y
        w, h = lane.steps * lane.col_w, NUM_ROWS * self.row_h
        inset = lane.inset
        self.rect(x, y, w, h, COL_EMPTY)
        for s in range(0, lane.steps, lane.step_per_beat):
            self.rect(lane.col_x(s), y, lane.col_w, h, COL_EMPTY_ONBEAT)
        if inset:
            # Cells are inset 1 px on every side, so neighbours share a 2 px
            # gap and the lane's edge is a single pixel.
            for r in range(NUM_ROWS + 1):
                edge = r in (0, NUM_ROWS)
                self.rect(x, y + r * self.row_h - (r > 0), w, 1 if edge else 2, COL_BG)
            for s in range(lane.steps + 1):
                edge = s in (0, lane.steps)
                self.rect(lane.col_x(s) - (s > 0), y, 1 if edge else 2, h, COL_BG)
        part = lane.part
        for r in range(NUM_ROWS):
            row = part.arpeggio[r]
            for s in range(min(lane.steps, len(row))):
                if row[s] > 0:
                    self.rect(lane.col_x(s) + inset, y + r * self.row_h + inset,
                              lane.col_w - 2 * inset, self.row_h - 2 * inset, shade(row[s], lane.shades))
        if self.style_h > 0 and not part.is_drum:
            for s in range(min(lane.steps, len(part.style))):
                c = STYLE_COLORS.get(part.style[s])
                if c is not None:
                    self.rect(lane.col_x(s) + inset, y + h + inset,
                              lane.col_w - 2 * inset, self.style_h - 2 * inset, c)

    def follow(self, steps):
        """Move the frames to `steps`, repainting only the columns that
        changed (two per part that moved)."""
        for i, lane in enumerate(self.lanes):
            if lane is None or i >= len(steps):
                continue
            new = steps[i]
            old = self.heads[i]
            if new == old:
                continue
            self.heads[i] = new
            if old is not None and 0 <= old < lane.steps:
                self._column(lane, old, False)
            if 0 <= new < lane.steps:
                self._column(lane, new, True)

    def _column(self, lane, step, framed):
        x = lane.col_x(step)
        w = lane.col_w
        inset = lane.inset
        self.rect(x, self.y, w, self.height, COL_PLAYHEAD if framed else COL_BG)
        for r in range(NUM_ROWS):
            self.rect(x + inset, self.y + r * self.row_h + inset,
                      w - 2 * inset, self.row_h - 2 * inset, lane.cell_color(r, step))
        if self.style_h > 0 and not lane.part.is_drum:
            c = STYLE_COLORS.get(lane.part.style_at(step))
            if c is not None:
                self.rect(x + inset, self.y + NUM_ROWS * self.row_h + inset,
                          w - 2 * inset, self.style_h - 2 * inset, c)
