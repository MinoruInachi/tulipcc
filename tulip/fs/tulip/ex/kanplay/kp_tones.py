"""General MIDI program number -> AMY patch, for kanplay parts.

KANTAN Play song files name instruments by GM program (0..127, 128 = drums)
because the original hardware carries a GM chip. Tulip's AMY has Juno-106
(0..127), DX7 (128..255) and piano (256) presets instead, so each GM family
gets the nearest-sounding AMY patch here. Edit OVERRIDES to taste.
"""
import synth

DRUM_KIT = 384          # TR-808 kit; 385 909, 386 Linn, 387 MR-12, 389 Power

# GM family (program // 8) -> AMY patch.
FAMILY_PATCH = [
    256,    # 0-7   piano            -> dpwe piano
    148,    # 8-15  chromatic perc   -> DX7 VIBE 1
    8,      # 16-23 organ            -> Juno Organ I
    73,     # 24-31 guitar           -> Juno Nylon Guitar
    142,    # 32-39 bass             -> DX7 BASS 1
    64,     # 40-47 strings          -> Juno Strings
    131,    # 48-55 ensemble/choir   -> DX7 STRINGS 1
    128,    # 56-63 brass            -> DX7 BRASS 1
    86,     # 64-71 reed             -> Juno Sharp Reed
    151,    # 72-79 pipe             -> DX7 FLUTE 1
    32,     # 80-87 synth lead       -> Juno Lead I
    47,     # 88-95 synth pad        -> Juno Synth Pad
    101,    # 96-103 synth fx        -> Juno Ethereal
    28,     # 104-111 ethnic         -> Juno Koto
    149,    # 112-119 percussive     -> DX7 MARIMBA
    71,     # 120-127 sound fx       -> Juno Noise Sweep
]

# Specific programs that deserve better than their family default.
OVERRIDES = {
    # The interpolated piano (256) costs ~0.12 of the Tab5's render budget per
    # voice against ~0.08 for a Juno or DX7 voice, so GM pianos go to the DX7
    # pianos and 256 is kept for program 2 only.
    0: 135,     # Acoustic Grand     -> DX7 PIANO 1
    1: 136,     # Bright Acoustic    -> DX7 PIANO 2
    2: 256,     # Electric Grand     -> interpolated piano
    3: 137,     # Honky-tonk         -> DX7 PIANO 3
    4: 14,      # Electric Piano 1  -> Juno Elect. Piano I
    5: 15,      # Electric Piano 2  -> Juno Elect. Piano II
    6: 68,      # Harpsichord
    7: 41,      # Clavinet
    9: 213,     # Glockenspiel      -> DX7 GLOKENSPL
    11: 148,    # Vibraphone
    12: 149,    # Marimba
    13: 18,     # Xylophone
    16: 8,      # Drawbar Organ
    17: 9,      # Percussive Organ
    19: 145,    # Church Organ      -> DX7 PIPES 1
    21: 77,     # Accordion
    24: 73,     # Nylon Guitar
    25: 139,    # Steel Guitar      -> DX7 GUITAR 1 (Juno 27 costs more per voice)
    26: 139,    # Jazz Guitar       -> DX7 GUITAR 1
    27: 140,    # Clean Guitar
    30: 30,     # Distortion        -> Juno Funky I
    32: 87,     # Acoustic Bass     -> Juno Bass Pluck
    33: 142,    # Finger Bass
    34: 143,    # Pick Bass
    36: 31,     # Slap Bass         -> Juno Synth Bass I
    38: 36,     # Synth Bass 1      -> Juno Synth Bass II
    39: 238,    # Synth Bass 2      -> DX7 SYN-BASS 1
    40: 65,     # Violin
    42: 122,    # Cello
    46: 188,    # Harp
    48: 4,      # String Ensemble   -> Juno Moving Strings
    49: 21,     # Slow Strings      -> Juno String III
    52: 6,      # Choir Aahs
    53: 157,    # Voice Oohs        -> DX7 VOICE 1
    56: 2,      # Trumpet
    61: 26,     # Brass Section
    62: 232,    # Synth Brass 1     -> DX7 SYNBRASS 1
    65: 196,    # Alto Sax
    71: 105,    # Clarinet
    73: 3,      # Flute
    80: 32,     # Square Lead
    81: 33,     # Saw Lead
    88: 47,     # New Age Pad
    89: 4,      # Warm Pad
    108: 28,    # Kalimba          -> Koto-ish
}


