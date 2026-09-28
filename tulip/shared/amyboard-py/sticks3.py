# sticks3.py
# M5Stack StickS3 hardware for the AMYboard firmware (boards/STICKS3):
# the M5PM1 PMIC, the ES8311 codec and the ST7789 LCD. amyboard.py uses this
# in place of the PCM9211 / GP8413 / OLED code when running on a StickS3.
import time

PM1_ADDR = 0x6e
ES8311_ADDR = 0x18

# M5PM1 registers
_PM1_I2C_CFG = 0x09
_PM1_WDT_CNT = 0x0A
_PM1_GPIO_MODE = 0x10
_PM1_GPIO_OUT = 0x11
_PM1_GPIO_DRV = 0x13
_PM1_GPIO_FUNC0 = 0x16
# M5PM1 GPIOs used here
_PM1_LCD_POWER = 2   # L3B enable: LCD power
_PM1_SPK_AMP = 3     # AW8737 speaker amplifier enable


def _retry(fn):
    # The M5PM1 NACKs the first transaction after it has idled on the bus
    # (I2C idle sleep, until power_init() turns that off), so retry briefly.
    for _ in range(5):
        try:
            return fn()
        except OSError:
            time.sleep_ms(5)
    return fn()


def _rd(i2c, addr, reg):
    return _retry(lambda: i2c.readfrom_mem(addr, reg, 1)[0])


def _wr(i2c, addr, reg, val):
    _retry(lambda: i2c.writeto_mem(addr, reg, bytes([val])))


def _pm1_bit(i2c, reg, bit, on):
    v = _rd(i2c, PM1_ADDR, reg)
    _wr(i2c, PM1_ADDR, reg, (v | (1 << bit)) if on else (v & ~(1 << bit)))


def _pm1_gpio_output(i2c, bit, on):
    _pm1_bit(i2c, _PM1_GPIO_FUNC0, bit, False)  # plain GPIO function
    _pm1_bit(i2c, _PM1_GPIO_MODE, bit, True)    # output
    _pm1_bit(i2c, _PM1_GPIO_DRV, bit, False)    # push-pull
    _pm1_bit(i2c, _PM1_GPIO_OUT, bit, on)


def power_init(i2c):
    """Keep the PMIC awake, switch on the LCD, and hold the speaker amp in
    shutdown until speaker_amp(True) -- which should come after AMY has
    started the I2S clocks."""
    _wr(i2c, PM1_ADDR, _PM1_I2C_CFG, 0x00)  # disable I2C idle sleep
    _wr(i2c, PM1_ADDR, _PM1_WDT_CNT, 0x00)  # disable the PMIC watchdog
    _pm1_gpio_output(i2c, _PM1_LCD_POWER, True)
    # The PMIC runs off the battery and keeps its registers across ESP
    # resets, so the enable line may still be high from the last boot.
    _pm1_gpio_output(i2c, _PM1_SPK_AMP, False)


def speaker_amp(i2c, on):
    """Switch the AW8737 speaker amp (one rising edge on its SHDN line = mode
    1, the 1.2W power limit).

    Some StickS3s whine faintly at a mosquito pitch while the amp is on and
    idle. Whether it does is decided afresh each time the amp is enabled --
    the same sequence comes up quiet one time and whining the next -- and
    M5Stack documents it: "Early batches of StickS3 may produce slight
    abnormal noise after startup, which does not affect functional use."
    Nothing in the enable timing changes it."""
    _pm1_bit(i2c, _PM1_GPIO_OUT, _PM1_SPK_AMP, on)


def es8311_init(i2c, mic_gain=0):
    """Set the ES8311 up for AMY: I2S slave, MCLK = 256fs from the ESP on
    GPIO18, 32-bit left-justified slots (AMY's ESP-master I2S format), DAC
    to the speaker amp and the MEMS mic into the ADC.

    mic_gain is the analog PGA gain in 3dB steps, 0..10 (0dB..30dB). It
    defaults to 0: the mic sits next to the speaker, so a hot input fed
    back to the output howls."""
    w = lambda reg, val: _wr(i2c, ES8311_ADDR, reg, val)
    w(0x00, 0x1F)       # reset
    time.sleep_ms(20)
    w(0x00, 0x00)
    w(0x00, 0x80)       # power on, slave mode
    w(0x01, 0x3F)       # MCLK from the MCLK pin, all clocks on
    w(0x02, 0x00)       # pre-divider 1, multiplier x1 (MCLK is already 256fs)
    w(0x03, 0x10)       # ADC single speed, OSR 16
    w(0x04, 0x10)       # DAC OSR 16
    w(0x05, 0x00)       # ADC / DAC clock dividers 1
    w(0x09, 0x11)       # DAC serial in: 32-bit, left-justified
    w(0x0A, 0x11)       # ADC serial out: 32-bit, left-justified
    w(0x0D, 0x01)       # power up analog circuitry
    w(0x0E, 0x02)       # enable analog PGA and ADC modulator
    w(0x12, 0x00)       # power up the DAC
    w(0x13, 0x10)       # enable the output driver
    w(0x14, 0x10 | max(0, min(10, int(mic_gain))))  # MIC1P/N, PGA gain
    w(0x17, 0xBF)       # ADC volume 0dB
    w(0x1C, 0x6A)       # ADC EQ bypass, digital DC offset cancel
    w(0x37, 0x08)       # bypass the DAC EQ
    w(0x32, 0xBF)       # DAC volume 0dB


