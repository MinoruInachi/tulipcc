# matplotlib.pyplot for Tulip.
#
# The state machine, the artists and the renderer, in that order.  Everything
# draws with `tulip.bg_*` calls into the background layer; nothing here keeps
# a pixel buffer of its own.
#
# ---------------------------------------------------------------------------
# What is supported
#
#   figures   figure, subplot, subplots, axes, gca, gcf, sca, cla, clf, close
#   plots     plot, step, scatter, bar, barh, hist, boxplot, pie, errorbar,
#             fill_between, fill_betweenx, axhline, axvline, axhspan, axvspan,
#             imshow, arrow
#   decor     title, suptitle, xlabel, ylabel, xlim, ylim, xticks, yticks,
#             grid, legend, text, annotate, axis, tight_layout
#   scales    xscale, yscale, semilogx, semilogy, loglog
#   output    show, draw, savefig, pause
#
# ---------------------------------------------------------------------------
# Where it deviates from upstream, and why
#
#   * `show()` draws and returns.  It cannot block -- there is no event loop to
#     block on -- and it does NOT throw the figure away, so `plot(); show();
#     plot(); show()` accumulates.  Call `clf()` (or `figure()`) to start over.
#   * `alpha=` is accepted and ignored.  The background layer is one byte per
#     pixel with no blending.
#   * Font sizes are Tulip font numbers (0-18), not points.  See rcParams.
#   * `ylabel` is drawn as a column of upright characters rather than rotated
#     text; Tulip's text renderer draws left to right only.
#   * `imshow` defaults to `aspect='auto'` (fill the axes box) rather than
#     upstream's `'equal'`, so that the axes frame and the image stay aligned
#     and the ticks keep meaning what they say.  Pass `aspect='equal'` for the
#     upstream look.
#   * Colors collapse to Tulip's 256-entry RGB332 palette; see colors.py.
#
# ---------------------------------------------------------------------------
# Example
#
#     from ulab import numpy as np
#     import matplotlib.pyplot as plt
#
#     x = np.linspace(0, 4 * np.pi, 200)
#     plt.plot(x, np.sin(x), label='sin')
#     plt.plot(x, np.cos(x), 'r--', label='cos')
#     plt.title('trig')
#     plt.xlabel('x'); plt.ylabel('y')
#     plt.grid(True); plt.legend()
#     plt.show()

import math

import tulip

from . import rcParams
from .colors import to_pal, get_cmap, ALPHA_INDEX

_INF = float("inf")

# hist() takes a `range=` argument, the same name upstream uses, which
# shadows the builtin inside that method.  Keep a reference out here.
_RANGE = range

# (line height, cap height) for Tulip's 19 built-in fonts, read out of the
# u8g2 font headers in shared/u8fontdata.c (bytes 10 and 13).  Widths are not
# in here: only two of the fonts are fixed pitch, so string widths come from
# _text_w() below instead.
_FONT_METRICS = (
    (21, 13),  # 0  t0_22
    (21, 13),  # 1  t0_22b
    (20, 13),  # 2  crox4t
    (16, 13),  # 3  lubI12
    (21, 14),  # 4  calibration_gothic_nbp
    (18, 14),  # 5  helvB14
    (18, 14),  # 6  helvR14
    (23, 16),  # 7  logisoso16
    (13, 9),   # 8  6x13
    (13, 9),   # 9  8x13
    (15, 9),   # 10 profont15
    (13, 9),   # 11 crox1h
    (14, 11),  # 12 fewture
    (17, 12),  # 13 helvR12
    (14, 10),  # 14 luRS10
    (25, 18),  # 15 luRS18
    (26, 18),  # 16 osb18
    (35, 24),  # 17 logisoso24
    (34, 25),  # 18 lubB24
)

# Dash patterns in pixels, on/off alternating.  '-' is solid and skips the
# dash walker entirely.
_DASHES = {
    "-": None,
    "solid": None,
    "--": (11, 6),
    "dashed": (11, 6),
    ":": (2, 4),
    "dotted": (2, 4),
    "-.": (11, 4, 2, 4),
    "dashdot": (11, 4, 2, 4),
    "": False,          # explicit "draw no line"
    "none": False,
    "None": False,
}


# --------------------------------------------------------------------------
# small numeric helpers


def _finite(v):
    return v == v and -_INF < v < _INF


def _is_seq(v):
    """True for anything len() accepts, except strings.

    Not `hasattr(v, "__len__")`: MicroPython's built-in types do not expose
    their dunder methods as attributes, so that answers False for a plain
    list and every sequence argument silently gets treated as a scalar.
    """
    if isinstance(v, str):
        return False
    try:
        len(v)
    except TypeError:
        return False
    return True


def _seq(a):
    """Coerce list / tuple / range / ulab ndarray / scalar to a list of floats."""
    if a is None:
        return []
    if isinstance(a, (int, float)):
        return [float(a)]
    return [float(v) for v in a]


def _bounds1(vals):
    """(min, max, smallest positive) over the finite entries, or None."""
    lo = hi = pos = None
    for v in vals:
        if not _finite(v):
            continue
        if lo is None or v < lo:
            lo = v
        if hi is None or v > hi:
            hi = v
        if v > 0 and (pos is None or v < pos):
            pos = v
    if lo is None:
        return None
    return (lo, hi, pos)


def _merge(a, b):
    """Union of two (min, max, minpos) triples, either of which may be None."""
    if a is None:
        return b
    if b is None:
        return a
    pos = a[2] if b[2] is None else (b[2] if a[2] is None else min(a[2], b[2]))
    return (min(a[0], b[0]), max(a[1], b[1]), pos)


def _screen():
    return tulip.screen_size()


# --------------------------------------------------------------------------
# text measurement
#
# Tulip exposes no measure-text call, but bg_str() returns the advance width of
# what it just drew.  So we draw single characters into the offscreen columns
# past the right edge of the panel -- framebuffer memory the scanout never
# reads -- and keep the numbers.  One throwaway draw per (font, character)
# pair, cached for the life of the process.

_char_w = {}
_scratch = None


def _text_w(s, font):
    """Exact pixel width of `s` rendered in Tulip font `font`."""
    global _scratch
    if not s:
        return 0
    if _scratch is None:
        # OFFSCREEN_X_PX is at least 128 on every Tulip panel, and no single
        # glyph in the built-in fonts is anywhere near that wide.
        _scratch = _screen()[0] + 4
    total = 0
    for ch in s:
        key = (font, ch)
        w = _char_w.get(key)
        if w is None:
            w = tulip.bg_str(ch, _scratch, 4, 0, font)
            _char_w[key] = w
        total += w
    return total


def _font_h(font):
    return _FONT_METRICS[font][0] if 0 <= font < len(_FONT_METRICS) else 13


def _font_cap(font):
    return _FONT_METRICS[font][1] if 0 <= font < len(_FONT_METRICS) else 9


def _draw_text(s, x, y, color, font, ha="left", va="baseline"):
    """Draw `s` with matplotlib's alignment vocabulary.

    bg_str places the baseline at `y` and the left edge at `x`, so everything
    else is an offset computed from the font metrics.
    """
    if not s:
        return
    if ha != "left":
        w = _text_w(s, font)
        if ha == "center":
            x -= w // 2
        elif ha == "right":
            x -= w
    if va != "baseline":
        cap = _font_cap(font)
        if va == "center":
            y += cap // 2
        elif va == "top":
            y += cap
        elif va == "bottom":
            pass
    tulip.bg_str(s, int(x), int(y), color, font)


# --------------------------------------------------------------------------
# clipping

_L, _R, _B, _T = 1, 2, 4, 8


def _outcode(x, y, box):
    code = 0
    if x < box[0]:
        code = _L
    elif x > box[2]:
        code = _R
    if y < box[1]:
        code |= _T
    elif y > box[3]:
        code |= _B
    return code


def _clip_line(ax, ay, bx, by, box):
    """Cohen-Sutherland against an inclusive (x0, y0, x1, y1) pixel box."""
    ca = _outcode(ax, ay, box)
    cb = _outcode(bx, by, box)
    for _ in range(8):
        if not (ca | cb):
            return (ax, ay, bx, by)
        if ca & cb:
            return None
        c = ca if ca else cb
        if c & _B:
            if by == ay:
                return None
            x = ax + (bx - ax) * (box[3] - ay) / (by - ay)
            y = box[3]
        elif c & _T:
            if by == ay:
                return None
            x = ax + (bx - ax) * (box[1] - ay) / (by - ay)
            y = box[1]
        elif c & _R:
            if bx == ax:
                return None
            y = ay + (by - ay) * (box[2] - ax) / (bx - ax)
            x = box[2]
        else:
            if bx == ax:
                return None
            y = ay + (by - ay) * (box[0] - ax) / (bx - ax)
            x = box[0]
        if c == ca:
            ax, ay, ca = x, y, _outcode(x, y, box)
        else:
            bx, by, cb = x, y, _outcode(x, y, box)
    return None


def _inside(x, y, box):
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def _clip_rect(x0, y0, x1, y1, box):
    """Intersect a pixel rect with the clip box; None if it falls outside."""
    x0 = max(x0, box[0])
    y0 = max(y0, box[1])
    x1 = min(x1, box[2])
    y1 = min(y1, box[3])
    if x1 < x0 or y1 < y0:
        return None
    return (x0, y0, x1, y1)


# --------------------------------------------------------------------------
# styled line drawing


def _seg(x0, y0, x1, y1, color, lw, box):
    """One clipped straight segment."""
    c = _clip_line(x0, y0, x1, y1, box)
    if c is None:
        return
    tulip.bg_line(int(c[0]), int(c[1]), int(c[2]), int(c[3]), color, int(lw))


def _dashed_seg(x0, y0, x1, y1, color, lw, pat, phase, box):
    """One dashed segment; returns the dash phase to carry into the next one."""
    dx = x1 - x0
    dy = y1 - y0
    length = math.sqrt(dx * dx + dy * dy)
    if length < 0.5:
        return phase
    ux = dx / length
    uy = dy / length

    period = 0.0
    for p in pat:
        period += p
    rem = phase % period
    idx = 0
    while rem >= pat[idx]:
        rem -= pat[idx]
        idx = (idx + 1) % len(pat)
    on = (idx % 2) == 0
    left = pat[idx] - rem

    pos = 0.0
    while pos < length:
        step = left if left < (length - pos) else (length - pos)
        if on:
            _seg(x0 + ux * pos, y0 + uy * pos,
                 x0 + ux * (pos + step), y0 + uy * (pos + step), color, lw, box)
        pos += step
        left -= step
        if left <= 1e-9:
            idx = (idx + 1) % len(pat)
            left = pat[idx]
            on = not on
    return phase + length


def _polyline(pts, color, lw, ls, box):
    """Draw a list of (x, y) pixel points; None entries break the line."""
    pat = _DASHES.get(ls, None)
    if pat is False or lw <= 0 or color is None:
        return
    phase = 0.0
    prev = None
    for p in pts:
        if p is None:
            prev = None
            continue
        if prev is not None:
            if pat is None:
                _seg(prev[0], prev[1], p[0], p[1], color, lw, box)
            else:
                phase = _dashed_seg(prev[0], prev[1], p[0], p[1],
                                    color, lw, pat, phase, box)
        prev = p


