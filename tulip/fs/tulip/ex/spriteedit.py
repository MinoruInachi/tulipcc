"""SPRITEEDIT -- a sprite editor for tulipcc, drawn for a fingertip.

    run('spriteedit')

Paint on the big canvas in the middle. The left column holds the tools (Pen,
Erase, Fill, Pick, Line, Rect, Box, Oval, Disc, Move), the drawing modes (Mirror
X/Y, Grid, Onion skin) and the edits (Undo, Redo, Flip, Rotate, Clear). The right
column holds the colour (RGB sliders over a 32 colour palette; moving a slider
re-mixes the selected swatch), a live preview of the animation, and the frames.

With a keyboard: P E F I L R B O D M pick a tool, U undo, Y redo, S save, G grid,
X and V mirror, K onion, space play, left and right change frame, Esc quit.

What it writes
--------------
A PNG, because that is what tulip.sprite_png() reads, kept in /user. Frames are
stacked in one vertical strip rather than laid out across a sheet, because sprite
RAM has no stride of its own: a sprite is `w*h` consecutive pixels. Stacked, every
frame is already contiguous once the PNG is loaded, so a game can do this:

    (w, h, _) = tulip.sprite_png("hero.png", 0)
    frame_h = 16
    tulip.sprite_register(5, 0 + frame * w * frame_h, w, frame_h)
    tulip.sprite_on(5)

A frame also has to fit sprite RAM together with the rest, so the frame count is
capped at what fits (seven at 64x64, thirty-one at 32x32): anything this saves can
be loaded by a game as it stands. A tEXt chunk remembers the frame height, the
frame rate and the palette for the next time it is opened here; anything else
reading the PNG skips it. A PNG from elsewhere opens too -- one that is a whole
number of squares tall is taken for a strip of them, and anything else for a single
frame.

The colours are RGB565 on every board, so a sprite saved on a Tulip CC and opened
on a Tab5 keeps what it had, and a board with the 256 colour palette only rounds
what it shows. Transparent is the pixel the Tab5 itself uses for it, 0x4daa, which
is palette entry 0x55 expanded: a Tab5 can copy a frame into sprite RAM as it is.

Why it is drawn the way it is
-----------------------------
The canvas is an array of what is on the screen (`shown`) next to the frame being
edited, and every change anywhere goes the same way: change the frame, say which
box of cells may differ, and _flush() paints the cells in that box that really do.
So a dab costs one rectangle, a switch to a similar frame or an undo costs the
cells that differ, and nothing ever clears the canvas first -- which is what would
make it flicker. A line or a box being dragged out is not special either: each
sample restarts from the frame as it was and draws the shape again, and the diff
leaves only what moved.

Undo holds a list of frames, not of changes. Frames are shared between the
snapshots, the only thing ever written in place is a private copy of the current
frame made by _checkpoint(), so a snapshot is a list of pointers and Dup costs
nothing. The same identity check is what keeps sprite RAM up to date (_sync_ram):
a position whose frame is not the one already loaded there gets copied.

The preview and the frame strip are real sprites out of that RAM. What you are
looking at is what a game would get, transparency included, and there is no
thumbnail to draw. Handle 0 is left alone because a USB mouse's pointer lives
there (and at the end of sprite RAM, hence _RAM_PIXELS).

The name dialog has its own keyboard on the BG plane rather than an LVGL one, so
a dialog is the same code as the rest of the app and takes the same keys.
"""

import math
import os
from array import array

import tulip

# A Tab5 is touch-only and twice the size, so its controls are finger-sized and
# its BG plane takes true colour; everywhere else keeps the palette.
_BIG = tulip.board() == "TAB5"
_TRUECOLOR = _BIG
_ALPHA_PAL = 0x55

CLEAR = 0x4daa                  # the transparent pixel, as RGB565

MAX_SIDE = 64
SIZES = (8, 16, 24, 32, 48, 64)
MAX_FRAMES = 64
# The end of sprite RAM is where the mouse pointer's 12x19 is kept.
_RAM_PIXELS = 32 * 32 * 32 - 12 * 19
_UNDO_DEPTH = 40

_PREVIEW_SPRITE = 1
_STRIP_SPRITE = 2
_LAST_SPRITE = 30               # 31 is what a touch registers as, in collisions()

_META_KEY = b"tulip-sprite"
_RECOVER_NAME = "spriteedit_recover.png"


def _pack(r, g, b):
    """An (r,g,b) as a model pixel: RGB565, and never the transparent value. One
    step of blue is not a visible change."""
    c = ((r & 0xf8) << 8) | ((g & 0xfc) << 3) | (b >> 3)
    return c + 1 if c == CLEAR else c


def _unpack(c):
    """Bit replication, so that packing the result gives the same pixel back and
    white is 255 rather than 248."""
    r = c >> 11
    g = (c >> 5) & 63
    b = c & 31
    return ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2))


def _pal(c):
    """The nearest palette index. 0x55 is the transparent entry; a colour landing
    on it would punch a hole rather than draw."""
    (r, g, b) = _unpack(c)
    p = tulip.color(r, g, b)
    return p + 1 if p == _ALPHA_PAL else p


def _c(r, g, b):
    """A colour for the app's own chrome, in what the BG plane takes."""
    if _TRUECOLOR:
        return (r, g, b)
    return _pal(_pack(r, g, b))


_ink_cache = {}


def _ink(c):
    """A model pixel, in what the BG plane takes."""
    v = _ink_cache.get(c)
    if v is None:
        if len(_ink_cache) > 1024:
            _ink_cache.clear()
        v = _unpack(c) if _TRUECOLOR else _pal(c)
        _ink_cache[c] = v
    return v


BG = _c(30, 30, 38)
BAR = _c(18, 18, 24)
BAR_TEXT = _c(224, 224, 232)
BTN = _c(58, 60, 74)
BTN_EDGE = _c(92, 96, 116)
BTN_ON = _c(236, 176, 56)
BTN_TEXT = _c(236, 236, 242)
BTN_ON_TEXT = _c(24, 20, 10)
GRID_LINE = _c(16, 16, 20)
PANEL = _c(44, 44, 56)
SEL = _c(255, 255, 255)
TRACK = _c(20, 20, 26)
DLG = _c(52, 54, 68)
DLG_EDGE = _c(150, 154, 180)
FIELD = _c(16, 16, 22)
CHANNELS = (_c(226, 70, 70), _c(84, 196, 92), _c(86, 130, 240))

# The two squares of the transparency check, as model pixels: they go through the
# same diff as everything else on the canvas.
_CHECK = (_pack(104, 104, 112), _pack(72, 72, 80))

# DawnBringer's 32 colour palette, a widely used general purpose one for pixel art.
_DEFAULT_PALETTE = (
    0x000000, 0x222034, 0x45283c, 0x663931, 0x8f563b, 0xdf7126, 0xd9a066, 0xeec39a,
    0xfbf236, 0x99e550, 0x6abe30, 0x37946e, 0x4b692f, 0x524b24, 0x323c39, 0x3f3f74,
    0x306082, 0x5b6ee1, 0x639bff, 0x5fcde4, 0xcbdbfc, 0xffffff, 0x9badb7, 0x847e87,
    0x696a6a, 0x595652, 0x76428a, 0xac3232, 0xd95763, 0xd77bba, 0x8f974a, 0x8a6f30,
)

# Preview backdrops, to check a sprite against something dark, mid and light.
_PREVIEW_BG = (_c(14, 14, 18), _c(70, 110, 170), _c(226, 226, 232), _c(38, 110, 70))

# ------------------------------------------------------------------- geometry

(SW, SH) = tulip.screen_size()

PAD = 8
GAP = 6 if _BIG else 4
# The strip across the top carries this app's buttons and leaves room for
# UIScreen's task bar at the right. See solitaire.py on why its colour runs the
# whole width only on the Tab5.
BAR_H = 56 if _BIG else 34
BAR_RESERVE = 124 if _BIG else 100
BAR_FILL_W = SW if _BIG else SW - BAR_RESERVE
BAR_BTN_W = 76 if _BIG else 56
BAR_BTN_COUNT = 4

