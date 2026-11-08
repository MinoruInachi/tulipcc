# matplotlib on Tulip

Tulip ships a small `matplotlib` package — `matplotlib.pyplot` plus
`matplotlib.colors` — that draws through the `tulip.bg_*` primitives into the
background layer. A finished plot is ordinary pixels on the screen, so you can
screenshot it, blit it, scroll it or draw sprites over it like anything else.

It is frozen into the firmware, so there is nothing to install:

```python
from ulab import numpy as np
import matplotlib.pyplot as plt

x = np.linspace(0, 4 * np.pi, 300)
plt.plot(x, np.sin(x), label='sin')
plt.plot(x, np.cos(x), 'r--', label='cos')
plt.title('trig')
plt.xlabel('radians'); plt.ylabel('amplitude')
plt.grid(True); plt.legend()
plt.show()
```

`plt.full_screen(True)` hides Tulip's text layer so a full-screen plot is not
covered by the REPL. `plt.full_screen(False)` hands the screen back — text
layer on and the REPL's own background repainted, which discards the plot.
That second part matters on Tab5, where the REPL screen is transparent and the
background plane the plot painted *is* its background; turning the text back on
by itself would leave the REPL unreadable over your figure. It is a no-op if
some other app owns the screen. You can still type blind while the text layer
is off.

If you would rather keep the plot and have the REPL text over it, just do not
call `full_screen` at all — that is the default.

`tulip.run('plotdemo')` runs a four-page tour of everything below.

