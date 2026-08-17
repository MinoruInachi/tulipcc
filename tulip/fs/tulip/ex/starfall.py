"""STARFALL -- a fixed shooter for tulipcc, in the late-70s arcade style.

All the art and code here is original; the name is ours too, so nothing in it
borrows anyone's trademark.

    run('starfall')

Arrows (or A / D) move, space (or Z) fires, ESC quits. On a touch screen, hold
a finger along the bottom strip to steer -- the ship follows it -- and tap
anywhere above the bunkers to fire.

Everything here is drawn on the BG plane; the sprite layer is not used at all.
There are 55 invaders and Tulip only has 32 sprite handles, so the formation had
to live on the BG in any case, and on a Tab5 moving a single sprite reports the
whole screen as changed, which turns every frame into a full recomposite. Erase
and redraw on the BG costs only the rows it touches.

Drawing without a sprite layer means moving things have to erase themselves, and
that only works because of one invariant: everything that moves here travels
through black. A shot stops at whatever it touches -- bunker, invader, bomb -- so
the space it flies through has already been cleared, and a bomb only ever falls
from the lowest invader in its column. So an erase can never take out something
that mattered.

The invaders make use of that twice over: each one is a single bitmap write of a
cell padded by exactly one step of movement, so redrawing the cell at its new
position also covers where it just was. The cells tile exactly (the column pitch
*is* the cell width), which is also what makes hit detection a single cell lookup
instead of a scan over 55 rectangles.

Only a few invaders move per tick, the way the 1978 machine did it, so the march
speeds up on its own as the formation thins out: a sweep is done once every
survivor has moved, and fewer survivors make for shorter sweeps. The march sound
is one note per sweep, so it speeds up with them.

The pace comes from tulip.ticks_ms(), not from the frame count, because the frame
rate here is not a constant: a Tab5 recomposites the rows that changed, so a
frame that touches one band of the screen runs several times faster than one
touching the whole height. That is also why the layout is built up from the floor
and kept vertically compact -- the ship and the invader that just moved are the
two ends of the span that gets recomposited. Shots step through their travel in
sub-steps no larger than the smallest target, so a slow frame cannot tunnel one
through a bunker either.
"""

import random

import amy
import tulip

# ------------------------------------------------------------------ geometry

(SW, SH) = tulip.screen_size()

# The art below is drawn at arcade resolution and blown up by S, so every size
# on screen is a whole number of art pixels.
S = 4 if SW >= 1200 else 3

ART_COLS, ART_ROWS = 12, 8                # every invader shares a 12x8 art cell
ART_W, ART_H = ART_COLS * S, ART_ROWS * S
STEP = 2 * S                              # how far an invader moves, per move
PAD = STEP                                # black margin drawn around the art

# The padded cell is what gets written to the BG. PAD == STEP means the new cell
# always covers the old art, and PITCH_X == CELL_W means neighbouring cells
# touch without overlapping, so a moving invader can never clip the one next to
# it. Don't change one of these without the others.
CELL_W, CELL_H = ART_W + 2 * PAD, ART_H
COLS, ROWS = 11, 5
PITCH_X, PITCH_Y = CELL_W, ART_H + 2 * S
FORM_W = (COLS - 1) * PITCH_X + ART_W
MARGIN = 6 * S                            # closest anything gets to an edge
DROP = 6 * S                              # how far they drop when they reverse