BODY_Y = BAR_H + PAD
BODY_H = SH - BODY_Y - PAD

TOOL_W = 84 if _BIG else 60
TOOLS_W = 2 * TOOL_W + GAP
_SECTION = 16 if _BIG else 10
TOOL_ROWS = 10
TOOL_H = (BODY_H - (TOOL_ROWS - 1) * GAP - 2 * _SECTION) // TOOL_ROWS

# The canvas is the biggest square the height allows; everything else is what is
# left over on either side of it.
BOX = BODY_H
BOX_X = PAD + TOOLS_W + PAD
PANEL_X = BOX_X + BOX + PAD
PANEL_W = SW - PANEL_X - PAD

SLIDER_H = 44 if _BIG else 28
SWATCH = 3 * SLIDER_H + 2 * GAP
SLIDER_LABEL_W = 24 if _BIG else 16
SLIDER_VALUE_W = 56 if _BIG else 32
SLIDERS_X = PANEL_X + SWATCH + GAP
TRACK_X = SLIDERS_X + SLIDER_LABEL_W
TRACK_W = PANEL_X + PANEL_W - SLIDER_VALUE_W - TRACK_X

PREVIEW_H = 100 if _BIG else 80
STRIP_H = 80
FRAME_BTN_H = 56 if _BIG else 38
STRIP_ARROW_W = 52 if _BIG else 36
_SPLIT = 10 if _BIG else 8

PAL_COLS = 8
PAL_ROWS = 4
PAL_Y = BODY_Y + SWATCH + _SPLIT
_below_pal = _SPLIT + PREVIEW_H + _SPLIT + STRIP_H + GAP + FRAME_BTN_H
PAL_H = (BODY_H - SWATCH - _SPLIT - _below_pal) // PAL_ROWS
PAL_W = PANEL_W // PAL_COLS

PREVIEW_Y = PAL_Y + PAL_ROWS * PAL_H + _SPLIT
STRIP_Y = PREVIEW_Y + PREVIEW_H + _SPLIT
FRAME_BTN_Y = STRIP_Y + STRIP_H + GAP
PREVIEW_BOX = 80

BTN_FONT = 15 if _BIG else 6
STATUS_FONT = 15 if _BIG else 8

DRAG_TOOLS = ("line", "rect", "box", "oval", "disc", "move")

_KEYMAP = {19: "pen", 8: "erase", 9: "fill", 12: "pick", 15: "line", 21: "rect",
           5: "box", 18: "oval", 7: "disc", 16: "move",
           24: "undo", 28: "redo", 22: "save", 10: "grid", 27: "mirror_x",
           25: "mirror_y", 14: "onion", 44: "play", 80: "prev", 79: "next",
           41: "quit"}

# HID codes for the name dialog. Only what is safe in a file name.
_NAME_KEYS = {45: "-"}
for _i in range(26):
    _NAME_KEYS[4 + _i] = chr(97 + _i)
for _i in range(9):
    _NAME_KEYS[30 + _i] = chr(49 + _i)
_NAME_KEYS[39] = "0"
_KEY_ENTER = 40
_KEY_ESC = 41
_KEY_BACKSPACE = 42

_KEYBOARD_ROWS = ("1234567890-", "qwertyuiop_", "asdfghjkl", "zxcvbnm")


def _save_dir():
    """Where sprites go. run() leaves the current directory in /sys/ex for a bundled
    app, which is no place to save anything."""
    user = tulip.root_dir() + "user"
    return user if tulip.is_folder(user) else tulip.pwd()


# ------------------------------------------------------------------------ PNG

def _crc32(data):
    try:
        import binascii
        return binascii.crc32(data) & 0xffffffff
    except (ImportError, AttributeError):
        crc = 0xffffffff
        for v in data:
            crc ^= v
            for _ in range(8):
                crc = (crc >> 1) ^ 0xedb88320 if crc & 1 else crc >> 1
        return crc ^ 0xffffffff


def _adler32(data):
    a = 1
    b = 0
    n = 0
    for v in data:
        a += v
        b += a
        n += 1
        # Well short of what overflows a small int on a 32 bit board.
        if n == 3800:
            a %= 65521
            b %= 65521
            n = 0
    return ((b % 65521) << 16) | (a % 65521)


def _chunk(kind, data):
    return (len(data).to_bytes(4, "big") + kind + data
            + _crc32(kind + data).to_bytes(4, "big"))


def encode_png(frames, w, h, meta=b""):
    """The frames as one RGBA PNG, stacked top to bottom. Stored, not deflated: there
    is no compressor on the boards, and the decoder does not mind."""
    rows = h * len(frames)
    raw = bytearray((1 + w * 4) * rows)
    o = 0
    for f in frames:
        i = 0
        for _y in range(h):
            o += 1                      # filter type 0
            for _x in range(w):
                c = f[i]
                i += 1
                if c != CLEAR:
                    (raw[o], raw[o + 1], raw[o + 2]) = _unpack(c)
                    raw[o + 3] = 255
                o += 4
    z = bytearray(b"\x78\x01")
    p = 0
    size = len(raw)
    while True:
        n = min(size - p, 65535)
        final = 1 if p + n == size else 0
        z.append(final)
        z.extend(n.to_bytes(2, "little"))
        z.extend((n ^ 0xffff).to_bytes(2, "little"))
        z.extend(raw[p:p + n])
        p += n
        if final:
            break
    z.extend(_adler32(raw).to_bytes(4, "big"))
    out = bytearray(b"\x89PNG\r\n\x1a\n")
    ihdr = (w.to_bytes(4, "big") + rows.to_bytes(4, "big")
            + bytes((8, 6, 0, 0, 0)))
    out.extend(_chunk(b"IHDR", ihdr))
    if meta:
        out.extend(_chunk(b"tEXt", _META_KEY + b"\0" + meta))
    out.extend(_chunk(b"IDAT", bytes(z)))
    out.extend(_chunk(b"IEND", b""))
    return bytes(out)


def png_info(data):
    """(width, height, our metadata or None), walking the chunks. None if this is not
    a PNG at all."""
    if data[:8] != b"\x89PNG\r\n\x1a\n" or len(data) < 24 or data[12:16] != b"IHDR":
        return None
    w = int.from_bytes(data[16:20], "big")
    h = int.from_bytes(data[20:24], "big")
    meta = None
    p = 8
    size = len(data)
    prefix = _META_KEY + b"\0"
    while p + 8 <= size:
        n = int.from_bytes(data[p:p + 4], "big")
        kind = data[p + 4:p + 8]
        if kind == b"tEXt" and data[p + 8:p + 8 + len(prefix)] == prefix:
            meta = data[p + 8 + len(prefix):p + 8 + n]
        elif kind == b"IEND":
            break
        p += 12 + n
    return (w, h, meta)


def parse_meta(meta):
    """frame_h=16;fps=8;pal=0000a0... into a dict of what was understood."""
    out = {}
    if meta is None:
        return out
    try:
        for part in bytes(meta).decode().split(";"):
            if part.startswith("frame_h="):
                out["frame_h"] = int(part[8:])
            elif part.startswith("fps="):
                out["fps"] = int(part[4:])
            elif part.startswith("pal="):
                hexes = part[4:]
                if len(hexes) == 4 * PAL_COLS * PAL_ROWS:
                    out["pal"] = [int(hexes[i:i + 4], 16)
                                  for i in range(0, len(hexes), 4)]
    except ValueError:
        pass                            # keep whatever parsed before it
    return out


def frame_height(w, h, meta):
    """How tall one frame of a w x h image is, or None if it cannot be opened."""
    frame_h = meta.get("frame_h", 0)
    if frame_h > 0 and h % frame_h == 0 and frame_h <= MAX_SIDE:
        return frame_h
    if h <= MAX_SIDE and (h == w or h % w != 0):
        return h
    if h % w == 0 and w <= MAX_SIDE:
        return w                        # a strip of squares
    return h if h <= MAX_SIDE else None


# ----------------------------------------------------------------- sprite RAM

_pal_table = None