It pairs with [ulab](https://github.com/v923z/micropython-ulab): every call
that takes a list also takes a ulab `ndarray`, so ulab does the arrays and this
does the drawing.

## What is supported

| | |
| --- | --- |
| figures | `figure` `subplot` `subplots` `axes` `gca` `gcf` `sca` `cla` `clf` `close` |
| plots | `plot` `step` `scatter` `bar` `barh` `hist` `boxplot` `pie` `errorbar` `fill_between` `fill_betweenx` `axhline` `axvline` `axhspan` `axvspan` `imshow` `arrow` |
| decoration | `title` `suptitle` `xlabel` `ylabel` `xlim` `ylim` `xticks` `yticks` `grid` `legend` `text` `annotate` `axis` `tight_layout` |
| scales | `xscale` `yscale` `semilogx` `semilogy` `loglog` `invert_xaxis` `invert_yaxis` |
| output | `show` `draw` `savefig` `pause` |
| 3-D | `add_subplot(projection='3d')`, then `scatter` `plot` `plot_wireframe` `plot_surface` `set_zlabel` `set_zlim` `view_init` `set_box_aspect` |

The same names exist as `Axes` methods (`ax.set_title`, `ax.plot`, …), so both
the stateful and the object-oriented styles work.

Format strings work the way they do upstream — `plot(x, y, 'r--o')` is a red
dashed line with circles, and naming a marker but no line style means markers
only, so `'ro'` scatters. Line styles are `-`, `--`, `:`, `-.`; markers are
`. , o v ^ < > s D d | _ + x *`.

## 3-D plots

`projection='3d'` gives 3-D axes, the way it does upstream — on
`fig.add_subplot`, `plt.subplot`, `plt.axes` and
`plt.subplots(subplot_kw={'projection': '3d'})`. A desktop mplot3d script runs
with only the numpy import changed to ulab:

```python
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D   # optional, as upstream
from ulab import numpy as np

fig = plt.figure()
ax = fig.add_subplot(projection='3d')
ax.set_title("３D散布図", size=20)
ax.set_xlabel("x軸"); ax.set_ylabel("y軸"); ax.set_zlabel("z軸")

data = np.linspace(-3 * np.pi, 3 * np.pi, 50)
x, y = np.meshgrid(data, data)
z = np.cos(x / np.pi) * np.sin(y / np.pi)
ax.scatter(x, y, z, s=1, c="red")
plt.show()
```

That 2,500-point scatter draws in about 1.2 s on a Tab5. A 50x50 `plot_surface` (2,401 quads) takes about 1.7 s.

* The plot types are `scatter(xs, ys, zs, s=, c=, cmap=, marker=,
  depthshade=True)`, `plot(xs, ys, zs, fmt)`, `plot_wireframe(X, Y, Z,
  rstride=, cstride=, rcount=50, ccount=50, color=, linewidth=)` and
  `plot_surface(X, Y, Z, rstride=, cstride=, rcount=50, ccount=50, color=,
  cmap=, vmin=, vmax=, shade=, facecolors=, edgecolor=)`. The 2-D-only ones
  (`bar`, `hist`, `imshow`, …) raise `NotImplementedError` on 3-D axes.
* `plot_surface` colors each quad in one of three ways:
  * with `cmap=`, from the mean z of the quad;
  * with `facecolors=`, one color per grid cell;
  * otherwise one color (the next cycle color, or `color=`), shaded by how
    the quad faces mplot3d's default light. `shade=False` turns the shading
    off, and a colormap is never shaded, as upstream.
* Quads are painted back to front, as mplot3d does. `X` and `Y` may be the
  1-D vectors you would have passed to `meshgrid`.
* Two-dimensional inputs such as a `meshgrid` are flattened row-major, here and
  in the 2-D `plot` and `scatter`.
* The look follows mplot3d's defaults: `view_init(elev=30, azim=-60)`, a 4:4:3
  box (`set_box_aspect`), the three far panes shaded with a grid on them
  (`ax.grid(False)` turns the grid off), and scatter points fading with depth.
  Points are drawn back to front.
* Unlike upstream the box is scaled to fill the axes.
* On the Tab5 the panes and the depth shading use true color. Elsewhere they go
  through the palette like every other color, so the panes come out white and
  the shading has only a few steps.

The code is in `tulip/shared/py/mpl_toolkits/mplot3d/axes3d.py`.

## Colors

Tulip's background layer is one byte per pixel in RGB332, so every color
collapses to one of 256 palette entries. `matplotlib.colors.to_pal()` accepts:

```python
to_pal('r')            # single letters: b g r c m y k w
to_pal('darkgreen')    # a useful subset of the CSS names
to_pal('C3')           # the tab10 property cycle, C0..C9
to_pal('#ff8800')      # '#rgb' and '#rrggbb'
to_pal('0.5')          # a gray level, 0 black to 1 white
to_pal((1.0, 0.5, 0))  # floats 0-1, or ints 0-255
to_pal(200)            # a raw Tulip palette index -- the one Tulip extension
```

All ten default cycle colors survive the quantization as distinct palette
indices, which is the property that actually matters for a plot. Blue carries
only two bits, so blues quantize four times more coarsely than reds and greens.

Colormaps for `imshow` and for `scatter(c=...)`: `viridis`, `plasma`,
`inferno`, `magma`, `gray`, `hot`, `cool`, `jet`, `coolwarm`, `spring`,
`autumn`, `winter`, each also with the `_r` reversed suffix.

## rcParams

`matplotlib.rcParams` is a plain dict read at draw time, and
`matplotlib.rcdefaults()` puts it back. Where upstream takes a point size,
this takes a **Tulip font number** (0–19, the same numbering `tulip.bg_str`
uses) — the fonts are bitmaps at fixed sizes, not scalable outlines. The keys
keep their upstream names so `rcParams.update()` calls copied from a desktop
script land somewhere sensible.

```python
matplotlib.rcParams['lines.linewidth'] = 3
matplotlib.rcParams['axes.titlesize'] = 17     # font 17 is logisoso24
matplotlib.rcParams['figure.facecolor'] = 'k'
matplotlib.rcParams['axes.facecolor'] = 'k'
matplotlib.rcParams['axes.edgecolor'] = 'w'
```

## Where it differs from upstream

* **`show()` draws and returns.** There is no event loop to block on, and it
  does not throw the figure away, so `plot(); show(); plot(); show()`
  accumulates. Call `clf()` (or `figure()`) to start a new plot.
* **`alpha=` is accepted and ignored.** One byte per pixel, no blending.
* **Font sizes are Tulip font numbers**, as above. `set_title`, `set_xlabel`,
  `set_ylabel` and `set_zlabel` take `fontsize=` / `size=` and `color=`. A
  `size=` that is not a font number, such as a desktop script's point size of
  20, is ignored and the rcParams font is used instead.
* **Text with Japanese in it is drawn in font 19**, efont's 16-dot Japanese
  font, whatever font was asked for, because fonts 0–18 are ASCII only. So
  `plt.title('温度')` just works. The ESP32-S3 builds have no font 19, so there
  the text is drawn in the requested font as before. `rcParams['font.family']`
  is accepted and ignored.
* **`ylabel` is a column of upright characters** rather than rotated text.
  Tulip's text renderer draws left to right only.
* **`imshow` defaults to `aspect='auto'`** (fill the axes box) rather than
  upstream's `'equal'`, so the frame and the image stay aligned and the ticks
  keep meaning what they say. Pass `aspect='equal'` for the upstream look.
* **`scatter(s=...)` is a marker diameter in pixels**, not an area in points
  squared.
* **`boxplot` returns a list of statistics dicts** (`q1`, `med`, `q3`,
  `whislo`, `whishi`, `fliers`, `position`) rather than a dict of artists.
* There is no Agg backend, no transform stack and no artist tree to
  introspect. `matplotlib.use()` is accepted and ignored.

## Speed

Drawing is Python calling C primitives once per line segment, so a plot costs
roughly a second on an ESP32-P4 at 1280x720:

| | |
| --- | --- |
| three 300-point lines, grid and legend | ~1.5 s |
| 2x2 grid of bar, hist, imshow and pie | ~1.5 s |
| 512-point FFT drawn as waveform + log spectrum | ~1.2 s |
| full-screen `imshow` of a 48x32 field | ~2.1 s |

`imshow` writes one `bg_bitmap` call per destination row and reuses the row
buffer whenever consecutive rows come from the same source row, so upscaling a
small array is much cheaper than the pixel count suggests. Long polylines are
the other cost — decimate before plotting if you need a redraw to feel
interactive.

## Layout

Margins are measured, not guessed. Tulip has no measure-text call, but
`bg_str` returns the advance width of what it drew, so the library renders
single characters into the offscreen columns past the right edge of the panel
— framebuffer memory the scanout never reads — and caches the widths. A plot
of megabytes and a plot of percentages both come out tight, in any of the 19
built-in fonts.

Subplot grids divide the figure evenly with a small gap, because each `Axes`
reserves its own decor space from those measurements. `tight_layout()` exists
and does nothing; there is nothing left for it to fix.

## Saving

`savefig(name)` redraws the figure and writes a PNG cropped to the figure
rectangle:

```python
plt.savefig('/user/plot.png')
```

## Where the code lives

`tulip/shared/py/matplotlib/` — `__init__.py` (rcParams), `colors.py`
(color and colormap resolution) and `pyplot.py` (the state machine, the
artists and the renderer) — and `tulip/shared/py/mpl_toolkits/mplot3d/` for
the 3-D axes. Most ports freeze all of `tulip/shared/py` recursively and pick
both up automatically; the ESP32-P4 board lists its frozen files individually,
so it names both packages explicitly in `tulip/esp32p4/boards/manifest.py`.
