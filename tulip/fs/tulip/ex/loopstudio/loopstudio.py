"""loopstudio -- a pattern-based music workstation for Tulip, in the manner of
FL Studio Mobile: a channel rack of step sequencers, a piano roll, a playlist
that arranges patterns into a song, and a mixer.

    tulip.run("loopstudio")

RACK   tap steps; M / S mute and solo; < > change the sound; tap a name to
       select the channel the piano roll edits.
ROLL   tap to add a note (drag right for its length), tap a note to delete.
SONG   tap a cell to place a pattern at a bar; SONG mode plays the arrangement.
MIXER  volume, pan, mute, solo per channel; master, reverb, chorus, echo.
FILE   new / demo / save / load (/user/loopstudio/*.json).

Space on a keyboard is play/stop. See README.md.
"""
import os

import lvgl as lv
import tulip

import ls_engine
import ls_model
import ls_views
from ls_views import Button, box, text, DOWN, MOVE, UP

USER_DIR = tulip.root_dir() + "user/loopstudio"
TOP_Y, TOP_H = 6, 52
STATUS_Y, STATUS_H = 60, 22
VIEW_Y = 84
MARGIN = 8
TASK_BAR_W = 132            # the quit / switch buttons LVGL keeps at the top right
REPEAT_DELAY, REPEAT_EVERY = 18, 3          # frames (~45 a second on the Tab5)

app = None