def _from_native(data, w, h, n):
    """n frames read back out of sprite RAM, as model frames."""
    global _pal_table
    size = w * h
    frames = []
    if _TRUECOLOR:
        for i in range(n):
            # A bytearray is taken as raw memory where bytes would be taken as a
            # sequence of numbers, in CPython and MicroPython alike.
            frames.append(array("H", bytearray(data[i * size * 2:(i + 1) * size * 2])))
        return frames
    if _pal_table is None:
        _pal_table = [_pack(*tulip.rgb(p)) for p in range(256)]
        _pal_table[_ALPHA_PAL] = CLEAR
    for i in range(n):
        f = array("H", (0 for _ in range(size)))
        o = i * size
        for j in range(size):
            f[j] = _pal_table[data[o + j]]
        frames.append(f)
    return frames


def _to_native(frame):
    if _TRUECOLOR:
        return bytes(frame)
    out = bytearray(len(frame))
    for i in range(len(frame)):
        c = frame[i]
        out[i] = _ALPHA_PAL if c == CLEAR else _ink(c)
    return bytes(out)


# -------------------------------------------------------------------- buttons

class _Btn:
    def __init__(self, x, y, w, h, label, fn, arg=None, on=None):
        self.x = x
        self.y = y
        self.w = w
        self.h = h
        self.label = label
        self.fn = fn
        self.arg = arg
        self.on = on

    def hit(self, x, y):
        return self.x <= x < self.x + self.w and self.y <= y < self.y + self.h

    def draw(self):
        lit = self.on is not None and self.on(self.arg)
        tulip.bg_rect(self.x, self.y, self.w, self.h, BTN_ON if lit else BTN, 1)
        tulip.bg_rect(self.x, self.y, self.w, self.h, BTN_EDGE, 0)
        tulip.bg_str(self.label, self.x, self.y, BTN_ON_TEXT if lit else BTN_TEXT,
                     BTN_FONT, self.w, self.h)

    def fire(self):
        if self.arg is None:
            self.fn()
        else:
            self.fn(self.arg)


# ------------------------------------------------------------------ the editor

