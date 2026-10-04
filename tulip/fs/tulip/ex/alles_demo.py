"""Two Rooms -- a performance for two Alles speakers, driven from Tulip.

Put the two Alles nodes apart: opposite ends of a room, or two rooms with the
door open. The piece is built around that distance rather than in spite of it.
A melody sounds on the far speaker and comes back off the near one a dotted
eighth later, chords breathe on one side while the answer arrives from the
other, and the last chord is split with its roots in one room and its top in
the other.

    import alles_demo
    alles_demo.play()
    alles_demo.stop()

Importing it starts the piece, the way the other examples here do.

Why it is written this way: Tulip plays every note itself and stamps each
message with the host time it should sound at, which is how the two speakers
stay together -- a node keeps an offset between that clock and its own and
schedules against ours, so a packet arriving a few milliseconds late still
plays on the beat. Notes are addressed by client number, the one each node
reports in `alles.map()`; one datagram then reaches both speakers instead of
one each.

Everything the nodes are told to play happens `alles.ALLES_LATENCY_MS` after
the stamp, which is the budget the network has to deliver inside, and it is
also why setting up takes a moment: a reset is scheduled, a patch load is not,
so defining the voices too soon lets the reset land on top of them. See
SETUP_MS.

With one node found, it plays the whole piece on that node. With none, it
plays on Tulip's own speaker so you can still hear it.
"""

import time

import amy
import alles
import tulip

BPM = 84
STEPS_PER_BAR = 16                      # sixteenths
STEP_MS = 60000.0 / BPM / 4
BAR_MS = STEP_MS * STEPS_PER_BAR

# Synth numbers are fixed, not allocated: every instrument is defined on
# every node, so a role can move from one speaker to the other, the one-node
# case needs no special setup, and AMY's synth table cannot fill up the way
# auto-allocation would after enough runs. See PRESETS below.
# Chord progression, one per bar: Am F C G Am F Dm E7. Voiced in the middle of
# the keyboard so the bass has room underneath and the bell room above.
CHORDS = (
    (57, 60, 64),   # Am
    (53, 57, 60),   # F
    (55, 60, 64),   # C
    (55, 59, 62),   # G
    (57, 60, 64),   # Am
    (53, 57, 60),   # F
    (53, 57, 62),   # Dm
    (52, 56, 59),   # E7
)
ROOTS = (45, 41, 48, 43, 45, 41, 50, 40)

# The tune, as (step_in_sixteenths, midi_note) per bar of the progression.
MELODY = (
    ((0, 69), (6, 72), (10, 71)),
    ((0, 72), (4, 69), (8, 65)),
    ((0, 67), (6, 72), (10, 76)),
    ((0, 74), (8, 71)),
    ((0, 69), (6, 76), (12, 74)),
    ((0, 72), (8, 69)),
    ((0, 74), (6, 77), (12, 76)),
    ((0, 71), (8, 68)),
)

# A dotted eighth and a dotted quarter after the note, on the other speaker.
ECHOES = ((3, 0.45), (6, 0.18))

# Bars 0-1 intro, 2-9 the tune on the far speaker, 10-17 traded bar by bar,
# 18 the final chord.
INTRO_BARS = 2
THEME_BARS = 8
TRADE_BARS = 8
LAST_BAR = INTRO_BARS + THEME_BARS + TRADE_BARS


# --- voices ---------------------------------------------------------------

# Built-in presets rather than patches of our own. The first version of this
# file built its three voices out of raw saws and resonant 24 dB filters, and
# they were both harsh and far too quiet: rendering the whole piece gave
# -35.7 dBFS rms against -21.1 for these presets, **14.6 dB** down. A signal
# that small has to be made up downstream, which is how it ended up sounding
# thin and broken at the speaker. These are the voices duo_demo.py uses, and
# they are properly levelled.
#
# (synth number, preset, voices for two rooms, for one room, for Tulip itself)
#
# Tulip gets its own, smaller budget. Measured on a Tab5 playing the whole
# piece locally, peak render load and overruns against the 5.8 ms block:
#
#   13 voices (the one-room budget)   load 0.88   12 overruns   6560 us
#    9 voices (the two-room budget)   load 0.65    0 overruns   5124 us
#    6 voices                         load 0.48    0 overruns   3765 us
#
# At 13 the scheduler was dragged so far behind that the piece ran at less
# than half speed and the board eventually reset. A node has nothing to do but
# render; Tulip is also driving a display and a radio.
#
# Rendering competes with the scheduler even below the overrun point, so the
# local budget is smaller again. Speed of the piece on Tulip, against local
# render load:
#
#   2 pad / 1 bass / 3 bell   load 0.47   81% speed
#   1 pad / 1 bass / 2 bell   load 0.06   89% speed
#   1 pad / 1 bass / 1 bell   load 0.06   93% speed
#
# The Juno pad is what costs: dropping it to one voice takes the load from
# 0.47 to 0.06. What is left of the lag is Python's own per-event cost, not
# rendering. Tulip takes the middle one -- it still plays the tune and its
# echoes, which is what you listen for, and it leaves the board responsive.
# Whatever is on Tulip is an audition, not the performance: chords go
# monophonic and busy bars lose notes. The speakers are the piece.
PRESETS = (
    (1, 47, 3, 5, 1),   # Juno A68 Synth Pad -- the chords
    (2, 36, 2, 2, 1),   # Juno A55 Synth Bass II
    (3, 52, 4, 6, 2),   # Juno A75 Pluck Bell -- the tune and its echoes
)
PAD, BASS, BELL = PRESETS[0][0], PRESETS[1][0], PRESETS[2][0]

