# midi_out.py -- send MIDI to something else, and check that it arrived.
#
#   run('midi_out')
#
# Nothing here makes a sound on Tulip itself. That is the point: every byte
# goes out to whatever is listening on the other end, so you need a synth, a
# sound module or a DAW there to hear anything.
#
# Where "out" is depends on the board. The Tab5 sends to a class-compliant USB
# MIDI device plugged into its USB-A host port -- with nothing plugged in, the
# calls below succeed and go nowhere. Tulip CC and AMYboard send over their own
# USB MIDI connection, and AMYboard also out its MIDI out jack.

import tulip, amy, time

# Status bytes. The low nibble of a channel message is the channel, 0-15, which
# instruments number 1-16 -- so ch=0 here is what the front panel calls 1.
NOTE_OFF = 0x80
NOTE_ON = 0x90
CONTROL_CHANGE = 0xB0
PROGRAM_CHANGE = 0xC0
PITCH_BEND = 0xE0
CLOCK, START, STOP = 0xF8, 0xFA, 0xFC


def note(number, velocity=100, duration=0.4, ch=0):
    """Play one note and let go of it."""
    tulip.midi_out(bytes([NOTE_ON | ch, number, velocity]))
    time.sleep(duration)
    tulip.midi_out(bytes([NOTE_OFF | ch, number, 0]))


def chord(numbers, velocity=100, duration=1.0, ch=0):
    """Several notes at once. A buffer may hold as many messages as you like."""
    on = bytearray()
    off = bytearray()
    for n in numbers:
        on += bytes([NOTE_ON | ch, n, velocity])
        off += bytes([NOTE_OFF | ch, n, 0])
    tulip.midi_out(on)
    time.sleep(duration)
    tulip.midi_out(off)


def panic(channels=16):
    """All notes off, everywhere. Worth knowing before you need it."""
    for ch in range(channels):
        tulip.midi_out(bytes([CONTROL_CHANGE | ch, 123, 0]))


def connected():
    """True if this board can say whether a MIDI device is there.

    Only the Tab5 knows -- it owns the USB host port, so it can see the device
    attach. Elsewhere this returns None, meaning "no idea", not "nothing".
    """
    if not hasattr(tulip, 'usb_status'):
        return None
    return tulip.usb_status().get('midi', None)


def sent():
    """Packets the Tab5 has put on the wire, and how many failed.

    Returns None on boards without the counters. When nothing can be heard,
    this is what separates "we never sent it" from "it was sent and ignored":
    errors and timeouts stay at 0 and status stays 0 when the device is taking
    them.
    """
    if not hasattr(tulip, 'usb_status'):
        return None
    s = tulip.usb_status()
    if 'midi_out_packets' not in s:
        return None
    return (s['midi_out_packets'], s['midi_out_errors'],
            s['midi_out_timeouts'], s['midi_out_status'])


def demo():
    is_there = connected()
    if is_there is False:
        print("No USB MIDI device attached. Plug one into the USB-A port --")
        print("everything below will run, but the bytes have nowhere to go.")
    elif is_there is None:
        print("This board cannot tell whether a MIDI device is listening.")
    before = sent()

    print("a note")
    note(60)
    time.sleep(0.4)

    print("a scale")
    for n in (60, 62, 64, 65, 67, 69, 71, 72):
        note(n, duration=0.18)
    time.sleep(0.4)

    print("a chord")
    chord([60, 64, 67, 72])
    time.sleep(0.4)

    print("program change, then a note in the same buffer")
    # One buffer, two messages: the program change takes effect and the note
    # plays on the new sound.
    tulip.midi_out(bytes([PROGRAM_CHANGE, 5, NOTE_ON, 60, 100]))
    time.sleep(0.6)
    tulip.midi_out(bytes([NOTE_OFF, 60, 0]))
    tulip.midi_out(bytes([PROGRAM_CHANGE, 0]))
    time.sleep(0.4)

    print("modulation wheel up and down while a note is held")
    tulip.midi_out(bytes([NOTE_ON, 64, 100]))
    for value in list(range(0, 128, 8)) + list(range(127, -1, -8)):
        tulip.midi_out(bytes([CONTROL_CHANGE, 1, value]))
        time.sleep(0.03)
    tulip.midi_out(bytes([NOTE_OFF, 64, 0]))
    time.sleep(0.4)

    print("pitch bend, a 14-bit value split low byte first")
    tulip.midi_out(bytes([NOTE_ON, 60, 100]))
    for bend in range(8192, 16384, 256):
        tulip.midi_out(bytes([PITCH_BEND, bend & 0x7F, (bend >> 7) & 0x7F]))
        time.sleep(0.03)
    tulip.midi_out(bytes([PITCH_BEND, 0x00, 0x40]))   # 8192, centre
    tulip.midi_out(bytes([NOTE_OFF, 60, 0]))
    time.sleep(0.4)

    print("transport and clock: start, 2 bars at 120 BPM, stop")
    # 24 clocks to the quarter note is the MIDI standard, whatever the tempo.
    tulip.midi_out(bytes([START]))
    for _tick in range(24 * 8):
        tulip.midi_out(bytes([CLOCK]))
        time.sleep(60.0 / 120 / 24)
    tulip.midi_out(bytes([STOP]))
    time.sleep(0.4)

    print("sysex: a universal identity request")
    # Send it whole, F0 to F7; the packetising into USB-MIDI is done for you.
    tulip.midi_out(bytes([0xF0, 0x7E, 0x7F, 0x06, 0x01, 0xF7]))
    time.sleep(0.4)

    print("AMY playing the far end instead of its own voices")
    # A synth with a note output sends its notes out as MIDI. Give it no
    # voices and it costs no oscillators and makes no sound here -- it is
    # purely an interface to whatever is on the other end.
    if hasattr(amy, 'NOTE_OUTPUT_MIDI_OUT'):
        amy.send(synth=15, note_output='%d,1' % amy.NOTE_OUTPUT_MIDI_OUT)
        for n in (60, 64, 67):
            amy.send(synth=15, note=n, vel=0.8)
            time.sleep(0.4)
            amy.send(synth=15, note=n, vel=0)
        amy.send(synth=15, note_output='%d' % amy.NOTE_OUTPUT_OFF)
    else:
        print("  (this AMY build has no note outputs)")

    panic()

    after = sent()
    if before is not None and after is not None:
        print("\n%d packets sent, %d errors, %d timeouts, last status %d"
              % (after[0] - before[0], after[1] - before[1],
                 after[2] - before[2], after[3]))
        print("(errors and timeouts at 0 and status 0 means the device took"
              " every one of them)")
    print("done")


demo()
