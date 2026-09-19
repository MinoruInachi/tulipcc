"""CPython tests for the kanplay chord engine, song loader and scheduler.

Run from the repo root:  python3 -m unittest tulip/tests/test_kanplay.py

tulip, amy, synth and sequencer are stubbed so the scheduler's AMY messages
can be inspected without hardware.
"""
import json
import os
import sys
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.normpath(os.path.join(HERE, "..", "fs", "tulip", "ex", "kanplay"))
sys.path.insert(0, APP)


# --- stubs -------------------------------------------------------------------

class _Amy(types.ModuleType):
    AMY_SEQUENCER_PPQ = 48

    def __init__(self):
        super().__init__("amy")
        self.sent = []
        self.ticks = 1000

    def send(self, **kw):
        self.sent.append(kw)

    def sequencer_ticks(self):
        return self.ticks


class _Synth:
    def __init__(self, amy, kind, patch, num_voices, channel=None):
        self.amy = amy
        self.channel = channel
        self.kind = kind
        self.patch = patch
        self.num_voices = num_voices
        self.released = False

    def note_on(self, note, velocity=1, ticks=None, **kw):
        self.amy.sent.append(dict(synth=self.kind, patch=self.patch, note=note,
                                  vel=velocity, ticks=ticks, **kw))

    def note_off(self, note, ticks=None):
        self.amy.sent.append(dict(synth=self.kind, patch=self.patch, note=note,
                                  vel=0, ticks=ticks))

    def all_notes_off(self):
        self.amy.sent.append(dict(synth=self.kind, patch=self.patch, note=0, vel=0))

    def release(self):
        self.released = True


def install_stubs():
    amy = _Amy()
    synth = types.ModuleType("synth")
    synth.PatchSynth = lambda num_voices=4, patch=None, channel=None, **kw: _Synth(amy, "patch", patch, num_voices, channel)
    synth.DrumSynth = lambda num_voices=1, patch=384, channel=None: _Synth(amy, "drum", patch, num_voices, channel)
    tulip = types.ModuleType("tulip")
    tulip.state = {"bpm": 120, "keys": (0, 0, 0, 0, 0, 0, 0), "kb_cb": None,
                   "midi": []}

    def seq_bpm(bpm=None):
        if bpm is not None:
            tulip.state["bpm"] = bpm
        return tulip.state["bpm"]
    tulip.seq_bpm = seq_bpm
    tulip.keys = lambda: tulip.state["keys"]
    tulip.keyboard_callback = lambda cb=None: tulip.state.__setitem__("kb_cb", cb)
    tulip.midi_out = lambda b: tulip.state["midi"].append(bytes(b))
    tulip.root_dir = lambda: "/"
    tulip.seq_add_callback = lambda f, t, p: 1
    tulip.seq_remove_callback = lambda tag: None
    sequencer = types.ModuleType("sequencer")

    class TulipSequence:
        def __init__(self, divider, func):
            self.func = func
            self.cleared = False

        def clear(self):
            self.cleared = True
    sequencer.TulipSequence = TulipSequence
    for m in (amy, synth, tulip, sequencer):
        sys.modules[m.__name__] = m
    for m in ("kp_chords", "kp_song", "kp_tones", "kp_input", "kp_engine"):
        sys.modules.pop(m, None)
    return amy, tulip


AMY, TULIP = install_stubs()
import kp_chords      # noqa: E402
import kp_engine      # noqa: E402
import kp_input       # noqa: E402
import kp_song        # noqa: E402
import kp_tones       # noqa: E402


# --- chords ------------------------------------------------------------------

