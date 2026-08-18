# Color handling for Tulip's matplotlib.
#
# Tulip's background layer is one byte per pixel in RGB332, so every color a
# plotting call names -- 'r', 'red', '#ff0000', (1.0, 0.0, 0.0), 'C3' -- has to
# collapse to one of 256 palette entries.  Everything here funnels through
# tulip.color(), so all of those spellings land on the same index and identity
# comparisons between colors stay meaningful.
#
# Two consequences worth knowing about up front:
#
#   * Blue carries only two bits, so blues quantize four times more coarsely
#     than reds and greens.  The default property cycle below is tab10, and it
#     was checked after quantization -- all ten entries survive as distinct
#     palette indices, which is the property that actually matters for a plot.
#   * There is no alpha channel.  `alpha=` arguments are accepted and ignored
#     rather than raising, so desktop code keeps running.

import tulip

# Palette index 0x55 is Tulip's bitmap transparency key: bg_bitmap() skips
# those bytes rather than writing them.  imshow() is the only thing here that
# goes through bg_bitmap, and it nudges the index by one rather than punching
# holes in the image.
ALPHA_INDEX = 0x55


def rgb_to_pal(r, g, b):
    """Pack 0-255 components into a Tulip palette index."""
    return tulip.color(int(r) & 0xFF, int(g) & 0xFF, int(b) & 0xFF)


