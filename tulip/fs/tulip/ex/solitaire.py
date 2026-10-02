"""SOLITAIRE -- Klondike patience for tulipcc, dealt for a fingertip.

    run('solitaire')

Drag a card where it should go and it follows your finger -- the card, and any
run sitting on it, lifts off its pile and travels under the fingertip with a
yellow outline round it. What you aim with is the card, not the fingertip, so
lining it up over a pile is enough; let go anywhere it cannot legally land and
it goes back where it came from.

Or play it in taps: tap a card to pick it up, tap where it should go. Tapping
anywhere in a tableau column counts as that column, so a tap does not have to
land on the card itself, and tapping a picked-up card a second time sends it to
its foundation if it can go, or puts it back down if it cannot. A press only
becomes a drag once the finger has actually travelled, so the two never get in
each other's way.

Tap the stock (top left) to deal, and again when it is empty to turn the waste
back over. The bar across the top has New, Undo, Auto (send everything that can
go to the foundations) and Draw, which switches between turning one card and
turning three. With a keyboard: N, U, A, D, space to deal.

Why it is drawn the way it is
-----------------------------
This is a turn-based game, so nothing is drawn per frame: every pile paints
itself into its own rectangle, and a move repaints only the two or three
rectangles it touched (9 ms each on a Tab5, against 116 ms for the lot). Undo
compares the board with what it restored and repaints the difference. A full
repaint is for a new game and for coming back from another app, and that is
all. That is also why the cards are laid out from a handful of derived
constants rather than literals -- the Tab5's panel is 1280x720 and Tulip CC's
is 1024x600, and the card size falls out of whichever of the two dimensions is
tighter (it is always the height: a tableau pile has to fan).

The faces follow a printed deck. The corner index is the rank with its suit
directly under it; the suit marks on a numbered card sit in the three columns
and the rows every real one uses, and the ones below the middle are upside
down, because that is how they are printed; the ace keeps its single oversized
mark; and a court card carries a framed, double-ended figure rather than a big
letter, which is what makes a picture card read as one. None of it is type:
every Tulip font except the Japanese one is a u8g2 "_tr" face -- ASCII
U+0020..U+007E and nothing else -- so there is no glyph for a spade to set, and
the marks and the figures are all built from triangles and circles. Only the
rank in the corner is text, which is also why the second index is not turned
upside down the way a real one is: a Tulip font cannot set a rotated glyph.

A card under a finger is the one thing that is drawn over and over, and what it
costs is not the problem -- what a panel refresh can catch half-done is. Two
rules keep a drag from flickering. The carried cards are only ever added to the
screen, never taken off it and put back: each step restores just the strip they
are about to leave (_restore_vacated), never the part they still cover, so
there is no instant at which they are missing. And nothing clears a pile: a
column is 474 px tall, and clearing one to redraw ten cards into it leaves it
blank for several refreshes, which is what a drag across the board used to look
like. Restoring a band paints the cards that reach into it straight over
themselves instead (_restore / _paint_*), so the worst that shows is a card
re-inking in place. A step clears 4,700 px against a column's 53,000, and takes
12 ms for one card or 18 ms for a thirteen-card run, against 105 ms for the
whole board.

Nothing is ever taken off the model while it is in the air either: a carried
card is a hole in the drawing, not a hole in the game, so an app switch or a
keypress in the middle of a drag can drop the whole thing with nothing to put
back.

The frame callback is only there for the clock, for Auto's one-move-per-frame
cascade, and for the winning bounce. The bounce needs no erase at all: the
cards leave trails, the way the 1990 Windows one did, so it is a handful of
cards drawn per frame and nothing read back.
"""

import random

import tulip

# A Tab5 is touch-only and twice the size, so its controls are finger-sized and
# its BG plane takes true colour; everywhere else keeps the palette.
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
    # in the plane rather than draw. One step of blue is not a visible change.
    return pal + 1 if pal == _ALPHA else pal


FELT = _c(14, 84, 54)
SLOT = _c(36, 118, 82)
CARD_FACE = _c(250, 248, 242)
CARD_EDGE = _c(130, 130, 134)
RED_INK = _c(198, 36, 36)
BLACK_INK = _c(26, 26, 30)
BACK = _c(30, 62, 134)
BACK_INK = _c(104, 140, 212)
SEL = _c(252, 212, 72)
BAR = _c(8, 46, 30)
BAR_TEXT = _c(222, 236, 224)
BTN = _c(26, 96, 64)
BTN_EDGE = _c(58, 146, 104)
BTN_TEXT = _c(236, 244, 236)

# ------------------------------------------------------------------- geometry

(SW, SH) = tulip.screen_size()

PAD = 8
# The top strip carries this app's own buttons on the left and leaves room for
# UIScreen's task bar (switch and quit) on the right. BAR_RESERVE is how much of
# the right end this app keeps its text out of. The strip's own colour, though,
# can run the whole width wherever LVGL is composited over the BG plane rather
# than drawn into it -- which is the Tab5, and only the Tab5. Stopping the fill
# short there would leave a black seam beside the task bar; doing it anywhere
# else would paint the task bar out.
BAR_H = 56 if _BIG else 34
BAR_RESERVE = 124 if _BIG else 100
BAR_FILL_W = SW if _BIG else SW - BAR_RESERVE
ROW_GAP = 16 if _BIG else 10

# Height decides the card size: the board is a top row, then a tableau that has
# to fan. Size it so a nine-card face-up fan fits at the full fan step, and let
# anything longer squeeze its own fan (see Solitaire._offsets).
_card_h = (SH - BAR_H - 2 * PAD - ROW_GAP) // 4
_card_w = _card_h * 5 // 7
# ...unless seven columns will not fit side by side, which only happens on a
# screen far narrower than any Tulip has.
_w_fit = (SW - 2 * 12 - 6 * 10) // 7
if _card_w > _w_fit:
    _card_w = _w_fit
    _card_h = _card_w * 7 // 5
