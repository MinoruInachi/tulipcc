"""Late-70s technopop instrumental for tulipcc -- original material.

Four parts in the idiom of the era: an 808 kit, an octave-jumping bass, a
16th-note sequencer arpeggio, and a syn-brass lead.

Timing comes from AMY's sequencer, not from Python. A bar-level scheduler runs
about once per bar and queues the *next* bar's notes at absolute sequencer
ticks, so every note lands sample-accurately even though the scheduler itself
is only as punctual as tulip.defer(). Everything is derived from one absolute
tick grid, so the piece cannot drift.

    import technopop
    technopop.play()
    technopop.stop()

To use your own melody, replace LEAD -- it is just a list of bars, each a list
of (step_in_16ths, midi_note, velocity). The other parts have the same shape.

Every voice's envelope decays to zero while the note is still held, rather than
relying on a note-off to start the release. A sequencer that only schedules
note-ons -- as this one does for the drums and the sequenced parts -- would
otherwise leave the last note of each synth sounding forever. The lead also gets
an explicit note-off so its voices are released cleanly.
"""

import amy
import synth
import tulip

BPM = 132
# The piece has a fixed length and ends on its own -- 32 bars is four times
# through the 8-bar melody, about 58 seconds. Set it to None to loop forever
# (then technopop.stop() is the only way out).
BARS = 32
# How far ahead of the sequencer to keep bars queued. The scheduler re-checks
# several times a bar and queues whatever now falls inside this window, so it
# is driven by AMY's actual tick position rather than by when tulip.defer()
# happened to run -- defer jitter cannot accumulate into a shrinking lead.
LOOKAHEAD_BARS = 1
TICKS_PER_BEAT = amy.AMY_SEQUENCER_PPQ      # 48
TICKS_PER_16TH = TICKS_PER_BEAT // 4        # 12
TICKS_PER_BAR = TICKS_PER_BEAT * 4          # 192
BAR_MS = 4 * 60000.0 / BPM

# 808 kit, addressed by General MIDI note (see patches.drumkit).
KICK, RIM, SNARE, CLAP = 36, 37, 38, 39
CH_HAT, OP_HAT, COWBELL = 42, 46, 56
CYMBAL_NOTE = 49

# --- patterns -------------------------------------------------------------
# Each entry is (step_in_16ths, midi_note, velocity). A bar is 16 steps.

DRUMS = [
    # bar 1 of 2
    [(0, KICK, 1.0), (0, CH_HAT, 0.5), (2, CH_HAT, 0.3), (4, SNARE, 0.9),
     (4, CH_HAT, 0.4), (6, CH_HAT, 0.3), (8, KICK, 0.9), (8, CH_HAT, 0.5),
     (10, CH_HAT, 0.3), (11, KICK, 0.6), (12, SNARE, 0.9), (12, CH_HAT, 0.4),
     (14, OP_HAT, 0.4)],
    # bar 2 of 2 -- same, with a rimshot pickup into the next bar
    [(0, KICK, 1.0), (0, CH_HAT, 0.5), (2, CH_HAT, 0.3), (4, SNARE, 0.9),
     (4, CH_HAT, 0.4), (6, CH_HAT, 0.3), (8, KICK, 0.9), (8, CH_HAT, 0.5),
     (10, CH_HAT, 0.3), (11, KICK, 0.6), (12, SNARE, 0.9), (12, CH_HAT, 0.4),
     (14, RIM, 0.5), (15, RIM, 0.6)],
]

# One bar per chord: Am, F, C, G.
_BASS_ROOTS = [45, 41, 48, 43]        # A2, F2, C3, G2
BASS = [[(0, r, 0.9), (3, r + 12, 0.6), (6, r, 0.8), (8, r, 0.9),
         (11, r + 12, 0.6), (14, r, 0.7)] for r in _BASS_ROOTS]

# The sequencer figure: a three-note chord cell cycling against a 16-step bar,
# so the accent walks through the pattern instead of locking to the beat.
_ARP_CHORDS = [
    (69, 72, 76),   # Am
    (65, 69, 72),   # F
    (64, 67, 72),   # C
    (67, 71, 74),   # G
]
ARP = [[(s, ch[s % 3] + (12 if (s % 6) >= 3 else 0), 0.55 if s % 4 else 0.75)
        for s in range(16)] for ch in _ARP_CHORDS]

