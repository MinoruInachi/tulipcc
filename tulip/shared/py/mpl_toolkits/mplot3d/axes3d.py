# 3-D axes for Tulip's matplotlib.
#
# Made by fig.add_subplot(projection='3d') (or plt.subplot / plt.axes /
# subplots(subplot_kw=...) with the same argument), the way upstream does it.
# Like the 2-D Axes this keeps its data in data coordinates and projects at
# draw time, so limits, labels and the view can all be set after plotting.
#
# Supported: scatter, plot, plot_wireframe, plot_surface, set_xlim/ylim/zlim,
# set_xlabel/ylabel/zlabel, set_title, view_init, set_box_aspect, grid,
# legend.  The 2-D-only plot types raise NotImplementedError rather than
# drawing nonsense.
#
# The look follows mplot3d's defaults: elev=30, azim=-60, a 4:4:3 box, the
# three far panes shaded and gridded, and scatter points fading with depth
# (depthshade).  The box is scaled to fill the axes, which upstream does not
# do -- on a 1280x720 panel the space is worth more than the fidelity.
#
# Where the display has true color (Tab5) the panes and the depth shading use
# it; elsewhere they go through the RGB332 palette like every other color here,
# so a pane may come out white and the shading in a few coarse steps.

import math

import tulip

from matplotlib import rcParams
from matplotlib.colors import (to_pal, pal_to_rgb, rgb_to_pal, get_cmap,
                               _CMAP_STOPS)
from matplotlib.pyplot import (Axes, _seq, _bounds1, _merge, _finite, _is_seq,
                               _nice_step, _snap, _fmt_tick, _fmt_spec,
                               _draw_text, _text_w, _font_h, _font_cap,
                               _marker, _polyline, _font_arg, _pick_font)

# mplot3d's pane gray, nudged to one RGB565 holds exactly: 242 comes back
# from 5-6-5 bits as (247, 243, 247), visibly pink.
_PANE_RGB = (246, 246, 246)
_TICK_LEN = 5
_TICK_PAD = 4
_LABEL_GAP = 8
_SHADES = 16            # depth buckets for depthshade and draw order

_truecolor = None


def _has_truecolor():
    """True where bg_* calls take (r, g, b) tuples -- the Tab5's RGB565 BG."""
    global _truecolor
    if _truecolor is None:
        try:
            tulip.bg_pixel(tulip.screen_size()[0] + 4, 0, (255, 255, 255))
            _truecolor = True
        except (TypeError, ValueError):
            _truecolor = False
    return _truecolor


def _rgb_color(r, g, b):
    if _has_truecolor():
        return (int(r), int(g), int(b))
    return rgb_to_pal(r, g, b)


def _shade(pal, f, face):
    """Palette color `pal` faded toward the face color: f=1 is full strength."""
    r, g, b = pal_to_rgb(pal)
    fr, fg, fb = face
    return _rgb_color(fr + (r - fr) * f, fg + (g - fg) * f, fb + (b - fb) * f)


def _ticks(lo, hi, max_n):
    """Ticks at 1, 2, 2.5 or 5 times a power of ten, no more than max_n."""
    span = hi - lo
    if span <= 0 or not _finite(span):
        return [lo], 1.0
    base = _nice_step(span, max_n)
    mag = 10.0 ** math.floor(math.log10(base))
    for m in (1.0, 2.0, 2.5, 5.0, 10.0, 20.0, 25.0, 50.0):
        step = m * mag
        first = math.ceil(lo / step - 1e-9)
        last = math.floor(hi / step + 1e-9)
        if last - first + 1 <= max_n:
            break
    out = []
    k = first
    while k <= last:
        out.append(_snap(k * step, step))
        k += 1
    return out, step


def _fmt3(v, step):
    """A tick label with as many decimals as the step has, on every tick.

    mplot3d labels a 2.5 step -7.5, -5.0, ... 7.5; the 2-D formatter trims
    per value and, sized for 1-2-5 steps, would round 7.5 to 8.
    """
    a = abs(v)
    if a >= 1e5 or (a and a < 1e-4):
        return _fmt_tick(v, step)
    d = 0
    while d < 8:
        m = abs(step) * 10 ** d
        if abs(m - round(m)) < 1e-6:
            break
        d += 1
    txt = ("%." + str(d) + "f") % v
    if txt.startswith("-") and float(txt) == 0:
        txt = txt[1:]
    return txt


def _xyz(a):
    return _seq(a)