def pal_to_rgb(pal):
    """Unpack a palette index back to the 0-255 components it displays as."""
    pal = int(pal) & 0xFF
    r = (pal & 0xE0) >> 5
    g = (pal & 0x1C) >> 2
    b = pal & 0x03
    # Replicate the high bits down so full-scale stays full-scale.
    return (r * 255 // 7, g * 255 // 7, b * 255 // 3)


# matplotlib's single-letter codes.  These are the saturated ones, distinct
# from the muted tab10 cycle below -- same split as upstream.
_BASE = {
    "b": (0, 0, 255),
    "g": (0, 128, 0),
    "r": (255, 0, 0),
    "c": (0, 191, 191),
    "m": (191, 0, 191),
    "y": (191, 191, 0),
    "k": (0, 0, 0),
    "w": (255, 255, 255),
}

# tab10, matplotlib's default property cycle since 2.0.
TAB10 = (
    (31, 119, 180),    # C0 blue
    (255, 127, 14),    # C1 orange
    (44, 160, 44),     # C2 green
    (214, 39, 40),     # C3 red
    (148, 103, 189),   # C4 purple
    (140, 86, 75),     # C5 brown
    (227, 119, 194),   # C6 pink
    (127, 127, 127),   # C7 gray
    (188, 189, 34),    # C8 olive
    (23, 190, 207),    # C9 cyan
)

# A useful subset of the CSS names.  Not the whole 148-entry table: most of it
# quantizes onto the same handful of RGB332 cells anyway, and this is frozen
# into firmware.
_NAMED = {
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "red": (255, 0, 0),
    "green": (0, 128, 0),
    "blue": (0, 0, 255),
    "cyan": (0, 255, 255),
    "magenta": (255, 0, 255),
    "yellow": (255, 255, 0),
    "gray": (128, 128, 128),
    "grey": (128, 128, 128),
    "lightgray": (211, 211, 211),
    "lightgrey": (211, 211, 211),
    "darkgray": (169, 169, 169),
    "darkgrey": (169, 169, 169),
    "orange": (255, 165, 0),
    "purple": (128, 0, 128),
    "brown": (165, 42, 42),
    "pink": (255, 192, 203),
    "lime": (0, 255, 0),
    "navy": (0, 0, 128),
    "teal": (0, 128, 128),
    "olive": (128, 128, 0),
    "maroon": (128, 0, 0),
    "gold": (255, 215, 0),
    "silver": (192, 192, 192),
    "indigo": (75, 0, 130),
    "violet": (238, 130, 238),
    "turquoise": (64, 224, 208),
    "salmon": (250, 128, 114),
    "crimson": (220, 20, 60),
    "coral": (255, 127, 80),
    "khaki": (240, 230, 140),
    "tan": (210, 180, 140),
    "beige": (245, 245, 220),
    "skyblue": (135, 206, 235),
    "steelblue": (70, 130, 180),
    "darkblue": (0, 0, 139),
    "darkgreen": (0, 100, 0),
    "darkred": (139, 0, 0),
    "lightblue": (173, 216, 230),
    "lightgreen": (144, 238, 144),
}

_cache = {}


def to_pal(c, default=None):
    """Resolve any supported color spec to a Tulip palette index.

    Accepts, in the order tried:

      * `None`                 -> `default` (itself resolved, or None)
      * `int`                  -> a raw Tulip palette index, passed through.
                                  This is the Tulip-specific extension; it lets
                                  you name a palette cell no RGB spelling can
                                  reach exactly.
      * `'C0'` .. `'C9'`       -> the tab10 property cycle
      * `'r'`, `'red'`         -> single letters and the CSS names above
      * `'#rgb'`, `'#rrggbb'`  -> hex
      * `'0.5'`                -> a gray level, 0 black to 1 white
      * `(r, g, b)`            -> floats 0-1 (matplotlib) or ints 0-255,
        `(r, g, b, a)`            distinguished by whether any value exceeds 1;
                                  alpha is ignored
    """
    if c is None:
        return None if default is None else to_pal(default)
    if isinstance(c, int) and not isinstance(c, bool):
        return c & 0xFF
    if isinstance(c, (tuple, list)):
        if len(c) < 3:
            raise ValueError("color tuple needs 3 or 4 components")
        r, g, b = c[0], c[1], c[2]
        if r > 1 or g > 1 or b > 1:
            return rgb_to_pal(r, g, b)
        return rgb_to_pal(r * 255, g * 255, b * 255)
    if not isinstance(c, str):
        raise ValueError("unknown color spec %r" % (c,))

    hit = _cache.get(c)
    if hit is not None:
        return hit
    pal = _parse_str(c)
    _cache[c] = pal
    return pal


def _parse_str(c):
    s = c.strip()
    if len(s) == 2 and (s[0] == "C" or s[0] == "c") and s[1].isdigit():
        return rgb_to_pal(*TAB10[int(s[1])])
    low = s.lower()
    if low in _BASE:
        return rgb_to_pal(*_BASE[low])
    if low in _NAMED:
        return rgb_to_pal(*_NAMED[low])
    if s.startswith("#"):
        h = s[1:]
        if len(h) == 3:
            return rgb_to_pal(int(h[0], 16) * 17, int(h[1], 16) * 17, int(h[2], 16) * 17)
        if len(h) in (6, 8):
            return rgb_to_pal(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
        raise ValueError("bad hex color %r" % (c,))
    # matplotlib spells grays as a numeric string: '0' black, '1' white.
    try:
        level = float(s)
    except (ValueError, TypeError):
        raise ValueError("unknown color %r" % (c,))
    if not 0.0 <= level <= 1.0:
        raise ValueError("gray level %r is outside 0..1" % (c,))
    v = int(level * 255)
    return rgb_to_pal(v, v, v)


def cycle_pals():
    """The default property cycle, as palette indices."""
    return [rgb_to_pal(*rgb) for rgb in TAB10]


# --------------------------------------------------------------- colormaps

# Anchor points sampled from the upstream colormaps.  Five stops is plenty:
# RGB332 has 8 red levels, 8 green and 4 blue, so a finer table would quantize
# onto the same cells anyway.
_CMAP_STOPS = {
    "viridis": ((68, 1, 84), (59, 82, 139), (33, 145, 140), (94, 201, 98), (253, 231, 37)),
    "plasma": ((13, 8, 135), (126, 3, 168), (204, 71, 120), (248, 149, 64), (240, 249, 33)),
    "inferno": ((0, 0, 4), (87, 16, 110), (188, 55, 84), (249, 142, 9), (252, 255, 164)),
    "magma": ((0, 0, 4), (81, 18, 124), (183, 55, 121), (252, 137, 97), (252, 253, 191)),
    "gray": ((0, 0, 0), (64, 64, 64), (128, 128, 128), (191, 191, 191), (255, 255, 255)),
    "hot": ((0, 0, 0), (128, 0, 0), (255, 0, 0), (255, 191, 0), (255, 255, 255)),
    "cool": ((0, 255, 255), (64, 191, 255), (128, 128, 255), (191, 64, 255), (255, 0, 255)),
    "jet": ((0, 0, 143), (0, 128, 255), (124, 255, 121), (255, 128, 0), (128, 0, 0)),
    "coolwarm": ((59, 76, 192), (144, 178, 254), (221, 221, 221), (245, 156, 125), (180, 4, 38)),
    "spring": ((255, 0, 255), (255, 64, 191), (255, 128, 128), (255, 191, 64), (255, 255, 0)),
    "autumn": ((255, 0, 0), (255, 64, 0), (255, 128, 0), (255, 191, 0), (255, 255, 0)),
    "winter": ((0, 0, 255), (0, 64, 223), (0, 128, 191), (0, 191, 159), (0, 255, 128)),
}

# Reversed variants, matplotlib's '_r' suffix, are synthesised on lookup.


class Colormap:
    """Maps 0..1 to a Tulip palette index.

    The 64-entry lookup table is built once on construction; imshow() calls
    this per pixel, and interpolating in Python for every one of them would
    dominate the draw.
    """

    LUT_SIZE = 64

    def __init__(self, name, stops):
        self.name = name
        self.N = self.LUT_SIZE
        n = len(stops) - 1
        lut = bytearray(self.LUT_SIZE)
        for i in range(self.LUT_SIZE):
            t = i / (self.LUT_SIZE - 1) * n
            k = int(t)
            if k >= n:
                k = n - 1
            f = t - k
            a, b = stops[k], stops[k + 1]
            lut[i] = rgb_to_pal(a[0] + (b[0] - a[0]) * f,
                                a[1] + (b[1] - a[1]) * f,
                                a[2] + (b[2] - a[2]) * f)
        self._lut = lut

    def __call__(self, t):
        if t != t:                      # NaN maps to the low end, as upstream
            t = 0.0
        i = int(t * (self.LUT_SIZE - 1) + 0.5)
        if i < 0:
            i = 0
        elif i >= self.LUT_SIZE:
            i = self.LUT_SIZE - 1
        return self._lut[i]


_cmaps = {}


def get_cmap(name=None):
    """Look up a Colormap by name.  Accepts a Colormap and passes it through."""
    if name is None:
        name = "viridis"
    if isinstance(name, Colormap):
        return name
    hit = _cmaps.get(name)
    if hit is not None:
        return hit
    key, rev = (name[:-2], True) if name.endswith("_r") else (name, False)
    stops = _CMAP_STOPS.get(key)
    if stops is None:
        raise ValueError("unknown colormap %r; have %s"
                         % (name, ", ".join(sorted(_CMAP_STOPS))))
    if rev:
        stops = tuple(reversed(stops))
    cm = Colormap(name, stops)
    _cmaps[name] = cm
    return cm
