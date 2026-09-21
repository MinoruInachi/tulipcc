"""Playback for loopstudio: AMY synths for the channels and a 16th-note clock
that queues each step a little ahead of time.

Timing follows kanplay's engine: a sequencer.TulipSequence callback runs every
16th and sends the notes of the steps inside a short horizon to AMY as
*absolute* sequencer ticks under one AMY tag, so AMY plays them sample-tight
whatever the UI is doing, and Stop can cancel what is still queued
(ticks="0,0,<tag>"). Note-offs are queued with their note-ons.

Python never holds a whole pattern inside AMY, so edits, mutes, pattern
switches and the playlist all take effect on the next step.
"""
import amy
import sequencer
import synth
import tulip

import ls_model

PPQ = amy.AMY_SEQUENCER_PPQ
STEP_TICKS = PPQ // 4                 # a 16th
HORIZON_TICKS = STEP_TICKS + 2        # queue this far ahead of the clock
CLOCK_DIVIDER = 16
TAG = 230                             # AMY sequencer tag (kanplay has 240..245)
# Fixed AMY synth numbers: PatchSynth's own allocator never reuses a number
# and AMY has 64, so an app that rebuilds synths must not draw from it.
# 48 is the drum kit, 49.. the channels; clear of MIDI (1..16), drums.py (20+)
# and kanplay (58..63).
SYNTH_BASE = 48
# AMY sums every voice and clips at full scale.
MASTER_GAIN = 0.6
PATTERN, SONG = "pattern", "song"