CARD_W = _card_w
CARD_H = _card_h
RADIUS = max(3, CARD_W // 12)

# Spread the seven columns over the width, but not so far that a pile stops
# looking like it belongs to the one next to it.
GAP = min(CARD_W // 2, (SW - 24 - 7 * CARD_W) // 6)
MARGIN_X = (SW - 7 * CARD_W - 6 * GAP) // 2

TOP_Y = BAR_H + PAD
TAB_Y = TOP_Y + CARD_H + ROW_GAP
TAB_H = SH - PAD - TAB_Y

FAN_UP = CARD_H // 4
FAN_DOWN = max(4, CARD_H // 9)
# How far a finger has to travel before a press turns into a drag. Below it the
# press is still a tap, so tapping twice to move a card goes on working exactly
# as it did and a shaky finger does not lift anything by accident.
DRAG_SLOP = max(6, CARD_W // 8)

STOCK_COL = 0
WASTE_COL = 1
FOUND_COLS = (3, 4, 5, 6)

# Card art.
#
# The corner index is the rank with its suit directly under it, the way a card
# is printed. Stacked it is narrow and tall where the old side-by-side one was
# wide and short, and that one measurement sets most of the rest of the face:
# a fanned card has to show the whole of it (IX_BLOCK against FAN_UP), and the
# suit marks have to start below it.
IX_PAD = max(2, CARD_W // 28)
IX_H = max(12, CARD_H // 11)
IX_W = max(12, CARD_W // 5)
IX_GAP = 1
IX_PIP = max(7, CARD_W // 9)
IX_DEPTH = IX_H + IX_GAP + IX_PIP                   # the index itself
IX_BLOCK = IX_PAD + IX_DEPTH                        # how far down the card it reaches
IX_SIDE = IX_PAD + IX_W + IX_PAD                    # and how far across
# The oversized mark: the ace's, and the one ghosted on an empty foundation.
BIG_PIP = CARD_W // 2
# The waste fans by at least an index's width, or the cards under the top one
# say nothing.
WASTE_FAN = max(CARD_W // 3, IX_SIDE)

# The pip field: the part of the face a real card arranges its suit marks in,
# clear of both corner indices. Its top is pushed down far enough that a mark
# on the first row cannot touch the index above it, and what is left has to
# hold four rows of marks a third of it apart -- that is what caps NUM_PIP.
NUM_PIP = max(8, CARD_W // 6)
PF_X = CARD_W // 8
PF_W = CARD_W - 2 * PF_X
PF_Y = IX_BLOCK + NUM_PIP // 2 + 4
PF_H = CARD_H - 2 * PF_Y
# Left, centre and right: the three columns the marks line up in. The outer two
# sit well out towards the edges, as they are printed -- any closer in and the
# pairs on a nine stop reading as two columns.
PIP_COL = (PF_X + PF_W // 6, CARD_W // 2, CARD_W - PF_X - PF_W // 6)

# A court card's panel is not the pip field: it runs nearly the whole card, as
# a printed one does, and the two indices are knocked out of its corners.
CP_X = CARD_W // 10
CP_Y = CARD_H // 12
CP_W = CARD_W - 2 * CP_X
CP_H = CARD_H - 2 * CP_Y

# Tulip fonts (see tulip/shared/u8g2_fonts.c): 15 is luRS18, 5 is helvB14,
# 18 is lubB24, 17 is logisoso24, 8 is 6x13.
RANK_FONT = 15 if IX_H >= 17 else (5 if IX_H >= 12 else 14)
BANNER_FONT = 18
BTN_FONT = 17 if _BIG else 6
STATUS_FONT = 15 if _BIG else 8

BTN_H = BAR_H - 2 * max(3, BAR_H // 12)
BTN_W = 132 if _BIG else 88
BTN_GAP = 8

# ------------------------------------------------------------------ the cards
# A card is one int: rank * 4 + suit, with rank 0..12 for A..K and suit
# 0..3 for spades, hearts, diamonds, clubs. A pile is a list of those, lowest
# first, so the last entry is the one on top.

RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
SUIT_RED = (False, True, True, False)

# Where the suit marks go on a numbered card, as (column, row): column 0 left,
# 1 centre, 2 right; row in twelfths of the pip field, 0 at the top and 12 at
# the bottom. This is the arrangement every printed deck uses -- the pairs down
# the outside, the odd mark in the middle, and for the nine and ten the extra
# row that keeps the columns even. Keyed by rank index, so 1 is the two.
# Anything past the halfway row is printed upside down, and is drawn that way.
_PIPS = (
    None,                                                       # the ace is special
    ((1, 0), (1, 12)),
    ((1, 0), (1, 6), (1, 12)),
    ((0, 0), (2, 0), (0, 12), (2, 12)),
    ((0, 0), (2, 0), (1, 6), (0, 12), (2, 12)),
    ((0, 0), (2, 0), (0, 6), (2, 6), (0, 12), (2, 12)),
    ((0, 0), (2, 0), (1, 3), (0, 6), (2, 6), (0, 12), (2, 12)),
    ((0, 0), (2, 0), (1, 3), (0, 6), (2, 6), (1, 9), (0, 12), (2, 12)),
    ((0, 0), (2, 0), (0, 4), (2, 4), (1, 6), (0, 8), (2, 8), (0, 12), (2, 12)),
    ((0, 0), (2, 0), (1, 2), (0, 4), (2, 4), (0, 8), (2, 8), (1, 10),
     (0, 12), (2, 12)),
)


def _rank(card):
    return card // 4


def _suit(card):
    return card % 4


# ----------------------------------------------------------------- the pips


def _diamond(cx, cy, s, col):
    hw = max(1, s * 3 // 8)
    hh = max(1, s // 2)
    tulip.bg_triangle(cx, cy - hh, cx - hw, cy, cx + hw, cy, col, 1)
    tulip.bg_triangle(cx, cy + hh, cx - hw, cy, cx + hw, cy, col, 1)
    # The two halves meet on one row, which fillTriangle can leave a hair short.
    tulip.bg_line(cx - hw, cy, cx + hw, cy, col)


def _pip(cx, cy, s, st, col, flip=False):
    """One suit pip about s wide, centred on cx,cy. Built from triangles and
    circles because no Tulip font carries the suit characters.

    `flip` turns it upside down: every vertical offset is taken through d, so
    the marks in the lower half of a card are printed the way a real one has
    them, pointing back at the player who is holding it the other way up."""
    r = max(1, s // 4)
    d = -1 if flip else 1
    if st == 2:                                     # diamond: symmetrical
        _diamond(cx, cy, s, col)
    elif st == 1:                                   # heart: two lobes, a point
        tulip.bg_circle(cx - r, cy - d * r, r, col, 1)
        tulip.bg_circle(cx + r, cy - d * r, r, col, 1)
        tulip.bg_triangle(cx - 2 * r, cy - d * r, cx + 2 * r, cy - d * r,
                          cx, cy + d * 2 * r, col, 1)
    elif st == 0:                                   # spade: the heart, inverted
        tulip.bg_triangle(cx - 2 * r, cy + d * r, cx + 2 * r, cy + d * r,
                          cx, cy - d * 2 * r, col, 1)
        tulip.bg_circle(cx - r, cy + d * r, r, col, 1)
        tulip.bg_circle(cx + r, cy + d * r, r, col, 1)
        tulip.bg_triangle(cx, cy + d * r, cx - r, cy + d * (2 * r + r // 2),
                          cx + r, cy + d * (2 * r + r // 2), col, 1)
    else:                                           # club: three lobes, a stem
        tulip.bg_circle(cx, cy - d * r, r, col, 1)
        tulip.bg_circle(cx - r * 3 // 2, cy + d * (r // 2), r, col, 1)
        tulip.bg_circle(cx + r * 3 // 2, cy + d * (r // 2), r, col, 1)
        tulip.bg_triangle(cx, cy, cx - r, cy + d * 2 * r,
                          cx + r, cy + d * 2 * r, col, 1)


# ---------------------------------------------------------------- the cards


def _span(a, b):
    """A filled rect's (top, height) from two edges given in either order."""
    return (a, b - a) if b > a else (b, a - b)


def _court_half(x, top, w, hh, rk, col, d):
    """One end of a court card's figure, drawn from `top` towards the middle of
    the panel. d is +1 for the end that is the right way up and -1 for the one
    that is not, and every vertical offset goes through Y() -- so the two ends
    come out of the same code, mirrored, the way a real court card is printed.

    Measurements are in twenty-fourths of the half, 0 at the outer edge and 24
    at the middle: headdress, head, collar, robe. At forty-odd pixels an end it
    has to be a bust rather than the half-length figure a printed card carries,
    the head needs a face on it, and the shoulders have to slope over a decent
    run -- flattened into a few pixels they read as a hat brim, not a body."""

    def Y(n):
        return top + d * (hh * n // 24)

    cx = x + w // 2
    band_w = w * 2 // 5
    head_r = max(5, hh * 5 // 24)
    shoulder = w * 3 // 8

    if rk == 12:                                    # King: a pointed crown
        for k in (-1, 0, 1):
            px = cx + k * band_w // 3
            tulip.bg_triangle(px, Y(0), px - band_w // 6, Y(4),
                              px + band_w // 6, Y(4), col, 1)
    elif rk == 11:                                  # Queen: pearls on the band
        for k in (-1, 0, 1):
            tulip.bg_circle(cx + k * band_w // 3, Y(2),
                            max(2, band_w // 8), col, 1)
        tulip.bg_circle(cx - head_r, Y(10), head_r // 2, col, 1)  # her hair
        tulip.bg_circle(cx + head_r, Y(10), head_r // 2, col, 1)
    else:                                           # Jack: a cap and a feather
        tulip.bg_triangle(cx - band_w // 2, Y(4), cx + band_w // 2, Y(4),
                          cx - band_w // 3, Y(0), col, 1)
        tulip.bg_line(cx + band_w // 5, Y(3), cx + band_w * 2 // 3, Y(0),
                      col, 2)
    (by, bh) = _span(Y(4), Y(6))
    tulip.bg_rect(cx - band_w // 2, by, band_w, max(1, bh), col, 1)

    # The face is the colour of the card with the suit colour only round it and
    # for the features; filled solid it would read as a blob.
    tulip.bg_circle(cx, Y(10), head_r, col, 1)
    tulip.bg_circle(cx, Y(10), head_r - max(2, head_r // 6), CARD_FACE, 1)
    eye = max(1, head_r // 5)
    tulip.bg_circle(cx - head_r // 2, Y(9), eye, col, 1)
    tulip.bg_circle(cx + head_r // 2, Y(9), eye, col, 1)
    if rk == 12:                                    # the King wears a beard
        tulip.bg_triangle(cx - head_r + 1, Y(11), cx + head_r - 1, Y(11),
                          cx, Y(16), col, 1)
    else:
        tulip.bg_line(cx - head_r // 3, Y(12), cx + head_r // 3, Y(12), col)

    # Collar, then shoulders, then the V the collar opens into -- which is what
    # keeps the robe from reading as one solid wedge.
    (cy, ch) = _span(Y(15), Y(17))
    tulip.bg_rect(cx - head_r, cy, head_r * 2, max(1, ch), col, 1)
    tulip.bg_triangle(cx - head_r, Y(17), cx + head_r, Y(17),
                      cx - shoulder, Y(23), col, 1)
    tulip.bg_triangle(cx + head_r, Y(17), cx - shoulder, Y(23),
                      cx + shoulder, Y(23), col, 1)
    tulip.bg_triangle(cx - head_r * 2 // 3, Y(17), cx + head_r * 2 // 3, Y(17),
                      cx, Y(22), CARD_FACE, 1)


def _court(x, y, w, h, rk, st, col):
    """A court card's middle: a framed, double-ended figure with the suit
    beside each end, which is what makes a picture card read as one at a
    glance without any of the rank being set in type."""
    tulip.bg_roundrect(x, y, w, h, RADIUS, col)
    hh = h // 2
    inset = max(3, h // 24)
    _court_half(x, y + inset, w, hh - inset - 1, rk, col, 1)
    _court_half(x, y + h - inset - 1, w, hh - inset - 1, rk, col, -1)
    # The rule between the two ends goes on last, as a gap with a line in it:
    # the robes run right up to it, and drawn first it would be painted over.
    tulip.bg_rect(x + 1, y + hh - 1, w - 2, 3, CARD_FACE, 1)
    tulip.bg_line(x + 1, y + hh, x + w - 2, y + hh, col)
    s = NUM_PIP * 3 // 4
    _pip(x + w - s, y + s, s, st, col)
    _pip(x + s, y + h - s, s, st, col, True)


def _slot(x, y):
    """An empty place for a pile."""
    tulip.bg_roundrect(x, y, CARD_W, CARD_H, RADIUS, SLOT)
    tulip.bg_roundrect(x + 1, y + 1, CARD_W - 2, CARD_H - 2, RADIUS, SLOT)


def _outline(x, y, w, h):
    """The picked-up marker: three rings so it reads at arm's length."""
    for i in range(3):
        tulip.bg_roundrect(x + i, y + i, w - 2 * i, h - 2 * i,
                           max(1, RADIUS - i), SEL)


def _index(x, y, rk, st, col, clear=False):
    """A corner index: the rank, with its suit directly under it.

    A printed card turns the second one upside down. No Tulip font can set a
    rotated glyph, so this one repeats the rank the right way up -- which is
    also what keeps it readable in a fanned pile, where the second index is the
    one that shows.

    `clear` knocks a card-coloured hole out first, for the court cards, whose
    panel runs underneath."""
    if clear:
        tulip.bg_rect(x - 1, y - 1, IX_W + 2, IX_DEPTH + 2, CARD_FACE, 1)
    tulip.bg_str(RANKS[rk], x, y, col, RANK_FONT, IX_W, IX_H)
    _pip(x + IX_W // 2, y + IX_H + IX_GAP + IX_PIP // 2, IX_PIP, st, col)


def _draw_face(x, y, card, vis=None):
    """A card face. `vis` is how many of its top pixels will still be visible
    once the card above it is drawn -- everything below that is skipped rather
    than drawn and immediately covered."""
    st = _suit(card)
    rk = _rank(card)
    col = RED_INK if SUIT_RED[st] else BLACK_INK
    full = vis is None or vis >= CARD_H
    court = rk >= 10
    tulip.bg_roundrect(x, y, CARD_W, CARD_H, RADIUS, CARD_FACE, 1)
    tulip.bg_roundrect(x, y, CARD_W, CARD_H, RADIUS, CARD_EDGE)
    # The middle goes down before the indices, because a court card's frame
    # runs the whole height of the card and they are cut out of it.
    if full:
        if court:
            _court(x + CP_X, y + CP_Y, CP_W, CP_H, rk, st, col)
        elif rk == 0:
            # The ace keeps the single oversized mark every deck gives it.
            _pip(x + CARD_W // 2, y + CARD_H // 2, BIG_PIP, st, col)
        else:
            for (c, n) in _PIPS[rk]:
                _pip(x + PIP_COL[c], y + PF_Y + PF_H * n // 12, NUM_PIP, st,
                     col, n > 6)
    _index(x + IX_PAD, y + IX_PAD, rk, st, col, court and full)
    if full:
        _index(x + CARD_W - IX_PAD - IX_W, y + CARD_H - IX_PAD - IX_DEPTH,
               rk, st, col, court)


def _draw_back(x, y, vis=None):
    tulip.bg_roundrect(x, y, CARD_W, CARD_H, RADIUS, BACK, 1)
    tulip.bg_roundrect(x, y, CARD_W, CARD_H, RADIUS, CARD_EDGE)
    inset = max(3, CARD_W // 14)
    tulip.bg_roundrect(x + inset, y + inset, CARD_W - 2 * inset,
                       CARD_H - 2 * inset, max(2, RADIUS - 2), BACK_INK)
    if vis is not None and vis < CARD_H:
        return
    s = CARD_W // 3
    for dy in (-s, 0, s):
        _diamond(x + CARD_W // 2, y + CARD_H // 2 + dy, s // 2, BACK_INK)


def col_x(col):
    return MARGIN_X + col * (CARD_W + GAP)


def _overlaps(ax, aw, bx, bw):
    return ax < bx + bw and bx < ax + aw


# ------------------------------------------------------------------ the game

_BUTTONS = ("new", "undo", "auto", "draw")
_BUTTON_TEXT = {"new": "New", "undo": "Undo", "auto": "Auto"}
# HID usage ids, the same table starfall reads: n u a d, space, ESC.
_KEYMAP = {17: "new", 24: "undo", 4: "auto", 7: "draw", 44: "deal", 41: "quit"}

# The winning bounce, in 1/16ths of a pixel so none of it needs a float.
_GRAVITY = 24                   # 1.5 px per frame, per frame
_BOUNCE_SPEEDS = (128, 176, 224)
_BOUNCE_STOP = 64               # a bounce slower than this is not worth drawing
# A frame of the bounce costs about 6 ms to draw and the rest to composite, and
# the trails spread until almost every row is dirty -- a Tab5 settles near 13
# frames a second. So the length of the whole thing is _ANIM_LAUNCH * 52 frames,
# and these two are set by the stopwatch rather than by taste.
_ANIM_LAUNCH = 2                # frames between letting one card go and the next
_ANIM_IN_FLIGHT = 6             # how many are falling at once


class Solitaire:
    def __init__(self, app):
        self.app = app
        self.quitting = False
        self.draw_n = 1
        self.buttons = []
        x = PAD
        for name in _BUTTONS:
            self.buttons.append((name, x, BTN_W))
            x += BTN_W + BTN_GAP
        self.status_x = x + BTN_GAP
        self.status_w = SW - BAR_RESERVE - self.status_x
        self.keys_held = ()
        self.finger_down = False
        self.press_target = None
        # The cards under the finger. drag_ref names where they came from,
        # drag_dx/dy where in the first one it was taken hold of, drag_x/y
        # where the carried stack is now, and drag_moved is False until the
        # finger has travelled far enough for any of it to be drawn.
        self.drag_ref = None
        self.drag_cards = ()
        self.drag_dx = self.drag_dy = 0
        self.drag_x = self.drag_y = 0
        self.drag_moved = False
        self.now = 0
        self.clock_shown = -1
        self._reset()

    # --------------------------------------------------------------- lifecycle

    def activate(self, app):
        self.now = tulip.ticks_ms()
        # Nothing here uses sprites, and one left behind by whatever ran before
        # would cost every frame from now on.
        tulip.sprite_clear()
        tulip.Sprite.reset()
        self.finger_down = False
        self.press_target = None
        self.keys_held = ()
        self._end_drag()
        self._draw_all()
        try:
            tulip.touch_callback(self._touch)
        except Exception:
            pass                        # no touch panel; the keyboard still works
        tulip.frame_callback(self._tick, app)

    def deactivate(self, app):
        # Whatever was in the air goes back on its pile. Nothing was ever taken
        # off the model -- a carried card is only a hole in the drawing -- so
        # dropping the drag state is all it takes.
        self._end_drag()
        tulip.frame_callback()
        try:
            tulip.touch_callback()
        except Exception:
            pass

    # ------------------------------------------------------------------- state

    def _reset(self):
        deck = list(range(52))
        # MicroPython's random has no shuffle(), so: Fisher-Yates.
        for i in range(51, 0, -1):
            j = random.randrange(i + 1)
            deck[i], deck[j] = deck[j], deck[i]
        self.tab = [[] for _ in range(7)]
        self.down = [0] * 7
        for p in range(7):
            for q in range(p, 7):
                self.tab[q].append(deck.pop())
            self.down[p] = p
        self.stock = deck
        self.waste = []
        self.found = [0, 0, 0, 0]
        self.undo = []
        self.moves = 0
        self.elapsed_ms = 0
        self.sel = None
        self.msg = ""
        self.auto = False
        self.anim = []
        self.anim_queue = None
        self.anim_wait = 0
        self.anim_done = False
        self.clock_shown = -1

    def _snapshot(self):
        return (tuple(self.stock), tuple(self.waste), tuple(self.found),
                tuple(tuple(t) for t in self.tab), tuple(self.down), self.moves)

    def _push_undo(self):
        self.undo.append(self._snapshot())
        if len(self.undo) > 300:
            del self.undo[0]

    def _pop_undo(self):
        """Step back one move, and say what the board looked like before, so the
        caller can repaint the piles that actually changed. A full repaint costs
        116 ms on a Tab5 and undo is pressed far too often to pay that."""
        if not self.undo:
            return None
        before = (list(self.stock), list(self.waste), list(self.found),
                  [list(t) for t in self.tab], list(self.down), self.sel)
        (stock, waste, found, tab, down, moves) = self.undo.pop()
        self.stock = list(stock)
        self.waste = list(waste)
        self.found = list(found)
        self.tab = [list(t) for t in tab]
        self.down = list(down)
        self.moves = moves
        self.sel = None
        self.auto = False
        return before

    def _draw_changed(self, before):
        (stock, waste, found, tab, down, sel) = before
        if stock != self.stock:
            self._draw_stock()
        if waste != self.waste:
            self._draw_waste()
        for s in range(4):
            if found[s] != self.found[s]:
                self._draw_found(s)
        for p in range(7):
            if tab[p] != self.tab[p] or down[p] != self.down[p]:
                self._draw_tab(p)
        # Whatever was picked up is not any more, so its outline has to go even
        # if nothing else about that pile moved.
        if sel is not None:
            self._repaint(sel)
        self._draw_status()

    # ------------------------------------------------------------------ layout

    def _offsets(self, p):
        """Where each card of tableau pile p sits, relative to TAB_Y.

        A pile can reach nineteen cards, which will not fan at the full step, so
        a pile that does not fit shrinks its own two steps until it does. The
        backs give first, down to a sliver -- all they have to show is that
        they are there -- and only then the faces, which have to show a whole
        corner index. That order buys three or four more cards before an index
        starts being clipped; past about sixteen in one pile there is no room
        left and the suit under the rank goes."""
        cards = self.tab[p]
        n = len(cards)
        if n == 0:
            return []
        nd = self.down[p]
        fd, fu = FAN_DOWN, FAN_UP
        steps_down = min(nd, n - 1)
        steps_up = n - 1 - steps_down
        room = TAB_H - CARD_H
        need = steps_down * fd + steps_up * fu
        if need > room and steps_down:
            fd = max(3, fd - (need - room + steps_down - 1) // steps_down)
            need = steps_down * fd + steps_up * fu
        if need > room and steps_up:
            fu = max(4, (room - steps_down * fd) // steps_up)
        out = []
        y = 0
        for i in range(n):
            out.append(y)
            y += fd if i < nd else fu
        return out

    def _col_at(self, x):
        """Which of the seven columns x belongs to. Every pixel belongs to one,
        so a tap that misses a card by a few mm still lands on its pile."""
        c = (x - MARGIN_X + GAP // 2) // (CARD_W + GAP)
        if c < 0:
            return 0
        return 6 if c > 6 else c

    def _hit(self, x, y):
        if y < BAR_H:
            for (name, bx, bw) in self.buttons:
                if bx <= x < bx + bw:
                    return ("btn", name)
            return None
        if y < TOP_Y:
            return None
        if y < TOP_Y + CARD_H:
            if x < col_x(STOCK_COL) + CARD_W + GAP // 2:
                return ("stock",)
            # The waste reaches as far as it is actually drawn -- one card, or
            # three fanned -- so the gap beyond it is not part of any pile.
            fanned = max(0, min(len(self.waste), self.draw_n) - 1) * WASTE_FAN
            if x < col_x(WASTE_COL) + CARD_W + fanned + GAP // 2:
                return ("waste",)
            c = self._col_at(x)
            if c < FOUND_COLS[0]:
                return None
            return ("found", c - FOUND_COLS[0])
        if y < TAB_Y:
            return None
        p = self._col_at(x)
        offs = self._offsets(p)
        n = len(offs)
        for i in range(n - 1, -1, -1):
            top = TAB_Y + offs[i]
            h = CARD_H if i == n - 1 else offs[i + 1] - offs[i]
            if top <= y < top + h:
                return ("tab", p, i)
        return ("tabcol", p)

    @staticmethod
    def _pile_of(ref):
        return (ref[0], ref[1]) if ref[0] == "found" else \
               ("tab", ref[1]) if ref[0] in ("tab", "tabcol") else (ref[0], None)

    # ------------------------------------------------------------------- rules

    def _cards_of(self, sel):
        kind = sel[0]
        if kind == "waste":
            return [self.waste[-1]] if self.waste else []
        if kind == "found":
            s = sel[1]
            return [(self.found[s] - 1) * 4 + s] if self.found[s] else []
        (p, i) = (sel[1], sel[2])
        return self.tab[p][i:]

    def _grabbable(self, ref):
        kind = ref[0]
        if kind == "waste":
            return bool(self.waste)
        if kind == "found":
            return self.found[ref[1]] > 0
        if kind != "tab":
            return False
        (p, i) = (ref[1], ref[2])
        if i < self.down[p]:
            return False
        run = self.tab[p][i:]
        # Legal play can only ever build a descending two-colour run, but check
        # anyway: it costs nothing and it means a pick-up can never make a move
        # the rules would not allow.
        for k in range(len(run) - 1):
            a, b = run[k], run[k + 1]
            if _rank(b) != _rank(a) - 1 or SUIT_RED[_suit(a)] == SUIT_RED[_suit(b)]:
                return False
        return True

    def _accepts(self, dest, cards):
        if not cards:
            return False
        kind = dest[0]
        card = cards[0]
        if kind == "found":
            s = dest[1]
            return (len(cards) == 1 and _suit(card) == s
                    and _rank(card) == self.found[s])
        if kind in ("tab", "tabcol"):
            pile = self.tab[dest[1]]
            if not pile:
                return _rank(card) == 12                 # only a King starts one
            top = pile[-1]
            return (_rank(card) == _rank(top) - 1
                    and SUIT_RED[_suit(card)] != SUIT_RED[_suit(top)])
        return False

    def _move(self, sel, dest):
        """Move the selection onto dest, if the rules allow. Repaints what it
        touched and returns whether it happened."""
        cards = self._cards_of(sel)
        if not self._accepts(dest, cards):
            return False
        self._push_undo()
        kind = sel[0]
        if kind == "waste":
            self.waste.pop()
        elif kind == "found":
            self.found[sel[1]] -= 1
        else:
            del self.tab[sel[1]][sel[2]:]
        if dest[0] == "found":
            self.found[dest[1]] += 1
        else:
            self.tab[dest[1]].extend(cards)
        self.moves += 1
        self.msg = ""
        self.sel = None
        if kind == "tab":
            p = sel[1]
            # Taking the last face-up card off a pile turns the next one over.
            if self.tab[p] and self.down[p] == len(self.tab[p]):
                self.down[p] -= 1
        self._repaint(sel)
        self._repaint(dest)
        self._draw_status()
        if sum(self.found) == 52:
            self._start_win()
        return True

    def _to_foundation(self, sel):
        cards = self._cards_of(sel)
        if len(cards) != 1:
            return False
        return self._move(sel, ("found", _suit(cards[0])))

    def _auto_step(self):
        """One card to a foundation, if any can go. Auto runs this a frame at a
        time so the finish reads as a cascade rather than a jump cut."""
        if self.waste and self._to_foundation(("waste",)):
            return True
        for p in range(7):
            if self.tab[p] and self._to_foundation(("tab", p, len(self.tab[p]) - 1)):
                return True
        return False

    def _deal(self):
        if self.stock:
            self._push_undo()
            for _ in range(self.draw_n):
                if not self.stock:
                    break
                self.waste.append(self.stock.pop())
        elif self.waste:
            self._push_undo()
            self.stock = self.waste[::-1]
            self.waste = []
        else:
            return
        self.moves += 1
        self.msg = ""
        self._select(None)
        self._draw_stock()
        self._draw_waste()
        self._draw_status()

    # -------------------------------------------------------------- selection

    def _select(self, sel):
        if sel == self.sel:
            return
        old = self.sel
        self.sel = sel
        if old is not None:
            self._repaint(old)
        if sel is not None:
            self._repaint(sel)

    # ----------------------------------------------------------------- drawing

    def _lifted_from(self, kind, key=None):
        """True while the cards in the air came off this pile, so it has to
        draw itself without them."""
        ref = self.drag_ref
        return (self.drag_moved and ref is not None and ref[0] == kind
                and (key is None or ref[1] == key))

    def _repaint(self, ref):
        kind = ref[0]
        if kind == "stock":
            self._draw_stock()
        elif kind == "waste":
            self._draw_waste()
        elif kind == "found":
            self._draw_found(ref[1])
        else:
            self._draw_tab(ref[1])

    def _draw_all(self):
        tulip.bg_rect(0, BAR_H, SW, SH - BAR_H, FELT, 1)
        self._draw_bar()
        self._draw_stock()
        self._draw_waste()
        for s in range(4):
            self._draw_found(s)
        for p in range(7):
            self._draw_tab(p)

    def _label(self, name):
        if name == "draw":
            return "Draw %d" % self.draw_n
        return _BUTTON_TEXT[name]

    def _draw_bar(self):
        tulip.bg_rect(0, 0, BAR_FILL_W, BAR_H, BAR, 1)
        by = (BAR_H - BTN_H) // 2
        for (name, bx, bw) in self.buttons:
            tulip.bg_roundrect(bx, by, bw, BTN_H, 6, BTN, 1)
            tulip.bg_roundrect(bx, by, bw, BTN_H, 6, BTN_EDGE)
            tulip.bg_str(self._label(name), bx, by, BTN_TEXT, BTN_FONT,
                         bw, BTN_H)
        self._draw_status()

    def _draw_status(self):
        if self.status_w < 60:
            return
        secs = self.elapsed_ms // 1000
        self.clock_shown = secs
        text = "%d/52   %d:%02d   %d moves" % (sum(self.found), secs // 60,
                                               secs % 60, self.moves)
        if self.msg:
            text = self.msg + "    " + text
        tulip.bg_rect(self.status_x, 0, self.status_w, BAR_H, BAR, 1)
        tulip.bg_str(text, self.status_x, 0, BAR_TEXT, STATUS_FONT,
                     self.status_w, BAR_H)

    # Each pile comes in two halves: _paint_ puts its cards down where they
    # belong, and _draw_ clears its rectangle first. Restoring a band of the
    # board only ever paints -- see _restore().

    def _draw_stock(self):
        tulip.bg_rect(col_x(STOCK_COL), TOP_Y, CARD_W, CARD_H, FELT, 1)
        self._paint_stock()

    def _paint_stock(self):
        x = col_x(STOCK_COL)
        if self.stock:
            _draw_back(x, TOP_Y)
            return
        _slot(x, TOP_Y)
        if self.waste:
            # There is another pass in it: say so with a ring and an arrow head.
            cx, cy = x + CARD_W // 2, TOP_Y + CARD_H // 2
            r = CARD_W // 4
            tulip.bg_circle(cx, cy, r, SLOT)
            tulip.bg_circle(cx, cy, r - 1, SLOT)
            a = max(3, r // 3)
            tulip.bg_triangle(cx + r, cy - a, cx + r + a, cy,
                              cx + r - a, cy, SLOT, 1)

    def _draw_waste(self):
        tulip.bg_rect(col_x(WASTE_COL), TOP_Y, CARD_W + 2 * WASTE_FAN, CARD_H,
                      FELT, 1)
        self._paint_waste()

    def _paint_waste(self):
        x = col_x(WASTE_COL)
        if not self.waste:
            _slot(x, TOP_Y)
            return
        shown = self.waste[-min(len(self.waste), self.draw_n):]
        lifted = self._lifted_from("waste")
        if lifted:
            shown = shown[:-1]
            if not shown:
                _slot(x, TOP_Y)
                return
        last = len(shown) - 1
        for (k, card) in enumerate(shown):
            _draw_face(x + k * WASTE_FAN, TOP_Y, card,
                       None if k == last else WASTE_FAN)
        if self.sel is not None and self.sel[0] == "waste" and not lifted:
            _outline(x + last * WASTE_FAN, TOP_Y, CARD_W, CARD_H)

    def _draw_found(self, s):
        tulip.bg_rect(col_x(FOUND_COLS[s]), TOP_Y, CARD_W, CARD_H, FELT, 1)
        self._paint_found(s)

    def _paint_found(self, s):
        x = col_x(FOUND_COLS[s])
        lifted = self._lifted_from("found", s)
        n = self.found[s] - (1 if lifted else 0)
        if not n:
            _slot(x, TOP_Y)
            _pip(x + CARD_W // 2, TOP_Y + CARD_H // 2, BIG_PIP, s, SLOT)
            return
        _draw_face(x, TOP_Y, (n - 1) * 4 + s)
        if self.sel == ("found", s) and not lifted:
            _outline(x, TOP_Y, CARD_W, CARD_H)

    def _draw_tab(self, p):
        tulip.bg_rect(col_x(p), TAB_Y, CARD_W, TAB_H, FELT, 1)
        self._paint_tab(p)

    def _tab_shown(self, p):
        """How many of this pile's cards belong on the table -- the rest, if
        any, are in the air under a finger."""
        if self._lifted_from("tab", p):
            return self.drag_ref[2]
        return len(self.tab[p])

    def _paint_tab(self, p, first=0):
        """Put the pile's cards back, from `first` upward. Never fewer than
        that: a card covers the one under it, so a restored card has to be
        followed by every card that was drawn on top of it."""
        x = col_x(p)
        cards = self.tab[p]
        n = self._tab_shown(p)
        if n == 0:
            if first == 0:
                _slot(x, TAB_Y)
            return
        offs = self._offsets(p)         # of the whole pile: the fan does not
        nd = self.down[p]               # change because the top of it is lifted
        for i in range(first, n):
            y = TAB_Y + offs[i]
            vis = None if i == n - 1 else offs[i + 1] - offs[i]
            if i < nd:
                _draw_back(x, y, vis)
            else:
                _draw_face(x, y, cards[i], vis)
        if self.sel is not None and self.sel[0] == "tab" and self.sel[1] == p \
                and self.sel[2] < n:
            i = self.sel[2]
            _outline(x, TAB_Y + offs[i], CARD_W,
                     offs[n - 1] - offs[i] + CARD_H)

    def _first_card_at(self, p, y0, y1):
        """The lowest card of pile p whose own rectangle reaches into the band
        y0..y1, or None if none does."""
        n = self._tab_shown(p)
        if n == 0:
            return 0 if (y0 < TAB_Y + CARD_H and y1 > TAB_Y) else None
        offs = self._offsets(p)
        for i in range(n):
            top = TAB_Y + offs[i]
            if top + CARD_H > y0:
                return i if top < y1 else None
        return None

    def _restore(self, x0, y0, x1, y1):
        """Put the board back over one rectangle.

        Only the rectangle is cleared, and the piles that reach into it repaint
        their cards without clearing themselves. That is the whole reason a
        drag does not flicker: a column is 474 px tall and clearing one blanks
        it for as long as it takes to draw ten cards back into it, which is
        several panel refreshes -- and a card being carried crosses two of them
        at a time.
        """
        if x0 < 0:
            x0 = 0
        if y0 < BAR_H:
            y0 = BAR_H                  # the bar is never drawn over
        if x1 > SW:
            x1 = SW
        if y1 > SH:
            y1 = SH
        if x1 <= x0 or y1 <= y0:
            return
        tulip.bg_rect(x0, y0, x1 - x0, y1 - y0, FELT, 1)
        w = x1 - x0
        if y0 < TOP_Y + CARD_H and y1 > TOP_Y:
            if _overlaps(x0, w, col_x(STOCK_COL), CARD_W):
                self._paint_stock()
            if _overlaps(x0, w, col_x(WASTE_COL), CARD_W + 2 * WASTE_FAN):
                self._paint_waste()
            for s in range(4):
                if _overlaps(x0, w, col_x(FOUND_COLS[s]), CARD_W):
                    self._paint_found(s)
        if y0 < TAB_Y + TAB_H and y1 > TAB_Y:
            for p in range(7):
                if not _overlaps(x0, w, col_x(p), CARD_W):
                    continue
                first = self._first_card_at(p, y0, y1)
                if first is not None:
                    self._paint_tab(p, first)

    # ------------------------------------------------------------------- input

    def _touch(self, up):
        try:
            point = tulip.touch()
        except Exception:
            return
        (x, y) = (point[0], point[1])
        if up:
            self.finger_down = False
            ref = self.drag_ref
            src = self.press_target
            self.press_target = None
            if ref is not None and self.drag_moved:
                self._drop_carried(x, y)
                return
            self._end_drag()
            # Nothing was carried anywhere, so this was a tap. Where a board
            # reports no samples between a press and a release, a press in one
            # pile and a release in another still reads as the move it stands
            # for -- that is all the drag there was before this.
            if src is None or self.sel is None or x < 0 or y < 0:
                return
            target = self._hit(x, y)
            if target is not None and self._pile_of(target) != self._pile_of(src):
                self._drop(target)
            return
        if self.finger_down:
            # A held sample. If something was picked up, it follows the finger.
            if self.drag_ref is not None and x >= 0 and y >= 0:
                self._carry_to(x, y)
            return
        self.finger_down = True
        if x < 0 or y < 0:
            return
        self._press(x, y)

    def _press(self, x, y):
        if self.anim_queue is not None:
            self._new_game()
            return
        target = self._hit(x, y)
        self.press_target = target
        if target is None:
            self._select(None)
            return
        kind = target[0]
        if kind == "btn":
            self._select(None)
            self._button(target[1])
            return
        self.auto = False
        if kind == "stock":
            self._select(None)
            self._deal()
            return
        if self.sel is not None:
            if self._pile_of(target) == self._pile_of(self.sel):
                # The same pile again: send a single card home if it can go,
                # otherwise put it back down.
                if not self._to_foundation(self.sel):
                    self._select(None)
                return
            if self._drop(target):
                return
            # Not a legal destination -- read it as picking that card up instead.
        if self._grabbable(target):
            self._select(target)
            self._pick_up(target, x, y)
        else:
            self._select(None)

    def _drop(self, target):
        if self.sel is None:
            return False
        if target[0] in ("stock", "waste"):
            return False
        return self._move(self.sel, target)

    # --------------------------------------------------------------- carrying
    # Everything a card does between being pressed and being let go. It is a
    # pick-up rather than a drag until the finger has actually travelled
    # DRAG_SLOP, so a tap still means what it always did: nothing is lifted,
    # nothing is repainted, and a short press selects in place.

    def _home_of(self, ref):
        """Where the first of the cards under this ref is sitting now."""
        kind = ref[0]
        if kind == "waste":
            shown = min(len(self.waste), self.draw_n)
            return (col_x(WASTE_COL) + max(0, shown - 1) * WASTE_FAN, TOP_Y)
        if kind == "found":
            return (col_x(FOUND_COLS[ref[1]]), TOP_Y)
        return (col_x(ref[1]), TAB_Y + self._offsets(ref[1])[ref[2]])

    def _pick_up(self, ref, x, y):
        (hx, hy) = self._home_of(ref)
        self.drag_ref = ref
        self.drag_cards = self._cards_of(ref)
        # Where in the card it was taken hold of. A tap on the waste counts for
        # the whole fan, so clamp it inside the card the tap actually picked up.
        self.drag_dx = min(max(x - hx, 0), CARD_W - 1)
        self.drag_dy = min(max(y - hy, 0), CARD_H - 1)
        self.drag_x, self.drag_y = hx, hy
        self.drag_moved = False

    def _end_drag(self):
        self.drag_ref = None
        self.drag_cards = ()
        self.drag_moved = False

    def _cancel_drag(self):
        """Put whatever is in the air back on its pile and take it off the
        felt. Anything that changes the board from outside a touch -- a key, a
        button -- has to call this first, or the next held sample would carry a
        card that belongs to the board that has just been replaced, and letting
        go of it would drop a stale ref onto the new one."""
        if self.drag_ref is None:
            return
        ref = self.drag_ref
        (ox, oy) = (self.drag_x, self.drag_y)
        h = self._carried_h()
        moved = self.drag_moved
        self._end_drag()
        if moved:
            self._restore(ox, oy, ox + CARD_W, oy + h)
            self._repaint(ref)

    def _carried_h(self):
        return CARD_H + (len(self.drag_cards) - 1) * FAN_UP

    def _carry_to(self, x, y):
        nx = x - self.drag_dx
        ny = y - self.drag_dy
        # Keep what is carried on the screen: bg_str takes an unsigned x, so a
        # card drawn off the left edge would wrap to the far side of the plane.
        if nx < 0:
            nx = 0
        elif nx > SW - CARD_W:
            nx = SW - CARD_W
        if ny < BAR_H:
            ny = BAR_H
        elif ny > SH - CARD_H:
            ny = SH - CARD_H
        if not self.drag_moved:
            (hx, hy) = (self.drag_x, self.drag_y)
            if abs(nx - hx) + abs(ny - hy) < DRAG_SLOP:
                return                          # still a tap, not a drag yet
            self.drag_moved = True
            h = self._carried_h()
            # The pile it came from draws itself without these cards now. Only
            # the rectangle they were sitting in is put back, not the column.
            self._restore(hx, hy, hx + CARD_W, hy + h)
            self.drag_x, self.drag_y = nx, ny
            self._draw_carried()
            return
        if abs(nx - self.drag_x) + abs(ny - self.drag_y) < 3:
            return                              # not far enough to be worth it
        (ox, oy) = (self.drag_x, self.drag_y)
        self.drag_x, self.drag_y = nx, ny
        self._restore_vacated(ox, oy, nx, ny, self._carried_h())
        self._draw_carried()

    def _restore_vacated(self, ox, oy, nx, ny, h):
        """Put the board back over the part of the old rectangle the cards are
        about to leave -- never the part they still cover.

        That is what keeps a carried card on the screen the whole way across:
        it is only ever added to, never taken away and put back, so there is no
        moment at which a panel refresh can catch it missing. The two pieces
        are the strip it has moved off sideways and, over what is still covered
        horizontally, the strip it has moved off vertically."""
        if nx > ox:
            self._restore(ox, oy, min(nx, ox + CARD_W), oy + h)
        elif nx < ox:
            self._restore(max(nx + CARD_W, ox), oy, ox + CARD_W, oy + h)
        lx0 = ox if ox > nx else nx
        lx1 = min(ox + CARD_W, nx + CARD_W)
        if lx1 <= lx0:
            return                              # moved clear of itself sideways
        if ny > oy:
            self._restore(lx0, oy, lx1, min(ny, oy + h))
        elif ny < oy:
            self._restore(lx0, max(ny + h, oy), lx1, oy + h)

    def _draw_carried(self):
        (x, y) = (self.drag_x, self.drag_y)
        cards = self.drag_cards
        last = len(cards) - 1
        for (i, card) in enumerate(cards):
            cy = y + i * FAN_UP
            if cy >= SH:
                break                           # the tail of a long run, off-screen
            _draw_face(x, cy, card, None if i == last else FAN_UP)
        _outline(x, y, CARD_W, self._carried_h())

    def _drop_carried(self, fx, fy):
        ref = self.drag_ref
        (ox, oy) = (self.drag_x, self.drag_y)
        h = self._carried_h()
        # Aim with the card, not with the fingertip: what the player lines up
        # over a pile is the card they are holding.
        target = self._hit(ox + CARD_W // 2, oy + CARD_H // 2)
        if target is None or target[0] == "btn":
            target = self._hit(fx, fy)
        self._end_drag()
        self._restore(ox, oy, ox + CARD_W, oy + h)
        self.sel = None
        if (target is not None and target[0] != "btn"
                and self._pile_of(target) != self._pile_of(ref)
                and self._move(ref, target)):
            return
        self._repaint(ref)                      # it goes back where it came from

    def _button(self, name):
        if name == "new":
            self._new_game()
        elif name == "undo":
            before = self._pop_undo()
            if before is not None:
                self.msg = ""
                self._draw_changed(before)
        elif name == "auto":
            self.auto = not self.auto
            self.msg = ""
            self._draw_status()
        elif name == "draw":
            self.draw_n = 3 if self.draw_n == 1 else 1
            self._draw_bar()
            self._draw_waste()

    def _new_game(self):
        self._reset()
        self._draw_all()

    def _read_keys(self):
        if not hasattr(tulip, "keys"):
            return
        held = tulip.keys()[1:]
        for code in held:
            if code in _KEYMAP and code not in self.keys_held:
                self._on_key(_KEYMAP[code])
        self.keys_held = held

    def _on_key(self, action):
        # A key can land in the middle of a drag; the touch callback and the
        # frame callback that polls the keyboard are separate schedulings.
        self._cancel_drag()
        if self.anim_queue is not None and action != "quit":
            self._new_game()
            return
        if action == "quit":
            self.quitting = True
            tulip.frame_callback()
            tulip.defer(lambda app: app.quit(), self.app, 20)
        elif action == "deal":
            self._select(None)
            self._deal()
        else:
            self._button(action)

    # ------------------------------------------------------------- the winner

    def _start_win(self):
        # Short, because the status line is only as wide as the gap between
        # this app's buttons and the task bar. The rest of it is said in the
        # banner, once the cards have finished falling.
        self.msg = "You win!"
        self.auto = False
        self.sel = None
        # Kings first, so the pile comes apart from the top the way it was built.
        self.anim_queue = [(rk * 4 + s, FOUND_COLS[s])
                           for rk in range(12, -1, -1) for s in range(4)]
        self.anim = []
        self.anim_wait = 0
        self.anim_done = False
        self._draw_status()

    def _draw_banner(self):
        w = min(SW - 2 * MARGIN_X, CARD_W * 5)
        h = CARD_H
        x = (SW - w) // 2
        y = (SH - h) // 2
        tulip.bg_roundrect(x, y, w, h, RADIUS * 2, BAR, 1)
        for i in range(3):
            tulip.bg_roundrect(x + i, y + i, w - 2 * i, h - 2 * i,
                               RADIUS * 2, SEL)
        secs = self.elapsed_ms // 1000
        tulip.bg_str("YOU WIN", x, y + h // 8, SEL, BANNER_FONT, w, h // 3)
        tulip.bg_str("%d moves in %d:%02d -- tap to deal again"
                     % (self.moves, secs // 60, secs % 60),
                     x, y + h // 2, BAR_TEXT, STATUS_FONT, w, h // 3)

    def _step_anim(self):
        """The foundations come apart and bounce off the bottom of the screen,
        leaving their trails. Nothing is erased and nothing is read back, which
        is the whole trick: the trail IS the frames that came before."""
        self.anim_wait -= 1
        if (self.anim_queue and self.anim_wait <= 0
                and len(self.anim) < _ANIM_IN_FLIGHT):
            (card, col) = self.anim_queue.pop(0)
            # Alternate sides. All four foundations sit right of centre, so
            # sending each card off the nearer edge would send every one of
            # them the same way and pile the whole deck into one corner.
            speed = random.choice(_BOUNCE_SPEEDS)
            if len(self.anim_queue) & 1:
                speed = -speed
            self.anim.append([col_x(col) * 16, TOP_Y * 16, speed, 0, card])
            self.anim_wait = _ANIM_LAUNCH
        floor = (SH - CARD_H) * 16
        gone = False
        for b in self.anim:
            b[0] += b[2]
            b[3] += _GRAVITY
            b[1] += b[3]
            if b[1] > floor:
                b[1] = floor
                b[3] = -(b[3] * 3) // 4
                if -b[3] < _BOUNCE_STOP:
                    b[3] = 0            # worn out; it skids off the side instead
                    b[2] *= 2
            x = b[0] // 16
            if x < 0 or x + CARD_W > SW:
                b[4] = None             # bg_str takes no negative x; stop drawing
                gone = True
                continue
            _draw_face(x, b[1] // 16, b[4])
        if gone:
            self.anim = [b for b in self.anim if b[4] is not None]
        if not self.anim and not self.anim_queue and not self.anim_done:
            self.anim_done = True
            self._draw_banner()

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
            print("solitaire: stopped on an error")
            sys.print_exception(e)

    def _frame(self):
        now = tulip.ticks_ms()
        dt = now - self.now
        self.now = now
        if dt < 0 or dt > 1000:
            dt = 16                     # away, or something else held the CPU
        self._read_keys()
        if self.anim_queue is not None:
            self._step_anim()
            return
        self.elapsed_ms += dt
        if self.auto and not self._auto_step():
            self.auto = False
            self.msg = "Nothing more can go up"
            self._draw_status()
        if self.elapsed_ms // 1000 != self.clock_shown:
            self._draw_status()


def run(app):
    app.game = True
    game = Solitaire(app)
    app.solitaire = game
    app.activate_callback = game.activate
    app.deactivate_callback = game.deactivate
    app.present()