class _Scatter3D:
    def __init__(self, x, y, z, sizes, colors, marker, edgecolor, depthshade,
                 label):
        self.x = x
        self.y = y
        self.z = z
        self.sizes = sizes
        self.colors = colors
        self.marker = marker
        self.edgecolor = edgecolor
        self.depthshade = depthshade
        self.label = label

    def bounds3(self):
        return (_bounds1(self.x), _bounds1(self.y), _bounds1(self.z))

    def draw(self, ax):
        n = len(self.x)
        pts = []
        dmin = dmax = None
        for i in range(n):
            p = ax._project(self.x[i], self.y[i], self.z[i])
            if p is None:
                continue
            d = p[2]
            if dmin is None or d < dmin:
                dmin = d
            if dmax is None or d > dmax:
                dmax = d
            pts.append((p[0], p[1], d, i))
        if not pts:
            return
        # Back to front, in depth buckets rather than a full sort: sorting a
        # few thousand nearly ordered tuples is seconds on MicroPython.
        span = (dmax - dmin) or 1.0
        buckets = [[] for _ in range(_SHADES)]
        for p in pts:
            k = int((p[2] - dmin) / span * _SHADES)
            buckets[k if k < _SHADES else _SHADES - 1].append(p)
        face = ax._face_rgb()
        cache = {}
        for k in range(_SHADES):
            # mplot3d fades the farthest points to 30% (its _zalpha).
            f = 0.3 + 0.7 * k / (_SHADES - 1) if self.depthshade else 1.0
            for x, y, _d, i in buckets[k]:
                c = self.colors[i]
                if self.depthshade:
                    key = (c, k)
                    sc = cache.get(key)
                    if sc is None:
                        sc = _shade(c, f, face)
                        cache[key] = sc
                    c = sc
                _marker(x, y, self.marker, self.sizes[i], c)
                if self.edgecolor is not None:
                    _marker(x, y, self.marker, self.sizes[i], self.edgecolor,
                            False)

    def legend_style(self):
        return ("", 0, self.colors[0] if self.colors else 0,
                self.marker, self.sizes[0] if self.sizes else 7)


class _Line3D:
    def __init__(self, x, y, z, color, ls, lw, marker, ms, mfc, label):
        self.x = x
        self.y = y
        self.z = z
        self.color = color
        self.ls = ls
        self.lw = lw
        self.marker = marker
        self.ms = ms
        self.mfc = mfc
        self.label = label

    def bounds3(self):
        return (_bounds1(self.x), _bounds1(self.y), _bounds1(self.z))

    def draw(self, ax):
        pts = []
        for i in range(len(self.x)):
            p = ax._project(self.x[i], self.y[i], self.z[i], clip=False)
            pts.append(None if p is None else (p[0], p[1]))
        _polyline(pts, self.color, self.lw, self.ls, ax._clipbox)
        if self.marker:
            mfc = self.color if self.mfc is None else self.mfc
            for i in range(len(pts)):
                if pts[i] is not None and \
                        ax._project(self.x[i], self.y[i], self.z[i]) is not None:
                    _marker(pts[i][0], pts[i][1], self.marker, self.ms, mfc)

    def legend_style(self):
        return (self.ls, self.lw, self.color, self.marker, self.ms)


def _grid(a):
    """A 2-D input (ulab ndarray, list of rows) as a list of float rows.

    None when `a` is one-dimensional.
    """
    rows = []
    for r in a:
        if not _is_seq(r):
            return None
        rows.append([float(v) for v in r])
    return rows


def _grids(X, Y, Z):
    """X, Y, Z as equal-shape row lists.

    Z must be 2-D.  X and Y may also be the 1-D vectors a meshgrid would have
    been made from (X along the columns, Y down the rows), which saves the
    meshgrid call.
    """
    z = _grid(Z)
    if not z or not z[0]:
        raise ValueError("Z must be a 2-D array")
    nr, nc = len(z), len(z[0])
    x = _grid(X)
    if x is None:
        xs = _seq(X)
        if len(xs) != nc:
            raise ValueError("1-D X needs one value per column of Z")
        x = [xs] * nr
    y = _grid(Y)
    if y is None:
        ys = _seq(Y)
        if len(ys) != nr:
            raise ValueError("1-D Y needs one value per row of Z")
        y = [[v] * nc for v in ys]
    for g in (x, y, z):
        if len(g) != nr or any(len(r) != nc for r in g):
            raise ValueError("X, Y and Z must have the same shape")
    return x, y, z


def _indices(n, stride, count):
    """Row or column indices to use, as upstream: every `stride`th, or enough
    for `count` of them, always ending on the last."""
    if stride is None:
        stride = max(1, int(math.ceil(n / max(1, count))))
    if stride <= 0:
        return []
    idx = list(range(0, n, stride))
    if idx[-1] != n - 1:
        idx.append(n - 1)
    return idx


