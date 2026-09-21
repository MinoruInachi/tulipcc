"""Song data for loopstudio: channels, patterns, the playlist, JSON files.

Nothing here touches tulip, amy or lvgl, so it runs (and is tested) under
CPython: tulip/tests/test_loopstudio.py.

A pattern holds, per channel, a list of notes [step, length, pitch, vel]:
step and length in 16ths, pitch a MIDI note, vel 1..127. The channel rack's
step buttons and the piano roll edit the same lists -- a lit step is just a
one-step note at the channel's root pitch, as in FL Studio.
"""
import json

STEPS_PER_BAR = 16
NUM_CHANNELS = 8
NUM_PATTERNS = 8
MAX_PATTERN_BARS = 2
SONG_BARS = 32              # width of the playlist
NOTE_MIN, NOTE_MAX = 12, 119
DEFAULT_VEL = 100
FORMAT = 1

DRUM, SYNTH = "drum", "synth"

# Drum channel sounds: (name, General MIDI note). The kit patches map GM
# notes to samples (synth.DrumSynth), so these work on every kit.
DRUM_SOUNDS = [
    ("Kick", 36), ("Kick 2", 35), ("Snare", 38), ("Snare 3", 40), ("Rimshot", 37),
    ("Clap", 39), ("Closed Hat", 42), ("Open Hat", 46), ("Shaker", 70),
    ("Low Tom", 45), ("High Tom", 48), ("Cymbal", 49), ("Cowbell", 56),
    ("Clave", 75), ("Conga Hi", 63), ("Conga Mid", 62), ("Conga Lo", 64),
]
DRUM_KITS = [(384, "TR-808"), (385, "TR-909"), (386, "Linn 9000"), (387, "MR-12"),
             (389, "Power")]

# Instruments the rack steps through: (name, AMY patch). Juno-106 presets are
# 0..127, DX7 128..255, 256 is the sampled piano (dearer to render).
INSTRUMENTS = [
    ("Synth Bass", 36), ("Bass Pluck", 87), ("DX Bass", 142), ("DX SynBass", 238),
    ("Saw Lead", 33), ("Square Lead", 32), ("Funky", 30), ("Sharp Reed", 86),
    ("E.Piano", 14), ("DX Piano", 135), ("Piano", 256), ("Organ", 8),
    ("Clavinet", 41), ("Nylon Gtr", 73), ("DX Guitar", 139), ("Koto", 28),
    ("Vibes", 148), ("Marimba", 149), ("Glocken", 213), ("Harp", 188),
    ("Strings", 64), ("Moving Str", 4), ("DX Strings", 131), ("Choir", 6),
    ("Synth Pad", 47), ("Ethereal", 101), ("Brass", 0), ("DX Brass", 128),
    ("Flute", 3), ("DX Flute", 151),
]


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def drum_name(note):
    for name, n in DRUM_SOUNDS:
        if n == note:
            return name
    return "Drum %d" % note


def instrument_name(patch):
    for name, p in INSTRUMENTS:
        if p == patch:
            return name
    return "Patch %d" % patch


def _step_choice(choices, current, step):
    """The entry `step` places after the one whose value is `current`."""
    values = [c[1] for c in choices]
    i = values.index(current) if current in values else 0
    return choices[(i + step) % len(choices)]


class Channel:
    def __init__(self, kind=SYNTH, patch=33, root=60, voices=3, volume=0.8, pan=0.5):
        self.kind = kind
        self.patch = patch          # AMY patch (synth) or GM drum note (drum)
        self.root = root            # pitch a step button plays
        self.voices = voices
        self.volume = volume
        self.pan = pan
        self.mute = False
        self.solo = False

    @property
    def is_drum(self):
        return self.kind == DRUM

    @property
    def name(self):
        return drum_name(self.patch) if self.is_drum else instrument_name(self.patch)

    def next_sound(self, step):
        """Step to the next drum sound or instrument. A drum channel's root
        follows its sound; what is already in the patterns moves with it
        (see Song.change_sound)."""
        if self.is_drum:
            self.patch = _step_choice(DRUM_SOUNDS, self.patch, step)[1]
            self.root = self.patch
        else:
            self.patch = _step_choice(INSTRUMENTS, self.patch, step)[1]

    def to_dict(self):
        return dict(kind=self.kind, patch=self.patch, root=self.root, voices=self.voices,
                    volume=round(self.volume, 3), pan=round(self.pan, 3),
                    mute=self.mute, solo=self.solo)

    @classmethod
    def from_dict(cls, d):
        c = cls(kind=DRUM if d.get("kind") == DRUM else SYNTH,
                patch=int(d.get("patch", 33)),
                root=_clamp(int(d.get("root", 60)), NOTE_MIN, NOTE_MAX),
                voices=_clamp(int(d.get("voices", 3)), 1, 6),
                volume=_clamp(float(d.get("volume", 0.8)), 0.0, 1.0),
                pan=_clamp(float(d.get("pan", 0.5)), 0.0, 1.0))
        c.mute = bool(d.get("mute", False))
        c.solo = bool(d.get("solo", False))
        return c