# Loading a patch is real work on a small node, so they go out one at a time
# rather than in a burst; the reference does the same with a 0.1 s sleep.
VOICE_GAP_MS = 150

# How loud to ask the nodes to be. Measured by rendering the piece per node:
# at volume 2 the busier speaker peaks at 0.715 and neither clips, and that is
# 6 dB up on the default of 1; volume 3 clips. One speaker carrying both parts
# gets everything at once, and 2 already clips there, so it stays at 1. Not a
# lasting change to a node either way -- a reset puts it back to 1, and play()
# starts with one.
DUO_VOLUME = 2
SOLO_VOLUME = 1

# The far speaker carries the tune and one note of each chord while the near
# one carries the bass, two chord notes and the echoes, which measured 6.3 dB
# apart. Lifting the chord note that crosses the room closes part of that gap
# without moving any material -- it is the one voice on the far side that
# holds through the bar. Swept against the rendered piece: 1.4 brings the gap
# to 5.0 dB with both speakers still peaking under 0.9, where 1.6 would reach
# 0.917 and start eating the headroom volume 2 needs.
HIGH_PAD_LIFT = 1.4


# --- the score ------------------------------------------------------------

def _score(near, far):
    """Build {step: [(dest, kwargs), ...]} for the whole piece.

    `near` and `far` are node addresses, or None to mean Tulip's own speaker.
    Built in full before a note sounds, so the ticker only has to look up a
    step, and so the shape of the piece can be checked without any hardware.
    """
    events = {}

    def at(step, dest, **kw):
        events.setdefault(step, []).append((dest, kw))

    def chord(bar, low, high, notes, vel):
        """A chord with its roots in one room and its top in the other.

        Splitting it is the point: one speaker cannot put the bottom of a
        chord across the room from its top.
        """
        base = bar * STEPS_PER_BAR
        for i, n in enumerate(notes):
            top = i == len(notes) - 1
            dest = high if top else low
            at(base, dest, synth=PAD, note=n,
               vel=min(1.0, vel * HIGH_PAD_LIFT) if top else vel)
            # Released a sixteenth before the bar ends, so the next chord
            # starts into a decaying one rather than a hard cut.
            #
            # Sent twice. The pad is the one voice held by its note-off rather
            # than by its own decay -- the bass and the bell presets both fall
            # to silence on their own -- so this is the only datagram in the
            # piece whose loss leaves a note sounding in another room with
            # nothing to stop it. The earlier hand-built pad had a fourth
            # envelope segment that took a held note to silence; a preset has
            # no such escape, so the note-off gets the redundancy instead.
            at(base + STEPS_PER_BAR - 1, dest, synth=PAD, note=n, vel=0,
               retries=2)


    def bell(step, dest, other, note, vel):
        """A struck note and its echoes, bouncing between the two rooms."""
        at(step, dest, synth=BELL, note=note, vel=vel)
        here = dest
        for delay, falloff in ECHOES:
            # Ping-pong: each repeat comes off the opposite wall from the one
            # before, so the sound walks away from you.
            here = other if here is dest else dest
            at(step + delay, here, synth=BELL, note=note, vel=vel * falloff)

    # Intro: the chord opens across both rooms, then one call crosses it.
    for bar in range(INTRO_BARS):
        chord(bar, near, far, CHORDS[bar % len(CHORDS)], 0.5)
    bell(INTRO_BARS * STEPS_PER_BAR - 8, far, near, 69, 0.75)

    # Theme: chord split both ways, bass near, tune far, echoes ping-ponging.
    for i in range(THEME_BARS):
        bar = INTRO_BARS + i
        chord(bar, near, far, CHORDS[i % len(CHORDS)], 0.55)
        base = bar * STEPS_PER_BAR
        at(base, near, synth=BASS, note=ROOTS[i % len(ROOTS)], vel=0.8)
        at(base + 8, near, synth=BASS, note=ROOTS[i % len(ROOTS)], vel=0.7)
        for step, note in MELODY[i % len(MELODY)]:
            bell(base + step, far, near, note, 0.8)

    # Trading: the tune changes room every bar and the chord turns over with
    # it, so the whole picture swings from one side to the other.
    for i in range(TRADE_BARS):
        bar = INTRO_BARS + THEME_BARS + i
        lead, answer = (near, far) if i % 2 else (far, near)
        chord(bar, answer, lead, CHORDS[i % len(CHORDS)], 0.5)
        base = bar * STEPS_PER_BAR
        at(base, near, synth=BASS, note=ROOTS[i % len(ROOTS)], vel=0.8)
        for step, note in MELODY[i % len(MELODY)]:
            bell(base + step, lead, answer, note, 0.8)

    # The last chord, spread between the rooms, with a high bell handed over.
    base = LAST_BAR * STEPS_PER_BAR
    at(base, near, synth=BASS, note=45, vel=0.8)
    for n in (57, 60):
        at(base, near, synth=PAD, note=n, vel=0.55)
    for n in (64, 69):
        at(base, far, synth=PAD, note=n, vel=0.5)
    at(base + 2, far, synth=BELL, note=81, vel=0.5)
    at(base + 5, near, synth=BELL, note=81, vel=0.22)

    return events