def _bounds_grid(g):
    b = None
    for row in g:
        b = _merge(b, _bounds1(row))
    return b


def _cmap_rgb(cmap):
    """t in 0..1 -> (r, g, b), interpolated from the colormap's stops.

    The Colormap class answers with palette indices; on a true-color panel a
    shaded surface deserves the full gradient.
    """
    name = cmap.name
    key, rev = (name[:-2], True) if name.endswith("_r") else (name, False)
    stops = _CMAP_STOPS[key]
    if rev:
        stops = tuple(reversed(stops))
    n = len(stops) - 1

    def f(t):
        if t != t:
            t = 0.0
        t = 0.0 if t < 0 else (1.0 if t > 1 else t)
        u = t * n
        k = int(u)
        if k >= n:
            k = n - 1
        w = u - k
        a, b = stops[k], stops[k + 1]
        return (a[0] + (b[0] - a[0]) * w, a[1] + (b[1] - a[1]) * w,
                a[2] + (b[2] - a[2]) * w)
    return f


# mplot3d's default LightSource(azdeg=225, altdeg=19.4712), as a unit vector
# in data space: light from the -x, -y side, a little above.
_LIGHT = (-0.6666666, -0.6666666, 0.3333333)


def _first(t):
    return t[0]


class _Wireframe:
    def __init__(self, lines, bounds, color, lw, ls, label):
        self.lines = lines          # each a list of (x, y, z)
        self.bounds = bounds
        self.color = color
        self.lw = lw
        self.ls = ls
        self.label = label

    def bounds3(self):
        return self.bounds

    def draw(self, ax):
        for line in self.lines:
            pts = []
            for x, y, z in line:
                p = ax._project(x, y, z, clip=False)
                pts.append(None if p is None else (p[0], p[1]))
            _polyline(pts, self.color, self.lw, self.ls, ax._clipbox)

    def legend_style(self):
        return (self.ls, self.lw, self.color, None, 0)