class Pattern:
    def __init__(self, bars=1):
        self.bars = bars
        self.notes = [[] for _ in range(NUM_CHANNELS)]
        self._by_step = None

    @property
    def steps(self):
        return self.bars * STEPS_PER_BAR

    def is_empty(self):
        return not any(self.notes)

    def set_bars(self, bars):
        """Shortening drops the notes that start past the new end and trims
        the ones that run over it."""
        self.bars = _clamp(bars, 1, MAX_PATTERN_BARS)
        n = self.steps
        for ch in range(NUM_CHANNELS):
            kept = [x for x in self.notes[ch] if x[0] < n]
            for x in kept:
                x[1] = min(x[1], n - x[0])
            self.notes[ch] = kept
        self._by_step = None

    # --- editing -----------------------------------------------------------

    def find(self, ch, step, pitch):
        """The note at `pitch` sounding on `step` (it may have started earlier)."""
        for x in self.notes[ch]:
            if x[2] == pitch and x[0] <= step < x[0] + x[1]:
                return x
        return None

    def starts_at(self, ch, step):
        return [x for x in self.notes[ch] if x[0] == step]

    def add(self, ch, step, pitch, length=1, vel=DEFAULT_VEL):
        """Add a note, clearing same-pitch notes it would overlap."""
        if not (0 <= step < self.steps):
            return None
        length = _clamp(length, 1, self.steps - step)
        pitch = _clamp(pitch, NOTE_MIN, NOTE_MAX)
        end = step + length
        self.notes[ch] = [x for x in self.notes[ch]
                          if not (x[2] == pitch and x[0] < end and step < x[0] + x[1])]
        note = [step, length, pitch, _clamp(int(vel), 1, 127)]
        self.notes[ch].append(note)
        self._by_step = None
        return note

    def remove(self, ch, note):
        try:
            self.notes[ch].remove(note)
        except ValueError:
            return
        self._by_step = None

    def resize(self, ch, note, length):
        """Set a note's length, stopping short of the next same-pitch note."""
        limit = self.steps - note[0]
        for x in self.notes[ch]:
            if x is not note and x[2] == note[2] and x[0] > note[0]:
                limit = min(limit, x[0] - note[0])
        note[1] = _clamp(length, 1, limit)
        self._by_step = None

    def toggle_step(self, ch, step, pitch):
        """A channel-rack step button: clears whatever starts on the step,
        or adds a one-step note at `pitch`. Returns True if a note was added."""
        here = self.starts_at(ch, step)
        if here:
            for x in here:
                self.remove(ch, x)
            return False
        self.add(ch, step, pitch)
        return True

    def clear_channel(self, ch):
        self.notes[ch] = []
        self._by_step = None

    def clear(self):
        self.notes = [[] for _ in range(NUM_CHANNELS)]
        self._by_step = None

    def repitch(self, ch, old, new):
        for x in self.notes[ch]:
            if x[2] == old:
                x[2] = new
        self._by_step = None

    # --- playback ----------------------------------------------------------

    def events_at(self, step):
        """[(channel index, note), ...] starting on `step`."""
        if self._by_step is None:
            index = {}
            for ch in range(NUM_CHANNELS):
                for x in self.notes[ch]:
                    index.setdefault(x[0], []).append((ch, x))
            self._by_step = index
        return self._by_step.get(step, ())

    def to_dict(self):
        return dict(bars=self.bars, notes=[[list(x) for x in ch] for ch in self.notes])

    @classmethod
    def from_dict(cls, d):
        p = cls(bars=_clamp(int(d.get("bars", 1)), 1, MAX_PATTERN_BARS))
        for ch, notes in enumerate(d.get("notes", [])[:NUM_CHANNELS]):
            for x in notes:
                if len(x) >= 3:
                    p.add(ch, int(x[0]), int(x[2]), int(x[1]), int(x[3]) if len(x) > 3 else DEFAULT_VEL)
        return p


