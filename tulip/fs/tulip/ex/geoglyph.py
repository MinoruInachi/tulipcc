"""GEOGLYPH -- a vertically scrolling shooter for tulipcc, after the air-and-
ground arcade games of the early 80s.

All the art, names and music here are original.

    run('geoglyph')

You fly north over forests, rivers, roads and old desert line drawings. Things
in the air and things on the ground are separate targets with separate weapons:
the cannon only reaches the air, and bombs only reach the ground, landing where
the sight in front of the ship is. The sight turns red over a ground target --
and it also flickers over some places where nothing seems to be.

Keyboard: arrows (or WASD) fly, Z (or space) fires -- hold it -- and X bombs.
ESC quits.

Touch: drag in the left panel to fly (the ship follows the finger's movement,
not its position), hold the FIRE column of the right panel, tap the BOMB
column. QUIT is in the top right corner.

How the ground works
--------------------
The terrain is the BG plane scrolled by the display itself: every screen line
gets the same y speed, so nothing redraws it. Speeds go through a C `%` that
keeps the sign, so a negative speed would index before the BG buffer; scrolling
down is spelled as BGH - SCROLL instead, which is -SCROLL mod the plane height.

With that, world row w (w grows the further north you have flown) always lives
at BG row (-w) % BGH, and shows on screen line T - w, where T = SCROLL x frames.
The plane is OFF_Y rows taller than the screen, and those rows are always the
ones just above the top of the screen, so new terrain is drawn there in strips
of SEG rows a moment before it scrolls into view.

Ground targets are drawn into the BG as part of that terrain and a destroyed
one gets a crater drawn over it, so they cost no sprites. Their positions
follow T, which counts frame callbacks, and the ground therefore moves per
frame while everything in the air moves per millisecond. On a Tab5 a scrolling
screen is a full recomposite every frame (about 10 fps), so sprites add nothing
to what a frame already costs there.

The side panels are the same in every BG row, so they look still while the
plane scrolls under them. Anything with text on it -- the scores, the labels,
the banners -- is a sprite, painted in the BG's right-hand margin (never on
screen) and copied into sprite RAM.
"""

import math
import random

import amy
import tulip

# ------------------------------------------------------------------ geometry

