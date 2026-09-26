# draw all 65536 rgb565 colors nicely -- the rgb332.py of the Tab5
# 32 tiles, one per blue level (5 bits). In each tile green (6 bits) runs left
# to right and red (5 bits) top to bottom.
import tulip

if not hasattr(tulip, 'bg_pixel_rgb'):
    raise OSError("rgb565.py needs an RGB565 BG plane (Tab5)")

sw, sh = tulip.screen_size()
cols = 8
tw = 64 * 2    # each green step is 2px wide
th = 32 * 4    # each red step is 4px tall
gx = (sw - cols * tw) // cols
gy = 40

tulip.bg_clear(0)
# bg_str draws from the bottom left
tulip.bg_str("RGB565: one tile per blue level, green ->, red v", gx // 2, 26, 255, 0)
n = tw * 2              # bytes in one row of a tile
buf = bytearray(n * 4)  # one red step: 4 identical rows
mv = memoryview(buf)
for b in range(32):
    x = (b % cols) * (tw + gx) + gx // 2
    y = (b // cols) * (th + gy) + 34
    for r in range(32):
        for g in range(64):
            # native pixels are little-endian RGB565, 2 bytes each
            p = (r << 11) | (g << 5) | b
            i = g * 4
            buf[i] = buf[i + 2] = p & 0xff
            buf[i + 1] = buf[i + 3] = p >> 8
        for k in (1, 2, 3):
            mv[k * n:(k + 1) * n] = mv[:n]
        tulip.bg_bitmap(x, y + r * 4, tw, 4, buf)
    # bg_bitmap skips the transparent pixel (0x4daa = r9 g45 b10), so paint
    # that one cell directly
    if b == 10:
        tulip.bg_rect(x + 45 * 2, y + 9 * 4, 2, 4, (9 << 3, 45 << 2, 10 << 3), 1)
    tulip.bg_str("b=%02d" % b, x, y + th + 17, 255, 9)