class ChordTests(unittest.TestCase):
    def test_diatonic_names_in_c(self):
        names = [kp_chords.Chord(d).name() for d in range(1, 8)]
        self.assertEqual(names, ["C", "Dm", "Em", "F", "G", "Am", "Bdim"])

    def test_key_and_modifiers(self):
        self.assertEqual(kp_chords.Chord(5, key=7, modifier="7").name(), "D7")
        self.assertEqual(kp_chords.Chord(2, modifier="7").name(), "Dm7")
        self.assertEqual(kp_chords.Chord(1, modifier="M7").name(), "CM7")
        self.assertEqual(kp_chords.Chord(4, modifier="sus4").name(), "Fsus4")
        self.assertEqual(kp_chords.Chord(6, modifier="add9").name(), "Amadd9")
        self.assertEqual(kp_chords.Chord(7, modifier="m7-5").name(), "Bm7-5")

    def test_swap_and_shift(self):
        self.assertEqual(kp_chords.Chord(6, minor_swap=True).name(), "A")
        self.assertEqual(kp_chords.Chord(1, minor_swap=True).name(), "Cm")
        self.assertEqual(kp_chords.Chord(7, semitone_shift=-1).name(), "Bbdim")
        self.assertEqual(kp_chords.Chord(3, semitone_shift=-1, minor_swap=True).name(), "Eb")

    def test_on_chord(self):
        c = kp_chords.Chord(1, bass_degree=3)
        self.assertEqual(c.name(), "C/E")
        notes = c.notes("Close")
        self.assertEqual(notes[0] % 12, 4)
        self.assertLess(notes[0], notes[1])

    def test_close_voicing(self):
        self.assertEqual(kp_chords.Chord(1).notes("Close"), [48, 52, 55, 60, 64, 67])
        self.assertEqual(kp_chords.Chord(5, modifier="7").notes("Close"), [55, 59, 62, 65, 67, 71])
        # position moves the register window, as the KANTAN API describes
        self.assertEqual(kp_chords.Chord(1, position=12).notes("Close")[0], 60)
        self.assertEqual(kp_chords.Chord(1, position=-12).notes("Close")[0], 36)

    def test_guitar_voicing_is_on_chord_tones(self):
        for d in range(1, 8):
            for mod in kp_chords.MODIFIERS:
                c = kp_chords.Chord(d, modifier=mod)
                pcs = set(c.pitch_classes())
                notes = c.notes("Guitar")
                self.assertEqual(len(notes), 6)
                self.assertEqual(notes[0] % 12, c.root_pc, (d, mod))
                for n in notes:
                    if n:
                        self.assertIn(n % 12, pcs, (d, mod, notes))
                self.assertGreaterEqual(min(n for n in notes if n), 40)

    def test_static_voicing_root_is_pitch_3(self):
        for d in range(1, 8):
            c = kp_chords.Chord(d)
            notes = c.notes("Static")
            self.assertEqual(notes[2] % 12, c.root_pc)
            self.assertTrue(48 <= notes[2] <= 59)
            self.assertEqual(notes, sorted(notes))

    def test_ukulele(self):
        notes = kp_chords.Chord(1).notes("Ukulele")
        self.assertEqual(notes[4:], [0, 0])
        self.assertEqual(sorted(n % 12 for n in notes[:4]), [0, 0, 4, 7])

    def test_melody_voicings(self):
        self.assertEqual(kp_chords.Chord(1).notes("M-Major"), [60, 62, 64, 65, 67, 69])
        self.assertEqual(kp_chords.Chord(1).notes("M-Penta"), [60, 62, 64, 67, 69, 72])
        self.assertEqual(kp_chords.Chord(1, key=2).notes("M-Chroma"), [62, 63, 64, 65, 66, 67])

    def test_parse_degree(self):
        self.assertEqual(kp_chords.parse_degree("3b~"), (3, -1, True))
        self.assertEqual(kp_chords.parse_degree("5#"), (5, 1, False))
        c = kp_chords.chord_from_step({"main": "6~", "mod": "7", "bass": "3"})
        self.assertEqual(c.name(), "A7/E")


# --- song --------------------------------------------------------------------

SONG_DIR = os.path.join(APP, "songs")


