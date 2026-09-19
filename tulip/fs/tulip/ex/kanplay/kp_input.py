"""Input layer for kanplay: keyboard, touch pads and MIDI all become one set
of held "buttons", and the app only ever sees press/release edges.

Buttons (string ids), laid out like the KANTAN Play base's 15 play buttons:

    roots (left 3x3)         modifiers (right two columns)
    [7] [8] [9]              [dim]  [sus4]
    [4] [5] [6]              [7th]  [M7]
    [1] [2] [3]              [swap] [add9]

8 and 9 are the flat/sharp keys (held with a root), "swap" flips major and
minor. Extra buttons: "stop", "key-", "key+", "slot-", "slot+", "song-",
"song+", "mode".

Sources:

- Keyboard: presses arrive by tulip.keyboard_callback (an ASCII code, no
  release), releases by polling tulip.keys(), which returns the HID scan codes
  currently held (modifier + six rollover slots). A USB HID keyboard reports
  those exactly; the Tab5's built-in keyboard reports a release without saying
  which key came up, and the firmware guesses the most recent press, so on
  that keyboard let go of a root before the modifier you held with it.
- Touch pads: the UI hit-tests all three points of tulip.touch() against its
  pads itself and hands the result to set_touch(); LVGL's own input device
  only follows one finger, so its button events cannot see a chord-type pad
  and a root pad held together. ui_down()/ui_up() remain for single widgets.
- MIDI: midi_note() maps an incoming keyboard's white keys to roots.

Call poll() once per frame (and from the keyboard callback) so edges fire.
"""
import tulip

ROOTS = ("1", "2", "3", "4", "5", "6", "7")
FLAT = "8"
SHARP = "9"
MODIFIERS = ("dim", "7th", "swap", "sus4", "M7", "add9")
# Button id -> chord modifier name as kp_chords spells it.
MOD_CHORD = {"dim": "dim", "7th": "7", "sus4": "sus4", "M7": "M7", "add9": "add9"}
PLAY_BUTTONS = ROOTS + (FLAT, SHARP) + MODIFIERS

# ASCII character (as tulip.keyboard_callback delivers it) -> button.
DEFAULT_KEYMAP = {
    "1": "1", "2": "2", "3": "3", "4": "4", "5": "5", "6": "6", "7": "7",
    "8": "8", "9": "9",
    "u": "dim", "j": "7th", "m": "swap",
    "i": "sus4", "k": "M7", ",": "add9",
    " ": "stop",
    "-": "key-", "=": "key+",
    "[": "slot-", "]": "slot+",
    "<": "song-", ">": "song+",
    "\t": "mode",
}

# HID usage ids for the keys above (USB HID Usage Tables, keyboard page).
_HID_DIGITS = {str(d): 0x1e + d - 1 for d in range(1, 10)}
_HID_DIGITS["0"] = 0x27
_HID_PUNCT = {" ": 0x2c, "-": 0x2d, "=": 0x2e, "[": 0x2f, "]": 0x30,
              ",": 0x36, ".": 0x37, "<": 0x36, ">": 0x37, "\t": 0x2b}
_HID_KEYPAD = {str(d): 0x59 + d - 1 for d in range(1, 10)}   # KP1..KP9
_HID_KEYPAD["-"] = 0x56
_HID_KEYPAD["="] = 0x57                                       # KP+


def _scan_code(ch):
    if ch in _HID_DIGITS:
        return _HID_DIGITS[ch]
    if "a" <= ch <= "z":
        return 0x04 + ord(ch) - ord("a")
    return _HID_PUNCT.get(ch)


def build_scan_map(keymap):
    """ASCII keymap -> {scan_code: button}, with the numeric keypad doubling
    the digit row."""
    out = {}
    for ch, button in keymap.items():
        code = _scan_code(ch)
        if code is not None:
            out[code] = button
        if ch in _HID_KEYPAD:
            out[_HID_KEYPAD[ch]] = button
    return out


class Inputs:
    def __init__(self, listener=None, keymap=None):
        self.listener = listener
        self.keymap = dict(keymap or DEFAULT_KEYMAP)
        self.scan_map = build_scan_map(self.keymap)
        self.held = set()           # buttons currently down (all sources)
        self.order = []             # press order, oldest first
        self._ui = set()
        self._touch = set()
        self._midi = set()
        self._active = False

    # --- lifecycle --------------------------------------------------------

    def start(self):
        self._active = True
        tulip.keyboard_callback(self._on_key)

    def stop(self):
        self._active = False
        tulip.keyboard_callback()
        self._ui.clear()
        self._touch.clear()
        self._midi.clear()
        self.poll()

    # --- sources ----------------------------------------------------------

    def _keyboard_buttons(self):
        try:
            scans = tulip.keys()
        except AttributeError:
            return set()
        out = set()
        for code in scans[1:]:
            b = self.scan_map.get(code)
            if b is not None:
                out.add(b)
        return out

    def _on_key(self, c):
        # A press we may not have polled yet; the scan set already holds it
        # (the firmware publishes last_scan before scheduling this callback),
        # so one poll fires the edge with no frame of latency.
        self.poll()

    def ui_down(self, button):
        self._ui.add(button)
        self.poll()

    def ui_up(self, button):
        self._ui.discard(button)
        self.poll()

    def set_touch(self, buttons):
        """The pads currently under any finger (from the UI's hit test)."""
        buttons = set(buttons)
        if buttons != self._touch:
            self._touch = buttons
            self.poll()

    def midi_note(self, note, on):
        """White keys C..B -> roots 1..7 (any octave)."""
        white = {0: "1", 2: "2", 4: "3", 5: "4", 7: "5", 9: "6", 11: "7"}
        b = white.get(note % 12)
        if b is None:
            return
        if on:
            self._midi.add(b)
        else:
            self._midi.discard(b)
        self.poll()

    # --- edges ------------------------------------------------------------

    def poll(self):
        if not self._active:
            now = self._ui | self._touch
        else:
            now = self._keyboard_buttons() | self._ui | self._touch | self._midi
        if now == self.held:
            return
        # Edges that land in the same poll are ordered so a chord-type key
        # counts before the root it is held with (and lets go after it): two
        # fingers that touch within one frame play the intended chord.
        released = sorted(self.held - now, key=lambda b: b not in ROOTS)
        pressed = sorted(now - self.held, key=lambda b: b in ROOTS)
        self.held = now
        for b in released:
            if b in self.order:
                self.order.remove(b)
            if self.listener:
                self.listener(b, False)
        for b in pressed:
            self.order.append(b)
            if self.listener:
                self.listener(b, True)

    def is_down(self, button):
        return button in self.held

    def held_modifier(self):
        """The most recently pressed chord-type modifier (not swap), or ""."""
        for b in reversed(self.order):
            if b in MOD_CHORD:
                return MOD_CHORD[b]
        return ""

    def semitone_shift(self):
        return (1 if SHARP in self.held else 0) - (1 if FLAT in self.held else 0)

    def roots_down(self):
        return [b for b in self.order if b in ROOTS]
