"""CPython tests for loopstudio: the song model, the step scheduler, and a
smoke test of the BG-plane views.

Run as a script (the tulip/ directory shadows the `tulip` module name under
`-m unittest`):

    python3 tulip/tests/test_loopstudio.py

tulip, amy, synth, sequencer and lvgl are stubbed: AMY messages and BG-plane
draw calls are recorded so they can be inspected without hardware.
"""
import os
import sys
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.normpath(os.path.join(HERE, "..", "fs", "tulip", "ex", "loopstudio"))
sys.path.insert(0, APP)

W, H = 1280, 720


# --- stubs -------------------------------------------------------------------

class _Amy(types.ModuleType):
    AMY_SEQUENCER_PPQ = 48

    def __init__(self):
        super().__init__("amy")
        self.sent = []
        self.fx = []
        self.ticks = 1000
        self.instrument_generation = 0   # bumped by an AMY reset

    def send(self, **kw):
        self.sent.append(kw)

    def sequencer_ticks(self):
        return self.ticks

    def reverb(self, level=-1, **kw):
        self.fx.append(("reverb", level))

    def chorus(self, level=-1, **kw):
        self.fx.append(("chorus", level))

    def echo(self, level=None, **kw):
        self.fx.append(("echo", level))


class _Synth:
    made = []

    def __init__(self, kind, num_voices, channel, patch):
        self.kind, self.num_voices, self.synth, self.patch = kind, num_voices, channel, patch
        self.inited = False
        self.released = False
        self.offs = 0
        self.rebuilt = 0
        _Synth.made.append(self)

    def deferred_init(self):
        self.inited = True

    def _rebuild_after_amy_reset(self):
        self.rebuilt += 1

    def all_notes_off(self):
        self.offs += 1

    def release(self):
        self.released = True


amy = _Amy()
synth = types.ModuleType("synth")
synth.PatchSynth = lambda num_voices=4, channel=None, patch=None, **kw: _Synth("patch", num_voices, channel, patch)
synth.PatchSynth.reset = lambda: None
synth.DrumSynth = lambda num_voices=1, channel=None, patch=384: _Synth("drum", num_voices, channel, patch)

tulip = types.ModuleType("tulip")
tulip.state = dict(bpm=120, touch=[-1] * 6, draws=[], clock=[], user=tempfile.mkdtemp() + "/")
os.mkdir(tulip.state["user"] + "user")


def _seq_bpm(bpm=None):
    if bpm is not None:
        tulip.state["bpm"] = bpm
    return tulip.state["bpm"]


def _draw(kind):
    def f(*a):
        tulip.state["draws"].append((kind,) + a)
    return f


tulip.seq_bpm = _seq_bpm
tulip.screen_size = lambda: (W, H)
tulip.root_dir = lambda: tulip.state["user"]
tulip.amy_render_load = lambda: 0.3
tulip.touch = lambda: tuple(tulip.state["touch"])
tulip.touch_callback = lambda cb=None: tulip.state.__setitem__("touch_cb", cb)
tulip.keyboard_callback = lambda cb=None: tulip.state.__setitem__("key_cb", cb)
tulip.frame_callback = lambda cb=None, arg=None: tulip.state.__setitem__("frame_cb", cb)
tulip.bg_clear = _draw("clear")
tulip.bg_rect = _draw("rect")
tulip.bg_roundrect = _draw("roundrect")
tulip.bg_line = _draw("line")
tulip.bg_str = _draw("str")

sequencer = types.ModuleType("sequencer")


class _TulipSequence:
    def __init__(self, divider, func):
        self.divider, self.func, self.cleared = divider, func, False
        tulip.state["clock"].append(self)

    def clear(self):
        self.cleared = True


sequencer.TulipSequence = _TulipSequence

lv = types.ModuleType("lvgl")
for name, mod in (("amy", amy), ("synth", synth), ("tulip", tulip), ("sequencer", sequencer), ("lvgl", lv)):
    sys.modules[name] = mod