# Original 8-bar melody, A minor pentatonic. Replace this to play your own.
LEAD_GATE_16THS = 6         # how long a lead note is held before its note-off

LEAD = [
    [(0, 76, 0.9), (2, 79, 0.8), (4, 81, 0.95), (8, 79, 0.8), (10, 76, 0.8),
     (12, 74, 0.85)],
    [(0, 72, 0.85), (2, 74, 0.8), (4, 76, 0.9), (8, 74, 0.8), (12, 69, 0.85)],
    [(0, 72, 0.85), (2, 76, 0.85), (4, 79, 0.95), (8, 81, 0.9), (12, 79, 0.8)],
    [(0, 74, 0.9), (4, 71, 0.8), (8, 67, 0.85), (12, 74, 0.8)],
    [(0, 81, 0.95), (2, 79, 0.8), (4, 76, 0.9), (8, 74, 0.85), (12, 76, 0.8)],
    [(0, 77, 0.9), (4, 76, 0.85), (8, 72, 0.9), (12, 74, 0.8)],
    [(0, 76, 0.9), (2, 79, 0.85), (4, 84, 0.95), (8, 81, 0.9), (12, 79, 0.85)],
    [(0, 78, 0.9), (4, 79, 0.9), (8, 74, 0.85)],
]


# --- voices ---------------------------------------------------------------

def _msg(arg_dicts):
    return ''.join(amy.message(**d) for d in arg_dicts)


def _bass_patch():
    """Short filtered saw, the envelope closing the filter fast."""
    return _msg([
        {'osc': 0, 'wave': amy.SAW_DOWN, 'filter_type': amy.FILTER_LPF24,
         'resonance': 3.0, 'filter_freq': '180,0.4,0,0,4', 'pan': 0.5},
        # Decays to 0 on its own -- see the note about note-offs above.
        {'osc': 0, 'bp0': '0,1,320,0,40,0'},
    ])


def _arp_patch():
    """Bright narrow pulse with a plucked decay -- the sequencer voice."""
    return _msg([
        {'osc': 0, 'wave': amy.PULSE, 'duty': 0.22,
         'filter_type': amy.FILTER_LPF24, 'resonance': 4.0,
         'filter_freq': '600,0.6,0,0,3', 'pan': 0.38},
        {'osc': 0, 'bp0': '0,1,140,0,20,0'},
    ])


def _lead_patch():
    """Two detuned saws through a slow-opening filter: syn-brass."""
    return _msg([
        {'osc': 1, 'wave': amy.SAW_DOWN, 'freq': '440,1.005', 'amp': 0.5,
         'filter_type': amy.FILTER_LPF24, 'resonance': 1.2,
         'filter_freq': '320,0.55,0,0,2.5', 'pan': 0.62},
        {'osc': 1, 'bp0': '25,1,900,0,120,0'},
        {'osc': 0, 'wave': amy.SAW_DOWN, 'amp': 0.5, 'chained_osc': 1,
         'filter_type': amy.FILTER_LPF24, 'resonance': 1.2,
         'filter_freq': '320,0.55,0,0,2.5', 'pan': 0.38},
        {'osc': 0, 'bp0': '25,1,900,0,120,0'},
    ])


_parts = []          # (synth, pattern_list) in play order
_bar = 0             # next bar index to queue
_bar0_tick = 0       # absolute tick of bar 0
# Bumped by every play() and stop(). A deferred _tick carries the generation it
# was armed under and retires itself if that no longer matches -- without this,
# a _tick still sitting in tulip's defer queue from an earlier play() comes back
# to life the moment play() starts again, and you end up with two schedulers
# queueing the same bars.
_gen = 0


_dropped = 0        # notes that missed their tick; should stay 0