class Engine:
    def __init__(self, song):
        self.song = song
        self.mode = PATTERN
        self.pattern_index = 0
        self.playing = False
        self.drums = None
        self.synths = [None] * ls_model.NUM_CHANNELS
        self._built = [None] * ls_model.NUM_CHANNELS     # (patch, voices) per synth
        self._kit = None
        self._clock = None
        self._pos = 0                  # next step to queue, within the loop
        self._next_tick = 0            # its tick, before swing
        self._origin = 0               # tick of the loop's step 0
        self._fx = None
        self.set_song(song)

    # --- synths ------------------------------------------------------------

    def set_song(self, song):
        self.stop()
        self.song = song
        self.pattern_index = 0
        tulip.seq_bpm(int(song.bpm))
        self.apply_sounds()
        self.apply_fx()

    def _build(self, make):
        s = make()
        # PatchSynth builds its AMY synth on the first note_on; notes here go
        # to the synth number directly, so build it now.
        init = getattr(s, "deferred_init", None)
        if init:
            init()
        return s

    def apply_sounds(self):
        """(Re)build whichever synths no longer match the song."""
        song = self.song
        if any(c.is_drum for c in song.channels):
            if self._kit != song.kit:
                self._release(self.drums)
                self.drums = self._build(lambda: synth.DrumSynth(
                    num_voices=1, channel=SYNTH_BASE, patch=song.kit))
                self._kit = song.kit
        for i, c in enumerate(song.channels):
            want = None if c.is_drum else (c.patch, c.voices)
            if want == self._built[i]:
                continue
            self._release(self.synths[i])
            self.synths[i] = None
            if want is not None:
                self.synths[i] = self._build(lambda: synth.PatchSynth(
                    num_voices=c.voices, channel=SYNTH_BASE + 1 + i, patch=c.patch))
            self._built[i] = want

    def _silence(self, s):
        """All notes off, as a velocity 0 with *no* note. Not
        synth.all_notes_off(): that sends note=0, and AMY's note==0 branch
        (instrument_voice_for_note_event) forgets which voices are sounding
        without sending them a note-off. Measured on the Tab5: the chord
        rang on at full render load, and no later note-off could reach it --
        only rebuilding the synth did."""
        if s is not None and s.synth is not None:
            amy.send(synth=s.synth, vel=0)

    def _release(self, s):
        if s is not None:
            try:
                self._silence(s)
                s.release()
            except Exception:
                pass

    def apply_fx(self):
        """Send the effect levels that changed. An effect at 0 is left off
        entirely: each one costs render time on the Tab5 even when quiet."""
        s = self.song
        fx = (round(s.reverb, 2), round(s.chorus, 2), round(s.echo, 2))
        old = self._fx or (0, 0, 0)
        if fx[0] != old[0]:
            amy.reverb(fx[0])
        if fx[1] != old[1]:
            amy.chorus(fx[1])
        if fx[2] != old[2]:
            # A dotted eighth at the song tempo.
            amy.echo(level=fx[2], delay_ms=int(45000 / max(40, s.bpm)), feedback=0.4)
        self._fx = fx

    def set_bpm(self, bpm):
        self.song.bpm = max(40, min(240, int(bpm)))
        tulip.seq_bpm(self.song.bpm)
        if self.song.echo > 0 and self._fx:
            self._fx = (self._fx[0], self._fx[1], -1)     # the echo time follows the tempo
            self.apply_fx()

    # --- notes -------------------------------------------------------------

    def _target(self, ch):
        c = self.song.channels[ch]
        s = self.drums if c.is_drum else self.synths[ch]
        return c, (s.synth if s is not None else None)

    def _gain(self, c, vel):
        return round(vel / 127.0 * c.volume * self.song.master * MASTER_GAIN, 3)

    def _send(self, number, note, vel, ticks, pan):
        if pan is None:
            amy.send(synth=number, note=note, vel=vel, ticks=ticks)
        else:
            amy.send(synth=number, note=note, vel=vel, ticks=ticks, pan=pan)

    def _play(self, ch, note, on_tick, off_tick):
        c, number = self._target(ch)
        if number is None:
            return
        vel = self._gain(c, note[3])
        if vel <= 0:
            return
        pan = None if abs(c.pan - 0.5) < 0.02 else round(c.pan, 2)
        self._send(number, note[2], vel, "%d,0,%d" % (on_tick, TAG), pan)
        if not c.is_drum:
            self._send(number, note[2], 0, "%d,0,%d" % (off_tick, TAG), None)

    def audition(self, ch, pitch, vel=ls_model.DEFAULT_VEL):
        """Sound a note now (a tap in the rack or the piano roll)."""
        c, number = self._target(ch)
        if number is None:
            return
        now = amy.sequencer_ticks()
        pan = None if abs(c.pan - 0.5) < 0.02 else round(c.pan, 2)
        self._send(number, pitch, self._gain(c, vel), None, pan)
        if not c.is_drum:
            self._send(number, pitch, 0, "%d,0,%d" % (now + STEP_TICKS * 2, TAG), None)

    # --- the clock ---------------------------------------------------------

    def loop_steps(self):
        if self.mode == SONG:
            return max(1, self.song.song_bars()) * ls_model.STEPS_PER_BAR
        return self.song.patterns[self.pattern_index].steps

    def _swing(self, step):
        """Ticks the step is played late: the off 16ths, up to half a step."""
        if step & 1:
            return self.song.swing * STEP_TICKS // 200
        return 0

    def _tick_of(self, base_tick, base_step, step):
        return base_tick + (step - base_step) * STEP_TICKS + self._swing(step)

    def _queue_step(self, pos, tick):
        song = self.song
        if self.mode == SONG:
            events = song.events_at_song_step(pos)
        else:
            events = song.patterns[self.pattern_index].events_at(pos)
        if not events:
            return
        audible = song.audible()
        on = tick + self._swing(pos)
        for ch, note in events:
            if audible[ch]:
                off = self._tick_of(tick, pos, pos + note[1]) - 1
                self._play(ch, note, on, max(on + 1, off))

    def _on_clock(self, tick):
        if not self.playing:
            return
        horizon = tick + HORIZON_TICKS
        # A stalled main thread (a long redraw, a file save) can leave us
        # behind; skip to the present rather than fire a burst of late notes.
        behind = (tick - self._next_tick) // STEP_TICKS
        if behind > 1:
            self._pos += behind
            self._next_tick += behind * STEP_TICKS
        while self._next_tick <= horizon:
            n = self.loop_steps()
            if self._pos >= n:
                self._pos %= n
            if self._pos == 0:
                self._origin = self._next_tick
            self._queue_step(self._pos, self._next_tick)
            self._pos += 1
            self._next_tick += STEP_TICKS

    def play(self, from_step=0):
        if self.playing:
            return
        self.apply_sounds()
        self.playing = True
        self._pos = from_step % self.loop_steps()
        self._next_tick = amy.sequencer_ticks() + 2
        self._origin = self._next_tick - self._pos * STEP_TICKS
        if self._clock is None:
            self._clock = sequencer.TulipSequence(CLOCK_DIVIDER, self._on_clock)
        self._on_clock(amy.sequencer_ticks())

    def stop(self):
        was = self.playing
        self.playing = False
        if was:
            # The queued note-offs go with the tag, so silence what is sounding.
            amy.send(ticks="0,0,%d" % TAG)
            for s in self.synths:
                self._silence(s)

    def toggle(self):
        if self.playing:
            self.stop()
        else:
            self.play()

    def set_mode(self, mode):
        if mode != self.mode:
            playing = self.playing
            self.stop()
            self.mode = mode
            if playing:
                self.play()

    def set_pattern(self, index):
        self.pattern_index = index % ls_model.NUM_PATTERNS

    def position(self):
        """The step sounding now (not the one last queued), or -1."""
        if not self.playing:
            return -1
        return ((amy.sequencer_ticks() - self._origin) // STEP_TICKS) % self.loop_steps()

    def close(self):
        self.stop()
        if self._clock is not None:
            self._clock.clear()
            self._clock = None
        for s in [self.drums] + self.synths:
            self._release(s)
        self.drums = None
        self.synths = [None] * ls_model.NUM_CHANNELS
        self._built = [None] * ls_model.NUM_CHANNELS
        self._kit = None
        if self._fx and any(self._fx):
            self.fx_off()

    def fx_off(self):
        amy.reverb(0)
        amy.chorus(0)
        amy.echo(level=0)
        self._fx = None