import ls_model      # noqa: E402
import ls_engine     # noqa: E402
import ls_views      # noqa: E402
import loopstudio    # noqa: E402


def notes_sent():
    return [m for m in amy.sent if "note" in m]


def tick_of(m):
    return int(m["ticks"].split(",")[0])


# --- the model ---------------------------------------------------------------

class PatternTest(unittest.TestCase):
    def test_toggle_step(self):
        p = ls_model.Pattern()
        self.assertTrue(p.toggle_step(0, 4, 36))
        self.assertEqual(p.notes[0], [[4, 1, 36, 100]])
        self.assertFalse(p.toggle_step(0, 4, 36))
        self.assertEqual(p.notes[0], [])

    def test_toggle_clears_roll_notes_on_the_step(self):
        p = ls_model.Pattern()
        p.add(4, 0, 60, 4)
        p.add(4, 0, 64, 4)
        self.assertFalse(p.toggle_step(4, 0, 48))
        self.assertEqual(p.notes[4], [])

    def test_add_replaces_overlap_on_same_pitch_only(self):
        p = ls_model.Pattern()
        p.add(0, 0, 60, 4)
        p.add(0, 2, 62, 4)
        p.add(0, 3, 60, 2)
        self.assertEqual(sorted(p.notes[0]), [[2, 4, 62, 100], [3, 2, 60, 100]])

    def test_add_clips_to_the_pattern(self):
        p = ls_model.Pattern()
        self.assertEqual(p.add(0, 14, 60, 8)[1], 2)
        self.assertIsNone(p.add(0, 16, 60))
        self.assertEqual(p.add(0, 0, 200)[2], ls_model.NOTE_MAX)

    def test_find_covers_the_whole_note(self):
        p = ls_model.Pattern()
        n = p.add(0, 2, 60, 3)
        self.assertIs(p.find(0, 4, 60), n)
        self.assertIsNone(p.find(0, 5, 60))
        self.assertIsNone(p.find(0, 3, 61))

    def test_resize_stops_at_the_next_note(self):
        p = ls_model.Pattern()
        n = p.add(0, 0, 60, 1)
        p.add(0, 6, 60, 1)
        p.resize(0, n, 12)
        self.assertEqual(n[1], 6)
        p.resize(0, n, 0)
        self.assertEqual(n[1], 1)

    def test_events_index_follows_edits(self):
        p = ls_model.Pattern()
        p.add(0, 0, 36)
        self.assertEqual(len(p.events_at(0)), 1)
        p.add(1, 0, 38)
        self.assertEqual([ch for ch, _ in p.events_at(0)], [0, 1])
        p.clear_channel(0)
        self.assertEqual([ch for ch, _ in p.events_at(0)], [1])

    def test_shortening_trims(self):
        p = ls_model.Pattern(bars=2)
        p.add(0, 14, 60, 8)
        p.add(0, 20, 62, 2)
        p.set_bars(1)
        self.assertEqual(p.notes[0], [[14, 2, 60, 100]])