def es8311_volume(i2c, vol):
    """DAC volume register: 0 = mute, 0xBF = 0dB, 0xFF = +32dB."""
    _wr(i2c, ES8311_ADDR, 0x32, max(0, min(255, int(vol))))


class LCD:
    """The StickS3's ST7789, landscape: a 240x135 GS4 framebuffer (the same
    4-bit grey format as AMYboard's OLED, over the whole panel). Held with
    the front button (KEY1) on the right. show() expands the grey levels to
    RGB565 with a palette blit, one row at a time."""
    WIDTH = 240
    HEIGHT = 135
    _MADCTL = 0x60                # MV|MX: landscape, KEY1 on the right
    # The 135x240 panel sits at (52, 40) in the controller's 240x320 RAM; in
    # this rotation that puts the visible area at x 40, y 240-135-52 = 53.
    _X0 = 40
    _Y0 = 53

    def __init__(self, i2c, backlight=200):
        import framebuf
        from machine import Pin, SPI, PWM
        # The panel's supply is switched by the PMIC. After a cold power-on it
        # is off, and a panel initialised unpowered stays dark.
        _pm1_gpio_output(i2c, _PM1_LCD_POWER, True)
        time.sleep_ms(100)
        self._cs = Pin(41, Pin.OUT, value=1)
        self._dc = Pin(45, Pin.OUT, value=0)
        self._rst = Pin(21, Pin.OUT, value=1)
        self._spi = SPI(2, baudrate=40_000_000, polarity=0, phase=0,
                        sck=Pin(40), mosi=Pin(39), miso=None)
        self._bl = PWM(Pin(38), freq=20000, duty_u16=0)

        self.buffer = bytearray(self.WIDTH * self.HEIGHT // 2)
        self.framebuf = framebuf.FrameBuffer(self.buffer, self.WIDTH, self.HEIGHT,
                                             framebuf.GS4_HMSB)
        # Grey level g (0..15) -> white * g/15, byte-swapped because the
        # panel takes RGB565 big-endian and framebuf stores it little-endian.
        pal = bytearray(32)
        for g in range(16):
            c = ((g * 31 // 15) << 11) | ((g * 63 // 15) << 5) | (g * 31 // 15)
            pal[2 * g] = c >> 8
            pal[2 * g + 1] = c & 0xFF
        self._palette = framebuf.FrameBuffer(pal, 16, 1, framebuf.RGB565)
        self._row = bytearray(self.WIDTH * 2)
        self._row_fb = framebuf.FrameBuffer(self._row, self.WIDTH, 1, framebuf.RGB565)
        self._shadow = bytearray(len(self.buffer))
        self._shadow_valid = False

        self._reset()
        self.show(full=True)   # the buffer starts all black
        self.backlight(backlight)

    def _cmd(self, c, data=None):
        self._cs(0)
        self._dc(0)
        self._spi.write(bytes([c]))
        if data:
            self._dc(1)
            self._spi.write(data)
        self._cs(1)

    def _reset(self):
        self._rst(0)
        time.sleep_ms(20)
        self._rst(1)
        time.sleep_ms(120)
        self._cmd(0x01)                  # SWRESET
        time.sleep_ms(150)
        self._cmd(0x11)                  # SLPOUT
        time.sleep_ms(120)
        self._cmd(0x3A, b'\x55')         # COLMOD 16-bit
        self._cmd(0x36, bytes([self._MADCTL]))  # MADCTL: rotation, RGB
        self._cmd(0x21)                  # INVON (this panel is inverted)
        self._cmd(0x13)                  # NORON
        self._cmd(0x29)                  # DISPON

    def _window(self, x0, y0, x1, y1):
        self._cmd(0x2A, bytes([x0 >> 8, x0 & 0xFF, x1 >> 8, x1 & 0xFF]))
        self._cmd(0x2B, bytes([y0 >> 8, y0 & 0xFF, y1 >> 8, y1 & 0xFF]))

    def backlight(self, level):
        """0 (off) .. 255 (full)."""
        self._bl.duty_u16(max(0, min(255, int(level))) * 257)

    def show(self, full=False):
        """Push the framebuffer to the panel if it changed (or if full)."""
        buf, shadow = self.buffer, self._shadow
        if not full and self._shadow_valid and buf == shadow:
            return
        self._window(self._X0, self._Y0,
                     self._X0 + self.WIDTH - 1, self._Y0 + self.HEIGHT - 1)
        self._cmd(0x2C)
        self._cs(0)
        self._dc(1)
        fb, row_fb, pal, row = self.framebuf, self._row_fb, self._palette, self._row
        for y in range(self.HEIGHT):
            row_fb.blit(fb, 0, -y, -1, pal)
            self._spi.write(row)
        self._cs(1)
        shadow[:] = buf
        self._shadow_valid = True
