# BLE MIDI for Tulip on the Tab5.
#
# The ESP32-P4 has no radio of its own: the BLE controller lives on the Tab5's
# ESP32-C6 and reaches us as HCI multiplexed onto the same SDIO link Wi-Fi uses
# (ESP-HOSTED). The NimBLE host runs here, so this is ordinary MicroPython
# `bluetooth` code -- nothing in it is Tab5 specific except that the firmware
# must be built with MICROPY_PY_BLUETOOTH and the hosted HCI config.
#
# Both link roles are here, because "Bluetooth MIDI" means two different things:
#
#   peripheral()  Tulip advertises as a BLE-MIDI instrument. A Mac, iPad or
#                 iPhone connects *to* Tulip and plays it. This is what you want
#                 for playing the Tab5 from a DAW or a phone keyboard app.
#   keyboard()    Tulip scans for a BLE-MIDI peripheral (a CME WIDI, a KORG
#                 microKEY Air, a Yamaha MD-BT01 dongle) and connects *to* it.
#                 This is what you want to play the Tab5 from a real keyboard.
#
# Received bytes go to tulip.midi_local(), which is AMY's own MIDI input path
# (convert_midi_bytes_to_messages -> amy_event_midi_message_received). That both
# plays the note and queues the message for midi.py's callback, so a BLE
# keyboard behaves exactly like a USB one: midi.config, kanplay, loopstudio and
# anything else watching tulip.midi_in() all see it.

import bluetooth
import time
import tulip

# Standard MIDI-over-BLE service, per the MMA/Bluetooth SIG spec. The I/O
# characteristic is notify (peripheral -> central) + write-without-response
# (central -> peripheral); both directions carry the same packet format.
MIDI_SERVICE = bluetooth.UUID('03b80e5a-ede8-4b33-a751-6ce34ec4c700')
MIDI_IO_CHAR = bluetooth.UUID('7772e5db-3868-4112-a1a9-f2669d106bf3')

_FLAG_READ = 0x0002
_FLAG_WRITE_NO_RSP = 0x0004
_FLAG_NOTIFY = 0x0010

# modbluetooth event numbers (extmod/modbluetooth.h). Spelled out rather than
# imported because micropython.const names are not exported from the module.
_CENTRAL_CONNECT = 1
_CENTRAL_DISCONNECT = 2
_GATTS_WRITE = 3
_SCAN_RESULT = 5
_SCAN_DONE = 6
_PERIPHERAL_CONNECT = 7
_PERIPHERAL_DISCONNECT = 8
_GATTC_SERVICE_RESULT = 9
_GATTC_SERVICE_DONE = 10
_GATTC_CHARACTERISTIC_RESULT = 11
_GATTC_CHARACTERISTIC_DONE = 12
_GATTC_DESCRIPTOR_RESULT = 13
_GATTC_DESCRIPTOR_DONE = 14
_GATTC_WRITE_DONE = 17
_GATTC_NOTIFY = 18

# An iOS central negotiates an MTU well past the 23-byte default and will send
# several messages per packet, so the GATT server's receive buffer has to be
# bigger than modbluetooth's 20-byte default or writes get truncated.
_RX_BUFFER = 256

# How many data bytes follow each status byte. Channel voice messages are the
# common case; the F-series entries matter because a BLE packet may carry a
# System Common or Real-Time message between note events.
def _data_len(status):
    if status < 0xc0:        # note off/on, aftertouch, control change
        return 2
    if status < 0xe0:        # program change, channel pressure
        return 1
    if status < 0xf0:        # pitch bend
        return 2
    if status == 0xf2:       # song position pointer
        return 2
    if status == 0xf1 or status == 0xf3:  # MTC quarter frame, song select
        return 1
    return 0                 # tune request, real-time, and anything else


