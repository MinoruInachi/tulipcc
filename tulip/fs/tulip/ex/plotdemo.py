# A tour of matplotlib.pyplot on Tulip.
#
# Run it with `tulip.run('plotdemo')`, or just `import plotdemo`.  Press any
# key to move to the next page; the text layer is hidden while it runs so the
# plots are unobstructed, and the REPL gets its screen back when it finishes.
#
# Everything here uses ulab for the maths, which is the pairing the port is
# meant for: ulab does the arrays, matplotlib draws them.
#
# Note the driver below is NOT called `run`: this draws straight to the BG
# plane, so it is a plain script like ex/rgb332.py, not a switchable UIScreen
# app.  tulip.run() treats a module with a `run` attribute as an app -- it
# imports the module (running it once) and then calls module.run(screen) as
# well (running it twice), and leaves an opaque black UIScreen behind in
# ui.running_apps that every later tulip.run('plotdemo') switches to instead.

import math

import tulip
from ulab import numpy as np

import matplotlib.pyplot as plt


def page_lines():
    """Line styles, markers, the property cycle, grid and legend."""
    x = np.linspace(0, 4 * math.pi, 300)
    plt.plot(x, np.sin(x), label="sin")
    plt.plot(x, np.cos(x), "r--", label="cos")
    plt.plot(x[::20], np.sin(x[::20]) * 0.5, "go", label="samples")
    plt.title("lines, dashes and markers")
    plt.xlabel("radians")
    plt.ylabel("amplitude")
    plt.grid(True)
    plt.legend()


def page_grid():
    """Four different plot types sharing one figure."""
    fig, ax = plt.subplots(2, 2)

    ax[0][0].bar(["do", "re", "mi", "fa"], [3, 7, 2, 5])
    ax[0][0].set_title("bar")

    # A cheap deterministic spread; no random module needed.
    samples = [((i * 7919) % 997) / 99.7 for i in range(600)]
    ax[0][1].hist(samples, bins=20)
    ax[0][1].set_title("hist")

    t = np.linspace(0, 6, 120)
    ax[1][0].scatter(t, np.sin(t * 2), s=6, c=np.sin(t * 2), cmap="viridis")
    ax[1][0].set_title("scatter")

    ax[1][1].pie([30, 25, 20, 15, 10], labels=["a", "b", "c", "d", "e"],
                 autopct="%.0f%%")
    ax[1][1].set_title("pie")

    plt.suptitle("one figure, four axes")


def page_signal():
    """The reason this pairs with ulab: look at a signal, then at its spectrum."""
    n = 512
    rate = 8000.0
    t = np.linspace(0, n / rate, n)
    sig = np.sin(2 * np.pi * 440 * t) + 0.4 * np.sin(2 * np.pi * 1300 * t)

    spec = np.fft.fft(sig)
    # ulab has no np.abs, and with complex support on fft returns one complex
    # array rather than a (real, imag) pair.
    mag = np.sqrt(np.real(spec) * np.real(spec)
                  + np.imag(spec) * np.imag(spec))[:n // 2] * (2.0 / n)
    freq = np.linspace(0, rate / 2, n // 2)

    fig, ax = plt.subplots(2, 1)
    ax[0].plot(t[:200] * 1000.0, sig[:200])
    ax[0].set_title("440 Hz + 1300 Hz")
    ax[0].set_xlabel("ms")
    ax[0].set_ylabel("x")
    ax[0].grid(True)

    ax[1].semilogy(freq[1:], mag[1:] + 1e-6)
    ax[1].set_title("spectrum")
    ax[1].set_xlabel("Hz")
    ax[1].set_ylabel("mag")
    ax[1].grid(True)


def page_image():
    """imshow with a colormap, plus filled regions and error bars."""
    fig, ax = plt.subplots(1, 2)

    field = [[math.sin(i / 5.0) * math.cos(j / 6.0) for i in range(64)]
             for j in range(48)]
    ax[0].imshow(field, cmap="plasma")
    ax[0].set_title("imshow")

    x = np.linspace(0, 10, 60)
    ax[1].fill_between(x, np.sin(x), np.sin(x) - 0.4, color="C0")
    ax[1].errorbar(x[::8], np.sin(x[::8]), yerr=0.25, fmt="ko", capsize=5,
                   label="measured")
    ax[1].axhspan(-0.15, 0.15, color="C8")
    ax[1].set_title("fill, spans and error bars")
    ax[1].legend(loc="lower left")


PAGES = (page_lines, page_grid, page_signal, page_image)


def _tour():
    plt.full_screen(True)
    try:
        for page in PAGES:
            plt.close("all")
            page()
            plt.show()
            tulip.key_wait()
    finally:
        # However we got here -- last page, ctrl-C, an error mid-draw -- give
        # the REPL its screen back rather than leaving it over a white figure.
        plt.close("all")
        plt.full_screen(False)


_tour()
