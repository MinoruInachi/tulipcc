# ime.py
#
# Japanese input for Tulip: romaji to kana, kana to kanji, and a one-line 変換
# strip along the bottom of the console.
#
#     import ime
#     ime.start()          # arm it; Ctrl-\ then hands the keyboard back and forth
#
# 変換, かな, Ctrl-Space and Ctrl-\ all switch it on and off, handled in the key
# path itself so they work whoever owns the keyboard. No one default covers every
# keyboard: the Tab5's own 70-key one reaches backslash through a Sym layer, so
# Ctrl-\ cannot be pressed there, while a JIS keyboard has 変換 and no Ctrl-Space
# habit. ime.keytest() and ime.toggle() rebind it for anything else.
#
# HOW THE PIECES DIVIDE
#
# The C side is only plumbing (tulip.ime, tulip.ime_key, tulip.ime_callback, and
# see shared/keyscan.c). It exists because taking keys away from the REPL, LVGL
# and the editor has to happen synchronously in the key path, and because
# mp_sched_schedule() drops callbacks when its queue is full -- so keys are queued
# in C and drained from here once per frame instead of one callback per key.
#
# Everything else is here, because the dictionary is a deflate stream that only
# MicroPython's deflate module can open, and once the dictionary is in Python the
# conversion belongs in Python too.
#
# THE STRIP IS ONLY HALF THE INDICATOR
#
# The strip exists while you are composing, so between one word and the next
# there was nothing saying the keyboard was still Japanese. The cursor turns
# orange instead, for as long as the IME holds the keyboard, wherever the typing
# is going: the console's (display_tfb_cursor()), the editor's (move_cursor() in
# editor.c), and the focused LVGL text area's, which is the only one of the three
# that is not a TFB cell and so is done from here.
#
# Nothing on screen could be given up for a badge. The bottom row is where the
# console itself types once it has scrolled full, so a permanent bar there hides
# the line being worked on -- which is the whole reason the strip goes away
# between words. And a badge anywhere but column 0 is not even drawn: a row stops
# rendering at its first empty cell, so a four-cell badge at the right margin
# needs the whole row padded with spaces, which draws a full-width band. The
# cursor takes no space and is where the eye already is.
#
# WHY THE 変換 STRIP RATHER THAN INLINE PREEDIT
#
# Composing text has to be shown before it is committed, and the three places it
# can be committed to -- the editor, an LVGL text area, the REPL's readline -- draw
# text in three unrelated ways. Inline preedit means writing it three times.
# A strip on the bottom console row is written once, which is how Japanese FEPs
# did it before inline preedit existed. The row underneath is saved while
# composing and put back afterwards.
#
# WHY THE CONVERSION IS PER-SEGMENT AND NOT SMARTER
#
# Splitting a whole sentence the way Google or ATOK does needs a morphological
# analyser and a connection-cost model. What is here instead is 文節 conversion:
# space converts the longest reading the dictionary knows from the start of what
# you typed, and ← / → resize that segment. That needs nothing but a sorted
# dictionary and is what a 1980s FEP did -- learnable, predictable, and it fits.
#
# KATAKANA HAS ITS OWN KEY, AND WHY IT HAS TO
#
# Ctrl-I is カタカナ and Ctrl-U is ひらがな, over the whole reading and without
# consulting the dictionary -- F7 and F6 in MS-IME, ATOK and mozc, which is where
# the Ctrl- spelling comes from too. They are not a shortcut for cycling to the
# katakana candidate: that candidate only covers the prefix the dictionary
# matched, and a loanword has no entry at all, so cycling can never reach
# アイスクリーム however long you hold space.
#
# There is no 半角カナ (F8). The console font has no halfwidth katakana --
# U+FF61-FF9F is absent from efont Biwidth -- so it would commit text the screen
# cannot draw. 英数 (F9/F10) is what a capital letter and switching the IME off
# already do.

import tulip

DICT_PATH = "/sys/ime/jdic.z"