class SongTest(unittest.TestCase):
    def test_clips_of_a_two_bar_pattern(self):
        s = ls_model.Song()
        s.patterns[1].set_bars(2)
        self.assertTrue(s.toggle_clip(1, 4))
        self.assertEqual(s.clip_at(1, 5), 4)
        self.assertIsNone(s.clip_at(1, 6))
        self.assertTrue(s.toggle_clip(1, 3))            # overlaps: replaces the clip at 4
        self.assertEqual(s.clips[1], [3])
        self.assertFalse(s.toggle_clip(1, 4))           # tap its second bar: removed
        self.assertEqual(s.clips[1], [])
        self.assertFalse(s.toggle_clip(1, ls_model.SONG_BARS - 1))     # would run off the end
        self.assertEqual(s.song_bars(), 0)

    def test_song_events(self):
        s = ls_model.Song()
        s.patterns[1].set_bars(2)
        s.patterns[0].add(0, 0, 36)
        s.patterns[1].add(4, 17, 40)
        s.toggle_clip(0, 0)
        s.toggle_clip(0, 3)
        s.toggle_clip(1, 2)
        self.assertEqual(s.song_bars(), 4)
        self.assertEqual(len(s.events_at_song_step(0)), 1)
        self.assertEqual(s.events_at_song_step(16), [])
        self.assertEqual(s.events_at_song_step(3 * 16 + 1), [(4, [17, 1, 40, 100])])
        self.assertEqual([ch for ch, _ in s.events_at_song_step(3 * 16)], [0])

    def test_fix_clips_after_lengthening(self):
        s = ls_model.Song()
        for b in (0, 1, 2, 31):
            s.toggle_clip(0, b)
        s.patterns[0].set_bars(2)
        s.fix_clips(0)
        self.assertEqual(s.clips[0], [0, 2])

    def test_audible(self):
        s = ls_model.Song()
        s.channels[1].mute = True
        self.assertEqual(s.audible()[:3], [True, False, True])
        s.channels[1].solo = True                       # solo wins over its own mute
        self.assertEqual(s.audible()[:3], [False, True, False])

    def test_drum_sound_change_moves_the_steps(self):
        s = ls_model.Song()
        s.patterns[2].add(0, 0, 36)
        s.change_sound(0, 1)
        self.assertEqual(s.channels[0].root, 35)
        self.assertEqual(s.patterns[2].notes[0][0][2], 35)
        self.assertEqual(s.channels[0].name, "Kick 2")

    def test_round_trip(self):
        s = ls_model.demo()
        s.channels[2].pan = 0.25
        s.reverb = 0.3
        path = os.path.join(tempfile.mkdtemp(), "x.json")
        ls_model.save(s, path)
        t = ls_model.load(path)
        self.assertEqual(t.to_dict(), s.to_dict())
        self.assertGreater(t.song_bars(), 0)

    def test_load_survives_junk(self):
        t = ls_model.Song.from_dict({"bpm": 999, "channels": [{"kind": "drum", "patch": 36, "root": 500}],
                                     "patterns": [{"bars": 9, "notes": [[[99, 1, 60], [0, 1, 60, 300]]]}],
                                     "clips": [[0, 0, 77, 1]]})
        self.assertEqual(t.bpm, 240)
        self.assertEqual(len(t.channels), ls_model.NUM_CHANNELS)
        self.assertEqual(t.patterns[0].bars, 2)
        self.assertEqual(t.patterns[0].notes[0], [[0, 1, 60, 127]])
        self.assertEqual(t.clips[0], [0])


# --- the engine --------------------------------------------------------------