def _marker(x, y, marker, size, color, filled=True):
    """Draw one marker centred on a pixel.  The caller does the clipping."""
    x = int(x)
    y = int(y)
    h = int(size) // 2
    if h < 1:
        h = 1
    fill = 1 if filled else 0
    if marker == ",":
        tulip.bg_pixel(x, y, color)
    elif marker == ".":
        tulip.bg_circle(x, y, max(1, h // 2), color, 1)
    elif marker == "o":
        tulip.bg_circle(x, y, h, color, fill)
    elif marker == "s":
        tulip.bg_rect(x - h, y - h, 2 * h + 1, 2 * h + 1, color, fill)
    elif marker == "+":
        tulip.bg_line(x - h, y, x + h, y, color)
        tulip.bg_line(x, y - h, x, y + h, color)
    elif marker == "x":
        tulip.bg_line(x - h, y - h, x + h, y + h, color)
        tulip.bg_line(x - h, y + h, x + h, y - h, color)
    elif marker == "*":
        tulip.bg_line(x - h, y, x + h, y, color)
        tulip.bg_line(x, y - h, x, y + h, color)
        g = (h * 7) // 10
        tulip.bg_line(x - g, y - g, x + g, y + g, color)
        tulip.bg_line(x - g, y + g, x + g, y - g, color)
    elif marker == "|":
        tulip.bg_line(x, y - h, x, y + h, color)
    elif marker == "_":
        tulip.bg_line(x - h, y, x + h, y, color)
    elif marker in ("D", "d"):
        w = h if marker == "D" else max(1, (h * 6) // 10)
        tulip.bg_triangle(x, y - h, x + w, y, x - w, y, color, fill)
        tulip.bg_triangle(x, y + h, x + w, y, x - w, y, color, fill)
    elif marker == "^":
        tulip.bg_triangle(x, y - h, x + h, y + h, x - h, y + h, color, fill)
    elif marker == "v":
        tulip.bg_triangle(x, y + h, x + h, y - h, x - h, y - h, color, fill)
    elif marker == "<":
        tulip.bg_triangle(x - h, y, x + h, y - h, x + h, y + h, color, fill)
    elif marker == ">":
        tulip.bg_triangle(x + h, y, x - h, y - h, x - h, y + h, color, fill)
    else:
        tulip.bg_circle(x, y, h, color, fill)


def _fmt_spec(fmt):
    """Split a matplotlib format string into (color, linestyle, marker).

    Any of the three may come back None, meaning "not mentioned, use the
    default".  A format string that names a marker but no line style means
    markers only -- that is upstream's rule and it is what makes 'ro' scatter
    rather than draw a red polyline.
    """
    color = ls = marker = None
    i = 0
    n = len(fmt)
    while i < n:
        two = fmt[i:i + 2]
        if two in ("--", "-."):
            ls = two
            i += 2
            continue
        ch = fmt[i]
        if ch in "-:":
            ls = ch
            i += 1
            continue
        if ch in ".,ov^<>sp*+xDd|_":
            marker = ch
            i += 1
            continue
        if ch == "C" and i + 1 < n and fmt[i + 1].isdigit():
            color = fmt[i:i + 2]
            i += 2
            continue
        if ch in "bgrcmykw":
            color = ch
            i += 1
            continue
        raise ValueError("unrecognised character %r in format string %r" % (ch, fmt))
    if marker is not None and ls is None:
        ls = ""
    return color, ls, marker


# --------------------------------------------------------------------------
# tick location


def _nice_step(span, target):
    """The 1/2/5-times-a-power-of-ten step closest to span/target."""
    if span <= 0 or not _finite(span):
        return 1.0
    raw = span / max(1, target)
    mag = 10.0 ** math.floor(math.log10(raw))
    norm = raw / mag
    if norm <= 1.5:
        step = 1.0
    elif norm <= 3.0:
        step = 2.0
    elif norm <= 7.0:
        step = 5.0
    else:
        step = 10.0
    return step * mag


def _snap(v, step):
    """Round off the float dust so 0.30000000000000004 prints as 0.3."""
    if step <= 0:
        return v
    d = 2 - int(math.floor(math.log10(step)))
    if d < 0:
        d = 0
    elif d > 12:
        d = 12
    return round(v, d)


def _linear_ticks(lo, hi, target):
    step = _nice_step(hi - lo, target)
    first = math.ceil(lo / step - 1e-9)
    out = []
    k = 0
    while k < 200:
        v = (first + k) * step
        if v > hi + step * 1e-9:
            break
        out.append(_snap(v, step))
        k += 1
    return out, step


def _log_ticks(lo, hi):
    """Decade ticks between two positive data values, subdivided when sparse."""
    e0 = int(math.floor(math.log10(lo)))
    e1 = int(math.ceil(math.log10(hi)))
    decades = e1 - e0
    if decades <= 1:
        mults = (1, 2, 3, 5)
        stride = 1
    elif decades == 2:
        mults = (1, 3)
        stride = 1
    elif decades <= 8:
        mults = (1,)
        stride = 1
    else:
        mults = (1,)
        stride = (decades + 7) // 8
    out = []
    e = e0
    while e <= e1 and len(out) < 200:
        base = 10.0 ** e
        for m in mults:
            v = base * m
            if lo * (1 - 1e-9) <= v <= hi * (1 + 1e-9):
                out.append(v)
        e += stride
    return out


def _fmt_tick(v, step):
    """Format a tick value tersely enough for a 6-pixel-wide font."""
    if v == 0:
        return "0"
    a = abs(v)
    if a >= 1e5 or a < 1e-4:
        s = "%.3g" % v
        # '1.2e+06' wastes three characters on a screen this narrow.
        s = s.replace("e+0", "e").replace("e-0", "e-").replace("e+", "e")
        return s
    d = 0
    s = abs(step)
    while s < 1 and d < 8:
        s *= 10
        d += 1
    txt = ("%." + str(d) + "f") % v
    if "." in txt:
        txt = txt.rstrip("0").rstrip(".")
    return txt if txt not in ("", "-", "-0") else "0"


# --------------------------------------------------------------------------
# artists
#
# Every plotting call appends one of these to an Axes.  An artist keeps its
# data in data coordinates and converts at draw time, so changing the limits
# or the scale after plotting redraws correctly instead of re-rasterising
# stale pixels.
#
# `data_bounds()` returns `(xb, yb)` where each half is `(min, max, smallest
# positive)` or None.  The smallest positive is what a log axis autoscales
# from; carrying it here means one pass over the data instead of a second one
# per axis.


class _Artist:
    label = None

    def data_bounds(self):
        return None

    def draw(self, ax):
        pass

    def legend_style(self):
        """(linestyle, linewidth, color, marker, markersize) or None."""
        return None


class _Line(_Artist):
    def __init__(self, x, y, color, ls, lw, marker, ms, mfc, label, fillstyle=True):
        self.x = x
        self.y = y
        self.color = color
        self.ls = ls
        self.lw = lw
        self.marker = marker
        self.ms = ms
        self.mfc = mfc
        self.label = label
        self.fillstyle = fillstyle

    def data_bounds(self):
        return (_bounds1(self.x), _bounds1(self.y))

    def draw(self, ax):
        box = ax._clipbox
        pts = []
        for i in range(len(self.x)):
            pts.append(ax._px(self.x[i], self.y[i]))
        _polyline(pts, self.color, self.lw, self.ls, box)
        if self.marker:
            mfc = self.color if self.mfc is None else self.mfc
            for p in pts:
                if p is not None and _inside(p[0], p[1], box):
                    _marker(p[0], p[1], self.marker, self.ms, mfc, self.fillstyle)

    def legend_style(self):
        return (self.ls, self.lw, self.color, self.marker, self.ms)


class _Bars(_Artist):
    """Axis-aligned filled rectangles in data coordinates.

    bar(), barh() and hist() all land here; only the geometry differs.
    """

    def __init__(self, rects, color, edgecolor, lw, label):
        self.rects = rects          # list of (x0, x1, y0, y1)
        self.color = color
        self.edgecolor = edgecolor
        self.lw = lw
        self.label = label

    def data_bounds(self):
        xs = []
        ys = []
        for r in self.rects:
            xs.append(r[0])
            xs.append(r[1])
            ys.append(r[2])
            ys.append(r[3])
        return (_bounds1(xs), _bounds1(ys))

    def draw(self, ax):
        box = ax._clipbox
        for x0, x1, y0, y1 in self.rects:
            a = ax._px(x0, y0)
            b = ax._px(x1, y1)
            if a is None or b is None:
                continue
            px0 = int(min(a[0], b[0]))
            px1 = int(max(a[0], b[0]))
            py0 = int(min(a[1], b[1]))
            py1 = int(max(a[1], b[1]))
            # A bar whose value rounds to zero height still deserves a line,
            # otherwise an empty histogram bin looks like missing data.
            if px1 == px0:
                px1 = px0 + 1
            if py1 == py0:
                py1 = py0 + 1
            c = _clip_rect(px0, py0, px1, py1, box)
            if c is None:
                continue
            if self.color is not None:
                tulip.bg_rect(c[0], c[1], c[2] - c[0] + 1, c[3] - c[1] + 1,
                              self.color, 1)
            if self.edgecolor is not None and self.lw > 0:
                # Draw the outline from the unclipped rect so that edges which
                # fall outside the axes stay outside instead of being drawn on
                # the clip boundary.
                _seg(px0, py0, px1, py0, self.edgecolor, self.lw, box)
                _seg(px1, py0, px1, py1, self.edgecolor, self.lw, box)
                _seg(px1, py1, px0, py1, self.edgecolor, self.lw, box)
                _seg(px0, py1, px0, py0, self.edgecolor, self.lw, box)

    def legend_style(self):
        return ("bar", self.lw, self.color, None, 0)


class _Fill(_Artist):
    """fill_between / fill_betweenx, rasterised a pixel column (or row) at a time.

    Scanning by destination pixel rather than tessellating into triangles keeps
    it correct for non-monotonic boundaries and makes clipping free.
    """

    def __init__(self, t, a, b, color, label, vertical=True):
        self.t = t
        self.a = a
        self.b = b
        self.color = color
        self.label = label
        self.vertical = vertical

    def data_bounds(self):
        vals = list(self.a) + list(self.b)
        if self.vertical:
            return (_bounds1(self.t), _bounds1(vals))
        return (_bounds1(vals), _bounds1(self.t))

    def draw(self, ax):
        box = ax._clipbox
        if self.color is None or len(self.t) < 2:
            return
        n = len(self.t)
        for i in range(n - 1):
            if self.vertical:
                p0 = ax._px(self.t[i], self.a[i])
                p1 = ax._px(self.t[i], self.b[i])
                q0 = ax._px(self.t[i + 1], self.a[i + 1])
                q1 = ax._px(self.t[i + 1], self.b[i + 1])
            else:
                p0 = ax._px(self.a[i], self.t[i])
                p1 = ax._px(self.b[i], self.t[i])
                q0 = ax._px(self.a[i + 1], self.t[i + 1])
                q1 = ax._px(self.b[i + 1], self.t[i + 1])
            if p0 is None or p1 is None or q0 is None or q1 is None:
                continue
            self._span(p0, p1, q0, q1, box)

    def _span(self, p0, p1, q0, q1, box):
        """Fill the quad p0-p1-q1-q0 by sweeping the axis it is a function of."""
        if self.vertical:
            s0, s1 = p0[0], q0[0]
            lo0, hi0 = p0[1], p1[1]
            lo1, hi1 = q0[1], q1[1]
        else:
            s0, s1 = p0[1], q0[1]
            lo0, hi0 = p0[0], p1[0]
            lo1, hi1 = q0[0], q1[0]
        if s1 < s0:
            s0, s1 = s1, s0
            lo0, lo1 = lo1, lo0
            hi0, hi1 = hi1, hi0
        steps = int(s1) - int(s0)
        if steps < 1:
            steps = 1
        for k in range(steps + 1):
            f = k / steps
            s = s0 + (s1 - s0) * f
            lo = lo0 + (lo1 - lo0) * f
            hi = hi0 + (hi1 - hi0) * f
            if hi < lo:
                lo, hi = hi, lo
            if self.vertical:
                c = _clip_rect(int(s), int(lo), int(s), int(hi), box)
            else:
                c = _clip_rect(int(lo), int(s), int(hi), int(s), box)
            if c is not None:
                tulip.bg_rect(c[0], c[1], c[2] - c[0] + 1, c[3] - c[1] + 1,
                              self.color, 1)

    def legend_style(self):
        return ("bar", 0, self.color, None, 0)


class _Span(_Artist):
    """axhline / axvline / axhspan / axvspan.

    `lo` and `hi` are the extent along the artist's own axis (equal for a
    line); `frac0`/`frac1` are the extent across the other axis in axes
    fraction, which is how upstream spells the partial-width case.
    """

    def __init__(self, horizontal, lo, hi, frac0, frac1,
                 color, ls, lw, filled, label):
        self.horizontal = horizontal
        self.lo = lo
        self.hi = hi
        self.frac0 = frac0
        self.frac1 = frac1
        self.color = color
        self.ls = ls
        self.lw = lw
        self.filled = filled
        self.label = label

    def data_bounds(self):
        b = _bounds1((self.lo, self.hi))
        return (None, b) if self.horizontal else (b, None)

    def draw(self, ax):
        box = ax._clipbox
        bx, by, bw, bh = ax._box
        if self.horizontal:
            a = ax._py(self.lo)
            b = ax._py(self.hi)
            if a is None or b is None:
                return
            x0 = bx + bw * self.frac0
            x1 = bx + bw * self.frac1
            if self.filled:
                c = _clip_rect(int(x0), int(min(a, b)), int(x1), int(max(a, b)), box)
                if c is not None:
                    tulip.bg_rect(c[0], c[1], c[2] - c[0] + 1, c[3] - c[1] + 1,
                                  self.color, 1)
            else:
                _polyline([(x0, a), (x1, a)], self.color, self.lw, self.ls, box)
        else:
            a = ax._px_only(self.lo)
            b = ax._px_only(self.hi)
            if a is None or b is None:
                return
            y0 = by + bh * (1.0 - self.frac1)
            y1 = by + bh * (1.0 - self.frac0)
            if self.filled:
                c = _clip_rect(int(min(a, b)), int(y0), int(max(a, b)), int(y1), box)
                if c is not None:
                    tulip.bg_rect(c[0], c[1], c[2] - c[0] + 1, c[3] - c[1] + 1,
                                  self.color, 1)
            else:
                _polyline([(a, y0), (a, y1)], self.color, self.lw, self.ls, box)

    def legend_style(self):
        if self.filled:
            return ("bar", 0, self.color, None, 0)
        return (self.ls, self.lw, self.color, None, 0)


class _ErrorBars(_Artist):
    def __init__(self, x, y, xerr, yerr, color, lw, capsize):
        self.x = x
        self.y = y
        self.xerr = xerr
        self.yerr = yerr
        self.color = color
        self.lw = lw
        self.capsize = capsize

    def data_bounds(self):
        xs = list(self.x)
        ys = list(self.y)
        if self.xerr:
            for i in range(len(self.x)):
                xs.append(self.x[i] - self.xerr[i])
                xs.append(self.x[i] + self.xerr[i])
        if self.yerr:
            for i in range(len(self.y)):
                ys.append(self.y[i] - self.yerr[i])
                ys.append(self.y[i] + self.yerr[i])
        return (_bounds1(xs), _bounds1(ys))

    def draw(self, ax):
        box = ax._clipbox
        cap = self.capsize
        for i in range(len(self.x)):
            if self.yerr:
                a = ax._px(self.x[i], self.y[i] - self.yerr[i])
                b = ax._px(self.x[i], self.y[i] + self.yerr[i])
                if a and b:
                    _seg(a[0], a[1], b[0], b[1], self.color, self.lw, box)
                    if cap:
                        _seg(a[0] - cap, a[1], a[0] + cap, a[1], self.color, self.lw, box)
                        _seg(b[0] - cap, b[1], b[0] + cap, b[1], self.color, self.lw, box)
            if self.xerr:
                a = ax._px(self.x[i] - self.xerr[i], self.y[i])
                b = ax._px(self.x[i] + self.xerr[i], self.y[i])
                if a and b:
                    _seg(a[0], a[1], b[0], b[1], self.color, self.lw, box)
                    if cap:
                        _seg(a[0], a[1] - cap, a[0], a[1] + cap, self.color, self.lw, box)
                        _seg(b[0], b[1] - cap, b[0], b[1] + cap, self.color, self.lw, box)


class _Text(_Artist):
    def __init__(self, x, y, s, color, font, ha, va, axes_fraction=False):
        self.x = x
        self.y = y
        self.s = s
        self.color = color
        self.font = font
        self.ha = ha
        self.va = va
        self.axes_fraction = axes_fraction

    def data_bounds(self):
        # Upstream does not autoscale to text either; a label near the edge
        # should not push the data around.
        return None

    def draw(self, ax):
        if self.axes_fraction:
            bx, by, bw, bh = ax._box
            px = bx + bw * self.x
            py = by + bh * (1.0 - self.y)
        else:
            p = ax._px(self.x, self.y)
            if p is None:
                return
            px, py = p
        if not _inside(px, py, ax._clipbox):
            return
        _draw_text(self.s, px, py, self.color, self.font, self.ha, self.va)


class _Arrow(_Artist):
    def __init__(self, x, y, dx, dy, color, head_width, head_length, lw):
        self.x = x
        self.y = y
        self.dx = dx
        self.dy = dy
        self.color = color
        self.head_width = head_width
        self.head_length = head_length
        self.lw = lw

    def data_bounds(self):
        return (_bounds1((self.x, self.x + self.dx)),
                _bounds1((self.y, self.y + self.dy)))

    def draw(self, ax):
        box = ax._clipbox
        tail = ax._px(self.x, self.y)
        tip = ax._px(self.x + self.dx, self.y + self.dy)
        if tail is None or tip is None:
            return
        _draw_arrow(tail, tip, self.color, self.lw,
                    self.head_width, self.head_length, box)


def _draw_arrow(tail, tip, color, lw, head_w, head_l, box):
    """Shaft plus a filled triangular head, sized in pixels."""
    dx = tip[0] - tail[0]
    dy = tip[1] - tail[1]
    length = math.sqrt(dx * dx + dy * dy)
    if length < 1:
        return
    ux = dx / length
    uy = dy / length
    if head_l > length:
        head_l = length
    bx = tip[0] - ux * head_l
    by = tip[1] - uy * head_l
    _seg(tail[0], tail[1], bx, by, color, lw, box)
    # Perpendicular, for the two base corners of the head.
    hx = -uy * head_w * 0.5
    hy = ux * head_w * 0.5
    if _inside(tip[0], tip[1], box):
        tulip.bg_triangle(int(tip[0]), int(tip[1]),
                          int(bx + hx), int(by + hy),
                          int(bx - hx), int(by - hy), color, 1)


class _Image(_Artist):
    """imshow(), written a scanline at a time through tulip.bg_bitmap().

    One bg_bitmap call per destination row: building a whole-rect bytearray
    would be a second copy of the image in RAM, and one call per pixel would
    spend the whole budget in the Python-to-C boundary.
    """

    def __init__(self, data, extent, cmap, vmin, vmax, aspect, origin):
        self.data = data
        self.extent = extent            # (left, right, bottom, top)
        self.cmap = cmap
        self.vmin = vmin
        self.vmax = vmax
        self.aspect = aspect
        self.origin = origin

    def data_bounds(self):
        e = self.extent
        return (_bounds1((e[0], e[1])), _bounds1((e[2], e[3])))

    def draw(self, ax):
        rows, cols = _shape2(self.data)
        if rows < 1 or cols < 1:
            return
        e = self.extent
        a = ax._px(e[0], e[3])
        b = ax._px(e[1], e[2])
        if a is None or b is None:
            return
        dx0, dy0 = min(a[0], b[0]), min(a[1], b[1])
        dx1, dy1 = max(a[0], b[0]), max(a[1], b[1])
        if self.aspect == "equal":
            # Fit the source aspect inside the destination rect, centred.
            dw = dx1 - dx0 + 1
            dh = dy1 - dy0 + 1
            scale = min(dw / cols, dh / rows)
            w = int(cols * scale)
            h = int(rows * scale)
            dx0 += (dw - w) // 2
            dy0 += (dh - h) // 2
            dx1 = dx0 + w - 1
            dy1 = dy0 + h - 1
        clip = _clip_rect(dx0, dy0, dx1, dy1, ax._clipbox)
        if clip is None:
            return
        # bg_bitmap raises rather than clamping, so keep it inside the panel.
        sw, sh = _screen()
        cx0 = max(int(clip[0]), 0)
        cy0 = max(int(clip[1]), 0)
        cx1 = min(int(clip[2]), sw - 1)
        cy1 = min(int(clip[3]), sh - 1)
        if cx1 < cx0 or cy1 < cy0:
            return

        vmin, vmax = self._range()
        span = vmax - vmin
        inv = 0.0 if span == 0 else 1.0 / span
        cmap = self.cmap
        width = cx1 - cx0 + 1
        row_buf = bytearray(width)
        dw = dx1 - dx0 + 1
        dh = dy1 - dy0 + 1

        # Precompute the source column for every destination column: the inner
        # loop runs width*height times and this takes a divide out of it.
        col_of = [0] * width
        for i in range(width):
            sc = int((cx0 + i - dx0) * cols / dw)
            col_of[i] = cols - 1 if sc >= cols else (0 if sc < 0 else sc)

        last_src = -1
        for y in range(cy0, cy1 + 1):
            sr = int((y - dy0) * rows / dh)
            if sr >= rows:
                sr = rows - 1
            elif sr < 0:
                sr = 0
            if self.origin == "lower":
                sr = rows - 1 - sr
            # An upscaled image maps many destination rows onto one source
            # row, and the column mapping never changes -- so the row buffer
            # from last time is still exactly right.  Recompute only when the
            # source row actually moves.
            if sr != last_src:
                src_row = self.data[sr]
                for i in range(width):
                    pal = cmap((float(src_row[col_of[i]]) - vmin) * inv)
                    # 0x55 is bg_bitmap's transparency key; shifting one
                    # palette cell is invisible next to RGB332 quantisation
                    # and beats a hole in the image.
                    row_buf[i] = pal + 1 if pal == ALPHA_INDEX else pal
                last_src = sr
            tulip.bg_bitmap(cx0, y, width, 1, row_buf)

    def _range(self):
        if self.vmin is not None and self.vmax is not None:
            return (self.vmin, self.vmax)
        rows, cols = _shape2(self.data)
        lo = hi = None
        for j in range(rows):
            r = self.data[j]
            for i in range(cols):
                v = float(r[i])
                if not _finite(v):
                    continue
                if lo is None or v < lo:
                    lo = v
                if hi is None or v > hi:
                    hi = v
        if lo is None:
            lo, hi = 0.0, 1.0
        if self.vmin is not None:
            lo = self.vmin
        if self.vmax is not None:
            hi = self.vmax
        return (lo, hi if hi > lo else lo + 1.0)


def _shape2(a):
    """(rows, cols) for a ulab 2-D ndarray or a list of lists."""
    shape = getattr(a, "shape", None)
    if shape is not None and len(shape) == 2:
        return (shape[0], shape[1])
    return (len(a), len(a[0]))


class _Wedges(_Artist):
    """pie(), drawn as a triangle fan.

    Tulip has no arc primitive, so each wedge is a fan of filled triangles at
    a fixed angular step -- fine enough that the rim reads as a curve at any
    size a Tulip panel can show.
    """

    STEP_DEG = 3.0

    def __init__(self, fracs, colors, labels, autopct, radius, startangle,
                 edgecolor, font, textcolor):
        self.fracs = fracs
        self.colors = colors
        self.labels = labels
        self.autopct = autopct
        self.radius = radius
        self.startangle = startangle
        self.edgecolor = edgecolor
        self.font = font
        self.textcolor = textcolor

    def draw(self, ax):
        bx, by, bw, bh = ax._box
        cx = bx + bw * 0.5
        cy = by + bh * 0.5
        r = min(bw, bh) * 0.5 * self.radius
        ang = self.startangle
        for i, frac in enumerate(self.fracs):
            sweep = frac * 360.0
            self._wedge(cx, cy, r, ang, sweep, self.colors[i])
            mid = math.radians(ang + sweep * 0.5)
            if self.labels and i < len(self.labels) and self.labels[i]:
                lx = cx + math.cos(mid) * r * 1.08
                ly = cy - math.sin(mid) * r * 1.08
                ha = "left" if math.cos(mid) >= 0 else "right"
                _draw_text(self.labels[i], lx, ly, self.textcolor, self.font,
                           ha, "center")
            if self.autopct:
                tx = cx + math.cos(mid) * r * 0.6
                ty = cy - math.sin(mid) * r * 0.6
                _draw_text(self.autopct % (frac * 100.0), tx, ty,
                           self.textcolor, self.font, "center", "center")
            ang += sweep

    def _wedge(self, cx, cy, r, start, sweep, color):
        n = int(abs(sweep) / self.STEP_DEG) + 1
        a0 = math.radians(start)
        px = cx + math.cos(a0) * r
        py = cy - math.sin(a0) * r
        for k in range(1, n + 1):
            a = math.radians(start + sweep * k / n)
            qx = cx + math.cos(a) * r
            qy = cy - math.sin(a) * r
            tulip.bg_triangle(int(cx), int(cy), int(px), int(py),
                              int(qx), int(qy), color, 1)
            px, py = qx, qy
        if self.edgecolor is not None:
            a1 = math.radians(start + sweep)
            tulip.bg_line(int(cx), int(cy),
                          int(cx + math.cos(a0) * r), int(cy - math.sin(a0) * r),
                          self.edgecolor)
            tulip.bg_line(int(cx), int(cy),
                          int(cx + math.cos(a1) * r), int(cy - math.sin(a1) * r),
                          self.edgecolor)


class _Scatter(_Artist):
    """scatter() with per-point size and color.

    Kept apart from _Line because the per-point lists are the whole point of
    scatter; folding them into _Line would put two unused lists on every
    ordinary plot() call.
    """

    def __init__(self, x, y, sizes, colors, marker, edgecolor, label):
        self.x = x
        self.y = y
        self.sizes = sizes
        self.colors = colors
        self.marker = marker
        self.edgecolor = edgecolor
        self.label = label

    def data_bounds(self):
        return (_bounds1(self.x), _bounds1(self.y))

    def draw(self, ax):
        box = ax._clipbox
        n = len(self.x)
        for i in range(n):
            p = ax._px(self.x[i], self.y[i])
            if p is None or not _inside(p[0], p[1], box):
                continue
            _marker(p[0], p[1], self.marker, self.sizes[i], self.colors[i])
            if self.edgecolor is not None:
                _marker(p[0], p[1], self.marker, self.sizes[i],
                        self.edgecolor, False)

    def legend_style(self):
        return ("", 0, self.colors[0] if self.colors else 0,
                self.marker, self.sizes[0] if self.sizes else 7)


def _clampf(v):
    """Keep a pixel coordinate inside the int16 range the C primitives take."""
    if v != v:
        return 0.0
    if v > 30000.0:
        return 30000.0
    if v < -30000.0:
        return -30000.0
    return v


def _percentile(sorted_vals, q):
    """Linear-interpolation percentile, matching numpy's default method."""
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    k = (n - 1) * q
    f = int(k)
    c = f + 1
    if c >= n:
        return sorted_vals[n - 1]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


# --------------------------------------------------------------------------


class Axes:
    """One set of axes: a pixel box, a pair of scales, and a list of artists.

    Everything is recomputed at draw time from the data, so setting limits or
    labels after plotting works the way it does upstream.
    """

    def __init__(self, figure, rect):
        # `rect` is (left, bottom, width, height) in figure fraction with a
        # bottom-left origin -- upstream's add_axes convention.  The conversion
        # to the framebuffer's top-left origin happens in _layout().
        self.figure = figure
        self._rect = rect
        self.cla()

    def cla(self):
        """Drop every artist and reset the styling to the rcParams defaults."""
        self.artists = []
        self.title = None
        self.xlabel = None
        self.ylabel = None
        self._xlim = None
        self._ylim = None
        self._xscale = "linear"
        self._yscale = "linear"
        self._xticks = None
        self._yticks = None
        self._grid_x = bool(rcParams["axes.grid"])
        self._grid_y = bool(rcParams["axes.grid"])
        self._grid_kw = {}
        self._frame = True
        self._ticks_visible = True
        self._legend_kw = None
        self._cycle = 0
        self._aspect_equal = False
        self._invert_x = False
        self._invert_y = False
        self.facecolor = rcParams["axes.facecolor"]
        self._box = (0, 0, 1, 1)
        self._clipbox = (0, 0, 1, 1)
        self._xlog = False
        self._ylog = False
        self._sx = 1.0
        self._sy = 1.0
        return self

    # ---------------------------------------------------------------- style

    def _next_color(self):
        cyc = rcParams["axes.prop_cycle"]
        c = cyc[self._cycle % len(cyc)]
        self._cycle += 1
        return to_pal(c)

    # ------------------------------------------------------------ transform

    def _px(self, x, y):
        """Data point to pixel, or None when the point cannot be shown."""
        if x != x or y != y:
            return None
        if self._xlog:
            if x <= 0:
                return None
            x = math.log10(x)
        if self._ylog:
            if y <= 0:
                return None
            y = math.log10(y)
        bx, by, bw, bh = self._box
        px = bx + bw - 1 - (x - self._vx0) * self._sx if self._invert_x \
            else bx + (x - self._vx0) * self._sx
        py = by + (y - self._vy0) * self._sy if self._invert_y \
            else by + bh - 1 - (y - self._vy0) * self._sy
        return (_clampf(px), _clampf(py))

    def _px_only(self, x):
        if x != x:
            return None
        if self._xlog:
            if x <= 0:
                return None
            x = math.log10(x)
        bx, _by, bw, _bh = self._box
        if self._invert_x:
            return _clampf(bx + bw - 1 - (x - self._vx0) * self._sx)
        return _clampf(bx + (x - self._vx0) * self._sx)

    def _py(self, y):
        if y != y:
            return None
        if self._ylog:
            if y <= 0:
                return None
            y = math.log10(y)
        bx, by, bw, bh = self._box
        if self._invert_y:
            return _clampf(by + (y - self._vy0) * self._sy)
        return _clampf(by + bh - 1 - (y - self._vy0) * self._sy)

    # -------------------------------------------------------------- limits

    def _data_bounds(self):
        xb = yb = None
        for a in self.artists:
            b = a.data_bounds()
            if b is None:
                continue
            if b[0] is not None:
                xb = _merge(xb, b[0])
            if b[1] is not None:
                yb = _merge(yb, b[1])
        return xb, yb

    def _axis_view(self, lim, b, log, margin):
        """Turn user limits or data bounds into a (v0, v1) pair in view space."""
        if lim is not None:
            lo, hi = lim
        elif b is None:
            lo, hi = (1.0, 10.0) if log else (0.0, 1.0)
        else:
            lo, hi = b[0], b[1]
            if log:
                # A log axis cannot show zero or negatives.  Fall back to the
                # smallest positive value in the data rather than refusing.
                if lo <= 0:
                    lo = b[2] if b[2] is not None else 1.0
                if hi <= 0:
                    hi = lo * 10.0
        if log:
            if lo <= 0:
                lo = 1e-12
            if hi <= lo:
                hi = lo * 10.0
            v0 = math.log10(lo)
            v1 = math.log10(hi)
        else:
            v0, v1 = float(lo), float(hi)
        if v1 <= v0:
            # A constant series, or a single point: give it something to sit in.
            pad = abs(v0) * 0.05
            if pad == 0:
                pad = 0.5
            v0 -= pad
            v1 += pad
        elif lim is None and margin:
            pad = (v1 - v0) * margin
            v0 -= pad
            v1 += pad
        return v0, v1

    def _equalize(self):
        """Make one data unit the same number of pixels on both axes."""
        bw = self._box[2] - 1
        bh = self._box[3] - 1
        if bw < 1 or bh < 1:
            return
        sx = bw / (self._vx1 - self._vx0)
        sy = bh / (self._vy1 - self._vy0)
        s = min(sx, sy)
        cx = (self._vx0 + self._vx1) * 0.5
        cy = (self._vy0 + self._vy1) * 0.5
        hx = bw / (2 * s)
        hy = bh / (2 * s)
        self._vx0, self._vx1 = cx - hx, cx + hx
        self._vy0, self._vy1 = cy - hy, cy + hy

    # --------------------------------------------------------------- ticks

    def _tick_values(self, is_x):
        pinned = self._xticks if is_x else self._yticks
        v0, v1 = (self._vx0, self._vx1) if is_x else (self._vy0, self._vy1)
        log = self._xlog if is_x else self._ylog
        font = rcParams["xtick.labelsize" if is_x else "ytick.labelsize"]

        if pinned is not None:
            vals, labels = pinned
            if labels is None:
                step = _nice_step(abs(v1 - v0), max(1, len(vals)))
                labels = [_fmt_tick(v, step) for v in vals]
            return vals, labels, font
        if log:
            vals = _log_ticks(10.0 ** v0, 10.0 ** v1)
            return vals, [_fmt_tick(v, v) for v in vals], font

        # Aim for a tick roughly every 110 px across and 60 px down; that is
        # about what the 6x13 tick font needs to stay legible without crowding.
        extent = self._box[2] if is_x else self._box[3]
        target = int(extent // (110 if is_x else 60))
        if target < 2:
            target = 2
        elif target > 12:
            target = 12
        vals, step = _linear_ticks(v0, v1, target)
        return vals, [_fmt_tick(v, step) for v in vals], font

    # -------------------------------------------------------------- layout

    def _layout(self, xlabels, ylabels):
        """Place the plot box inside the axes rect, leaving room for the decor.

        Every margin here is measured, not guessed: the y tick labels are as
        wide as the widest string actually rendered in the actual font, so a
        plot of megabytes and a plot of percentages both come out tight.
        """
        fx, fy, fw, fh = self.figure._px_rect()
        l, b, w, h = self._rect
        ax0 = fx + l * fw
        ay0 = fy + (1.0 - b - h) * fh
        aw = w * fw
        ah = h * fh

        xfont = rcParams["xtick.labelsize"]
        yfont = rcParams["ytick.labelsize"]
        lfont = rcParams["axes.labelsize"]
        tfont = rcParams["axes.titlesize"]

        pad = 6
        left = right = top = bottom = pad

        if self._ticks_visible:
            wmax = 0
            for s in ylabels:
                v = _text_w(s, yfont)
                if v > wmax:
                    wmax = v
            left += rcParams["ytick.major.size"] + rcParams["ytick.major.pad"] + wmax
            bottom += (rcParams["xtick.major.size"] + rcParams["xtick.major.pad"]
                       + _font_cap(xfont))
            if xlabels:
                # The outermost x tick label is centred on the box edge, so half
                # of it hangs outside.
                right += _text_w(xlabels[-1], xfont) // 2
                over = _text_w(xlabels[0], xfont) // 2
                first_over = over - left
                if first_over > 0:
                    left += first_over
            if ylabels:
                top += _font_cap(yfont) // 2

        self._xlabel_h = 0
        if self.xlabel:
            self._xlabel_h = _font_h(lfont) + 2
            bottom += self._xlabel_h
        self._ylabel_w = 0
        if self.ylabel:
            # Drawn as a column of upright characters, so it is only as wide as
            # its widest glyph.
            for ch in self.ylabel:
                v = _text_w(ch, lfont)
                if v > self._ylabel_w:
                    self._ylabel_w = v
            self._ylabel_w += 3
            left += self._ylabel_w
        self._title_h = 0
        if self.title:
            self._title_h = _font_h(tfont) + 4
            top += self._title_h

        bw = int(aw - left - right)
        bh = int(ah - top - bottom)
        if bw < 8:
            bw = 8
        if bh < 8:
            bh = 8
        self._box = (int(ax0 + left), int(ay0 + top), bw, bh)
        self._axes_rect_px = (int(ax0), int(ay0), int(aw), int(ah))
        # Baselines for the decor, so _draw_labels() does not have to
        # rediscover the margins it just spent them on.
        self._title_base = int(ay0 + pad + _font_cap(tfont))
        self._xlabel_base = int(ay0 + ah - pad)
        self._ylabel_x = int(ax0 + pad + self._ylabel_w * 0.5)

    def _scale(self):
        bx, by, bw, bh = self._box
        dx = self._vx1 - self._vx0
        dy = self._vy1 - self._vy0
        self._sx = (bw - 1) / dx if dx else 1.0
        self._sy = (bh - 1) / dy if dy else 1.0
        self._clipbox = (bx, by, bx + bw - 1, by + bh - 1)

    def _prepare(self):
        self._xlog = self._xscale == "log"
        self._ylog = self._yscale == "log"
        xb, yb = self._data_bounds()
        margin = rcParams["axes.margin"]
        self._vx0, self._vx1 = self._axis_view(self._xlim, xb, self._xlog, margin)
        self._vy0, self._vy1 = self._axis_view(self._ylim, yb, self._ylog, margin)

        # Seed the box with the whole axes rect so the first tick-count
        # estimate has something to divide, then iterate: pass one sizes the
        # margins from provisional labels, pass two re-picks the ticks now that
        # the real box width is known.
        fx, fy, fw, fh = self.figure._px_rect()
        l, b, w, h = self._rect
        self._box = (int(fx + l * fw), int(fy + (1.0 - b - h) * fh),
                     int(w * fw), int(h * fh))
        xt = xlab = yt = ylab = None
        for _ in range(2):
            xt, xlab, _xf = self._tick_values(True)
            yt, ylab, _yf = self._tick_values(False)
            self._layout(xlab, ylab)
            self._scale()
            if self._aspect_equal:
                self._equalize()
                self._scale()
        return xt, xlab, yt, ylab

    # ---------------------------------------------------------------- draw

    def draw(self):
        xt, xlab, yt, ylab = self._prepare()
        bx, by, bw, bh = self._box

        face = to_pal(self.facecolor)
        if face is not None:
            tulip.bg_rect(bx, by, bw, bh, face, 1)

        if self._grid_x or self._grid_y:
            self._draw_grid(xt, yt)

        for a in self.artists:
            a.draw(self)

        self._draw_frame()
        if self._ticks_visible:
            self._draw_ticks(xt, xlab, yt, ylab)
        self._draw_labels()
        if self._legend_kw is not None:
            self._draw_legend()

    def _draw_grid(self, xt, yt):
        color = to_pal(self._grid_kw.get("color", rcParams["grid.color"]))
        ls = self._grid_kw.get("linestyle", rcParams["grid.linestyle"])
        lw = self._grid_kw.get("linewidth", rcParams["grid.linewidth"])
        box = self._clipbox
        if self._grid_x:
            for v in xt:
                px = self._px_only(v)
                if px is None:
                    continue
                _polyline([(px, box[1]), (px, box[3])], color, lw, ls, box)
        if self._grid_y:
            for v in yt:
                py = self._py(v)
                if py is None:
                    continue
                _polyline([(box[0], py), (box[2], py)], color, lw, ls, box)

    def _draw_frame(self):
        if not self._frame:
            return
        color = to_pal(rcParams["axes.edgecolor"])
        if color is None:
            return
        bx, by, bw, bh = self._box
        for i in range(max(1, int(rcParams["axes.linewidth"]))):
            tulip.bg_rect(bx + i, by + i, bw - 2 * i, bh - 2 * i, color, 0)

    def _draw_ticks(self, xt, xlab, yt, ylab):
        bx, by, bw, bh = self._box
        xcolor = to_pal(rcParams["xtick.color"])
        ycolor = to_pal(rcParams["ytick.color"])
        xfont = rcParams["xtick.labelsize"]
        yfont = rcParams["ytick.labelsize"]
        xsize = rcParams["xtick.major.size"]
        ysize = rcParams["ytick.major.size"]
        xpad = rcParams["xtick.major.pad"]
        ypad = rcParams["ytick.major.pad"]
        y_axis = by + bh - 1

        for i in range(len(xt)):
            px = self._px_only(xt[i])
            if px is None or px < bx - 0.5 or px > bx + bw - 0.5:
                continue
            px = int(px)
            tulip.bg_line(px, y_axis, px, y_axis + xsize, xcolor)
            _draw_text(xlab[i], px, y_axis + xsize + xpad + _font_cap(xfont),
                       xcolor, xfont, "center")

        for i in range(len(yt)):
            py = self._py(yt[i])
            if py is None or py < by - 0.5 or py > by + bh - 0.5:
                continue
            py = int(py)
            tulip.bg_line(bx - ysize, py, bx, py, ycolor)
            _draw_text(ylab[i], bx - ysize - ypad, py, ycolor, yfont,
                       "right", "center")

    def _draw_labels(self):
        bx, by, bw, bh = self._box
        if self.title:
            _draw_text(self.title, bx + bw // 2, self._title_base,
                       to_pal(rcParams["axes.titlecolor"]),
                       rcParams["axes.titlesize"], "center")
        color = to_pal(rcParams["axes.labelcolor"])
        font = rcParams["axes.labelsize"]
        if self.xlabel:
            _draw_text(self.xlabel, bx + bw // 2, self._xlabel_base,
                       color, font, "center")
        if self.ylabel:
            # Upright characters stacked downward.  Tulip draws left to right
            # only, and a column reads better than a horizontal label crammed
            # in above the axis.
            step = _font_h(font)
            y = by + bh // 2 - (len(self.ylabel) * step) // 2 + _font_cap(font)
            for ch in self.ylabel:
                _draw_text(ch, self._ylabel_x, y, color, font, "center")
                y += step

    def _legend_entries(self):
        out = []
        for a in self.artists:
            lab = a.label
            if not lab or lab.startswith("_"):
                continue
            st = a.legend_style()
            if st is not None:
                out.append((lab, st))
        return out

    def _draw_legend(self):
        entries = self._legend_entries()
        if not entries:
            return
        kw = self._legend_kw
        font = kw.get("fontsize", rcParams["legend.fontsize"])
        loc = kw.get("loc", rcParams["legend.loc"])
        face = to_pal(kw.get("facecolor", rcParams["legend.facecolor"]))
        edge = to_pal(kw.get("edgecolor", rcParams["legend.edgecolor"]))
        tcolor = to_pal(rcParams["text.color"])

        sample = 26
        gap = 6
        margin = 7
        row = _font_h(font) + 2
        tw = 0
        for lab, _ in entries:
            v = _text_w(lab, font)
            if v > tw:
                tw = v
        w = margin * 2 + sample + gap + tw
        h = margin * 2 + row * len(entries)

        bx, by, bw, bh = self._box
        inset = 6
        if "left" in loc:
            x = bx + inset
        elif "center" in loc and "upper" not in loc and "lower" not in loc:
            x = bx + (bw - w) // 2
        else:
            x = bx + bw - w - inset
        if "lower" in loc:
            y = by + bh - h - inset
        elif loc == "center":
            y = by + (bh - h) // 2
        else:
            y = by + inset

        if face is not None:
            tulip.bg_rect(x, y, w, h, face, 1)
        if edge is not None:
            tulip.bg_rect(x, y, w, h, edge, 0)

        box = (x, y, x + w - 1, y + h - 1)
        cy = y + margin + row // 2
        for lab, st in entries:
            ls, lw, color, marker, ms = st
            sx0 = x + margin
            sx1 = sx0 + sample
            if color is None:
                pass
            elif ls == "bar":
                tulip.bg_rect(sx0, cy - 4, sample, 9, color, 1)
            elif ls:
                _polyline([(sx0, cy), (sx1, cy)], color, lw, ls, box)
            if marker and color is not None:
                _marker((sx0 + sx1) // 2, cy, marker, ms, color)
            _draw_text(lab, sx1 + gap, cy, tcolor, font, "left", "center")
            cy += row

    # ------------------------------------------------------------ plotting

    def _line_style(self, fmt, kw):
        """Resolve a format string plus kwargs into concrete line properties."""
        fc, fls, fmk = _fmt_spec(fmt) if fmt else (None, None, None)
        color = kw.get("color", kw.get("c", fc))
        ls = kw.get("linestyle", kw.get("ls", fls))
        lw = kw.get("linewidth", kw.get("lw", rcParams["lines.linewidth"]))
        marker = kw.get("marker", fmk)
        ms = kw.get("markersize", kw.get("ms", rcParams["lines.markersize"]))
        mfc = kw.get("markerfacecolor", kw.get("mfc", None))
        if ls is None:
            ls = rcParams["lines.linestyle"]
        if marker is None:
            marker = rcParams["lines.marker"]
        color = self._next_color() if color is None else to_pal(color)
        return (color, ls, lw, marker, ms, to_pal(mfc))

    def plot(self, *args, **kw):
        """plot(y), plot(x, y), plot(x, y, fmt), and repeated groups of those."""
        args = list(args)
        label = kw.get("label")
        out = []
        while args:
            first = args.pop(0)
            second = None
            fmt = None
            if args and not isinstance(args[0], str):
                second = args.pop(0)
            if args and isinstance(args[0], str):
                fmt = args.pop(0)
            if second is None:
                y = _seq(first)
                x = [float(i) for i in range(len(y))]
            else:
                x = _seq(first)
                y = _seq(second)
            if len(x) != len(y):
                raise ValueError("x and y must be the same length (%d != %d)"
                                 % (len(x), len(y)))
            color, ls, lw, marker, ms, mfc = self._line_style(fmt, kw)
            ln = _Line(x, y, color, ls, lw, marker, ms, mfc,
                       label if not out else None)
            self.artists.append(ln)
            out.append(ln)
        return out

    def step(self, x, y=None, fmt=None, where="pre", **kw):
        """A staircase through the points, expanded into an ordinary polyline."""
        if y is None:
            y = _seq(x)
            x = [float(i) for i in range(len(y))]
        else:
            x = _seq(x)
            y = _seq(y)
        if len(x) != len(y):
            raise ValueError("x and y must be the same length")
        sx = []
        sy = []
        for i in range(len(x)):
            if i:
                if where == "post":
                    sx.append(x[i])
                    sy.append(y[i - 1])
                elif where == "mid":
                    mid = (x[i - 1] + x[i]) * 0.5
                    sx.append(mid)
                    sy.append(y[i - 1])
                    sx.append(mid)
                    sy.append(y[i])
                else:                       # 'pre'
                    sx.append(x[i - 1])
                    sy.append(y[i])
            sx.append(x[i])
            sy.append(y[i])
        color, ls, lw, marker, ms, mfc = self._line_style(fmt, kw)
        ln = _Line(sx, sy, color, ls, lw, None, ms, mfc, kw.get("label"))
        self.artists.append(ln)
        return [ln]

    def semilogx(self, *args, **kw):
        self._xscale = "log"
        return self.plot(*args, **kw)

    def semilogy(self, *args, **kw):
        self._yscale = "log"
        return self.plot(*args, **kw)

    def loglog(self, *args, **kw):
        self._xscale = "log"
        self._yscale = "log"
        return self.plot(*args, **kw)

    def scatter(self, x, y, s=None, c=None, marker="o", **kw):
        """Points with optional per-point size and color.

        `s` is a marker diameter in pixels here, not an area in points squared;
        there is no point size on a bitmap font display.  `c` takes a color, a
        list of colors, or a list of numbers to run through `cmap`.
        """
        x = _seq(x)
        y = _seq(y)
        if len(x) != len(y):
            raise ValueError("x and y must be the same length")
        n = len(x)

        if s is None:
            sizes = [rcParams["lines.markersize"]] * n
        elif isinstance(s, (int, float)):
            sizes = [s] * n
        else:
            sizes = _seq(s)
            if len(sizes) < n:
                sizes = (sizes * n)[:n]

        numeric = c is not None and not isinstance(c, str) and \
            not isinstance(c, int) and _is_seq(c) and \
            len(c) == n and (n == 0 or isinstance(c[0], (int, float)))
        if numeric:
            vals = _seq(c)
            b = _bounds1(vals)
            vmin = kw.get("vmin", b[0] if b else 0.0)
            vmax = kw.get("vmax", b[1] if b else 1.0)
            cmap = get_cmap(kw.get("cmap"))
            span = (vmax - vmin) or 1.0
            colors = [cmap((v - vmin) / span) for v in vals]
        elif c is None:
            colors = [self._next_color()] * n
        elif _is_seq(c) and n and not isinstance(c[0], (int, float)):
            colors = [to_pal(v) for v in c]
            if len(colors) < n:
                colors = (colors * n)[:n]
        else:
            colors = [to_pal(c)] * n

        art = _Scatter(x, y, sizes, colors, marker,
                       to_pal(kw.get("edgecolor", kw.get("edgecolors"))),
                       kw.get("label"))
        self.artists.append(art)
        return art

    def _categorical(self, x, is_x=True):
        """Turn string categories into 0..n-1 and pin the tick labels to them."""
        if x and isinstance(x[0], str):
            labels = list(x)
            vals = [float(i) for i in range(len(labels))]
            if is_x:
                self._xticks = (vals, labels)
            else:
                self._yticks = (vals, labels)
            return vals
        return _seq(x)

    def bar(self, x, height, width=0.8, bottom=0.0, **kw):
        x = self._categorical(list(x) if _is_seq(x) else [x], True)
        height = _seq(height)
        if len(x) != len(height):
            raise ValueError("x and height must be the same length")
        base = _seq(bottom)
        if len(base) == 1:
            base = base * len(x)
        widths = _seq(width)
        if len(widths) == 1:
            widths = widths * len(x)
        rects = []
        for i in range(len(x)):
            half = widths[i] * 0.5
            rects.append((x[i] - half, x[i] + half, base[i], base[i] + height[i]))
        color = kw.get("color")
        art = _Bars(rects,
                    self._next_color() if color is None else to_pal(color),
                    to_pal(kw.get("edgecolor")),
                    kw.get("linewidth", rcParams["patch.linewidth"]),
                    kw.get("label"))
        self.artists.append(art)
        return art

    def barh(self, y, width, height=0.8, left=0.0, **kw):
        y = self._categorical(list(y) if _is_seq(y) else [y], False)
        width = _seq(width)
        if len(y) != len(width):
            raise ValueError("y and width must be the same length")
        base = _seq(left)
        if len(base) == 1:
            base = base * len(y)
        heights = _seq(height)
        if len(heights) == 1:
            heights = heights * len(y)
        rects = []
        for i in range(len(y)):
            half = heights[i] * 0.5
            rects.append((base[i], base[i] + width[i], y[i] - half, y[i] + half))
        color = kw.get("color")
        art = _Bars(rects,
                    self._next_color() if color is None else to_pal(color),
                    to_pal(kw.get("edgecolor")),
                    kw.get("linewidth", rcParams["patch.linewidth"]),
                    kw.get("label"))
        self.artists.append(art)
        return art

    def hist(self, x, bins=10, range=None, density=False, cumulative=False, **kw):
        """Returns (counts, edges, artist), matching upstream's (n, bins, patches)."""
        vals = sorted(v for v in _seq(x) if _finite(v))
        if not vals:
            raise ValueError("hist() got no finite values")
        if isinstance(bins, int):
            if bins < 1:
                raise ValueError("hist() needs at least one bin")
            lo, hi = range if range else (vals[0], vals[-1])
            if hi <= lo:
                lo, hi = lo - 0.5, lo + 0.5
            edges = [lo + (hi - lo) * k / bins for k in _RANGE(bins + 1)]
        else:
            edges = sorted(_seq(bins))
            if len(edges) < 2:
                raise ValueError("hist() needs at least two bin edges")

        nb = len(edges) - 1
        counts = [0.0] * nb
        for v in vals:
            if v < edges[0] or v > edges[-1]:
                continue
            # Bins are half-open except the last, which closes -- as upstream.
            k = nb - 1
            for j in _RANGE(nb):
                if v < edges[j + 1]:
                    k = j
                    break
            counts[k] += 1.0
        total = 0.0
        for c in counts:
            total += c
        if density and total:
            for j in _RANGE(nb):
                w = edges[j + 1] - edges[j]
                counts[j] = counts[j] / (total * w) if w else 0.0
        if cumulative:
            run = 0.0
            for j in _RANGE(nb):
                run += counts[j]
                counts[j] = run

        rects = [(edges[j], edges[j + 1], 0.0, counts[j]) for j in _RANGE(nb)]
        color = kw.get("color")
        art = _Bars(rects,
                    self._next_color() if color is None else to_pal(color),
                    to_pal(kw.get("edgecolor", rcParams["patch.edgecolor"])),
                    kw.get("linewidth", rcParams["patch.linewidth"]),
                    kw.get("label"))
        self.artists.append(art)
        return counts, edges, art

    def boxplot(self, x, positions=None, widths=0.5, whis=1.5, showfliers=True, **kw):
        """Box-and-whisker for one series or a list of series.

        Returns a list of per-series dicts with the computed statistics, which
        is the part of upstream's return value that is actually useful here.
        """
        series = list(x)
        if series and isinstance(series[0], (int, float)):
            series = [series]
        series = [sorted(v for v in _seq(s) if _finite(v)) for s in series]
        if positions is None:
            positions = [float(i + 1) for i in range(len(series))]
        else:
            positions = _seq(positions)

        line_c = to_pal(kw.get("color", "k"))
        med_c = to_pal(kw.get("mediancolor", "C3"))
        box_c = to_pal(kw.get("boxcolor")) if kw.get("boxcolor") else None
        lw = kw.get("linewidth", 1)
        stats = []
        for i, s in enumerate(series):
            if not s:
                continue
            p = positions[i]
            half = widths * 0.5
            q1 = _percentile(s, 0.25)
            med = _percentile(s, 0.5)
            q3 = _percentile(s, 0.75)
            iqr = q3 - q1
            lo_fence = q1 - whis * iqr
            hi_fence = q3 + whis * iqr
            low = min(v for v in s if v >= lo_fence) if any(v >= lo_fence for v in s) else q1
            high = max(v for v in s if v <= hi_fence) if any(v <= hi_fence for v in s) else q3
            fliers = [v for v in s if v < low or v > high]

            self.artists.append(_Bars([(p - half, p + half, q1, q3)],
                                      box_c, line_c, lw, kw.get("label") if not i else None))
            self.artists.append(_Line([p - half, p + half], [med, med],
                                      med_c, "-", max(1, lw + 1), None, 0, None, None))
            self.artists.append(_Line([p, p], [q1, low], line_c, "-", lw, None, 0, None, None))
            self.artists.append(_Line([p, p], [q3, high], line_c, "-", lw, None, 0, None, None))
            self.artists.append(_Line([p - half * 0.5, p + half * 0.5], [low, low],
                                      line_c, "-", lw, None, 0, None, None))
            self.artists.append(_Line([p - half * 0.5, p + half * 0.5], [high, high],
                                      line_c, "-", lw, None, 0, None, None))
            if showfliers and fliers:
                self.artists.append(_Line([p] * len(fliers), fliers, line_c, "",
                                          lw, "o", 6, None, None, False))
            stats.append({"q1": q1, "med": med, "q3": q3,
                          "whislo": low, "whishi": high, "fliers": fliers,
                          "position": p})
        return stats

    def pie(self, x, labels=None, colors=None, autopct=None, startangle=0.0,
            radius=1.0, **kw):
        vals = [v for v in _seq(x) if _finite(v) and v > 0]
        total = 0.0
        for v in vals:
            total += v
        if total <= 0:
            raise ValueError("pie() needs at least one positive value")
        fracs = [v / total for v in vals]
        if colors is None:
            pals = [self._next_color() for _ in fracs]
        else:
            pals = [to_pal(c) for c in colors]
            while len(pals) < len(fracs):
                pals += pals
        art = _Wedges(fracs, pals, list(labels) if labels else None, autopct,
                      radius, startangle, to_pal(kw.get("edgecolor")),
                      kw.get("fontsize", rcParams["xtick.labelsize"]),
                      to_pal(rcParams["text.color"]))
        self.artists.append(art)
        # A pie has no meaningful axes; upstream hides them too.
        self._frame = False
        self._ticks_visible = False
        self._aspect_equal = False
        return art

    def errorbar(self, x, y, yerr=None, xerr=None, fmt="", **kw):
        x = _seq(x)
        y = _seq(y)
        if len(x) != len(y):
            raise ValueError("x and y must be the same length")
        n = len(x)

        def spread(e):
            if e is None:
                return None
            v = _seq(e)
            return v * n if len(v) == 1 else v

        color, ls, lw, marker, ms, mfc = self._line_style(fmt or None, kw)
        ecolor = to_pal(kw.get("ecolor")) if kw.get("ecolor") else color
        self.artists.append(_ErrorBars(x, y, spread(xerr), spread(yerr), ecolor,
                                       kw.get("elinewidth", 1),
                                       kw.get("capsize", 4)))
        ln = _Line(x, y, color, ls, lw, marker, ms, mfc, kw.get("label"))
        self.artists.append(ln)
        return ln

    def fill_between(self, x, y1, y2=0.0, **kw):
        x = _seq(x)
        a = _seq(y1)
        b = _seq(y2)
        if len(a) == 1:
            a = a * len(x)
        if len(b) == 1:
            b = b * len(x)
        color = kw.get("color", kw.get("facecolor"))
        art = _Fill(x, a, b,
                    self._next_color() if color is None else to_pal(color),
                    kw.get("label"), True)
        self.artists.append(art)
        return art

    def fill_betweenx(self, y, x1, x2=0.0, **kw):
        y = _seq(y)
        a = _seq(x1)
        b = _seq(x2)
        if len(a) == 1:
            a = a * len(y)
        if len(b) == 1:
            b = b * len(y)
        color = kw.get("color", kw.get("facecolor"))
        art = _Fill(y, a, b,
                    self._next_color() if color is None else to_pal(color),
                    kw.get("label"), False)
        self.artists.append(art)
        return art

    def axhline(self, y=0.0, xmin=0.0, xmax=1.0, **kw):
        return self._add_span(True, y, y, xmin, xmax, False, kw)

    def axvline(self, x=0.0, ymin=0.0, ymax=1.0, **kw):
        return self._add_span(False, x, x, ymin, ymax, False, kw)

    def axhspan(self, ymin, ymax, xmin=0.0, xmax=1.0, **kw):
        return self._add_span(True, ymin, ymax, xmin, xmax, True, kw)

    def axvspan(self, xmin, xmax, ymin=0.0, ymax=1.0, **kw):
        return self._add_span(False, xmin, xmax, ymin, ymax, True, kw)

    def _add_span(self, horizontal, lo, hi, f0, f1, filled, kw):
        color = kw.get("color", kw.get("facecolor"))
        art = _Span(horizontal, float(lo), float(hi), float(f0), float(f1),
                    self._next_color() if color is None else to_pal(color),
                    kw.get("linestyle", kw.get("ls", "-")),
                    kw.get("linewidth", kw.get("lw", rcParams["lines.linewidth"])),
                    filled, kw.get("label"))
        self.artists.append(art)
        return art

    def imshow(self, data, cmap=None, vmin=None, vmax=None, extent=None,
               aspect="auto", origin="upper", **kw):
        rows, cols = _shape2(data)
        if extent is None:
            # Upstream's pixel-centre convention: cell (0, 0) spans -0.5..0.5.
            extent = (-0.5, cols - 0.5, rows - 0.5, -0.5) if origin == "upper" \
                else (-0.5, cols - 0.5, -0.5, rows - 0.5)
        art = _Image(data, tuple(float(v) for v in extent), get_cmap(cmap),
                     vmin, vmax, aspect, origin)
        self.artists.append(art)
        # Row 0 at the top means the y axis runs downward, which is what
        # upstream means by origin='upper'.
        self._invert_y = origin == "upper"
        return art

    def arrow(self, x, y, dx, dy, **kw):
        color = kw.get("color", kw.get("fc", kw.get("facecolor")))
        art = _Arrow(float(x), float(y), float(dx), float(dy),
                     self._next_color() if color is None else to_pal(color),
                     kw.get("head_width", 12), kw.get("head_length", 16),
                     kw.get("linewidth", kw.get("lw", rcParams["lines.linewidth"])))
        self.artists.append(art)
        return art

    def text(self, x, y, s, **kw):
        art = _Text(float(x), float(y), str(s),
                    to_pal(kw.get("color", rcParams["text.color"])),
                    kw.get("fontsize", rcParams["xtick.labelsize"]),
                    kw.get("ha", kw.get("horizontalalignment", "left")),
                    kw.get("va", kw.get("verticalalignment", "baseline")),
                    kw.get("transform") == "axes")
        self.artists.append(art)
        return art

    def annotate(self, s, xy, xytext=None, arrowprops=None, **kw):
        if xytext is None:
            xytext = xy
        art = self.text(xytext[0], xytext[1], s, **kw)
        if arrowprops is not None:
            color = arrowprops.get("color", arrowprops.get("facecolor", "k"))
            self.artists.append(_Arrow(
                float(xytext[0]), float(xytext[1]),
                float(xy[0]) - float(xytext[0]), float(xy[1]) - float(xytext[1]),
                to_pal(color),
                arrowprops.get("head_width", 10),
                arrowprops.get("head_length", 12),
                arrowprops.get("linewidth", 1)))
        return art

    # -------------------------------------------------------------- setters

    def set_title(self, s, **kw):
        self.title = None if s is None else str(s)
        return self.title

    def set_xlabel(self, s, **kw):
        self.xlabel = None if s is None else str(s)
        return self.xlabel

    def set_ylabel(self, s, **kw):
        self.ylabel = None if s is None else str(s)
        return self.ylabel

    def set_xlim(self, left=None, right=None):
        if left is None and right is None:
            return self.get_xlim()
        if right is None and _is_seq(left):
            left, right = left[0], left[1]
        self._xlim = (float(left), float(right))
        return self._xlim

    def set_ylim(self, bottom=None, top=None):
        if bottom is None and top is None:
            return self.get_ylim()
        if top is None and _is_seq(bottom):
            bottom, top = bottom[0], bottom[1]
        self._ylim = (float(bottom), float(top))
        return self._ylim

    def get_xlim(self):
        if self._xlim is not None:
            return self._xlim
        xb, _ = self._data_bounds()
        v0, v1 = self._axis_view(None, xb, self._xscale == "log",
                                 rcParams["axes.margin"])
        return (10.0 ** v0, 10.0 ** v1) if self._xscale == "log" else (v0, v1)

    def get_ylim(self):
        if self._ylim is not None:
            return self._ylim
        _, yb = self._data_bounds()
        v0, v1 = self._axis_view(None, yb, self._yscale == "log",
                                 rcParams["axes.margin"])
        return (10.0 ** v0, 10.0 ** v1) if self._yscale == "log" else (v0, v1)

    def invert_xaxis(self):
        self._invert_x = not self._invert_x
        return self._invert_x

    def invert_yaxis(self):
        self._invert_y = not self._invert_y
        return self._invert_y

    def set_xscale(self, scale):
        if scale not in ("linear", "log"):
            raise ValueError("scale must be 'linear' or 'log'")
        self._xscale = scale
        return scale

    def set_yscale(self, scale):
        if scale not in ("linear", "log"):
            raise ValueError("scale must be 'linear' or 'log'")
        self._yscale = scale
        return scale

    def set_xticks(self, ticks, labels=None):
        self._xticks = None if ticks is None else \
            (_seq(ticks), [str(v) for v in labels] if labels is not None else None)
        return self._xticks

    def set_yticks(self, ticks, labels=None):
        self._yticks = None if ticks is None else \
            (_seq(ticks), [str(v) for v in labels] if labels is not None else None)
        return self._yticks

    def grid(self, visible=None, which="major", axis="both", **kw):
        if visible is None:
            visible = not (self._grid_x or self._grid_y)
        if axis in ("both", "x"):
            self._grid_x = bool(visible)
        if axis in ("both", "y"):
            self._grid_y = bool(visible)
        if kw:
            self._grid_kw.update(kw)
        return visible

    def legend(self, *args, **kw):
        if args and isinstance(args[0], str):
            kw.setdefault("loc", args[0])
        self._legend_kw = kw
        return kw

    def axis(self, *args, **kw):
        """axis(), axis('off'), axis('equal'), axis([x0, x1, y0, y1]), ..."""
        if not args:
            return self.get_xlim() + self.get_ylim()
        a = args[0]
        if isinstance(a, str):
            if a == "off":
                self._frame = False
                self._ticks_visible = False
            elif a == "on":
                self._frame = True
                self._ticks_visible = True
            elif a in ("equal", "scaled", "image"):
                self._aspect_equal = True
            elif a == "square":
                self._aspect_equal = True
            elif a == "tight":
                xb, yb = self._data_bounds()
                if xb:
                    self._xlim = (xb[0], xb[1])
                if yb:
                    self._ylim = (yb[0], yb[1])
            elif a == "auto":
                self._xlim = None
                self._ylim = None
                self._aspect_equal = False
            else:
                raise ValueError("unknown axis() option %r" % (a,))
            return self.get_xlim() + self.get_ylim()
        if len(a) == 4:
            self.set_xlim(a[0], a[1])
            self.set_ylim(a[2], a[3])
            return self.get_xlim() + self.get_ylim()
        raise ValueError("axis() takes four limits or a keyword")


# --------------------------------------------------------------------------
# figures


class Figure:
    """A rectangle of screen holding one or more Axes.

    The default figure is the whole panel.  `figsize` is honoured for code
    that carries it over from the desktop -- inches times `figure.dpi` -- but
    `rect=(x, y, w, h)` in pixels is the direct way to say it here.
    """

    def __init__(self, num, rect=None, facecolor=None):
        self.number = num
        sw, sh = _screen()
        self._rect = tuple(int(v) for v in rect) if rect else (0, 0, sw, sh)
        self.facecolor = rcParams["figure.facecolor"] if facecolor is None \
            else facecolor
        self.axes = []
        self._suptitle = None
        self._current = None

    def _px_rect(self):
        """The rect the axes may use, after the suptitle takes its band."""
        x, y, w, h = self._rect
        if self._suptitle:
            d = _font_h(rcParams["figure.titlesize"]) + 8
            return (x, y + d, w, h - d)
        return self._rect

    # ---------------------------------------------------------------- axes

    def add_axes(self, rect):
        ax = Axes(self, tuple(float(v) for v in rect))
        self.axes.append(ax)
        self._current = ax
        return ax

    def add_subplot(self, *args):
        nrows, ncols, index = _subplot_spec(args)
        rect = _subplot_rect(nrows, ncols, index)
        # Re-selecting the same cell returns the existing axes, as upstream.
        for ax in self.axes:
            if ax._rect == rect:
                self._current = ax
                return ax
        return self.add_axes(rect)

    def subplots(self, nrows=1, ncols=1, **kw):
        grid = []
        for r in range(nrows):
            row = [self.add_subplot(nrows, ncols, r * ncols + c + 1)
                   for c in range(ncols)]
            grid.append(row)
        self._current = grid[0][0]
        if nrows == 1 and ncols == 1:
            return grid[0][0]
        if nrows == 1:
            return grid[0]
        if ncols == 1:
            return [row[0] for row in grid]
        return grid

    def gca(self):
        if self._current is None:
            if self.axes:
                self._current = self.axes[0]
            else:
                self.add_axes((0.0, 0.0, 1.0, 1.0))
        return self._current

    def clf(self):
        self.axes = []
        self._current = None
        self._suptitle = None
        return self

    def suptitle(self, s, **kw):
        self._suptitle = None if s is None else str(s)
        return self._suptitle

    # --------------------------------------------------------------- draw

    def draw(self):
        _require_display()
        x, y, w, h = self._rect
        face = to_pal(self.facecolor)
        if face is not None:
            tulip.bg_rect(x, y, w, h, face, 1)
        if self._suptitle:
            font = rcParams["figure.titlesize"]
            _draw_text(self._suptitle, x + w // 2, y + 4 + _font_cap(font),
                       to_pal(rcParams["text.color"]), font, "center")
        if not self.axes:
            self.gca()
        for ax in self.axes:
            ax.draw()
        return self

    def savefig(self, fname, **kw):
        self.draw()
        if not hasattr(tulip, "int_screenshot"):
            raise RuntimeError("this Tulip build has no screenshot support")
        x, y, w, h = self._rect
        tulip.int_screenshot(str(fname), x, y, w, h)
        return fname


def _subplot_spec(args):
    """(nrows, ncols, index) from either three ints or the packed 221 form."""
    if len(args) == 1:
        v = int(args[0])
        if v < 111:
            raise ValueError("subplot() code %r must be a three digit number" % (v,))
        return (v // 100, (v // 10) % 10, v % 10)
    if len(args) == 3:
        return (int(args[0]), int(args[1]), int(args[2]))
    if not args:
        return (1, 1, 1)
    raise ValueError("subplot() takes one or three arguments")


def _subplot_rect(nrows, ncols, index):
    """Grid cell as a figure-fraction rect, bottom-left origin.

    The gaps are small because each Axes reserves its own decor space from
    measured text -- there is no need for the generous upstream defaults that
    have to guess at label sizes.
    """
    if index < 1 or index > nrows * ncols:
        raise ValueError("subplot index %d is outside a %dx%d grid"
                         % (index, nrows, ncols))
    i = index - 1
    row = i // ncols
    col = i % ncols
    gw = 0.03 if ncols > 1 else 0.0
    gh = 0.05 if nrows > 1 else 0.0
    w = (1.0 - gw * (ncols - 1)) / ncols
    h = (1.0 - gh * (nrows - 1)) / nrows
    return (col * (w + gw), 1.0 - (row + 1) * h - row * gh, w, h)


def _require_display():
    ready = getattr(tulip, "display_ready", None)
    if ready is not None and not ready():
        raise RuntimeError("the display is not running yet; nothing to plot on")


# --------------------------------------------------------------------------
# the pyplot state machine

_figures = {}
_current = None


def figure(num=None, figsize=None, facecolor=None, rect=None, **kw):
    """Select a figure by number, creating it if it does not exist."""
    global _current
    if isinstance(num, Figure):
        _current = num
        _figures[num.number] = num
        return num
    if num is None:
        num = 1
        while num in _figures:
            num += 1
    if num in _figures and figsize is None and rect is None:
        _current = _figures[num]
        return _current
    if rect is None and figsize is not None:
        dpi = rcParams["figure.dpi"]
        sw, sh = _screen()
        rect = (0, 0, min(sw, int(figsize[0] * dpi)), min(sh, int(figsize[1] * dpi)))
    fig = Figure(num, rect, facecolor)
    _figures[num] = fig
    _current = fig
    return fig


def gcf():
    """The current figure, created on demand."""
    return _current if _current is not None else figure()


def gca():
    """The current axes of the current figure, created on demand."""
    return gcf().gca()


def sca(ax):
    global _current
    _current = ax.figure
    ax.figure._current = ax
    return ax


def subplot(*args, **kw):
    return gcf().add_subplot(*args)


def subplots(nrows=1, ncols=1, **kw):
    fig = figure(**{k: v for k, v in kw.items()
                    if k in ("num", "figsize", "facecolor", "rect")})
    return fig, fig.subplots(nrows, ncols)


def axes(rect=None):
    return gcf().add_axes(rect if rect else (0.0, 0.0, 1.0, 1.0))


def clf():
    return gcf().clf()


def cla():
    return gca().cla()


def close(fig=None):
    """close(), close(num), close(fig) or close('all')."""
    global _current
    if fig == "all":
        _figures.clear()
        _current = None
        return
    if fig is None:
        fig = gcf()
    num = fig.number if isinstance(fig, Figure) else fig
    _figures.pop(num, None)
    if _current is not None and _current.number == num:
        _current = None


def show(*args, **kw):
    """Render the current figure to the screen.

    There is no event loop to block on, so this draws and returns, and it does
    not throw the figure away -- plot(); show(); plot(); show() accumulates.
    Call clf() to start a new plot.
    """
    return gcf().draw()


def draw():
    return gcf().draw()


def savefig(fname, **kw):
    return gcf().savefig(fname, **kw)


def pause(interval):
    """Draw, then wait.  There is nothing to service in between, but scripts
    written against upstream use it as a frame delay and that still works."""
    import time
    gcf().draw()
    time.sleep(interval)


def suptitle(s, **kw):
    return gcf().suptitle(s, **kw)


def tight_layout(**kw):
    """Accepted and ignored: the layout is already computed from measured text."""
    return None


def full_screen(on=True):
    """Take the whole screen for plotting, or hand it back to the REPL.

    Not a matplotlib function.  The REPL draws in the text frame buffer on top
    of the background layer, so a full-screen plot has your typing over it
    until you turn the text layer off.  You can still type blind -- run
    `plt.full_screen(False)` to bring it back.

    Handing it back is more than turning the text layer on again.  Where LVGL
    is an overlay (Tab5), the REPL's screen is transparent and the background
    plane -- the one the plot just painted white -- *is* its background, so
    restoring only the text would leave the REPL unreadable over the last
    figure.  Re-presenting the REPL screen is what puts its background back,
    and it is a no-op if some other app owns the screen right now.
    """
    if on:
        if hasattr(tulip, "tfb_stop"):
            tulip.tfb_stop()
        return True

    screen = None
    try:
        import ui
        screen = ui.repl_screen
    except (ImportError, AttributeError):
        pass
    # Only take the screen back if the REPL is what we borrowed it from.
    if screen is not None and getattr(screen, "active", False):
        screen.present()
    else:
        if hasattr(tulip, "tfb_start"):
            tulip.tfb_start()
        if hasattr(tulip, "tfb_update"):
            tulip.tfb_update()
    return False


# --------------------------------------------------------------------------
# the delegating shorthand -- every one of these is gca().<same name>


def plot(*args, **kw):
    return gca().plot(*args, **kw)


def step(*args, **kw):
    return gca().step(*args, **kw)


def scatter(*args, **kw):
    return gca().scatter(*args, **kw)


def bar(*args, **kw):
    return gca().bar(*args, **kw)


def barh(*args, **kw):
    return gca().barh(*args, **kw)


def hist(*args, **kw):
    return gca().hist(*args, **kw)


def boxplot(*args, **kw):
    return gca().boxplot(*args, **kw)


def pie(*args, **kw):
    return gca().pie(*args, **kw)


def errorbar(*args, **kw):
    return gca().errorbar(*args, **kw)


def fill_between(*args, **kw):
    return gca().fill_between(*args, **kw)


def fill_betweenx(*args, **kw):
    return gca().fill_betweenx(*args, **kw)


def axhline(*args, **kw):
    return gca().axhline(*args, **kw)


def axvline(*args, **kw):
    return gca().axvline(*args, **kw)


def axhspan(*args, **kw):
    return gca().axhspan(*args, **kw)


def axvspan(*args, **kw):
    return gca().axvspan(*args, **kw)


def imshow(*args, **kw):
    return gca().imshow(*args, **kw)


def arrow(*args, **kw):
    return gca().arrow(*args, **kw)


def text(*args, **kw):
    return gca().text(*args, **kw)


def annotate(*args, **kw):
    return gca().annotate(*args, **kw)


def title(s, **kw):
    return gca().set_title(s, **kw)


def xlabel(s, **kw):
    return gca().set_xlabel(s, **kw)


def ylabel(s, **kw):
    return gca().set_ylabel(s, **kw)


def grid(*args, **kw):
    return gca().grid(*args, **kw)


def legend(*args, **kw):
    return gca().legend(*args, **kw)


def axis(*args, **kw):
    return gca().axis(*args, **kw)


def xlim(*args):
    ax = gca()
    if not args:
        return ax.get_xlim()
    return ax.set_xlim(*args)


def ylim(*args):
    ax = gca()
    if not args:
        return ax.get_ylim()
    return ax.set_ylim(*args)


def xticks(ticks=None, labels=None):
    ax = gca()
    if ticks is None and labels is None:
        return ax._xticks
    return ax.set_xticks(ticks, labels)


def yticks(ticks=None, labels=None):
    ax = gca()
    if ticks is None and labels is None:
        return ax._yticks
    return ax.set_yticks(ticks, labels)


def xscale(scale):
    return gca().set_xscale(scale)


def yscale(scale):
    return gca().set_yscale(scale)


def semilogx(*args, **kw):
    return gca().semilogx(*args, **kw)


def semilogy(*args, **kw):
    return gca().semilogy(*args, **kw)


def loglog(*args, **kw):
    return gca().loglog(*args, **kw)