# Short General MIDI names (<= 12 chars) for the part panel.
GM_NAMES = [
    "Piano", "Bright Piano", "E.Grand", "Honky-tonk", "E.Piano 1", "E.Piano 2", "Harpsichord", "Clavinet",
    "Celesta", "Glockenspiel", "Music Box", "Vibraphone", "Marimba", "Xylophone", "Tubular Bell", "Dulcimer",
    "Drawbar Org", "Perc Organ", "Rock Organ", "Church Organ", "Reed Organ", "Accordion", "Harmonica", "Tango Acc",
    "Nylon Guitar", "Steel Guitar", "Jazz Guitar", "Clean Guitar", "Muted Guitar", "Overdrive Gt", "Distortion", "Gt Harmonics",
    "Ac. Bass", "Finger Bass", "Pick Bass", "Fretless", "Slap Bass 1", "Slap Bass 2", "Synth Bass 1", "Synth Bass 2",
    "Violin", "Viola", "Cello", "Contrabass", "Tremolo Str", "Pizzicato", "Harp", "Timpani",
    "Strings", "Slow Strings", "Syn Strings1", "Syn Strings2", "Choir Aahs", "Voice Oohs", "Synth Voice", "Orch Hit",
    "Trumpet", "Trombone", "Tuba", "Muted Trumpt", "French Horn", "Brass", "Syn Brass 1", "Syn Brass 2",
    "Soprano Sax", "Alto Sax", "Tenor Sax", "Bari Sax", "Oboe", "English Horn", "Bassoon", "Clarinet",
    "Piccolo", "Flute", "Recorder", "Pan Flute", "Bottle", "Shakuhachi", "Whistle", "Ocarina",
    "Square Lead", "Saw Lead", "Calliope", "Chiff Lead", "Charang", "Voice Lead", "Fifths Lead", "Bass+Lead",
    "New Age Pad", "Warm Pad", "Polysynth", "Choir Pad", "Bowed Pad", "Metallic Pad", "Halo Pad", "Sweep Pad",
    "Rain", "Soundtrack", "Crystal", "Atmosphere", "Brightness", "Goblins", "Echoes", "Sci-Fi",
    "Sitar", "Banjo", "Shamisen", "Koto", "Kalimba", "Bagpipe", "Fiddle", "Shanai",
    "Tinkle Bell", "Agogo", "Steel Drums", "Woodblock", "Taiko", "Melodic Tom", "Synth Drum", "Rev Cymbal",
    "Fret Noise", "Breath", "Seashore", "Bird Tweet", "Telephone", "Helicopter", "Applause", "Gunshot",
]

# The programs the part panel steps through: every family's first program,
# every program with a dedicated AMY mapping, and the drum kit.
TONE_CHOICES = sorted(set(range(0, 128, 8)) | set(OVERRIDES.keys()) | {128})


def name_for(program):
    program = int(program)
    if program == 128:
        return "Drums"
    return GM_NAMES[program % 128]


def next_tone(program, step):
    """The next program in TONE_CHOICES after `program`, in either direction."""
    program = int(program)
    if program in TONE_CHOICES:
        i = TONE_CHOICES.index(program)
    else:
        # Between two choices: up lands on the next larger, down on the next
        # smaller.
        i = max([k for k, t in enumerate(TONE_CHOICES) if t < program] or [-1])
        if step < 0:
            i += 1
    return TONE_CHOICES[(i + step) % len(TONE_CHOICES)]


# Render cost per voice as a fraction of one audio block on the Tab5,
# measured 2026-09-19 (tulip.amy_render_load(), idle 0.06 subtracted).
COST_PIANO256 = 0.12
COST_VOICE = 0.08
COST_DRUMS = 0.10
# Budget in model units. Struck notes decay, so the real load runs below the
# sustained-note model: a slot modelled at 0.66 measured 0.35, one at 1.26
# measured 1.2 (noise); 1.0 measured 0.85-0.88 on six parts, and rapid
# re-triggering (six taps a second on Pop_Basic1) pushed a 0.9 slot to 1.0.
# 0.75 keeps those peaks near 0.85, under the 0.98 failsafe.
RENDER_BUDGET = 0.75


def voice_cost(program):
    """Render cost of one voice of this program."""
    program = int(program)
    if program == 128:
        return COST_DRUMS
    return COST_PIANO256 if patch_for(program) == 256 else COST_VOICE


def fit_voices(wanted, programs, budget=RENDER_BUDGET):
    """Trim per-part voice counts until their render cost fits the budget.
    The part with the most voices loses one first (a later part on ties, so
    a lead chord part keeps more than an accompaniment part with the same
    count); every part keeps at least one."""
    voices = list(wanted)
    def cost():
        return sum(v * voice_cost(pr) for v, pr in zip(voices, programs) if v)
    while cost() > budget:
        i = max(range(len(voices)), key=lambda k: (voices[k], k) if voices[k] > 1 else (-1, k))
        if voices[i] <= 1:
            break
        voices[i] -= 1
    return voices


def patch_for(program):
    """AMY patch number for a GM program (0..127)."""
    program = int(program) % 128
    if program in OVERRIDES:
        return OVERRIDES[program]
    return FAMILY_PATCH[program // 8]


# AMY synth numbers for the six parts. PatchSynth's own allocator hands out
# 16, 17, 18, ... and never reuses a released number, and AMY has 64 synths
# (amy_config.max_synths), so an app that rebuilds its synths on every slot
# change goes silent after 48 of them. Fixed numbers at the top of the range
# avoid that and stay clear of the MIDI channels (1..16) and drums.py (20+).
SYNTH_BASE = 58


def make_synth(program, num_voices, synth_number):
    """A synth.PatchSynth (or DrumSynth for program 128) on a fixed AMY
    synth number; making a new one on the same number reconfigures it."""
    if int(program) == 128:
        s = synth.DrumSynth(num_voices=1, channel=synth_number, patch=DRUM_KIT)
    else:
        s = synth.PatchSynth(num_voices=max(1, min(6, num_voices)),
                             channel=synth_number, patch=patch_for(program))
    # PatchSynth builds the AMY synth lazily on its first note_on; the engine
    # sends notes to the synth number directly (kp_engine.PartPlayer._note),
    # so build it now or the first notes would go to a synth that is not there.
    init = getattr(s, "deferred_init", None)
    if init:
        init()
    return s
