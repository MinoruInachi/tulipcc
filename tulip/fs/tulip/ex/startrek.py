"""STAR TREK -- the 1970s mainframe game, given a bridge to play it from.

    run('startrek')

This is the game that ran on every teletype in the 1970s (Mike Mayfield's
original, by way of the 1974 "Super Star Trek"): the Klingons have invaded a
galaxy of 8x8 quadrants, each one 8x8 sectors, and the Enterprise has a fixed
number of stardates to destroy every one of them. The rules, the numbers and
the messages are the classic ones -- 3000 units of energy, ten photon
torpedoes, phaser energy split between the targets and falling off with range,
shields that have to hold or the ship is lost, eight ship's systems that the
Klingons can knock out and that repair themselves while the ship travels, and
starbases to dock at to refuel, rearm and repair. What is new is that it is no
longer typed: the short range scan is a live picture of the quadrant, the
computer's galactic record is a map, and the commands are buttons.

How to play
-----------
Tap a sector on the scanner (the big grid on the left) to aim at it, then:
TORPEDO fires a photon torpedo at it, IMPULSE flies there. With nothing aimed,
TORPEDO goes for the nearest Klingon. Tap a quadrant on the galaxy map (top
right) and WARP takes the ship there. PHASERS fire at every Klingon in the
quadrant at once, with as much energy as you give them; SHIELDS sets how much
energy stands between the ship and their disruptors. DAMAGE reports the state
of the ship's systems, and offers repairs while docked. Fly next to a starbase
to dock.

Keys: W warp, I impulse, P phasers, T torpedo, S shields, D damage, H help,
N new game, ESC quit. The arrows move the aim, and TAB moves it between the
scanner and the map. In a slider, left/right (and up/down, finer) set the
amount, Enter confirms and ESC cancels.

Why it is built the way it is
-----------------------------
Everything is drawn on the BG plane, as in solitaire and starfall. The game
is turn-based, so nothing is redrawn per frame: each sector of the scanner
paints itself into its own square (_draw_cell), and anything that flies over
the scanner -- a torpedo, a phaser beam, a disruptor bolt, an explosion -- is
cleaned up by repainting the squares it touched (_restore_box, _restore_line),
never the whole grid. The panels on the right repaint only when what they
show changes.

Every command that takes time on screen is a generator. It changes the game
and draws as it goes, and yields how many milliseconds to wait before its
next step; the frame callback is what steps it (_tick), and input is held off
until it has finished. That keeps the rules readable in the order they happen
-- the torpedo flies, the Klingon blows up, the survivors fire back -- with
no state machine between them.

The ships are drawn from triangles and circles in a 24-unit cell and turned
to their heading (_shape), so the Enterprise points the way it last flew and
every Klingon turns to face it.
"""

import math
import random

import tulip

try:
    import amy
except ImportError:
    amy = None

_BIG = tulip.board() == "TAB5"
_TRUECOLOR = _BIG
_ALPHA = 0x55


def _c(r, g, b):
    """A colour for the BG plane: an (r,g,b) where that is supported, else the
    nearest palette index."""
    if _TRUECOLOR:
        return (r, g, b)
    pal = tulip.color(r, g, b)
    # 0x55 is the transparent entry; a colour landing on it would punch a hole
    # in the plane rather than draw.
    return pal + 1 if pal == _ALPHA else pal


SPACE = _c(2, 3, 10)
SRS_BG = _c(5, 8, 20)
SRS_GRID = _c(22, 32, 62)
SPECK = _c(70, 80, 110)
PANEL = _c(10, 16, 32)
PANEL_EDGE = _c(46, 66, 116)
TEXT = _c(214, 222, 236)
DIM = _c(110, 122, 150)
FAINT = _c(44, 54, 80)
AMBER = _c(255, 168, 40)
GOOD = _c(120, 228, 140)
WARN = _c(255, 200, 70)
BAD = _c(255, 92, 80)
BLACK = _c(0, 0, 0)
WHITE = _c(255, 255, 255)
SEL = _c(255, 224, 64)
BAR = _c(14, 12, 26)

HULL = _c(204, 208, 220)
HULL_DARK = _c(140, 146, 162)
HULL_LIGHT = _c(236, 240, 248)
BUSSARD = _c(255, 84, 60)
DEFLECTOR = _c(255, 170, 70)
NACELLE_GLOW = _c(110, 170, 255)

K_HULL = _c(120, 132, 108)
K_DARK = _c(78, 88, 70)
K_GLOW = _c(255, 60, 40)

BASE_RING = _c(170, 178, 196)
BASE_HUB = _c(120, 200, 255)
BASE_POD = _c(60, 110, 220)
BASE_LIGHT = _c(255, 240, 160)

PHASER = _c(255, 140, 30)
PHASER_CORE = _c(255, 240, 150)
DISRUPTOR = _c(90, 255, 120)
DISRUPTOR_CORE = _c(210, 255, 210)
SHIELD = _c(90, 190, 255)
TORP = _c(255, 80, 40)
TORP_CORE = _c(255, 230, 180)
FIRE1 = _c(255, 250, 210)
FIRE2 = _c(255, 200, 60)
FIRE3 = _c(240, 100, 30)
FIRE4 = _c(150, 40, 20)

# A star is one of these, outer glow to core.
STAR_KINDS = (
    (_c(90, 70, 10), _c(200, 160, 30), _c(255, 230, 110), _c(255, 255, 230)),
    (_c(90, 40, 10), _c(210, 110, 30), _c(255, 170, 80), _c(255, 240, 210)),
    (_c(20, 40, 90), _c(80, 130, 230), _c(170, 210, 255), _c(255, 255, 255)),
    (_c(80, 20, 20), _c(190, 60, 40), _c(255, 120, 90), _c(255, 220, 200)),
)

COND_COLOURS = {
    "GREEN": _c(28, 128, 58),
    "YELLOW": _c(196, 150, 20),
    "RED": _c(200, 34, 34),
    "DOCKED": _c(34, 96, 186),
}
RED_DIM = _c(96, 18, 18)

# LCARS-ish pills, one colour per kind of command.
BTN_COLOURS = {
    "warp": _c(153, 153, 255),
    "impulse": _c(153, 170, 230),
    "phasers": _c(255, 153, 0),
    "torpedo": _c(220, 110, 90),
    "shields": _c(110, 170, 220),
    "damage": _c(255, 204, 153),
    "help": _c(204, 153, 204),
    "new": _c(170, 120, 170),
}

# ------------------------------------------------------------------- geometry

(SW, SH) = tulip.screen_size()

PAD = 12 if _BIG else 8
# The top strip leaves room for UIScreen's task bar on the right; on a Tab5
# LVGL is composited over the BG, so the strip's colour can run the full width
# there, while anywhere else that would paint the task bar out. (The same rule
# as solitaire.)
BAR_H = 56 if _BIG else 34
BAR_RESERVE = 124 if _BIG else 100
BAR_FILL_W = SW if _BIG else SW - BAR_RESERVE

