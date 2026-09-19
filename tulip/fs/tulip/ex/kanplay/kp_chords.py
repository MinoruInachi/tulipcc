"""Chord construction for kanplay: degree + key + modifier + voicing -> MIDI notes.

This is an independent, from-scratch implementation. It follows the *shape* of
the KANTAN Music API's public header (the enum names for modifiers and
voicings, the 1..6 "pitch" slots, key numbering, semitone shift, on-chord bass,
position, minor swap) so that KANTAN Play song files map onto it directly, but
none of the voicing tables here come from that library.

Pure Python, no tulip/amy imports, so it runs and is tested under CPython.

    chord = Chord(degree=5, key=0, modifier="7")
    chord.name()            -> "G7"
    chord.notes("Close")    -> [55, 59, 62, 65, 67, 71]   (pitch 1..6, low to high)

A 0 in the notes list means "muted", as in the KANTAN song format.
"""

KEY_NAMES = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
MINOR_KEY_NAMES = ["Am", "Bbm", "Bm", "Cm", "C#m", "Dm", "D#m", "Em", "Fm",
                   "F#m", "Gm", "G#m"]
MAJOR_SCALE = [0, 2, 4, 5, 7, 9, 11]
MAJOR_PENTATONIC = [0, 2, 4, 7, 9]
# Diatonic triad quality of each degree of the major scale.
DIATONIC_QUALITY = ["maj", "min", "min", "maj", "maj", "min", "dim"]

# Modifier names as they appear in song JSON ("mod" field / part enums).
MODIFIERS = ["", "dim", "m7-5", "sus4", "6", "7", "add9", "M7", "aug",
             "7sus4", "dim7"]
VOICINGS = ["Close", "Guitar", "Static", "Ukulele",
            "M-Major", "M-Penta", "M-Chroma"]
MELODY_VOICINGS = ("M-Major", "M-Penta", "M-Chroma")

PITCHES = 6          # pitch slots 1..6
MUTE = 0

# Standard guitar tuning (E2 A2 D3 G3 B3 E4) and ukulele (G4 C4 E4 A4, re-entrant).
GUITAR_OPEN = [40, 45, 50, 55, 59, 64]
UKULELE_OPEN = [67, 60, 64, 69]


def _third(quality):
    return 3 if quality in ("min", "dim") else 4


def _fifth(quality):
    return 6 if quality == "dim" else 7


def chord_intervals(quality, modifier):
    """Semitone intervals above the root, ascending, for a chord quality
    ("maj"/"min"/"dim") with a KANTAN-style modifier applied."""
    third = _third(quality)
    fifth = _fifth(quality)
    if modifier == "":
        return [0, third, fifth]
    if modifier == "dim":
        return [0, 3, 6]
    if modifier == "m7-5":
        return [0, 3, 6, 10]
    if modifier == "sus4":
        return [0, 5, 7]
    if modifier == "6":
        return [0, third, fifth, 9]
    if modifier == "7":
        return [0, third, fifth, 10]
    if modifier == "add9":
        return [0, third, fifth, 14]
    if modifier == "M7":
        return [0, third, fifth, 11]
    if modifier == "aug":
        return [0, 4, 8]
    if modifier == "7sus4":
        return [0, 5, 7, 10]
    if modifier == "dim7":
        return [0, 3, 6, 9]
    raise ValueError("unknown modifier %r" % modifier)


def chord_suffix(quality, modifier):
    """The chord-symbol suffix, e.g. ("min", "7") -> "m7"."""
    if modifier in ("dim", "m7-5", "sus4", "aug", "7sus4", "dim7"):
        return modifier
    m = "m" if quality == "min" else ("dim" if quality == "dim" else "")
    if modifier == "" and quality == "dim":
        return "dim"
    return m + modifier


def degree_root_pc(degree, key, semitone_shift=0):
    """Pitch class (0..11) of a scale degree in a key, shifted by semitones."""
    return (key + MAJOR_SCALE[(degree - 1) % 7] + semitone_shift) % 12