# Key codes, as scan_ascii() produces them.
_BS = 8
# Tab and Ctrl-I are the same byte, and Ctrl-I is F7 -- convert to katakana -- in
# MS-IME, ATOK and Google/mozc alike. While something is being composed that is
# what it means here too; with nothing composed it is still Tab and passes
# through, so the editor keeps its indent key.
_TAB = 9
_CTRL_U = 21            # F6: back to hiragana
_ENTER = 13
_ESC = 27
# The toggle key is deliberately not a constant here: tulip.ime_toggle() can
# rebind it, and a stale copy would leave C switching the IME on for a key
# Python then did not recognise -- on, but never off again.
_SPACE = 32
_DOWN = 258
_UP = 259
_LEFT = 260
_RIGHT = 261

# Composition states.
_OFF = 0                # not holding the keyboard
_KANA = 1               # gathering a reading
_CONV = 2               # a segment is converted; cycling candidates


# ---------------------------------------------------------------- romaji
#
# Longest match wins, so "sha" beats "sa" and "kk" is caught before "ka". Written
# out rather than generated from consonant x vowel rows because the exceptions
# (shi/chi/tsu/fu, the ya-row gaps, the small kana) outnumber the regular part.

_ROMAJI = {}
for _row in (
    "a あ|i い|u う|e え|o お",
    "ka か|ki き|ku く|ke け|ko こ|kya きゃ|kyi きぃ|kyu きゅ|kye きぇ|kyo きょ",
    "ga が|gi ぎ|gu ぐ|ge げ|go ご|gya ぎゃ|gyu ぎゅ|gyo ぎょ",
    "sa さ|si し|su す|se せ|so そ|sya しゃ|syu しゅ|syo しょ",
    "sha しゃ|shi し|shu しゅ|she しぇ|sho しょ",
    "za ざ|zi じ|zu ず|ze ぜ|zo ぞ|zya じゃ|zyu じゅ|zyo じょ",
    "ja じゃ|ji じ|ju じゅ|je じぇ|jo じょ|jya じゃ|jyu じゅ|jyo じょ",
    "ta た|ti ち|tu つ|te て|to と|tya ちゃ|tyu ちゅ|tyo ちょ",
    "cha ちゃ|chi ち|chu ちゅ|che ちぇ|cho ちょ",
    "tsa つぁ|tsi つぃ|tsu つ|tse つぇ|tso つぉ|thi てぃ|tha てゃ|tho てょ",
    "da だ|di ぢ|du づ|de で|do ど|dya ぢゃ|dyu ぢゅ|dyo ぢょ|dha でゃ|dhi でぃ",
    # No "nn -> ん" entry, deliberately. With one, "nn" matches first and eats both
    # letters, so konnichiwa comes out こんいちわ and mannaka まんあか. Without it the
    # rule below ('n' before anything that cannot continue な行 is ん) fires on the
    # first n and leaves the second to start にちわ. The cost is that a bare "nn"
    # with nothing after it is んん; "n'" is the explicit single ん.
    "na な|ni に|nu ぬ|ne ね|no の|nya にゃ|nyu にゅ|nyo にょ|n' ん",
    "ha は|hi ひ|hu ふ|he へ|ho ほ|hya ひゃ|hyu ひゅ|hyo ひょ",
    "ba ば|bi び|bu ぶ|be べ|bo ぼ|bya びゃ|byu びゅ|byo びょ",
    "pa ぱ|pi ぴ|pu ぷ|pe ぺ|po ぽ|pya ぴゃ|pyu ぴゅ|pyo ぴょ",
    "ma ま|mi み|mu む|me め|mo も|mya みゃ|myu みゅ|myo みょ",
    "ya や|yi いぃ|yu ゆ|ye いぇ|yo よ",
    "ra ら|ri り|ru る|re れ|ro ろ|rya りゃ|ryu りゅ|ryo りょ",
    "wa わ|wi うぃ|wu う|we うぇ|wo を|wha うぁ",
    "fa ふぁ|fi ふぃ|fu ふ|fe ふぇ|fo ふぉ|fya ふゃ|fyu ふゅ|fyo ふょ",
    "va ゔぁ|vi ゔぃ|vu ゔ|ve ゔぇ|vo ゔぉ",
    "xa ぁ|xi ぃ|xu ぅ|xe ぇ|xo ぉ|xya ゃ|xyu ゅ|xyo ょ|xtu っ|xtsu っ|xn ん",
    "la ぁ|li ぃ|lu ぅ|le ぇ|lo ぉ|lya ゃ|lyu ゅ|lyo ょ|ltu っ",
):
    for _pair in _row.split("|"):
        _k, _v = _pair.split(" ")
        _ROMAJI[_k] = _v