def _queue_bar(bar):
    """Queue every note of one bar at its absolute sequencer tick."""
    global _dropped
    base = _bar0_tick + bar * TICKS_PER_BAR
    now = amy.sequencer_ticks()
    for sy, pattern in _parts:
        row = pattern[bar % len(pattern)]
        for step, note, vel in row:
            tick = base + step * TICKS_PER_16TH
            # A tick that has already gone by would never fire -- AMY only
            # matches on equality -- so drop it rather than leave a stuck tag.
            if tick <= now:
                _dropped += 1     # lead time ran out -- the note would never fire
                continue
            sy.note_on(note, vel, ticks=tick)
            if pattern is LEAD:
                sy.note_off(note, ticks=tick + LEAD_GATE_16THS * TICKS_PER_16TH)


def _tick(gen):
    global _bar
    if gen != _gen:
        return          # superseded by a later play()/stop()
    horizon = amy.sequencer_ticks() + LOOKAHEAD_BARS * TICKS_PER_BAR
    while _bar0_tick + _bar * TICKS_PER_BAR <= horizon:
        if BARS is not None and _bar >= BARS:
            _finish(gen)
            return
        _queue_bar(_bar)
        _bar += 1
    # Re-check well inside the lookahead window, so one late callback still
    # leaves plenty of margin.
    tulip.defer(_tick, gen, int(BAR_MS / 3))


def _finish(gen):
    """Land on the tonic, let it ring, then shut everything down."""
    base = _bar0_tick + _bar * TICKS_PER_BAR
    for sy, pattern in _parts:
        if pattern is LEAD:
            for note in (57, 69, 72, 76):       # Am, spread over two octaves
                sy.note_on(note, 0.8, ticks=base)
        elif pattern is DRUMS:
            sy.note_on(KICK, 1.0, ticks=base)
            sy.note_on(CYMBAL_NOTE, 0.7, ticks=base)
    # The chord lands a lookahead window from now; give it two more bars to
    # decay before pulling everything down.
    tulip.defer(lambda _a: stop(), None, int(BAR_MS * (LOOKAHEAD_BARS + 2)))


def play():
    """Start the piece. Safe to call again; it restarts from the top."""
    global _parts, _bar, _bar0_tick, _gen, _dropped
    stop()
    _dropped = 0

    amy.send(reset=amy.RESET_TIMEBASE)
    synth.PatchSynth.reset()          # includes amy.reset()
    amy.send(tempo=BPM)
    amy.reverb(0.35)

    drums = synth.DrumSynth(num_voices=1, patch=384)      # TR-808
    bass = synth.PatchSynth(num_voices=2, patch_string=_bass_patch())
    arp = synth.PatchSynth(num_voices=4, patch_string=_arp_patch())
    lead = synth.PatchSynth(num_voices=4, patch_string=_lead_patch())
    _parts = [(drums, DRUMS), (bass, BASS), (arp, ARP), (lead, LEAD)]

    # Line bar 0 up on the next bar boundary, then keep every later bar on that
    # same grid: the scheduler's jitter never accumulates into the music.
    now = amy.sequencer_ticks()
    _bar0_tick = (now - (now % TICKS_PER_BAR)
                  + (LOOKAHEAD_BARS + 1) * TICKS_PER_BAR)
    _bar = 0
    _gen += 1
    # Queue the first bar immediately and stay one bar ahead from then on.
    _tick(_gen)
    if BARS is not None:
        print("technopop: %d bars, ~%d seconds. technopop.stop() to cut it short."
              % (BARS, int(BARS * BAR_MS / 1000)))


def stop():
    """Silence everything and retire any scheduler still in flight."""
    global _gen, _parts
    _gen += 1
    parts, _parts = _parts, []
    # Drop our own pending callbacks outright rather than trusting them to
    # notice the generation change, so a wedged scheduler cannot keep the queue
    # growing. Anything already handed to AMY is cleared by the resets below.
    try:
        queue = tulip._defer_queue
        for i in range(len(queue) - 1, -1, -1):
            if queue[i][0] is _tick:
                queue.pop(i)
    except (AttributeError, IndexError):
        pass
    amy.send(reset=amy.RESET_SEQUENCER)   # drop notes queued but not yet fired
    for sy, _pattern in parts:
        try:
            sy.all_notes_off()
        except Exception:
            pass
    amy.send(reset=amy.RESET_ALL_NOTES)


play()
