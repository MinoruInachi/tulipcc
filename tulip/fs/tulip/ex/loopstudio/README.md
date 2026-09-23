# loopstudio -- a pattern-based music workstation for Tulip

A touch workstation in the manner of FL Studio Mobile, on AMY: a channel rack
of step sequencers, a piano roll, a playlist that arranges patterns into a
song, and a mixer. Built for the Tab5's 1280x720 touch screen.

    tulip.run("loopstudio")

It opens on a demo song. Press **PLAY**.

## The top bar

| | |
|---|---|
| **PLAY / STOP** | the transport (space on a keyboard) |
| **PAT / SONG** | loop the current pattern, or play the playlist's arrangement |
| **- 124 BPM +** | tempo; hold to repeat |
| **Swing** | 0 / 15 / 30 / 45 / 60: how late the off 16ths land |
| **< P1 >** | the pattern the rack and piano roll edit (8 per song) |
| **1 bar / 2 bar** | the pattern's length |
| **RACK ROLL SONG MIXER FILE** | the views |

The line under it shows the song name, messages, and AMY's render load. Past
0.9 mute a channel: at 0.98 AMY's overload failsafe silences everything.

Playback carries on while another app is in front (the task bar's switch
button, or the launcher): the clock runs in the sequencer, not in the screen.
Only **STOP** or quitting loopstudio stops the song. An app that resets AMY
on start (kanplay does) silences the synths for a step; loopstudio notices
and rebuilds them.

## RACK -- the channel rack

Eight channels: four drum sounds of one kit and four synths. Tap a step to
light it, drag along a row to paint. A lit step is a one-step note at the
channel's root pitch; a step shown in cyan holds piano-roll notes at other
pitches (tapping it clears them).

- **M** / **S** mute and solo.
- **<** **>** step through the drum sounds or the instruments (Juno-106, DX7
  and piano patches). A drum channel's steps follow it to the new sound.
- Tap the name to select the channel (the one the piano roll edits) and hear it.
- **Kit** steps through the drum kits: TR-808, TR-909, Linn 9000, MR-12, Power.

## ROLL -- the piano roll

One octave of the selected channel across the whole pattern.

- Tap an empty cell to add a note; keep the finger down and drag right to set
  its length. **Length** is what a plain tap gives (it follows your last drag).
- Tap a note to delete it; drag from a note to resize it.
- **Oct - / Dn / Up / Oct +** scroll; the keys on the left audition.
- **<** **>** change channel, **Clear** empties the channel in this pattern.

## SONG -- the playlist

Rows are patterns, columns are bars (32, in two pages). Tap a cell to place
the pattern there, tap a clip to remove it; patterns on different rows play
together. Tap a row's label to make it the current pattern. The song is as
long as its last clip and loops. **SONG** mode in the top bar plays it; while
it does, the rack and the roll show a playhead whenever their pattern is the
one sounding.

## MIXER

A fader, pan (with a centre detent), mute and solo per channel, then the
master level and the reverb, chorus and echo sends. The echo is a dotted
eighth at the song tempo. Each effect costs render time from the moment it
leaves zero -- watch the load.

## FILE

**New**, **Demo**, **Save**, **Save as new** (`song1`, `song2`, ...), and the
saved songs; tap one to load it. Songs are JSON in `/user/loopstudio/`.

## How it works

- `ls_model.py` -- channels, patterns, clips, JSON. No Tulip imports; tested
  under CPython.
- `ls_engine.py` -- one AMY synth per channel on fixed synth numbers (48..56),
  and a 16th-note `sequencer.TulipSequence` clock that sends each step to AMY
  a little ahead of time as absolute sequencer ticks under one tag, as
  kanplay's auto mode does. AMY plays them sample-tight whatever the UI is
  doing; Stop cancels the tag. Nothing is stored inside AMY's sequencer, so
  every edit, mute and pattern switch is heard on the next step.
- `ls_views.py` -- the views, drawn on the BG plane with `bg_rect` / `bg_str`
  and hit-tested by hand rather than built from LVGL widgets: a view repaints
  only the cell that changed, where LVGL would invalidate and redraw areas.
- `loopstudio.py` -- the top bar, touch and frame dispatch, files.

Tests: `python3 tulip/tests/test_loopstudio.py` (model, scheduler, and a
smoke test that taps through every view and checks each draw call lands on
the screen).

## Limits

- Eight channels, eight patterns of one or two bars, a 32-bar playlist.
- One velocity per note (100) from the UI; the file format carries velocity.
- The piano roll shows one octave at a time, because a fingertip needs rows
  about 4 mm tall on the Tab5.
- Render budget: the Tab5 renders AMY on one core. The demo's four synth
  channels use 2-3 voices each; the sampled Piano costs about half again as
  much per voice as a Juno or DX7 patch.
