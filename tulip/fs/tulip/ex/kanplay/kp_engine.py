"""Playback engine for kanplay: a KANTAN Play slot (six parts, each with an
arpeggio pattern, stroke style and voicing) played against the current chord.

It follows the manual-play model of KANTAN Play Core's task_kantanplay.cpp:

* **Pressing a root is the on-beat, releasing it is the off-beat.** Each
  press advances every part to its next on-beat step and plays it; each
  release advances one sub-step and plays that. With step_per_beat 2 a
  press plays step 0, the release step 1, the next press step 2, and so on.
  Nothing runs on a clock: the player taps the rhythm.
* **Off-beat auto** fills the sub-steps between presses by itself, spacing
  them from the interval between the last two presses (song tempo until
  there are two). With three or more steps per beat the sub-steps after the
  manual release are filled the same way.
* **Auto mode** runs the beat from the song tempo instead; the first press
  starts it and chord changes land on the next on-beat.
* Notes stop when their row plays again, on a mute ("M") step, on stop(),
  or after AUTORELEASE_MS -- KANTAN's 5 s auto note-off.
* Loop: a part's steps run 0..loop_step and wrap. A chord change sends the
  part back to step 0 only if it is at or past anchor_step (so a drum part
  with anchor_step 64 keeps its groove across chord changes); a slot change
  always restarts.
* Pattern rows: row 0 is the highest chord tone (pitch 6) and row 5 the
  lowest (pitch 1), as in KANTAN's arpeggio arrays; row 6 is the drum row.

Timing: events go to AMY as absolute sequencer ticks, one AMY tag per part,
so pending sub-steps can be cancelled by a new press (ticks="0,0,<tag>").
Strokes are stroke_speed ms apart rounded to the 48 PPQ tick grid (~10 ms
at 120 BPM). Auto-release is checked from a quarter-note housekeeping tick.
"""
import amy
import sequencer
import tulip

import kp_chords
import kp_song
import kp_tones

PPQ = amy.AMY_SEQUENCER_PPQ
TAG_BASE = 240                # AMY sequencer tags 240..245, one per part
AUTORELEASE_MS = 5000         # KANTAN def::app::autorelease_msec
AUTO_HORIZON_TICKS = 12       # auto mode queues this far ahead (a 16th)
AUTO_CLOCK_DIVIDER = 16
HOUSEKEEP_DIVIDER = 4
NOTE_MIN, NOTE_MAX = 12, 120
MANUAL, AUTO = "manual", "auto"
# Headroom: AMY sums every voice of every part and clips at full scale, so a
# six-string strum on four parts distorts at velocity 1. Each note is scaled
# by MASTER_GAIN / sqrt(parts playing) -- see Player.mix_gain().
MASTER_GAIN = 0.5


def ms_to_ticks(ms, bpm):
    return int(round(ms * bpm * PPQ / 60000.0))