del _row, _pair, _k, _v

# Typed as-is rather than romaji. Everything else printable is passed through to
# whatever is underneath.
_PUNCT = {",": "、", ".": "。", "-": "ー", "[": "「", "]": "」", "/": "・",
          "!": "！", "?": "？", "~": "〜"}

# The vowels a syllable can end on, for deciding whether a lone 'n' is ん yet.
_VOWELS = "aiueo"

# The palette entry the console and the editor paint their cursor with while the
# IME is on (IME_CURSOR_COLOR in display.h). Same number here so an LVGL text
# area's cursor is the same colour as the other two.
_CURSOR_PAL = 244

_HIRA_MIN = 0x3041
_HIRA_MAX = 0x3096


def to_katakana(s):
    return "".join(chr(ord(c) + 0x60) if _HIRA_MIN <= ord(c) <= _HIRA_MAX else c
                   for c in s)


def _romaji_step(pending):
    """Consume as much of `pending` as is unambiguously decided.

    Returns (kana, pending). Called after each keystroke, so at most one syllable
    comes out per call -- except for っ and ん, which are decided by the letter
    after them and so come out together with it.
    """
    out = ""
    while pending:
        if pending in _ROMAJI:
            out += _ROMAJI[pending]
            pending = ""
            break
        # Still a prefix of something: wait for the next letter.
        prefix = False
        for k in _ROMAJI:
            if k.startswith(pending):
                prefix = True
                break
        if prefix:
            break
        # Not a prefix of anything, so the first letter is decided one way or
        # another. Doubled consonant -> っ, and the doubled letter starts again.
        if len(pending) >= 2 and pending[0] == pending[1] and pending[0] not in _VOWELS + "n":
            out += "っ"
            pending = pending[1:]
            continue
        # 'n' followed by anything that cannot continue な行 is ん.
        if pending[0] == "n" and len(pending) >= 2 and pending[1] not in _VOWELS + "y'":
            out += "ん"
            pending = pending[1:]
            continue
        # Junk. Emit it literally so a typo is visible rather than swallowed.
        out += pending[0]
        pending = pending[1:]
    return out, pending


def _romaji_flush(pending):
    """Whatever is left in `pending` when the reading ends.

    A trailing 'n' is ん -- that is the one case romaji leaves genuinely undecided
    until something follows it. Anything else was never going to become kana and
    comes out as the letters that were typed, so a typo stays visible.
    """
    out = ""
    while pending:
        if pending in _ROMAJI:
            out += _ROMAJI[pending]
            pending = ""
        elif pending[0] == "n":
            out += "ん"
            pending = pending[1:]
        else:
            out += pending[0]
            pending = pending[1:]
    return out


# ---------------------------------------------------------------- dictionary

class Dictionary:
    """Reading to candidates, over the deflate stream in /sys/jdic.z.

    The file decompresses to about 1.8MB of lines sorted by reading, and is
    searched in place. An index of its 59073 line offsets would be a Python list
    of 59073 ints -- more memory than the dictionary itself -- so this binary
    searches the buffer and snaps each probe back to the nearest line start.
    """

    def __init__(self, path=DICT_PATH):
        import deflate
        f = open(path, "rb")
        try:
            # "JDIC1 <10 digits>\n", then the zlib stream. The length is in the
            # file because deflate.read() with no size argument grows its result
            # as it goes: 16.5s on this file, against 2.0s for readinto() into a
            # bytearray of the right size. Both measured on a Tab5.
            head = f.read(17)
            if not head.startswith(b"JDIC1 "):
                raise ValueError("not a jdic file")
            size = int(head[6:16])
            buf = bytearray(size)
            mv = memoryview(buf)
            d = deflate.DeflateIO(f, deflate.ZLIB)
            got = 0
            while got < size:
                n = d.readinto(mv[got:])
                if not n:
                    break
                got += n
            if got != size:
                raise ValueError("jdic truncated: %d of %d" % (got, size))
            self.buf = bytes(buf)
        finally:
            f.close()
        # Skip the ';;' licence header; everything after it is sorted.
        self.base = 0
        while self.base < len(self.buf) and self.buf[self.base] == 0x3b:  # ';'
            nl = self.buf.find(b"\n", self.base)
            if nl < 0:
                break
            self.base = nl + 1

    def lookup(self, reading):
        key = reading.encode()
        buf = self.buf
        lo, hi = self.base, len(buf)
        while lo < hi:
            mid = (lo + hi) >> 1
            ls = buf.rfind(b"\n", lo, mid)
            ls = lo if ls < 0 else ls + 1
            le = buf.find(b"\n", ls)
            if le < 0:
                le = hi
            sp = buf.find(b" ", ls, le)
            if sp < 0:
                return None
            k = buf[ls:sp]
            if k == key:
                # "よみ /a/b/c/" -> [a, b, c]
                return buf[sp + 2:le - 1].decode().split("/")
            if k < key:
                lo = le + 1
            else:
                # ls <= mid < hi, so this always narrows and the loop ends.
                hi = ls
        return None

    def longest(self, reading):
        """Longest prefix of `reading` in the dictionary: (length, candidates)."""
        for n in range(len(reading), 0, -1):
            got = self.lookup(reading[:n])
            if got:
                return n, got
        return 0, None


