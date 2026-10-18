"""Performance files: one piece, as parts and notes, on disk.

A performance is what reached AMY -- which part played which pitch, how hard,
from when to when -- with nothing of how it was decided. A transcription
module like rydeen.py flattens its score to exactly that before it plays a
note, a sequencer app has it in its pattern data, and a Recorder here can take
it off a live performance; all three land in the same file, and anything that
can read the file can play it, draw it or edit it without knowing where it
came from.

    import perf
    p = perf.load('/user/perf/rydeen.perf')
    player = perf.Player(p)
    player.play()

    r = perf.Recorder('my take')            # capture whatever plays next
    r.start()
    ...
    r.stop().save('/user/perf/take.perf')

The format
----------
Plain text, one record per line, `.perf`. Line-based because the device reads
and writes it a line at a time: no parser state, no allocation spike on a long
piece, and `diff` and a text editor both work on it. The first line is

    # tulip-performance 1

and the version number governs everything after it. Then header lines, one
`key value` each, where the value runs to the end of the line:

    title   Rydeen                  free text, may be empty
    bpm     140                     quarter notes a minute
    ppq     48                      file ticks in a quarter note
    beats   4                       beats in a bar
    bars    30                      informational; save() recomputes it
    setup   V0.5Z                   an AMY wire string sent once at the start

`setup` is for what the piece set up around its notes and cannot be read back
off them -- a master volume, a chorus, a reverb. It is AMY's own wire format
rather than a schema of our own, so anything AMY takes can go in it; several
`setup` lines are concatenated in order.

Then one `part` line per part, in part-index order:

    part 0 Poly patch=141 voices=3 color=86,214,255

The name carries no spaces. The keys are `patch` (an AMY patch number),
`kit` (an AMY drum-kit patch -- a part with one is a drum part and its notes
are General MIDI drum numbers), `voices` (how many notes it may sound at once),
`color` (r,g,b for a display; optional, and a reader may choose its own) and
`ring` (see below). Unknown keys are kept and written back out, so a later
version can add one without this one dropping it.

A part built from a patch of its own rather than a numbered one carries it on
its own line, where the value can hold anything but a newline:

    patchstr 0 v0w8f440,1,0,0,0,1...Z

Then the line `notes`, and one note per line, five integers:

    tick part note velocity duration

`tick` is in `ppq` units from the start of the piece -- the first event is at
0 -- `note` and `velocity` are MIDI-scaled 0..127 (velocity 0 would be a note
off, so it never appears), and `duration` is in ticks, where **0 means no note
off is ever sent**: a drum hit, or a plucked string, which rings for as long
as its envelope does. Nothing in the file says how long that is, so a display
has nothing to draw; a part's `ring` is that length, in ticks, purely as a
drawing hint. It changes no sound.

Notes are written in `tick` order. In memory velocity is a float 0..1, the way
AMY takes it; 0..127 is only how it is stored, so a file reads the same as a
MIDI track and converts to one without a scale change.

What the format deliberately does not carry: tempo changes, controllers, pitch
bend, per-note patch changes. A piece that needs those is not finished being
played when it is written, which is a different thing from a performance.
"""

import amy
import tulip

try:
    import synth
except ImportError:                 # the format alone is useful without AMY
    synth = None

# A sequencer tag must be below amy_config.max_sequencer_tags (256); AMY
# drops anything at or above it without a word.
MAX_TAG = 256

MAGIC = "tulip-performance"
VERSION = 1
SUFFIX = ".perf"
# Where scan() looks, in order of precedence -- a file in /user/pieces hides
# a shipped one of the same name, so a sample can be edited by copying it.
# Not "/user/perf": the REPL runs with /user as the working directory and ""
# first on sys.path, so a directory of that name shadows this module and
# `import perf` finds an empty package instead.
DIRS = ("/user/pieces", "/sys/ex/pieces", "/user")

# General MIDI drum names, for the kit notes a piece is likely to use.
# Six characters at most: these are read in a lane label beside the roll.
GM_DRUMS = {
    35: "kick2", 36: "kick", 37: "stick", 38: "snare", 39: "clap",
    40: "snare2", 41: "tom-l", 42: "hat", 43: "tom-l2", 44: "pedal",
    45: "tom-m", 46: "hat-op", 47: "tom-m2", 48: "tom-h",
    49: "crash", 50: "tom-h2", 51: "ride", 52: "china", 53: "bell",
    54: "tamb", 55: "splash", 56: "cowbel", 57: "crash2", 59: "ride2",
}