# --- transport ------------------------------------------------------------

# A destructive reset is *scheduled*, so it happens ALLES_LATENCY_MS after the
# host time it carries, while a patch load takes effect the moment it arrives.
# Define instruments too soon and the reset lands on top of them: the nodes
# lose their synths partway into the piece, which is what "it resets" looks
# like from the room. The reference alles.py waits the node's latency plus a
# little, and so do we.
#
# Measured against Tulip's own AMY this looked like 300 ms, and that is what
# this file shipped at first -- wrong by the whole latency, because a local
# AMY schedules nothing.
SETUP_MS = alles.ALLES_LATENCY_MS + 400

# How far ahead of its moment each event goes out. The node schedules against
# the host clock we stamp on it, so this only has to beat the network, and
# anything late by less than the node's latency still plays on time.
LOOKAHEAD_MS = 250

_gen = 0            # retires schedulers from an earlier play()/stop()
_events = {}
_step = 0
_last_step = 0
_t0 = 0
_nodes = ()
_near = None
_far = None


def _broadcast(**kw):
    """One message to every node, or to Tulip if there are none."""
    if _nodes:
        alles.send(**kw)
    else:
        amy.send(**kw)


def _emit(dest, kw, at_ms):
    """One event, to one node by client number, stamped with when to play it."""
    kw = dict(kw)
    retries = kw.pop("retries", 1)
    if dest is None:
        amy.send(**kw)                  # Tulip's own speaker, which has no 't'
    else:
        alles.send(retries=retries, client=dest, at_ms=at_ms, **kw)


def _define_voice(args):
    """Load one preset on every node -- or on Tulip, if there are none.

    Sent to the group rather than per node: the definitions are identical
    everywhere, and giving a node a role it never plays costs nothing but lets
    the piece hand that role over mid-phrase.

    One at a time, VOICE_GAP_MS apart: _near is _far whenever a single
    speaker carries the whole piece, and then it needs the larger voice count
    because both halves land on it at once.
    """
    (gen, index) = args
    if gen != _gen:
        return
    if index >= len(PRESETS):
        _begin(gen)
        return
    (synth_num, preset, voices, solo_voices, local_voices) = PRESETS[index]
    if _nodes:
        # The speakers get the full count; Tulip, if it is monitoring, gets
        # the small one. They have to be sent separately for that -- a plain
        # alles.send() with local playback on would echo the speakers' count
        # to Tulip, which is exactly the overload measured above.
        was_local = alles.local()
        alles.local(False)
        alles.send(synth=synth_num, patch=preset,
                   num_voices=solo_voices if _near is _far else voices)
        alles.local(was_local)
        if was_local:
            alles.local_send(synth=synth_num, patch=preset,
                             num_voices=local_voices)
    else:
        amy.send(synth=synth_num, patch=preset, num_voices=local_voices)
    tulip.defer(_define_voice, (gen, index + 1), VOICE_GAP_MS)