class _Surface:
    """Quads drawn back to front (the painter's algorithm), as mplot3d does.

    Exactly one coloring applies: `facecolors` (one per grid cell), a colormap
    of each quad's mean z, or one color shaded by how each quad faces the
    light.
    """

    def __init__(self, x, y, z, ri, ci, color, shade, cmap, vmin, vmax,
                 facecolors, edgecolor, lw, label):
        self.x = x
        self.y = y
        self.z = z
        self.ri = ri
        self.ci = ci
        self.color = color
        self.shade = shade
        self.cmap = cmap
        self.vmin = vmin
        self.vmax = vmax
        self.facecolors = facecolors
        self.edgecolor = edgecolor
        self.lw = lw
        self.label = label
        self.bounds = (_bounds_grid(x), _bounds_grid(y), _bounds_grid(z))

    def bounds3(self):
        return self.bounds

    def _colorer(self):
        """A function (corners in data space, cell i, j) -> drawable color."""
        true = _has_truecolor()
        cache = {}
        if self.facecolors is not None:
            fc = self.facecolors
            return lambda c, i, j: to_pal(fc[i][j])
        if self.cmap is not None:
            lo = self.vmin
            hi = self.vmax
            span = (hi - lo) or 1.0
            if true:
                rgb = _cmap_rgb(self.cmap)

                def f(c, i, j):
                    t = ((c[0][2] + c[1][2] + c[2][2] + c[3][2]) / 4 - lo) / span
                    # 256 steps is finer than RGB565 can show anyway.
                    k = int(t * 255 + 0.5)
                    v = cache.get(k)
                    if v is None:
                        r, g, b = rgb(k / 255)
                        v = (int(r), int(g), int(b))
                        cache[k] = v
                    return v
                return f
            cm = self.cmap
            return lambda c, i, j: cm(
                ((c[0][2] + c[1][2] + c[2][2] + c[3][2]) / 4 - lo) / span)
        base = pal_to_rgb(self.color)
        if not self.shade:
            return lambda c, i, j: self.color
        L = _LIGHT

        def g(c, i, j):
            # mplot3d's _generate_normals for a quad: (p0 - p1) x (p1 - p2),
            # then brightness 0.3..1 from the light's angle of incidence.
            ax_ = c[0][0] - c[1][0]
            ay_ = c[0][1] - c[1][1]
            az_ = c[0][2] - c[1][2]
            bx_ = c[1][0] - c[2][0]
            by_ = c[1][1] - c[2][1]
            bz_ = c[1][2] - c[2][2]
            nx = ay_ * bz_ - az_ * by_
            ny = az_ * bx_ - ax_ * bz_
            nz = ax_ * by_ - ay_ * bx_
            n = math.sqrt(nx * nx + ny * ny + nz * nz)
            s = (nx * L[0] + ny * L[1] + nz * L[2]) / n if n else 1.0
            k = int((s + 1) * 32 + 0.5)        # 65 brightness steps
            v = cache.get(k)
            if v is None:
                f = 0.3 + 0.7 * k / 64
                v = _rgb_color(base[0] * f, base[1] * f, base[2] * f)
                cache[k] = v
            return v
        return g

    def draw(self, ax):
        x, y, z = self.x, self.y, self.z
        ri, ci = self.ri, self.ci
        P = []
        for i in ri:
            xi, yi, zi = x[i], y[i], z[i]
            P.append([ax._project(xi[j], yi[j], zi[j], clip=False) for j in ci])
        color_of = self._colorer()
        quads = []
        dmin = dmax = None
        for a in range(len(ri) - 1):
            i0, i1 = ri[a], ri[a + 1]
            for b in range(len(ci) - 1):
                p0, p1, p2, p3 = P[a][b], P[a][b + 1], P[a + 1][b + 1], P[a + 1][b]
                if p0 is None or p1 is None or p2 is None or p3 is None:
                    continue
                j0, j1 = ci[b], ci[b + 1]
                corners = ((x[i0][j0], y[i0][j0], z[i0][j0]),
                           (x[i0][j1], y[i0][j1], z[i0][j1]),
                           (x[i1][j1], y[i1][j1], z[i1][j1]),
                           (x[i1][j0], y[i1][j0], z[i1][j0]))
                d = p0[2] + p1[2] + p2[2] + p3[2]
                if dmin is None or d < dmin:
                    dmin = d
                if dmax is None or d > dmax:
                    dmax = d
                quads.append((d, p0, p1, p2, p3, color_of(corners, i0, j0)))
        if not quads:
            return
        # Back to front.  One bucket per quad and a sort of each (tiny)
        # bucket gives the exact order in linear time; a straight sort of a
        # grid's nearly ordered depths is quicksort's worst case, seconds here.
        n = len(quads)
        span = (dmax - dmin) or 1.0
        buckets = [None] * n
        for q in quads:
            k = int((q[0] - dmin) / span * (n - 1))
            bk = buckets[k]
            if bk is None:
                buckets[k] = [q]
            else:
                bk.append(q)
        edge = self.edgecolor
        tri = tulip.bg_triangle
        for bk in buckets:
            if bk is None:
                continue
            if len(bk) > 1:
                bk.sort(key=_first)
            for _d, p0, p1, p2, p3, c in bk:
                x0, y0 = int(p0[0]), int(p0[1])
                x1, y1 = int(p1[0]), int(p1[1])
                x2, y2 = int(p2[0]), int(p2[1])
                x3, y3 = int(p3[0]), int(p3[1])
                tri(x0, y0, x1, y1, x2, y2, c, 1)
                tri(x0, y0, x2, y2, x3, y3, c, 1)
                if edge is not None:
                    tulip.bg_line(x0, y0, x1, y1, edge)
                    tulip.bg_line(x1, y1, x2, y2, edge)
                    tulip.bg_line(x2, y2, x3, y3, edge)
                    tulip.bg_line(x3, y3, x0, y0, edge)

    def legend_style(self):
        return ("-", 6, self.color if self.color is not None else 0, None, 0)


def _unsupported(name):
    def f(self, *args, **kw):
        raise NotImplementedError("%s() is not available on 3-D axes" % name)
    return f