# Used for a part whose file gives no colour, by part index.
COLORS = ((255, 88, 146), (86, 214, 255), (166, 128, 255), (255, 186, 74),
          (255, 118, 62), (132, 222, 158), (240, 220, 120), (120, 200, 220))

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def _in_tick_order(rows):
    """Bucket `rows` -- (tick, ...) tuples or lists -- into tick order.

    Not rows.sort(key=...): MicroPython calls the key function on every
    comparison and its sort degenerates on data that is already in order,
    which is exactly the shape of a score. Sorting one piece's 1894 events
    that way took over a minute on a Tab5; bucketing by tick is a single pass
    and runs in milliseconds, at the cost of one list slot per tick of the
    piece. Rows that share a tick keep the order they arrived in, so a caller
    that wants note-offs first puts them in first.
    """
    if not rows:
        return []
    hi = 0
    for r in rows:
        if r[0] > hi:
            hi = r[0]
    buckets = [None] * (hi + 1)
    for r in rows:
        b = buckets[r[0]]
        if b is None:
            buckets[r[0]] = [r]
        else:
            b.append(r)
    out = []
    for b in buckets:
        if b is not None:
            out.extend(b)
    return out


def note_name(n):
    return "%s%d" % (NOTE_NAMES[n % 12], n // 12 - 1)


def drum_name(n):
    return GM_DRUMS.get(n, "d%d" % n)


class Part:
    """One instrument: what it plays with, and how much of it at once."""

    def __init__(self, name="part", patch=None, kit=None, voices=1,
                 color=None, extra=None, patch_string=None, ring=0):
        self.name = name
        self.patch = patch
        self.kit = kit
        self.patch_string = patch_string
        self.voices = voices
        self.color = color
        self.ring = ring                # drawing length for a note with no off
        self.extra = extra or {}        # keys this version does not know
        self.is_drum = kit is not None

    def synth(self, channel):
        """The synth.py object that plays this part on AMY."""
        if synth is None:
            raise RuntimeError("synth.py is not available")
        if self.is_drum:
            return synth.DrumSynth(num_voices=self.voices, channel=channel,
                                   patch=self.kit)
        if self.patch_string is not None:
            return synth.PatchSynth(num_voices=self.voices, channel=channel,
                                    patch_string=self.patch_string)
        return synth.PatchSynth(num_voices=self.voices, channel=channel,
                                patch=self.patch)

    def line(self, index):
        out = ["part %d %s" % (index, self.name.replace(" ", "_"))]
        if self.kit is not None:
            out.append("kit=%d" % self.kit)
        elif self.patch is not None:
            out.append("patch=%d" % self.patch)
        out.append("voices=%d" % self.voices)
        if self.ring:
            out.append("ring=%d" % self.ring)
        if self.color:
            out.append("color=%d,%d,%d" % self.color)
        for k in sorted(self.extra):
            out.append("%s=%s" % (k, self.extra[k]))
        return " ".join(out)


class Performance:
    """A piece: a part table, and every note in tick order."""

    def __init__(self, title="", bpm=120.0, ppq=None, beats=4):
        self.title = title
        self.bpm = bpm
        self.ppq = ppq or amy.AMY_SEQUENCER_PPQ
        self.beats = beats
        # An AMY wire string the piece set up around its notes: volume, an
        # effect, anything. Sent once when a Player starts it.
        self.setup = ""
        self.parts = []
        # (tick, part index, midi note, velocity 0..1, duration in ticks)
        self.notes = []
        self._open = {}                 # (part, note) -> index, while building
        self._released = set()          # parts that were ever sent a note-off

    # ---------------------------------------------------------- building
    def add_part(self, part):
        self.parts.append(part)
        return len(self.parts) - 1

    def note_on(self, tick, part, note, vel):
        """Start a note. A second on for a pitch already sounding ends the
        first one, the way a synth voice behaves."""
        self._close(tick, part, note)
        self._open[(part, note)] = len(self.notes)
        self.notes.append([tick, part, note, vel, 0])

    def note_off(self, tick, part, note):
        # Only a real release marks the part. note_on() closes a pitch it is
        # about to restrike through _close() instead, which must not count:
        # the difference is exactly what finish() uses to tell a part that is
        # released from one that is left to ring.
        self._released.add(part)
        self._close(tick, part, note)

    def _close(self, tick, part, note):
        i = self._open.pop((part, note), None)
        if i is not None:
            n = self.notes[i]
            n[4] = max(1, tick - n[0])

    def finish(self, rebase=True):
        """Close the piece: notes that were never released keep duration 0,
        ticks are rebased so the first note is at 0, and notes go in order."""
        self._open = {}
        if not self.notes:
            return self
        # A part that was never sent a note-off is one whose notes ring on
        # their own envelope -- every drum kit, and any synth patch that
        # decays to zero while the note is still held. Giving those notes a
        # length would make a Player release them, which the piece never did;
        # and without this a repeated hi-hat would pick up a length from the
        # hit that followed it, because note_on() ends a pitch already
        # sounding. Drum parts are included whatever they were sent: AMY's
        # kits ignore note-offs.
        for n in self.notes:
            part = n[1]
            if part not in self._released or (part < len(self.parts)
                                              and self.parts[part].is_drum):
                n[4] = 0
        if rebase:
            first = min(n[0] for n in self.notes)
            if first:
                for n in self.notes:
                    n[0] -= first
        self.notes = [tuple(n) for n in _in_tick_order(self.notes)]
        return self

    # ---------------------------------------------------------- reading
    def ticks_per_bar(self):
        return self.beats * self.ppq

    def total(self):
        t = 0
        for tick, _p, _n, _v, dur in self.notes:
            if tick + dur > t:
                t = tick + dur
        return t or 1

    def bars(self):
        tpb = self.ticks_per_bar()
        return (self.total() + tpb - 1) // tpb

    def spans(self):
        """Per part, [(on, off, note, vel)] in order -- what a display wants.

        A drum hit (duration 0) is given a short one so it can be drawn and
        metered; nothing is ever sent to AMY for its end.
        """
        hit = max(1, self.ppq // 8)
        out = [[] for _ in self.parts]
        for tick, part, note, vel, dur in self.notes:
            if part < len(out):
                out[part].append((tick, tick + (dur or self.parts[part].ring
                                                or hit), note, vel))
        return out

    def pitch_range(self):
        """The lowest and highest pitched (non-drum) note, or None."""
        lo, hi = 128, -1
        for _t, part, note, _v, _d in self.notes:
            if part < len(self.parts) and not self.parts[part].is_drum:
                if note < lo:
                    lo = note
                if note > hi:
                    hi = note
        return None if hi < lo else (lo, hi)

    def drum_notes(self):
        """Every drum pitch the piece uses, highest first."""
        seen = set()
        for _t, part, note, _v, _d in self.notes:
            if part < len(self.parts) and self.parts[part].is_drum:
                seen.add(note)
        return sorted(seen, reverse=True)

    def events(self):
        """(tick, is_on, part, note, vel), offs before ons at the same tick.

        The offs go in first so that bucketing, which is stable, leaves a
        repeated pitch released before it is struck again.
        """
        offs, ons = [], []
        for tick, part, note, vel, dur in self.notes:
            ons.append((tick, 1, part, note, vel))
            if dur:
                offs.append((tick + dur, 0, part, note, 0))
        return _in_tick_order(offs + ons)

    # ---------------------------------------------------------- writing
    def save(self, path):
        with open(path, "w") as f:
            f.write("# %s %d\n" % (MAGIC, VERSION))
            f.write("title %s\n" % self.title)
            f.write("bpm %g\n" % self.bpm)
            f.write("ppq %d\n" % self.ppq)
            f.write("beats %d\n" % self.beats)
            f.write("bars %d\n" % self.bars())
            if self.setup:
                f.write("setup %s\n" % self.setup)
            for i, part in enumerate(self.parts):
                f.write(part.line(i) + "\n")
                if part.patch_string is not None:
                    f.write("patchstr %d %s\n" % (i, part.patch_string))
            f.write("notes\n")
            # Written a chunk at a time: a long piece is thousands of lines and
            # one joined string of them is the biggest allocation in the file.
            buf = []
            for tick, part, note, vel, dur in self.notes:
                buf.append("%d %d %d %d %d\n" %
                           (tick, part, note, min(127, int(vel * 127 + 0.5)), dur))
                if len(buf) >= 256:
                    f.write("".join(buf))
                    buf = []
            if buf:
                f.write("".join(buf))
        return path


def _part_from_line(rest):
    """`0 Poly patch=141 voices=3 color=86,214,255` -> (index, Part)."""
    bits = rest.split()
    index, name = int(bits[0]), bits[1].replace("_", " ")
    part = Part(name)
    for bit in bits[2:]:
        if "=" not in bit:
            continue
        k, v = bit.split("=", 1)
        if k == "patch":
            part.patch = int(v)
        elif k == "kit":
            part.kit = int(v)
        elif k == "voices":
            part.voices = int(v)
        elif k == "ring":
            part.ring = int(v)
        elif k == "color":
            part.color = tuple(int(c) for c in v.split(","))
        else:
            part.extra[k] = v
    part.is_drum = part.kit is not None
    return index, part


def header(path):
    """The header alone: enough to list a file without reading its notes."""
    info = {"path": path, "title": "", "bpm": 0, "bars": 0, "parts": 0}
    with open(path) as f:
        first = f.readline()
        if MAGIC not in first:
            raise ValueError("%s: not a performance file" % path)
        for line in f:
            line = line.strip()
            if not line or line[0] == "#":
                continue
            if line == "notes":
                break
            key, _sp, rest = line.partition(" ")
            if key == "part":
                info["parts"] += 1
            elif key == "title":
                info["title"] = rest
            elif key in ("bpm",):
                info["bpm"] = float(rest)
            elif key in ("bars", "ppq", "beats"):
                info[key] = int(rest)
    if not info["title"]:
        info["title"] = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return info


def load(path):
    p = Performance()
    parts = {}
    with open(path) as f:
        first = f.readline()
        if MAGIC not in first:
            raise ValueError("%s: not a performance file" % path)
        in_notes = False
        for line in f:
            line = line.strip()
            if not line or line[0] == "#":
                continue
            if not in_notes:
                if line == "notes":
                    in_notes = True
                    for i in sorted(parts):
                        p.parts.append(parts[i])
                    continue
                key, _sp, rest = line.partition(" ")
                if key == "part":
                    i, part = _part_from_line(rest)
                    parts[i] = part
                elif key == "patchstr":
                    i, _sp2, wire = rest.partition(" ")
                    if int(i) in parts:
                        parts[int(i)].patch_string = wire
                elif key == "setup":
                    p.setup += rest
                elif key == "title":
                    p.title = rest
                elif key == "bpm":
                    p.bpm = float(rest)
                elif key == "ppq":
                    p.ppq = int(rest)
                elif key == "beats":
                    p.beats = int(rest)
                continue
            tick, part, note, vel, dur = line.split()
            p.notes.append((int(tick), int(part), int(note),
                            int(vel) / 127.0, int(dur)))
    if not p.parts:                     # a file with no `notes` line at all
        for i in sorted(parts):
            p.parts.append(parts[i])
    for i, part in enumerate(p.parts):
        if part.color is None:
            part.color = COLORS[i % len(COLORS)]
    if not p.title:
        p.title = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    # A file written by save() is already in tick order, so this is one pass
    # over it and nothing more in the normal case.
    last = -1
    for n in p.notes:
        if n[0] < last:
            p.notes = _in_tick_order(p.notes)
            break
        last = n[0]
    return p


def scan(dirs=DIRS):
    """Every readable performance file in `dirs`, as header() dicts."""
    import os
    out, seen = [], set()
    for d in dirs:
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for name in sorted(names):
            if not name.endswith(SUFFIX):
                continue
            path = (d + "/" + name).replace("//", "/")
            if name in seen:            # /user/perf wins over /user
                continue
            seen.add(name)
            try:
                out.append(header(path))
            except (OSError, ValueError):
                pass
    return out


# -------------------------------------------------------------------- player

class Player:
    """Hand a Performance to AMY's sequencer, a few beats at a time.

    Queuing a whole piece at once does not fit: AMY holds 256 scheduled events
    for every app on the device together, and a bass part alone can be 64 a
    bar. So a deferred pump keeps LOOKAHEAD_BEATS of it queued and no more --
    which is also what lets stop() be immediate, since only what is already
    queued has to be cleared.
    """

    # Three beats is ~1.3 s at 140 BPM: longer than any stall Python is likely
    # to take (an app switch is ~850 ms of C work), short enough to leave most
    # of AMY's queue to everything else.
    LOOKAHEAD_BEATS = 3

    def __init__(self, perf, channel=40, tag=200):
        """`channel` is the first AMY synth number to use, one per part, and
        `tag` the first AMY sequencer tag -- one per part as well, so a single
        part can be taken back out of the queue without disturbing the rest,
        which is what mute() needs.

        A tag must be below amy_config.max_sequencer_tags, which is 256:
        sequencer_add_wire() bounds-checks it and silently drops anything
        above, so a base of 300 plays nothing at all. 200 leaves thirty parts
        before loopstudio's tags (230..) and is clear of kanplay (240..) and
        rydeen.py (250); the synth numbers are clear of MIDI (1..16),
        drums.py (20..), loopstudio (48..) and kanplay (58..).
        """
        self.perf = perf
        self.channel = channel
        self.tag = tag
        # Which parts are silenced. Kept across play() and stop(): a mute is
        # the listener's, not the piece's, and nothing of it is written to the
        # file.
        self.muted = [False] * len(perf.parts)
        # One tag per part while they fit under the 256 limit; past that they
        # share the base tag and a mute takes the stragglers with it.
        self.tags = [tag + i if tag + i < MAX_TAG else tag
                     for i in range(len(perf.parts))]
        self.synths = []
        self.events = []
        self.next = 0
        self.start_tick = 0
        self.gen = 0
        self.playing = False
        self.scale = amy.AMY_SEQUENCER_PPQ / float(perf.ppq)
        # Bound once: tulip's defer queue is searched by identity in stop(),
        # and `self._pump` makes a new object every time it is written.
        self._pump_cb = self._pump
        self._finish_cb = self._finish

    # The piece's own ticks, which is what a display works in.
    def position(self):
        if not self.playing:
            return 0
        return int((amy.sequencer_ticks() - self.start_tick) / self.scale)

    def play(self):
        self.stop()
        perf = self.perf
        tulip.seq_bpm(perf.bpm)
        if perf.setup:
            # Whatever the piece set up around its notes -- volume, an effect.
            amy.send_wire(perf.setup)
        self.synths = [p.synth(self.channel + i) for i, p in enumerate(perf.parts)]
        # Build the AMY synths now rather than on the first scheduled note, so
        # loading a patch does not land in the middle of the music.
        for s in self.synths:
            s.deferred_init()
        self.events = perf.events()
        self.next = 0
        ppq = amy.AMY_SEQUENCER_PPQ
        now = amy.sequencer_ticks()
        # Start on a beat, past the lookahead, so the first beat is queued whole.
        self.start_tick = now - now % ppq + (self.LOOKAHEAD_BEATS + 1) * ppq
        self.gen += 1
        self.playing = True
        self._pump(self.gen)

    def _pump(self, gen):
        if gen != self.gen:                 # a stop() or a later play()
            return
        ppq = amy.AMY_SEQUENCER_PPQ
        horizon = amy.sequencer_ticks() + self.LOOKAHEAD_BEATS * ppq
        events, synths = self.events, self.synths
        while self.next < len(events):
            tick, is_on, part, note, vel = events[self.next]
            tick = self.start_tick + int(tick * self.scale)
            if tick > horizon:
                break
            # A muted part still advances the cursor: it is being played,
            # just not heard, so unmuting picks up at the next note rather
            # than flushing out everything it missed.
            if part < len(synths) and not self.muted[part]:
                ticks = "%d,0,%d" % (tick, self.tags[part])
                if is_on:
                    synths[part].note_on(note, vel, ticks=ticks)
                else:
                    synths[part].note_off(note, ticks=ticks)
            self.next += 1
        beat_ms = 60000.0 / self.perf.bpm
        if self.next < len(events):
            tulip.defer(self._pump_cb, gen, int(beat_ms / 2))
        else:
            # Queued to the end: let the lookahead play out and the longest
            # release ring before the synths go back to AMY.
            tulip.defer(self._finish_cb, gen,
                        int(beat_ms * (self.LOOKAHEAD_BEATS + 4)))

    def _finish(self, gen):
        if gen == self.gen:
            self.stop()

    def mute(self, part, on=None):
        """Silence one part, or let it sound again. Returns its new state.

        Muting has to reach back into what is already queued -- the pump runs
        three beats ahead, so otherwise a part would keep playing for over a
        second after the tap. Clearing the part's own tag drops that, and
        takes its pending note-offs with it, so the voices sounding right now
        are released by hand.
        """
        if not 0 <= part < len(self.muted):
            return False
        on = (not self.muted[part]) if on is None else bool(on)
        self.muted[part] = on
        if on and part < len(self.synths):
            amy.send(ticks="0,0,%d" % (self.tags[part]))
            try:
                self.synths[part].all_notes_off()
            except Exception:
                pass
        return on

    def stop(self):
        self.gen += 1
        self.playing = False
        # Drop our pending callbacks rather than waiting for them to notice.
        try:
            queue = tulip._defer_queue
            for i in range(len(queue) - 1, -1, -1):
                if queue[i][0] in (self._pump_cb, self._finish_cb):
                    queue.pop(i)
        except (AttributeError, IndexError):
            pass
        # Clear only our own tags: another app's queued notes keep playing.
        for t in set(self.tags) or (self.tag,):
            amy.send(ticks="0,0,%d" % t)
        synths, self.synths = self.synths, []
        for s in synths:
            try:
                s.all_notes_off()
                s.release()
            except Exception:
                pass


# ------------------------------------------------------------------ recorder

class Recorder:
    """Capture whatever plays through synth.py into a Performance.

    Every piece on this device reaches AMY through PatchSynth.note_on, whether
    it came from a transcription module, a sequencer app or the MIDI input, so
    wrapping that one pair of methods records all of them without any of them
    knowing. Notes scheduled ahead carry their tick in `ticks=`, and that --
    not when the call happened -- is when they sound, so it is what is kept.
    """

    def __init__(self, title="", beats=4):
        self.perf = Performance(title=title, beats=beats)
        self.by_synth = {}
        self._on = self._off = None
        self._started = False

    def _part(self, s):
        key = s.synth
        if key not in self.by_synth:
            kit = isinstance(s, synth.DrumSynth) if synth else False
            # Named after what it plays with, since a recording has no other
            # name for it. The file is text: rename it there afterwards.
            wire = None if kit else getattr(s, "patch_string", None)
            if kit:
                name = "kit%s" % s.patch
            elif wire is not None:
                name = "patch%d" % len(self.perf.parts)
            else:
                name = "patch%s" % s.patch
            part = Part(name=name,
                        patch=None if (kit or wire is not None) else s.patch,
                        kit=s.patch if kit else None,
                        patch_string=wire,
                        voices=getattr(s, "num_voices", 1))
            self.by_synth[key] = self.perf.add_part(part)
        return self.by_synth[key]

    def _note(self, s, note, vel, ticks, on):
        if not self._started:
            # The tempo belongs to the piece being played, and whatever set it
            # may only have done so as it started -- after start() was called.
            self._started = True
            self.perf.bpm = tulip.seq_bpm() or self.perf.bpm
        tick, part = self._tick(ticks), self._part(s)
        if on:
            self.perf.note_on(tick, part, note, vel)
        else:
            self.perf.note_off(tick, part, note)

    def _tick(self, ticks):
        if ticks:
            try:
                return int(str(ticks).split(",")[0])
            except ValueError:
                pass
        return amy.sequencer_ticks()

    def start(self):
        if synth is None:
            raise RuntimeError("synth.py is not available")
        if self._on is not None:
            return
        rec = self
        self._on = synth.PatchSynth.note_on
        self._off = synth.PatchSynth.note_off
        real_on, real_off = self._on, self._off

        def note_on(s, note, velocity=1, ticks=None, **kw):
            rec._note(s, note, velocity, ticks, bool(velocity))
            return real_on(s, note, velocity, ticks=ticks, **kw)

        def note_off(s, note, ticks=None):
            rec._note(s, note, 0, ticks, False)
            return real_off(s, note, ticks=ticks)

        synth.PatchSynth.note_on = note_on
        synth.PatchSynth.note_off = note_off

    def stop(self):
        """Put synth.py back and return the Performance."""
        if self._on is not None:
            synth.PatchSynth.note_on = self._on
            synth.PatchSynth.note_off = self._off
            self._on = self._off = None
        for i, part in enumerate(self.perf.parts):
            part.color = COLORS[i % len(COLORS)]
        return self.perf.finish()