class _Parser:
    """Turns BLE-MIDI packets into a plain MIDI byte stream.

    A packet is a 6-bit-high timestamp header, then one or more
    (timestamp byte, MIDI message) pairs. Running status is allowed: a message
    may drop its status byte and keep the previous one. Sysex is the awkward
    case -- it starts with a timestamp and F0, its payload bytes carry no
    timestamps at all and may span several packets, and only the closing F7
    gets a timestamp of its own.

    The parser is one object per link so a half-finished sysex (or a running
    status) from one peer cannot bleed into another's stream.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self._running = 0
        self._in_sysex = False
        self._partial = b''         # a message cut off at the end of a packet

    def feed(self, packet):
        if not packet:
            return b''
        out = bytearray()
        n = len(packet)
        partial = self._partial
        self._partial = b''
        if packet[0] & 0x80:
            i = 1                   # byte 0 is the header; its timestamp bits
            partial = b''           # are all we would get, and we do not use them
        else:
            # No header, so not a BLE-MIDI packet -- but the M-VAVE FM-1 sends
            # these: when a chord overflows the 20-byte payload of the default
            # MTU it cuts the packet at 20 bytes and sends the rest bare. Read
            # it as the tail of the previous packet.
            i = 0
        if partial:
            want = 1 + _data_len(partial[0]) - len(partial)
            if want > n:
                self._partial = partial + bytes(packet)
                return b''
            out += partial
            out += packet[:want]
            i = want
        while i < n:
            byte = packet[i]
            if self._in_sysex:
                if byte == 0xf7:
                    out.append(0xf7)
                    self._in_sysex = False
                    i += 1
                elif byte & 0x80:
                    i += 1          # timestamp ahead of the closing F7
                else:
                    out.append(byte)
                    i += 1
                continue

            # Outside sysex a high-bit byte here is the message's timestamp.
            if byte & 0x80:
                i += 1
                if i >= n:
                    break
                byte = packet[i]

            if byte & 0x80:         # real status byte
                status = byte
                i += 1
                if status == 0xf0:
                    out.append(0xf0)
                    self._in_sysex = True
                    self._running = 0
                    continue
                if status < 0xf0:
                    self._running = status
                elif status >= 0xf8:
                    out.append(status)   # real-time: no data bytes, keeps running status
                    continue
                else:
                    self._running = 0    # other System Common cancels running status
                want = _data_len(status)
                if i + want > n:
                    # Truncated: keep the fragment in case a bare tail follows.
                    self._partial = bytes((status,)) + bytes(packet[i:])
                    break
                out.append(status)
                for k in range(want):
                    out.append(packet[i + k])
                i += want
            else:                   # running status: data bytes only
                if not self._running:
                    i += 1          # no status to apply it to
                    continue
                want = _data_len(self._running)
                if i + want > n:
                    self._partial = bytes((self._running,)) + bytes(packet[i:])
                    break
                out.append(self._running)
                for k in range(want):
                    out.append(packet[i + k])
                i += want
        return bytes(out)


def _pack(message, now_ms=None):
    """Wrap one MIDI message as a BLE-MIDI packet (header + timestamp + bytes)."""
    if now_ms is None:
        now_ms = time.ticks_ms()
    ts = now_ms & 0x1fff            # 13-bit millisecond timestamp
    return bytes((0x80 | (ts >> 7), 0x80 | (ts & 0x7f))) + bytes(message)


class BLEMidi:
    def __init__(self, name='Tulip Tab5'):
        self.name = name
        self._ble = bluetooth.BLE()
        # Only ever bring the stack up once per boot. modbluetooth's
        # active(True) always calls mp_bluetooth_init(), which deinits first,
        # and tearing NimBLE down and back up over the hosted HCI link
        # deadlocks the host task (Interrupt wdt timeout on CPU1). There is no
        # way back from that short of a reset, so never restart it: stop()
        # drops the link and the advertisement but leaves the stack running.
        if not self._ble.active():
            self._ble.active(True)
        self._ble.config(gap_name=name)
        self._ble.irq(self._irq)

        # peripheral side
        self._handle = None
        self._centrals = set()
        self._advertising = False

        # central side
        self._conn = None
        self._value_handle = None
        self._cccd_handle = None
        self._target = None
        self._scanning = False

        self._parsers = {}
        self.messages = 0           # MIDI messages handed to AMY, for diagnosis
        self.packets = 0

    # ---- shared ----

    def _deliver(self, key, packet):
        parser = self._parsers.get(key)
        if parser is None:
            parser = _Parser()
            self._parsers[key] = parser
        data = parser.feed(packet)
        self.packets += 1
        if data:
            self.messages += 1
            # AMY's own MIDI input: plays the note and queues it for midi.py.
            tulip.midi_local(data)

    def _all_notes_off(self):
        # A BLE link can drop between a note on and its note off -- a peer that
        # writes without response and disconnects in the same breath loses the
        # write, and the note hangs forever. Anything that ends a link sends
        # CC 123 on every channel, the same thing a USB cable being pulled
        # should do.
        for channel in range(16):
            tulip.midi_local(bytes((0xb0 | channel, 0x7b, 0x00)))

    def active(self):
        return self._ble.active()

    def status(self):
        return {
            'advertising': self._advertising,
            'centrals': len(self._centrals),
            'connected_to': self._target,
            'packets': self.packets,
            'messages': self.messages,
        }

    def send(self, message):
        """Send a MIDI message to whatever is on the other end of the link."""
        packet = _pack(message)
        sent = False
        if self._handle is not None:
            for conn in tuple(self._centrals):
                try:
                    self._ble.gatts_notify(conn, self._handle, packet)
                    sent = True
                except OSError:
                    self._centrals.discard(conn)
        if self._conn is not None and self._value_handle is not None:
            # Write without response: the spec forbids expecting one.
            self._ble.gattc_write(self._conn, self._value_handle, packet, 0)
            sent = True
        return sent

    # ---- peripheral: a Mac/iPad connects to us ----

    def peripheral(self):
        if self._handle is None:
            ((self._handle,),) = self._ble.gatts_register_services(
                ((MIDI_SERVICE, ((MIDI_IO_CHAR,
                                  _FLAG_READ | _FLAG_WRITE_NO_RSP | _FLAG_NOTIFY),)),))
            self._ble.gatts_set_buffer(self._handle, _RX_BUFFER, False)
        name = self.name.encode()
        # Flags + the complete 128-bit service UUID list, which is what a
        # central filters on; the name goes in the scan response because the
        # UUID alone already fills most of the 31-byte advertisement.
        adv = b'\x02\x01\x06' + bytes((17, 0x07)) + bytes(MIDI_SERVICE)
        resp = bytes((len(name) + 1, 0x09)) + name
        self._ble.gap_advertise(100000, adv_data=adv, resp_data=resp)
        self._advertising = True
        return True

    def stop_peripheral(self):
        self._ble.gap_advertise(None)
        self._advertising = False
        self._all_notes_off()
        for conn in tuple(self._centrals):
            try:
                self._ble.gap_disconnect(conn)
            except OSError:
                pass

    # ---- central: we connect to a BLE MIDI keyboard ----

    def keyboard(self, timeout_ms=10000):
        """Scan for a BLE-MIDI peripheral and connect to the first one found."""
        self._found = None
        self._scanning = True
        self._ble.gap_scan(timeout_ms, 30000, 30000, True)
        t0 = time.ticks_ms()
        while self._scanning and time.ticks_diff(time.ticks_ms(), t0) < timeout_ms + 1000:
            time.sleep_ms(50)
        if self._found is None:
            return None
        addr_type, addr, name = self._found
        self._target = name or ':'.join('%02x' % b for b in addr)
        self._ble.gap_connect(addr_type, addr, 6000, 7500, 15000)
        t0 = time.ticks_ms()
        while self._value_handle is None and time.ticks_diff(time.ticks_ms(), t0) < 12000:
            time.sleep_ms(50)
        if self._value_handle is None:
            self._target = None
            return None
        return self._target

    def disconnect(self):
        if self._conn is not None:
            try:
                self._ble.gap_disconnect(self._conn)
            except OSError:
                pass

    @staticmethod
    def _adv_fields(adv):
        i = 0
        while i + 1 < len(adv):
            ln = adv[i]
            if ln == 0:
                break
            yield adv[i + 1], adv[i + 2:i + 1 + ln]
            i += 1 + ln

    def _is_midi_adv(self, adv):
        want = bytes(MIDI_SERVICE)
        for t, payload in self._adv_fields(adv):
            if t in (0x06, 0x07) and want in payload:
                return True
        return False

    @staticmethod
    def _adv_name(adv):
        for t, payload in BLEMidi._adv_fields(adv):
            if t in (0x08, 0x09):
                try:
                    return bytes(payload).decode()
                except Exception:
                    return None
        return None

    # ---- events ----

    def _irq(self, event, data):
        if event == _GATTS_WRITE:
            conn, attr = data
            if attr == self._handle:
                self._deliver(('c', conn), self._ble.gatts_read(attr))

        elif event == _CENTRAL_CONNECT:
            conn, _, _ = data
            self._centrals.add(conn)
            self._parsers.pop(('c', conn), None)

        elif event == _CENTRAL_DISCONNECT:
            conn, _, _ = data
            self._centrals.discard(conn)
            self._parsers.pop(('c', conn), None)
            self._all_notes_off()
            if self._advertising:
                self.peripheral()       # go back to advertising for the next one

        elif event == _SCAN_RESULT:
            addr_type, addr, adv_type, rssi, adv = data
            if self._found is None and self._is_midi_adv(bytes(adv)):
                self._found = (addr_type, bytes(addr), self._adv_name(bytes(adv)))
                self._ble.gap_scan(None)

        elif event == _SCAN_DONE:
            self._scanning = False

        elif event == _PERIPHERAL_CONNECT:
            conn, _, _ = data
            self._conn = conn
            self._ble.gattc_discover_services(conn, MIDI_SERVICE)

        elif event == _PERIPHERAL_DISCONNECT:
            self._all_notes_off()
            self._conn = None
            self._value_handle = None
            self._cccd_handle = None
            self._target = None
            self._parsers.pop(('p',), None)

        elif event == _GATTC_SERVICE_RESULT:
            conn, start, end, uuid = data
            if uuid == MIDI_SERVICE:
                self._svc_range = (start, end)

        elif event == _GATTC_SERVICE_DONE:
            if self._conn is not None and getattr(self, '_svc_range', None):
                start, end = self._svc_range
                self._ble.gattc_discover_characteristics(self._conn, start, end)

        elif event == _GATTC_CHARACTERISTIC_RESULT:
            conn, end_handle, value_handle, properties, uuid = data
            if uuid == MIDI_IO_CHAR:
                self._value_handle = value_handle
                self._char_end = end_handle

        elif event == _GATTC_CHARACTERISTIC_DONE:
            if self._conn is not None and self._value_handle is not None:
                self._ble.gattc_discover_descriptors(
                    self._conn, self._value_handle, getattr(self, '_char_end', self._value_handle + 2))

        elif event == _GATTC_DESCRIPTOR_RESULT:
            conn, dsc_handle, uuid = data
            if uuid == bluetooth.UUID(0x2902):     # client characteristic config
                self._cccd_handle = dsc_handle

        elif event == _GATTC_DESCRIPTOR_DONE:
            # Subscribing is what starts the keyboard sending us notes.
            handle = self._cccd_handle
            if handle is None and self._value_handle is not None:
                handle = self._value_handle + 1    # the usual layout, if discovery missed it
            if self._conn is not None and handle is not None:
                try:
                    self._ble.gattc_write(self._conn, handle, b'\x01\x00', 1)
                    self._cccd_written = handle
                except OSError:
                    pass

        elif event == _GATTC_WRITE_DONE:
            conn, handle, status = data
            if handle == getattr(self, '_cccd_written', None):
                self._cccd_written = None
                # Ask for a bigger MTU, as a Mac or iPad does. At the default 23
                # a keyboard has 20 bytes per packet, and a chord sent with
                # running status does not fit. Done after subscribing, so a
                # peer that never answers cannot hold up the notes.
                try:
                    self._ble.gattc_exchange_mtu(conn)
                except OSError:
                    pass

        elif event == _GATTC_NOTIFY:
            conn, value_handle, notify_data = data
            if value_handle == self._value_handle:
                self._deliver(('p',), bytes(notify_data))


_midi = None


def _get():
    global _midi
    if _midi is None:
        _midi = BLEMidi()
    return _midi


def peripheral(name=None):
    """Advertise Tulip as a BLE-MIDI instrument for a Mac/iPad to connect to."""
    global _midi
    if _midi is None:
        _midi = BLEMidi(name) if name else BLEMidi()
    return _get().peripheral()


def keyboard(timeout_ms=10000):
    """Find and connect to a BLE-MIDI keyboard. Returns its name, or None."""
    return _get().keyboard(timeout_ms)


def send(message):
    return _get().send(message)


def status():
    return _get().status()


def stop():
    m = _get()
    m.stop_peripheral()
    m.disconnect()