class Axes3D(Axes):
    name = "3d"

    def cla(self):
        super().cla()
        self.zlabel = None
        self._zlim = None
        self.elev = 30.0
        self.azim = -60.0
        self._aspect3 = (4.0, 4.0, 3.0)
        self._zoom = 1.0
        # mplot3d draws its grid unless told otherwise (rcParams axes3d.grid).
        g = bool(rcParams.get("axes3d.grid", True))
        self._grid_x = g
        self._grid_y = g
        return self

    # ------------------------------------------------------------ setters

    def set_zlabel(self, s, **kw):
        self.zlabel = None if s is None else str(s)
        self._text_kw["zlabel"] = kw
        return self.zlabel

    def set_zlim(self, bottom=None, top=None):
        if bottom is None and top is None:
            return self.get_zlim()
        if top is None and not isinstance(bottom, (int, float)):
            bottom, top = bottom[0], bottom[1]
        self._zlim = (float(bottom), float(top))
        return self._zlim

    def get_xlim(self):
        return self._limits()[0]

    def get_ylim(self):
        return self._limits()[1]

    def get_zlim(self):
        return self._limits()[2]

    def view_init(self, elev=None, azim=None, roll=None, **kw):
        if elev is not None:
            self.elev = float(elev)
        if azim is not None:
            self.azim = float(azim)

    def set_box_aspect(self, aspect, zoom=1):
        if aspect is not None:
            self._aspect3 = tuple(float(v) for v in aspect)
        self._zoom = float(zoom)

    # ------------------------------------------------------------- plots

    def scatter(self, xs, ys, zs=0, zdir="z", s=None, c=None, depthshade=True,
                marker="o", **kw):
        """Points in 3-D.  s, c, cmap, vmin/vmax and marker as in 2-D."""
        x = _xyz(xs)
        y = _xyz(ys)
        n = len(x)
        z = [float(zs)] * n if isinstance(zs, (int, float)) else _xyz(zs)
        if len(y) != n or len(z) != n:
            raise ValueError("xs, ys and zs must be the same length")
        # The 2-D scatter resolves sizes and colors; borrow it and take the
        # artist back off the list.
        art2 = Axes.scatter(self, x, y, s=s, c=c, marker=marker, **kw)
        self.artists.pop()
        art = _Scatter3D(x, y, z, art2.sizes, art2.colors, marker,
                         art2.edgecolor, depthshade, kw.get("label"))
        self.artists.append(art)
        return art

    def plot(self, xs, ys, *args, zdir="z", **kw):
        """plot(xs, ys, zs, fmt) -- one 3-D line."""
        args = list(args)
        zs = kw.pop("zs", 0)
        if args and not isinstance(args[0], str):
            zs = args.pop(0)
        fmt = args.pop(0) if args and isinstance(args[0], str) else None
        x = _xyz(xs)
        y = _xyz(ys)
        n = len(x)
        z = [float(zs)] * n if isinstance(zs, (int, float)) else _xyz(zs)
        if len(y) != n or len(z) != n:
            raise ValueError("xs, ys and zs must be the same length")
        color, ls, lw, marker, ms, mfc = self._line_style(fmt, kw)
        ln = _Line3D(x, y, z, color, ls, lw, marker, ms, mfc, kw.get("label"))
        self.artists.append(ln)
        return [ln]

    def plot_wireframe(self, X, Y, Z, rstride=None, cstride=None, rcount=50,
                       ccount=50, **kw):
        """Lines along the rows and columns of a grid surface.

        Each line keeps every point of its row or column; the strides only
        thin out how many lines there are, as upstream.  A stride of 0 drops
        that direction's lines.
        """
        x, y, z = _grids(X, Y, Z)
        nr, nc = len(z), len(z[0])
        lines = []
        for i in _indices(nr, rstride, rcount):
            lines.append([(x[i][j], y[i][j], z[i][j]) for j in range(nc)])
        for j in _indices(nc, cstride, ccount):
            lines.append([(x[i][j], y[i][j], z[i][j]) for i in range(nr)])
        color, ls, lw, _m, _ms, _mfc = self._line_style(None, kw)
        if "linewidth" not in kw and "lw" not in kw:
            lw = 1
        art = _Wireframe(lines, (_bounds_grid(x), _bounds_grid(y),
                                 _bounds_grid(z)), color, lw, ls,
                         kw.get("label"))
        self.artists.append(art)
        return art

    def plot_surface(self, X, Y, Z, rstride=None, cstride=None, rcount=50,
                     ccount=50, color=None, cmap=None, vmin=None, vmax=None,
                     shade=None, facecolors=None, **kw):
        """A filled grid surface.

        With `cmap` each quad takes the colormap's color for its mean z
        (vmin/vmax default to the data range); otherwise one color, shaded by
        how each quad faces mplot3d's default light unless shade=False.
        `edgecolor=` outlines the quads.  `alpha=` and `antialiased=` are
        accepted and ignored.
        """
        x, y, z = _grids(X, Y, Z)
        nr, nc = len(z), len(z[0])
        ri = _indices(nr, rstride, rcount)
        ci = _indices(nc, cstride, ccount)
        cm = None
        if cmap is not None and facecolors is None:
            cm = get_cmap(cmap)
            zb = _bounds_grid(z)
            if vmin is None:
                vmin = zb[0] if zb else 0.0
            if vmax is None:
                vmax = zb[1] if zb else 1.0
        col = None
        if cm is None and facecolors is None:
            col = self._next_color() if color is None else to_pal(color)
        if shade is None:
            shade = cm is None          # upstream: never shade a colormap
        edge = kw.get("edgecolor", kw.get("edgecolors"))
        art = _Surface(x, y, z, ri, ci, col, shade, cm, vmin, vmax, facecolors,
                       None if edge is None or edge == "none" else to_pal(edge),
                       kw.get("linewidth", kw.get("lw", 1)), kw.get("label"))
        self.artists.append(art)
        return art

    plot3D = plot
    scatter3D = scatter

    # --------------------------------------------------------- transform

    def _limits(self):
        xb = yb = zb = None
        for a in self.artists:
            b = a.bounds3()
            xb = _merge(xb, b[0])
            yb = _merge(yb, b[1])
            zb = _merge(zb, b[2])
        return (self._view(self._xlim, xb), self._view(self._ylim, yb),
                self._view(self._zlim, zb))

    @staticmethod
    def _view(lim, b):
        if lim is not None:
            return lim
        if b is None:
            return (0.0, 1.0)
        lo, hi = b[0], b[1]
        if hi <= lo:
            pad = abs(lo) * 0.05 or 0.5
            return (lo - pad, hi + pad)
        # mplot3d's autoscale margin is 1/48 of the span on each side.
        pad = (hi - lo) / 48.0
        return (lo - pad, hi + pad)

    def _setup_view(self):
        a, e = math.radians(self.azim), math.radians(self.elev)
        ca, sa, ce, se = math.cos(a), math.sin(a), math.cos(e), math.sin(e)
        # screen right, screen up, and toward the viewer, in box space
        self._R = (-sa, ca, 0.0)
        self._U = (-ca * se, -sa * se, ce)
        self._V = (ca * ce, sa * ce, se)
        m = max(self._aspect3)
        self._half = tuple(v / m * 0.5 for v in self._aspect3)

    def _norm(self, x, y, z):
        """Data -> box space, each axis centred with half-width _half[i]."""
        (x0, x1), (y0, y1), (z0, z1) = self._lims
        h = self._half
        return ((x - x0) / (x1 - x0) * 2 * h[0] - h[0],
                (y - y0) / (y1 - y0) * 2 * h[1] - h[1],
                (z - z0) / (z1 - z0) * 2 * h[2] - h[2])

    def _proj_box(self, u, v, w):
        R, U, V = self._R, self._U, self._V
        sx = u * R[0] + v * R[1]
        sy = u * U[0] + v * U[1] + w * U[2]
        d = u * V[0] + v * V[1] + w * V[2]
        return (self._cx + sx * self._scale, self._cy - sy * self._scale, d)

    def _project(self, x, y, z, clip=True):
        """Data point to (px, py, depth); None if hidden or outside the box."""
        if x != x or y != y or z != z:
            return None
        if clip:
            (x0, x1), (y0, y1), (z0, z1) = self._lims
            if not (min(x0, x1) <= x <= max(x0, x1) and
                    min(y0, y1) <= y <= max(y0, y1) and
                    min(z0, z1) <= z <= max(z0, z1)):
                return None
        u, v, w = self._norm(x, y, z)
        return self._proj_box(u, v, w)

    def _pt(self, u, v, w):
        p = self._proj_box(u, v, w)
        return (int(p[0]), int(p[1]))

    def _face_rgb(self):
        face = to_pal(self.facecolor)
        return pal_to_rgb(face) if face is not None else (255, 255, 255)

    # -------------------------------------------------------------- draw

    def _axis_ticks(self):
        """Tick values and labels for x, y, z, sized to the box on screen."""
        out = []
        for i in range(3):
            lo, hi = self._lims[i]
            # Roughly one tick per 45 px of the axis's on-screen length.
            a = [0.0, 0.0, 0.0]
            b = [0.0, 0.0, 0.0]
            a[i] = -self._half[i]
            b[i] = self._half[i]
            pa = self._proj_box(a[0], a[1], a[2])
            pb = self._proj_box(b[0], b[1], b[2])
            length = math.sqrt((pa[0] - pb[0]) ** 2 + (pa[1] - pb[1]) ** 2)
            n = max(3, min(10, int(length / 45)))
            vals, step = _ticks(min(lo, hi), max(lo, hi), n)
            out.append((vals, [_fmt3(v, step) for v in vals]))
        return out

    def _axis_edges(self):
        """Where each axis line runs, as (fixed box coords, outward vector).

        Each axis sits on an edge of the far panes' frame, as in mplot3d:
        x and y along the floor on the side nearest the viewer, z up the
        right-hand one of the two silhouette edges.
        """
        h = self._half
        V = self._V
        back = [(-h[i] if V[i] > 0 else h[i]) for i in range(3)]
        front = [-back[0], -back[1], -back[2]]
        edges = []
        # x: y at the front, z at the back (the floor when looking down)
        edges.append(((None, front[1], back[2]), (0.0, front[1], 0.0)))
        edges.append(((front[0], None, back[2]), (front[0], 0.0, 0.0)))
        # z: of the two vertical edges that are neither nearest nor farthest,
        # the one further right on screen.
        c1 = (front[0], back[1])
        c2 = (back[0], front[1])
        s1 = c1[0] * self._R[0] + c1[1] * self._R[1]
        s2 = c2[0] * self._R[0] + c2[1] * self._R[1]
        c = c1 if s1 >= s2 else c2
        edges.append(((c[0], c[1], None), (c[0], c[1], 0.0)))
        return back, edges

    def _fit(self, ticks):
        """Scale and centre the box so it and its labels fill the axes."""
        fx, fy, fw, fh = self.figure._px_rect()
        l, b, w, h = self._rect
        ax0 = fx + l * fw
        ay0 = fy + (1.0 - b - h) * fh
        aw = w * fw
        ah = h * fh
        tfont = self._label_style("title", self.title)[0]
        title_h = (_font_h(tfont) + 14) if self.title else 0
        tick_font = rcParams["xtick.labelsize"]
        tw = 0
        for vals, labs in ticks:
            for s in labs:
                tw = max(tw, _text_w(s, tick_font))
        lh = 0
        for which, s in (("xlabel", self.xlabel), ("ylabel", self.ylabel),
                         ("zlabel", self.zlabel)):
            if s:
                f = self._label_style(which, s)[0]
                lh = max(lh, _font_h(f), _text_w(s, f) // 2)
        mx = _TICK_LEN + _TICK_PAD + tw + _LABEL_GAP + lh + 6
        my = _TICK_LEN + _TICK_PAD + _font_h(tick_font) + _LABEL_GAP + lh + 6

        hx, hy, hz = self._half
        xs = []
        ys = []
        self._scale = 1.0
        self._cx = self._cy = 0.0
        for u in (-hx, hx):
            for v in (-hy, hy):
                for wv in (-hz, hz):
                    p = self._proj_box(u, v, wv)
                    xs.append(p[0])
                    ys.append(p[1])
        ew = max(xs) - min(xs)
        eh = max(ys) - min(ys)
        avail_w = max(16, aw - 2 * mx)
        avail_h = max(16, ah - title_h - 2 * my)
        self._scale = min(avail_w / ew, avail_h / eh) * self._zoom
        mid_x = (max(xs) + min(xs)) / 2
        mid_y = (max(ys) + min(ys)) / 2
        self._cx = ax0 + aw / 2 - mid_x * self._scale
        self._cy = ay0 + title_h + (ah - title_h) / 2 - mid_y * self._scale
        self._axes_px = (int(ax0), int(ay0), int(aw), int(ah))
        self._title_base = int(ay0 + 10 + _font_cap(tfont))
        # The 2-D legend code places itself in _box; the clip box keeps lines
        # inside the axes.
        self._box = (int(ax0 + 6), int(ay0 + title_h), int(aw - 12),
                     int(ah - title_h - 6))
        self._clipbox = (int(ax0), int(ay0), int(ax0 + aw - 1),
                         int(ay0 + ah - 1))

    def _quad(self, a, b, c, d, color):
        pa, pb, pc, pd = self._pt(*a), self._pt(*b), self._pt(*c), self._pt(*d)
        tulip.bg_triangle(pa[0], pa[1], pb[0], pb[1], pc[0], pc[1], color, 1)
        tulip.bg_triangle(pa[0], pa[1], pc[0], pc[1], pd[0], pd[1], color, 1)

    def _line(self, a, b, color):
        pa, pb = self._pt(*a), self._pt(*b)
        tulip.bg_line(pa[0], pa[1], pb[0], pb[1], color)

    def _label_at(self, s, anchor, out, dist, font, color, w=None, h=None):
        """Centre `s` `dist` px beyond `anchor` along the screen image of `out`.

        The extra half-extent of the text along that direction keeps a wide
        label from overlapping its axis whichever way the axis faces.
        """
        p0 = self._proj_box(*anchor)
        p1 = self._proj_box(anchor[0] + out[0], anchor[1] + out[1],
                            anchor[2] + out[2])
        dx = p1[0] - p0[0]
        dy = p1[1] - p0[1]
        n = math.sqrt(dx * dx + dy * dy) or 1.0
        dx /= n
        dy /= n
        if w is None:
            w = _text_w(s, font)
        if h is None:
            h = _font_cap(_pick_font(s, font))
        dist += abs(dx) * w / 2 + abs(dy) * h / 2
        _draw_text(s, p0[0] + dx * dist, p0[1] + dy * dist, color, font,
                   "center", "center")
        return dist + abs(dx) * w / 2 + abs(dy) * h / 2

    def draw(self):
        self._lims = self._limits()
        self._setup_view()
        # Fit once with a guessed tick set, then again with ticks sized for
        # the real box -- the same two-pass idea as the 2-D layout.
        self._scale = 1.0
        self._cx = self._cy = 0.0
        ticks = self._axis_ticks()
        self._fit(ticks)
        ticks = self._axis_ticks()
        self._fit(ticks)

        ax0, ay0, aw, ah = self._axes_px
        face = to_pal(self.facecolor)
        if face is not None:
            tulip.bg_rect(ax0, ay0, aw, ah, face, 1)

        back, edges = self._axis_edges()
        hx, hy, hz = self._half
        pane = _rgb_color(*_PANE_RGB)
        bx, by, bz = back
        # The three far panes.
        self._quad((bx, -hy, -hz), (bx, hy, -hz), (bx, hy, hz), (bx, -hy, hz),
                   pane)
        self._quad((-hx, by, -hz), (hx, by, -hz), (hx, by, hz), (-hx, by, hz),
                   pane)
        self._quad((-hx, -hy, bz), (hx, -hy, bz), (hx, hy, bz), (-hx, hy, bz),
                   pane)

        if self._grid_x or self._grid_y:
            gcolor = to_pal(self._grid_kw.get("color", rcParams["grid.color"]))
            for i in range(3):
                for v in ticks[i][0]:
                    t = self._norm(v, v, v)[i]
                    # Each tick value draws on the two far panes it crosses.
                    for j in range(3):
                        if j == i:
                            continue
                        k = 3 - i - j
                        a = [0.0, 0.0, 0.0]
                        b = [0.0, 0.0, 0.0]
                        a[i] = b[i] = t
                        a[j] = b[j] = back[j]
                        a[k] = -self._half[k]
                        b[k] = self._half[k]
                        self._line(a, b, gcolor)

        for art in self.artists:
            art.draw(self)

        # Axis lines, ticks, tick labels and axis labels.
        ecolor = to_pal(rcParams["axes.edgecolor"])
        tick_font = rcParams["xtick.labelsize"]
        tick_color = to_pal(rcParams["xtick.color"])
        names = (("xlabel", self.xlabel), ("ylabel", self.ylabel),
                 ("zlabel", self.zlabel))
        for i in range(3):
            fixed, out = edges[i]
            a = list(fixed)
            b = list(fixed)
            a[i] = -self._half[i]
            b[i] = self._half[i]
            if ecolor is not None:
                self._line(a, b, ecolor)
            if not self._ticks_visible:
                continue
            vals, labs = ticks[i]
            reach = 0
            for k in range(len(vals)):
                p = list(fixed)
                p[i] = self._norm(vals[k], vals[k], vals[k])[i]
                if abs(p[i]) > self._half[i] + 1e-9:
                    continue
                p0 = self._proj_box(*p)
                q = self._proj_box(p[0] + out[0], p[1] + out[1], p[2] + out[2])
                dx = q[0] - p0[0]
                dy = q[1] - p0[1]
                n = math.sqrt(dx * dx + dy * dy) or 1.0
                tulip.bg_line(int(p0[0]), int(p0[1]),
                              int(p0[0] + dx / n * _TICK_LEN),
                              int(p0[1] + dy / n * _TICK_LEN), tick_color)
                r = self._label_at(labs[k], p, out, _TICK_LEN + _TICK_PAD,
                                   tick_font, tick_color)
                reach = max(reach, r)
            which, s = names[i]
            if s:
                font, color = self._label_style(which, s)
                mid = list(fixed)
                mid[i] = 0.0
                self._label_at(s, mid, out, reach + _LABEL_GAP, font, color)

        if self.title:
            font, color = self._label_style("title", self.title)
            _draw_text(self.title, ax0 + aw // 2, self._title_base, color,
                       font, "center")
        if self._legend_kw is not None:
            self._draw_legend()


# The 2-D-only plot types: refuse rather than draw in the wrong space.
for _name in ("step", "semilogx", "semilogy", "loglog", "bar", "barh", "hist",
              "boxplot", "pie", "errorbar", "fill_between", "fill_betweenx",
              "axhline", "axvline", "axhspan", "axvspan", "imshow", "arrow",
              "text", "annotate"):
    setattr(Axes3D, _name, _unsupported(_name))