class Song:
    def __init__(self, name="untitled"):
        self.name = name
        self.bpm = 120
        self.swing = 0              # 0..100: how far the off 16ths are pushed late
        self.kit = DRUM_KITS[0][0]
        self.master = 0.8
        self.reverb = 0.0
        self.chorus = 0.0
        self.echo = 0.0
        self.channels = default_channels()
        self.patterns = [Pattern() for _ in range(NUM_PATTERNS)]
        self.clips = [[] for _ in range(NUM_PATTERNS)]    # start bars, per pattern

    # --- channels ----------------------------------------------------------

    def change_sound(self, ch, step):
        """Step a channel's sound. A drum channel's step notes follow it to
        the new drum, in every pattern."""
        c = self.channels[ch]
        old = c.root
        c.next_sound(step)
        if c.is_drum and c.root != old:
            for p in self.patterns:
                p.repitch(ch, old, c.root)

    def next_kit(self, step):
        self.kit = _step_choice(DRUM_KITS, self.kit, step)[0]

    def kit_name(self):
        for patch, name in DRUM_KITS:
            if patch == self.kit:
                return name
        return "Kit %d" % self.kit

    def audible(self):
        """Per channel: does it sound, given mutes and solos?"""
        solo = any(c.solo for c in self.channels)
        return [(c.solo if solo else not c.mute) for c in self.channels]

    # --- playlist ----------------------------------------------------------

    def clip_at(self, pattern, bar):
        """Start bar of the clip of `pattern` covering `bar`, or None."""
        n = self.patterns[pattern].bars
        for start in self.clips[pattern]:
            if start <= bar < start + n:
                return start
        return None

    def toggle_clip(self, pattern, bar):
        """Tap a playlist cell: remove the clip under it, or place one
        (pushing out any clip of the same pattern it would overlap)."""
        start = self.clip_at(pattern, bar)
        if start is not None:
            self.clips[pattern].remove(start)
            return False
        n = self.patterns[pattern].bars
        if bar + n > SONG_BARS:
            return False
        self.clips[pattern] = sorted([s for s in self.clips[pattern]
                                      if not (s < bar + n and bar < s + n)] + [bar])
        return True

    def fix_clips(self, pattern):
        """After a pattern's length changed: drop clips that now overlap
        their neighbour or run off the end."""
        n = self.patterns[pattern].bars
        kept = []
        for s in sorted(self.clips[pattern]):
            if s + n <= SONG_BARS and (not kept or kept[-1] + n <= s):
                kept.append(s)
        self.clips[pattern] = kept

    def song_bars(self):
        end = 0
        for p, starts in enumerate(self.clips):
            if starts:
                end = max(end, max(starts) + self.patterns[p].bars)
        return end

    def events_at_song_step(self, step):
        """[(channel, note), ...] for an absolute 16th of the arrangement."""
        bar, s = divmod(step, STEPS_PER_BAR)
        out = []
        for p in range(NUM_PATTERNS):
            start = self.clip_at(p, bar)
            if start is not None:
                out.extend(self.patterns[p].events_at((bar - start) * STEPS_PER_BAR + s))
        return out

    # --- files -------------------------------------------------------------

    def to_dict(self):
        return dict(format=FORMAT, name=self.name, bpm=self.bpm, swing=self.swing, kit=self.kit,
                    master=round(self.master, 3), reverb=round(self.reverb, 3),
                    chorus=round(self.chorus, 3), echo=round(self.echo, 3),
                    channels=[c.to_dict() for c in self.channels],
                    patterns=[p.to_dict() for p in self.patterns],
                    clips=[list(c) for c in self.clips])

    @classmethod
    def from_dict(cls, d):
        s = cls(str(d.get("name", "untitled")))
        s.bpm = _clamp(int(d.get("bpm", 120)), 40, 240)
        s.swing = _clamp(int(d.get("swing", 0)), 0, 100)
        s.kit = int(d.get("kit", DRUM_KITS[0][0]))
        s.master = _clamp(float(d.get("master", 0.8)), 0.0, 1.0)
        s.reverb = _clamp(float(d.get("reverb", 0)), 0.0, 1.0)
        s.chorus = _clamp(float(d.get("chorus", 0)), 0.0, 1.0)
        s.echo = _clamp(float(d.get("echo", 0)), 0.0, 1.0)
        chans = [Channel.from_dict(c) for c in d.get("channels", [])][:NUM_CHANNELS]
        s.channels = chans + default_channels()[len(chans):]
        pats = [Pattern.from_dict(p) for p in d.get("patterns", [])][:NUM_PATTERNS]
        s.patterns = pats + [Pattern() for _ in range(NUM_PATTERNS - len(pats))]
        for p, starts in enumerate(d.get("clips", [])[:NUM_PATTERNS]):
            s.clips[p] = sorted(set(int(b) for b in starts if 0 <= int(b) < SONG_BARS))
            s.fix_clips(p)
        return s


