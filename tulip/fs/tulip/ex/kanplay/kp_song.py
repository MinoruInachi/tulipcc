"""KANTAN Play Core song JSON (format "KANTANPlayCore", type "Song", version
1..3) as plain Python objects, with every default and copy-reference resolved
so the engine never has to look anything up.

Pure Python (json only), so it runs under CPython for tests.
"""
import json

FORMAT = "KANTANPlayCore"
NUM_PARTS = 6
NUM_ROWS = 7            # pitch rows 0..5 are chord tones 1..6, row 6 is drums-only
MAX_STEPS = 64
DRUM_TONE = 128
DEFAULT_DRUM_NOTE = [57, 42, 46, 50, 39, 38, 36]

PART_DEFAULTS = {
    "volume": 100, "tone": 0, "octave": 0, "voicing": "Close",
    "loop_step": 1, "anchor_step": 0, "stroke_speed": 20, "enabled": True,
    "pan": 0,
}
SLOT_DEFAULTS = {"key_offset": 0, "step_per_beat": 2}


class Part:
    def __init__(self, d=None, drum_note=None):
        d = d or {}
        for k, v in PART_DEFAULTS.items():
            setattr(self, k, d.get(k, v))
        rows = d.get("arpeggio") or []
        self.arpeggio = [list(rows[i]) if i < len(rows) else [] for i in range(NUM_ROWS)]
        self.style = list(d.get("style") or [])
        self.drum_note = list(d.get("drum_note") or drum_note or DEFAULT_DRUM_NOTE)

    def copy(self):
        p = Part()
        for k in PART_DEFAULTS:
            setattr(p, k, getattr(self, k))
        p.arpeggio = [list(r) for r in self.arpeggio]
        p.style = list(self.style)
        p.drum_note = list(self.drum_note)
        return p

    @property
    def is_drum(self):
        return self.tone == DRUM_TONE

    def velocity(self, row, step):
        r = self.arpeggio[row]
        return r[step] if step < len(r) else 0

    def style_at(self, step):
        return self.style[step] if step < len(self.style) else ""

    def active_rows(self):
        return [i for i in range(NUM_ROWS) if any(self.arpeggio[i])]

    def plays(self):
        return self.enabled and self.volume > 0 and bool(self.active_rows())

    def max_polyphony(self):
        """The most rows that sound on any one step: the voices the part
        needs. A strummed chord needs six, an arpeggio that alternates rows
        only one or two, and AMY render time on the Tab5 is spent per voice."""
        n = max([len(r) for r in self.arpeggio] + [0])
        best = 0
        for step in range(n):
            best = max(best, sum(1 for r in self.arpeggio if step < len(r) and r[step] > 0))
        return best

    def pattern_length(self):
        n = max([len(r) for r in self.arpeggio] + [len(self.style)])
        return max(n, self.loop_step + 1)

    def to_dict(self):
        d = {k: getattr(self, k) for k in PART_DEFAULTS}
        d["arpeggio"] = [list(r) for r in self.arpeggio]
        d["style"] = list(self.style)
        d["drum_note"] = list(self.drum_note)
        return d


class Slot:
    def __init__(self, d=None, drum_notes=None):
        d = d or {}
        for k, v in SLOT_DEFAULTS.items():
            setattr(self, k, d.get(k, v))
        parts = (d.get("chord_mode") or {}).get("part") or []
        self.parts = [Part(parts[i] if i < len(parts) else None,
                           drum_notes[i] if drum_notes else None)
                      for i in range(NUM_PARTS)]

    def copy(self):
        s = Slot()
        s.key_offset = self.key_offset
        s.step_per_beat = self.step_per_beat
        s.parts = [p.copy() for p in self.parts]
        return s

    def to_dict(self):
        return {"key_offset": self.key_offset, "step_per_beat": self.step_per_beat,
                "chord_mode": {"part": [p.to_dict() for p in self.parts]}}