class SpriteEdit:
    def __init__(self, app):
        self.app = app
        self.save_dir = _save_dir()
        self.tool = "pen"
        self.last_tool = "pen"
        self.mirror_x = False
        self.mirror_y = False
        self.grid = True
        self.onion = False
        self.palette = [_pack(p >> 16, (p >> 8) & 255, p & 255)
                        for p in _DEFAULT_PALETTE]
        self.swatch = 21
        self.color = self.palette[self.swatch]
        self.fps = 8
        self.playing = False
        self.play_pos = 0
        self.play_at = 0
        self.preview_bg = 0
        self.modal = None
        self.quitting = False
        self.msg = None
        self.keys_held = ()
        self.finger_down = False
        self.grabbed = None
        self.dialog_btns = []
        self.new_w = 16
        self.new_h = 16
        self.typed = ""
        self.file_page = 0
        self.files = []
        self.confirm_what = None
        self.in_ram = []
        self.cur_ram_stale = False
        self.ram_pushed_at = 0
        self.at_cell = None
        self.drag_box = None
        self._new_doc(16, 16)
        self._build_buttons()

    # ------------------------------------------------------------- the document

    def _new_doc(self, w, h, frames=None, path=None):
        self.w = w
        self.h = h
        self.frames = frames if frames else [self._blank()]
        self.cur = 0
        self.path = path
        self.dirty = False
        self.undo = []
        self.redo = []
        self.max_frames = min(MAX_FRAMES, _RAM_PIXELS // (w * h))
        self.strip_first = 0
        self.shown = array("H", (0 for _ in range(w * h)))
        self.zoom = min(BOX // w, BOX // h)
        self.cv_x = BOX_X + (BOX - w * self.zoom) // 2
        self.cv_y = BODY_Y + (BOX - h * self.zoom) // 2
        self.slot_w = max(w, 48) + 8
        self.slots = (PANEL_W - 2 * STRIP_ARROW_W - 2 * GAP) // self.slot_w
        self._clear_dirty()

    def _blank(self):
        return array("H", (CLEAR for _ in range(self.w * self.h)))

    def _frame(self):
        return self.frames[self.cur]

    def _checkpoint(self):
        """Call before changing anything. The snapshot keeps the current frame as it
        is and the editing goes on in a copy of it."""
        self.undo.append((self.cur, list(self.frames)))
        if len(self.undo) > _UNDO_DEPTH:
            self.undo.pop(0)
        self.frames[self.cur] = array("H", self.frames[self.cur])

    def _commit(self, changed=None):
        """Call after changing anything. A stroke that changed nothing takes its
        snapshot back, so Undo never appears to do nothing."""
        if changed is None:
            (cur, frames) = self.undo[-1]
            changed = (len(frames) != len(self.frames) or cur != self.cur
                       or frames[cur] != self.frames[cur])
        if not changed:
            self.undo.pop()
            return
        self.redo = []
        self.dirty = True
        self._frame_changed()

    def _frame_changed(self):
        self.cur_ram_stale = True
        self._draw_status()

    def _step_history(self, source, dest):
        if not source:
            self._say("Nothing to undo" if source is self.undo else "Nothing to redo")
            return
        dest.append((self.cur, self.frames))
        (self.cur, self.frames) = source.pop()
        # These frames are shared with the snapshot just taken; editing makes its
        # own copy first, in _checkpoint.
        self.frames = list(self.frames)
        self.dirty = True
        self._after_frames_changed()

    def _after_frames_changed(self):
        # Keep the frame being edited in view. Only here: paging the strip by hand
        # is allowed to leave it behind.
        if self.cur < self.strip_first or self.cur >= self.strip_first + self.slots:
            self.strip_first = self.cur - self.cur % self.slots
        self._show_cur()
        self._sync_ram()
        self._draw_strip()
        self._place_sprites()
        self._draw_status()

    def _show_cur(self):
        self._dirty_all()
        self._flush()

    # ------------------------------------------------------------------- canvas

    def _clear_dirty(self):
        self.d_x0 = self.w
        self.d_y0 = self.h
        self.d_x1 = -1
        self.d_y1 = -1

    def _dirty_all(self):
        self.d_x0 = 0
        self.d_y0 = 0
        self.d_x1 = self.w - 1
        self.d_y1 = self.h - 1

    def _flush(self, force=False):
        """Paint the cells in the dirty box that differ from what is on the screen."""
        if self.d_x1 < 0:
            return
        (x0, y0, x1, y1) = (self.d_x0, self.d_y0, self.d_x1, self.d_y1)
        self._clear_dirty()
        f = self.frames[self.cur]
        under = self.frames[self.cur - 1] if self.onion and self.cur > 0 else None
        shown = self.shown
        w = self.w
        z = self.zoom
        size = z - 1 if self.grid and z >= 4 else z
        ox = self.cv_x
        oy = self.cv_y
        rect = tulip.bg_rect
        check = _CHECK
        for y in range(y0, y1 + 1):
            i = y * w + x0
            py = oy + y * z
            for x in range(x0, x1 + 1):
                c = f[i]
                if c == CLEAR:
                    # What shows through a transparent cell is a colour like any
                    # other as far as the diff goes.
                    tone = check[(x ^ y) & 1]
                    if under is not None and under[i] != CLEAR:
                        c = ((under[i] & 0xf7de) >> 1) + ((tone & 0xf7de) >> 1)
                    else:
                        c = tone
                if force or c != shown[i]:
                    shown[i] = c
                    rect(ox + x * z, py, size, size, _ink(c), 1)
                i += 1

    def _draw_canvas(self):
        z = self.zoom
        tulip.bg_rect(BOX_X, BODY_Y, BOX, BOX, BG, 1)
        tulip.bg_rect(self.cv_x - 1, self.cv_y - 1, self.w * z + 1, self.h * z + 1,
                      GRID_LINE, 1)
        self._dirty_all()
        self._flush(force=True)

    def _plot(self, x, y):
        c = self.paint
        f = self.frames[self.cur]
        w = self.w
        h = self.h
        xs = (x, w - 1 - x) if self.mirror_x else (x,)
        ys = (y, h - 1 - y) if self.mirror_y else (y,)
        for py in ys:
            if 0 <= py < h:
                for px in xs:
                    if 0 <= px < w:
                        f[py * w + px] = c
                        if px < self.d_x0:
                            self.d_x0 = px
                        if px > self.d_x1:
                            self.d_x1 = px
                        if py < self.d_y0:
                            self.d_y0 = py
                        if py > self.d_y1:
                            self.d_y1 = py

    def _line(self, x0, y0, x1, y1):
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        while True:
            self._plot(x0, y0)
            if x0 == x1 and y0 == y1:
                return
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def _span(self, x0, x1, y):
        """_plot for a whole run. A 64x64 Box is 4096 cells, and going through _plot
        for each of those took a second a sample."""
        w = self.w
        h = self.h
        x0 = max(x0, 0)
        x1 = min(x1, w - 1)
        if x0 > x1:
            return
        c = self.paint
        f = self.frames[self.cur]
        mx0 = w - 1 - x1
        mx1 = w - 1 - x0
        for py in ((y, h - 1 - y) if self.mirror_y else (y,)):
            if 0 <= py < h:
                o = py * w
                for i in range(o + x0, o + x1 + 1):
                    f[i] = c
                if self.mirror_x:
                    for i in range(o + mx0, o + mx1 + 1):
                        f[i] = c
                self.d_y0 = min(self.d_y0, py)
                self.d_y1 = max(self.d_y1, py)
        if self.d_y1 >= 0:
            self.d_x0 = min(self.d_x0, mx0 if self.mirror_x else x0, x0)
            self.d_x1 = max(self.d_x1, mx1 if self.mirror_x else x1, x1)

    def _rect(self, x0, y0, x1, y1, filled):
        if x0 > x1:
            (x0, x1) = (x1, x0)
        if y0 > y1:
            (y0, y1) = (y1, y0)
        if filled:
            for y in range(y0, y1 + 1):
                self._span(x0, x1, y)
            return
        self._span(x0, x1, y0)
        self._span(x0, x1, y1)
        for y in range(y0, y1 + 1):
            self._plot(x0, y)
            self._plot(x1, y)

    def _oval(self, x0, y0, x1, y1, filled):
        if x0 > x1:
            (x0, x1) = (x1, x0)
        if y0 > y1:
            (y0, y1) = (y1, y0)
        # A cell is in if its centre is inside the ellipse that touches the outer
        # edges of the box, tested in doubled coordinates so that an even-sized oval
        # has its centre between two cells. All integers, so it comes out exactly
        # symmetrical. Each row only needs the first cell it starts at.
        w = x1 - x0 + 1
        h = y1 - y0 + 1
        cx = x0 + x1
        cy = y0 + y1
        starts = []
        for y in range(y0, y1 + 1):
            d = 2 * y - cy
            limit = w * w * (h * h - d * d) // (h * h)
            x = x0
            # The middle cell or two always count, or a tall thin oval would have
            # rows with nothing in them.
            while 2 * x + 1 < cx and (2 * x - cx) * (2 * x - cx) > limit:
                x += 1
            starts.append(x)
        # And a flat oval has to reach the sides of the box it was dragged out to,
        # which the test on its own stops short of.
        starts[(h - 1) // 2] = x0
        starts[h // 2] = x0
        for row in range(h):
            left = starts[row]
            y = y0 + row
            if filled:
                self._span(left, cx - left, y)
                continue
            # The outline is whatever has nothing above or below it: from the start
            # of this row to the start of whichever neighbour is narrower.
            if row == 0 or row == h - 1:
                self._span(left, cx - left, y)
                continue
            inner = max(starts[row - 1], starts[row + 1], left + 1)
            for x in range(left, min(inner, (cx + 2) // 2)):
                self._plot(x, y)
                self._plot(cx - x, y)

    def _flood(self, x, y):
        f = self.frames[self.cur]
        w = self.w
        size = w * self.h
        start = y * w + x
        old = f[start]
        c = self.paint
        if old == c:
            return
        f[start] = c
        stack = [start]
        while stack:
            i = stack.pop()
            if i % w and f[i - 1] == old:
                f[i - 1] = c
                stack.append(i - 1)
            if (i + 1) % w and f[i + 1] == old:
                f[i + 1] = c
                stack.append(i + 1)
            if i >= w and f[i - w] == old:
                f[i - w] = c
                stack.append(i - w)
            if i + w < size and f[i + w] == old:
                f[i + w] = c
                stack.append(i + w)
        self._dirty_all()

    def _shifted(self, base, dx, dy):
        w = self.w
        h = self.h
        out = array("H", base)
        i = 0
        for y in range(h):
            to = ((y + dy) % h) * w
            for x in range(w):
                out[to + (x + dx) % w] = base[i]
                i += 1
        return out

    def _cell(self, x, y):
        # Floor division, so that a finger off the canvas gets a cell off the canvas
        # and drawing simply clips.
        return ((x - self.cv_x) // self.zoom, (y - self.cv_y) // self.zoom)

    def _on_canvas(self, x, y):
        return (self.cv_x <= x < self.cv_x + self.w * self.zoom
                and self.cv_y <= y < self.cv_y + self.h * self.zoom)

    # ------------------------------------------------------------------ strokes

    def _stroke_begin(self, cx, cy):
        self.paint = CLEAR if self.tool == "erase" else self.color
        self.at_cell = (cx, cy)
        self.start_cell = (cx, cy)
        self.drag_box = None
        if self.tool == "pick":
            self._pick(cx, cy)
            return
        self._checkpoint()
        self.cur_ram_stale = True
        if self.tool in ("pen", "erase"):
            self._plot(cx, cy)
        elif self.tool == "fill":
            self._flood(cx, cy)
            if self.mirror_x or self.mirror_y:
                if self.mirror_x:
                    self._flood(self.w - 1 - cx, cy)
                if self.mirror_y:
                    self._flood(cx, self.h - 1 - cy)
                if self.mirror_x and self.mirror_y:
                    self._flood(self.w - 1 - cx, self.h - 1 - cy)
        else:
            self._shape(cx, cy)
        self._flush()
        self._draw_status()

    def _stroke_move(self, cx, cy):
        if (cx, cy) == self.at_cell:
            return
        (lx, ly) = self.at_cell
        self.at_cell = (cx, cy)
        if self.tool == "pick":
            self._pick(cx, cy)
            return
        if self.tool in ("pen", "erase"):
            self._line(lx, ly, cx, cy)
        elif self.tool in DRAG_TOOLS:
            self._shape(cx, cy)
        else:
            return
        self.cur_ram_stale = True
        self._flush()
        self._draw_status()

    def _stroke_end(self):
        tool = self.tool
        self.at_cell = None
        if tool == "pick":
            # An eyedropper is a detour on the way to painting with what it found,
            # unless what it found was nothing.
            if self.last_tool != "pick" and self.tool == "pick":
                self._set_tool(self.last_tool)
            return
        self._commit()

    def _shape(self, cx, cy):
        """A drag draws the whole shape again each time from the frame as it was."""
        (sx, sy) = self.start_cell
        (_, snapshot) = self.undo[-1]
        base = snapshot[self.cur]
        tool = self.tool
        if tool == "move":
            self.frames[self.cur] = self._shifted(base, cx - sx, cy - sy)
            self._dirty_all()
            return
        self.frames[self.cur] = array("H", base)
        if self.drag_box is not None:
            (self.d_x0, self.d_y0, self.d_x1, self.d_y1) = self.drag_box
        if tool == "line":
            self._line(sx, sy, cx, cy)
        elif tool == "rect":
            self._rect(sx, sy, cx, cy, False)
        elif tool == "box":
            self._rect(sx, sy, cx, cy, True)
        elif tool == "oval":
            self._oval(sx, sy, cx, cy, False)
        elif tool == "disc":
            self._oval(sx, sy, cx, cy, True)
        self.drag_box = (self.d_x0, self.d_y0, self.d_x1, self.d_y1)

    def _pick(self, cx, cy):
        if not (0 <= cx < self.w and 0 <= cy < self.h):
            return
        c = self._frame()[cy * self.w + cx]
        if c == CLEAR:
            self.last_tool = "erase"
            return
        if self.last_tool == "erase":
            self.last_tool = "pen"
        self._set_color(c, None)

    # ---------------------------------------------------------- whole-frame edits

    def _edit(self, make):
        self._checkpoint()
        self.frames[self.cur] = make(self._frame())
        self._commit()
        self._show_cur()

    def _flip_h(self):
        (w, h) = (self.w, self.h)

        def make(f):
            out = array("H", f)
            for y in range(h):
                o = y * w
                for x in range(w):
                    out[o + x] = f[o + w - 1 - x]
            return out
        self._edit(make)

    def _flip_v(self):
        (w, h) = (self.w, self.h)

        def make(f):
            out = array("H", f)
            for y in range(h):
                o = y * w
                p = (h - 1 - y) * w
                for x in range(w):
                    out[o + x] = f[p + x]
            return out
        self._edit(make)

    def _rot90(self):
        (w, h) = (self.w, self.h)
        if w != h:
            self._say("Rotate needs a square sprite")
            return

        def make(f):
            out = array("H", f)
            for y in range(h):
                for x in range(w):
                    out[x * w + (w - 1 - y)] = f[y * w + x]
            return out
        self._edit(make)

    def _clear_frame(self):
        self._edit(lambda f: self._blank())

    # -------------------------------------------------------------------- frames

    def _goto(self, i):
        if i == self.cur or not (0 <= i < len(self.frames)):
            return
        self.cur = i
        self._after_frames_changed()

    def _prev(self):
        self._goto((self.cur - 1) % len(self.frames))

    def _next(self):
        self._goto((self.cur + 1) % len(self.frames))

    def _frame_op(self, op):
        n = len(self.frames)
        if op in ("add", "dup") and n >= self.max_frames:
            self._say("%d frames is all sprite RAM holds at this size" % self.max_frames)
            return
        if op == "del" and n <= 1:
            self._say("A sprite needs at least one frame")
            return
        if (op == "left" and self.cur == 0) or (op == "right" and self.cur == n - 1):
            return
        # Nothing here writes to a frame, so no private copy is needed; the snapshot
        # is what makes it undoable.
        self.undo.append((self.cur, list(self.frames)))
        if len(self.undo) > _UNDO_DEPTH:
            self.undo.pop(0)
        if op == "add":
            self.frames.insert(self.cur + 1, self._blank())
            self.cur += 1
        elif op == "dup":
            self.frames.insert(self.cur + 1, self.frames[self.cur])
            self.cur += 1
        elif op == "del":
            self.frames.pop(self.cur)
            self.cur = min(self.cur, len(self.frames) - 1)
        else:
            to = self.cur + (-1 if op == "left" else 1)
            (self.frames[self.cur], self.frames[to]) = (self.frames[to], self.frames[self.cur])
            self.cur = to
        self.redo = []
        self.dirty = True
        self._after_frames_changed()

    def _page_strip(self, step):
        first = self.strip_first + step * self.slots
        if 0 <= first < len(self.frames):
            self.strip_first = first
            self._draw_strip()
            self._place_sprites()

    def _sync_ram(self):
        """Bring sprite RAM up to date with the frame list, by position."""
        size = self.w * self.h
        ram = self.in_ram
        for i in range(len(self.frames)):
            f = self.frames[i]
            if i >= len(ram):
                ram.append(None)
            if ram[i] is not f or (i == self.cur and self.cur_ram_stale):
                tulip.sprite_bitmap(_to_native(f), i * size)
                ram[i] = f
        del ram[len(self.frames):]
        self.cur_ram_stale = False
        self.ram_pushed_at = tulip.ticks_ms()

    def _place_sprites(self):
        """The preview and the strip. Registering does not mark the display dirty on
        every board; moving does."""
        (w, h) = (self.w, self.h)
        size = w * h
        n = len(self.frames)
        if self.play_pos >= n or not self.playing:
            self.play_pos = self.cur
        tulip.sprite_register(_PREVIEW_SPRITE, self.play_pos * size, w, h)
        tulip.sprite_move(_PREVIEW_SPRITE,
                          PANEL_X + (PREVIEW_BOX - w) // 2,
                          PREVIEW_Y + (PREVIEW_H - h) // 2)
        tulip.sprite_on(_PREVIEW_SPRITE)
        for s in range(self.slots):
            handle = _STRIP_SPRITE + s
            i = self.strip_first + s
            if i < n and handle <= _LAST_SPRITE:
                tulip.sprite_register(handle, i * size, w, h)
                tulip.sprite_move(handle,
                                  self._slot_x(s) + (self.slot_w - w) // 2,
                                  STRIP_Y + (STRIP_H - h) // 2)
                tulip.sprite_on(handle)
            elif handle <= _LAST_SPRITE:
                tulip.sprite_off(handle)
        for handle in range(_STRIP_SPRITE + self.slots, _LAST_SPRITE + 1):
            tulip.sprite_off(handle)

    def _sprites_off(self):
        for handle in range(_PREVIEW_SPRITE, _LAST_SPRITE + 1):
            tulip.sprite_off(handle)

    # -------------------------------------------------------------------- colour

    def _set_color(self, c, swatch):
        """swatch is the palette entry this now is, or None for a colour that is not
        in the palette, which is what the eyedropper usually finds."""
        old = self.swatch
        self.color = c
        self.swatch = swatch
        if old is not None and old != swatch:
            self._draw_swatch(old)
        if swatch is not None:
            self._draw_swatch(swatch)
        self._draw_color()
        self._draw_status()

    def _choose_swatch(self, i):
        self._set_color(self.palette[i], i)
        if self.tool in ("erase", "pick", "move"):
            self._set_tool("pen")

    def _slide(self, ch, x):
        v = (x - TRACK_X) * 255 // (TRACK_W - 1)
        v = 0 if v < 0 else (255 if v > 255 else v)
        rgb = list(_unpack(self.color))
        rgb[ch] = v
        c = _pack(rgb[0], rgb[1], rgb[2])
        if c == self.color:
            return
        if self.swatch is not None:
            self.palette[self.swatch] = c
            self.dirty = True
        self._set_color(c, self.swatch)

    # ----------------------------------------------------------------- drawing UI

    def _build_buttons(self):
        self.bar_btns = []
        x = PAD
        for (label, fn) in (("New", self._ask_new), ("Open", self._ask_open),
                            ("Save", self._save), ("As", self._ask_save)):
            self.bar_btns.append(_Btn(x, GAP, BAR_BTN_W, BAR_H - 2 * GAP, label, fn))
            x += BAR_BTN_W + GAP
        self.status_x = x + GAP

        cols = (PAD, PAD + TOOL_W + GAP)
        self.left_btns = []
        y = BODY_Y

        def row(a, b):
            nonlocal y
            self.left_btns.append(_Btn(cols[0], y, TOOL_W, TOOL_H, *a))
            self.left_btns.append(_Btn(cols[1], y, TOOL_W, TOOL_H, *b))
            y += TOOL_H + GAP

        is_tool = lambda t: self.tool == t
        tool = lambda label, name: (label, self._set_tool, name, is_tool)
        row(tool("Pen", "pen"), tool("Erase", "erase"))
        row(tool("Fill", "fill"), tool("Pick", "pick"))
        row(tool("Line", "line"), tool("Rect", "rect"))
        row(tool("Box", "box"), tool("Oval", "oval"))
        row(tool("Disc", "disc"), tool("Move", "move"))
        y += _SECTION
        flag = lambda label, name: (label, self._toggle, name,
                                    lambda n: getattr(self, n))
        row(flag("Mir X", "mirror_x"), flag("Mir Y", "mirror_y"))
        row(flag("Grid", "grid"), flag("Onion", "onion"))
        y += _SECTION
        row(("Undo", self._undo), ("Redo", self._redo))
        row(("Flip H", self._flip_h), ("Flip V", self._flip_v))
        row(("Rot 90", self._rot90), ("Clear", self._clear_frame))

        self.preview_btns = []
        px = PANEL_X + PREVIEW_BOX + GAP
        pw = (PANEL_X + PANEL_W - px - 2 * GAP) // 3
        ph = (PREVIEW_H - GAP) // 2
        y2 = PREVIEW_Y + ph + GAP
        self.preview_btns.append(_Btn(px, PREVIEW_Y, pw * 2 + GAP, ph, "Play",
                                      self._toggle_play, None,
                                      lambda _: self.playing))
        self.preview_btns.append(_Btn(px + 2 * (pw + GAP), PREVIEW_Y, pw, ph, "Bg",
                                      self._cycle_preview_bg))
        self.preview_btns.append(_Btn(px, y2, pw, ph, "-", self._fps_step, -1))
        self.fps_x = px + pw + GAP
        self.fps_w = pw
        self.fps_y = y2
        self.fps_h = ph
        self.preview_btns.append(_Btn(px + 2 * (pw + GAP), y2, pw, ph, "+",
                                      self._fps_step, 1))

        self.frame_btns = [
            _Btn(PANEL_X, STRIP_Y, STRIP_ARROW_W, STRIP_H, "<", self._page_strip, -1),
            _Btn(PANEL_X + PANEL_W - STRIP_ARROW_W, STRIP_Y, STRIP_ARROW_W, STRIP_H,
                 ">", self._page_strip, 1),
        ]
        ops = (("Add", "add"), ("Dup", "dup"), ("Del", "del"),
               ("<-", "left"), ("->", "right"))
        bw = (PANEL_W - (len(ops) - 1) * GAP) // len(ops)
        for i in range(len(ops)):
            (label, op) = ops[i]
            self.frame_btns.append(_Btn(PANEL_X + i * (bw + GAP), FRAME_BTN_Y, bw,
                                        FRAME_BTN_H, label, self._frame_op, op))
        self.all_btns = self.bar_btns + self.left_btns + self.preview_btns + self.frame_btns

    def _draw_all(self):
        tulip.bg_rect(0, BAR_H, SW, SH - BAR_H, BG, 1)
        tulip.bg_rect(0, 0, BAR_FILL_W, BAR_H, BAR, 1)
        for b in self.bar_btns:
            b.draw()
        self._draw_status()
        self._draw_canvas()
        self._draw_left()
        self._draw_color()
        for i in range(PAL_COLS * PAL_ROWS):
            self._draw_swatch(i)
        self._draw_preview()
        self._draw_strip()
        for b in self.frame_btns:
            b.draw()

    def _draw_left(self):
        for b in self.left_btns:
            b.draw()

    def _say(self, text):
        self.msg = text
        self._draw_status()

    def _draw_status(self):
        if self.modal is not None:
            return
        name = self.path.split("/")[-1] if self.path else "untitled"
        text = self.msg
        if text is None:
            (r, g, b) = _unpack(self.color)
            text = "%s%s  %dx%d  frame %d/%d  #%02x%02x%02x" % (
                name, "*" if self.dirty else "", self.w, self.h,
                self.cur + 1, len(self.frames), r, g, b)
            if self.at_cell is not None:
                text += "  (%d,%d)" % self.at_cell
        w = SW - BAR_RESERVE - self.status_x
        tulip.bg_rect(self.status_x, 0, w, BAR_H, BAR, 1)
        tulip.bg_str(text, self.status_x, 0, BAR_TEXT, STATUS_FONT, w, BAR_H)

    def _draw_color(self):
        tulip.bg_rect(PANEL_X, BODY_Y, SWATCH, SWATCH, _ink(self.color), 1)
        tulip.bg_rect(PANEL_X, BODY_Y, SWATCH, SWATCH, BTN_EDGE, 0)
        rgb = _unpack(self.color)
        for ch in range(3):
            y = BODY_Y + ch * (SLIDER_H + GAP)
            tulip.bg_rect(SLIDERS_X, y, PANEL_X + PANEL_W - SLIDERS_X, SLIDER_H, BG, 1)
            tulip.bg_str("RGB"[ch], SLIDERS_X, y, BTN_TEXT, BTN_FONT, SLIDER_LABEL_W, SLIDER_H)
            tulip.bg_rect(TRACK_X, y + 2, TRACK_W, SLIDER_H - 4, TRACK, 1)
            fill = rgb[ch] * (TRACK_W - 1) // 255
            if fill:
                tulip.bg_rect(TRACK_X, y + 2, fill, SLIDER_H - 4, CHANNELS[ch], 1)
            tulip.bg_rect(TRACK_X + max(0, fill - 2), y, 5, SLIDER_H, SEL, 1)
            tulip.bg_str(str(rgb[ch]), TRACK_X + TRACK_W, y, BTN_TEXT, BTN_FONT,
                         SLIDER_VALUE_W, SLIDER_H)

    def _swatch_at(self, i):
        return (PANEL_X + (i % PAL_COLS) * PAL_W, PAL_Y + (i // PAL_COLS) * PAL_H)

    def _draw_swatch(self, i):
        (x, y) = self._swatch_at(i)
        tulip.bg_rect(x, y, PAL_W - 1, PAL_H - 1, _ink(self.palette[i]), 1)
        if i == self.swatch:
            tulip.bg_rect(x, y, PAL_W - 1, PAL_H - 1, SEL, 0)
            tulip.bg_rect(x + 1, y + 1, PAL_W - 3, PAL_H - 3, GRID_LINE, 0)

    def _draw_preview(self):
        tulip.bg_rect(PANEL_X, PREVIEW_Y, PREVIEW_BOX, PREVIEW_H,
                      _PREVIEW_BG[self.preview_bg], 1)
        tulip.bg_rect(PANEL_X, PREVIEW_Y, PREVIEW_BOX, PREVIEW_H, BTN_EDGE, 0)
        for b in self.preview_btns:
            b.draw()
        self._draw_fps()

    def _draw_fps(self):
        tulip.bg_rect(self.fps_x, self.fps_y, self.fps_w, self.fps_h, BG, 1)
        tulip.bg_str("%d fps" % self.fps, self.fps_x, self.fps_y, BTN_TEXT, BTN_FONT,
                     self.fps_w, self.fps_h)

    def _slot_x(self, s):
        return PANEL_X + STRIP_ARROW_W + GAP + s * self.slot_w

    def _draw_strip(self):
        n = len(self.frames)
        tulip.bg_rect(PANEL_X + STRIP_ARROW_W, STRIP_Y,
                      PANEL_W - 2 * STRIP_ARROW_W, STRIP_H, BG, 1)
        for s in range(self.slots):
            i = self.strip_first + s
            if i >= n:
                break
            x = self._slot_x(s)
            tulip.bg_rect(x + 1, STRIP_Y, self.slot_w - 2, STRIP_H, PANEL, 1)
            if i == self.cur:
                tulip.bg_rect(x + 1, STRIP_Y, self.slot_w - 2, STRIP_H, BTN_ON, 0)
                tulip.bg_rect(x + 2, STRIP_Y + 1, self.slot_w - 4, STRIP_H - 2, BTN_ON, 0)

    # ------------------------------------------------------------------- actions

    def _set_tool(self, name):
        if name == "pick" and self.tool != "pick":
            self.last_tool = self.tool
        self.tool = name
        self._draw_left()

    def _toggle(self, name):
        setattr(self, name, not getattr(self, name))
        self._draw_left()
        if name == "grid":
            self._draw_canvas()
        elif name == "onion":
            self._show_cur()

    def _undo(self):
        self._step_history(self.undo, self.redo)

    def _redo(self):
        self._step_history(self.redo, self.undo)

    def _toggle_play(self):
        self.playing = not self.playing
        self.play_at = tulip.ticks_ms()
        self.play_pos = self.cur
        if not self.playing:
            self._place_sprites()
        for b in self.preview_btns:
            b.draw()
        self._draw_fps()

    def _fps_step(self, step):
        self.fps = min(30, max(1, self.fps + step))
        self.dirty = True
        self._draw_fps()

    def _cycle_preview_bg(self):
        self.preview_bg = (self.preview_bg + 1) % len(_PREVIEW_BG)
        self._draw_preview()

    # ------------------------------------------------------------------- dialogs

    def _show_modal(self, kind):
        self.modal = kind
        self._sprites_off()
        self._draw_modal()

    def _close_modal(self):
        self.modal = None
        self.dialog_btns = []
        self._draw_all()
        self._place_sprites()

    def _dialog_box(self, w, h, title):
        x = (SW - w) // 2
        y = BAR_H + (SH - BAR_H - h) // 2
        tulip.bg_rect(x, y, w, h, DLG, 1)
        tulip.bg_rect(x, y, w, h, DLG_EDGE, 0)
        tulip.bg_str(title, x, y + 4, BTN_TEXT, STATUS_FONT, w, BAR_H - 8)
        return (x, y)

    def _draw_modal(self):
        self.dialog_btns = []
        getattr(self, "_draw_" + self.modal)()
        for b in self.dialog_btns:
            b.draw()

    def _dialog_btn(self, *args, **kw):
        self.dialog_btns.append(_Btn(*args, **kw))

    def _guarded(self, then):
        """then(), but not over the top of unsaved work without asking."""
        if self.dirty:
            self.confirm_what = ("Discard unsaved changes?", then, None)
            self._show_modal("confirm")
        else:
            then()

    def _draw_confirm(self):
        (w, h) = (min(SW - 80, 620), BAR_H * 3 + 2 * PAD)
        (x, y) = self._dialog_box(w, h, self.confirm_what[0])
        bw = (w - 3 * PAD) // 2
        by = y + h - PAD - BAR_H
        self._dialog_btn(x + PAD, by, bw, BAR_H, "No", self._close_modal)
        self._dialog_btn(x + 2 * PAD + bw, by, bw, BAR_H, "Yes", self._confirmed)

    def _confirmed(self):
        (_, then, arg) = self.confirm_what
        self._close_modal()
        if arg is None:
            then()
        else:
            then(arg)

    # New

    def _ask_new(self):
        self._guarded(lambda: self._show_modal("new"))

    def _new_size(self, what):
        (axis, value) = what
        if axis == "w":
            self.new_w = value
        else:
            self.new_h = value
        self._draw_modal()

    def _draw_new(self):
        btn_h = BAR_H
        w = min(SW - 80, 640)
        h = BAR_H + PAD + 2 * (btn_h + PAD) + PAD + BAR_H + PAD
        (x, y) = self._dialog_box(w, h, "New sprite, %d x %d: up to %d frames" % (
            self.new_w, self.new_h,
            min(MAX_FRAMES, _RAM_PIXELS // (self.new_w * self.new_h))))
        label_w = 90 if _BIG else 70
        bw = (w - label_w - 2 * PAD - (len(SIZES) - 1) * GAP) // len(SIZES)
        ry = y + BAR_H + PAD
        for (axis, label) in (("w", "Width"), ("h", "Height")):
            tulip.bg_str(label, x + PAD, ry, BTN_TEXT, BTN_FONT, label_w, btn_h)
            for i in range(len(SIZES)):
                s = SIZES[i]
                current = self.new_w if axis == "w" else self.new_h
                self._dialog_btn(x + PAD + label_w + i * (bw + GAP), ry, bw, btn_h,
                                 str(s), self._new_size, (axis, s),
                                 on=(lambda _a, on=(s == current): on))
            ry += btn_h + PAD
        bw = (w - 3 * PAD) // 2
        by = y + h - PAD - BAR_H
        self._dialog_btn(x + PAD, by, bw, BAR_H, "Cancel", self._close_modal)
        self._dialog_btn(x + 2 * PAD + bw, by, bw, BAR_H, "Create", self._create)

    def _create(self):
        self._new_doc(self.new_w, self.new_h)
        self.in_ram = []
        self._sync_ram()
        self._close_modal()

    # Open

    def _ask_open(self):
        self._guarded(self._list_files)

    def _list_files(self):
        try:
            names = os.listdir(self.save_dir)
        except OSError:
            names = []
        self.files = sorted(n for n in names if n.lower().endswith(".png"))
        self.file_page = 0
        self._show_modal("open")

    def _file_page_step(self, step):
        self.file_page += step
        self._draw_modal()

    def _draw_open(self):
        rows = 7 if _BIG else 8
        row_h = BAR_H
        w = min(SW - 40, 900)
        h = BAR_H + (rows + 1) * (row_h + GAP) + PAD
        title = "Open from %s" % self.save_dir
        if not self.files:
            title = "No PNG files in %s" % self.save_dir
        (x, y) = self._dialog_box(w, h, title)
        bw = (w - 2 * PAD - GAP) // 2
        per_page = rows * 2
        first = self.file_page * per_page
        for i in range(first, min(first + per_page, len(self.files))):
            cell = i - first
            self._dialog_btn(x + PAD + (cell % 2) * (bw + GAP),
                             y + BAR_H + (cell // 2) * (row_h + GAP),
                             bw, row_h, self.files[i][:40], self._load, self.files[i])
        by = y + h - PAD - BAR_H
        third = (w - 2 * PAD - 2 * GAP) // 3
        if self.file_page > 0:
            self._dialog_btn(x + PAD, by, third, BAR_H, "<", self._file_page_step, -1)
        self._dialog_btn(x + PAD + third + GAP, by, third, BAR_H, "Cancel", self._close_modal)
        if first + per_page < len(self.files):
            self._dialog_btn(x + PAD + 2 * (third + GAP), by, third, BAR_H, ">",
                             self._file_page_step, 1)

    def _load(self, name):
        path = self.save_dir + "/" + name
        self.msg = self._load_file(path)
        self._sync_ram()
        self._close_modal()

    def _load_file(self, path):
        """Replaces the document, or returns what went wrong and leaves it alone."""
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            return "Could not read " + path
        info = png_info(data)
        if info is None:
            return "Not a PNG: " + path
        (w, h, raw_meta) = info
        meta = parse_meta(raw_meta)
        if w < 1 or w > MAX_SIDE or h < 1:
            return "Too wide: this edits up to %d pixels across" % MAX_SIDE
        if w * h > _RAM_PIXELS:
            return "%dx%d does not fit sprite RAM" % (w, h)
        fh = frame_height(w, h, meta)
        if fh is None:
            return "%dx%d: frames up to %d tall only" % (w, h, MAX_SIDE)
        # The decoder writes into sprite RAM, and that is the only way to get pixels
        # out of a PNG here. _sync_ram() puts back whatever it overwrote.
        self.in_ram = []
        try:
            tulip.sprite_png(data, 0)
            native = tulip.sprite_bitmap(0, w * h * (2 if _TRUECOLOR else 1))
        except ValueError as e:
            return "Could not decode %s: %s" % (path, e)
        n = h // fh
        self._new_doc(w, fh, _from_native(native, w, fh, n), path)
        if "fps" in meta:
            self.fps = min(30, max(1, meta["fps"]))
        if "pal" in meta:
            self.palette = [c for c in meta["pal"]]
            self.swatch = None
        return None

    # Save

    def _meta(self):
        return ("frame_h=%d;fps=%d;pal=%s" % (
            self.h, self.fps, "".join("%04x" % c for c in self.palette))).encode()

    def _save(self):
        if self.path is None:
            self._ask_save()
        else:
            self._write(self.path)

    def _ask_save(self):
        name = self.path.split("/")[-1] if self.path else ""
        self.typed = name[:-4] if name.lower().endswith(".png") else name
        self._show_modal("save")

    def _draw_field(self):
        (x, y, w, h) = self.field
        tulip.bg_rect(x, y, w, h, FIELD, 1)
        tulip.bg_rect(x, y, w, h, BTN_EDGE, 0)
        tulip.bg_str(self.typed + "|.png", x, y, BTN_TEXT, STATUS_FONT, w, h)

    def _draw_save(self):
        cols = len(_KEYBOARD_ROWS[0])
        key_w = 60 if _BIG else 50
        key_h = BAR_H
        w = cols * (key_w + GAP) + 2 * PAD
        h = BAR_H + key_h + PAD + (len(_KEYBOARD_ROWS) + 1) * (key_h + GAP) + 2 * PAD
        (x, y) = self._dialog_box(w, h, "Save to %s" % self.save_dir)
        self.field = (x + PAD, y + BAR_H, w - 2 * PAD, key_h)
        self._draw_field()
        ry = y + BAR_H + key_h + PAD
        for row in _KEYBOARD_ROWS:
            kx = x + PAD
            for ch in row:
                self._dialog_btn(kx, ry, key_w, key_h, ch, self._type, ch)
                kx += key_w + GAP
            if row == _KEYBOARD_ROWS[-1]:
                self._dialog_btn(kx, ry, x + w - PAD - kx - GAP, key_h, "Delete",
                                 self._backspace)
            ry += key_h + GAP
        bw = (w - 3 * PAD) // 2
        self._dialog_btn(x + PAD, ry, bw, key_h, "Cancel", self._close_modal)
        self._dialog_btn(x + 2 * PAD + bw, ry, bw, key_h, "Save", self._save_typed)

    def _type(self, ch):
        if len(self.typed) < 24:
            self.typed += ch
            self._draw_field()

    def _backspace(self):
        self.typed = self.typed[:-1]
        self._draw_field()

    def _save_typed(self):
        if not self.typed:
            return
        path = self.save_dir + "/" + self.typed + ".png"
        if path != self.path and tulip.exists(path):
            self.confirm_what = ("Replace %s.png?" % self.typed, self._write, path)
            self.modal = "confirm"
            tulip.bg_rect(0, BAR_H, SW, SH - BAR_H, BG, 1)
            self._draw_modal()
            return
        self._close_modal()
        self._write(path)

    def _write(self, path):
        # About 3 s for the most a file can hold, seven frames at 64x64, nearly all
        # of it this Python loop over every pixel and then over every byte.
        self._say("Saving...")
        data = encode_png(self.frames, self.w, self.h, self._meta())
        tmp = path + ".tmp"
        try:
            with open(tmp, "wb") as f:
                f.write(data)
            try:
                os.rename(tmp, path)
            except OSError:
                os.remove(path)
                os.rename(tmp, path)
        except OSError as e:
            self._say("Save failed: %s" % e)
            return False
        self.path = path
        self.dirty = False
        self._say("Saved %s, %d bytes" % (path.split("/")[-1], len(data)))
        return True

    # --------------------------------------------------------------------- input

    def _touch(self, up):
        try:
            point = tulip.touch()
        except Exception:
            return
        (x, y) = (point[0], point[1])
        if up:
            self.finger_down = False
            self._release()
            return
        if x < 0 or y < 0:
            return
        if self.finger_down:
            self._hold(x, y)
            return
        self.finger_down = True
        self.msg = None
        self._press(x, y)

    def _press(self, x, y):
        if self.modal is not None:
            for b in self.dialog_btns:
                if b.hit(x, y):
                    b.fire()
                    return
            return
        if self._on_canvas(x, y):
            self.grabbed = "canvas"
            (cx, cy) = self._cell(x, y)
            self._stroke_begin(cx, cy)
            return
        for ch in range(3):
            sy = BODY_Y + ch * (SLIDER_H + GAP)
            if SLIDERS_X <= x < PANEL_X + PANEL_W and sy <= y < sy + SLIDER_H:
                self.grabbed = ch
                self._slide(ch, x)
                return
        if PANEL_X <= x < PANEL_X + PAL_COLS * PAL_W and PAL_Y <= y < PAL_Y + PAL_ROWS * PAL_H:
            self._choose_swatch((y - PAL_Y) // PAL_H * PAL_COLS + (x - PANEL_X) // PAL_W)
            return
        for b in self.all_btns:
            if b.hit(x, y):
                b.fire()
                return
        if STRIP_Y <= y < STRIP_Y + STRIP_H:
            s = (x - self._slot_x(0)) // self.slot_w
            if 0 <= s < self.slots:
                self._goto(self.strip_first + s)

    def _hold(self, x, y):
        if self.grabbed == "canvas":
            (cx, cy) = self._cell(x, y)
            self._stroke_move(cx, cy)
        elif self.grabbed is not None:
            self._slide(self.grabbed, x)

    def _release(self):
        grabbed = self.grabbed
        self.grabbed = None
        if grabbed == "canvas":
            self._stroke_end()
            self._draw_status()

    def _read_keys(self):
        if not hasattr(tulip, "keys"):
            return
        held = tulip.keys()[1:]
        for code in held:
            if code and code not in self.keys_held:
                self._on_key(code)
        self.keys_held = held

    def _on_key(self, code):
        if self.modal == "save":
            if code in _NAME_KEYS:
                self._type(_NAME_KEYS[code])
            elif code == _KEY_BACKSPACE:
                self._backspace()
            elif code == _KEY_ENTER:
                self._save_typed()
            elif code == _KEY_ESC:
                self._close_modal()
            return
        if self.modal is not None:
            if code == _KEY_ESC:
                self._close_modal()
            return
        action = _KEYMAP.get(code)
        if action is None or self.grabbed is not None:
            return
        self.msg = None
        if action in ("pen", "erase", "fill", "pick", "line", "rect", "box", "oval",
                      "disc", "move"):
            self._set_tool(action)
        elif action in ("mirror_x", "mirror_y", "grid", "onion"):
            self._toggle(action)
        elif action == "quit":
            self.quitting = True
            tulip.frame_callback()
            tulip.defer(lambda app: app.quit(), self.app, 20)
        else:
            {"undo": self._undo, "redo": self._redo, "save": self._save,
             "play": self._toggle_play, "prev": self._prev, "next": self._next}[action]()

    # ----------------------------------------------------------------- lifecycle

    def activate(self, app):
        # A sprite left on by whatever ran before would sit on top of the panels.
        # Not sprite_clear(): that wipes handle 0 and the RAM behind it, which is
        # the mouse pointer.
        self._sprites_off()
        self.in_ram = []
        self._sync_ram()
        self.finger_down = False
        self.grabbed = None
        self.keys_held = ()
        self.at_cell = None
        if self.modal is not None:
            self.modal = None
            self.dialog_btns = []
        self._draw_all()
        self._place_sprites()
        try:
            tulip.touch_callback(self._touch)
        except Exception:
            pass                        # no touch panel; the keyboard still works
        tulip.frame_callback(self._tick, app)

    def deactivate(self, app):
        # A stroke still in progress lands as it stands.
        if self.grabbed == "canvas":
            self._release()
        self.grabbed = None
        tulip.frame_callback()
        try:
            tulip.touch_callback()
        except Exception:
            pass

    def on_quit(self, app):
        # The quit button is UIScreen's and there is no asking from here, so
        # unsaved work goes somewhere it can be found rather than nowhere.
        if self.dirty:
            path = self.save_dir + "/" + _RECOVER_NAME
            try:
                with open(path, "wb") as f:
                    f.write(encode_png(self.frames, self.w, self.h, self._meta()))
                print("spriteedit: unsaved work kept in " + path)
            except OSError as e:
                print("spriteedit: could not keep unsaved work: %s" % e)
        self._sprites_off()

    def _tick(self, app):
        # The frame scheduler can still reach us after a switch away or a quit.
        if not app.active or self.quitting:
            return
        try:
            self._frame_tick()
        except Exception as e:
            import sys
            tulip.frame_callback()
            print("spriteedit: stopped on an error")
            sys.print_exception(e)

    def _frame_tick(self):
        self._read_keys()
        if self.modal is not None:
            return
        now = tulip.ticks_ms()
        # While painting, the preview follows at ten updates a second. It costs a
        # copy of the frame, and on a palette board a loop over it.
        if self.cur_ram_stale and now - self.ram_pushed_at >= 100:
            self._sync_ram()
        if self.playing and now - self.play_at >= 1000 // self.fps:
            self.play_at = now
            self.play_pos = (self.play_pos + 1) % len(self.frames)
            tulip.sprite_register(_PREVIEW_SPRITE, self.play_pos * self.w * self.h,
                                  self.w, self.h)
            tulip.sprite_move(_PREVIEW_SPRITE,
                              PANEL_X + (PREVIEW_BOX - self.w) // 2,
                              PREVIEW_Y + (PREVIEW_H - self.h) // 2)


def run(app):
    app.game = True
    editor = SpriteEdit(app)
    app.spriteedit = editor
    app.activate_callback = editor.activate
    app.deactivate_callback = editor.deactivate
    app.quit_callback = editor.on_quit
    app.present()