# The arcade screen was barely wider than the formation, so it only took a
# couple of dozen moves to reach the edge and drop. A 16:9 screen is far wider
# than 11 invaders, so the formation reverses at its own bounds rather than at
# the screen edge -- otherwise the march would drift for a minute a row.
TRAVEL = 16 * STEP
BOUND_L = max(MARGIN, (SW - FORM_W) // 2 - TRAVEL // 2)
BOUND_R = min(SW - MARGIN, (SW + FORM_W) // 2 + TRAVEL // 2)

CHUNK = 2 * S                             # one destructible piece of a bunker
BUNKER_COLS, BUNKER_ROWS = 11, 8
BUNKER_W, BUNKER_H = BUNKER_COLS * CHUNK, BUNKER_ROWS * CHUNK
BUNKERS = 4

# The bunkers belong under the ground the invaders actually cover, so they are
# laid out across the march's own span rather than across the screen: spread over
# the whole width they would sit outside it, with gaps twice their own size. The
# arcade spacing is about one bunker's width between them, and a little more than
# that at each end.
BUNKER_GAP = BUNKER_W * 7 // 8
BUNKER_PITCH = BUNKER_W + BUNKER_GAP
BUNKER_SPAN = (BUNKERS - 1) * BUNKER_PITCH + BUNKER_W
BUNKER_X = [BOUND_L + (BOUND_R - BOUND_L - BUNKER_SPAN) // 2 + i * BUNKER_PITCH
            for i in range(BUNKERS)]

# The ship and the mystery ship keep to the same span. Letting the ship run out
# past the ends of the march would hand it a corner no bomb could ever reach.
PLAY_L, PLAY_R = BOUND_L, BOUND_R

# Built from the floor up, and deliberately shallow: see the note above about
# what a frame costs.
GROUND_Y = SH - 12 * S
PLAYER_Y = GROUND_Y - ART_H - 2 * S
BUNKER_Y = PLAYER_Y - 6 * S - BUNKER_H
FORM_TOP = BUNKER_Y - 16 * S - ART_H - (ROWS - 1) * PITCH_Y
HUD_Y = FORM_TOP - 12 * S                 # the score sits just above them
HUD_H = 12 * S
HUD_BASE = FORM_TOP - 3 * S               # bg_str y is the text baseline
UFO_Y = FORM_TOP - 22 * S

# Speeds in screen pixels per second, so the game plays the same whatever the
# frame rate turns out to be.
SHIP_SPEED = 65 * S
SHOT_SPEED = 150 * S
BOMB_SPEED = 55 * S
UFO_SPEED = 25 * S
SUBSTEP = CHUNK                           # never step past the smallest target

MARCH_HZ = 60.0                           # invader moves per second, arcade rate
MARCH_BURST = 8                           # cap the moves one frame may catch up
MAX_BOMBS = 3
BOMB_HZ = 0.45                            # bombs a second, before the wave bonus
SPLAT_MS = 250                            # how long an invader's debris shows
DYING_MS = 1400
CLEAR_MS = 2000
UFO_EVERY_MS = 20000
BOMB_WIGGLE_MS = 120
STARS = 70                                # static, in the sky above the action

HUD_FONT, BANNER_FONT = 10, 17

# ------------------------------------------------------------------- palette


def _pal(r, g, b):
    """Pack 8-bit r, g, b into a Tulip RGB332 palette index."""
    return (r & 0xE0) | ((g & 0xE0) >> 3) | ((b & 0xC0) >> 6)


BLACK = 0
WHITE = _pal(255, 255, 255)
GREEN = _pal(0, 255, 64)
CYAN = _pal(0, 255, 255)
YELLOW = _pal(255, 255, 0)
RED = _pal(255, 32, 32)
GREY = _pal(160, 160, 160)

# ----------------------------------------------------------------------- art
# '.' is background, which on the BG plane means black.

SQUID = (
    ("....####....",
     "...######...",
     "..########..",
     "..##.##.##..",
     "..########..",
     "...#.##.#...",
     "..#.#..#.#..",
     "...#....#..."),
    ("....####....",
     "...######...",
     "..########..",
     "..##.##.##..",
     "..########..",
     "....#..#....",
     "...#.##.#...",
     "..#.#..#.#.."),
)

CRAB = (
    ("..#......#..",
     "...#....#...",
     "..########..",
     ".##.####.##.",
     "############",
     "#.########.#",
     "#.#......#.#",
     "...##..##..."),
    ("..#......#..",
     "#..#....#..#",
     "#.########.#",
     "###.####.###",
     "############",
     ".##########.",
     "..#......#..",
     ".#........#."),
)

OCTOPUS = (
    ("...######...",
     ".##########.",
     "############",
     "###..##..###",
     "############",
     "..###..###..",
     ".##..##..##.",
     "..##....##.."),
    ("...######...",
     ".##########.",
     "############",
     "###..##..###",
     "############",
     "..###..###..",
     ".##.####.##.",
     "#.#......#.#"),
)

SPLAT = (
    "............",
    "..#..#..#...",
    "...#.#.#....",
    "#...###...#.",
    "..###.###...",
    "#...###...#.",
    "...#.#.#....",
    "..#..#..#...",
)

SHIP = (
    "......#......",
    ".....###.....",
    ".....###.....",
    ".###########.",
    "#############",
    "#############",
    "#############",
    "##.#######.##",
)

SHIP_BOOM = (
    "..#...#.#....",
    "#..#.#...#..#",
    ".#.#..#.#.#..",
    "..####.###...",
    "#.##.#.#.##.#",
    ".#.####.#.#..",
    "#..#.#..#..#.",
    ".#..#..#..#..",
)

SHOT = ("#", "#", "#", "#", "#")

BOMB = (
    (".#.", "#..", ".#.", "..#", ".#.", "#.."),
    (".#.", "..#", ".#.", "#..", ".#.", "..#"),
)

UFO = (
    "....########....",
    "..############..",
    ".##############.",
    "###.##.##.##.###",
    "################",
    "...####..####...",
    "....##....##....",
)

BUNKER = (
    "...#####...",
    "..#######..",
    ".#########.",
    "###########",
    "###########",
    "###########",
    "####...####",
    "###.....###",
)

# Row 0 is the 30-point squid, rows 1-2 the 20-point crab, rows 3-4 the
# 10-point octopus, same as the arcade.
KINDS = (SQUID, CRAB, OCTOPUS)
ROW_KIND = (0, 1, 1, 2, 2)
KIND_COLOR = (CYAN, GREEN, YELLOW)
KIND_POINTS = (30, 20, 10)
UFO_POINTS = 100

# One oscillator per sound, set up once and then retriggered with note + vel.
OSC_MARCH, OSC_SHOOT, OSC_HIT, OSC_BOOM, OSC_UFO = 60, 61, 62, 63, 64
OSCS = (OSC_MARCH, OSC_SHOOT, OSC_HIT, OSC_BOOM, OSC_UFO)
MARCH_NOTES = (36, 34, 32, 31)

# ------------------------------------------------------------------ bitmaps


def _bitmap(rows, ink, pad=0, scale=S):
    """Blow ASCII art up into an RGB332 bitmap, scale x scale per art pixel.

    The lines are kept as bytes rather than bytearrays: MicroPython will repeat
    bytes with *, but not a bytearray.
    """
    edge = bytes(pad)
    out = bytearray()
    for row in rows:
        line = edge
        for char in row:
            line += bytes([BLACK if char == "." else ink]) * scale
        line += edge
        out += line * scale
    return bytes(out)


def _art_size(rows, scale=S):
    return (len(rows[0]) * scale, len(rows) * scale)


# ------------------------------------------------------------------ the game


class Starfall:
    def __init__(self, app):
        self.app = app
        self.hi_score = 0
        self.state = "attract"
        self.touch_x = None                # steering finger, None if lifted
        self.touch_fire = False
        self.quitting = False
        self.now = 0

    # ------------------------------------------------------------ lifecycle

    def activate(self, app):
        self.now = tulip.ticks_ms()
        # Nothing here uses sprites, and a sprite left on by whatever ran before
        # would cost every frame from now on.
        tulip.sprite_clear()
        tulip.Sprite.reset()
        self._wipe()
        self._load_art()
        self._start_sounds()
        self.score = 0
        self.lives = 3
        self.wave = 1
        self._new_wave()
        self.state = "attract"
        self._banner("STARFALL", "ARROWS MOVE - SPACE FIRES - ESC QUITS")
        try:
            tulip.touch_callback(self._touch)
        except Exception:
            pass                           # no touch panel here, keys still work
        tulip.frame_callback(self._tick, app)

    def deactivate(self, app):
        tulip.frame_callback()
        try:
            tulip.touch_callback()
        except Exception:
            pass
        for osc in OSCS:
            amy.send(osc=osc, vel=0)

    def _load_art(self):
        (self.ship_w, self.ship_h) = _art_size(SHIP)
        self.ship_art = _bitmap(SHIP, GREEN)
        self.boom_art = _bitmap(SHIP_BOOM, GREEN)
        (self.shot_w, self.shot_h) = _art_size(SHOT)
        self.shot_art = _bitmap(SHOT, WHITE)
        (self.ufo_w, self.ufo_h) = _art_size(UFO)
        self.ufo_art = _bitmap(UFO, RED)
        (self.bomb_w, self.bomb_h) = _art_size(BOMB[0])
        self.bomb_art = [_bitmap(frame, WHITE) for frame in BOMB]
        # One padded cell per invader kind and animation frame, the debris, and
        # an all-black cell to wipe one out with.
        self.cells = [[_bitmap(frame, KIND_COLOR[kind], PAD)
                       for frame in KINDS[kind]] for kind in range(len(KINDS))]
        self.splat_cells = [_bitmap(SPLAT, KIND_COLOR[kind], PAD)
                            for kind in range(len(KINDS))]
        self.empty_cell = bytes(CELL_W * CELL_H)
        self.bunker_art = _bitmap(BUNKER, GREEN, scale=CHUNK)

    def _start_sounds(self):
        # Every envelope has to reach zero while the note is still held. The last
        # pair of a bp0 is the release and only runs on note-off, so a non-zero
        # level before it is a sustain -- and these are all fire-and-forget, with
        # no note-off to end them, so a sustain means a sound that never stops.
        amy.send(osc=OSC_MARCH, wave=amy.SAW_DOWN, bp0="0,1,140,0,0,0", vel=0)
        amy.send(osc=OSC_SHOOT, wave=amy.SAW_DOWN, bp0="0,1,110,0,0,0", vel=0)
        amy.send(osc=OSC_HIT, wave=amy.NOISE, bp0="0,1,150,0,0,0", vel=0)
        amy.send(osc=OSC_BOOM, wave=amy.NOISE, bp0="0,1,700,0,0,0", vel=0)
        amy.send(osc=OSC_UFO, wave=amy.PULSE, bp0="0,1,260,0,0,0", vel=0)

    # ---------------------------------------------------------- wave setup

    def _new_wave(self):
        # Each wave starts a row or two lower, the way the arcade did it.
        self.form_x = (SW - FORM_W) // 2
        self.form_y = FORM_TOP + min(self.wave - 1, 3) * DROP
        self.alive = [bytearray(b"\x01" * COLS) for _ in range(ROWS)]
        self.live_count = ROWS * COLS
        self.ix = [[self.form_x + col * PITCH_X for col in range(COLS)]
                   for _ in range(ROWS)]
        self.iy = [self.form_y + row * PITCH_Y for row in range(ROWS)]
        self.flip = [bytearray(COLS) for _ in range(ROWS)]
        self.dx = STEP
        self.edge_hit = False
        self.march_note = 0
        self.march_credit = 0.0
        self.splats = []

        self.ship_x = float((SW - self.ship_w) // 2)
        self.ship_at = None                # where the ship is currently drawn
        self.boom_at = None
        self.shot = None                   # [x, y] of the player's one shot
        self.shot_at = None
        self.bombs = []                    # [x, y, drawn_y, frame, wiggle_ms]
        self.ufo = None                    # [x, direction, drawn_x]
        self.ufo_at = self.now + UFO_EVERY_MS
        self.timer = 0

        self._wipe()
        self._draw_stars()
        tulip.bg_rect(0, GROUND_Y, SW, S, GREEN, 1)
        self._reset_bunkers()
        self._begin_sweep()
        self._draw_formation()
        self._draw_hud()
        self._draw_ship()

    # ------------------------------------------------------------- task bar

    def _task_bar_buttons(self):
        for name in ("quit_button", "alttab_button"):
            button = getattr(self.app, name, None)
            if button is not None:
                yield button

    def _task_bar_box(self):
        """The corner the quit and alt-tab buttons sit in, or None if there is
        no task bar.

        Asked of LVGL rather than assumed: the buttons are 56 pixels square only
        on a touch board (_style_task_bar_button() in ui.py), and the alt-tab one
        is not created at all when Starfall is the only app running.
        """
        boxes = [(b.get_x(), b.get_y(), b.get_width(), b.get_height())
                 for b in self._task_bar_buttons()]
        if not boxes:
            return None
        return (min([x for (x, y, w, h) in boxes]),
                min([y for (x, y, w, h) in boxes]),
                max([x + w for (x, y, w, h) in boxes]),
                max([y + h for (x, y, w, h) in boxes]))

    def _wipe(self):
        """Black the whole screen out, and ask for the task bar back.

        LVGL paints those two buttons into the same framebuffer the BG plane
        draws into, and it only repaints what it believes has changed -- so a
        bg_clear() takes them with it and nothing ever puts them back. That is
        what made them flash into view under a finger and then disappear again:
        the touch invalidated them, and the next wave wiped them out once more.
        editor.py does the same after it clears the text framebuffer.
        """
        tulip.bg_clear(BLACK)
        for button in self._task_bar_buttons():
            button.invalidate()

    def _draw_stars(self):
        """Fill the sky above the mystery ship's lane. Drawn once a wave, and
        nothing ever moves through it, so it costs no frames at all."""
        bar = self._task_bar_box()
        size = S // 2 or 1
        for _ in range(STARS):
            x = random.randrange(2 * S, SW - 2 * S)
            y = random.randrange(2 * S, UFO_Y - 2 * S)
            # The sky reaches over the task bar, and a star up there would be a
            # speck left sitting on a button until LVGL next repainted it.
            if bar is not None and (x < bar[2] and x + size > bar[0]
                                    and y < bar[3] and y + size > bar[1]):
                continue
            shade = random.choice((GREY, WHITE, _pal(96, 96, 128)))
            tulip.bg_rect(x, y, size, size, shade, 1)

    def _reset_bunkers(self):
        self.bunker_x = list(BUNKER_X)
        self.bunkers = []
        for x in self.bunker_x:
            self.bunkers.append([bytearray([0 if char == "." else 1
                                            for char in row])
                                 for row in BUNKER])
            tulip.bg_bitmap(x, BUNKER_Y, BUNKER_W, BUNKER_H, self.bunker_art)

    # -------------------------------------------------------------- drawing

    def _draw_invader(self, row, col):
        tulip.bg_bitmap(self.ix[row][col] - PAD, self.iy[row], CELL_W, CELL_H,
                        self.cells[ROW_KIND[row]][self.flip[row][col]])

    def _draw_formation(self):
        for row in range(ROWS):
            live = self.alive[row]
            for col in range(COLS):
                if live[col]:
                    self._draw_invader(row, col)

    def _live_cells(self):
        """Where everyone still alive is drawn, as (x, y) of the padded cell.

        A dead invader stops moving, so its ix is left wherever it died, and a
        row that has been cleared out stays in iy for the rest of the wave.
        Anything that works off the whole formation's outline rather than this
        list ends up acting for invaders that are not there any more.
        """
        out = []
        for row in range(ROWS):
            live = self.alive[row]
            y = self.iy[row]
            for col in range(COLS):
                if live[col]:
                    out.append((self.ix[row][col] - PAD, y))
        return out

    def _erase_formation(self, cells):
        # One rectangle per live invader, over the cell it is drawn in, used
        # when the formation drops a row. A single box around the lot would be
        # one call instead of up to 55, but it would also black out bunker
        # chunks nobody has reached -- and the model would not know.
        for (x, y) in cells:
            tulip.bg_rect(x, y, CELL_W, CELL_H, BLACK, 1)

    def _draw_hud(self):
        # Stop short of the task bar buttons in the top right corner.
        tulip.bg_rect(0, HUD_Y, SW - 60 * S, HUD_H, BLACK, 1)
        tulip.bg_str("SCORE %04d   HI %04d   LIVES %d   WAVE %d"
                     % (self.score, max(self.hi_score, self.score),
                        self.lives, self.wave),
                     4 * S, HUD_BASE, WHITE, HUD_FONT)

    def _banner(self, line1, line2=None):
        y = SH // 2 - 6 * S
        tulip.bg_rect(0, y - 8 * S, SW, 24 * S, BLACK, 1)
        tulip.bg_str(line1, 0, y, WHITE, BANNER_FONT, SW, 0)
        if line2 is not None:
            tulip.bg_str(line2, 0, y + 10 * S, GREY, HUD_FONT, SW, 0)

    def _clear_banner(self):
        y = SH // 2 - 6 * S
        tulip.bg_rect(0, y - 8 * S, SW, 24 * S, BLACK, 1)
        self._draw_formation()
        self._redraw_bunkers()

    def _redraw_bunkers(self):
        for index, chunks in enumerate(self.bunkers):
            x0 = self.bunker_x[index]
            for row in range(BUNKER_ROWS):
                for col in range(BUNKER_COLS):
                    if chunks[row][col]:
                        tulip.bg_rect(x0 + col * CHUNK, BUNKER_Y + row * CHUNK,
                                      CHUNK, CHUNK, GREEN, 1)

    def _draw_ship(self):
        x = int(self.ship_x)
        if self.ship_at == x:
            return
        if self.ship_at is not None:
            tulip.bg_rect(self.ship_at, PLAYER_Y, self.ship_w, self.ship_h,
                          BLACK, 1)
        tulip.bg_bitmap(x, PLAYER_Y, self.ship_w, self.ship_h, self.ship_art)
        self.ship_at = x

    def _erase_ship(self):
        if self.ship_at is not None:
            tulip.bg_rect(self.ship_at, PLAYER_Y, self.ship_w, self.ship_h,
                          BLACK, 1)
            self.ship_at = None

    # ---------------------------------------------------------------- input

    def _touch(self, up):
        try:
            point = tulip.touch()
        except Exception:
            return
        (x, y) = (point[0], point[1])
        if up:
            self.touch_x = None
            return
        if y > BUNKER_Y + BUNKER_H:
            self.touch_x = x               # steering strip along the bottom
        elif y > HUD_Y:
            self.touch_fire = True         # anywhere above the bunkers fires

    def _read_keys(self):
        """(left, right, fire, quit) from the keyboard, whichever one it is."""
        if not hasattr(tulip, "keys"):
            return (False, False, False, False)
        left = right = fire = quit = False
        for code in tulip.keys()[1:]:
            if code == 80 or code == 4:            # left arrow, A
                left = True
            elif code == 79 or code == 7:          # right arrow, D
                right = True
            elif code == 44 or code == 29:         # space, Z
                fire = True
            elif code == 41:                       # ESC
                quit = True
        return (left, right, fire, quit)

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
            print("starfall: stopped on an error")
            sys.print_exception(e)

    def _frame(self):
        now = tulip.ticks_ms()
        dt = now - self.now
        # A long gap means the app was away or something else held the CPU;
        # treat it as one ordinary frame rather than teleporting everything.
        if dt < 1 or dt > 250:
            dt = 25
        self.now = now

        (left, right, fire, quit) = self._read_keys()
        if quit:
            self.quitting = True
            tulip.frame_callback()
            tulip.defer(lambda app: app.quit(), self.app, 20)
            return

        fire = fire or self.touch_fire
        self.touch_fire = False

        if self.state == "attract":
            if fire:
                self._clear_banner()
                self.state = "play"
            return

        if self.state == "over":
            if fire:
                self.score = 0
                self.lives = 3
                self.wave = 1
                self._new_wave()
                self.state = "play"
            return

        self.timer -= dt
        if self.state == "clear":
            if self.timer <= 0:
                self.wave += 1
                self._new_wave()
                self.state = "play"
            return

        if self.state == "dying":
            if self.timer <= 0:
                amy.send(osc=OSC_BOOM, vel=0)   # the longest sound: make sure
                if self.boom_at is not None:
                    tulip.bg_rect(self.boom_at, PLAYER_Y, self.ship_w,
                                  self.ship_h, BLACK, 1)
                    self.boom_at = None
                if self.lives <= 0:
                    self.hi_score = max(self.hi_score, self.score)
                    self._banner("GAME OVER", "SPACE PLAYS AGAIN")
                    self.state = "over"
                else:
                    self.ship_x = float((SW - self.ship_w) // 2)
                    self._draw_ship()
                    self.state = "play"
            return

        self._move_ship(dt, left, right)
        if fire:
            self._fire()
        self._march(dt)
        self._age_splats(dt)
        self._move_shot(dt)
        self._move_bombs(dt)
        self._drop_bomb(dt)
        self._move_ufo(dt)

    def _move_ship(self, dt, left, right):
        reach = SHIP_SPEED * dt / 1000.0
        if self.touch_x is not None:
            target = self.touch_x - self.ship_w // 2
            if abs(target - self.ship_x) > reach:
                self.ship_x += reach if target > self.ship_x else -reach
            else:
                self.ship_x = target
        elif left and not right:
            self.ship_x -= reach
        elif right and not left:
            self.ship_x += reach
        else:
            return
        self.ship_x = max(PLAY_L, min(self.ship_x, PLAY_R - self.ship_w))
        self._draw_ship()

    def _fire(self):
        if self.shot is not None:
            return                         # one shot in the air at a time
        self.shot = [int(self.ship_x) + self.ship_w // 2 - self.shot_w // 2,
                     float(PLAYER_Y - self.shot_h)]
        self.shot_at = None
        self._draw_shot()
        amy.send(osc=OSC_SHOOT, note=76, vel=0.3)

    # ------------------------------------------------------------ the march

    def _march(self, dt):
        self.march_credit += dt * MARCH_HZ * (1.0 + (self.wave - 1) * 0.25) / 1000.0
        moves = int(self.march_credit)
        if moves > MARCH_BURST:
            moves = MARCH_BURST            # after a stall, don't teleport them
            self.march_credit = 0.0
        else:
            self.march_credit -= moves
        for _ in range(moves):
            if self.cursor >= len(self.order):
                self._end_sweep()
                if self.state != "play":
                    return
                continue
            (row, col) = self.order[self.cursor]
            self.cursor += 1
            if not self.alive[row][col]:
                continue
            x = self.ix[row][col] + self.dx
            if x < BOUND_L or x + ART_W > BOUND_R:
                self.edge_hit = True
            self.ix[row][col] = x
            self.flip[row][col] ^= 1
            self._draw_invader(row, col)

    def _end_sweep(self):
        # Everyone left has moved once, so the cell grid the hit test works off
        # has moved with them.
        self.form_x += self.dx
        if self.edge_hit:
            self.edge_hit = False
            self.dx = -self.dx
            self._descend()
            if self.state != "play":
                return
        amy.send(osc=OSC_MARCH, note=MARCH_NOTES[self.march_note], vel=0.4)
        self.march_note = (self.march_note + 1) % len(MARCH_NOTES)
        self._begin_sweep()

    def _begin_sweep(self):
        # Bottom row first and in the direction of travel, so the ripple runs
        # the way the formation is going.
        cols = range(COLS) if self.dx > 0 else range(COLS - 1, -1, -1)
        self.order = [(row, col) for row in range(ROWS - 1, -1, -1)
                      for col in cols if self.alive[row][col]]
        self.cursor = 0

    def _descend(self):
        cells = self._live_cells()
        self._erase_formation(cells)
        for row in range(ROWS):
            self.iy[row] += DROP
        cells = [(x, y + DROP) for (x, y) in cells]
        self._eat_bunkers(cells)
        self._draw_formation()
        # The lowest invader still flying, not the lowest row of the formation:
        # once a row has been cleared out it must not land on anybody.
        if cells and max([y for (x, y) in cells]) + ART_H >= PLAYER_Y:
            self._lose_ship(invaded=True)

    def _eat_bunkers(self, cells):
        """Whatever a live invader has come down on is gone for good.

        Cleared on screen as well as in the model: the rectangles that erase
        the formation before a drop cover where the invaders were, not where
        they land, so these have to clear themselves. A chunk the cell only
        half covers goes too -- the invader is about to be drawn over that
        half, so leaving it in the model would block shots through a hole that
        is plainly there on screen.
        """
        for (x, y) in cells:
            if y + CELL_H <= BUNKER_Y or y >= BUNKER_Y + BUNKER_H:
                continue
            r0 = max(0, (y - BUNKER_Y) // CHUNK)
            r1 = min(BUNKER_ROWS, -((BUNKER_Y - y - CELL_H) // CHUNK))
            for index, chunks in enumerate(self.bunkers):
                x0 = self.bunker_x[index]
                if x + CELL_W <= x0 or x >= x0 + BUNKER_W:
                    continue
                c0 = max(0, (x - x0) // CHUNK)
                c1 = min(BUNKER_COLS, -((x0 - x - CELL_W) // CHUNK))
                eaten = False
                for row in range(r0, r1):
                    line = chunks[row]
                    for col in range(c0, c1):
                        if line[col]:
                            eaten = True
                            line[col] = 0
                if eaten:
                    tulip.bg_rect(x0 + c0 * CHUNK, BUNKER_Y + r0 * CHUNK,
                                  (c1 - c0) * CHUNK, (r1 - r0) * CHUNK,
                                  BLACK, 1)

    def _age_splats(self, dt):
        if not self.splats:
            return
        left = []
        for splat in self.splats:
            splat[2] -= dt
            if splat[2] > 0:
                left.append(splat)
            else:
                tulip.bg_bitmap(splat[0], splat[1], CELL_W, CELL_H,
                                self.empty_cell)
        self.splats = left

    # ------------------------------------------------------------- the shot

    def _draw_shot(self):
        y = int(self.shot[1])
        if self.shot_at == y:
            return
        if self.shot_at is not None:
            tulip.bg_rect(self.shot[0], self.shot_at, self.shot_w, self.shot_h,
                          BLACK, 1)
        tulip.bg_bitmap(self.shot[0], y, self.shot_w, self.shot_h,
                        self.shot_art)
        self.shot_at = y

    def _move_shot(self, dt):
        if self.shot is None:
            return
        # Walk the travel in sub-steps: a slow frame can be a long way, and a
        # single jump would tunnel straight through a bunker or an invader.
        togo = SHOT_SPEED * dt / 1000.0
        while togo > 0 and self.shot is not None:
            step = SUBSTEP if togo > SUBSTEP else togo
            togo -= step
            self.shot[1] -= step
            (x, y) = (self.shot[0] + self.shot_w // 2, int(self.shot[1]))
            if y < UFO_Y - self.shot_h:
                self._end_shot()
                return
            if self._shot_hits(x, y):
                return
        self._draw_shot()

    def _shot_hits(self, x, y):
        """Everything the player's shot can run into, nearest to it first."""
        for bomb in self.bombs:
            if abs(bomb[0] + self.bomb_w // 2 - x) < self.bomb_w and \
                    abs(bomb[1] - y) < self.bomb_h:
                self._end_bomb(bomb)
                self._end_shot()
                return True
        if self._hit_bunker(x, y):
            self._end_shot()
            return True
        if self._hit_invader(x, y):
            self._end_shot()
            return True
        if self.ufo is not None and UFO_Y <= y < UFO_Y + self.ufo_h and \
                self.ufo[0] <= x <= self.ufo[0] + self.ufo_w:
            self.score += UFO_POINTS
            self._end_ufo()
            self._draw_hud()
            amy.send(osc=OSC_UFO, note=64, vel=0.4)
            self._end_shot()
            return True
        return False

    def _end_shot(self):
        if self.shot is not None and self.shot_at is not None:
            tulip.bg_rect(self.shot[0], self.shot_at, self.shot_w, self.shot_h,
                          BLACK, 1)
        self.shot = None
        self.shot_at = None

    def _hit_invader(self, x, y):
        """A point against the formation: at most one cell can hold it."""
        for row in range(ROWS - 1, -1, -1):
            top = self.iy[row]
            if not (top <= y < top + ART_H):
                continue
            col = (x - (self.form_x - PAD)) // PITCH_X
            if col < 0 or col >= COLS or not self.alive[row][col]:
                return False
            left = self.ix[row][col]
            if not (left <= x < left + ART_W):
                return False
            self._kill_invader(row, col)
            return True
        return False

    def _kill_invader(self, row, col):
        self.alive[row][col] = 0
        self.live_count -= 1
        self.score += KIND_POINTS[ROW_KIND[row]]
        self._draw_hud()
        amy.send(osc=OSC_HIT, note=48, vel=0.4)
        x = self.ix[row][col] - PAD
        tulip.bg_bitmap(x, self.iy[row], CELL_W, CELL_H,
                        self.splat_cells[ROW_KIND[row]])
        self.splats.append([x, self.iy[row], SPLAT_MS])
        # A dead invader is dropped from the sweep, so the survivors speed up.
        if self.live_count <= 0:
            self._wave_cleared()

    def _wave_cleared(self):
        self._end_shot()
        for bomb in list(self.bombs):
            self._end_bomb(bomb)
        self._end_ufo()
        self.hi_score = max(self.hi_score, self.score)
        self._banner("WAVE %d CLEARED" % self.wave)
        self.timer = CLEAR_MS
        self.state = "clear"

    # ------------------------------------------------------------ the bombs

    def _drop_bomb(self, dt):
        if len(self.bombs) >= MAX_BOMBS or self.live_count <= 0:
            return
        rate = BOMB_HZ + 0.2 * (self.wave - 1)
        if random.random() > rate * dt / 1000.0:
            return
        col = random.randrange(COLS)
        # Only the lowest invader in a column drops, so a bomb never has to
        # cross one of its own on the way down -- and so it always falls
        # through black, which is what lets it erase itself as it goes.
        for row in range(ROWS - 1, -1, -1):
            if self.alive[row][col]:
                bomb = [self.ix[row][col] + ART_W // 2 - self.bomb_w // 2,
                        float(self.iy[row] + ART_H), None, 0, BOMB_WIGGLE_MS]
                self.bombs.append(bomb)
                self._draw_bomb(bomb)
                return

    def _draw_bomb(self, bomb):
        y = int(bomb[1])
        if bomb[2] == y:
            return
        if bomb[2] is not None:
            tulip.bg_rect(bomb[0], bomb[2], self.bomb_w, self.bomb_h, BLACK, 1)
        tulip.bg_bitmap(bomb[0], y, self.bomb_w, self.bomb_h,
                        self.bomb_art[bomb[3]])
        bomb[2] = y

    def _move_bombs(self, dt):
        for bomb in list(self.bombs):
            togo = BOMB_SPEED * dt / 1000.0
            gone = False
            while togo > 0:
                step = SUBSTEP if togo > SUBSTEP else togo
                togo -= step
                bomb[1] += step
                x = bomb[0] + self.bomb_w // 2
                y = int(bomb[1]) + self.bomb_h
                if y > GROUND_Y:
                    self._end_bomb(bomb)
                    gone = True
                    break
                if y >= PLAYER_Y and self.ship_x <= x <= self.ship_x + self.ship_w:
                    self._end_bomb(bomb)
                    self._lose_ship()
                    return
                if self._hit_bunker(x, y):
                    self._end_bomb(bomb)
                    gone = True
                    break
            if gone:
                continue
            bomb[4] -= dt
            if bomb[4] <= 0:
                # Wiggle. Clear where it is first: dropping the drawn position
                # is what makes the redraw happen even if it has not fallen a
                # whole pixel, and the redraw has nothing to erase after that.
                bomb[4] = BOMB_WIGGLE_MS
                bomb[3] ^= 1
                if bomb[2] is not None:
                    tulip.bg_rect(bomb[0], bomb[2], self.bomb_w, self.bomb_h,
                                  BLACK, 1)
                    bomb[2] = None
            self._draw_bomb(bomb)

    def _end_bomb(self, bomb):
        if bomb[2] is not None:
            tulip.bg_rect(bomb[0], bomb[2], self.bomb_w, self.bomb_h, BLACK, 1)
            bomb[2] = None
        try:
            self.bombs.remove(bomb)
        except ValueError:
            pass

    # ----------------------------------------------------------- the bunkers

    def _hit_bunker(self, x, y):
        """Erode a bunker at a point, if a chunk is still standing there."""
        if not (BUNKER_Y <= y < BUNKER_Y + BUNKER_H):
            return False
        for index, x0 in enumerate(self.bunker_x):
            if not (x0 <= x < x0 + BUNKER_W):
                continue
            chunks = self.bunkers[index]
            col = (x - x0) // CHUNK
            row = (y - BUNKER_Y) // CHUNK
            if not chunks[row][col]:
                return False               # a hole shot earlier: pass on through
            for (r, c) in ((row, col), (row, col - 1), (row, col + 1),
                           (row + 1, col), (row - 1, col)):
                if 0 <= r < BUNKER_ROWS and 0 <= c < BUNKER_COLS and \
                        chunks[r][c] and (r == row and c == col
                                          or random.random() < 0.6):
                    chunks[r][c] = 0
                    tulip.bg_rect(x0 + c * CHUNK, BUNKER_Y + r * CHUNK,
                                  CHUNK, CHUNK, BLACK, 1)
            return True
        return False

    # --------------------------------------------------------- mystery ship

    def _move_ufo(self, dt):
        if self.ufo is None:
            if self.now >= self.ufo_at and self.live_count > 8:
                direction = 1 if random.random() < 0.5 else -1
                x = PLAY_L if direction > 0 else PLAY_R - self.ufo_w
                self.ufo = [float(x), direction, None]
                self._draw_ufo()
            return
        self.ufo[0] += UFO_SPEED * self.ufo[1] * dt / 1000.0
        if self.ufo[0] < PLAY_L or self.ufo[0] + self.ufo_w > PLAY_R:
            self._end_ufo()
            return
        self._draw_ufo()

    def _draw_ufo(self):
        x = int(self.ufo[0])
        if self.ufo[2] == x:
            return
        if self.ufo[2] is not None:
            tulip.bg_rect(self.ufo[2], UFO_Y, self.ufo_w, self.ufo_h, BLACK, 1)
        tulip.bg_bitmap(x, UFO_Y, self.ufo_w, self.ufo_h, self.ufo_art)
        self.ufo[2] = x

    def _end_ufo(self):
        if self.ufo is not None and self.ufo[2] is not None:
            tulip.bg_rect(self.ufo[2], UFO_Y, self.ufo_w, self.ufo_h, BLACK, 1)
        self.ufo = None
        self.ufo_at = self.now + UFO_EVERY_MS

    # -------------------------------------------------------- losing a ship

    def _lose_ship(self, invaded=False):
        self.lives -= 1
        if invaded:
            self.lives = 0                 # they landed: that is the whole game
        self._draw_hud()
        self.boom_at = int(self.ship_x)
        self._erase_ship()
        tulip.bg_bitmap(self.boom_at, PLAYER_Y, self.ship_w, self.ship_h,
                        self.boom_art)
        amy.send(osc=OSC_BOOM, note=36, vel=0.5)
        self._end_shot()
        for bomb in list(self.bombs):
            self._end_bomb(bomb)
        self.timer = DYING_MS
        self.state = "dying"


def run(app):
    app.game = True
    game = Starfall(app)
    app.starfall = game
    app.activate_callback = game.activate
    app.deactivate_callback = game.deactivate
    app.present()