class SongTests(unittest.TestCase):
    def test_bundled_songs_load(self):
        for name in sorted(os.listdir(SONG_DIR)):
            if not name.endswith(".json"):
                continue
            s = kp_song.load(os.path.join(SONG_DIR, name))
            self.assertGreaterEqual(len(s.slots), 1)
            self.assertTrue(any(p.plays() for sl in s.slots for p in sl.parts), name)

    def test_defaults_and_copy(self):
        d = {"format": "KANTANPlayCore", "type": "Song", "version": 3, "tempo": 99,
             "drum_note": [[1, 2, 3, 4, 5, 6, 7]] * 6,
             "slot": [
                 {"step_per_beat": 4, "chord_mode": {"part": [
                     {"tone": 25, "voicing": "Guitar", "arpeggio": [[100], [], [90]], "style": ["D"]},
                     {"tone": 128, "arpeggio": [[], [], [], [], [], [], [80]]},
                 ]}},
                 {"copy": [0]},
                 {"chord_mode": {"part": [{"copy": [0, 1]}]}},
                 {},
             ]}
        s = kp_song.loads(json.dumps(d))
        self.assertEqual(s.tempo, 99)
        self.assertEqual(len(s.slots), 4)
        p0 = s.slots[0].parts[0]
        self.assertEqual((p0.tone, p0.voicing, p0.loop_step, p0.stroke_speed), (25, "Guitar", 1, 20))
        self.assertEqual(p0.active_rows(), [0, 2])
        self.assertEqual(p0.velocity(2, 0), 90)
        self.assertEqual(p0.velocity(2, 5), 0)
        self.assertEqual(p0.style_at(0), "D")
        self.assertEqual(p0.style_at(3), "")
        self.assertEqual(p0.max_polyphony(), 2)
        self.assertTrue(s.slots[0].parts[1].is_drum)
        self.assertEqual(s.slots[0].parts[1].drum_note, [1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(s.slots[1].step_per_beat, 4)
        self.assertEqual(s.slots[1].parts[0].tone, 25)
        self.assertTrue(s.slots[2].parts[0].is_drum)
        self.assertEqual(s.slots[3].step_per_beat, 2)
        self.assertFalse(s.slots[3].parts[0].plays())
        # round trip
        s2 = kp_song.loads(json.dumps(s.to_dict()))
        self.assertEqual(s2.slots[2].parts[0].drum_note, [1, 2, 3, 4, 5, 6, 7])

    def test_timeline_resolution(self):
        d = {"progression": {"length": 16, "timeline": {
            "0": {"main": "1", "mod": "M7", "slot": 1, "part": [0, 1]},
            "8": {"main": "5", "bass": "3"},
            "12": {"mod": "7"}}}}
        s = kp_song.Song(d)
        self.assertEqual([k for k, _ in s.timeline], [0, 8, 12])
        e8 = dict(s.timeline[1][1])
        self.assertEqual((e8["main"], e8["mod"], e8["bass"], e8["slot"]), ("5", "M7", "3", 1))
        e12 = s.timeline[2][1]
        self.assertEqual((e12["main"], e12["mod"], e12["bass"]), ("5", "7", ""))


# --- tones -------------------------------------------------------------------

class ToneTests(unittest.TestCase):
    def test_every_program_maps(self):
        for p in range(128):
            self.assertIsInstance(kp_tones.patch_for(p), int)
        self.assertEqual(kp_tones.patch_for(2), 256)
        self.assertEqual(kp_tones.patch_for(0), 135)
        trimmed = kp_tones.fit_voices([6, 6, 6, 1], [25, 2, 33, 128])
        self.assertTrue(all(1 <= v <= 6 for v in trimmed), trimmed)
        self.assertLessEqual(sum(v * kp_tones.voice_cost(pr) for v, pr in zip(trimmed, [25, 2, 33, 128])), kp_tones.RENDER_BUDGET)
        self.assertLess(sum(trimmed), 19)
        self.assertEqual(kp_tones.fit_voices([4], [2]), [4])
        # Pop_Basic1's shape: the strum and the keys share the cut
        self.assertEqual(kp_tones.fit_voices([1, 1, 6, 4], [128, 33, 25, 5]), [1, 1, 4, 3])
        self.assertEqual(kp_tones.fit_voices([1, 1, 1, 1, 1, 1], [0] * 6), [1] * 6)
        self.assertEqual(kp_tones.make_synth(128, 6, 60).kind, "drum")
        self.assertEqual(kp_tones.make_synth(33, 9, 60).num_voices, 6)
        self.assertEqual(kp_tones.make_synth(33, 9, 60).channel, 60)
        self.assertEqual(kp_tones.name_for(25), "Steel Guitar")
        self.assertEqual(kp_tones.name_for(128), "Drums")
        self.assertEqual(kp_tones.next_tone(128, 1), 0)
        self.assertEqual(kp_tones.next_tone(0, -1), 128)
        self.assertEqual(kp_tones.next_tone(3, 1), 4)     # 3 is not a choice
        self.assertEqual(kp_tones.next_tone(3, -1), 2)
        self.assertEqual(kp_tones.next_tone(25, 1), 26)


# --- input -------------------------------------------------------------------

class InputTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.inp = kp_input.Inputs(listener=lambda b, d: self.events.append((b, d)))
        self.inp.start()

    def test_keyboard_edges_from_scan_codes(self):
        TULIP.state["keys"] = (0, 0x1e + 4, 0x0d, 0, 0, 0, 0)   # '5' and 'j'
        self.inp.poll()
        self.assertEqual(sorted(self.events), [("5", True), ("7th", True)])
        self.assertEqual(self.inp.held_modifier(), "7")
        self.assertEqual(self.inp.roots_down(), ["5"])
        TULIP.state["keys"] = (0, 0x0d, 0, 0, 0, 0, 0)
        self.inp.poll()
        self.assertEqual(self.events[-1], ("5", False))
        self.assertEqual(self.inp.roots_down(), [])

    def test_keypad_and_sharp(self):
        TULIP.state["keys"] = (0, 0x59, 0x27 - 0, 0, 0, 0, 0)    # KP1 and '0' (unmapped)
        self.inp.poll()
        self.assertEqual(self.events, [("1", True)])
        TULIP.state["keys"] = (0, 0x59, 0x26, 0, 0, 0, 0)        # KP1 and '9'
        self.inp.poll()
        self.assertEqual(self.inp.semitone_shift(), 1)

    def test_ui_and_midi_sources_merge(self):
        self.inp.ui_down("dim")
        self.inp.midi_note(62, True)                          # D -> "2"
        self.assertEqual(sorted(self.inp.held), ["2", "dim"])
        self.inp.midi_note(62, False)
        self.inp.ui_up("dim")
        self.assertEqual(self.inp.held, set())
        self.events = []
        self.inp.set_touch({"sus4", "5"})                     # two fingers, one frame
        self.assertEqual(self.events, [("sus4", True), ("5", True)])
        self.assertEqual(self.inp.held_modifier(), "sus4")
        self.inp.set_touch({"sus4"})
        self.assertEqual(self.events[-1], ("5", False))
        self.inp.set_touch(set())
        self.assertEqual(self.inp.held, set())
        self.inp.midi_note(61, True)                          # C# is not a root
        self.assertEqual(self.inp.held, set())

    def test_stop_clears_everything(self):
        TULIP.state["keys"] = (0, 0x1e, 0, 0, 0, 0, 0)
        self.inp.poll()
        self.inp.stop()
        self.assertEqual(self.events[-1], ("1", False))
        self.assertIsNone(TULIP.state["kb_cb"])
        TULIP.state["keys"] = (0, 0, 0, 0, 0, 0, 0)


# --- engine ------------------------------------------------------------------

def _song(parts, step_per_beat=2, tempo=120, swing=0):
    d = {"format": "KANTANPlayCore", "type": "Song", "version": 3, "tempo": tempo,
         "swing": swing, "slot": [{"step_per_beat": step_per_beat,
                                   "chord_mode": {"part": parts}}]}
    return kp_song.loads(json.dumps(d))


def _tick_of(msg):
    return int(str(msg["ticks"]).split(",")[0])


def _tag_of(msg):
    return int(str(msg["ticks"]).split(",")[2])


class EngineTests(unittest.TestCase):
    def setUp(self):
        AMY.sent = []
        AMY.ticks = 1000
        TULIP.state["midi"] = []
        self.player = kp_engine.Player()

    def ons(self):
        return [m for m in AMY.sent if "note" in m and m.get("vel", 0) > 0]

    def offs(self):
        return [m for m in AMY.sent if m.get("vel") == 0 and m.get("note")]

    def test_rows_map_to_pitches_high_first(self):
        # Row 0 is the highest tone (pitch 6), row 5 the lowest (pitch 1).
        song = _song([{"tone": 0, "arpeggio": [[100], [], [], [], [], [100]], "loop_step": 0}])
        self.player.load_song(song)
        self.player.press(1)
        notes = sorted(m["note"] for m in self.ons())
        self.assertEqual(notes, [48, 67])       # C3 and G4 of C: 48 52 55 60 64 67

    def test_press_is_on_beat_release_is_off_beat(self):
        song = _song([{"tone": 0, "arpeggio": [[100, 0, 80, 0], [100, 0, 80, 0]], "loop_step": 3}],
                     step_per_beat=2)
        self.player.load_song(song)
        self.player.press(1)                     # step 0
        self.assertEqual(len(self.ons()), 2)
        self.assertEqual(self.player.parts[0].step, 0)
        self.assertTrue(all(_tick_of(m) == 1000 for m in self.ons()))
        AMY.sent = []
        AMY.ticks = 1010
        self.player.release(1)                   # step 1: silent in this pattern
        self.assertEqual(self.player.parts[0].step, 1)
        self.assertEqual(self.ons(), [])
        self.player.release(1)                   # a second release does not move
        self.assertEqual(self.player.parts[0].step, 1)
        AMY.ticks = 1024
        self.player.press(1)                     # same chord: continues to step 2
        self.assertEqual(self.player.parts[0].step, 2)
        self.assertEqual([m["vel"] for m in self.ons()], [80 / 127.0 * kp_engine.MASTER_GAIN] * 2)
        AMY.ticks = 1048
        self.player.press(1)                     # step 4 > loop_step 3 -> wraps to 0
        self.assertEqual(self.player.parts[0].step, 0)
        self.assertEqual(self.player.onbeat_cycle, 24)

    def test_chord_change_respects_anchor(self):
        song = _song([
            {"tone": 0, "arpeggio": [[100, 90, 80, 70]], "loop_step": 3, "anchor_step": 0},
            {"tone": 128, "arpeggio": [[], [], [], [], [], [], [100, 90, 80, 70]], "loop_step": 3, "anchor_step": 64},
        ], step_per_beat=2)
        self.player.load_song(song)
        self.player.press(1)
        self.player.release(1)
        self.player.press(1)
        self.assertEqual([pp.step for pp in self.player.parts[:2]], [2, 2])
        AMY.sent = []
        self.player.press(4)                     # chord change
        self.assertEqual(self.player.parts[0].step, 0)   # past anchor 0: restart
        self.assertEqual(self.player.parts[1].step, 0)   # 2+2 = 4 > loop 3: wraps anyway
        self.player.release(4)
        self.player.press(4)
        self.player.release(4)
        self.player.press(6)                     # change while both at step 2
        self.assertEqual(self.player.parts[0].step, 0)
        self.assertEqual(self.player.parts[1].step, 0)   # 2 -> 4 wraps
        self.player.release(6)
        self.player.press(6)                     # -> 2 (no change)
        self.player.press(6)                     # -> 4 wraps 0
        self.player.release(6)                   # -> 1
        self.player.press(2)                     # change at step 1 (< anchor 64): drums keep going
        self.assertEqual(self.player.parts[0].step, 0)
        self.assertEqual(self.player.parts[1].step, 2)
        self.assertEqual(self.player.chord.name(), "Dm")

    def test_strum_and_note_offs(self):
        song = _song([{"tone": 25, "voicing": "Guitar", "stroke_speed": 20,
                       "arpeggio": [[100], [100], [100], [100], [100], [100]],
                       "style": ["D"], "loop_step": 0}])
        self.player.load_song(song)
        self.player.press(1)
        ons = self.ons()
        self.assertEqual([_tick_of(m) for m in ons], [1000 + 2 * i for i in range(6)])
        self.assertEqual([m["note"] for m in ons], sorted(m["note"] for m in ons))
        self.assertTrue(all(_tag_of(m) == kp_engine.TAG_BASE for m in ons))
        AMY.sent = []
        AMY.ticks = 1030
        self.player.press(1)                     # step 0 again: each row is re-struck
        # The same notes re-onset on their voices: no note-offs are sent.
        self.assertEqual(len(self.offs()), 0)
        self.assertEqual(len(self.ons()), 6)
        # the release of the previous press was cancelled: one cancel per part
        self.assertIn({"ticks": "0,0,%d" % kp_engine.TAG_BASE}, AMY.sent)
        AMY.sent = []
        AMY.ticks = 1060
        self.player.press(4)                     # a new chord: the old notes are released
        self.assertEqual(len(self.offs()), 6)
        self.assertLess(AMY.sent.index(self.offs()[0]), AMY.sent.index(self.ons()[0]))

    def test_autorelease_after_five_seconds(self):
        song = _song([{"tone": 0, "arpeggio": [[100]], "loop_step": 0}], tempo=120)
        self.player.load_song(song)
        self.player.press(1)
        pp = self.player.parts[0]
        self.assertEqual(pp.release_at[0], 1000 + 480)   # 5 s at 120 BPM = 480 ticks
        AMY.sent = []
        self.player._housekeep(1400)
        self.assertEqual(self.offs(), [])
        self.player._housekeep(1480)
        self.assertEqual(len(self.offs()), 1)
        self.assertIsNone(self.offs()[0]["ticks"])
        self.assertEqual(pp.sounding[0], 0)

    def test_offbeat_auto_fills_from_tapped_cycle(self):
        song = _song([{"tone": 0, "arpeggio": [[100, 90, 80, 70]], "loop_step": 3}],
                     step_per_beat=4, tempo=120)
        self.player.load_song(song)
        self.player.set_offbeat_auto(True)
        self.player.press(1)                     # no cycle yet: song tempo, 12 ticks per sub-step
        self.assertEqual([_tick_of(m) for m in self.ons()], [1000, 1012, 1024, 1036])
        self.assertEqual(self.player.parts[0].step, 3)
        AMY.sent = []
        AMY.ticks = 1064                         # tapped 64 ticks later
        self.player.press(1)
        self.assertEqual(self.player.onbeat_cycle, 64)
        self.assertEqual([_tick_of(m) for m in self.ons()], [1064, 1080, 1096, 1112])
        self.assertEqual(self.player.parts[0].step, 3)   # 4 wraps to 0, then 1, 2, 3

    def test_manual_offbeat_with_three_steps_fills_the_rest(self):
        song = _song([{"tone": 0, "arpeggio": [[100, 90, 80]], "loop_step": 2}], step_per_beat=3)
        self.player.load_song(song)
        self.player.press(1)
        AMY.sent = []
        AMY.ticks = 1020
        self.player.release(1)
        self.assertEqual([_tick_of(m) for m in self.ons()], [1020, 1040])
        self.assertEqual(self.player.beat_index, 2)

    def test_swing_offsets(self):
        self.assertEqual(kp_engine.substep_offsets(2, 48, 0), [0, 24])
        self.assertEqual(kp_engine.substep_offsets(2, 48, 100), [0, 32])
        self.assertEqual(kp_engine.substep_offsets(4, 48, 100), [0, 16, 24, 40])
        self.assertEqual(kp_engine.substep_offsets(3, 48, 100), [0, 16, 32])

    def test_auto_mode_runs_on_song_tempo(self):
        song = _song([{"tone": 0, "arpeggio": [[100, 80, 100, 80]], "loop_step": 3}],
                     step_per_beat=2, tempo=120)
        self.player.load_song(song)
        self.player.set_mode(kp_engine.AUTO)
        self.player.press(1)
        self.assertTrue(self.player.auto_running)
        self.assertEqual([_tick_of(m) for m in self.ons()], [1000])
        self.player._auto_clock(1012)            # horizon 1024: the off-beat at 1024
        self.assertEqual([_tick_of(m) for m in self.ons()], [1000, 1024])
        self.player.press(4)                     # lands on the next on-beat
        self.player._auto_clock(1036)            # horizon 1048
        self.assertEqual([_tick_of(m) for m in self.ons()], [1000, 1024, 1048])
        self.assertEqual(self.player.chord.name(), "F")
        self.assertEqual(self.ons()[-1]["note"], 72)   # row 0 = pitch 6 of F = C5
        self.player.stop()
        self.assertFalse(self.player.auto_running)
        self.player._auto_clock(1060)
        self.assertEqual(len(self.ons()), 3)

    def test_modifier_applies_next_beat(self):
        song = _song([{"tone": 0, "arpeggio": [[100, 100]], "loop_step": 1}], step_per_beat=2)
        self.player.load_song(song)
        self.player.press(5)
        self.assertEqual(self.player.chord.name(), "G")
        self.player.set_modifier("7")
        self.assertEqual(self.player.chord.name(), "G")
        self.player.release(5)
        self.assertEqual(self.player.chord.name(), "G7")

    def test_drums_and_transpose_and_octave(self):
        song = _song([
            {"tone": 128, "arpeggio": [[], [], [], [], [], [], [100]], "loop_step": 0},
            {"tone": 33, "voicing": "Static", "octave": -12, "arpeggio": [[], [], [], [100]], "loop_step": 0},
        ])
        song.base_key = 2                       # D
        self.player.load_song(song)
        self.player.set_key_shift(1)            # -> Eb
        self.player.press(1)
        ons = self.ons()
        self.assertEqual(len(ons), 2)
        # Notes go straight to AMY with the part's fixed synth number.
        drum = [m for m in ons if m["synth"] == kp_tones.SYNTH_BASE + 0][0]
        bass = [m for m in ons if m["synth"] == kp_tones.SYNTH_BASE + 1][0]
        self.assertEqual(drum["note"], 36)
        self.assertEqual(bass["note"] % 12, 3)  # Eb: row 3 = pitch 3 = the root
        self.assertTrue(36 <= bass["note"] <= 47)
        self.assertEqual(self.player.chord.name(), "Eb")

    def test_parts_keep_fixed_synth_numbers(self):
        song = _song([{"tone": t, "arpeggio": [[100]], "loop_step": 0} for t in (0, 128)])
        self.player.load_song(song)
        first = [pp.synth.channel for pp in self.player.parts[:2]]
        self.assertEqual(first, [kp_tones.SYNTH_BASE, kp_tones.SYNTH_BASE + 1])
        for i in range(60):                      # far more rebuilds than AMY has synths
            self.player.set_part_tone(0, 25 if i % 2 else 0)
        self.assertEqual(self.player.parts[0].synth.channel, kp_tones.SYNTH_BASE)
        self.assertLess(kp_tones.SYNTH_BASE + 5, 64)

    def test_part_enable_and_tone(self):
        song = _song([
            {"tone": 0, "arpeggio": [[100]], "loop_step": 0},
            {"tone": 25, "arpeggio": [[100]], "loop_step": 0},
        ])
        self.player.load_song(song)
        self.player.press(1)
        self.assertEqual(len(self.ons()), 2)
        self.player.set_part_enabled(1, False)
        self.assertIsNone(self.player.parts[1].synth)
        AMY.sent = []
        self.player.press(1)
        self.assertEqual(len(self.ons()), 1)
        self.player.set_part_enabled(1, True)
        self.assertEqual(self.player.parts[1].synth.patch, kp_tones.patch_for(25))
        self.player.set_part_tone(1, 33)
        self.assertEqual(self.player.parts[1].synth.patch, kp_tones.patch_for(33))
        self.player.set_part_tone(1, 128)               # becomes a drum part
        self.assertEqual(self.player.parts[1].synth.kind, "drum")
        self.assertTrue(any(song.slots[0].parts[1].arpeggio[6]))
        AMY.sent = []
        self.player.press(1)
        # the old row-0 pattern now plays drum_note[0] too, as on KANTAN
        base = kp_tones.SYNTH_BASE
        self.assertEqual(sorted(m["synth"] for m in self.ons()), [base + 0, base + 1, base + 1])
        # and the edit survives a save/load round trip
        d = song.to_dict()
        again = kp_song.Song(d)
        self.assertEqual(again.slots[0].parts[1].tone, 128)

    def test_midi_out(self):
        song = _song([{"tone": 0, "arpeggio": [[100]], "loop_step": 0}])
        self.player.load_song(song)
        self.player.set_midi_out(True)
        self.player.press(1)
        self.assertEqual(TULIP.state["midi"], [bytes((0x90, 67, 100))])
        self.player.stop()
        self.assertEqual(TULIP.state["midi"][-1], bytes((0x80, 67, 0)))

    def test_mix_gain_scales_with_parts(self):
        song = _song([{"tone": 0, "arpeggio": [[127]], "loop_step": 0},
                      {"tone": 25, "arpeggio": [[127]], "loop_step": 0},
                      {"tone": 33, "arpeggio": [[127]], "loop_step": 0},
                      {"tone": 128, "arpeggio": [[], [], [], [], [], [], [127]], "loop_step": 0}])
        self.player.load_song(song)
        self.assertAlmostEqual(self.player.mix_gain(), kp_engine.MASTER_GAIN / 2.0)
        self.player.press(1)
        self.assertTrue(all(abs(m["vel"] - kp_engine.MASTER_GAIN / 2.0) < 1e-9 for m in self.ons()))
        self.player.set_part_enabled(1, False)
        self.player.set_part_enabled(2, False)
        self.player.set_part_enabled(3, False)
        self.assertAlmostEqual(self.player.mix_gain(), kp_engine.MASTER_GAIN)

    def test_render_budget_trims_voices(self):
        song = _song([{"tone": t, "arpeggio": [[100]] * 6, "loop_step": 0} for t in (0, 25, 48, 33, 16)]
                     + [{"tone": 128, "arpeggio": [[], [], [], [], [], [], [100]], "loop_step": 0}])
        self.player.load_song(song)
        voices = [pp.synth_voices for pp in self.player.parts]
        self.assertEqual(sum(v * kp_tones.voice_cost(pp.synth_program) for v, pp in zip(voices, self.player.parts)) <= kp_tones.RENDER_BUDGET, True)
        self.assertTrue(all(v >= 1 for v in voices))
        self.assertLess(sum(voices), 30)
        # turning parts off gives the voices back
        for i in range(1, 6):
            self.player.set_part_enabled(i, False)
        self.assertEqual(self.player.parts[0].synth_voices, 6)

    def test_osc_budget_of_bundled_songs(self):
        for name in sorted(os.listdir(SONG_DIR)):
            if not name.endswith(".json"):
                continue
            s = kp_song.load(os.path.join(SONG_DIR, name))
            for si, sl in enumerate(s.slots):
                oscs = 0
                for p in sl.parts:
                    if not p.plays():
                        continue
                    oscs += 38 if p.is_drum else 7 * min(6, p.max_polyphony())
                self.assertLessEqual(oscs, 250, (name, si, oscs))


if __name__ == "__main__":
    unittest.main()