class FileView(ls_views.View):
    name = "FILE"
    ROW_H = 64
    LIST_Y = 72

    def __init__(self, app, x, y, w, h):
        super().__init__(app, x, y, w, h)
        self.files = []
        bx = x
        for label, action, color in (("New", app.new_song, ls_views.PANEL_HI),
                                     ("Demo", app.demo_song, ls_views.PANEL_HI),
                                     ("Save", app.save_song, ls_views.GREEN),
                                     ("Save as new", app.save_song_as_new, ls_views.GREEN)):
            fg = ls_views.BLACK if color == ls_views.GREEN else ls_views.WHITE
            self.buttons.append(Button(bx, y, 180, 56, label, action, color=color, fg=fg))
            bx += 190

    def draw_content(self):
        self.clear(self.x, self.y + self.LIST_Y - 8, self.w, self.h - self.LIST_Y + 8)
        self.files = self.app.list_songs()
        rows = (self.h - self.LIST_Y) // self.ROW_H
        if not self.files:
            text("no saved songs yet", self.x, self.y + self.LIST_Y, 400, self.ROW_H, ls_views.GREY)
        for i, name in enumerate(self.files[:rows * 2]):
            x = self.x + (i // rows) * 420
            y = self.y + self.LIST_Y + (i % rows) * self.ROW_H
            current = name == self.song.name
            ls_views.rbox(x, y + 3, 400, self.ROW_H - 6, ls_views.ACCENT if current else ls_views.PANEL)
            text(name, x, y + 3, 400, self.ROW_H - 6, ls_views.BLACK if current else ls_views.WHITE)

    def touch_content(self, phase, x, y):
        if phase != DOWN or y < self.y + self.LIST_Y:
            return
        rows = (self.h - self.LIST_Y) // self.ROW_H
        col, row = (x - self.x) // 420, (y - self.y - self.LIST_Y) // self.ROW_H
        i = col * rows + row
        if 0 <= col < 2 and 0 <= row < rows and i < len(self.files):
            self.app.load_song(self.files[i])


class LoopStudio:
    def __init__(self, screen):
        self.screen = screen
        # Start from a clean AMY, as kanplay does: after an overload failsafe
        # AMY stays mute until it is reset.
        import synth
        synth.PatchSynth.reset()
        self.song = ls_model.demo()
        self.engine = ls_engine.Engine(self.song)
        self.channel = 4
        self.closed = False
        self.active = False
        self.touching = False
        self.held = None            # [button, frames held] for auto-repeat
        self.frames = 0
        self.status = ""
        self.shown_status = None
        self.shown_playing = None
        W, H = tulip.screen_size()
        self.W, self.H = W, H
        area = (MARGIN, VIEW_Y, W - 2 * MARGIN, H - VIEW_Y - 4)
        self.views = [ls_views.RackView(self, *area), ls_views.RollView(self, *area),
                      ls_views.PlaylistView(self, *area), ls_views.MixerView(self, *area),
                      FileView(self, *area)]
        self.view = self.views[0]
        self._build_top_bar()

    # --- top bar -----------------------------------------------------------

    def _build_top_bar(self):
        e = self.engine
        self.top = []
        self.bx = MARGIN

        def add(w, label, action, gap=4, **kw):
            b = Button(self.bx, TOP_Y, w, TOP_H, label, action, **kw)
            self.top.append(b)
            self.bx += w + gap
            return b
        self.play_button = add(96, lambda: "STOP" if e.playing else "PLAY", self.toggle_play,
                               color=lambda: ls_views.RED if e.playing else ls_views.GREEN,
                               fg=ls_views.BLACK)
        self.mode_button = add(84, lambda: "SONG" if e.mode == ls_engine.SONG else "PAT", self.toggle_mode,
                               gap=14, color=lambda: ls_views.ACCENT if e.mode == ls_engine.SONG else ls_views.PANEL_HI)
        add(44, "-", lambda: self.change_bpm(-1)).repeat = True
        self.bpm_button = add(96, lambda: "%d BPM" % self.song.bpm, lambda: None, color=ls_views.PANEL)
        add(44, "+", lambda: self.change_bpm(1), gap=14).repeat = True
        self.swing_button = add(96, lambda: "Swing %d" % self.song.swing, self.next_swing, gap=14)
        add(44, "<", lambda: self.select_pattern(e.pattern_index - 1))
        self.pattern_button = add(56, lambda: "P%d" % (e.pattern_index + 1), lambda: None, color=ls_views.ACCENT,
                                  fg=ls_views.BLACK)
        add(44, ">", lambda: self.select_pattern(e.pattern_index + 1))
        self.bars_button = add(80, lambda: "%d bar" % self.song.patterns[e.pattern_index].bars,
                               self.toggle_bars, gap=14)
        self.tabs = []
        for v in self.views:
            self.tabs.append(add(70, v.name, (lambda v: lambda: self.show(v))(v), font=ls_views.FONT_S,
                                 color=(lambda v: lambda: ls_views.ACCENT if self.view is v else ls_views.PANEL)(v)))

    def draw_all(self):
        # A view change repaints everything from one bg_clear (55 ms on the
        # Tab5): simpler than clearing per view, and no dearer.
        tulip.bg_clear(ls_views.BG)
        for b in self.top:
            b.draw(False)
        self.shown_status = None
        self.draw_status()
        self.view.draw()

    def draw_status(self):
        load = tulip.amy_render_load()
        line = "%s   %s" % (self.song.name, self.status)
        right = "AMY load %.2f" % (round(load * 20) / 20)
        if load > 0.9:
            right += "  TOO HIGH: mute a channel"
        if (line, right) == self.shown_status:
            return
        self.shown_status = (line, right)
        box(0, STATUS_Y, self.W, STATUS_H, ls_views.BG)
        tulip.bg_str(line, MARGIN + 4, STATUS_Y + 16, ls_views.GREY, ls_views.FONT_S)
        text(right, self.W - 420, STATUS_Y, 412, STATUS_H, ls_views.RED if load > 0.9 else ls_views.GREY,
             ls_views.FONT_S)

    def say(self, status):
        self.status = status
        if self.active:
            self.draw_status()

    # --- actions -----------------------------------------------------------

    def show(self, view):
        if view is self.view:
            return
        self.view = view
        self.draw_all()

    def toggle_play(self):
        if self.engine.mode == ls_engine.SONG and not self.engine.playing and not self.song.song_bars():
            self.say("the playlist is empty: place patterns in SONG, or play in PAT mode")
            return
        self.engine.toggle()

    def toggle_mode(self):
        e = self.engine
        e.set_mode(ls_engine.PATTERN if e.mode == ls_engine.SONG else ls_engine.SONG)
        self.play_button.draw()
        if isinstance(self.view, ls_views.PlaylistView):
            self.view.draw_hint()

    def change_bpm(self, step):
        self.engine.set_bpm(self.song.bpm + step)
        self.bpm_button.draw()

    def next_swing(self):
        self.song.swing = (self.song.swing // 15 * 15 + 15) % 75

    def select_pattern(self, index):
        self.engine.set_pattern(index)
        self.pattern_button.draw()
        self.bars_button.draw()
        if isinstance(self.view, ls_views.PlaylistView):
            self.view.pattern_changed()
        else:
            self.draw_all()

    def toggle_bars(self):
        p = self.engine.pattern_index
        pat = self.song.patterns[p]
        pat.set_bars(1 if pat.bars >= ls_model.MAX_PATTERN_BARS else pat.bars + 1)
        self.song.fix_clips(p)
        self.draw_all()

    def select_channel(self, ch):
        if ch == self.channel:
            return
        old, self.channel = self.channel, ch
        changed = getattr(self.view, "channel_changed", None)
        if changed:
            changed(old, ch)

    # --- files -------------------------------------------------------------

    def list_songs(self):
        try:
            return sorted(n[:-5] for n in os.listdir(USER_DIR) if n.endswith(".json"))
        except OSError:
            return []

    def _set_song(self, song, status):
        self.song = song
        self.engine.set_song(song)
        for v in self.views:
            if isinstance(v, ls_views.RollView):
                v.low = [None] * ls_model.NUM_CHANNELS
        self.status = status
        if self.active:
            self.draw_all()

    def new_song(self):
        names = self.list_songs()
        n = 1
        while "song%d" % n in names:
            n += 1
        self._set_song(ls_model.Song("song%d" % n), "new song")

    def demo_song(self):
        self._set_song(ls_model.demo(), "demo loaded")

    def load_song(self, name):
        try:
            song = ls_model.load("%s/%s.json" % (USER_DIR, name))
            song.name = name
        except Exception as e:
            self.say("cannot load %s: %s" % (name, e))
            return
        self._set_song(song, "loaded")

    def save_song(self):
        try:
            os.mkdir(USER_DIR)
        except OSError:
            pass
        try:
            ls_model.save(self.song, "%s/%s.json" % (USER_DIR, self.song.name))
            self.say("saved")
        except OSError as e:
            self.say("save failed: %s" % e)
        if isinstance(self.view, FileView):
            self.view.draw_content()

    def save_song_as_new(self):
        names = self.list_songs()
        n = 1
        while "song%d" % n in names:
            n += 1
        self.song.name = "song%d" % n
        self.save_song()

    # --- input -------------------------------------------------------------

    def _touch(self, up):
        # The callback says 0 for a press and for every move while held, 1 for
        # the release; the release leaves point 0 at its last position.
        if self.closed or not self.active:
            return
        pts = tulip.touch()
        x, y = pts[0], pts[1]
        if up:
            if self.touching:
                self.touching = False
                self.held = None
                self.view.touch(UP, x, y)
            return
        phase = MOVE if self.touching else DOWN
        self.touching = True
        if phase == DOWN and y < VIEW_Y:
            if x < self.W - TASK_BAR_W:
                for b in self.top:
                    if b.hit(x, y):
                        b.action()
                        b.draw()
                        if getattr(b, "repeat", False):
                            self.held = [b, 0]
                        break
            return
        if y >= VIEW_Y or phase == MOVE:
            try:
                self.view.touch(phase, x, max(y, VIEW_Y))
            except Exception as e:          # never let a slip in a view kill the touch callback
                print("loopstudio:", e)
                self.say("error: %s" % e)

    def _key(self, c):
        if c == 32:
            self.toggle_play()

    def frame(self, arg):
        # A frame callback can still be queued after deactivate() cleared it.
        if self.closed or not self.active:
            return
        self.frames += 1
        if self.held:
            self.held[1] += 1
            n = self.held[1] - REPEAT_DELAY
            if n >= 0 and n % REPEAT_EVERY == 0:
                self.held[0].action()
        e = self.engine
        if e.playing != self.shown_playing:
            self.shown_playing = e.playing
            self.play_button.draw()
        self._playhead()
        if self.frames % 30 == 0:
            self.draw_status()

    def _playhead(self):
        e = self.engine
        pos = e.position()
        if isinstance(self.view, ls_views.PlaylistView):
            self.view.playhead(pos // ls_model.STEPS_PER_BAR if pos >= 0 and e.mode == ls_engine.SONG else -1)
            return
        if pos >= 0 and e.mode == ls_engine.SONG:
            # Where the pattern on screen is, if the arrangement is playing it.
            bar, s = divmod(pos, ls_model.STEPS_PER_BAR)
            start = self.song.clip_at(e.pattern_index, bar)
            pos = (bar - start) * ls_model.STEPS_PER_BAR + s if start is not None else -1
        self.view.playhead(pos)

    # --- lifecycle ---------------------------------------------------------

    def activate(self):
        if self.closed:
            return
        self.active = True
        self.touching = False
        self.draw_all()
        tulip.touch_callback(self._touch)
        tulip.keyboard_callback(self._key)
        tulip.frame_callback(self.frame, None)

    def deactivate(self):
        self.active = False
        self.engine.stop()
        tulip.frame_callback()
        tulip.touch_callback()
        tulip.keyboard_callback()
        tulip.bg_clear()

    def quit(self):
        self.closed = True
        self.engine.close()


def _activate(screen):
    app.activate()


def _deactivate(screen):
    app.deactivate()


def _quit(screen):
    app.quit()


def run(screen):
    global app
    # Everything but the task bar is drawn on the BG plane, so on the Tab5
    # (LVGL composited over it) the screen's own background must stay clear.
    screen.bg_plane = True
    screen.set_bg_color(ls_views.BG)
    screen.group.remove_flag(lv.obj.FLAG.SCROLLABLE)
    screen.group.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
    screen.activate_callback = _activate
    screen.deactivate_callback = _deactivate
    screen.quit_callback = _quit
    app = LoopStudio(screen)
    screen.present()