def default_channels():
    return [
        Channel(DRUM, 36, 36, 1, 0.9),
        Channel(DRUM, 38, 38, 1, 0.8),
        Channel(DRUM, 42, 42, 1, 0.6),
        Channel(DRUM, 39, 39, 1, 0.7),
        Channel(SYNTH, 36, 36, 2, 0.8),      # bass
        Channel(SYNTH, 33, 72, 2, 0.6),      # lead
        Channel(SYNTH, 14, 60, 3, 0.6),      # keys
        Channel(SYNTH, 47, 60, 3, 0.5),      # pad
    ]


def load(path):
    with open(path) as f:
        return Song.from_dict(json.load(f))


def save(song, path):
    with open(path, "w") as f:
        f.write(json.dumps(song.to_dict()))


def demo():
    """The song a first run opens with: four patterns and an arrangement."""
    s = Song("demo")
    s.bpm = 124
    KICK, SNARE, HAT, CLAP, BASS, LEAD, KEYS, PAD = range(8)
    # Am - F - C - G, one bar each, over two 2-bar patterns.
    chords = [(57, 60, 64), (53, 57, 60), (48, 52, 55), (55, 59, 62)]
    roots = [33, 29, 36, 31]

    beat = s.patterns[0]
    for st in (0, 4, 8, 12):
        beat.add(KICK, st, 36)
    for st in (4, 12):
        beat.add(SNARE, st, 38)
        beat.add(CLAP, st, 39, vel=70)
    for st in range(0, 16, 2):
        beat.add(HAT, st, 42, vel=110 if st % 4 == 0 else 70)
    beat.add(HAT, 15, 42, vel=60)

    for pi, pair in ((1, (0, 1)), (2, (2, 3))):
        p = s.patterns[pi]
        p.set_bars(2)
        for bar, ci in enumerate(pair):
            base = bar * STEPS_PER_BAR
            for st in (0, 3, 6, 8, 11, 14):
                p.add(BASS, base + st, roots[ci] + (12 if st in (6, 14) else 0), 2 if st in (0, 8) else 1)
            # Root and fifth only: a held pad voice is the dearest thing in
            # the song to render, and the keys already spell the chord.
            for n in (chords[ci][0], chords[ci][2]):
                p.add(PAD, base, n, 16, vel=80)
            for k, st in enumerate((0, 3, 6, 10, 12)):
                p.add(KEYS, base + st, chords[ci][k % 3] + 12, 2, vel=85)

    lead = s.patterns[3]
    lead.set_bars(2)
    for st, n, ln in ((0, 76, 3), (3, 72, 3), (6, 74, 2), (8, 76, 4), (14, 79, 2),
                      (16, 81, 3), (19, 79, 3), (22, 76, 2), (24, 72, 6)):
        lead.add(LEAD, st, n, ln, vel=95)

    for bar in range(8):
        s.toggle_clip(0, bar)
    for bar, p in ((0, 1), (2, 2), (4, 1), (6, 2)):
        s.toggle_clip(p, bar)
    s.toggle_clip(3, 4)
    s.toggle_clip(3, 6)
    return s
