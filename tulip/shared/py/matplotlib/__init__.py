# matplotlib for Tulip
#
# A small reimplementation of the slice of matplotlib that is worth having on a
# Tulip.  `matplotlib.pyplot` renders through the `tulip.bg_*` primitives into
# the background layer, so a finished plot is ordinary pixels on the screen:
# screenshot it, blit it, scroll it, or draw sprites over it like anything else.
#
# The API follows upstream matplotlib rather than inventing a Tulip dialect, so
# plotting code written on a desktop mostly runs unchanged.  What is missing is
# missing on purpose -- there is no Agg backend, no transform stack, and no
# artist tree to introspect.  See matplotlib/pyplot.py for the supported
# surface and the handful of documented deviations.
#
# It pairs with ulab: `plot()` and friends take ulab ndarrays anywhere they
# take a list, so `from ulab import numpy as np` covers the array half of a
# desktop scientific stack and this covers the drawing half.

__version__ = "0.1.0+tulip"

# Style defaults, read at draw time -- changing one affects the next show(),
# the same contract as upstream rcParams minus the validation.  Colors accept
# anything matplotlib.colors.to_pal() understands.
#
# Where upstream takes a point size we take a Tulip font number (0-18, see
# `tulip.bg_str`).  Point sizes are meaningless here: the fonts are bitmaps at
# fixed sizes, not scalable outlines.  The keys keep their upstream names so
# that rcParams.update() calls copied from a desktop script still land
# somewhere sensible.
_DEFAULTS = {
    "figure.facecolor": "w",
    "figure.edgecolor": None,
    "figure.titlesize": 5,          # helvB14
    "figure.dpi": 100,              # only used to turn figsize inches into px

    "axes.facecolor": "w",
    "axes.edgecolor": "k",
    "axes.linewidth": 1,
    "axes.labelcolor": "k",
    "axes.labelsize": 9,            # 8x13
    "axes.titlesize": 5,            # helvB14
    "axes.titlecolor": "k",
    "axes.grid": False,
    "axes.margin": 0.05,            # upstream axes.xmargin / axes.ymargin
    "axes.prop_cycle": ("C0", "C1", "C2", "C3", "C4",
                        "C5", "C6", "C7", "C8", "C9"),

    "grid.color": "#b0b0b0",
    "grid.linestyle": ":",
    "grid.linewidth": 1,

    "lines.linewidth": 2,
    "lines.linestyle": "-",
    "lines.marker": None,
    "lines.markersize": 7,

    "patch.edgecolor": "k",
    "patch.linewidth": 1,

    "text.color": "k",

    "xtick.color": "k",
    "xtick.labelsize": 8,           # 6x13
    "xtick.major.size": 4,
    "xtick.major.pad": 4,
    "ytick.color": "k",
    "ytick.labelsize": 8,
    "ytick.major.size": 4,
    "ytick.major.pad": 5,

    "legend.facecolor": "w",
    "legend.edgecolor": "#808080",
    "legend.fontsize": 8,
    "legend.loc": "upper right",
}

rcParams = dict(_DEFAULTS)


def rcdefaults():
    """Restore every rcParam to its built-in default."""
    rcParams.clear()
    rcParams.update(_DEFAULTS)


def use(backend=None):
    """Accepted and ignored.

    There is exactly one backend here -- the Tulip background layer -- but
    desktop scripts open with `matplotlib.use('Agg')` often enough that
    refusing would be more annoying than useful.
    """
    return "tulip"