# ---------------------------------------------------------------- the IME

class IME:
    def __init__(self):
        self.state = _OFF
        self.pending = ""       # romaji not yet a kana
        self.reading = ""       # kana gathered so far
        self.committed = ""     # segments already accepted this composition
        self.seg_len = 0        # kana of `reading` the current segment covers
        self.cands = []
        self.cand = 0
        self.dict = None
        self.target = None      # None = auto; or anything with .add_text(str)
        self._saved_row = None
        self._row = None
        self._lv_obj = None     # the text area whose cursor we recoloured
        self._lv_was = None     # and the colour it had before
        self._lv_tick = 0

    # -- dictionary ------------------------------------------------------
    def load(self, path=DICT_PATH):
        if self.dict is None:
            try:
                self.dict = Dictionary(path)
            except Exception as e:
                print("ime: no dictionary at %s (%s)" % (path, e))
                print("ime: kana input still works; run fs_create.py to install it")
                self.dict = False
        return self.dict

    # -- the strip -------------------------------------------------------
    def _need_japanese_font(self):
        """The 変換 strip is drawn through the TFB, and the TFB's other three fonts
        are CP437 -- they would drop every character the strip exists to show. So
        switch once, on the first activation, and then leave the console alone:
        the fonts differ in both geometry and encoding, so flipping back and forth
        with the IME would reflow the whole console every time.
        """
        try:
            if tulip.tfb_font() not in (3, 4):
                tulip.tfb_font(3)
        except ValueError:
            # A board built without the Japanese font. Kana will not show on the
            # strip, but committing still works, so this is not fatal.
            pass

    # -- the LVGL cursor --------------------------------------------------
    def _lv_focused(self):
        try:
            import lvgl as lv
            obj = lv.group_get_default().get_focused()
        except Exception:
            return None
        # add_text is what makes it something the IME can commit into, and so
        # also what makes its cursor worth colouring.
        return obj if obj is not None and hasattr(obj, "add_text") else None

    def _lv_cursor(self, on):
        """Match the console's cursor colour on the focused LVGL text area.

        A text area draws its cursor as a two-pixel border on PART.CURSOR, so
        that is the property to set -- and it has to be set for STATE.FOCUSED,
        because the theme styles that state and a local style on the default
        state loses to it. The theme's own colour is read before the first set,
        or it would read back as ours.

        Every call is guarded: an app can delete the text area while the IME
        still holds the keyboard, and the binding raises LvReferenceError rather
        than following a dangling pointer.
        """
        try:
            import lvgl as lv
            sel = lv.PART.CURSOR | lv.STATE.FOCUSED
            obj = self._lv_focused() if on else None
            if self._lv_obj is not None and self._lv_obj is not obj:
                try:
                    self._lv_obj.set_style_border_color(self._lv_was, sel)
                except Exception:
                    pass        # deleted out from under us; nothing to put back
                self._lv_obj = None
                self._lv_was = None
            if obj is None or self._lv_obj is obj:
                return
            self._lv_was = obj.get_style_border_color(lv.PART.CURSOR)
            self._lv_obj = obj
            obj.set_style_border_color(tulip.pal_to_lv(_CURSOR_PAL), sel)
        except Exception:
            self._lv_obj = None
            self._lv_was = None

    def _strip_row(self):
        cols, rows = tulip.tfb_size()
        if rows == 0 or cols == 0:
            return None, 0
        return rows - 1, cols

    def _save_strip(self):
        if self._saved_row is not None:
            return
        row, cols = self._strip_row()
        if row is None:
            return
        self._row = row
        # The app cannot print while the IME holds the keyboard, so this row will
        # not change underneath us until the composition ends.
        # Four values on Tab5, five on the other boards, so index rather than
        # unpack.
        self._saved_row = [tulip.tfb_str(x, row)[:4] for x in range(cols)]

    def _restore_strip(self):
        if self._saved_row is None:
            return
        row = self._row
        for x in range(len(self._saved_row)):
            ch, fmt, fg, bg = self._saved_row[x]
            tulip.tfb_str(x, row, ch if ch else " ", fmt, fg, bg)
        self._saved_row = None

    def _draw_strip(self):
        row, cols = self._strip_row()
        if row is None:
            return
        self._save_strip()
        text = self._strip_text()
        # Inverse video, because FORMAT_UNDERLINE is parsed out of ANSI but the
        # TFB renderer only draws FORMAT_INVERSE.
        tulip.tfb_str(0, row, (text + " " * cols)[:cols], 0x80, 255, 0)

    def _refresh(self):
        """The strip is up only while there is something on it.

        Between words there is nothing to show, and the bottom row belongs to the
        console again; the orange cursor is what says the IME still has the
        keyboard.
        """
        if self.state != _OFF and (self.state == _CONV or self.reading
                                   or self.pending or self.committed):
            self._draw_strip()
        else:
            self._restore_strip()

    def _strip_text(self):
        head = "[あ] " + self.committed
        if self.state == _CONV:
            head += self.cands[self.cand] + self.reading[self.seg_len:]
            head += "  %d/%d" % (self.cand + 1, len(self.cands))
        else:
            head += self.reading + self.pending
        return head

    # -- commit ----------------------------------------------------------
    def _deliver(self, text):
        if not text:
            return
        if self.target is not None:
            self.target.add_text(text)
            return
        # An LVGL text area with focus wants it first; the binding differs between
        # versions, so any failure just falls through to the console.
        try:
            import lvgl as lv
            focused = lv.group_get_default().get_focused()
            if focused is not None and hasattr(focused, "add_text"):
                focused.add_text(text)
                return
        except Exception:
            pass
        # The editor keeps its own buffer, and its per-key insert takes one byte.
        # sys.modules rather than import: if nobody has opened the editor there is
        # no reason for the IME to be the thing that loads it.
        try:
            import sys
            ed = sys.modules.get("editor")
            if ed is not None and ed.editor is not None and ed.editor.active:
                tulip.editor_insert(text)
                return
        except Exception:
            pass
        tulip.key_send_str(text)

    def _commit_all(self):
        text = self.committed
        if self.state == _CONV:
            text += self.cands[self.cand] + self.reading[self.seg_len:]
        else:
            text += self.reading + self.pending
        self._reset_composition()
        self._deliver(text)

    def _reset_composition(self):
        self.pending = ""
        self.reading = ""
        self.committed = ""
        self.seg_len = 0
        self.cands = []
        self.cand = 0
        self.state = _KANA
        self._restore_strip()

    # -- conversion ------------------------------------------------------
    def _convert(self):
        d = self.load()
        n, cands = (0, None)
        if d:
            n, cands = d.longest(self.reading)
        if not cands:
            # Nothing matched. Offer the mechanical conversions so space still
            # does something useful, over the whole reading.
            n, cands = len(self.reading), []
        self.seg_len = n
        seg = self.reading[:n]
        self.cands = list(cands)
        for extra in (to_katakana(seg), seg):
            if extra and extra not in self.cands:
                self.cands.append(extra)
        self.cand = 0
        self.state = _CONV

    def _kana_convert(self, katakana):
        """F7 / F6: the whole reading as katakana or as hiragana, no dictionary.

        Not the same thing as cycling to the katakana candidate that _convert()
        appends. That one only covers the segment the dictionary matched, and the
        match is a prefix: type aisukuri-mu and the longest reading in there is
        あい, so space offers 愛/相 and the katakana on the end of that list is
        アイ -- never アイスクリーム. A loanword has no dictionary entry by
        definition, which is exactly why every IME gives katakana its own key.
        """
        if self.pending:
            self.reading += _romaji_flush(self.pending)
            self.pending = ""
        if not self.reading:
            return False
        seg = self.reading
        kata = to_katakana(seg)
        self.seg_len = len(seg)
        self.cands = [kata, seg] if katakana else [seg, kata]
        if self.cands[0] == self.cands[1]:
            del self.cands[1]      # no kana in it; one candidate, not two
        self.cand = 0
        self.state = _CONV
        return True

    def _pick_kana(self, katakana):
        """F7 / F6 during conversion: switch this segment to kana in place.

        The segment keeps its length, so this composes with the ← / → resizing
        rather than fighting it.
        """
        seg = self.reading[:self.seg_len]
        want = to_katakana(seg) if katakana else seg
        if not want:
            return
        if want in self.cands:
            self.cand = self.cands.index(want)
        else:
            self.cands.append(want)
            self.cand = len(self.cands) - 1

    def _resize_segment(self, delta):
        n = self.seg_len + delta
        if n < 1 or n > len(self.reading):
            return
        d = self.load()
        seg = self.reading[:n]
        cands = (d.lookup(seg) if d else None) or []
        self.seg_len = n
        self.cands = list(cands)
        for extra in (to_katakana(seg), seg):
            if extra and extra not in self.cands:
                self.cands.append(extra)
        self.cand = 0

    def _accept_segment(self):
        """Take the current candidate and start on what is left."""
        self.committed += self.cands[self.cand]
        self.reading = self.reading[self.seg_len:]
        self.seg_len = 0
        self.cands = []
        self.cand = 0
        if self.reading:
            self._convert()
        else:
            self.state = _KANA

    # -- key handling ----------------------------------------------------
    def _forward(self, key):
        # False: skip the IME on the way out, or this hands the key straight back.
        tulip.key_send(key, False)

    def key(self, k):
        if k == tulip.ime_toggle():
            if self.state == _OFF:
                # Turning it on. C has already armed ime_active so the drain runs;
                # this press is the IME's cue to take the console and, the first
                # time round, to read the dictionary.
                self._need_japanese_font()
                self.state = _KANA
                self.load()
                self._refresh()
                # C armed ime_active from the key path already. Setting it again
                # from here is what repaints the cursor: the key path runs on the
                # keyboard task and must not touch the console.
                tulip.ime(True)
                self._lv_cursor(True)
            else:
                # Handing the keyboard back. Anything half-composed is committed
                # rather than thrown away, which is what every IME does on the way
                # out.
                self._commit_all()
                self.state = _OFF
                self._refresh()
                self._lv_cursor(False)
                tulip.ime(False)
            return

        if self.state == _OFF:
            # A key arrived without the toggle having turned us on -- C armed the
            # flag and something else queued behind it. Treat it as switched on.
            self._need_japanese_font()
            self.state = _KANA

        if self.state == _CONV:
            if k == _SPACE or k == _DOWN:
                self.cand = (self.cand + 1) % len(self.cands)
            elif k == _UP:
                self.cand = (self.cand - 1) % len(self.cands)
            elif k == _RIGHT:
                self._accept_segment()
            elif k == _LEFT:
                self._resize_segment(-1)
            elif k == _TAB or k == _CTRL_U:
                self._pick_kana(k == _TAB)
            elif k == _ENTER:
                self._commit_all()
                return
            elif k == _BS:
                # Back to the reading, unconverted.
                self.state = _KANA
                self.cands = []
                self.seg_len = 0
            elif k == _ESC:
                self.state = _KANA
                self.cands = []
                self.seg_len = 0
            else:
                # Anything else ends the conversion and is then handled fresh.
                self._commit_all()
                self.key(k)
                return
            self._refresh()
            return

        # _KANA: gathering a reading.
        if k == _BS:
            if self.pending:
                self.pending = self.pending[:-1]
            elif self.reading:
                self.reading = self.reading[:-1]
            elif self.committed:
                self.committed = self.committed[:-1]
            else:
                self._restore_strip()
                self._forward(k)
                return
            self._refresh()
            return

        if k == _ESC:
            if self.reading or self.pending or self.committed:
                self._reset_composition()
            else:
                self._forward(k)
            return

        if k == _ENTER:
            if self.reading or self.pending or self.committed:
                self._commit_all()
            else:
                self._forward(k)
            return

        if k == _SPACE:
            if self.pending:
                self.reading += _romaji_flush(self.pending)
                self.pending = ""
            if self.reading:
                self._convert()
                self._refresh()
            else:
                self._forward(k)
            return

        if k == _TAB or k == _CTRL_U:
            if self._kana_convert(k == _TAB):
                self._refresh()
            else:
                self._forward(k)
            return

        if k in (_LEFT, _RIGHT, _UP, _DOWN):
            if self.reading or self.pending or self.committed:
                self._commit_all()
            self._forward(k)
            return

        if k > 126 or k < 32:
            # Control keys and the arrow-key range: commit and let them through.
            if self.reading or self.pending or self.committed:
                self._commit_all()
            self._forward(k)
            return

        ch = chr(k)
        if ch in _PUNCT:
            if self.pending:
                self.reading += _romaji_flush(self.pending)
                self.pending = ""
            self.reading += _PUNCT[ch]
            self._refresh()
            return

        if "a" <= ch <= "z":
            self.pending += ch
            kana, self.pending = _romaji_step(self.pending)
            self.reading += kana
            self._refresh()
            return

        if "A" <= ch <= "Z":
            # Capitals are how you type a word that should stay Latin.
            if self.reading or self.pending or self.committed:
                self._commit_all()
            self._forward(k)
            return

        # Digits and the rest of ASCII pass through -- but the reading in front of
        # them has to be committed first, or the digit lands before it.
        if self.reading or self.pending or self.committed:
            self._commit_all()
        self._forward(k)

    # -- the per-frame drain ---------------------------------------------
    def poll(self, _arg=None):
        for _ in range(64):
            k = tulip.ime_key()
            if k is None:
                break
            self.key(k)
        # Follow the LVGL focus. An app can move it without the IME ever seeing a
        # key, and the coloured cursor has to go with it. Every fifteenth frame is
        # often enough for something a person does with a finger, and keeps this
        # off the per-key path.
        if self.state != _OFF:
            self._lv_tick += 1
            if self._lv_tick >= 15:
                self._lv_tick = 0
                self._lv_cursor(True)