class Song:
    def __init__(self, d=None, name="Blank"):
        d = d or {}
        self.name = name
        self.version = d.get("version", 3)
        self.tempo = d.get("tempo", 120)
        self.swing = d.get("swing", 0)
        self.base_key = d.get("base_key", 0)
        dn = d.get("drum_note") or []
        self.drum_note = [list(dn[i]) if i < len(dn) else list(DEFAULT_DRUM_NOTE)
                          for i in range(NUM_PARTS)]
        self.slots = self._load_slots(d.get("slot") or [], d.get("num_slot"))
        prog = d.get("progression") or d.get("sequence") or {}
        self.progression_length = prog.get("length", 0)
        self.timeline = self._load_timeline(prog.get("timeline") or {})

    def _load_slots(self, raw, num_slot):
        slots = []
        for sd in raw:
            sd = sd or {}
            if "copy" in sd:
                src = sd["copy"][0]
                slot = slots[src].copy() if 0 <= src < len(slots) else Slot(None, self.drum_note)
            elif not sd and self.version < 3 and slots:
                # version 2 treated an empty slot before the end as a copy of
                # the previous one; version 3 makes it a default slot.
                slot = slots[-1].copy()
            else:
                slot = Slot(sd, self.drum_note)
                parts = (sd.get("chord_mode") or {}).get("part") or []
                for pi, pd in enumerate(parts[:NUM_PARTS]):
                    if isinstance(pd, dict) and "copy" in pd:
                        ss, sp = pd["copy"][0], pd["copy"][1]
                        if 0 <= ss < len(slots) and 0 <= sp < NUM_PARTS:
                            slot.parts[pi] = slots[ss].parts[sp].copy()
            slots.append(slot)
        if num_slot:
            while len(slots) < num_slot:
                slots.append(Slot(None, self.drum_note))
            slots = slots[:num_slot]
        if not slots:
            slots = [Slot(None, self.drum_note)]
        return slots

    def _load_timeline(self, raw):
        """Resolve the diff-style timeline into a sorted list of
        (step, entry) with every field filled in from the previous entry."""
        out = []
        cur = {"main": "1", "mod": "", "bass": "", "slot": 0,
               "part": list(range(NUM_PARTS))}
        for k in sorted(raw.keys(), key=lambda s: int(s)):
            e = raw[k]
            cur = dict(cur)
            cur["bass"] = ""          # bass does not carry over
            for f in ("main", "mod", "bass", "slot", "part"):
                if f in e:
                    cur[f] = e[f]
            out.append((int(k), cur))
        return out

    def to_dict(self):
        return {
            "format": FORMAT, "type": "Song", "version": 3,
            "num_slot": len(self.slots), "tempo": self.tempo, "swing": self.swing,
            "base_key": self.base_key, "drum_note": [list(r) for r in self.drum_note],
            "slot": [s.to_dict() for s in self.slots],
            "progression": {"version": 1, "length": self.progression_length,
                            "timeline": {str(k): dict(v) for k, v in self.timeline}},
        }


def loads(text, name="song"):
    d = json.loads(text)
    if d.get("format") not in (None, FORMAT):
        raise ValueError("not a KANTAN Play song: format %r" % d.get("format"))
    if d.get("type") not in (None, "Song"):
        raise ValueError("not a Song file: type %r" % d.get("type"))
    return Song(d, name)


def load(path):
    name = path.replace("\\", "/").split("/")[-1]
    if name.endswith(".json"):
        name = name[:-5]
    with open(path, "r") as f:
        return loads(f.read(), name)


def save(song, path):
    with open(path, "w") as f:
        f.write(json.dumps(song.to_dict()))


def blank():
    """A one-slot song with a plain piano chord on every step."""
    s = Song(None, "Blank")
    p = s.slots[0].parts[0]
    p.arpeggio[0] = [100]
    p.arpeggio[1] = [100]
    p.arpeggio[2] = [100]
    p.arpeggio[3] = [100]
    return s
