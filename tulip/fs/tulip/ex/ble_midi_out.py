# ble_midi_out.py -- send MIDI over Bluetooth, and check that it went.
#
#   run('ble_midi_out')
#
# The Bluetooth twin of midi_out.py: the same tour, but every byte goes out
# through blemidi.send() instead of tulip.midi_out(). Nothing here makes a sound
# on Tulip itself, so you need something listening on the other end.
#
# There are two ways to have a far end, and this tries them in order:
#
#   1. Tulip connects to a BLE MIDI instrument -- a synth with Bluetooth MIDI
#      in (an M-VAVE FM-1, say), or a BLE MIDI adapter on a hardware synth.
#   2. If none is found, Tulip advertises as "Tulip Tab5" and waits for a Mac,
#      iPad or iPhone to connect to it. On a Mac that is Audio MIDI Setup >
#      Window > Show MIDI Studio > Bluetooth; a DAW then sees Tulip as a MIDI
#      input.
#
# An instrument that also sends MIDI (the FM-1 sends its keys and sequencer)
# plays Tulip's own voices while connected -- that is blemidi's MIDI in.
#
# Bluetooth MIDI is Tab5 only; the other Tulips have no radio.

import tulip, time

try:
    import blemidi
except ImportError:
    blemidi = None

# Status bytes. The low nibble of a channel message is the channel, 0-15, which
# instruments number 1-16 -- so ch=0 here is what the front panel calls 1.
NOTE_OFF = 0x80
NOTE_ON = 0x90
CONTROL_CHANGE = 0xB0
PROGRAM_CHANGE = 0xC0
PITCH_BEND = 0xE0
CLOCK, START, STOP = 0xF8, 0xFA, 0xFC


def out(message):
    """blemidi.send() takes what tulip.midi_out() takes: a list, tuple or bytes
    holding one or more whole messages, sysex included. It puts a BLE-MIDI
    timestamp in front of each message and splits what will not fit."""
    blemidi.send(message)


def note(number, velocity=100, duration=0.4, ch=0):
    """Play one note and let go of it."""
    out(bytes([NOTE_ON | ch, number, velocity]))
    time.sleep(duration)
    out(bytes([NOTE_OFF | ch, number, 0]))


def chord(numbers, velocity=100, duration=1.0, ch=0):
    """Several notes at once. A buffer may hold as many messages as you like."""
    on = bytearray()
    off = bytearray()
    for n in numbers:
        on += bytes([NOTE_ON | ch, n, velocity])
        off += bytes([NOTE_OFF | ch, n, 0])
    out(on)
    time.sleep(duration)
    out(off)


def panic(channels=16):
    """All notes off, everywhere. Worth knowing before you need it."""
    for ch in range(channels):
        out(bytes([CONTROL_CHANGE | ch, 123, 0]))


def connected():
    """True if a Bluetooth link is up, in either direction."""
    s = blemidi.status()
    return bool(s['connected_to'] or s['centrals'])


def connect(wait_s=30):
    """Find something to send to. True once there is a link."""
    if connected():
        return True
    print("Looking for a Bluetooth MIDI instrument...")
    name = blemidi.keyboard()
    if name:
        print("connected to", name)
        return True
    print("None found. Advertising as \"Tulip Tab5\" for %d s --" % wait_s)
    print("connect to it from a Mac (Audio MIDI Setup > MIDI Studio >")
    print("Bluetooth) or from an iPad or iPhone app.")
    blemidi.peripheral()
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < wait_s * 1000:
        if connected():
            print("a central connected")
            return True
        time.sleep_ms(200)
    return False


def sent():
    """BLE packets send() has put on the air since boot.

    Bluetooth MIDI is write-without-response by design, so there is no
    per-packet acknowledgement to count the way usb_status() counts USB
    errors. A packet counted here left Tulip; a link that has dropped shows up
    as connected() going False.
    """
    return blemidi.status()['sent']


def demo():
    if blemidi is None:
        print("Bluetooth MIDI needs the Tab5; this board has no radio.")
        return
    if not connect():
        print("Nothing connected, so there is nothing to send to.")
        return
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
    # plays on the new sound. Each gets its own timestamp byte on the air.
    out(bytes([PROGRAM_CHANGE, 5, NOTE_ON, 60, 100]))
    time.sleep(0.6)
    out(bytes([NOTE_OFF, 60, 0]))
    out(bytes([PROGRAM_CHANGE, 0]))
    time.sleep(0.4)

    print("modulation wheel up and down while a note is held")
    out(bytes([NOTE_ON, 64, 100]))
    for value in list(range(0, 128, 8)) + list(range(127, -1, -8)):
        out(bytes([CONTROL_CHANGE, 1, value]))
        time.sleep(0.03)
    out(bytes([NOTE_OFF, 64, 0]))
    time.sleep(0.4)

    print("pitch bend, a 14-bit value split low byte first")
    out(bytes([NOTE_ON, 60, 100]))
    for bend in range(8192, 16384, 256):
        out(bytes([PITCH_BEND, bend & 0x7F, (bend >> 7) & 0x7F]))
        time.sleep(0.03)
    out(bytes([PITCH_BEND, 0x00, 0x40]))   # 8192, centre
    out(bytes([NOTE_OFF, 60, 0]))
    time.sleep(0.4)

    print("transport and clock: start, 2 bars at 120 BPM, stop")
    # 24 clocks to the quarter note is the MIDI standard, whatever the tempo.
    # Each clock is its own BLE packet, sent the moment it is due; the
    # timestamp inside lets the far end even out the radio's jitter.
    out(bytes([START]))
    for _tick in range(24 * 8):
        out(bytes([CLOCK]))
        time.sleep(60.0 / 120 / 24)
    out(bytes([STOP]))
    time.sleep(0.4)

    print("sysex: a universal identity request")
    # Send it whole, F0 to F7; a long one is split across packets for you.
    out(bytes([0xF0, 0x7E, 0x7F, 0x06, 0x01, 0xF7]))
    time.sleep(0.4)

    # midi_out.py ends with an AMY synth driving the far end through a note
    # output. That output goes to USB MIDI, not Bluetooth, so it has no place
    # here.

    panic()

    print("\n%d BLE packets sent; still connected: %s"
          % (sent() - before, connected()))
    print("done")


demo()
