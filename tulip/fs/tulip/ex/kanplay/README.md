# kanplay -- a one-finger chord instrument for Tulip

A MicroPython take on InstaChord's [KANTAN Play](https://github.com/InstaChord/KANTAN_Play_core):
press a scale degree and the current song's parts play that chord -- strummed
guitar, piano arpeggio, bass line, drums -- in whatever key you are in. Song
files are the KANTAN Play Core JSON format, so patterns made for that device
load here.

    tulip.run("kanplay")

## Playing

The 15 play buttons of the KANTAN Play base, on a keyboard (USB or the Tab5's
built-in one) and as pads on screen:

    7 8 9        u  i        roots 1..7 = scale degrees I..VII
    4 5 6        j  k        8 / 9 held with a root = flat / sharp
    1 2 3        m  ,        u dim  j 7th  m swap (major <-> minor)
                             i sus4 k M7   , add9
    space stop    - / = key down/up    [ / ] slot    < / > song    tab manual/auto

Hold a chord-type key, then tap a root. As on the KANTAN Play, **pressing a
root is the on-beat and releasing it is the off-beat**: each press plays the
pattern's next on-beat step, each release the next sub-step, so you tap the
rhythm yourself and nothing runs on a clock. Notes ring until their row plays
again or 5 s pass. A numeric keypad works too, and a MIDI keyboard's white
keys are roots 1..7.

- **Fill: off / on**: with Fill on, the off-beat sub-steps between presses
  are filled in for you, spaced from your last two presses (KANTAN's
  "offbeat auto").
- **Manual / Auto**: Auto runs the beat from the song tempo; the first press
  starts it, chord changes land on the next beat, Stop ends it.
- **Reset** sends every part back to step 0.
- **Part panel** (bottom): one cell per part of the current slot, showing its
  instrument. Tap the name to turn the part on or off; `<` `>` step through
  the instruments (GM families plus every program with its own AMY patch;
  the last entry is the drum kit). "(-)" means the part has no pattern in
  this slot. **Save** writes the edited song to `/user/kanplay/<name>.json`.
- The top line shows AMY's render load; if it passes 0.9 turn a part off
  before the overload failsafe silences everything.
- **Key** transposes live. The song's `base_key` and the slot's `key_offset`
  are added to it.
- **Slot** switches the song's pattern/instrument preset (up to 64 per song).

On the Tab5's built-in keyboard, release the root before the chord-type key:
that keyboard does not say which key came up and the firmware assumes the most
recent press (see `kp_input.py`). A USB keyboard has no such limit.

## Songs

`songs/` holds the genre presets from the KANTAN Play Core repository
(`incbin/preset/song_genre/`, MIT, see `songs/LICENSE_InstaChord.txt`):
Simple Piano / Guitar / Guitar x2, ten Pop styles (Basic 1-2, Bright, Indie,
Soft, Modern, Acoustic, Shuffle, 6/8, Waltz) and three Rock styles (Basic,
Hard, Pop Rock). Pick one from the Song dropdown on the right; `<` and `>` on
the keyboard step through them in file-name order. The repository's
`incbin/preset/song_song/` folder has arrangements of well-known songs in the
same format; they are not bundled here, but any of them (or your own `*.json`)
dropped into `/user/kanplay/` shows up after the bundled ones, marked "(user)".
The format is documented in KANTAN Play Core's
`docs/development/core/song-format.md`; `kp_song.py` reads versions 1..3
including `copy` references and defaults.

What is honoured per part: `tone` (GM program, mapped to AMY patches in
`kp_tones.py`; 128 = drums), `octave`, `voicing` (Close, Guitar, Static,
Ukulele, M-Major, M-Penta, M-Chroma), `arpeggio` velocities, `style` (D / U /
M strokes), `stroke_speed`, `loop_step` / `anchor_step`, `volume`, `pan`,
`enabled`, `drum_note`; per song `tempo`, `swing`, `base_key`. Arpeggio row
0 is the highest chord tone and row 5 the lowest, as on the KANTAN Play. The
`progression` timeline is parsed but not auto-played yet.

## How it is built

| file | what |
|---|---|
| `kp_chords.py` | degree + key + modifier + voicing -> six MIDI notes. Pure Python, unit tested. Written from the KANTAN Music API's *public header* (enum names, argument meaning); the voicing tables are our own. |
| `kp_song.py` | song JSON -> `Song`/`Slot`/`Part` with defaults and copies resolved. Pure Python. |
| `kp_tones.py` | GM program -> AMY patch. |
| `kp_input.py` | keyboard (`tulip.keys()` scan codes + `keyboard_callback`), touch pads and MIDI merged into one held-button set with press/release edges. |
| `kp_engine.py` | KANTAN's manual-play model: press = on-beat, release = off-beat, off-beat auto fill, tempo-driven Auto mode, 5 s auto-release, loop/anchor semantics. Notes go to AMY at absolute ticks, one AMY tag per part so a new press cancels pending sub-steps. Strokes are `stroke_speed` ms apart rounded to the 48 PPQ grid (~10 ms at 120 BPM). |
| `kanplay.py` | the LVGL screen and the app glue. |

Tests (CPython, no hardware): `python3 tulip/tests/test_kanplay.py`.

## Limits worth knowing

- **AMY render load on the Tab5.** AMY renders on one core there, and a
  block that misses its deadline is heard as noise; at 0.98 AMY's failsafe
  resets every synth by itself. The app does not reset AMY when it starts
  (that would silence drums or loopstudio running alongside); its parts sit
  on fixed synth numbers from 58 up. `kp_tones.fit_voices()` trims
  each part's voices so the modelled cost stays under `RENDER_BUDGET`; the
  part panel shows "2/4v" when a part was trimmed. Six parts of Simple_Piano
  peak at about 0.75 with this. Each part gets only as many voices as its
  pattern sounds at once, and GM pianos map to the DX7 pianos (the
  interpolated piano costs half as much again per voice).
- **Stroke resolution** is the sequencer tick, about 10 ms; a 20 ms stroke is
  2 ticks per string.
- **Touch** reports at most 3 points and the LVGL pads use one; the keyboard
  is the better instrument surface.
- **MIDI out** (`Player.set_midi_out(True)`) sends note-ons when a step is
  *queued*, up to half a beat early. Not wired to the UI yet for that reason.