def _begin(gen):
    """Start the clock, once every voice is loaded."""
    global _events, _step, _last_step, _t0
    if gen != _gen:
        return          # stopped while we were waiting
    _events = _score(_near, _far)
    _last_step = max(_events)
    _step = 0
    # Step 0 is stamped for a moment far enough ahead that its datagram is
    # already out when it comes due.
    # In alles.host_ms(), because that is the clock the stamps are read in.
    _t0 = alles.host_ms() + LOOKAHEAD_MS * 2
    print("alles_demo: %d bars, ~%d seconds. alles_demo.stop() to cut it short."
          % (LAST_BAR + 1, int((_last_step + 1) * STEP_MS / 1000)))
    _tick(gen)


def _tick(gen):
    global _step
    if gen != _gen:
        return
    # Two clocks, deliberately: the stamp the nodes schedule against is in
    # alles.host_ms(), while our own callback has to be timed in the clock
    # tulip.defer() counts in. They tick at the same rate, so one offset
    # carries between them.
    due = _t0 + int(_step * STEP_MS)
    for dest, kw in _events.get(_step, ()):
        _emit(dest, kw, due)
    _step += 1
    if _step > _last_step:
        # Let the last chord ring -- and remember the nodes are still a whole
        # latency behind the clock we have been stamping.
        tulip.defer(lambda _a: stop(), None, 4000 + alles.ALLES_LATENCY_MS)
        return
    # Timed from the start rather than from the previous callback, so defer's
    # jitter cannot accumulate; and sent LOOKAHEAD_MS early, since the stamp
    # decides when it sounds, not the arrival.
    ahead = (_t0 + int(_step * STEP_MS) - LOOKAHEAD_MS) - alles.host_ms()
    tulip.defer(_tick, gen, max(1, ahead))


def play(local=False, volume=None):
    """Start the piece.

    local=True also plays it on Tulip's own speaker -- an audition, not a copy
    of the room. Tulip's AMY has no latency and no `t`, so it runs a whole
    ALLES_LATENCY_MS ahead of the speakers; it plays on the small voice budget
    in PRESETS, so chords go monophonic; and it still runs about a tenth slow,
    because rendering anything at all competes with the scheduler here. The
    speakers are where the piece is in tune and in time.

    volume turns the nodes up or down. Left out, it picks the measured value
    for however many speakers are playing (see DUO_VOLUME). It is not a
    lasting change to a node: a reset puts it back to 1, and this starts with
    one.

    With no node found, Tulip plays it alone whatever local says, so there is
    always something to hear.
    """
    global _gen, _nodes, _near, _far
    stop()

    _nodes = ()
    rows = ()
    if alles.on() or alles.mesh(local=False):
        rows = alles.map() or ()
        _nodes = tuple(ip for _client, ip, _clock in rows)

    if _nodes:
        alles.local(bool(local))
        # Addressed by the client number each node reports, which is what the
        # Alles firmware filters on -- one datagram then reaches both nodes
        # instead of one each.
        if len(rows) >= 2:
            _near, _far = rows[0][0], rows[1][0]
            print("alles_demo: near client %d (%s), far client %d (%s)"
                  % (_near, rows[0][1], _far, rows[1][1]))
            if len(rows) > 2:
                print("            %d further node(s) stay quiet" % (len(rows) - 2))
        else:
            _near = _far = rows[0][0]
            print("alles_demo: one node (client %d, %s) -- it plays both rooms"
                  % (_near, rows[0][1]))
    else:
        # Nothing out there. Take the mesh back down so amy.send() reaches
        # Tulip's own AMY, and play it here.
        alles.off()
        _near = _far = None
        print("alles_demo: no node answered -- Tulip plays both rooms")

    _gen += 1
    # Clear whatever was on the nodes, then wait out the latency it is
    # scheduled against before loading ours. See SETUP_MS.
    _broadcast(reset=amy.RESET_ALL_OSCS + amy.RESET_SEQUENCER)
    if volume is None:
        volume = SOLO_VOLUME if _near is _far else DUO_VOLUME
    _broadcast(volume=volume)
    tulip.defer(_define_voice, (_gen, 0), SETUP_MS)


def stop():
    """Silence every speaker and retire any scheduler still in flight."""
    global _gen, _events
    _gen += 1
    _events = {}
    # Drop our own pending callbacks rather than trusting them to notice the
    # generation change, so nothing keeps re-arming after we have stopped.
    try:
        queue = tulip._defer_queue
        for i in range(len(queue) - 1, -1, -1):
            if queue[i][0] in (_tick, _begin, _define_voice):
                queue.pop(i)
    except (AttributeError, IndexError):
        pass
    if alles.on():
        # Repeated: a note-off lost to a dropped datagram is a note left
        # ringing in another room with nothing to stop it.
        alles.send(retries=3, reset=amy.RESET_ALL_NOTES)
    else:
        amy.send(reset=amy.RESET_ALL_NOTES)


play()