(SW, SH) = tulip.screen_size()
# The BG plane's margin past the screen, sized the way display.h sizes it.
OFF_X = max(128, SW // 8)
OFF_Y = max(100, SH // 6)
BGH = SH + OFF_Y
SCRATCH = SW                   # x of the margin: free canvas for art and text

SCROLL = 8 if SW >= 1200 else 3   # ground pixels per frame
SEG = OFF_Y * 2 // 3              # terrain strip height; see _need_terrain

# The arcade's 7:9 portrait playfield, at full height, with panels either side.
PF_W = (SH * 7 // 9) // 8 * 8
PF_X = (SW - PF_W) // 2
PF_R = PF_X + PF_W
PANEL_W = PF_X

S = 2                          # art pixel -> screen pixels; sprites are 32x32
TILE = 16 * S

# Where the ship may fly, and how far ahead of it the bombsight sits.
FLY_TOP = SH * 3 // 10
FLY_BOTTOM = SH - TILE - 8
SIGHT_AHEAD = SH * 3 // 10

# Speeds in pixels per second (the air) -- see the note above about the ground.
SHIP_SPEED = 330
SHOT_SPEED = 1100
BULLET_SPEED = 300
FIRE_MS = 120
BOMB_MS = 520
BOMB_REACH = 24                # how close to a ground target a bomb must land

ZONE = SEG * 15                # world rows per kind of terrain
ZONES_PER_AREA = 6

HUD_W, HUD_H = min(112, OFF_X - 8), 32
LABEL_W, LABEL_H = 48, 16
BANNER_W, BANNER_H = OFF_X - 8, 28
HUD_FONT, TITLE_FONT = 9, 18

EXTRA_SHIP_AT = (20000, 60000)

# ------------------------------------------------------------------- palette


def _pal(r, g, b):
    """Pack 8-bit r, g, b into a Tulip RGB332 palette index."""
    return (r & 0xE0) | ((g & 0xE0) >> 3) | ((b & 0xC0) >> 6)


CLEAR = 0x55                   # the palette entry every plane treats as see-through
BLACK = 0
WHITE = _pal(255, 255, 255)

PANEL = _pal(0, 0, 85)
PANEL_LIT = _pal(36, 36, 170)
PANEL_EDGE = _pal(146, 146, 255)
FIRE_ZONE = _pal(73, 0, 85)
BOMB_ZONE = _pal(73, 36, 0)
HUD_BG = _pal(0, 0, 0)

GRASS = _pal(109, 146, 85)
GRASS_DARK = _pal(73, 109, 85)
FOREST = _pal(0, 73, 0)
FOREST_LIT = _pal(36, 109, 0)
SAND = _pal(182, 146, 85)
SAND_DARK = _pal(146, 109, 85)
PLAIN = _pal(146, 146, 85)
PLAIN_DARK = _pal(109, 109, 85)
LINE = _pal(219, 219, 170)
WATER = _pal(0, 73, 170)
WATER_LIT = _pal(73, 146, 255)
BANK = _pal(182, 182, 170)
ROAD = _pal(109, 109, 109)
ROAD_EDGE = _pal(73, 73, 85)
CRATER = _pal(73, 36, 0)
CRATER_DEEP = _pal(36, 0, 0)

# One character per colour in the sprite art. '.' is see-through.
INK = {
    ".": CLEAR,
    "K": BLACK,
    "W": WHITE,
    "L": _pal(182, 182, 170),
    "G": _pal(109, 109, 109),
    "D": _pal(73, 73, 85),
    "R": _pal(255, 0, 0),
    "O": _pal(255, 146, 0),
    "Y": _pal(255, 255, 0),
    "C": _pal(0, 219, 255),
    "B": _pal(0, 73, 255),
    "E": _pal(0, 146, 85),
    "T": _pal(109, 109, 0),
    "H": _pal(182, 182, 85),
}

# ----------------------------------------------------------------------- art
# All 16x16 unless noted, drawn at S x S per character.

SHIP = (
    ".......WW.......",
    "......WLLW......",
    "......WCCW......",
    ".....WLCCLW.....",
    ".....WLLLLW.....",
    "....DWLLLLWD....",
    "...DGWLRRLWGD...",
    "..DGGWLLLLWGGD..",
    ".DGGGWLLLLWGGGD.",
    "DGGLLWLLLLWLLGGD",
    "DGLLDDWLLWDDLLGD",
    ".DDD.DGLLGD.DDD.",
    "......GDDG......",
    ".....DG..GD.....",
    ".....OY..YO.....",
    "......O..O......",
)

SIGHT = (
    "XXXXX......XXXXX",
    "X..............X",
    "X..............X",
    "X..............X",
    "X..............X",
    "................",
    ".......XX.......",
    "......X..X......",
    "......X..X......",
    ".......XX.......",
    "................",
    "X..............X",
    "X..............X",
    "X..............X",
    "X..............X",
    "XXXXX......XXXXX",
)

SHOT = (".Y.", "YWY", "YWY", "YWY", ".Y.", ".Y.", ".O.", ".O.")   # 3x8

BOMB = (
    ("..DDDD..", ".DLLLLD.", "DLWWLLGD", "DLWLLLGD",
     "DLLLLGGD", "DLLLGGGD", ".DGGGGD.", "..DDDD.."),
    ("........", "..DDDD..", ".DLWLGD.", ".DWLLGD.",
     ".DLLGGD.", ".DLGGGD.", "..DDDD..", "........"),
    ("........", "........", "...DD...", "..DLGD..",
     "..DGGD..", "...DD...", "........", "........"),
)

BULLET = ((".WW.", "WYYW", "WYYW", ".WW."),
          (".YY.", "YRRY", "YRRY", ".YY."))                    # 4x4

RING = (
    (".....LLLLLL.....",
     "...LLWWWWWWLL...",
     "..LWWLLLLLLWWL..",
     ".LWLLGGGGGGLLWL.",
     ".LWLGG....GGLWL.",
     "LWLGG......GGLWL",
     "LWRG........GRWL",
     "LWLG........GLWL",
     "LWLG........GLWL",
     "LWRG........GRWL",
     "LWLGG......GGLWL",
     ".LWLGG....GGLWL.",
     ".LWLLGGGGGGLLWL.",
     "..LWWLLLLLLWWL..",
     "...LLWWWWWWLL...",
     ".....LLLLLL....."),
    ("................",
     "................",
     "................",
     "....LLLLLLLL....",
     "..LLWWWWWWWWLL..",
     ".LWWLLGGGGLLWWL.",
     "LWRGG......GGRWL",
     "LWLG........GLWL",
     "LWLG........GLWL",
     "LWRGG......GGRWL",
     ".LWWLLGGGGLLWWL.",
     "..LLWWWWWWWWLL..",
     "....LLLLLLLL....",
     "................",
     "................",
     "................"),
    ("................",
     "................",
     "................",
     "................",
     "................",
     "................",
     "..LLLLLLLLLLLL..",
     "LLWWWRWWWWRWWWLL",
     "GGLLLLLLLLLLLLGG",
     "..GGGGGGGGGGGG..",
     "................",
     "................",
     "................",
     "................",
     "................",
     "................"),
)

DART = (
    ".......OO.......",
    ".......YY.......",
    "..D....DD....D..",
    "..DG..DLLD..GD..",
    "..DLGDLWWLDGLD..",
    "..DLLGLWWLGLLD..",
    "...DLLLWWLLLD...",
    "...DLLLLLLLLD...",
    "....DLLRRLLD....",
    "....DGLRRLGD....",
    ".....DGLLGD.....",
    ".....DGLLGD.....",
    "......DGGD......",
    "......DGGD......",
    ".......DD.......",
    "................",
)

SWOOP = (
    "................",
    "................",
    "................",
    "......CCCC......",
    ".....CWWWWC.....",
    "....CWBBBBWC....",
    "CCCCCBBBBBBCCCCC",
    "BLLLLLLLLLLLLLLB",
    ".BEEEEEEEEEEEEB.",
    "..BBB.BBBB.BBB..",
    "......RYYR......",
    ".......RR.......",
    "................",
    "................",
    "................",
    "................",
)

SLAB = (
    ("................",
     "................",
     "................",
     "................",
     "DDDDDDDDDDDDDDDD",
     "DLLWWWWWWWWWWLLD",
     "DLGGGGGGGGGGGGLD",
     "DLGDDGGDDGGDDGLD",
     "DLGDDGGDDGGDDGLD",
     "DLGGGGGGGGGGGGLD",
     "DLLLLLLLLLLLLLLD",
     "DDDDDDDDDDDDDDDD",
     "................",
     "................",
     "................",
     "................"),
    ("................",
     "................",
     "................",
     "................",
     "................",
     "................",
     "DDDDDDDDDDDDDDDD",
     "DLLWWWWWWWWWWLLD",
     "DGGGGGGGGGGGGGGD",
     "DDDDDDDDDDDDDDDD",
     "................",
     "................",
     "................",
     "................",
     "................",
     "................"),
)

TANK_ART = (
    "................",
    "..DDD......DDD..",
    "..DGD.TTTT.DGD..",
    "..DDDTHHHHTDDD..",
    "..DGDTHTTHTDGD..",
    "..DDDTHTTHTDDD..",
    "..DGDTHHHHTDGD..",
    "..DDDTTTTTTDDD..",
    "..DGD..DD..DGD..",
    "..DDD..DD..DDD..",
    ".......DD.......",
    ".......DD.......",
    "................",
    "................",
    "................",
    "................",
)

# Scores, per kind of target.
RING_POINTS, DART_POINTS, SWOOP_POINTS = 50, 100, 70
TANK_POINTS, DOME_POINTS, PYLON_POINTS = 200, 300, 200
SECRET_POINTS, SPIRE_POINTS = 2000, 1000

# ------------------------------------------------------------------- sprites
# Sprite 0 is left alone: some boards park the mouse pointer on it.

SPR_HUD1, SPR_HUD2, SPR_FIRE, SPR_BOMB_L, SPR_QUIT, SPR_BANNER = 1, 2, 3, 4, 5, 6
SPR_SIGHT, SPR_SHIP, SPR_BOMB = 7, 8, 9
SPR_SHOTS = (10, 11, 12)
SPR_BULLETS = (13, 14, 15, 16, 17, 18)
SPR_AIR = (19, 20, 21, 22, 23, 24, 25, 26)
SPR_TANKS = (27, 28)
SPR_BOOMS = (29, 30, 31)
SPRITE_RAM = 32 * 32 * 32

# -------------------------------------------------------------------- sounds
# Our own synths, so AMY hands out the oscs and nothing collides with the
# synths Tulip already has on the MIDI channels (PatchSynth counts up from 16).

SYN_ZAP, SYN_DROP, SYN_HIT, SYN_BOOM = 24, 25, 26, 27
SYN_THUMP, SYN_BLIP, SYN_BASS, SYN_LEAD = 28, 29, 30, 31
SYNTHS = (SYN_ZAP, SYN_DROP, SYN_HIT, SYN_BOOM,
          SYN_THUMP, SYN_BLIP, SYN_BASS, SYN_LEAD)
MUSIC_TAG = 240
MUSIC_PERIOD = 384             # two bars of sixteenths at 48 PPQ

# (sixteenth, note) -- a two-bar loop in D minor.
BASS_LINE = ((0, 38), (3, 38), (6, 38), (8, 41), (11, 40), (14, 36),
             (16, 38), (19, 38), (22, 38), (24, 34), (27, 33), (30, 36))
LEAD_LINE = ((0, 74), (2, 69), (4, 77), (6, 69), (8, 76), (10, 69),
             (12, 72), (14, 69), (16, 74), (18, 69), (20, 77), (22, 79),
             (24, 76), (26, 72), (28, 69), (30, 67))

# Kinds of things on the ground.
DOME, PYLON, SECRET, SPIRE, TANK = 0, 1, 2, 3, 4
# Kinds of things in the air.
RING_KIND, DART_KIND, SWOOP_KIND, SLAB_KIND = 0, 1, 2, 3

# ------------------------------------------------------------------- helpers


def _bg_y(w):
    """The BG row world row w lives on."""
    return (-w) % BGH


def _wrap(y, reach):
    """The BG rows to draw something at: y, and y moved past the wrap too if
    the shape (reaching `reach` rows either side of y) crosses it. Drawing
    clips, so the copy that lands off the plane costs nothing."""
    if y - reach < 0:
        return (y, y + BGH)
    if y + reach >= BGH:
        return (y, y - BGH)
    return (y,)


class Air:
    """An enemy in the air. Screen coordinates, pixels per second."""

    def __init__(self, sprite, kind, x, y):
        self.sprite = sprite
        self.kind = kind
        self.x = float(x)
        self.y = float(y)
        self.vx = 0.0
        self.vy = 0.0
        self.age = 0               # ms since it appeared
        self.state = 0
        self.fired = False
        self.home_x = float(x)
        self.side = 1
        self.stop_y = 0
        self.frame = 0


class Ground:
    """A target on the ground, in world coordinates (see the module note)."""

    def __init__(self, kind, wx, wy):
        self.kind = kind
        self.wx = wx
        self.wy = float(wy)
        self.alive = True
        self.fire_in = random.randrange(900, 2600)
        self.sprite = None         # tanks only
        self.speed = 0.0           # tanks only, world rows per second


class Shot:
    def __init__(self, sprite, x, y):
        self.sprite = sprite
        self.x = x
        self.y = float(y)


class Bullet:
    def __init__(self, sprite, x, y, vx, vy):
        self.sprite = sprite
        self.x = float(x)
        self.y = float(y)
        self.vx = vx
        self.vy = vy
        self.age = 0


class Boom:
    def __init__(self, sprite, x, y, wy=None):
        self.sprite = sprite
        self.x = x
        self.y = y
        self.wy = wy               # set for a ground blast, which scrolls
        self.age = 0


# ------------------------------------------------------------------ the game


class Geoglyph:
    def __init__(self, app):
        self.app = app
        self.hi_score = 0
        self.quitting = False
        self.touching = False
        self.last_mover = None     # the flying finger's last position
        self.hud_dirty = False
        self.autopilot = False     # set from the REPL to soak-test a device
        self.now = 0
        self.vis = bytearray(32)
        self.state = "title"
        self.scrolling = False
        self.start_scroll = False
        self.frames = 0

    # ------------------------------------------------------------ lifecycle

    def activate(self, app):
        self.now = tulip.ticks_ms()
        self.quitting = False
        tulip.frame_callback()
        tulip.bg_scroll()
        tulip.Sprite.reset()
        self.vis = bytearray(32)
        self._sample_inks()
        self._load_art()
        self._start_sounds()
        self._title()
        try:
            tulip.touch_callback(self._touch)
        except Exception:
            pass                   # no touch panel here, keys still work
        tulip.frame_callback(self._tick, app)

    def deactivate(self, app):
        tulip.frame_callback()
        try:
            tulip.touch_callback()
        except Exception:
            pass
        # Put the scroll back before anyone else draws on this plane.
        tulip.bg_scroll()
        self.scrolling = False
        self._stop_music()
        for syn in SYNTHS:
            amy.send(synth=syn, num_voices=0)
        tulip.sprite_clear()
        self.vis = bytearray(32)

    # ------------------------------------------------------------------ art

    def _sample_inks(self):
        """What each palette entry is in bytes on this board's planes -- one
        byte on most Tulips, two (RGB565) on a Tab5. Read back from the margin
        rather than assumed, so the art is right on either."""
        self.px = {}
        for color in INK.values():
            tulip.bg_pixel(SCRATCH, 0, color)
            self.px[color] = bytes(tulip.bg_bitmap(SCRATCH, 0, 1, 1))

    def _art(self, rows, over=None):
        out = bytearray()
        for row in rows:
            line = b""
            for char in row:
                if over is not None and char in over:
                    line += self.px[over[char]] * S
                else:
                    line += self.px[INK[char]] * S
            out += line * S
        return bytes(out)

    def _reserve(self, w, h):
        mem = self.mem
        if mem + w * h > SPRITE_RAM:
            raise MemoryError("geoglyph: out of sprite RAM at %d" % mem)
        self.mem = mem + w * h
        return mem

    def _load(self, rows, over=None):
        (w, h) = (len(rows[0]) * S, len(rows) * S)
        mem = self._reserve(w, h)
        tulip.sprite_bitmap(self._art(rows, over), mem)
        return (mem, w, h)

    def _load_art(self):
        self.mem = 0
        self.a_ship = self._load(SHIP)
        self.a_sight = (self._load(SIGHT, {"X": INK["C"]}),
                        self._load(SIGHT, {"X": INK["R"]}))
        self.a_shot = self._load(SHOT)
        self.a_bomb = [self._load(frame) for frame in BOMB]
        self.a_bullet = [self._load(frame) for frame in BULLET]
        self.a_ring = [self._load(frame) for frame in RING]
        self.a_dart = self._load(DART)
        self.a_swoop = self._load(SWOOP)
        self.a_slab = [self._load(frame) for frame in SLAB]
        self.a_tank = self._load(TANK_ART)
        self.a_boom = self._explosions()
        self.m_hud1 = self._reserve(HUD_W, HUD_H)
        self.m_hud2 = self._reserve(HUD_W, HUD_H)
        self.m_fire = self._reserve(LABEL_W, LABEL_H)
        self.m_bomb = self._reserve(LABEL_W, LABEL_H)
        self.m_quit = self._reserve(LABEL_W, LABEL_H)
        self.m_banner = self._reserve(BANNER_W, BANNER_H)
        self._paint(SPR_FIRE, self.m_fire, LABEL_W, LABEL_H,
                    (("FIRE", LABEL_H // 2, WHITE, HUD_FONT),), FIRE_ZONE)
        self._paint(SPR_BOMB_L, self.m_bomb, LABEL_W, LABEL_H,
                    (("BOMB", LABEL_H // 2, WHITE, HUD_FONT),), BOMB_ZONE)
        self._paint(SPR_QUIT, self.m_quit, LABEL_W, LABEL_H,
                    (("QUIT", LABEL_H // 2, WHITE, HUD_FONT),), PANEL_LIT)

    def _explosions(self):
        """Three frames of fireball, drawn with circles in the margin."""
        y, o, r, w = INK["Y"], INK["O"], INK["R"], WHITE
        frames = []
        for circles in (((16, 16, 8, w), (16, 16, 4, y)),
                        ((16, 16, 13, o), (16, 16, 9, y), (15, 15, 4, w)),
                        ((16, 16, 15, r), (16, 16, 11, CLEAR), (6, 8, 3, o),
                         (25, 10, 3, o), (9, 24, 3, o), (24, 23, 2, y))):
            tulip.bg_rect(SCRATCH, 0, TILE, TILE, CLEAR, 1)
            for (cx, cy, radius, color) in circles:
                tulip.bg_circle(SCRATCH + cx, cy, radius, color, 1)
            mem = self._reserve(TILE, TILE)
            tulip.sprite_bitmap(tulip.bg_bitmap(SCRATCH, 0, TILE, TILE), mem)
            frames.append((mem, TILE, TILE))
        return frames

    def _paint(self, sprite, mem, w, h, lines, bg=HUD_BG):
        """Letter a sprite: draw in the margin, copy into sprite RAM. Each line
        is (text, centre y, colour, font), centred across the sprite."""
        tulip.bg_rect(SCRATCH, 0, w, h, bg, 1)
        tulip.bg_rect(SCRATCH, 0, w, h, PANEL_EDGE, 0)
        for (text, cy, color, font) in lines:
            tulip.bg_str(text, SCRATCH, cy, color, font, w, 0)
        tulip.sprite_bitmap(tulip.bg_bitmap(SCRATCH, 0, w, h), mem)
        tulip.sprite_register(sprite, mem, w, h)

    # -------------------------------------------------------------- sprites

    def _use(self, sprite, art):
        tulip.sprite_register(sprite, art[0], art[1], art[2])

    def _show(self, sprite, x, y):
        # sprite_move refuses anything off the screen, so off-screen is off.
        x = int(x)
        y = int(y)
        if 0 <= x < SW and 0 <= y < SH:
            tulip.sprite_move(sprite, x, y)
            if not self.vis[sprite]:
                tulip.sprite_on(sprite)
                self.vis[sprite] = 1
        else:
            self._hide(sprite)

    def _hide(self, sprite):
        if self.vis[sprite]:
            tulip.sprite_off(sprite)
            self.vis[sprite] = 0

    def _hide_all(self):
        for sprite in range(1, 32):
            self._hide(sprite)

    def _show_labels(self):
        mid = PF_R + PANEL_W // 2
        self._show(SPR_FIRE, PF_R + (PANEL_W // 2 - LABEL_W) // 2, SH - 40)
        self._show(SPR_BOMB_L, mid + (PANEL_W // 2 - LABEL_W) // 2, SH - 40)
        self._show(SPR_QUIT, SW - LABEL_W - 24, 12)

    def _draw_hud(self):
        self.hud_dirty = False
        hi = max(self.hi_score, self.score)
        self._paint(SPR_HUD1, self.m_hud1, HUD_W, HUD_H,
                    (("1UP %07d" % self.score, 9, WHITE, HUD_FONT),
                     ("HI  %07d" % hi, 23, INK["Y"], HUD_FONT)))
        self._paint(SPR_HUD2, self.m_hud2, HUD_W, HUD_H,
                    (("SHIPS %d" % self.lives, 9, WHITE, HUD_FONT),
                     ("AREA %d" % self.area, 23, INK["C"], HUD_FONT)))
        x = (PANEL_W - HUD_W) // 2
        self._show(SPR_HUD1, x, 16)
        self._show(SPR_HUD2, x, 24 + HUD_H)

    def _banner(self, text, ms):
        self._paint(SPR_BANNER, self.m_banner, BANNER_W, BANNER_H,
                    ((text, BANNER_H // 2, INK["Y"], HUD_FONT),))
        self._show(SPR_BANNER, PF_X + (PF_W - BANNER_W) // 2, SH * 2 // 5)
        self.banner_ms = ms

    # --------------------------------------------------------------- sounds

    def _start_sounds(self):
        # Every envelope reaches zero while the note is still held: nothing
        # here ever sends a note-off, so a sustain would never stop.
        for (syn, wave, env) in ((SYN_ZAP, amy.SAW_DOWN, "0,1,70,0,0,0"),
                                 (SYN_DROP, amy.TRIANGLE, "0,1,420,0,0,0"),
                                 (SYN_HIT, amy.NOISE, "0,1,160,0,0,0"),
                                 (SYN_BOOM, amy.NOISE, "0,1,700,0,0,0"),
                                 (SYN_THUMP, amy.SINE, "0,1,380,0,0,0"),
                                 (SYN_BLIP, amy.PULSE, "0,1,60,0,0,0"),
                                 (SYN_BASS, amy.TRIANGLE, "0,1,170,0,0,0"),
                                 (SYN_LEAD, amy.PULSE, "0,1,90,0,0,0")):
            amy.send(synth=syn, num_voices=2, oscs_per_voice=1)
            amy.send(synth=syn, osc=0, wave=wave, bp0=env)

    def _sfx(self, syn, note, vel):
        amy.send(synth=syn, note=note, vel=vel)

    def _start_music(self):
        # On AMY's own sequencer, so the beat does not wobble with the frame
        # rate. Everything on one tag, which a single send clears.
        self._stop_music()
        for (step, note) in BASS_LINE:
            amy.send(synth=SYN_BASS, note=note, vel=0.4,
                     ticks="%d,%d,%d" % (step * 12, MUSIC_PERIOD, MUSIC_TAG))
        for (step, note) in LEAD_LINE:
            amy.send(synth=SYN_LEAD, note=note, vel=0.08,
                     ticks="%d,%d,%d" % (step * 12, MUSIC_PERIOD, MUSIC_TAG))

    def _stop_music(self):
        amy.send(ticks=",,%d" % MUSIC_TAG)

    # ------------------------------------------------------- title and panels

    def _draw_panels(self, y, h):
        """The side panels over BG rows y .. y+h. Every row is the same, which
        is what keeps them still while the ground scrolls under them."""
        mid = PF_R + PANEL_W // 2
        tulip.bg_rect(0, y, PF_X, h, PANEL, 1)
        tulip.bg_rect(PF_R, y, PANEL_W, h, PANEL, 1)
        tulip.bg_rect(16, y, PF_X - 40, h, PANEL_LIT, 1)            # fly pad
        tulip.bg_rect(PF_R + 24, y, PANEL_W // 2 - 30, h, FIRE_ZONE, 1)
        tulip.bg_rect(mid + 6, y, PANEL_W // 2 - 22, h, BOMB_ZONE, 1)
        for x in (16, PF_X - 25, PF_R + 24, mid - 7, mid + 6, SW - 17):
            tulip.bg_rect(x, y, 1, h, PANEL_EDGE, 1)
        for (dx, color) in ((1, PANEL_EDGE), (3, INK["G"]), (5, INK["D"])):
            tulip.bg_rect(PF_X - dx - 1, y, 2, h, color, 1)
            tulip.bg_rect(PF_R + dx - 1, y, 2, h, color, 1)

    def _title(self):
        self.state = "title"
        self.scrolling = False
        self.start_scroll = False
        self._stop_music()
        self._hide_all()
        tulip.bg_scroll()
        tulip.bg_clear(BLACK)
        self._draw_panels(0, SH)
        tulip.bg_rect(PF_X, 0, PF_W, SH, PLAIN_DARK, 1)
        cx = PF_X + PF_W // 2
        self._glyph(0, cx, SH * 7 // 10, SH // 5, (0,))
        tulip.bg_str("GEOGLYPH", PF_X + 3, SH // 5 + 3, BLACK, TITLE_FONT, PF_W, 0)
        tulip.bg_str("GEOGLYPH", PF_X, SH // 5, LINE, TITLE_FONT, PF_W, 0)
        y = SH // 5 + 50
        for (text, color) in (("CANNON FOR THE AIR - BOMBS FOR THE GROUND", WHITE),
                              ("BOMBS LAND UNDER THE SIGHT", WHITE),
                              ("", WHITE),
                              ("KEYS: ARROWS FLY  Z FIRE  X BOMB  ESC QUIT", LINE),
                              ("TOUCH: DRAG LEFT PANEL  HOLD FIRE  TAP BOMB", LINE),
                              ("", WHITE),
                              ("HI SCORE %07d" % self.hi_score, INK["Y"])):
            if text:
                tulip.bg_str(text, PF_X, y, color, HUD_FONT, PF_W, 0)
            y += 20
        tulip.bg_str("PRESS Z OR TAP THE GROUND", PF_X, SH - 40, INK["C"],
                     HUD_FONT, PF_W, 0)
        self._show_labels()

    # -------------------------------------------------------------- terrain

    def _land(self, w):
        """What kind of land world row w is: the first zone is open grass with
        nothing on it, then the kinds come round in turn."""
        zone = w // ZONE
        if zone < 1:
            return "grass"
        return ("forest", "river", "desert", "road", "forest", "glyph")[(zone - 1) % 6]

    def _river_x(self, w):
        # Enters under the left panel at the start of its zone and leaves under
        # the right one at the end, so it never visibly starts or stops.
        start = (w // ZONE) * ZONE
        return int(PF_X - 90 + (PF_W + 180) * (w - start) // ZONE
                   + 50 * math.sin(w / 90.0))

    def _road_x(self, w):
        return PF_X + 80 + ((w // ZONE) * 7919) % (PF_W - 160)

    def _need_terrain(self):
        # A strip can only go down once every row of it is in the hidden band
        # above the screen: OFF_Y rows past the top. SEG is two thirds of that,
        # which leaves a few frames of slack to draw it in before it shows.
        top = self.frames * SCROLL
        while self.next_w + SEG - 1 <= top + OFF_Y:
            self._strip(self.next_w)
            self.next_w += SEG

    def _strip(self, w0):
        """Draw world rows w0 .. w0+SEG-1. Everything in it keeps inside the
        strip, because the strip above is drawn later and fills its own rows."""
        land = self._land(w0)
        top = _bg_y(w0 + SEG - 1)
        rows = (top,) if top + SEG <= BGH else (top, top - BGH)
        if land == "desert":
            (base, dark) = (SAND, SAND_DARK)
        elif land == "glyph":
            (base, dark) = (PLAIN, PLAIN_DARK)
        else:
            (base, dark) = (GRASS, GRASS_DARK)
        specks = [(random.randrange(PF_X, PF_R - 4), random.randrange(SEG - 2),
                   random.randrange(2, 6)) for _ in range(12)]
        for y in rows:
            tulip.bg_rect(PF_X, y, PF_W, SEG, base, 1)
            for (x, dy, w) in specks:
                tulip.bg_rect(x, y + dy, w, 2, dark, 1)

        avoid = None               # (x, half width) of a river or road
        if land == "forest":
            self._trees(rows)
        elif land == "desert":
            self._rocks(rows)
        elif land == "river":
            avoid = self._river(w0, rows)
        elif land == "road":
            avoid = self._road(w0, rows)
        elif land == "glyph" and random.random() < 0.6:
            kind = random.randrange(3)
            size = SEG // 2 - 6
            cx = random.randrange(PF_X + size + 10, PF_R - size - 10)
            self._glyph(kind, cx, SEG // 2, size, rows)
            if random.random() < 0.3:
                # Nothing is drawn for a secret. The sight still notices it.
                self.ground.append(Ground(SECRET, cx, w0 + SEG // 2))

        if land != "grass" and random.random() < min(0.55, 0.25 + 0.05 * self.area):
            self._place_target(land, w0, avoid)
        if land == "road" and random.random() < 0.25:
            self._spawn_tank(w0, avoid[0])

    def _trees(self, rows):
        for _ in range(random.randrange(2, 5)):
            cx = random.randrange(PF_X + 30, PF_R - 30)
            cy = random.randrange(16, SEG - 16)
            for _ in range(random.randrange(4, 8)):
                r = random.randrange(6, 13)
                x = cx + random.randrange(-22, 23)
                y = min(max(cy + random.randrange(-12, 13), r + 1), SEG - r - 2)
                for top in rows:
                    tulip.bg_circle(x, top + y, r, FOREST, 1)
                    tulip.bg_circle(x - 2, top + y - 2, r - 4, FOREST_LIT, 1)

    def _rocks(self, rows):
        for _ in range(random.randrange(3, 8)):
            x = random.randrange(PF_X + 10, PF_R - 10)
            y = random.randrange(8, SEG - 8)
            r = random.randrange(2, 6)
            for top in rows:
                tulip.bg_circle(x + 1, top + y + 1, r, SAND_DARK, 1)
                tulip.bg_circle(x, top + y, r, INK["H"], 1)

    def _river(self, w0, rows):
        half = 34
        xb = self._river_x(w0)
        xt = self._river_x(w0 + SEG)
        for top in rows:
            bottom = top + SEG
            tulip.bg_triangle(xt - half, top, xt + half, top, xb - half, bottom, WATER, 1)
            tulip.bg_triangle(xt + half, top, xb + half, bottom, xb - half, bottom, WATER, 1)
            tulip.bg_line(xt - half, top, xb - half, bottom, BANK, 3)
            tulip.bg_line(xt + half, top, xb + half, bottom, BANK, 3)
            tulip.bg_line(xt - 8, top + 10, xt - 8 + (xb - xt) // 3, top + 30, WATER_LIT, 2)
            # The river runs out under the panels, so put them back on top.
            self._draw_panels(top, SEG)
        return ((xt + xb) // 2, half)

    def _road(self, w0, rows):
        x = self._road_x(w0)
        cross = random.random() < 0.12
        for top in rows:
            if cross:
                tulip.bg_rect(PF_X, top + 22, PF_W, 36, ROAD, 1)
                tulip.bg_rect(PF_X, top + 20, PF_W, 2, ROAD_EDGE, 1)
                tulip.bg_rect(PF_X, top + 58, PF_W, 2, ROAD_EDGE, 1)
            tulip.bg_rect(x - 22, top, 44, SEG, ROAD, 1)
            tulip.bg_rect(x - 24, top, 3, SEG, ROAD_EDGE, 1)
            tulip.bg_rect(x + 22, top, 3, SEG, ROAD_EDGE, 1)
            for dy in (SEG // 10, SEG * 6 // 10):
                tulip.bg_rect(x - 2, top + dy, 4, SEG // 5, LINE, 1)
        return (x, 26)

    def _glyph(self, kind, cx, cy, size, rows):
        """One of the big line drawings on the ground."""
        if kind == 0:              # spiral
            path = []
            for i in range(48):
                a = i * 0.39
                r = size * i / 47.0
                path.append((cx + int(r * math.cos(a)), cy + int(r * math.sin(a))))
            paths = (path,)
        else:
            if kind == 1:          # bird
                shape = (((0, -1.0), (0, 0.55)),
                         ((-1.0, -0.1), (-0.5, -0.55), (0, -0.2), (0.5, -0.55), (1.0, -0.1)),
                         ((-0.35, 1.0), (0, 0.55), (0.35, 1.0)))
            else:                  # a long trapezoid and a zigzag through it
                shape = (((-1.0, 1.0), (-0.5, -1.0), (0.5, -1.0), (1.0, 1.0), (-1.0, 1.0)),
                         ((-0.7, 0.5), (-0.35, -0.5), (0, 0.5), (0.35, -0.5), (0.7, 0.5)))
            paths = [[(cx + int(px * size), cy + int(py * size)) for (px, py) in line]
                     for line in shape]
        for top in rows:
            for path in paths:
                for i in range(len(path) - 1):
                    (x0, y0) = path[i]
                    (x1, y1) = path[i + 1]
                    tulip.bg_line(x0, top + y0, x1, top + y1, LINE, 2)

    def _place_target(self, land, w0, avoid):
        for _ in range(4):
            x = random.randrange(PF_X + 30, PF_R - 30)
            if avoid is None or abs(x - avoid[0]) > avoid[1] + 26:
                break
        else:
            return
        if land == "desert" or (land == "glyph" and random.random() < 0.5):
            kind = PYLON
        else:
            kind = DOME
        g = Ground(kind, x, w0 + random.randrange(20, SEG - 20))
        self.ground.append(g)
        self._draw_ground(g)

    def _draw_ground(self, g):
        y0 = _bg_y(int(g.wy))
        x = g.wx
        for y in _wrap(y0, 20):
            if not g.alive:
                tulip.bg_circle(x, y, 17, CRATER, 1)
                tulip.bg_circle(x + 2, y + 2, 11, CRATER_DEEP, 1)
                tulip.bg_rect(x - 20, y + 9, 4, 3, CRATER, 1)
                tulip.bg_rect(x + 15, y - 12, 3, 3, CRATER, 1)
            elif g.kind == DOME:
                tulip.bg_circle(x + 3, y + 3, 15, INK["D"], 1)
                tulip.bg_circle(x, y, 15, INK["D"], 1)
                tulip.bg_circle(x, y, 12, INK["G"], 1)
                tulip.bg_circle(x, y, 8, INK["L"], 1)
                tulip.bg_circle(x - 3, y - 3, 3, WHITE, 1)
                tulip.bg_circle(x, y, 3, BLACK, 1)
            elif g.kind == PYLON:
                tulip.bg_rect(x - 15, y - 15, 30, 30, INK["D"], 1)
                tulip.bg_triangle(x - 13, y - 13, x + 13, y - 13, x, y, INK["L"], 1)
                tulip.bg_triangle(x - 13, y - 13, x - 13, y + 13, x, y, INK["G"], 1)
                tulip.bg_triangle(x + 13, y - 13, x + 13, y + 13, x, y, INK["H"], 1)
                tulip.bg_triangle(x - 13, y + 13, x + 13, y + 13, x, y, INK["T"], 1)
                tulip.bg_circle(x, y, 3, INK["R"], 1)
            elif g.kind == SPIRE:
                tulip.bg_circle(x, y + 4, 14, SAND_DARK, 1)
                tulip.bg_triangle(x - 9, y + 12, x + 9, y + 12, x, y - 18, INK["Y"], 1)
                tulip.bg_triangle(x - 9, y + 12, x, y + 12, x, y - 18, INK["O"], 1)
                tulip.bg_circle(x, y - 3, 3, WHITE, 1)

    # ---------------------------------------------------------------- input

    def _touch(self, up):
        self.touching = not up

    def _read_input(self):
        """(dx, dy, nudge, fire, bomb, start, quit) from the keys, the touch
        panel, or the autopilot. dx and dy are -1..1; nudge is a finger's
        movement in pixels since the last frame, or None."""
        dx = dy = 0
        nudge = None
        fire = bomb = start = quit = False
        if hasattr(tulip, "keys"):
            for code in tulip.keys()[1:]:
                if code == 80 or code == 4:            # left arrow, A
                    dx = -1
                elif code == 79 or code == 7:          # right arrow, D
                    dx = 1
                elif code == 82 or code == 26:         # up arrow, W
                    dy = -1
                elif code == 81 or code == 22:         # down arrow, S
                    dy = 1
                elif code == 29 or code == 44:         # Z, space
                    fire = True
                elif code == 27:                       # X
                    bomb = True
                elif code == 40:                       # enter
                    start = True
                elif code == 41:                       # ESC
                    quit = True

        mover = None
        if self.touching:
            try:
                points = tulip.touch()
            except Exception:
                points = (-1, -1, -1, -1, -1, -1)
            # The panel compacts its report, so a finger has no stable slot:
            # sort the points out by where they are instead.
            for i in (0, 2, 4):
                (x, y) = (points[i], points[i + 1])
                if x < 0:
                    continue
                if x >= PF_R:
                    if x >= SW - LABEL_W - 40 and y < 48:
                        quit = True
                    elif x < PF_R + PANEL_W // 2:
                        fire = True
                    else:
                        bomb = True
                elif x < PF_X:
                    mover = (x, y)
                else:
                    start = True
        if mover is not None and self.last_mover is not None:
            mx = mover[0] - self.last_mover[0]
            my = mover[1] - self.last_mover[1]
            # A jump this big is a different finger landing, not a drag.
            if abs(mx) < 120 and abs(my) < 120:
                nudge = (mx * 1.6, my * 1.6)
        self.last_mover = mover

        if self.autopilot:
            dx = 1 if math.sin(self.now / 700.0) > 0 else -1
            dy = 1 if math.sin(self.now / 1300.0) > 0.3 else (-1 if math.sin(self.now / 1300.0) < -0.3 else 0)
            fire = True
            bomb = random.random() < 0.08
            start = True
        return (dx, dy, nudge, fire, bomb, start, quit)

    # ----------------------------------------------------------- frame loop

    def _tick(self, app):
        # The frame scheduler can still reach us after the app is switched away
        # from or quit, so check before touching the GPU.
        if not app.active or self.quitting:
            return
        try:
            self._frame()
        except Exception as e:
            import sys
            tulip.frame_callback()
            print("geoglyph: stopped on an error")
            sys.print_exception(e)

    def _frame(self):
        # One call per display frame, and the display has moved the ground by
        # SCROLL since the last one -- so count first.
        if self.scrolling:
            self.frames += 1
        elif self.start_scroll:
            self._begin_scroll()

        now = tulip.ticks_ms()
        dt = now - self.now
        # A long gap means the app was away or something else held the CPU;
        # treat it as an ordinary frame rather than teleporting everything.
        if dt < 1 or dt > 250:
            dt = 100
        self.now = now

        (dx, dy, nudge, fire, bomb, start, quit) = self._read_input()
        if quit:
            self.quitting = True
            tulip.frame_callback()
            tulip.defer(lambda app: app.quit(), self.app, 20)
            return
        if self.state == "title":
            if start or fire:
                self._new_game()
            return
        if self.scrolling:
            self._need_terrain()
        self._update(dt, dx, dy, nudge, fire, bomb)
        if self.state != "title" and self.hud_dirty:
            self._draw_hud()

    def _begin_scroll(self):
        """Set every line scrolling at once.

        This runs first thing in a frame callback, just after the display
        finished a frame, and it marks the screen changed before the loop so
        the frame now being made is a full, slow one. A frame landing half way
        through the loop would leave the lines above that point a step ahead
        of the rest for as long as the scroll runs.
        """
        tulip.bg_rect(SCRATCH, 0, 1, SH, CLEAR, 1)
        speed = BGH - SCROLL       # -SCROLL, kept positive: see the module note
        set_speed = tulip.bg_scroll_y_speed
        for line in range(SH):
            set_speed(line, speed)
        self.frames = 0
        self.scrolling = True
        self.start_scroll = False

    def _new_game(self):
        self.score = 0
        self.lives = 3
        self.area = 1
        self.extra = 0
        self.ground = []
        self.air = []
        self.shots = []
        self.bullets = []
        self.booms = []
        self.bomb = None
        self.bomb_held = False
        self.fire_wait = 0
        self.spawn_wait = 2500
        self.banner_ms = 0
        self.safe_ms = 2000
        self.timer = 0
        self.free_air = list(SPR_AIR)
        self.free_bullets = list(SPR_BULLETS)
        self.free_shots = list(SPR_SHOTS)
        self.free_tanks = list(SPR_TANKS)
        self.free_booms = list(SPR_BOOMS)

        # The scroll restarts from nothing, so the world does too.
        tulip.bg_scroll()
        self.scrolling = False
        self.frames = 0
        self._hide_all()
        tulip.bg_clear(BLACK)
        self._draw_panels(0, BGH)
        self.next_w = -(SH // SEG + 1) * SEG
        self._need_terrain()

        self.ship_x = float(PF_X + (PF_W - TILE) // 2)
        self.ship_y = float(FLY_BOTTOM - 40)
        self._use(SPR_SHIP, self.a_ship)
        self._use(SPR_SIGHT, self.a_sight[0])
        self.sight_frame = 0
        self._use(SPR_BOMB, self.a_bomb[0])
        for sprite in SPR_SHOTS:
            self._use(sprite, self.a_shot)
        for sprite in SPR_BULLETS:
            self._use(sprite, self.a_bullet[0])
        self._show_labels()
        self._draw_hud()
        self._banner("AREA 1", 2000)
        self.state = "play"
        self.start_scroll = True   # on the next frame callback
        self._start_music()

    def _update(self, dt, dx, dy, nudge, fire, bomb):
        top = self.frames * SCROLL
        if self.banner_ms > 0:
            self.banner_ms -= dt
            if self.banner_ms <= 0:
                self._hide(SPR_BANNER)
        area = 1 + max(0, top // ZONE - 1) // ZONES_PER_AREA
        if area != self.area:
            self.area = area
            self.hud_dirty = True
            self._banner("AREA %d" % area, 2000)

        if self.state == "play":
            self._fly(dt, dx, dy, nudge)
            self.fire_wait -= dt
            if fire and self.fire_wait <= 0 and self.free_shots:
                self._fire()
            if bomb and not self.bomb_held and self.bomb is None:
                self._drop_bomb(top)
            self.bomb_held = bomb
            self._aim(top)
        elif self.state == "dying":
            self.timer -= dt
            if self.timer <= 0:
                if self.lives <= 0:
                    self.hi_score = max(self.hi_score, self.score)
                    self._stop_music()
                    self._banner("GAME OVER", 4000)
                    self.state = "over"
                    self.timer = 4000
                else:
                    self.ship_x = float(PF_X + (PF_W - TILE) // 2)
                    self.ship_y = float(FLY_BOTTOM - 40)
                    self.safe_ms = 2000
                    self.state = "play"
        elif self.state == "over":
            self.timer -= dt
            if self.timer <= 0:
                self._title()
                return

        self._move_shots(dt)
        self._move_bomb(dt, top)
        self._move_air(dt)
        self._move_ground(dt, top)
        self._move_bullets(dt)
        self._age_booms(dt, top)
        if self.state == "play":
            self._spawn(dt, top)

    # ------------------------------------------------------------- the ship

    def _fly(self, dt, dx, dy, nudge):
        step = SHIP_SPEED * dt / 1000.0
        x = self.ship_x + dx * step
        y = self.ship_y + dy * step
        if nudge is not None:
            x += nudge[0]
            y += nudge[1]
        self.ship_x = min(max(x, PF_X), PF_R - TILE)
        self.ship_y = min(max(y, FLY_TOP), FLY_BOTTOM)
        if self.safe_ms > 0:
            self.safe_ms -= dt
            if (self.safe_ms // 100) % 2:
                self._hide(SPR_SHIP)
                return
        self._show(SPR_SHIP, self.ship_x, self.ship_y)

    def _sight(self):
        """Where the bombsight's centre is on screen."""
        return (self.ship_x + TILE // 2, self.ship_y + TILE // 2 - SIGHT_AHEAD)

    def _aim(self, top):
        (cx, cy) = self._sight()
        (wx, wy) = (cx, top - cy)
        lock = 0
        for g in self.ground:
            if g.alive and abs(g.wx - wx) < BOMB_REACH and abs(g.wy - wy) < BOMB_REACH:
                if g.kind != SECRET:
                    lock = 1
                    break
                if (self.now // 120) % 2:
                    lock = 1       # a flicker, not a lock: nothing shows there
        if lock != self.sight_frame:
            self._use(SPR_SIGHT, self.a_sight[lock])
            self.sight_frame = lock
        self._show(SPR_SIGHT, cx - TILE // 2, cy - TILE // 2)

    def _lose_ship(self):
        if self.state != "play" or self.safe_ms > 0:
            return
        self.lives -= 1
        self.hud_dirty = True
        self._boom(self.ship_x, self.ship_y)
        self._sfx(SYN_BOOM, 30, 0.6)
        self._sfx(SYN_THUMP, 28, 0.6)
        self._hide(SPR_SHIP)
        self._hide(SPR_SIGHT)
        for b in list(self.bullets):
            self._end_bullet(b)
        self.state = "dying"
        self.timer = 1600

    def _score(self, points):
        self.score += points
        self.hud_dirty = True
        if self.extra < len(EXTRA_SHIP_AT) and self.score >= EXTRA_SHIP_AT[self.extra]:
            self.extra += 1
            self.lives += 1
            self._sfx(SYN_BLIP, 91, 0.3)

    # ---------------------------------------------------------- the cannon

    def _fire(self):
        sprite = self.free_shots.pop()
        shot = Shot(sprite, int(self.ship_x) + (TILE - self.a_shot[1]) // 2,
                    self.ship_y - self.a_shot[2] + 6)
        self.shots.append(shot)
        self.fire_wait = FIRE_MS
        self._show(sprite, shot.x, shot.y)
        self._sfx(SYN_ZAP, 88, 0.12)

    def _move_shots(self, dt):
        (w, h) = (self.a_shot[1], self.a_shot[2])
        for shot in list(self.shots):
            was = shot.y
            shot.y -= SHOT_SPEED * dt / 1000.0
            # Everything the shot swept past this frame, not just where it is
            # now: at 10 fps it travels further than an enemy is tall.
            hit = None
            for a in self.air:
                if shot.x + w > a.x + 4 and shot.x < a.x + TILE - 4 and \
                        shot.y < a.y + TILE - 4 and was + h > a.y + 4:
                    if hit is None or a.y > hit.y:
                        hit = a
            if hit is not None:
                self._end_shot(shot)
                if hit.kind == SLAB_KIND:
                    self._sfx(SYN_BLIP, 96, 0.1)
                else:
                    self._kill_air(hit)
            elif shot.y < 0:
                self._end_shot(shot)
            else:
                self._show(shot.sprite, shot.x, shot.y)

    def _end_shot(self, shot):
        self._hide(shot.sprite)
        self.shots.remove(shot)
        self.free_shots.append(shot.sprite)

    # ------------------------------------------------------------ the bombs

    def _drop_bomb(self, top):
        (cx, cy) = self._sight()
        # [start x, start y, target world x, target world y, ms flown]
        self.bomb = [self.ship_x + TILE // 2, self.ship_y, cx, top - cy, 0]
        self._sfx(SYN_DROP, 79, 0.2)

    def _move_bomb(self, dt, top):
        b = self.bomb
        if b is None:
            return
        b[4] += dt
        if b[4] >= BOMB_MS:
            self._hide(SPR_BOMB)
            self.bomb = None
            self._bomb_lands(b[2], b[3], top)
            return
        p = b[4] / BOMB_MS
        # The target is on the ground, so it moves down the screen with it.
        x = b[0] + (b[2] - b[0]) * p
        y = b[1] + (top - b[3] - b[1]) * p
        self._use(SPR_BOMB, self.a_bomb[min(2, int(p * 3))])
        self._show(SPR_BOMB, x - 8, y - 8)

    def _bomb_lands(self, wx, wy, top):
        self._boom(wx - TILE // 2, top - wy - TILE // 2, wy)
        self._sfx(SYN_THUMP, 36, 0.5)
        for g in list(self.ground):
            if not g.alive or abs(g.wx - wx) >= BOMB_REACH or abs(g.wy - wy) >= BOMB_REACH:
                continue
            if g.kind == SECRET:
                g.kind = SPIRE
                self._draw_ground(g)
                self._score(SECRET_POINTS)
                self._banner("%d" % SECRET_POINTS, 1200)
                self._sfx(SYN_BLIP, 84, 0.3)
            else:
                self._kill_ground(g)

    def _kill_ground(self, g):
        g.alive = False
        if g.kind == DOME:
            self._score(DOME_POINTS)
        elif g.kind == PYLON:
            self._score(PYLON_POINTS)
        elif g.kind == SPIRE:
            self._score(SPIRE_POINTS)
        elif g.kind == TANK:
            self._score(TANK_POINTS)
            self._end_tank(g)
        self._sfx(SYN_BOOM, 40, 0.45)
        self._draw_ground(g)       # the crater

    # ------------------------------------------------------- on the ground

    def _spawn_tank(self, w0, road_x):
        if not self.free_tanks:
            return
        g = Ground(TANK, road_x, w0 + SEG // 2)
        g.sprite = self.free_tanks.pop()
        g.speed = 25 + random.randrange(20)
        self._use(g.sprite, self.a_tank)
        self.ground.append(g)

    def _end_tank(self, g):
        self._hide(g.sprite)
        self.free_tanks.append(g.sprite)
        g.sprite = None
        self.ground.remove(g)

    def _move_ground(self, dt, top):
        s = dt / 1000.0
        for g in list(self.ground):
            if g.kind == TANK:
                g.wy -= g.speed * s    # driving south, faster than the ground
            y = top - g.wy
            if y > SH + 40:
                if g.sprite is not None:
                    self._end_tank(g)
                else:
                    self.ground.remove(g)
                continue
            if g.sprite is not None:
                self._show(g.sprite, g.wx - TILE // 2, y - TILE // 2)
            if self.state != "play" or not g.alive or (g.kind != DOME and g.kind != TANK):
                continue
            if 0 < y < SH * 2 // 3:
                g.fire_in -= dt
                if g.fire_in <= 0:
                    g.fire_in = random.randrange(1600, 3200) - 100 * min(self.area, 8)
                    self._enemy_fire(g.wx, y)

    # ------------------------------------------------------------- the air

    def _spawn(self, dt, top):
        self.spawn_wait -= dt
        if self.spawn_wait > 0 or top < ZONE // 2:
            return
        self.spawn_wait = max(700, 2200 - 180 * self.area) + random.randrange(900)
        roll = random.random()
        if roll < 0.35:
            for _ in range(random.randrange(1, 3 + min(self.area, 3))):
                a = self._new_air(RING_KIND, random.randrange(PF_X, PF_R - TILE), -TILE)
                if a is not None:
                    a.vy = 240.0
                    a.stop_y = random.randrange(SH // 5, SH * 9 // 20)
        elif roll < 0.6:
            x = min(max(self.ship_x + random.randrange(-80, 81), PF_X), PF_R - TILE)
            a = self._new_air(DART_KIND, x, -TILE)
            if a is not None:
                a.vy = 430.0 + 30 * min(self.area, 6)
        elif roll < 0.85:
            side = 1 if random.random() < 0.5 else -1
            x = PF_X + 40 if side > 0 else PF_R - TILE - 40
            for i in range(4):
                a = self._new_air(SWOOP_KIND, x, -TILE - i * 56)
                if a is not None:
                    a.side = side
        else:
            count = random.randrange(3, 6)
            gap = (PF_W - TILE) // count
            for i in range(count):
                a = self._new_air(SLAB_KIND, PF_X + gap // 2 + i * gap, -TILE)
                if a is not None:
                    a.vy = 95.0

    def _new_air(self, kind, x, y):
        if not self.free_air:
            return None
        a = Air(self.free_air.pop(), kind, x, y)
        if kind == RING_KIND:
            self._use(a.sprite, self.a_ring[0])
        elif kind == DART_KIND:
            self._use(a.sprite, self.a_dart)
        elif kind == SWOOP_KIND:
            self._use(a.sprite, self.a_swoop)
        else:
            self._use(a.sprite, self.a_slab[0])
        self.air.append(a)
        return a

    def _move_air(self, dt):
        s = dt / 1000.0
        sx = self.ship_x + TILE // 2
        for a in list(self.air):
            was_y = a.y
            a.age += dt
            if a.kind == RING_KIND:
                if a.state == 0:
                    a.vx = min(160.0, max(-160.0, a.vx + (sx - a.x - TILE // 2) * 2.0 * s))
                    if a.y >= a.stop_y:
                        # Seen you: one shot, then away the other way.
                        a.state = 1
                        self._enemy_fire(a.x + TILE // 2, a.y + TILE // 2)
                        a.vx = 220.0 if sx < a.x + TILE // 2 else -220.0
                        a.vy = 60.0
                else:
                    a.vy -= 900.0 * s
                frame = (0, 1, 2, 1)[(a.age // (60 if a.state else 110)) % 4]
                if frame != a.frame:
                    a.frame = frame
                    self._use(a.sprite, self.a_ring[frame])
            elif a.kind == DART_KIND:
                if a.age < 450:
                    a.vx = 200.0 if sx > a.x + TILE // 2 else -200.0
                else:
                    a.vx = 0.0
                if not a.fired and a.y > SH // 4:
                    a.fired = True
                    if random.random() < 0.5:
                        self._enemy_fire(a.x + TILE // 2, a.y + TILE)
            elif a.kind == SWOOP_KIND:
                a.home_x += a.side * 40.0 * s
                a.vx = 0.0
                a.vy = 210.0
                a.x = a.home_x + a.side * 150.0 * (1.0 - math.cos(a.age * 0.0022))
                if not a.fired and a.age > 900 and a.y > 0:
                    a.fired = True
                    if random.random() < 0.4:
                        self._enemy_fire(a.x + TILE // 2, a.y + TILE)
            else:
                frame = (a.age // 380) % 2
                if frame != a.frame:
                    a.frame = frame
                    self._use(a.sprite, self.a_slab[frame])
            a.x += a.vx * s
            a.y += a.vy * s

            if a.y > SH or (a.y < -TILE and a.y < was_y) or \
                    a.x < PF_X - 8 or a.x > PF_R - TILE + 8:
                self._end_air(a)
                continue
            if self.state == "play" and self.safe_ms <= 0 and \
                    abs(a.x - self.ship_x) < 22 and abs(a.y - self.ship_y) < 22:
                if a.kind != SLAB_KIND:
                    self._kill_air(a)
                self._lose_ship()
                if a.kind != SLAB_KIND:
                    continue
            self._show(a.sprite, min(max(a.x, PF_X), PF_R - TILE), a.y)

    def _kill_air(self, a):
        if a.kind == RING_KIND:
            self._score(RING_POINTS)
        elif a.kind == DART_KIND:
            self._score(DART_POINTS)
        elif a.kind == SWOOP_KIND:
            self._score(SWOOP_POINTS)
        self._boom(a.x, a.y)
        self._sfx(SYN_HIT, 60, 0.35)
        self._end_air(a)

    def _end_air(self, a):
        self._hide(a.sprite)
        self.air.remove(a)
        self.free_air.append(a.sprite)

    # ----------------------------------------------------------- bullets

    def _enemy_fire(self, x, y):
        if self.state != "play" or not self.free_bullets:
            return
        dx = self.ship_x + TILE // 2 - x
        dy = self.ship_y + TILE // 2 - y
        d = math.sqrt(dx * dx + dy * dy) or 1.0
        speed = BULLET_SPEED + 20 * min(self.area, 8)
        b = Bullet(self.free_bullets.pop(), x - 4, y - 4, dx / d * speed, dy / d * speed)
        self.bullets.append(b)
        self._sfx(SYN_BLIP, 64, 0.06)

    def _move_bullets(self, dt):
        s = dt / 1000.0
        cx = self.ship_x + TILE // 2
        cy = self.ship_y + TILE // 2
        for b in list(self.bullets):
            (x0, y0) = (b.x + 4, b.y + 4)
            b.x += b.vx * s
            b.y += b.vy * s
            b.age += dt
            if b.x < PF_X or b.x > PF_R - 8 or b.y < 0 or b.y > SH - 8:
                self._end_bullet(b)
                continue
            if self.state == "play" and self.safe_ms <= 0:
                # Nearest point of this frame's path to the ship, so a fast
                # bullet cannot hop over it between two frames.
                (vx, vy) = (b.x + 4 - x0, b.y + 4 - y0)
                t = ((cx - x0) * vx + (cy - y0) * vy) / ((vx * vx + vy * vy) or 1.0)
                t = min(1.0, max(0.0, t))
                (px, py) = (x0 + vx * t - cx, y0 + vy * t - cy)
                if px * px + py * py < 100:
                    self._end_bullet(b)
                    self._lose_ship()
                    return         # and losing the ship ended every bullet
            self._use(b.sprite, self.a_bullet[(b.age // 80) % 2])
            self._show(b.sprite, b.x, b.y)

    def _end_bullet(self, b):
        self._hide(b.sprite)
        self.bullets.remove(b)
        self.free_bullets.append(b.sprite)

    # -------------------------------------------------------- explosions

    def _boom(self, x, y, wy=None):
        if self.free_booms:
            sprite = self.free_booms.pop()
        else:
            sprite = self.booms.pop(0).sprite
        self.booms.append(Boom(sprite, x, y, wy))
        self._use(sprite, self.a_boom[0])
        self._show(sprite, x, y)

    def _age_booms(self, dt, top):
        for b in list(self.booms):
            b.age += dt
            frame = b.age // 90
            if frame >= len(self.a_boom):
                self._hide(b.sprite)
                self.booms.remove(b)
                self.free_booms.append(b.sprite)
                continue
            self._use(b.sprite, self.a_boom[frame])
            y = b.y if b.wy is None else top - b.wy - TILE // 2
            self._show(b.sprite, b.x, y)


def run(app):
    app.game = True
    app.hide_task_bar = True       # QUIT lives in the right panel instead
    game = Geoglyph(app)
    app.geoglyph = game
    app.activate_callback = game.activate
    app.deactivate_callback = game.deactivate
    app.present()