def parse_degree(text):
    """Parse a KANTAN degree string like "5", "7b", "3#~", "3b~".

    Returns (degree, semitone_shift, minor_swap)."""
    text = text.strip()
    degree = int(text[0])
    shift = 0
    swap = False
    for ch in text[1:]:
        if ch == "b":
            shift -= 1
        elif ch == "#":
            shift += 1
        elif ch == "~":
            swap = True
    return degree, shift, swap


class Chord:
    """One chord, as the player would specify it: a degree in a key plus the
    options the KANTAN Music API takes. Immutable in practice."""

    def __init__(self, degree, key=0, modifier="", semitone_shift=0,
                 minor_swap=False, bass_degree=0, bass_semitone_shift=0,
                 position=0):
        if not 1 <= degree <= 7:
            raise ValueError("degree must be 1..7")
        if modifier not in MODIFIERS:
            raise ValueError("unknown modifier %r" % modifier)
        self.degree = degree
        self.key = key % 12
        self.modifier = modifier
        self.semitone_shift = semitone_shift
        self.minor_swap = minor_swap
        self.bass_degree = bass_degree
        self.bass_semitone_shift = bass_semitone_shift
        self.position = position

    # --- identity -------------------------------------------------------

    @property
    def quality(self):
        q = DIATONIC_QUALITY[self.degree - 1]
        if self.minor_swap:
            q = {"maj": "min", "min": "maj", "dim": "min"}[q]
        return q

    @property
    def root_pc(self):
        return degree_root_pc(self.degree, self.key, self.semitone_shift)

    @property
    def bass_pc(self):
        if not self.bass_degree:
            return None
        return degree_root_pc(self.bass_degree, self.key, self.bass_semitone_shift)

    def intervals(self):
        return chord_intervals(self.quality, self.modifier)

    def pitch_classes(self):
        r = self.root_pc
        return sorted(set((r + i) % 12 for i in self.intervals()))

    def name(self):
        n = KEY_NAMES[self.root_pc] + chord_suffix(self.quality, self.modifier)
        if self.bass_pc is not None and self.bass_pc != self.root_pc:
            n += "/" + KEY_NAMES[self.bass_pc]
        return n

    def __repr__(self):
        return "Chord(%s)" % self.name()

    # --- voicings ---------------------------------------------------------

    _notes_cache = {}

    def notes(self, voicing="Close"):
        """MIDI notes for pitch slots 1..6, low to high, 0 = muted."""
        # A voicing costs ~1 ms on the Tab5 and every press asks for one per
        # part, so remember them; a song uses a few dozen distinct chords.
        key = (self.degree, self.key, self.modifier, self.semitone_shift, self.minor_swap,
               self.bass_degree, self.bass_semitone_shift, self.position, voicing)
        cached = Chord._notes_cache.get(key)
        if cached is not None:
            return list(cached)
        notes = self._notes(voicing)
        if len(Chord._notes_cache) > 512:
            Chord._notes_cache.clear()
        Chord._notes_cache[key] = tuple(notes)
        return notes

    def _notes(self, voicing):
        if voicing in MELODY_VOICINGS:
            notes = self._melody(voicing)
        elif voicing == "Guitar":
            notes = self._guitar()
        elif voicing == "Static":
            notes = self._static()
        elif voicing == "Ukulele":
            notes = self._ukulele()
        else:
            notes = self._close()
        notes = self._apply_bass(notes, voicing)
        return [n if 0 < n < 128 else MUTE for n in notes]

    def _window_base(self, low, pc):
        """Lowest note >= low with pitch class pc, honouring position: a
        position of +n places the whole chord as if the key were n semitones
        lower and then transposes the result up by n."""
        pos = self.position
        return low + ((pc - low - pos) % 12) + pos

    def _close(self):
        """Root position, stacked from the root, wrapping up an octave.
        Root sits in C3..B3 (48..59) at position 0."""
        root = self._window_base(48, self.root_pc)
        iv = self.intervals()
        out = []
        octave = 0
        while len(out) < PITCHES:
            for i in iv:
                out.append(root + i + octave)
                if len(out) == PITCHES:
                    break
            octave += 12
        return out

    def _static(self):
        """Bass-line voicing: pitch 3 is always the root in C3..B3 (that is
        arpeggio row 3, the row KANTAN's bass presets use), pitches 2..1 walk
        down through chord tones and 4..6 walk up, so a bass part reading one
        slot gets the root of every chord in one register."""
        root = self._window_base(48, self.root_pc)
        pcs = self.pitch_classes()
        up = []
        n = root
        while len(up) < 3:
            n += 1
            if n % 12 in pcs:
                up.append(n)
        down = []
        n = root
        while len(down) < 2:
            n -= 1
            if n % 12 in pcs:
                down.append(n)
        down.reverse()
        return down + [root] + up

    def _fretted(self, opens, max_fret):
        """For each open string, the lowest fret (0..max_fret) landing on a
        chord tone."""
        pcs = self.pitch_classes()
        out = []
        for o in opens:
            note = MUTE
            for f in range(0, max_fret + 1):
                if (o + f) % 12 in pcs:
                    note = o + f
                    break
            out.append(note)
        return out

    def _guitar(self):
        """Six strings, a movable barre shape: root on the low E string, then
        the nearest chord tones up the neck. Position shifts the shape by
        octaves when it would otherwise leave the neck."""
        root = self._window_base(40, self.root_pc)     # E2..D#3
        fret = root - GUITAR_OPEN[0]
        opens = [o + fret for o in GUITAR_OPEN]
        pcs = self.pitch_classes()
        iv = self.intervals()
        # Shape from the E-form barre: R 5 (R|7th) 3 5 R. With a 4th chord tone
        # it takes the D string, where the octave root would have been.
        ext = iv[3] if len(iv) > 3 else 12
        shape = [0, _fifth(self.quality), ext, 12 + iv[1], 12 + _fifth(self.quality), 24]
        if self.modifier in ("sus4", "7sus4"):
            shape[3] = 12 + 5
        if self.modifier == "aug":
            shape[1] = 8
            shape[4] = 20
        notes = [root + s for s in shape]
        # add9: put the 9th on the top string instead of doubling the root.
        if self.modifier == "add9":
            notes[5] = root + 26
        # Keep every string on a chord tone (dim/aug fifths etc. are covered by
        # the shape; this is a safety net for anything the shape missed).
        for i, n in enumerate(notes):
            if n % 12 not in pcs:
                notes[i] = MUTE
        return notes

    def _ukulele(self):
        """Four re-entrant strings within the first frets; pitches 5 and 6 are
        muted."""
        notes = self._fretted(UKULELE_OPEN, 7)
        notes.sort()
        notes = [n + self.position for n in notes]
        return notes + [MUTE, MUTE]

    def _melody(self, voicing):
        """Scale notes for melody parts: pitch 1..6 are consecutive scale
        steps from the key's tonic at C4-ish, moved by position in semitones
        (via the same window rule), chord ignored."""
        if voicing == "M-Penta":
            scale = MAJOR_PENTATONIC
        elif voicing == "M-Chroma":
            scale = list(range(12))
        else:
            scale = MAJOR_SCALE
        tonic = self._window_base(60, self.key)
        out = []
        octave = 0
        while len(out) < PITCHES:
            for s in scale:
                out.append(tonic + s + octave)
                if len(out) == PITCHES:
                    break
            octave += 12
        return out

    def _apply_bass(self, notes, voicing):
        """On-chord: pitch 1 becomes the bass note, just below the root's
        register (melody voicings are left alone)."""
        bpc = self.bass_pc
        if bpc is None or voicing in MELODY_VOICINGS:
            return notes
        lowest = min([n for n in notes if n] or [48])
        bass = lowest - ((lowest - bpc) % 12)
        if bass == lowest:
            return notes
        if bass < 24:
            bass += 12
        return [bass] + notes[1:]


def chord_from_step(step, key=0, position=0):
    """Build a Chord from a KANTAN progression timeline entry
    ({"main": "5#", "mod": "7", "bass": "3"})."""
    degree, shift, swap = parse_degree(step.get("main", "1"))
    bass_degree = 0
    bass_shift = 0
    if step.get("bass"):
        bass_degree, bass_shift, _ = parse_degree(step["bass"])
    return Chord(degree, key=key, modifier=step.get("mod", "") or "",
                 semitone_shift=shift, minor_swap=swap,
                 bass_degree=bass_degree, bass_semitone_shift=bass_shift,
                 position=position)