def substep_offsets(step_per_beat, cycle_ticks, swing):
    """Tick offsets of sub-steps 0..S-1 within one beat of cycle_ticks.
    Even step counts swing: swing 0 is 1:1, swing 100 is 2:1 (KANTAN's
    calcSwing_x100)."""
    s = max(1, step_per_beat)
    step = cycle_ticks / float(s)
    if s % 2 == 0 and swing:
        long_ = step + step * (swing * 100 // 3) / 10000.0
        short = 2 * step - long_
        lengths = [long_ if k % 2 == 0 else short for k in range(s)]
    else:
        lengths = [step] * s
    out = [0]
    for k in range(s - 1):
        out.append(out[-1] + lengths[k])
    return [int(round(o)) for o in out]


class ChordOption:
    """What the next on-beat will play: the KANTAN _next_option."""

    def __init__(self, degree=0, modifier="", semitone_shift=0, minor_swap=False,
                 bass_degree=0, bass_semitone_shift=0, slot_index=0):
        self.degree = degree
        self.modifier = modifier
        self.semitone_shift = semitone_shift
        self.minor_swap = minor_swap
        self.bass_degree = bass_degree
        self.bass_semitone_shift = bass_semitone_shift
        self.slot_index = slot_index

    def copy(self):
        return ChordOption(self.degree, self.modifier, self.semitone_shift,
                           self.minor_swap, self.bass_degree,
                           self.bass_semitone_shift, self.slot_index)

    def same_root(self, other):
        return (self.degree == other.degree and self.bass_degree == other.bass_degree
                and self.semitone_shift == other.semitone_shift
                and self.minor_swap == other.minor_swap)

    def chord(self, key):
        return kp_chords.Chord(self.degree, key=key, modifier=self.modifier,
                               semitone_shift=self.semitone_shift,
                               minor_swap=self.minor_swap,
                               bass_degree=self.bass_degree,
                               bass_semitone_shift=self.bass_semitone_shift)


class PartPlayer:
    """One part of the current slot: its synth, its step and what it has
    sounding."""

    def __init__(self, index):
        self.index = index
        self.tag = TAG_BASE + index
        self.part = None
        self.synth_number = kp_tones.SYNTH_BASE + index   # fixed AMY synth number
        self.synth = None
        self.synth_program = None
        self.synth_voices = 0
        self.notes = [0] * kp_song.NUM_ROWS      # note per row for the chord
        self.sounding = [0] * kp_song.NUM_ROWS   # what each row last played
        self.release_at = [0] * kp_song.NUM_ROWS  # auto-release tick per row
        self.step = -1                            # -1 = part not playing
        self.midi_out = None                      # callable(bytes) or None

    # --- setup ------------------------------------------------------------

    def wanted_voices(self, part):
        if not part.plays():
            return 0
        return 1 if part.is_drum else max(1, min(6, part.max_polyphony()))

    def set_part(self, part, voices=None):
        self.part = part
        # max_polyphony walks the whole arpeggio (~1 ms per part on the Tab5),
        # so the UI reads this cached value rather than asking every frame.
        self.wanted = self.wanted_voices(part)
        if voices is None:
            voices = self.wanted
        if voices <= 0:
            self.release()
            self.step = -1
            return
        if (self.synth is None or self.synth_program != part.tone
                or self.synth_voices != voices):
            self.release()
            self.synth_number = kp_tones.SYNTH_BASE + self.index
            self.synth = kp_tones.make_synth(part.tone, voices, self.synth_number)
            self.synth_program = part.tone
            self.synth_voices = voices
            self.step = 0
        elif self.step < 0:
            self.step = 0

    def release(self):
        self.silence()
        if self.synth is not None:
            try:
                self.synth.release()
            except Exception:
                pass
        self.synth = None
        self.synth_program = None
        self.synth_voices = 0

    def set_chord(self, chord, transpose):
        part = self.part
        if part.is_drum:
            dn = list(part.drum_note)
            self.notes = dn + [0] * (kp_song.NUM_ROWS - len(dn))
            return
        voiced = chord.notes(part.voicing)          # pitch 1..6, low to high
        self.notes = [0] * kp_song.NUM_ROWS
        for row in range(6):
            n = voiced[5 - row]                      # row 0 = pitch 6 (highest)
            if n:
                n += transpose + part.octave
                self.notes[row] = n if NOTE_MIN <= n <= NOTE_MAX else 0

    # --- sounding notes -----------------------------------------------------

    def _tagged(self, tick):
        return "%d,0,%d" % (tick, self.tag)

    def _note(self, note, vel, ticks, pan=None):
        """One note message straight to AMY. PatchSynth.note_on costs ~1.7 ms
        on the Tab5 against 0.3 ms for the message itself, and a strummed
        step sends up to ~30 of them, so the wrapper is skipped here; its one
        job that matters (rebuilding the synth if AMY was reset under it) is
        done once per step in play_step."""
        if pan is None:
            amy.send(synth=self.synth_number, note=note, vel=vel, ticks=ticks)
        else:
            amy.send(synth=self.synth_number, note=note, vel=vel, ticks=ticks, pan=pan)

    def cancel_queued(self):
        amy.send(ticks="0,0,%d" % self.tag)

    def silence(self):
        """Stop everything queued and everything sounding, right now."""
        self.cancel_queued()
        if self.synth is not None and any(self.sounding):
            try:
                self.synth.all_notes_off()
            except Exception:
                pass
            if self.midi_out:
                for n in self.sounding:
                    if n:
                        self._midi(0x80, n, 0)
        self.sounding = [0] * kp_song.NUM_ROWS
        self.release_at = [0] * kp_song.NUM_ROWS

    def note_off_all(self, tick=None):
        """KANTAN chordNoteOff(part): release every row (at a tick, or now)."""
        for r in range(kp_song.NUM_ROWS):
            self._row_off(r, tick)

    def housekeep(self, now):
        """Auto-release rows whose 5 s are up."""
        for r in range(kp_song.NUM_ROWS):
            if self.sounding[r] and self.release_at[r] and self.release_at[r] <= now:
                self._row_off(r, None)

    def _row_off(self, r, tick):
        n = self.sounding[r]
        if n and not (self.part and self.part.is_drum) and self.synth is not None:
            self._note(n, 0, self._tagged(tick) if tick is not None else None)
            if self.midi_out:
                self._midi(0x80, n, 0)
        self.sounding[r] = 0
        self.release_at[r] = 0

    def _midi(self, status, note, vel):
        ch = 9 if self.part.is_drum else self.index
        self.midi_out(bytes((status | ch, note & 0x7f, vel & 0x7f)))

    # --- steps --------------------------------------------------------------

    def advance_on_beat(self, step_per_beat, normal_reset, force_reset):
        """Move to the next on-beat step (KANTAN chordStepAdvance, on-beat).
        Returns True if the pattern was sent back to the start."""
        if self.step < 0:
            return False
        p = self.part
        if force_reset:
            self.step = 0
            return True
        if normal_reset and self.step >= p.anchor_step:
            self.step = 0
            return True
        nxt = ((self.step + step_per_beat) // step_per_beat) * step_per_beat
        self.step = 0 if nxt > p.loop_step else nxt
        return False

    def advance_off_beat(self, step_per_beat, beat_index):
        if self.step >= 0:
            self.step = (self.step // step_per_beat) * step_per_beat + beat_index

    def play_step(self, tick, bpm, gain=1.0):
        """Schedule the current step's notes at tick (KANTAN chordStepPlay)."""
        part = self.part
        if self.step < 0 or self.synth is None:
            return
        rebuild = getattr(self.synth, "_rebuild_after_amy_reset", None)
        if rebuild and self.synth._generation != amy.instrument_generation:
            rebuild()
        idx = self.step
        style = "" if part.is_drum else part.style_at(idx)
        stroke = ms_to_ticks(part.stroke_speed, bpm)
        if style == "M":
            # A mute is a fast downstroke that damps everything: stroke/4
            # between rows, releasing 2*stroke later.
            t = tick
            for r in range(kp_song.NUM_ROWS - 1, -1, -1):
                if self.sounding[r]:
                    self._row_off(r, t + 2 * stroke)
                    t += max(0, stroke // 4)
            return
        rows = [r for r in range(kp_song.NUM_ROWS)
                if part.velocity(r, idx) > 0 and self.notes[r]]
        if not rows:
            return
        if style == "D":
            rows.reverse()                 # low (row 5) to high (row 0)
        spacing = stroke if style in ("D", "U") else 0
        level = part.volume / 100.0 * gain
        pan = 0.5 + part.pan / 10.0
        release = ms_to_ticks(AUTORELEASE_MS, bpm)
        for k, r in enumerate(rows):
            t = tick + k * spacing
            note = self.notes[r]
            v = part.velocity(r, idx)
            vel = min(1.0, v / 127.0 * level)
            if self.sounding[r] == note:
                # AMY re-onsets a note that is already sounding on the same
                # voice, so the note-off would only be one more message
                # (a strummed step sends ~30 and each costs ~0.3 ms).
                if self.midi_out:
                    self._midi(0x80, note, 0)
            else:
                self._row_off(r, t)
            self._note(note, vel, self._tagged(t), pan)
            self.sounding[r] = note
            self.release_at[r] = t + release
            if self.midi_out:
                # MIDI gets the pattern's own velocity; the mix gain is AMY's.
                self._midi(0x90, note, int(min(127, v * part.volume / 100.0)))


class Player:
    """The instrument: a song, the current slot and key, the parts, and the
    beat state that presses and releases drive."""

    def __init__(self):
        self.song = kp_song.blank()
        self.slot_index = 0
        self.key_shift = 0
        self.parts = [PartPlayer(i) for i in range(kp_song.NUM_PARTS)]
        self.mode = MANUAL
        self.offbeat_auto = False
        self.press_velocity = 100
        self.midi_out = False
        self.on_change = None                 # callable() for the UI
        self.current = ChordOption()          # what is playing (KANTAN _current_option)
        self.next = ChordOption()             # what the next on-beat plays
        self.chord = None                     # kp_chords.Chord of `current`
        self.beat_index = 0
        self.last_onbeat_tick = None
        self.onbeat_cycle = None              # ticks between the last two presses
        self.auto_running = False
        self._auto_beat_tick = 0
        self._auto_sub = 0
        self._auto_seq = None
        self._house_seq = None

    # --- configuration ------------------------------------------------------

    @property
    def slot(self):
        return self.song.slots[self.slot_index]

    @property
    def step_per_beat(self):
        return max(1, min(4, self.slot.step_per_beat))

    @property
    def key(self):
        return (self.song.base_key + self.slot.key_offset + self.key_shift) % 12

    def _transpose(self):
        total = self.song.base_key + self.slot.key_offset + self.key_shift
        return total - (total % 12)

    def load_song(self, song):
        self.stop()
        self.song = song
        self.slot_index = 0
        self.key_shift = 0
        self.onbeat_cycle = None
        tulip.seq_bpm(int(song.tempo))
        self._apply_slot()

    def set_slot(self, index):
        index = index % len(self.song.slots)
        if index == self.slot_index:
            return
        self.stop()
        self.slot_index = index
        self._apply_slot()

    def _apply_parts(self):
        """Give every part of the slot its synth, with voice counts trimmed
        so the Tab5's single-core render stays inside its budget: too many
        voices and blocks miss their deadline, which is audible as noise."""
        parts = self.slot.parts
        wanted = [pp.wanted_voices(part) for pp, part in zip(self.parts, parts)]
        voices = kp_tones.fit_voices(wanted, [part.tone for part in parts])
        for pp, part, v in zip(self.parts, parts, voices):
            pp.set_part(part, v)

    def _apply_slot(self):
        self._apply_parts()
        self.next.slot_index = self.slot_index
        self.current.slot_index = self.slot_index
        self.beat_index = 0
        if self.chord is not None:
            self._voice_chord()
        self._changed()

    def set_part_enabled(self, index, enabled):
        """Turn one part of the current slot on or off (edits the song)."""
        part = self.slot.parts[index]
        part.enabled = bool(enabled)
        self._reapply_part(index)

    def set_part_tone(self, index, program):
        """Change one part's GM program in the current slot (edits the song)."""
        part = self.slot.parts[index]
        program = int(program)
        was_drum = part.is_drum
        part.tone = program
        if part.is_drum != was_drum:
            # A part switching to or from the drum kit needs a pattern on the
            # rows that kind of part reads: give it a basic one if it has none.
            if part.is_drum and not any(part.arpeggio[6]):
                part.arpeggio[6] = [100, 0, 100, 0]
            elif not part.is_drum and not any(part.arpeggio[r] for r in range(6)):
                part.arpeggio[5] = [100]
        self._reapply_part(index)

    def _reapply_part(self, index):
        self.parts[index].silence()
        self._apply_parts()
        if self.chord is not None:
            self._voice_chord()
        self._changed()

    def set_key_shift(self, shift):
        self.key_shift = shift
        if self.chord is not None:
            self._voice_chord()
        self._changed()

    def set_mode(self, mode):
        if mode != self.mode:
            self.stop()
            self.mode = mode
            self._changed()

    def set_offbeat_auto(self, enabled):
        self.offbeat_auto = bool(enabled)
        self._changed()

    def set_midi_out(self, enabled):
        self.midi_out = enabled
        sink = tulip.midi_out if enabled else None
        for pp in self.parts:
            pp.midi_out = sink

    def _changed(self):
        if self.on_change:
            self.on_change()

    # --- clocks -------------------------------------------------------------

    def _ensure_housekeeping(self):
        if self._house_seq is None:
            self._house_seq = sequencer.TulipSequence(HOUSEKEEP_DIVIDER, self._housekeep)

    def _housekeep(self, tick):
        for pp in self.parts:
            pp.housekeep(tick)

    def _cycle_ticks(self):
        """Ticks per beat for filling sub-steps: measured from the player's
        presses when we have two, else the song tempo."""
        if self.onbeat_cycle:
            return self.onbeat_cycle
        return PPQ

    # --- the beat (KANTAN chordBeat / chordStepAdvance / chordStepPlay) -----

    def _voice_chord(self):
        self.chord = self.current.chord(self.key)
        t = self._transpose()
        for pp in self.parts:
            if pp.synth is not None:
                pp.set_chord(self.chord, t)

    def mix_gain(self):
        """Per-note gain that keeps the sum of the playing parts in range."""
        n = sum(1 for pp in self.parts if pp.synth is not None)
        return MASTER_GAIN / (max(1, n) ** 0.5)

    def _on_beat(self, tick, bpm):
        """Advance every part to its next on-beat step and play it at tick."""
        self.beat_index = 0
        normal_reset = not self.current.same_root(self.next)
        force_reset = self.current.slot_index != self.next.slot_index or self.current.degree == 0
        self.current = self.next.copy()
        self._voice_chord()
        gain = self.mix_gain() * self.press_velocity / 100.0
        for pp in self.parts:
            if pp.advance_on_beat(self.step_per_beat, normal_reset, force_reset):
                pp.note_off_all(tick)
            pp.play_step(tick, bpm, gain)

    def _off_beat(self, tick, bpm):
        """Advance one sub-step (never onto the next on-beat) and play it."""
        s = self.step_per_beat
        if self.beat_index >= s - 1:
            self.beat_index = s - 1
            return False
        self.beat_index += 1
        # Modifier changes apply immediately, without a reset (KANTAN).
        self.current.modifier = self.next.modifier
        self._voice_chord()
        gain = self.mix_gain()
        for pp in self.parts:
            pp.advance_off_beat(s, self.beat_index)
            pp.play_step(tick, bpm, gain)
        return True

    def _cancel_pending(self):
        for pp in self.parts:
            pp.cancel_queued()

    # --- manual play ------------------------------------------------------------

    def press(self, degree, modifier="", semitone_shift=0, minor_swap=False):
        """A root button went down: the on-beat."""
        self._ensure_housekeeping()
        self.next = ChordOption(degree, modifier, semitone_shift, minor_swap,
                                slot_index=self.slot_index)
        if self.mode == AUTO:
            if not self.auto_running:
                self._auto_start()
            self._changed()
            return
        now = amy.sequencer_ticks()
        bpm = tulip.seq_bpm()
        if self.last_onbeat_tick is not None:
            cycle = now - self.last_onbeat_tick
            # Ignore a pause (KANTAN resets the cycle after ~4 beats) and jitter.
            if PPQ // 4 <= cycle <= PPQ * 4:
                self.onbeat_cycle = cycle
        self.last_onbeat_tick = now
        self._cancel_pending()
        self._on_beat(now, bpm)
        if self.offbeat_auto:
            self._fill_substeps(now, bpm, from_sub=1)
        self._changed()

    def release(self, degree):
        """A root button came up: the off-beat, if it was the playing root."""
        if self.mode == AUTO or degree != self.current.degree:
            return
        if self.offbeat_auto:
            return
        now = amy.sequencer_ticks()
        bpm = tulip.seq_bpm()
        if self._off_beat(now, bpm):
            s = self.step_per_beat
            if s >= 3 and self.last_onbeat_tick is not None:
                # Manual off-beat with finer steps: the rest follow at the
                # press-to-release interval.
                gap = max(1, now - self.last_onbeat_tick)
                t = now
                while self.beat_index < s - 1:
                    t += gap
                    if not self._off_beat(t, bpm):
                        break
            self._changed()

    def set_modifier(self, modifier, semitone_shift=None, minor_swap=None):
        """A chord-type key changed while playing: it takes effect on the
        next beat (KANTAN updates _next_option on every button change)."""
        self.next.modifier = modifier
        if semitone_shift is not None:
            self.next.semitone_shift = semitone_shift
        if minor_swap is not None:
            self.next.minor_swap = minor_swap

    def _fill_substeps(self, onbeat_tick, bpm, from_sub):
        """Queue sub-steps from_sub..S-1 after an on-beat, spaced from the
        measured press interval (or the song tempo) with swing."""
        offsets = substep_offsets(self.step_per_beat, self._cycle_ticks(), self.song.swing)
        for k in range(from_sub, self.step_per_beat):
            if not self._off_beat(onbeat_tick + offsets[k], bpm):
                break

    # --- auto play ----------------------------------------------------------------

    def _auto_start(self):
        self.auto_running = True
        self.beat_index = 0
        self._auto_beat_tick = amy.sequencer_ticks()
        self._auto_sub = 0
        if self._auto_seq is None:
            self._auto_seq = sequencer.TulipSequence(AUTO_CLOCK_DIVIDER, self._auto_clock)
        self._auto_clock(self._auto_beat_tick)

    def _auto_clock(self, tick):
        if not self.auto_running:
            return
        bpm = tulip.seq_bpm()
        s = self.step_per_beat
        horizon = tick + AUTO_HORIZON_TICKS
        while True:
            offsets = substep_offsets(s, PPQ, self.song.swing)
            t = self._auto_beat_tick + offsets[self._auto_sub]
            if t > horizon:
                return
            if self._auto_sub == 0:
                self._on_beat(t, bpm)
            else:
                self._off_beat(t, bpm)
            self._auto_sub += 1
            if self._auto_sub >= s:
                self._auto_sub = 0
                self._auto_beat_tick += PPQ

    # --- transport ------------------------------------------------------------------

    def playing(self):
        return self.auto_running or any(pp.sounding[r] for pp in self.parts
                                        for r in range(kp_song.NUM_ROWS))

    def stop(self):
        """Everything off; the pattern position is kept, as KANTAN does."""
        self.auto_running = False
        for pp in self.parts:
            pp.silence()
        self._changed()

    def reset_steps(self):
        """Back to step 0 on every part (KANTAN step reset)."""
        self.stop()
        self.beat_index = 0
        self.onbeat_cycle = None
        self.last_onbeat_tick = None
        for pp in self.parts:
            if pp.step >= 0:
                pp.step = 0
        self.current = ChordOption(slot_index=self.slot_index)

    def close(self):
        self.stop()
        for seq in (self._auto_seq, self._house_seq):
            if seq is not None:
                seq.clear()
        self._auto_seq = None
        self._house_seq = None
        for pp in self.parts:
            pp.release()