_ime = IME()


def start(load_dictionary=False):
    """Arm the IME, so the toggle key starts working.

    Cheap by default: the dictionary is a couple of seconds to read and is left
    until the IME is first switched on, so that this can sit in boot.py without
    costing anything at boot. Pass True to pay it now instead.
    """
    tulip.ime_callback(_ime.poll)
    if load_dictionary:
        _ime._need_japanese_font()
        _ime.load()
    print("ime: ready -- 変換, かな, Ctrl-Space or Ctrl-\\ to switch 日本語 on and off")


def stop():
    if _ime.state != _OFF:
        _ime.state = _OFF
        _ime._refresh()
    _ime._lv_cursor(False)
    tulip.ime(False)
    tulip.ime_callback()


def target(obj=None):
    """Send committed text to `obj` (anything with .add_text) instead of guessing."""
    _ime.target = obj


def toggle(code=None):
    """Read or set the key that switches the IME on and off.

    Defaults cover the keyboards this was built for -- 変換 and かな on a JIS
    keyboard, Ctrl-Space and Ctrl-\\ elsewhere -- but a keyboard that cannot
    produce any of them can be pointed at whatever it does produce:

        ime.keytest()            # press the key, note the number it prints
        ime.toggle(<number>)     # and bind it
    """
    if code is None:
        return tulip.ime_toggle()
    tulip.ime_toggle(code)


def keytest(on=True):
    """Print what each key produces, to find the one that toggles the IME.

    28 is the toggle. If the key you want prints something else, point it at 28:

        tulip.key_remap(<scan>, 0, 0x1c)

    where <scan> is the raw HID code this also prints. Takes over
    tulip.keyboard_callback() while it runs, so the editor will not see keys --
    ime.keytest(False) puts it back.
    """
    if not on:
        tulip.keyboard_callback()
        print("ime: key test off")
        return

    def _report(k):
        held = tulip.keys()
        print("key %d   modifier 0x%02x   scan %s"
              % (k, held[0], [h for h in held[1:] if h]))

    tulip.keyboard_callback(_report)
    print("ime: press keys. %d is the toggle. ime.keytest(False) to stop."
          % tulip.ime_toggle())