# The scanner is square and as tall as the screen allows, but never more than
# about half the width: the panels on the right need the rest.
C = min((SH - BAR_H - 2 * PAD) // 8, (SW * 52 // 100) // 8)
SRS_X = PAD
SRS_Y = BAR_H + PAD
SRS_W = 8 * C

RX = SRS_X + SRS_W + PAD + 4
RY = SRS_Y
RW = SW - PAD - RX

# Tulip fonts (tulip/shared/u8g2_fonts.c): 18 lubB24, 17 logisoso24, 15 luRS18,
# 5 helvB14, 16 osb18, 9 8x13, 8 6x13. CHW is the width of an upper-case
# character, measured off a Tab5 screenshot for the proportional faces, and it
# is all the wrapping below has to go on: there is no way to measure a string
# before drawing it.
if _BIG:
    F_TITLE, F_TEXT, F_SMALL, F_BTN = 18, 15, 5, 17
    LH, ASC, CHW, CHW_SMALL, CHW_TITLE = 27, 19, 15, 10, 22
else:
    F_TITLE, F_TEXT, F_SMALL, F_BTN = 16, 8, 8, 9
    LH, ASC, CHW, CHW_SMALL, CHW_TITLE = 16, 11, 6, 6, 14

# The galaxy map, with a strip of row and column numbers along two sides.
LBL = 18 if _BIG else 12
G = (RW * 50 // 100 - LBL) // 8
MAP_X = RX + LBL
MAP_Y = RY + LBL
MAP_W = LBL + 8 * G
TOP_H = MAP_W

# The status panel beside it.
ST_X = RX + MAP_W + PAD
ST_W = RX + RW - ST_X
COND_H = 40 if _BIG else 24
GAUGE_H = 12 if _BIG else 7
GAP = 6 if _BIG else 4

# Command buttons: two rows of four along the bottom.
BTN_H = 64 if _BIG else 40
BTN_GAP = 8 if _BIG else 6
BTNS_H = 2 * BTN_H + BTN_GAP
BTNS_Y = SH - PAD - BTNS_H
BTN_W = (RW - 3 * BTN_GAP) // 4

# The log fills what is left between the map and the buttons.
LOG_X = RX
LOG_Y = RY + TOP_H + PAD
LOG_W = RW
LOG_H = BTNS_Y - PAD - LOG_Y
LOG_PAD = 8 if _BIG else 5
LOG_LINES = max(3, (LOG_H - 2 * LOG_PAD) // LH)
LOG_CHARS = (LOG_W - 2 * LOG_PAD) // CHW

_BUTTONS = ("warp", "impulse", "phasers", "torpedo",
            "shields", "damage", "help", "new")
_BUTTON_TEXT = {"warp": "WARP", "impulse": "IMPULSE", "phasers": "PHASERS",
                "torpedo": "TORPEDO", "shields": "SHIELDS", "damage": "DAMAGE",
                "help": "HELP", "new": "NEW GAME"}

# HID usage ids. a is 4, so a letter is 4 + its place in the alphabet.
_KEYMAP = {26: "warp", 12: "impulse", 19: "phasers", 23: "torpedo",
           22: "shields", 7: "damage", 11: "help", 17: "new"}
K_ENTER, K_ESC, K_TAB, K_SPACE = 40, 41, 43, 44
K_RIGHT, K_LEFT, K_DOWN, K_UP = 79, 80, 81, 82

# ------------------------------------------------------------------ the rules
# These are Super Star Trek's numbers.

E0 = 3000                       # full energy
P0 = 10                         # full load of torpedoes
K_ENERGY = 200                  # a Klingon starts with 100..300 of it
TORP_SPEED = 9.0                # sectors a second, on screen

EMPTY, SHIP, KLINGON, BASE, STAR = 0, 1, 2, 3, 4

WARP, SRS, LRS, PHA, TOR, DMG, SHE, COM = range(8)
DEVICES = ("WARP ENGINES", "SHORT RANGE SENSORS", "LONG RANGE SENSORS",
           "PHASER CONTROL", "PHOTON TUBES", "DAMAGE CONTROL",
           "SHIELD CONTROL", "LIBRARY-COMPUTER")
DEVICE_TAGS = ("WRP", "SRS", "LRS", "PHA", "TOR", "DMG", "SHD", "COM")

_NAMES_W = ("ANTARES", "RIGEL", "PROCYON", "VEGA", "CANOPUS", "ALTAIR",
            "SAGITTARIUS", "POLLUX")
_NAMES_E = ("SIRIUS", "DENEB", "CAPELLA", "BETELGEUSE", "ALDEBARAN",
            "REGULUS", "ARCTURUS", "SPICA")
_ROMAN = ("I", "II", "III", "IV")


def _qname(qx, qy):
    return (_NAMES_W if qx < 4 else _NAMES_E)[qy] + " " + _ROMAN[qx % 4]


def _rc(x, y):
    """A position the way the game has always printed one: row,column from 1."""
    return "%d,%d" % (y + 1, x + 1)


def _rnd():
    return random.random()


def _round(v):
    return int(math.floor(v + 0.5))


def _hash(n):
    n = (n * 1103515245 + 12345) & 0x7FFFFFFF
    return (n ^ (n >> 13)) & 0x7FFFFFFF


def _wrap(text, n):
    if len(text) <= n:
        return [text]
    lines = []
    cur = ""
    for word in text.split(" "):
        if cur and len(cur) + 1 + len(word) > n:
            lines.append(cur)
            cur = "  " + word
        else:
            cur = word if not cur else cur + " " + word
    if cur:
        lines.append(cur)
    return lines


# --------------------------------------------------------------------- sound
# Fire-and-forget voices on oscillators of their own, as starfall does it. Every
# envelope reaches zero while the note is still held, because nothing here
# ever sends a note-off. Frequencies are a base plus EG1 in octaves, so a
# sweep is one breakpoint list.

OSC_PHA, OSC_TORP, OSC_BOOM, OSC_DIS, OSC_WARP, OSC_ALERT, OSC_BEEP = range(60, 67)
OSCS = (OSC_PHA, OSC_TORP, OSC_BOOM, OSC_DIS, OSC_WARP, OSC_ALERT, OSC_BEEP)


def _send(**kw):
    if amy is None:
        return
    try:
        amy.send(**kw)
    except Exception:
        pass


def _start_sounds():
    if amy is None:
        return
    lpf = getattr(amy, "FILTER_LPF", 1)
    _send(osc=OSC_PHA, wave=amy.SAW_DOWN, freq="700,0,0,0,1",
          bp1="0,1,650,0,0,0", bp0="0,1,600,0.7,700,0,0,0",
          filter_type=lpf, filter_freq="3200", vel=0)
    _send(osc=OSC_TORP, wave=amy.SINE, freq="170,0,0,0,2.5",
          bp1="0,1,450,0,0,0", bp0="0,1,450,0,0,0", vel=0)
    _send(osc=OSC_BOOM, wave=amy.NOISE, filter_type=lpf,
          filter_freq="140,0,0,0,4.5", bp1="0,1,1100,0,0,0",
          bp0="0,1,1200,0,0,0", vel=0)
    _send(osc=OSC_DIS, wave=amy.PULSE, freq="90,0,0,0,2",
          bp1="0,1,300,0,0,0", bp0="0,1,350,0,0,0", vel=0)
    _send(osc=OSC_WARP, wave=amy.SINE, freq="110,0,0,0,3",
          bp1="0,0,900,1,0,1", bp0="0,0,200,1,1000,1,1300,0,0,0", vel=0)
    _send(osc=OSC_ALERT, wave=amy.PULSE, freq="330,0,0,0,1",
          bp1="0,0,450,1,0,1", bp0="0,1,450,1,520,0,0,0",
          filter_type=lpf, filter_freq="1600", vel=0)
    _send(osc=OSC_BEEP, wave=amy.SINE, freq="1320,0",
          bp0="0,1,90,0,0,0", vel=0)


def _sfx(osc, vel=0.4, freq=None):
    if freq is not None:
        _send(osc=osc, freq=freq, vel=vel)
    else:
        _send(osc=osc, vel=vel)


def _silence():
    for osc in OSCS:
        _send(osc=osc, vel=0)


# --------------------------------------------------------------- ship art
# Shapes are lists of parts in a 24-unit cell, nose up, turned to a heading
# and scaled by _shape(): ("p", points, colour) is a convex polygon,
# ("c", (x, y), r, colour) a filled circle, ("l", p0, p1, colour, width) a
# line.

_ENTERPRISE = (
    # nacelles and the pylons that hold them
    ("l", (-1.4, 7.4), (-5.3, 5.2), HULL_DARK, 2),
    ("l", (1.4, 7.4), (5.3, 5.2), HULL_DARK, 2),
    ("p", ((-6.6, 2.0), (-4.4, 2.0), (-4.4, 11.6), (-6.6, 11.6)), HULL),
    ("p", ((4.4, 2.0), (6.6, 2.0), (6.6, 11.6), (4.4, 11.6)), HULL),
    ("p", ((-6.6, 10.4), (-4.4, 10.4), (-4.4, 11.6), (-6.6, 11.6)), NACELLE_GLOW),
    ("p", ((4.4, 10.4), (6.6, 10.4), (6.6, 11.6), (4.4, 11.6)), NACELLE_GLOW),
    ("c", (-5.5, 2.2), 1.2, BUSSARD),
    ("c", (5.5, 2.2), 1.2, BUSSARD),
    # engineering hull, neck, deflector
    ("p", ((0.0, 2.6), (2.1, 4.2), (2.1, 9.2), (0.0, 10.8), (-2.1, 9.2),
           (-2.1, 4.2)), HULL_DARK),
    ("p", ((-0.9, -1.0), (0.9, -1.0), (1.1, 4.4), (-1.1, 4.4)), HULL_DARK),
    ("c", (0.0, 3.4), 1.0, DEFLECTOR),
    # the saucer
    ("c", (0.0, -4.2), 6.4, HULL),
    ("c", (0.0, -4.2), 4.2, HULL_LIGHT),
    ("c", (0.0, -4.2), 1.7, HULL_DARK),
)

_KLINGON = (
    # wings, nacelles at their tips, the boom and the command pod
    ("p", ((0.0, -0.6), (9.4, 5.4), (9.4, 7.6), (0.0, 5.0)), K_HULL),
    ("p", ((0.0, -0.6), (0.0, 5.0), (-9.4, 7.6), (-9.4, 5.4)), K_HULL),
    ("p", ((-3.2, 1.2), (3.2, 1.2), (2.6, 8.8), (-2.6, 8.8)), K_DARK),
    ("p", ((8.0, 2.0), (10.6, 2.0), (10.6, 9.4), (8.0, 9.4)), K_DARK),
    ("p", ((-10.6, 2.0), (-8.0, 2.0), (-8.0, 9.4), (-10.6, 9.4)), K_DARK),
    ("c", (9.3, 9.4), 0.9, K_GLOW),
    ("c", (-9.3, 9.4), 0.9, K_GLOW),
    ("p", ((-0.8, -6.6), (0.8, -6.6), (1.0, 1.4), (-1.0, 1.4)), K_HULL),
    ("c", (0.0, -8.4), 2.8, K_HULL),
    ("c", (0.0, -8.4), 1.4, K_DARK),
    ("c", (0.0, -10.4), 0.8, K_GLOW),
)


def _shape(cx, cy, k, heading, parts):
    co = math.cos(heading) * k
    si = math.sin(heading) * k
    for part in parts:
        kind = part[0]
        if kind == "p":
            pts = [(cx + _round(x * co - y * si), cy + _round(x * si + y * co))
                   for (x, y) in part[1]]
            (x0, y0) = pts[0]
            for i in range(1, len(pts) - 1):
                tulip.bg_triangle(x0, y0, pts[i][0], pts[i][1],
                                  pts[i + 1][0], pts[i + 1][1], part[2], 1)
        elif kind == "c":
            (x, y) = part[1]
            r = max(1, _round(part[2] * k))
            tulip.bg_circle(cx + _round(x * co - y * si),
                            cy + _round(x * si + y * co), r, part[3], 1)
        else:
            (x0, y0) = part[1]
            (x1, y1) = part[2]
            tulip.bg_line(cx + _round(x0 * co - y0 * si),
                          cy + _round(x0 * si + y0 * co),
                          cx + _round(x1 * co - y1 * si),
                          cy + _round(x1 * si + y1 * co),
                          part[3], max(1, _round(part[4] * k / 2)))


def _heading(dx, dy):
    """The heading that points the nose (art's -y) along (dx, dy)."""
    return math.atan2(dx, -dy)


# ------------------------------------------------------------------ the game


class StarTrek:
    def __init__(self, app):
        self.app = app
        self.quitting = False
        self.keys_held = ()
        self.finger_down = False
        self.dragging = False
        self.now = 0
        self.script = None
        self.wake = 0
        self.blink_at = 0
        self.blink_on = True
        self.buttons = []
        for (i, name) in enumerate(_BUTTONS):
            bx = RX + (i % 4) * (BTN_W + BTN_GAP)
            by = BTNS_Y + (i // 4) * (BTN_H + BTN_GAP)
            self.buttons.append((name, bx, by))
        self._new_game()

    # --------------------------------------------------------------- lifecycle

    def activate(self, app):
        self.now = tulip.ticks_ms()
        tulip.sprite_clear()
        tulip.Sprite.reset()
        self.finger_down = False
        self.dragging = False
        self.keys_held = ()
        _start_sounds()
        self._draw_all()
        try:
            tulip.touch_callback(self._touch)
        except Exception:
            pass                        # no touch panel; the keyboard still works
        tulip.frame_callback(self._tick, app)

    def deactivate(self, app):
        tulip.frame_callback()
        try:
            tulip.touch_callback()
        except Exception:
            pass
        _silence()

    # ------------------------------------------------------------- new game

    def _new_game(self):
        random.seed(tulip.ticks_ms())
        self.gal = []
        self.known = []
        k9 = 0
        b9 = 0
        for qy in range(8):
            row = []
            for qx in range(8):
                r = _rnd()
                k = 3 if r > 0.98 else (2 if r > 0.95 else (1 if r > 0.80 else 0))
                b = 1 if _rnd() > 0.96 else 0
                s = random.randint(1, 8)
                k9 += k
                b9 += b
                row.append([k, b, s])
            self.gal.append(row)
            self.known.append([None] * 8)
        self.qx = random.randint(0, 7)
        self.qy = random.randint(0, 7)
        self.sx = random.randint(0, 7)
        self.sy = random.randint(0, 7)
        if b9 == 0:
            # Super Star Trek's rule: no starbase anywhere puts one here, with
            # a Klingon or two to guard it.
            q = self.gal[self.qy][self.qx]
            if q[0] < 2:
                q[0] += 1
                k9 += 1
            q[1] = 1
            b9 = 1
        if k9 == 0:
            self.gal[(self.qy + 3) % 8][(self.qx + 3) % 8][0] = 1
            k9 = 1
        self.k_start = k9
        self.b_start = b9
        self.t0 = float(int(_rnd() * 20 + 20) * 100)
        self.t = self.t0
        self.t9 = 25 + int(_rnd() * 10)
        if k9 > self.t9:
            self.t9 = k9 + 1
        self.energy = float(E0)
        self.shields = 0.0
        self.torps = P0
        self.dmg = [0.0] * 8
        self.docked = False
        self.over = None
        self.heading = 0.0
        self.sel_sector = None
        self.sel_quad = None
        self.focus = "srs"
        self.picker = None
        self.overlay = None
        self.script = None
        self.log = []
        self.phaser_last = 0
        self.repair_est = None
        self._enter_quadrant(self.qx, self.qy, self.sx, self.sy)
        self._check_docked()
        self._say("STARDATE %.1f. CAPTAIN'S LOG:" % self.t, AMBER)
        self._say("%d KLINGON WARSHIPS HAVE INVADED THE GALAXY." % self.k_start)
        self._say("NOW IN %s QUADRANT." % _qname(self.qx, self.qy))
        if self.klingons:
            self._say("COMBAT AREA      CONDITION RED", BAD)
            self._say("   SHIELDS DANGEROUSLY LOW", BAD)
        self._show_briefing()

    def _klingons_left(self):
        n = 0
        for row in self.gal:
            for q in row:
                n += q[0]
        return n

    def _bases_left(self):
        n = 0
        for row in self.gal:
            for q in row:
                n += q[1]
        return n

    def _days_left(self):
        return self.t0 + self.t9 - self.t

    # --------------------------------------------------------- the quadrant

    def _empty_cell(self):
        while True:
            i = random.randint(0, 63)
            if self.grid[i] == EMPTY:
                return i

    def _enter_quadrant(self, qx, qy, sx, sy):
        self.qx = qx
        self.qy = qy
        self.sx = sx
        self.sy = sy
        self.grid = bytearray(64)
        self.grid[sy * 8 + sx] = SHIP
        (k, b, s) = self.gal[qy][qx]
        self.klingons = []
        for _ in range(k):
            i = self._empty_cell()
            self.grid[i] = KLINGON
            self.klingons.append([i % 8, i // 8, K_ENERGY * (0.5 + _rnd())])
        self.base = None
        if b:
            i = self._empty_cell()
            self.grid[i] = BASE
            self.base = (i % 8, i // 8)
        for _ in range(s):
            self.grid[self._empty_cell()] = STAR
        self.sel_sector = None
        if self.sel_quad == (qx, qy):
            self.sel_quad = None
        self._record()

    def _record(self):
        """Write what the sensors can see into the computer's galactic record:
        this quadrant always, and its neighbours while long range sensors
        work."""
        reach = 1 if self.dmg[LRS] >= 0 else 0
        for qy in range(self.qy - reach, self.qy + reach + 1):
            for qx in range(self.qx - reach, self.qx + reach + 1):
                if 0 <= qx < 8 and 0 <= qy < 8:
                    q = self.gal[qy][qx]
                    self.known[qy][qx] = (q[0], q[1], q[2])

    def _check_docked(self):
        was = self.docked
        self.docked = False
        for y in range(self.sy - 1, self.sy + 2):
            for x in range(self.sx - 1, self.sx + 2):
                if 0 <= x < 8 and 0 <= y < 8 and self.grid[y * 8 + x] == BASE:
                    self.docked = True
        if self.docked:
            self.energy = float(E0)
            self.torps = P0
            if not was:
                if self.shields > 0:
                    self._say("SHIELDS DROPPED FOR DOCKING PURPOSES", DIM)
                self.shields = 0.0
                self._say("DOCKED AT STARBASE. ENERGY AND TORPEDOES "
                          "REPLENISHED.", GOOD)
                _sfx(OSC_BEEP, 0.3, "880,0")
                tulip.defer(lambda _: _sfx(OSC_BEEP, 0.3, "1320,0"), 0, 180)

    def _condition(self):
        if self.docked:
            return "DOCKED"
        if self.klingons:
            return "RED"
        if self.energy < E0 * 0.1:
            return "YELLOW"
        return "GREEN"

    def _dist(self, x, y):
        dx = x - self.sx
        dy = y - self.sy
        return math.sqrt(dx * dx + dy * dy)

    def _klingon_at(self, x, y):
        for k in self.klingons:
            if k[0] == x and k[1] == y:
                return k
        return None

    def _remove_klingon(self, k):
        self.klingons.remove(k)
        self.grid[k[1] * 8 + k[0]] = EMPTY
        self.gal[self.qy][self.qx][0] -= 1
        self._record()

    # --------------------------------------------------------------- drawing

    def _draw_all(self):
        tulip.bg_rect(0, BAR_H, SW, SH - BAR_H, SPACE, 1)
        self._draw_bar()
        self._draw_srs()
        self._draw_map()
        self._draw_status()
        if self.picker is not None:
            self._draw_picker()
        else:
            self._draw_log()
        self._draw_buttons()

    def _draw_bar(self):
        tulip.bg_rect(0, 0, BAR_FILL_W, BAR_H, BAR, 1)
        tulip.bg_rect(0, BAR_H - 3, BAR_FILL_W, 3, AMBER, 1)
        tulip.bg_str("STAR TREK", PAD, 0, AMBER, F_TITLE, 9 * CHW_TITLE, BAR_H - 3)
        x = PAD + 9 * CHW_TITLE + 3 * PAD
        w = SW - BAR_RESERVE - x
        if w < 80:
            return
        days = self._days_left()
        if days < 0:
            days = 0
        text = "STARDATE %.1f   DAYS LEFT %.1f   KLINGONS %d   STARBASES %d" % (
            self.t, days, self._klingons_left(), self._bases_left())
        # Spaces and digits run narrower than CHW: this line measured 0.92 of
        # it on a Tab5.
        if len(text) * CHW * 92 // 100 > w:
            text = "SD %.1f  LEFT %.1f  K %d  B %d" % (
                self.t, days, self._klingons_left(), self._bases_left())
        tulip.bg_str(text, x, 0, TEXT, F_TEXT, w, BAR_H - 3)

    # The scanner.

    def _draw_srs(self):
        for y in range(8):
            for x in range(8):
                self._draw_cell(x, y)
        if self.overlay is not None:
            self._draw_overlay()

    def _cell_origin(self, x, y):
        return (SRS_X + x * C, SRS_Y + y * C)

    def _cell_centre(self, x, y):
        return (SRS_X + x * C + C // 2, SRS_Y + y * C + C // 2)

    def _draw_cell(self, x, y):
        if self.overlay is not None:
            return
        (px, py) = self._cell_origin(x, y)
        tulip.bg_rect(px, py, C, C, SRS_BG, 1)
        tulip.bg_rect(px, py, C, C, SRS_GRID)
        # A couple of faint background stars, the same ones every time this
        # square is drawn, so a repaint never looks like anything moved.
        h = _hash(((self.qy * 8 + self.qx) * 64 + y * 8 + x) * 7 + 3)
        for _ in range(2):
            h = _hash(h)
            sx = px + 3 + h % (C - 6)
            sy = py + 3 + (h >> 8) % (C - 6)
            tulip.bg_rect(sx, sy, 1 + (h >> 16) % 2, 1, SPECK, 1)
        obj = self.grid[y * 8 + x]
        (cx, cy) = (px + C // 2, py + C // 2)
        if self.dmg[SRS] < 0 and obj != SHIP:
            # Sensors out: static where the picture should be.
            for _ in range(6):
                h = _hash(h + tulip.ticks_ms())
                tulip.bg_rect(px + 2 + h % (C - 6), py + 2 + (h >> 9) % (C - 6),
                              3, 1, FAINT, 1)
            obj = EMPTY
        if obj == STAR:
            self._draw_star(cx, cy, h)
        elif obj == BASE:
            self._draw_base(cx, cy)
        elif obj == KLINGON:
            k = self._klingon_at(x, y)
            _shape(cx, cy, C / 27.0,
                   _heading(self.sx - x, self.sy - y), _KLINGON)
            if k is not None:
                self._draw_energy(px, py, k[2])
        elif obj == SHIP:
            _shape(cx, cy, C / 27.0, self.heading, _ENTERPRISE)
        if self.sel_sector == (x, y):
            self._draw_brackets(px, py, C, SEL)

    def _draw_star(self, cx, cy, h):
        (glow, mid, bright, core) = STAR_KINDS[(h >> 4) % len(STAR_KINDS)]
        r = C * (24 + (h >> 6) % 8) // 100
        tulip.bg_circle(cx, cy, r + C // 10, glow, 1)
        tulip.bg_circle(cx, cy, r, mid, 1)
        tulip.bg_circle(cx, cy, r * 2 // 3, bright, 1)
        tulip.bg_circle(cx, cy, max(1, r // 3), core, 1)
        spike = r + C // 5
        tulip.bg_line(cx - spike, cy, cx + spike, cy, bright)
        tulip.bg_line(cx, cy - spike, cx, cy + spike, bright)

    def _draw_base(self, cx, cy):
        r = C * 36 // 100
        w = max(2, C // 24)
        for (dx, dy) in ((1, 0), (0, 1), (-1, 0), (0, -1)):
            tulip.bg_line(cx, cy, cx + dx * r, cy + dy * r, BASE_RING, w)
        for i in range(w + 1):
            tulip.bg_circle(cx, cy, r - i, BASE_RING)
        tulip.bg_circle(cx, cy, C * 14 // 100, BASE_RING, 1)
        tulip.bg_circle(cx, cy, C * 10 // 100, BASE_HUB, 1)
        pod = max(2, C * 7 // 100)
        d = r * 7 // 10
        for (dx, dy) in ((1, 1), (-1, 1), (-1, -1), (1, -1)):
            tulip.bg_circle(cx + dx * d, cy + dy * d, pod, BASE_POD, 1)
        tulip.bg_circle(cx, cy - r, max(1, pod // 2), BASE_LIGHT, 1)
        if self.docked:
            tulip.bg_circle(cx, cy, r + 3, GOOD)

    def _draw_energy(self, px, py, e):
        w = C - 12
        full = max(2, int(w * min(e, 300) / 300))
        y = py + C - 6
        tulip.bg_rect(px + 6, y, w, 3, FAINT, 1)
        col = GOOD if e > 150 else (WARN if e > 60 else BAD)
        tulip.bg_rect(px + 6, y, full, 3, col, 1)

    def _draw_brackets(self, x, y, s, col):
        a = max(6, s // 5)
        w = 2 if _BIG else 1
        x1 = x + s - 1
        y1 = y + s - 1
        for (ox, oy, dx, dy) in ((x + 2, y + 2, 1, 1), (x1 - 2, y + 2, -1, 1),
                                 (x + 2, y1 - 2, 1, -1), (x1 - 2, y1 - 2, -1, -1)):
            tulip.bg_line(ox, oy, ox + dx * a, oy, col, w)
            tulip.bg_line(ox, oy, ox, oy + dy * a, col, w)

    def _restore_box(self, x0, y0, x1, y1):
        """Repaint every scanner square that the pixel box touches."""
        cx0 = max(0, (x0 - SRS_X) // C)
        cy0 = max(0, (y0 - SRS_Y) // C)
        cx1 = min(7, (x1 - SRS_X) // C)
        cy1 = min(7, (y1 - SRS_Y) // C)
        for y in range(cy0, cy1 + 1):
            for x in range(cx0, cx1 + 1):
                self._draw_cell(x, y)

    def _line_cells(self, x0, y0, x1, y1):
        """The scanner squares a pixel line runs through (and touches)."""
        cells = []
        dx = x1 - x0
        dy = y1 - y0
        n = max(1, int(math.sqrt(dx * dx + dy * dy) / (C / 4)))
        for i in range(n + 1):
            px = x0 + dx * i // n
            py = y0 + dy * i // n
            for (ox, oy) in ((-3, -3), (3, 3), (-3, 3), (3, -3)):
                cx = (px + ox - SRS_X) // C
                cy = (py + oy - SRS_Y) // C
                if 0 <= cx < 8 and 0 <= cy < 8 and (cx, cy) not in cells:
                    cells.append((cx, cy))
        return cells

    def _restore_cells(self, cells):
        for (x, y) in cells:
            self._draw_cell(x, y)

    # The galaxy map.

    def _draw_map(self):
        tulip.bg_rect(RX, RY, MAP_W, MAP_W, SPACE, 1)
        for i in range(8):
            tulip.bg_str(str(i + 1), MAP_X + i * G, RY, DIM, F_SMALL, G, LBL)
            tulip.bg_str(str(i + 1), RX, MAP_Y + i * G, DIM, F_SMALL, LBL, G)
        for qy in range(8):
            for qx in range(8):
                self._draw_map_cell(qx, qy)
        if self.sel_quad is not None:
            (tx, ty) = self.sel_quad
            tulip.bg_line(MAP_X + self.qx * G + G // 2, MAP_Y + self.qy * G + G // 2,
                          MAP_X + tx * G + G // 2, MAP_Y + ty * G + G // 2, SEL)
            self._draw_map_cell(self.qx, self.qy)
            self._draw_map_cell(tx, ty)
            self._draw_brackets(MAP_X + tx * G, MAP_Y + ty * G, G, SEL)
        x = MAP_X + self.qx * G
        y = MAP_Y + self.qy * G
        tulip.bg_rect(x, y, G, G, GOOD)
        tulip.bg_rect(x + 1, y + 1, G - 2, G - 2, GOOD)

    def _map_record(self, qx, qy):
        """What the map may show for a quadrant: the record, unless the computer
        that keeps it is out, when only what the sensors see right now."""
        if self.dmg[COM] < 0:
            reach = 1 if self.dmg[LRS] >= 0 else 0
            if abs(qx - self.qx) > reach or abs(qy - self.qy) > reach:
                return None
            q = self.gal[qy][qx]
            return (q[0], q[1], q[2])
        return self.known[qy][qx]

    def _draw_map_cell(self, qx, qy):
        x = MAP_X + qx * G
        y = MAP_Y + qy * G
        rec = self._map_record(qx, qy)
        if rec is None:
            tulip.bg_rect(x, y, G, G, SPACE, 1)
            tulip.bg_rect(x, y, G, G, FAINT)
            tulip.bg_rect(x + G // 2 - 1, y + G // 2 - 1, 2, 2, FAINT, 1)
            return
        (k, b, s) = rec
        if k:
            bg = _c(70, 14, 14) if k == 1 else _c(110, 18, 18)
        elif b:
            bg = _c(16, 40, 90)
        else:
            bg = _c(12, 18, 36)
        tulip.bg_rect(x, y, G, G, bg, 1)
        tulip.bg_rect(x, y, G, G, PANEL_EDGE)
        # The record as the computer printed it: Klingons, starbases, stars.
        w3 = G // 3
        tulip.bg_str(str(k), x + 1, y, BAD if k else DIM, F_SMALL, w3, G)
        tulip.bg_str(str(b), x + w3, y, BASE_HUB if b else DIM, F_SMALL, w3, G)
        tulip.bg_str(str(s), x + 2 * w3 - 1, y, WARN, F_SMALL, w3, G)

    # The status panel.

    def _draw_status(self):
        x = ST_X
        w = ST_W
        tulip.bg_rect(x, RY, w, TOP_H, SPACE, 1)
        y = RY
        self._draw_condition()
        y += COND_H + GAP
        tulip.bg_str(_qname(self.qx, self.qy), x, y + ASC, AMBER, F_TEXT)
        y += LH
        tulip.bg_str("QUAD %s  SECTOR %s" % (_rc(self.qx, self.qy),
                                              _rc(self.sx, self.sy)),
                     x, y + ASC, DIM, F_TEXT)
        y += LH + GAP
        y = self._gauge(x, y, w, "ENERGY", self.energy, E0, GOOD)
        y = self._gauge(x, y, w, "SHIELDS", self.shields, E0, SHIELD)
        label = "TORPEDOES"
        tulip.bg_str(label, x, y + ASC, TEXT, F_TEXT)
        pw = max(4, min(LH // 2, (w - len(label) * CHW - GAP) // P0 - 3))
        px = x + w - P0 * (pw + 3) + 3
        ph = LH // 2
        for i in range(P0):
            col = TORP if i < self.torps else FAINT
            tulip.bg_roundrect(px + i * (pw + 3), y + (LH - ph) // 2, pw, ph,
                               max(1, pw // 3), col, 1)
        y += LH + GAP
        cw = w // 4
        for i in range(8):
            cx = x + (i % 4) * cw
            cy = y + (i // 4) * LH
            ok = self.dmg[i] >= 0
            if self.dmg[DMG] < 0 and i != DMG:
                col = DIM                       # nobody to report it
            else:
                col = GOOD if ok else BAD
            r = max(3, LH // 5)
            tulip.bg_circle(cx + r + 1, cy + LH // 2, r, col, 1)
            tulip.bg_str(DEVICE_TAGS[i], cx + 2 * r + 5, cy + ASC,
                         TEXT if ok else BAD, F_SMALL)

    def _draw_condition(self):
        cond = self._condition()
        col = COND_COLOURS[cond]
        if cond == "RED" and not self.blink_on:
            col = RED_DIM
        tulip.bg_rect(ST_X, RY, ST_W, COND_H, SPACE, 1)
        tulip.bg_roundrect(ST_X, RY, ST_W, COND_H, COND_H // 3, col, 1)
        tulip.bg_str("CONDITION " + cond, ST_X, RY, WHITE, F_TEXT, ST_W, COND_H)

    def _gauge(self, x, y, w, label, value, top, col):
        tulip.bg_str(label, x, y + ASC, TEXT, F_TEXT)
        num = "%d" % int(value)
        tulip.bg_str(num, x + w - len(num) * CHW - CHW // 2, y + ASC, TEXT, F_TEXT)
        y += LH
        tulip.bg_rect(x, y, w, GAUGE_H, FAINT, 1)
        full = int(w * min(value, top) / top)
        if full > 0:
            if col == GOOD and value < E0 * 0.1:
                col = WARN
            tulip.bg_rect(x, y, full, GAUGE_H, col, 1)
        return y + GAUGE_H + GAP

    # The log.

    def _say(self, text, col=None):
        for line in _wrap(text, LOG_CHARS):
            self.log.append((line, TEXT if col is None else col))
        if len(self.log) > 60:
            self.log = self.log[-LOG_LINES:]
        if self.picker is None and getattr(self, "drawn", False):
            self._draw_log()

    def _draw_log(self):
        tulip.bg_rect(LOG_X, LOG_Y, LOG_W, LOG_H, PANEL, 1)
        tulip.bg_rect(LOG_X, LOG_Y, LOG_W, LOG_H, PANEL_EDGE)
        lines = self.log[-LOG_LINES:]
        y = LOG_Y + LOG_PAD + (LOG_H - 2 * LOG_PAD - LOG_LINES * LH) // 2
        for (text, col) in lines:
            tulip.bg_str(text, LOG_X + LOG_PAD, y + ASC, col, F_TEXT)
            y += LH

    # The buttons.

    def _draw_buttons(self):
        for (name, bx, by) in self.buttons:
            col = BTN_COLOURS[name]
            tulip.bg_roundrect(bx, by, BTN_W, BTN_H, BTN_H // 2, col, 1)
            tulip.bg_str(_BUTTON_TEXT[name], bx, by, BLACK, F_BTN, BTN_W, BTN_H)

    # Overlays on the scanner: the briefing, help, and the end of the game.

    def _show_briefing(self):
        base = "STARBASE" if self.b_start == 1 else "STARBASES"
        self.overlay = ("YOUR ORDERS", [
            ("AS COMMANDER OF THE U.S.S. ENTERPRISE, YOUR ORDERS ARE AS "
             "FOLLOWS:", TEXT),
            ("DESTROY THE %d KLINGON WARSHIPS WHICH HAVE INVADED THE GALAXY "
             "BEFORE THEY CAN ATTACK FEDERATION HEADQUARTERS ON STARDATE %.0f. "
             "THIS GIVES YOU %d DAYS." % (self.k_start, self.t0 + self.t9,
                                          self.t9), AMBER),
            ("", TEXT),
            ("THERE %s %d %s IN THE GALAXY FOR RESUPPLYING YOUR SHIP."
             % ("IS" if self.b_start == 1 else "ARE", self.b_start, base), TEXT),
        ] + ([("KLINGONS ARE HERE AND YOUR SHIELDS ARE DOWN: RAISE THEM "
               "FIRST!", BAD)]
             if self.klingons else []) + [
            ("TAP HELP FOR HOW TO FLY HER.", DIM),
        ], "TAP TO BEGIN")

    def _show_help(self):
        self.overlay = ("HOW TO PLAY", [
            ("SCANNER: TAP A SECTOR TO AIM AT IT.", AMBER),
            ("TORPEDO fires at it (or at the nearest Klingon), IMPULSE flies "
             "there.", TEXT),
            ("MAP: TAP A QUADRANT, THEN WARP.", AMBER),
            ("Squares read Klingons/bases/stars.", TEXT),
            ("PHASERS hit every Klingon at once; the energy is split and "
             "falls off with range.", TEXT),
            ("SHIELDS take the Klingons' fire. At zero the ship is lost.", TEXT),
            ("Dock beside a starbase to refuel, rearm and repair.", TEXT),
            ("KEYS: W I P T S D H N, ARROWS + TAB AIM, ESC QUITS.", DIM),
        ], "TAP TO CONTINUE")

    def _draw_overlay(self):
        (title, lines, footer) = self.overlay
        m = PAD + 4
        x = SRS_X + m
        y = SRS_Y + m
        w = SRS_W - 2 * m
        h = SRS_W - 2 * m
        tulip.bg_rect(SRS_X, SRS_Y, SRS_W, SRS_W, SRS_BG, 1)
        tulip.bg_roundrect(x, y, w, h, 16, PANEL, 1)
        tulip.bg_roundrect(x, y, w, h, 16, AMBER)
        tulip.bg_roundrect(x + 1, y + 1, w - 2, h - 2, 15, AMBER)
        th = LH * 2
        tulip.bg_str(title, x, y + 4, AMBER, F_TITLE, w, th)
        tulip.bg_rect(x + 24, y + th + 4, w - 48, 2, AMBER, 1)
        bottom = y + h - 2 * LH
        tulip.bg_rect(x + 24, bottom, w - 48, 2, AMBER, 1)
        tulip.bg_str(footer, x, bottom + 2, GOOD, F_TEXT, w, 2 * LH - 4)
        cy = y + th + 4 + LH // 2
        n = (w - 48) // CHW - 1
        for (text, col) in lines:
            for line in (_wrap(text.upper(), n) if text else [""]):
                if cy + LH > bottom:
                    return
                tulip.bg_str(line.strip(), x + 24, cy + ASC, col, F_TEXT)
                cy += LH
            cy += LH // 3

    def _close_overlay(self):
        self.overlay = None
        self._draw_srs()

    # Sliders and yes/no questions, drawn over the log.

    def _open_picker(self, kind, title, top, value, ok_text, help_text=None):
        self.picker = {"kind": kind, "title": title, "max": int(top),
                       "value": int(max(0, min(top, value))),
                       "ok": ok_text, "help": help_text}
        self._draw_picker()

    def _picker_geometry(self):
        bw = BTN_W
        bh = BTN_H - 8 if _BIG else BTN_H - 6
        by = LOG_Y + LOG_H - LOG_PAD - bh
        ok_x = LOG_X + LOG_W - LOG_PAD - bw
        cancel_x = ok_x - BTN_GAP - bw
        tx0 = LOG_X + 3 * LOG_PAD
        tx1 = LOG_X + LOG_W - 3 * LOG_PAD
        ty = LOG_Y + LOG_PAD + 3 * LH
        return (by, bh, bw, ok_x, cancel_x, tx0, tx1, ty)

    def _draw_picker(self):
        p = self.picker
        (by, bh, bw, ok_x, cancel_x, tx0, tx1, ty) = self._picker_geometry()
        tulip.bg_rect(LOG_X, LOG_Y, LOG_W, LOG_H, PANEL, 1)
        tulip.bg_rect(LOG_X, LOG_Y, LOG_W, LOG_H, AMBER)
        tulip.bg_str(p["title"], LOG_X + LOG_PAD, LOG_Y + LOG_PAD + ASC, AMBER,
                     F_TEXT)
        if p["max"] > 0:
            v = "%d / %d" % (p["value"], p["max"])
            tulip.bg_str(v, LOG_X + LOG_W - LOG_PAD - len(v) * CHW,
                         LOG_Y + LOG_PAD + ASC, WHITE, F_TEXT)
            th = GAUGE_H + 4
            tulip.bg_roundrect(tx0, ty - th // 2, tx1 - tx0, th, th // 2, FAINT, 1)
            kx = tx0 + (tx1 - tx0) * p["value"] // p["max"]
            if kx > tx0:
                tulip.bg_roundrect(tx0, ty - th // 2, kx - tx0, th, th // 2,
                                   AMBER, 1)
            r = th + 4
            tulip.bg_circle(kx, ty, r, WHITE, 1)
            tulip.bg_circle(kx, ty, r - 3, AMBER, 1)
        if p["help"]:
            tulip.bg_str(p["help"], LOG_X + LOG_PAD, LOG_Y + LOG_PAD + LH + ASC,
                         DIM, F_TEXT)
        tulip.bg_roundrect(cancel_x, by, bw, bh, bh // 2, DIM, 1)
        tulip.bg_str("CANCEL", cancel_x, by, BLACK, F_BTN, bw, bh)
        tulip.bg_roundrect(ok_x, by, bw, bh, bh // 2, AMBER, 1)
        tulip.bg_str(p["ok"], ok_x, by, BLACK, F_BTN, bw, bh)

    def _picker_touch(self, x, y, first):
        p = self.picker
        (by, bh, bw, ok_x, cancel_x, tx0, tx1, ty) = self._picker_geometry()
        if first and by <= y < by + bh:
            if ok_x <= x < ok_x + bw:
                self._picker_done(True)
                return
            if cancel_x <= x < cancel_x + bw:
                self._picker_done(False)
                return
        if p["max"] > 0 and (self.dragging or (first and abs(y - ty) < LH * 3 // 2
                                              and tx0 - LH <= x <= tx1 + LH)):
            self.dragging = True
            v = (x - tx0) * p["max"] // (tx1 - tx0)
            self._picker_set(v if p["max"] < 100 else _round(v / 10.0) * 10)

    def _picker_set(self, v):
        p = self.picker
        v = max(0, min(p["max"], int(v)))
        if v != p["value"]:
            p["value"] = v
            self._draw_picker()

    def _picker_done(self, ok):
        p = self.picker
        self.picker = None
        self.dragging = False
        self._draw_log()
        if not ok:
            return
        kind = p["kind"]
        if kind == "phasers":
            self._run(self._fire_phasers(p["value"]))
        elif kind == "shields":
            self._set_shields(p["value"])
        elif kind == "repair":
            self._run(self._repair())
        elif kind == "new":
            self._new_game()
            self._draw_all()

    # ------------------------------------------------------------- commands

    def _run(self, gen):
        self.script = gen
        self.wake = tulip.ticks_ms()

    def _command(self, name):
        if self.script is not None:
            return
        if self.picker is not None:
            self._picker_done(False)
        if self.overlay is not None:
            self._close_overlay()
            if name != "help" and name != "new":
                return
        _sfx(OSC_BEEP, 0.15, "1320,0")
        if name == "help":
            self._show_help()
            self._draw_srs()
            return
        if name == "new":
            if self.over is not None:
                self._new_game()
                self._draw_all()
            else:
                self._open_picker("new", "ABANDON THIS MISSION?", 0, 0, "NEW GAME",
                                  "A NEW GALAXY, A NEW SET OF ORDERS.")
            return
        if self.over is not None:
            self._say("THE MISSION IS OVER. TAP NEW GAME.", DIM)
            return
        if name == "warp":
            self._warp()
        elif name == "impulse":
            self._impulse()
        elif name == "phasers":
            self._phasers()
        elif name == "torpedo":
            self._torpedo()
        elif name == "shields":
            self._shields()
        elif name == "damage":
            self._damage_report()

    # Navigation.

    def _warp(self):
        if self.sel_quad is None or self.sel_quad == (self.qx, self.qy):
            self._say("TAP A QUADRANT ON THE GALAXY MAP TO SET A COURSE.", DIM)
            return
        if self.dmg[WARP] < 0:
            self._say("WARP ENGINES ARE DAMAGED. IMPULSE ONLY UNTIL THEY "
                      "ARE REPAIRED.", WARN)
            return
        (tqx, tqy) = self.sel_quad
        ax = self.qx * 8 + self.sx
        ay = self.qy * 8 + self.sy
        # Aim for the middle of the quadrant, on the near side of it.
        tx = tqx * 8 + (3 if ax < tqx * 8 + 3.5 else 4)
        ty = tqy * 8 + (3 if ay < tqy * 8 + 3.5 else 4)
        dx = tx - ax
        dy = ty - ay
        d = math.sqrt(dx * dx + dy * dy)
        w = d / 8.0
        if w > 8.0:
            w = 8.0
            d = 64.0
        need = int(w * 8 + 0.5) + 10
        if not self._afford(need, w):
            return
        n = math.sqrt(dx * dx + dy * dy)
        self._run(self._fly(dx / n, dy / n, d))

    def _impulse(self):
        if self.sel_sector is None or self.sel_sector == (self.sx, self.sy):
            self._say("TAP A SECTOR ON THE SCANNER TO MOVE TO.", DIM)
            return
        (tx, ty) = self.sel_sector
        dx = tx - self.sx
        dy = ty - self.sy
        d = math.sqrt(dx * dx + dy * dy)
        # Impulse is not the warp engines, so it still works with them
        # damaged -- which is how a ship with no warp drive gets anywhere
        # while they repair. (The 1974 game had only the one engine, limited
        # to warp 0.2: a sector at a time.)
        w = d / 8.0
        need = int(w * 8 + 0.5) + 10
        if not self._afford(need, w):
            return
        self._run(self._fly(dx / d, dy / d, d))

    def _afford(self, need, w):
        if self.energy >= need:
            return True
        self._say("ENGINEERING REPORTS 'INSUFFICIENT ENERGY AVAILABLE FOR "
                  "MANEUVERING AT WARP %.1f!'" % w, WARN)
        if self.shields + self.energy >= need and self.dmg[SHE] >= 0:
            self._say("DEFLECTOR CONTROL ROOM ACKNOWLEDGES %d UNITS OF ENERGY "
                      "PRESENTLY DEPLOYED TO SHIELDS." % int(self.shields), DIM)
        return False

    def _fly(self, ux, uy, d):
        """Move the ship d sectors along (ux, uy): within the quadrant if it
        ends there, else to wherever in the galaxy the course comes out."""
        if self.klingons and not self.docked:
            yield from self._klingons_fire()
            if self.over:
                return
        self.heading = _heading(ux, uy)
        if self.sel_sector is not None:
            (ox, oy) = self.sel_sector
            self.sel_sector = None
            self._draw_cell(ox, oy)
        was_docked = self.docked
        x0 = float(self.sx)
        y0 = float(self.sy)
        moved = 0.0
        last = (self.sx, self.sy)
        blocked = None
        left = False
        # The part of the flight inside this quadrant, where whatever is in the
        # way stops the ship -- the rest of the galaxy is crossed at warp.
        steps = max(1, int(d / 0.1))
        for i in range(1, steps + 1):
            t = d * i / steps
            cx = _round(x0 + ux * t)
            cy = _round(y0 + uy * t)
            if not (0 <= cx < 8 and 0 <= cy < 8):
                left = True
                break
            if (cx, cy) == last:
                continue
            obj = self.grid[cy * 8 + cx]
            if obj != EMPTY:
                blocked = obj
                break
            self._move_ship(cx, cy)
            last = (cx, cy)
            moved = t
            yield 45
        # Paid for before any docking, which fills the tanks.
        if left:
            moved = d
        self.energy -= int(moved + 0.5) + 10
        if self.energy < 0:
            self._say("SHIELD CONTROL SUPPLIES ENERGY TO COMPLETE THE MANEUVER.",
                      WARN)
            self.shields += self.energy
            self.energy = 0.0
            if self.shields < 0:
                self.shields = 0.0
        wf = moved / 8.0
        self.t += 1.0 if wf >= 1 else max(0.1, int(10 * wf) / 10.0)
        if left:
            ax = self.qx * 8 + x0 + ux * d
            ay = self.qy * 8 + y0 + uy * d
            nx = min(63, max(0, _round(ax)))
            ny = min(63, max(0, _round(ay)))
            if nx != _round(ax) or ny != _round(ay):
                self._say("LT. UHURA REPORTS MESSAGE FROM STARFLEET COMMAND: "
                          "'PERMISSION TO CROSS THE GALACTIC PERIMETER IS "
                          "HEREBY DENIED.'", WARN)
            yield from self._warp_effect()
            if (nx // 8, ny // 8) == (self.qx, self.qy):
                # Clamped back into the quadrant it was leaving.
                i = ny % 8 * 8 + nx % 8
                if self.grid[i] != EMPTY:
                    i = self._empty_cell()
                self._move_ship(i % 8, i // 8, draw=False)
            else:
                self._enter_quadrant(nx // 8, ny // 8, nx % 8, ny % 8)
            self._check_docked()
            self._draw_srs()
            self._say("NOW ENTERING %s QUADRANT." % _qname(self.qx, self.qy), AMBER)
        else:
            if blocked is not None:
                what = {STAR: "A STAR", BASE: "A STARBASE"}.get(blocked, "A KLINGON")
                self._say("WARP ENGINES SHUT DOWN AT SECTOR %s DUE TO BAD "
                          "NAVIGATION (%s IN THE WAY)." % (_rc(self.sx, self.sy),
                                                          what), WARN)
            self._check_docked()
            if self.base is not None and self.docked != was_docked:
                self._draw_cell(self.base[0], self.base[1])
        self._record()
        self.sel_quad = None
        self._draw_map()
        self._draw_status()
        self._draw_bar()
        yield from self._repairs_in_flight(wf)
        if left and self.klingons and not self.docked:
            self._say("COMBAT AREA      CONDITION RED", BAD)
            if self.shields <= 200:
                self._say("   SHIELDS DANGEROUSLY LOW", BAD)
            for _ in range(2):
                _sfx(OSC_ALERT, 0.3)
                yield 560
        self._end_of_turn()

    def _move_ship(self, x, y, draw=True):
        (ox, oy) = (self.sx, self.sy)
        self.grid[oy * 8 + ox] = EMPTY
        self.sx = x
        self.sy = y
        self.grid[y * 8 + x] = SHIP
        if draw:
            self._draw_cell(ox, oy)
            self._draw_cell(x, y)
            # The Klingons turn to follow the ship.
            for k in self.klingons:
                self._draw_cell(k[0], k[1])

    def _warp_effect(self):
        _sfx(OSC_WARP, 0.35)
        cx = SRS_X + SRS_W // 2
        cy = SRS_Y + SRS_W // 2
        streaks = []
        for _ in range(72):
            a = _rnd() * 2 * math.pi
            r = 0.04 + _rnd() * 0.3
            streaks.append((math.cos(a), math.sin(a), r))
        half = SRS_W // 2
        for f in range(14):
            tulip.bg_rect(SRS_X, SRS_Y, SRS_W, SRS_W, SPACE, 1)
            grow = 1.0 + f * f * 0.06
            for (ca, sa, r) in streaks:
                r0 = r * grow
                r1 = r0 * (1.15 + f * 0.05)
                if r0 > 1.0:
                    continue
                r1 = min(r1, 1.0)
                col = WHITE if r < 0.15 else NACELLE_GLOW
                tulip.bg_line(cx + int(ca * r0 * half), cy + int(sa * r0 * half),
                              cx + int(ca * r1 * half), cy + int(sa * r1 * half),
                              col, 2 if _BIG else 1)
            yield 40

    def _repairs_in_flight(self, w):
        d6 = 1.0 if w >= 1 else w
        fixed = []
        for i in range(8):
            if self.dmg[i] < 0:
                self.dmg[i] += d6
                if -0.1 < self.dmg[i] < 0:
                    self.dmg[i] = -0.1
                elif self.dmg[i] >= 0:
                    self.dmg[i] = 0.0
                    fixed.append(i)
        for i in fixed:
            self._say("DAMAGE CONTROL REPORT: %s REPAIR COMPLETED." % DEVICES[i],
                      GOOD)
        if _rnd() <= 0.2:
            i = random.randint(0, 7)
            if _rnd() < 0.6:
                self.dmg[i] -= _rnd() * 5 + 1
                self._say("DAMAGE CONTROL REPORT: %s DAMAGED." % DEVICES[i], BAD)
            elif self.dmg[i] < 0:
                self.dmg[i] = min(0.0, self.dmg[i] + _rnd() * 3 + 1)
                self._say("DAMAGE CONTROL REPORT: %s STATE OF REPAIR IMPROVED."
                          % DEVICES[i], GOOD)
        if fixed:
            self._draw_srs()
        self._draw_status()
        yield 0

    # Weapons.

    def _phasers(self):
        if not self.klingons:
            self._say("SCIENCE OFFICER SPOCK REPORTS 'SENSORS SHOW NO ENEMY "
                      "SHIPS IN THIS QUADRANT'.", DIM)
            return
        if self.dmg[PHA] < 0:
            self._say("PHASERS INOPERATIVE.", WARN)
            return
        if self.energy < 1:
            self._say("NO ENERGY TO FIRE PHASERS.", WARN)
            return
        # Suggest what it would take to finish them all at the worst roll.
        need = 0
        for k in self.klingons:
            need = max(need, k[2] * self._dist(k[0], k[1]) / 2.0)
        guess = int(need * len(self.klingons) / 50 + 1) * 50
        if self.phaser_last:
            guess = self.phaser_last
        hint = "%d KLINGON%s IN RANGE" % (len(self.klingons),
                                          "" if len(self.klingons) == 1 else "S")
        if self.dmg[COM] < 0:
            hint = "COMPUTER DOWN: ACCURACY REDUCED"
        self._open_picker("phasers", "PHASERS: ENERGY TO FIRE", self.energy,
                          min(guess, self.energy), "FIRE", hint)

    def _fire_phasers(self, x):
        if x <= 0:
            return
        self.phaser_last = x
        self.energy -= x
        self._draw_status()
        if self.dmg[COM] < 0:
            x = x * _rnd()
        targets = list(self.klingons)
        h1 = int(x / len(targets))
        _sfx(OSC_PHA, 0.4)
        (sx, sy) = self._cell_centre(self.sx, self.sy)
        ends = [self._cell_centre(k[0], k[1]) for k in targets]
        cells = []
        for (ex, ey) in ends:
            for c in self._line_cells(sx, sy, ex, ey):
                if c not in cells:
                    cells.append(c)
        w = 4 if _BIG else 3
        for f in range(4):
            for (ex, ey) in ends:
                tulip.bg_line(sx, sy, ex, ey, PHASER, w)
                tulip.bg_line(sx, sy, ex, ey, PHASER_CORE, 1)
            yield 70
            self._restore_cells(cells)
            yield 30
        for k in targets:
            d = self._dist(k[0], k[1])
            h = int((h1 / d) * (_rnd() + 2))
            if h <= 0.15 * k[2]:
                self._say("SENSORS SHOW NO DAMAGE TO ENEMY AT %s" % _rc(k[0], k[1]),
                          DIM)
                continue
            k[2] -= h
            self._say("%d UNIT HIT ON KLINGON AT SECTOR %s" % (h, _rc(k[0], k[1])),
                      AMBER)
            if k[2] <= 0:
                yield from self._destroy_klingon(k)
            else:
                self._say("   (SENSORS SHOW %d UNITS REMAINING)" % int(k[2]), DIM)
                self._draw_cell(k[0], k[1])
            yield 120
        self._draw_status()
        if self._won():
            yield from self._victory()
            return
        yield from self._klingons_fire()
        self._end_of_turn()

    def _torpedo(self):
        if self.torps <= 0:
            self._say("ALL PHOTON TORPEDOES EXPENDED.", WARN)
            return
        if self.dmg[TOR] < 0:
            self._say("PHOTON TUBES ARE NOT OPERATIONAL.", WARN)
            return
        target = self.sel_sector
        if target is None or target == (self.sx, self.sy):
            if not self.klingons:
                self._say("TAP A SECTOR ON THE SCANNER TO AIM.", DIM)
                return
            best = None
            for k in self.klingons:
                d = self._dist(k[0], k[1])
                if best is None or d < best[0]:
                    best = (d, k[0], k[1])
            target = (best[1], best[2])
        dx = target[0] - self.sx
        dy = target[1] - self.sy
        d = math.sqrt(dx * dx + dy * dy)
        ang = math.atan2(dy, dx)
        if self.dmg[COM] < 0:
            ang += (_rnd() - 0.5) * 0.5
            self._say("COMPUTER DOWN: TORPEDO AIMED BY HAND.", WARN)
        self._run(self._fire_torpedo(math.cos(ang), math.sin(ang)))

    def _fire_torpedo(self, ux, uy):
        self.torps -= 1
        self.energy = max(0.0, self.energy - 2)
        self.heading = _heading(ux, uy)
        self._draw_cell(self.sx, self.sy)
        self._draw_status()
        _sfx(OSC_TORP, 0.4)
        self._say("TORPEDO TRACK:", DIM)
        # Where it goes is settled first, a tenth of a sector at a time as the
        # 1974 game stepped it; then the flight is drawn against the clock, so
        # it crosses the scanner at the same speed whatever the frame rate.
        x0 = float(self.sx)
        y0 = float(self.sy)
        hit = None
        track = []
        i = 0
        while True:
            i += 1
            t = i * 0.1
            cx = _round(x0 + ux * t)
            cy = _round(y0 + uy * t)
            if not (0 <= cx < 8 and 0 <= cy < 8):
                break
            if (cx, cy) != (self.sx, self.sy):
                if not track or track[-1] != (cx, cy):
                    track.append((cx, cy))
                obj = self.grid[cy * 8 + cx]
                if obj != EMPTY:
                    hit = (cx, cy, obj)
                    t += 0.3                    # into the square, not its edge
                    break
        r = max(3, C // 9)
        prev = None
        start = tulip.ticks_ms()
        while True:
            pos = (tulip.ticks_ms() - start) * TORP_SPEED / 1000.0
            if pos >= t:
                break
            px = SRS_X + int((x0 + ux * pos + 0.5) * C)
            py = SRS_Y + int((y0 + uy * pos + 0.5) * C)
            if prev is not None:
                self._restore_box(prev[0] - r - 2, prev[1] - r - 2,
                                  prev[0] + r + 2, prev[1] + r + 2)
            tulip.bg_circle(px, py, r + 2, TORP, 1)
            tulip.bg_circle(px, py, r - 1, TORP_CORE, 1)
            prev = (px, py)
            yield 0
        if prev is not None:
            self._restore_box(prev[0] - r - 2, prev[1] - r - 2,
                              prev[0] + r + 2, prev[1] + r + 2)
        if len(track) > 1:
            self._say("   " + "  ".join([_rc(x, y) for (x, y) in track[-4:]]), DIM)
        if hit is None:
            self._say("TORPEDO MISSED.", DIM)
        else:
            (hx, hy, obj) = hit
            if obj == KLINGON:
                yield from self._destroy_klingon(self._klingon_at(hx, hy))
            elif obj == STAR:
                yield from self._explode(hx, hy, small=True)
                self._say("STAR AT %s ABSORBED TORPEDO ENERGY." % _rc(hx, hy), DIM)
            elif obj == BASE:
                self.grid[hy * 8 + hx] = EMPTY
                self.base = None
                self.gal[self.qy][self.qx][1] -= 1
                self._record()
                yield from self._explode(hx, hy)
                self._say("*** STARBASE DESTROYED ***", BAD)
                self._say("STARFLEET COMMAND REVIEWING YOUR RECORD TO CONSIDER "
                          "COURT MARTIAL!", BAD)
                self.docked = False
                self._draw_map()
                self._draw_bar()
        self._draw_status()
        if self._won():
            yield from self._victory()
            return
        yield from self._klingons_fire()
        self._end_of_turn()

    def _destroy_klingon(self, k):
        if k is None:
            return
        self._remove_klingon(k)
        if self.sel_sector == (k[0], k[1]):
            self.sel_sector = None
        yield from self._explode(k[0], k[1])
        self._say("*** KLINGON DESTROYED ***", GOOD)
        self._draw_map()
        self._draw_bar()
        self._draw_status()

    def _room(self, cx, cy):
        """How big a circle round (cx, cy) can get without leaving the
        scanner -- past its edge there is nothing to repaint it with."""
        return min(cx - SRS_X, SRS_X + SRS_W - 1 - cx, cy - SRS_Y,
                   SRS_Y + SRS_W - 1 - cy)

    def _explode(self, x, y, small=False):
        _sfx(OSC_BOOM, 0.3 if small else 0.6)
        (cx, cy) = self._cell_centre(x, y)
        top = min(C // 3 if small else C * 9 // 10, self._room(cx, cy))
        frames = ((FIRE1, 0.25), (FIRE2, 0.5), (FIRE3, 0.8), (FIRE2, 1.0),
                  (FIRE3, 0.9), (FIRE4, 0.7))
        for (col, f) in frames:
            r = max(2, int(top * f))
            tulip.bg_circle(cx, cy, r, col, 1)
            if f >= 0.5:
                tulip.bg_circle(cx, cy, r // 2, FIRE1, 1)
            yield 45
        self._restore_box(cx - top - 2, cy - top - 2, cx + top + 2, cy + top + 2)
        # Debris that settles for a moment, then the square is clean.
        h = _hash(tulip.ticks_ms())
        for _ in range(8):
            h = _hash(h)
            tulip.bg_rect(cx - C // 3 + h % (2 * C // 3),
                          cy - C // 3 + (h >> 8) % (2 * C // 3), 2, 2, FIRE3, 1)
        yield 160
        self._restore_box(cx - top - 2, cy - top - 2, cx + top + 2, cy + top + 2)

    def _klingons_fire(self):
        if not self.klingons:
            return
        if self.docked:
            self._say("STARBASE SHIELDS PROTECT THE ENTERPRISE.", SHIELD)
            yield 200
            return
        (sx, sy) = self._cell_centre(self.sx, self.sy)
        for k in list(self.klingons):
            if k[2] <= 0:
                continue
            d = self._dist(k[0], k[1])
            h = int((k[2] / d) * (2 + _rnd()))
            self.shields -= h
            k[2] = k[2] / (3 + _rnd())
            (kx, ky) = self._cell_centre(k[0], k[1])
            cells = self._line_cells(kx, ky, sx, sy)
            _sfx(OSC_DIS, 0.4)
            # The bolt crawls out of the Klingon, then the shields take it.
            n = 5
            for i in range(1, n + 1):
                tulip.bg_line(kx, ky, kx + (sx - kx) * i // n, ky + (sy - ky) * i // n,
                              DISRUPTOR, 3 if _BIG else 2)
                yield 18
            tulip.bg_line(kx, ky, sx, sy, DISRUPTOR_CORE, 1)
            _sfx(OSC_BOOM, 0.2)
            if self.shields > 0:
                for rr in (C * 45 // 100, C * 48 // 100):
                    tulip.bg_circle(sx, sy, rr, SHIELD)
            yield 90
            self._restore_cells(cells)
            self._restore_box(sx - C // 2, sy - C // 2, sx + C // 2, sy + C // 2)
            self._say("%d UNIT HIT ON ENTERPRISE FROM SECTOR %s" % (h, _rc(k[0], k[1])),
                      BAD)
            if self.shields <= 0:
                self.shields = 0.0
                self._draw_status()
                yield from self._ship_destroyed()
                return
            self._say("      <SHIELDS DOWN TO %d UNITS>" % int(self.shields), WARN)
            if h >= 20 and _rnd() <= 0.6 and h / self.shields > 0.02:
                i = random.randint(0, 7)
                self.dmg[i] -= h / self.shields + 0.5 * _rnd()
                self._say("DAMAGE CONTROL REPORTS '%s DAMAGED BY THE HIT'"
                          % DEVICES[i], BAD)
                if i == SRS or i == COM or i == LRS:
                    self._draw_srs()
                    self._draw_map()
            self._draw_status()
            yield 250

    # Shields, damage, repairs.

    def _shields(self):
        if self.dmg[SHE] < 0:
            self._say("SHIELD CONTROL INOPERABLE.", WARN)
            return
        total = self.energy + self.shields
        value = self.shields if self.shields > 0 else min(total, 500)
        self._open_picker("shields", "SHIELDS: ENERGY TO DEPLOY", total, value,
                          "SET", "AVAILABLE ENERGY = %d" % int(total))

    def _set_shields(self, x):
        if int(x) == int(self.shields):
            self._say("<SHIELDS UNCHANGED>", DIM)
            return
        self.energy = self.energy + self.shields - x
        self.shields = float(x)
        self._say("DEFLECTOR CONTROL ROOM REPORT: 'SHIELDS NOW AT %d UNITS PER "
                  "YOUR COMMAND.'" % int(x), SHIELD)
        _sfx(OSC_BEEP, 0.2, "660,0")
        self._draw_status()

    def _damage_report(self):
        damaged = [i for i in range(8) if self.dmg[i] < 0]
        if self.dmg[DMG] < 0:
            self._say("DAMAGE CONTROL REPORT NOT AVAILABLE.", WARN)
        elif not damaged:
            self._say("DAMAGE CONTROL REPORT: ALL SYSTEMS OPERATIONAL.", GOOD)
        else:
            self._say("DEVICE                 STATE OF REPAIR", AMBER)
            for i in damaged:
                self._say("  %s  %.2f" % (DEVICES[i], self.dmg[i]), BAD)
        if self.docked and damaged:
            est = 0.1 * len(damaged) + 0.5 * _rnd()
            if est > 0.9:
                est = 0.9
            self.repair_est = est
            self._open_picker("repair", "TECHNICIANS STANDING BY", 0, 0, "REPAIR",
                              "ESTIMATED TIME: %.2f STARDATES" % est)

    def _repair(self):
        self.t += self.repair_est + 0.1
        for i in range(8):
            self.dmg[i] = 0.0
        self._say("REPAIRS COMPLETED IN %.1f STARDATES." % (self.repair_est + 0.1),
                  GOOD)
        self._record()
        self._draw_srs()
        self._draw_map()
        self._draw_status()
        self._draw_bar()
        self._end_of_turn()
        yield 0

    # The end of the game.

    def _won(self):
        return self._klingons_left() == 0

    def _victory(self):
        _sfx(OSC_WARP, 0.3)
        self.over = "win"
        rating = 1000 * (self.k_start / max(1.0, self.t - self.t0)) ** 2
        self._say("CONGRATULATIONS, CAPTAIN!", GOOD)
        self._say("YOUR EFFICIENCY RATING IS %d." % int(rating), GOOD)
        yield 400
        self.overlay = ("MISSION ACCOMPLISHED", [
            ("CONGRATULATIONS, CAPTAIN! THE LAST KLINGON BATTLE CRUISER "
             "MENACING THE FEDERATION HAS BEEN DESTROYED.", GOOD),
            ("", TEXT),
            ("STARDATE %.1f, %.1f DAYS INTO THE MISSION." % (self.t,
                                                           self.t - self.t0), TEXT),
            ("YOUR EFFICIENCY RATING IS %d." % int(rating), AMBER),
        ], "TAP NEW GAME FOR ANOTHER COMMAND")
        self._draw_srs()
        self._draw_bar()

    def _ship_destroyed(self):
        (cx, cy) = self._cell_centre(self.sx, self.sy)
        _sfx(OSC_BOOM, 0.7)
        for (col, f) in ((FIRE1, 0.3), (FIRE2, 0.7), (FIRE3, 1.1), (FIRE2, 1.4),
                         (FIRE3, 1.6), (FIRE4, 1.5)):
            r = min(int(C * f), self._room(cx, cy))
            tulip.bg_circle(cx, cy, r, col, 1)
            tulip.bg_circle(cx, cy, r // 2, FIRE1, 1)
            yield 70
        self.grid[self.sy * 8 + self.sx] = EMPTY
        self._lose("ENTERPRISE DESTROYED", [
            ("THE ENTERPRISE HAS BEEN DESTROYED. THE FEDERATION WILL BE "
             "CONQUERED.", BAD)])

    def _lose(self, title, lines):
        self.over = "lost"
        left = self._klingons_left()
        self._say(lines[0][0], BAD)
        self.overlay = (title, lines + [
            ("", TEXT),
            ("IT IS STARDATE %.1f." % self.t, TEXT),
            ("THERE WERE %d KLINGON BATTLE CRUISERS LEFT AT THE END OF YOUR "
             "MISSION." % left, AMBER),
        ], "TAP NEW GAME FOR ANOTHER COMMAND")
        self._draw_srs()
        self._draw_status()
        self._draw_bar()

    def _end_of_turn(self):
        self._draw_bar()
        if self.over:
            return
        if self._days_left() <= 0:
            self._lose("OUT OF TIME", [
                ("THE KLINGONS HAVE REACHED FEDERATION HEADQUARTERS. YOU RAN "
                 "OUT OF TIME.", BAD)])
            return
        if (self.shields + self.energy <= 10
                or (self.energy <= 10 and self.dmg[SHE] < 0)) and not self.docked:
            self._lose("STRANDED", [
                ("** FATAL ERROR ** YOU'VE JUST STRANDED YOUR SHIP IN SPACE. "
                 "YOU HAVE INSUFFICIENT MANEUVERING ENERGY.", BAD)])

    # ------------------------------------------------------------------ input

    def _touch(self, up):
        try:
            point = tulip.touch()
        except Exception:
            return
        (x, y) = (point[0], point[1])
        if up:
            self.finger_down = False
            self.dragging = False
            return
        first = not self.finger_down
        self.finger_down = True
        if x < 0 or y < 0:
            return
        if self.picker is not None:
            self._picker_touch(x, y, first)
            return
        if not first or self.script is not None:
            return
        self._press(x, y)

    def _press(self, x, y):
        for (name, bx, by) in self.buttons:
            if bx <= x < bx + BTN_W and by <= y < by + BTN_H:
                self._command(name)
                return
        if self.overlay is not None:
            if SRS_X <= x < SRS_X + SRS_W and SRS_Y <= y < SRS_Y + SRS_W:
                self._close_overlay()
            return
        if SRS_X <= x < SRS_X + SRS_W and SRS_Y <= y < SRS_Y + SRS_W:
            self.focus = "srs"
            self._aim_sector((x - SRS_X) // C, (y - SRS_Y) // C)
            return
        if MAP_X <= x < MAP_X + 8 * G and MAP_Y <= y < MAP_Y + 8 * G:
            self.focus = "map"
            self._aim_quad((x - MAP_X) // G, (y - MAP_Y) // G)

    def _aim_sector(self, x, y):
        old = self.sel_sector
        if (x, y) == (self.sx, self.sy):
            self.sel_sector = None
        else:
            self.sel_sector = (x, y)
        if old is not None:
            self._draw_cell(old[0], old[1])
        if self.sel_sector is not None:
            self._draw_cell(x, y)
            self._describe_sector(x, y)

    def _describe_sector(self, x, y):
        obj = self.grid[y * 8 + x] if self.dmg[SRS] >= 0 else EMPTY
        d = self._dist(x, y)
        what = {EMPTY: "EMPTY SPACE", KLINGON: "KLINGON WARSHIP",
                BASE: "STARBASE", STAR: "STAR"}.get(obj, "")
        line = "SECTOR %s: %s, RANGE %.1f" % (_rc(x, y), what, d)
        if obj == KLINGON:
            k = self._klingon_at(x, y)
            if k is not None:
                line += ", %d UNITS" % int(k[2])
        self._say(line, DIM)

    def _aim_quad(self, qx, qy):
        if (qx, qy) == (self.qx, self.qy):
            self.sel_quad = None
        else:
            self.sel_quad = (qx, qy)
        self._draw_map()
        if self.sel_quad is not None:
            dx = (qx - self.qx) * 8.0
            dy = (qy - self.qy) * 8.0
            w = min(8.0, math.sqrt(dx * dx + dy * dy) / 8.0)
            rec = self._map_record(qx, qy)
            what = ("NO RECORD" if rec is None else
                    "%d KLINGONS, %d BASES, %d STARS" % rec)
            self._say("%s: %s. ABOUT WARP %.1f." % (_qname(qx, qy), what, w), DIM)

    def _read_keys(self):
        if not hasattr(tulip, "keys"):
            return
        try:
            held = tulip.keys()[1:]
        except Exception:
            return
        for code in held:
            if code and code not in self.keys_held:
                self._on_key(code)
        self.keys_held = held

    def _on_key(self, code):
        if code == K_ESC:
            if self.picker is not None:
                self._picker_done(False)
                return
            self.quitting = True
            tulip.frame_callback()
            _silence()
            tulip.defer(lambda app: app.quit(), self.app, 20)
            return
        if self.picker is not None:
            p = self.picker
            step = 100 if p["max"] >= 1000 else 10
            if code == K_RIGHT:
                self._picker_set(p["value"] + step)
            elif code == K_LEFT:
                self._picker_set(p["value"] - step)
            elif code == K_UP:
                self._picker_set(p["value"] + 10)
            elif code == K_DOWN:
                self._picker_set(p["value"] - 10)
            elif code == K_ENTER or code == K_SPACE:
                self._picker_done(True)
            return
        if self.script is not None:
            return
        if self.overlay is not None and code in (K_ENTER, K_SPACE):
            self._close_overlay()
            return
        if code in _KEYMAP:
            self._command(_KEYMAP[code])
            return
        if code == K_TAB:
            self.focus = "map" if self.focus == "srs" else "srs"
            return
        moves = {K_RIGHT: (1, 0), K_LEFT: (-1, 0), K_DOWN: (0, 1), K_UP: (0, -1)}
        if code in moves and self.overlay is None:
            (mx, my) = moves[code]
            if self.focus == "srs":
                (x, y) = self.sel_sector or (self.sx, self.sy)
                self._aim_sector(min(7, max(0, x + mx)), min(7, max(0, y + my)))
            else:
                (x, y) = self.sel_quad or (self.qx, self.qy)
                self._aim_quad(min(7, max(0, x + mx)), min(7, max(0, y + my)))

    # ------------------------------------------------------------- frame loop

    def _tick(self, app):
        # The frame scheduler can still reach us after a switch away or a quit.
        if not app.active or self.quitting:
            return
        try:
            self._frame()
        except Exception as e:
            import sys
            tulip.frame_callback()
            print("startrek: stopped on an error")
            sys.print_exception(e)

    def _frame(self):
        now = tulip.ticks_ms()
        self.now = now
        self._read_keys()
        if self.script is not None and now - self.wake >= 0:
            try:
                wait = next(self.script)
                self.wake = now + (wait or 0)
            except StopIteration:
                self.script = None
                self._draw_status()
        # Condition red blinks.
        if now - self.blink_at >= 500:
            self.blink_at = now
            self.blink_on = not self.blink_on
            if self._condition() == "RED" and not self.over:
                self._draw_condition()


def run(app):
    app.game = True
    game = StarTrek(app)
    game.drawn = True
    app.startrek = game
    app.activate_callback = game.activate
    app.deactivate_callback = game.deactivate
    app.present()