class EngineTest(unittest.TestCase):
    def setUp(self):
        amy.sent.clear()
        amy.fx.clear()
        amy.ticks = 1000
        _Synth.made.clear()
        self.song = ls_model.Song()
        self.engine = ls_engine.Engine(self.song)

    def tearDown(self):
        self.engine.close()

    def run_clock(self, steps):
        for _ in range(steps):
            amy.ticks += ls_engine.STEP_TICKS
            self.engine._on_clock(amy.ticks)

    def test_synths_use_fixed_numbers_and_are_built(self):
        numbers = sorted(s.synth for s in _Synth.made)
        self.assertEqual(numbers, [48, 53, 54, 55, 56])
        self.assertTrue(all(s.inited for s in _Synth.made))
        self.assertTrue(all(n < 64 for n in numbers))

    def test_only_a_changed_synth_is_rebuilt(self):
        made = len(_Synth.made)
        self.engine.apply_sounds()
        self.assertEqual(len(_Synth.made), made)
        old = self.engine.synths[4]
        self.song.change_sound(4, 1)
        self.engine.apply_sounds()
        self.assertEqual(len(_Synth.made), made + 1)
        self.assertTrue(old.released)
        self.assertEqual(self.engine.synths[4].synth, 53)

    def test_pattern_loops_on_the_grid(self):
        p = self.song.patterns[0]
        p.add(0, 0, 36)
        p.add(0, 4, 36)
        self.engine.play()
        self.run_clock(40)
        ticks = [tick_of(m) for m in notes_sent()]
        self.assertEqual(ticks, sorted(ticks))
        self.assertGreaterEqual(len(ticks), 5)
        gaps = [b - a for a, b in zip(ticks, ticks[1:])]
        self.assertEqual(set(gaps), {4 * 12, 12 * 12})
        self.assertTrue(all(m["ticks"].endswith(",0,%d" % ls_engine.TAG) for m in notes_sent()))

    def test_nothing_is_queued_in_the_past(self):
        self.song.patterns[0].add(0, 0, 36)
        self.engine.play()
        self.assertGreater(tick_of(notes_sent()[0]), amy.ticks)

    def test_synth_notes_get_offs_and_drums_do_not(self):
        p = self.song.patterns[0]
        p.add(0, 0, 36)
        p.add(4, 0, 40, 4)
        self.engine.play()
        drum = [m for m in notes_sent() if m["synth"] == 48]
        bass = [m for m in notes_sent() if m["synth"] == 53]
        self.assertEqual(len(drum), 1)
        self.assertEqual([m["vel"] > 0 for m in bass], [True, False])
        self.assertEqual(tick_of(bass[1]) - tick_of(bass[0]), 4 * 12 - 1)

    def test_swing_delays_the_off_sixteenths(self):
        self.song.swing = 50
        p = self.song.patterns[0]
        p.add(0, 0, 36)
        p.add(0, 1, 36)
        p.add(0, 2, 36)
        self.engine.play()
        self.run_clock(4)
        t = [tick_of(m) for m in notes_sent()]
        self.assertEqual([t[1] - t[0], t[2] - t[0]], [12 + 3, 24])

    def test_mute_solo_volume_pan(self):
        p = self.song.patterns[0]
        p.add(0, 0, 36)
        p.add(1, 0, 38)
        self.song.channels[0].mute = True
        self.song.channels[1].pan = 0.2
        self.engine.play()
        sent = notes_sent()
        self.assertEqual([m["note"] for m in sent], [38])
        self.assertEqual(sent[0]["pan"], 0.2)
        self.assertLessEqual(sent[0]["vel"], ls_engine.MASTER_GAIN)
        self.assertGreater(sent[0]["vel"], 0)

    def test_stop_cancels_the_tag_and_silences(self):
        self.song.patterns[0].add(4, 0, 40, 8)
        self.engine.play()
        self.engine.stop()
        self.assertIn({"ticks": "0,0,%d" % ls_engine.TAG}, amy.sent)
        # All-notes-off is vel=0 with no note; note=0 leaves AMY's voices ringing.
        self.assertIn({"synth": 53, "vel": 0}, amy.sent)
        self.assertFalse([m for m in amy.sent if m.get("note") == 0])
        self.assertEqual(self.engine.synths[4].offs, 0)
        amy.sent.clear()
        self.run_clock(8)
        self.assertEqual(amy.sent, [])

    def test_another_apps_amy_reset_is_recovered(self):
        # kanplay's start calls synth.PatchSynth.reset() while we play on.
        self.song.reverb = 0.3
        self.engine.apply_fx()
        self.song.patterns[0].add(0, 0, 36)
        self.engine.play()
        amy.fx.clear()
        amy.sent.clear()
        amy.instrument_generation += 1
        self.run_clock(ls_model.STEPS_PER_BAR)
        alive = [s for s in _Synth.made if not s.released]
        self.assertTrue(alive)
        # Rebuilt once, on the first clock after the reset, not every step.
        self.assertEqual([s.rebuilt for s in alive], [1] * len(alive))
        self.assertIn(("reverb", 0.3), amy.fx)
        self.assertTrue([m for m in amy.sent if m.get("note") == 36 and m.get("vel")])

    def test_song_mode_plays_the_arrangement(self):
        self.song.patterns[0].add(0, 0, 36)
        self.song.patterns[1].add(1, 0, 38)
        self.song.toggle_clip(0, 0)
        self.song.toggle_clip(1, 1)
        self.engine.set_mode(ls_engine.SONG)
        self.engine.play()
        self.run_clock(3 * 16)
        self.assertEqual([m["note"] for m in notes_sent()][:4], [36, 38, 36, 38])
        t = [tick_of(m) for m in notes_sent()]
        self.assertEqual(t[1] - t[0], 16 * 12)

    def test_position_tracks_the_audible_step(self):
        self.engine.play()
        start = amy.ticks
        self.assertIn(self.engine.position(), (0, 15))      # queued just ahead of now
        self.run_clock(5)
        self.assertEqual(self.engine.position(), (amy.ticks - start - 2) // 12 % 16)
        self.run_clock(16)
        self.assertEqual(self.engine.position(), (amy.ticks - start - 2) // 12 % 16)

    def test_a_stall_skips_rather_than_bursts(self):
        p = self.song.patterns[0]
        for s in range(16):
            p.add(0, s, 36)
        self.engine.play()
        self.run_clock(2)
        amy.sent.clear()
        amy.ticks += 10 * 12                                  # the main thread hung
        self.engine._on_clock(amy.ticks)
        # At most the step just missed (better late than dropped) and the
        # ones inside the horizon -- not the ten that went by.
        self.assertLessEqual(len(notes_sent()), 3)
        self.assertTrue(all(tick_of(m) > amy.ticks - 2 * ls_engine.STEP_TICKS for m in notes_sent()))

    def test_fx_sent_only_when_changed(self):
        self.assertEqual(amy.fx, [])
        self.song.reverb = 0.4
        self.engine.apply_fx()
        self.engine.apply_fx()
        self.assertEqual(amy.fx, [("reverb", 0.4)])
        self.engine.close()
        self.assertIn(("reverb", 0), amy.fx)

    def test_audition(self):
        self.engine.audition(4, 50)
        on, off = notes_sent()
        self.assertIsNone(on["ticks"])
        self.assertEqual(off["vel"], 0)
        self.assertGreater(tick_of(off), amy.ticks)


# --- the views ---------------------------------------------------------------

class AppTest(unittest.TestCase):
    def setUp(self):
        amy.sent.clear()
        tulip.state["draws"].clear()
        screen = types.SimpleNamespace()
        self.app = loopstudio.LoopStudio(screen)
        self.app.activate()

    def tearDown(self):
        self.app.deactivate()
        self.app.quit()

    def tap(self, x, y):
        self.drag([(x, y)])

    def drag(self, points):
        for x, y in points:
            tulip.state["touch"][0:2] = [x, y]
            self.app._touch(0)
        self.app._touch(1)

    def assert_draws_on_screen(self):
        for d in tulip.state["draws"]:
            if d[0] in ("rect", "roundrect"):
                x, y, w, h = d[1:5]
                self.assertTrue(0 <= x and 0 <= y and w > 0 and h > 0 and x + w <= W and y + h <= H, d)
            elif d[0] == "line":
                self.assertTrue(all(0 <= v for v in d[1:5]) and d[1] < W and d[3] < W and d[2] < H and d[4] < H, d)
            elif d[0] == "str":
                self.assertTrue(0 <= d[2] < W and 0 <= d[3] < H, d)
                self.assertIsInstance(d[1], str)

    def test_every_view_draws_inside_the_screen(self):
        for bars in (1, 2):
            self.app.song.patterns[self.app.engine.pattern_index].set_bars(bars)
            for v in self.app.views:
                self.app.show(v)
                self.assertTrue(tulip.state["draws"])
        self.assert_draws_on_screen()

    def test_the_top_bar_clears_the_task_bar(self):
        self.assertLessEqual(max(b.x + b.w for b in self.app.top), W - loopstudio.TASK_BAR_W)

    def test_rack_tap_and_drag_paint(self):
        rack = self.app.views[0]
        p = self.app.song.patterns[0]
        p.clear()
        cw = rack._cell_w()
        y = rack._row_y(1) + 20
        self.tap(rack.gx + 2 * cw + 5, y)
        self.assertEqual([n[0] for n in p.notes[1]], [2])
        self.drag([(rack.gx + s * cw + 5, y) for s in (4, 5, 6)])
        self.assertEqual(sorted(n[0] for n in p.notes[1]), [2, 4, 5, 6])
        self.drag([(rack.gx + s * cw + 5, y) for s in (6, 5, 3)])      # starts on a lit step: erases
        self.assertEqual(sorted(n[0] for n in p.notes[1]), [2, 4])
        self.assertEqual(self.app.channel, 1)
        self.assert_draws_on_screen()

    def test_rack_row_matches_its_steps(self):
        # draw_row colours a row from one pass over its notes; draw_step asks
        # the pattern about one step. They must agree.
        rack = self.app.views[0]
        self.app.select_pattern(1)
        p = self.app.song.patterns[1]
        p.add(4, 5, 72)                     # off-root only
        p.add(4, 9, 72)
        p.add(4, 9, self.app.song.channels[4].root)     # root after an off-root note
        draws = tulip.state["draws"]
        for row in range(ls_model.NUM_CHANNELS):
            draws.clear()
            rack.draw_row(row)
            by_row = [d for d in draws if d[0] == "roundrect" and d[1] >= rack.gx]
            draws.clear()
            for s_ in range(p.steps):
                rack.draw_step(row, s_)
            self.assertEqual(by_row, list(draws))

    def test_rack_mute_and_sound(self):
        rack = self.app.views[0]
        y = rack._row_y(5) + 20
        self.tap(rack.x + 10, y)
        self.assertTrue(self.app.song.channels[5].mute)
        self.tap(rack.x + 60, y)
        self.assertTrue(self.app.song.channels[5].solo)
        before = self.app.song.channels[5].patch
        self.tap(rack.x + 340, y)
        self.assertNotEqual(self.app.song.channels[5].patch, before)
        self.assertEqual(self.app.engine.synths[5].patch, self.app.song.channels[5].patch)

    def test_roll_add_resize_delete(self):
        roll = self.app.views[1]
        self.app.select_channel(5)
        self.app.select_pattern(7)
        self.app.show(roll)
        p = self.app.song.patterns[7]
        cw = roll._cell_w()
        low = roll._low()
        y = roll.gy + (roll.ROWS - 1 - 3) * roll.row_h + 5          # low + 3
        self.drag([(roll.gx + 1 * cw + 3, y), (roll.gx + 5 * cw + 3, y)])
        self.assertEqual(p.notes[5], [[1, 5, low + 3, 100]])
        self.assertEqual(roll.length, 5)
        self.tap(roll.gx + 3 * cw, y)                                 # inside the note: delete
        self.assertEqual(p.notes[5], [])
        self.assert_draws_on_screen()

    def test_roll_scroll_is_clamped(self):
        roll = self.app.views[1]
        self.app.show(roll)
        for _ in range(12):
            roll._scroll(12)
        self.assertEqual(roll._low() + roll.ROWS - 1, ls_model.NOTE_MAX)
        for _ in range(12):
            roll._scroll(-12)
        self.assertEqual(roll._low(), ls_model.NOTE_MIN)
        self.assert_draws_on_screen()

    def test_playlist_tap(self):
        pl = self.app.views[2]
        self.app.show(pl)
        self.app.song.clips = [[] for _ in range(8)]
        y = pl.gy + 2 * pl.row_h + 10
        self.tap(pl.gx + 3 * pl.cw + 4, y)
        self.assertEqual(self.app.song.clips[2], [3])
        self.tap(pl.x + 10, y)
        self.assertEqual(self.app.engine.pattern_index, 2)
        pl._next_page()
        self.tap(pl.gx + 4, y)
        self.assertEqual(self.app.song.clips[2], [3, 16])
        self.assert_draws_on_screen()

    def test_mixer_faders(self):
        mx = self.app.views[3]
        self.app.show(mx)
        x = mx.x + 2 * mx.sw + mx.sw // 2
        self.drag([(x, mx.fy + 100), (x, mx.fy - 50)])
        self.assertEqual(self.app.song.channels[2].volume, 1.0)
        self.drag([(x, mx.fy + mx.fh), (x, H - 1)])
        self.assertEqual(self.app.song.channels[2].volume, 0.0)
        self.drag([(x, mx.pan_y + 10), (mx.x + 2 * mx.sw, mx.pan_y + 10)])
        self.assertEqual(self.app.song.channels[2].pan, 0.0)
        rx = mx.x + 9 * mx.sw + mx.sw // 2                            # reverb strip
        self.drag([(rx, mx.fy + mx.fh // 2)])
        self.assertAlmostEqual(self.app.song.reverb, 0.5, delta=0.05)
        self.assertTrue(any(f[0] == "reverb" for f in amy.fx))
        self.assert_draws_on_screen()

    def test_transport_and_playhead(self):
        play = self.app.play_button
        self.tap(play.x + 5, play.y + 5)
        self.assertTrue(self.app.engine.playing)
        for _ in range(40):
            amy.ticks += 3
            self.app.frame(None)
        self.assertGreaterEqual(self.app.view.head, 0)
        self.app._key(32)
        self.assertFalse(self.app.engine.playing)
        self.app.frame(None)
        self.assertEqual(self.app.view.head, -1)
        self.assert_draws_on_screen()

    def test_switching_apps_keeps_playing(self):
        self.app.song.patterns[0].add(0, 0, 36)
        self.app.toggle_play()
        self.app.deactivate()
        self.assertTrue(self.app.engine.playing)
        self.assertIsNone(tulip.state["frame_cb"])
        amy.sent.clear()
        for _ in range(ls_model.STEPS_PER_BAR):
            amy.ticks += ls_engine.STEP_TICKS
            self.app.engine._on_clock(amy.ticks)
        self.assertTrue([m for m in amy.sent if m.get("note") == 36 and m.get("vel")])
        self.assertNotIn({"ticks": "0,0,%d" % ls_engine.TAG}, amy.sent)
        tulip.state["draws"].clear()
        self.app.activate()
        self.app.frame(None)
        self.assertTrue(self.app.engine.playing)
        self.assertGreaterEqual(self.app.view.head, 0)
        self.assert_draws_on_screen()
        self.app.quit()
        self.assertFalse(self.app.engine.playing)

    def test_song_mode_with_an_empty_playlist_does_not_start(self):
        self.app.song.clips = [[] for _ in range(8)]
        self.app.toggle_mode()
        self.app.toggle_play()
        self.assertFalse(self.app.engine.playing)
        self.assertIn("empty", self.app.status)

    def test_bpm_repeat_while_held(self):
        minus = self.app.top[2]
        bpm = self.app.song.bpm
        tulip.state["touch"][0:2] = [minus.x + 5, minus.y + 5]
        self.app._touch(0)
        for _ in range(loopstudio.REPEAT_DELAY + 3 * loopstudio.REPEAT_EVERY + 1):
            self.app.frame(None)
        self.app._touch(1)
        self.assertEqual(self.app.song.bpm, bpm - 5)
        self.assertEqual(tulip.state["bpm"], bpm - 5)

    def test_save_new_load(self):
        self.app.show(self.app.views[4])
        self.app.song.patterns[5].add(3, 7, 39)
        self.app.save_song_as_new()
        name = self.app.song.name
        self.assertEqual(self.app.list_songs(), [name])
        self.app.new_song()
        self.assertNotEqual(self.app.song.name, name)
        self.assertTrue(self.app.song.patterns[5].is_empty())
        fv = self.app.views[4]
        self.tap(fv.x + 20, fv.y + fv.LIST_Y + 10)
        self.assertEqual(self.app.song.name, name)
        self.assertEqual(self.app.song.patterns[5].notes[3], [[7, 1, 39, 100]])
        self.assertIs(self.app.engine.song, self.app.song)
        self.assert_draws_on_screen()


if __name__ == "__main__":
    unittest.main()
