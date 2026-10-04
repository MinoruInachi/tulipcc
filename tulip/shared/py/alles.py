# alles.py -- drive a mesh of Alles speakers from Tulip.
#
# Alles nodes are AMY synths that listen on a UDP multicast group and play
# whatever wire messages arrive. Tulip only ever *controls* them: it never
# answers a sync request and never claims a client number of its own, so
# nothing here makes Tulip a node.
#
# This used to be a C transport (tulip/shared/alles.c plus a per-port
# multicast.c, with tulip.alles_send() / tulip.alles_map() bindings). All of
# that went away in 2025 when Tulip moved onto AMY's current API -- and it had
# already been #if 0'd for a while before that. It comes back as plain Python
# because that is all it ever needed: the payload is the AMY wire string and
# nothing else, so socket.sendto() is the whole transport. One file then
# covers Tulip CC, the Tab5 and desktop.
#
# What the wire looks like, confirmed against live nodes:
#
#   controller -> group   U<our_ms>i<index>Z          a sync request
#   node       -> group   _U<node_ms>i<index>g<client>r<quartet>y0Z
#   node       -> group   _U<node_ms>i-1g<client>r<quartet>y0Z    every ~10 s
#
# The leading '_' marks a message as node bookkeeping rather than something to
# play, `i-1` an unsolicited ping rather than an answer, and `r` is the last
# octet of the node's address. AMY's own parser does not read `U`, `i`, `g` or
# `r` at all -- parse.c only reserves those letters with a comment -- so every
# field above is handled by the node firmware, and by map()/sync() here.

import amy
import time as _time

try:
    import socket as _socket
except ImportError:  # web/Emscripten has no sockets; import stays harmless
    _socket = None

GROUP = "232.10.11.12"
PORT = 9294

# The latency an Alles node adds to everything it is told to play, matching
# ALLES_LATENCY_MS in the reference alles.py. Two consequences, and both of
# them have bitten this file:
#
#  * Anything a node *schedules* happens this long after the host time it
#    carries, while a patch load takes effect the moment it arrives. So after
#    a destructive reset you must wait out this latency before defining
#    instruments, or the reset lands on top of them -- see alles_demo's
#    SETUP_MS. Measured against Tulip's own AMY the wait looked like 300 ms,
#    because a local AMY has no latency at all.
#  * It is also what makes the nodes play together: they schedule against the
#    host clock we stamp on every message, not against their own arrival time,
#    so the latency is the budget the network has to deliver inside.
ALLES_LATENCY_MS = 1000

# How long to let a fresh group join settle before trusting a read. See
# _get_rx(); 200 ms was comfortably enough on a home LAN.
SETTLE_MS = 200

_tx = None            # the send socket, open while meshed
_rx = None            # the receive socket, kept joined once created
_saved_override = None # amy.override_send as we found it
_meshed = False
_local = True
_local_ip = None
_sync_index = 0


def _pause(ms):
    if hasattr(_time, "sleep_ms"):
        _time.sleep_ms(ms)
    else:
        _time.sleep(ms / 1000)


def _ip_bytes(dotted):
    # socket.inet_aton is not on every MicroPython port; four bytes is four
    # bytes, and this form is what setsockopt wants on both.
    return bytes(int(p) for p in dotted.split("."))


def _outbound_ip():
    # The address of the interface a packet to the group would leave by.
    #
    # tulip.ip() is the answer on hardware, and the one we prefer there. It is
    # not enough on its own: a desktop build has no `network` module, so it
    # answers 127.0.0.1, and joining the group on loopback finds no one. For
    # that case we ask the routing table instead -- "connecting" a UDP socket
    # sends nothing, it only fixes the route, and getsockname() then names the
    # interface. MicroPython's socket has no getsockname(), hence the guard;
    # on those builds tulip.ip() is the only route and is also the right one.
    try:
        import tulip
        ip = tulip.ip()
    except (ImportError, AttributeError):
        ip = None
    if ip is not None and not ip.startswith("127."):
        return ip
    if _socket is not None and hasattr(_socket.socket, "getsockname"):
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        try:
            s.connect(_socket.getaddrinfo(GROUP, PORT)[0][-1])
            found = s.getsockname()[0]
            if found and not found.startswith("127."):
                return found
        except OSError:
            pass
        finally:
            s.close()
    return ip


def _group_addr():
    return _socket.getaddrinfo(GROUP, PORT)[0][-1]


def _get_rx():
    """The receive socket, created and joined to the group on first use.

    Kept rather than reopened per call, because IP_ADD_MEMBERSHIP is not
    instant: the IGMP join has to reach the switch before group traffic comes
    back, and a socket opened and used immediately misses the first round
    entirely (measured: the first map() after a fresh join saw nothing, every
    later one saw both nodes). Joining once and holding it also means a
    multi-round sync() pays that cost once. off() releases it.

    Our own sent packets arrive here too, which is why every reader skips
    anything that does not start with '_'.
    """
    global _rx
    if _rx is not None:
        return _rx
    ip = _local_ip or _outbound_ip()
    if ip is None:
        raise OSError("no local IP; pass local_ip= or join wifi first")
    s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
    s.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
    s.bind(("", PORT))
    s.setsockopt(_socket.IPPROTO_IP, _socket.IP_ADD_MEMBERSHIP,
                 _ip_bytes(GROUP) + _ip_bytes(ip))
    s.settimeout(0.05)
    _rx = s
    _pause(SETTLE_MS)
    return _rx


# A send can come back ENOMEM when lwIP's transmit path is momentarily full.
# Measured on a Tab5: 3 of 200 back-to-back datagrams failed that way, and
# nothing failed at all with 5 ms between sends -- so the pause is 5 ms, and
# six tries give the stack 30 ms to drain, which still fits inside any musical
# step. A failure here means "in a moment", not "give up": a dropped note-off
# is a note left ringing in another room with nothing to stop it.
#
# It does not always clear. One run right after a reflash dropped 27 sends in
# its first nine seconds and none in the fifty after, and that episode has not
# reproduced since -- not from a cold socket, not straight after a boot, not
# under local rendering. So retrying is a mitigation, not a cure, and nothing
# here may depend on a datagram arriving: see the pad envelope in alles_demo,
# which decays to silence on its own so a lost note-off cannot leave a note
# sounding. Failures that survive every retry are counted rather than printed
# per packet, because they arrive in bursts and would bury the console.
_SEND_TRIES = 6
_SEND_PAUSE_MS = 5
send_failures = 0


def _stamp(m, at_ms=None):
    """Prefix the host time a node should schedule this message for.

    AMY's own wire protocol dropped the absolute time field, but an Alles node
    still reads `t`, and that is the whole reason two of them can play in
    step: each one keeps an offset between this clock and its own and
    schedules against ours. Without the prefix a node plays the message when
    it happens to arrive, which is as tight as the network that minute.

    Sequencer messages (`H`) are scheduled by tick instead and must not be
    stamped -- the reference alles.py makes the same exception.

    The clock is amy.millis(), the same one the reference stamps with: ms
    since boot on Tulip, ms since midnight on a desktop. Which clock it is
    matters less than everything agreeing on one, because a node sets its
    offset from the first `t` it sees and keeps it. Feed it stamps from two
    different epochs and it schedules events at nonsense times -- far enough
    out and they never fire, and a node can be left sounding with nothing
    listening. amy.ticks_ms() is not a substitute: it reads 0 on a CPython
    host that has not started AMY, which is exactly how this went wrong.
    """
    if m.startswith("H"):
        return m
    return "t%d" % (amy.millis() if at_ms is None else at_ms) + m


def _datagram(m, dest):
    global send_failures
    b = m.encode()
    for attempt in range(_SEND_TRIES):
        try:
            _tx.sendto(b, dest)
            return True
        except OSError as e:
            last = e
            if attempt < _SEND_TRIES - 1:
                _pause(_SEND_PAUSE_MS)
    send_failures += 1
    if send_failures == 1 or send_failures % 25 == 0:
        print("[alles] send dropped after %d tries (%s); %d so far"
              % (_SEND_TRIES, last, send_failures))
    return False


def _mesh_send(m):
    # Installed as amy.override_send, so everything that already makes sound
    # on Tulip -- synth.py, the sequencer, a sketch calling amy.send() -- goes
    # out to the mesh without knowing about it.
    _datagram(_stamp(m), _group_addr())
    if _local:
        amy._send_wire(m)


def mesh(local=True, local_ip=None, latency_ms=None):
    """Start sending AMY messages to the mesh as well as (or instead of) here.

    local=False silences Tulip's own speaker and makes it a pure controller.
    local_ip names the interface to send from, for a host with more than one.
    latency_ms, if given, is sent to the nodes -- see the note in sync()
    before reaching for it.
    """
    global _tx, _saved_override, _meshed, _local, _local_ip
    if _socket is None:
        print("[alles] no socket module on this build")
        return False
    ip = local_ip or _outbound_ip()
    if ip is None:
        print("[alles] need to be on wifi. Use tulip.wifi('ssid', 'password').")
        return False
    _local_ip = local_ip
    _local = local
    if not _meshed:
        _tx = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        previous = amy.override_send
        # Never remember our own hook as the thing to put back. A script that
        # dies between mesh() and off() leaves _mesh_send installed; the next
        # mesh() would then save it as "what was there before", and off()
        # would restore the mesh instead of ending it -- for good, since every
        # later round trip saves it again. A reimported module has a fresh
        # _mesh_send, so compare by name rather than by identity.
        if getattr(previous, "__name__", None) == "_mesh_send":
            previous = None
        _saved_override = previous
        amy.override_send = _mesh_send
        _meshed = True
    print("[alles] mesh on, sending from %s to %s:%d, local playback %s"
          % (ip, GROUP, PORT, "on" if _local else "off"))
    if latency_ms is not None:
        send(latency_ms=latency_ms)
    return True


def off():
    """Stop sending to the mesh, put amy.override_send back, drop the sockets."""
    global _tx, _rx, _saved_override, _meshed
    if _meshed:
        amy.override_send = _saved_override
        _saved_override = None
        _meshed = False
    if _tx is not None:
        _tx.close()
        _tx = None
    if _rx is not None:
        _rx.close()
        _rx = None
    print("[alles] mesh off")


def on():
    """Whether the mesh is currently being sent to."""
    return _meshed


def local(enable=None):
    """Read, or set, whether Tulip also plays what it sends to the mesh."""
    global _local
    if enable is not None:
        _local = bool(enable)
    return _local


def send(retries=1, to=None, at_ms=None, **kwargs):
    """Send one AMY message to the mesh, or to named nodes.

    Takes everything amy.send() takes. retries>1 repeats the datagram:
    multicast is not reliable, and a dropped note-on is silence until the next
    one.

    client= addresses one node by the number it reports in map(); AMY's own
    parser never reads that field, the node firmware does, so it works only on
    nodes that implement it (the Alles firmware does).

    to= names one node address, or a list of them, and sends unicast instead
    of to the group. Useful when a node does not read client=: a node answers
    a sync request sent straight to it and no other node does, so delivery is
    per-node either way. client= is the cheaper of the two -- one datagram
    reaches every node rather than one per node.

    at_ms is the host time the nodes should play this at, in the same clock as
    amy.millis(). Leave it out for "as soon as you can"; give it a time a
    little ahead to have several nodes sound together. Either way the node
    adds ALLES_LATENCY_MS on top.
    """
    if not _meshed:
        print("[alles] not meshed; call alles.mesh() first")
        return
    m = _stamp(amy.message(**kwargs), at_ms)
    if to is None:
        dests = (_group_addr(),)
    elif isinstance(to, str):
        dests = ((to, PORT),)
    else:
        dests = tuple((ip, PORT) for ip in to)
    for _ in range(max(1, retries)):
        for dest in dests:
            _datagram(m, dest)
    # Once, not once per retry: the retries are there to survive a dropped
    # datagram, and the local speaker never dropped anything. Unstamped,
    # because Tulip's own AMY is the one that dropped `t` from the wire -- it
    # would reject the prefix, and it has no latency to schedule against
    # anyway.
    if _local:
        amy._send_wire(amy.message(**kwargs))


def local_send(**kwargs):
    """Play one message on Tulip's own AMY only, never on the mesh.

    amy.send() cannot do this while meshed -- override_send is installed, so
    it goes out to the group. Needed when Tulip has to be told something
    different from the nodes: alles_demo gives its local monitor a smaller
    voice count than it gives the speakers, because Tulip is also driving a
    display and a radio and cannot carry the same polyphony.
    """
    amy._send_wire(amy.message(**kwargs))


def _parse_reply(data):
    # '_U<ms>i<index>g<client>r<quartet>y<n>Z' -> {'U':.., 'i':.., ...}.
    # Parsed generically by letter so a node that adds a field of its own does
    # not break us: any single letter starts a field, the digits (with a sign)
    # up to the next letter are its value.
    try:
        s = data.decode()
    except (UnicodeError, AttributeError):
        return None
    if not s.startswith("_"):
        return None   # a playable message, or our own packet looped back
    fields = {}
    key = None
    start = 0
    for i in range(1, len(s) + 1):
        ch = s[i] if i < len(s) else "Z"
        if ("a" <= ch <= "z") or ("A" <= ch <= "Z"):
            if key is not None:
                try:
                    fields[key] = int(s[start:i])
                except ValueError:
                    pass
            key = ch
            start = i + 1
    return fields


def _collect(timeout_ms, index):
    # One round of node replies: sends a sync request, then reads until the
    # timeout. Returns (t0, {quartet: (fields, rtt_ms, ip)}), where t0 is the
    # clock reading taken immediately before the request went out -- sync()
    # needs that exact instant, so opening the receive socket (which has to
    # happen first, or we would miss the fastest answer) must not be counted
    # into the round trip.
    s = _get_rx()
    out = {}
    t0 = amy.ticks_ms()
    # map() and sync() work without mesh(), so borrow a socket when there is
    # no standing one to send from.
    tx = _tx if _meshed else _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
    tx.sendto(("U%di%dZ" % (t0, index)).encode(), _group_addr())
    if tx is not _tx:
        tx.close()
    end = _time.ticks_add(_time.ticks_ms(), timeout_ms)
    while _time.ticks_diff(end, _time.ticks_ms()) > 0:
        try:
            data, addr = s.recvfrom(256)
        except OSError:
            continue
        f = _parse_reply(data)
        if f is None:
            continue
        if f.get("i", -1) != index:
            # Not an answer to this request: either a node's unsolicited
            # 10-second ping (i-1) or an answer to an earlier round.
            continue
        rtt = amy.ticks_ms() - t0
        # Key on the node's own `r` so two replies from one node collapse;
        # fall back to the address it came from if a node omits the field.
        q = f.get("r")
        if q is None:
            q = int(addr[0].split(".")[-1])
        out[q] = (f, rtt, addr[0])
    return t0, out


def map(timeout_ms=500):
    """Who is on the mesh, as a list of (client, ip, clock_ms).

    Sorted by client number. Each node reports its own client number in its
    pings (the `g` field), which is what client= addresses; the clock is the
    node's AMY millisecond clock, so the longest-booted node has the largest.

    This asks, rather than listens: nodes announce themselves unprompted only
    every 10 seconds, so a listen-only discovery would have to wait that long
    to be sure it had seen everyone, where a sync request is answered in
    milliseconds (6 ms round trip, measured from a Tab5 on a home LAN).
    """
    if _socket is None:
        print("[alles] no socket module on this build")
        return None
    global _sync_index
    # Asked up to three times: the request and every answer is a single
    # datagram on a best-effort transport, so an empty round means "ask
    # again", not "nobody is there". Three because a two-node mesh was once
    # seen to answer with only one node.
    for _attempt in range(3):
        _sync_index = (_sync_index + 1) % 100
        _t0, found = _collect(timeout_ms, _sync_index)
        if found:
            break
    rows = []
    for q, (f, _rtt, ip) in found.items():
        rows.append((f.get("g", -1), ip, f.get("U", 0)))
    rows.sort()
    return rows


def alive(timeout_ms=500):
    """How many nodes answered."""
    rows = map(timeout_ms)
    return 0 if rows is None else len(rows)


def sync(rounds=5, timeout_ms=500):
    """Measure each node's clock against ours.

    Returns {ip: {'client', 'clock_ms', 'offset_ms', 'rtt_ms'}}. offset_ms is
    how far the node's AMY clock is ahead of ours, estimated the way NTP does
    it -- the reply is assumed to have been written half an RTT ago -- and
    kept from the round with the smallest RTT, which is the least distorted
    sample.

    Keyed by address, not by client number: a client number is a label the
    nodes negotiate between themselves, and it is neither unique nor stable
    while they are settling. Two nodes fresh out of a reboot both answered as
    client 0 here, and keying on that merged them into one row and invented a
    second -- the address is the identity, the client number is reported
    alongside it.

    This measures the skew; it does not fix it. Aligning playback needs a time
    field on the wire for the node to schedule against, and AMY removed one:
    parse.c still carries the line "t no longer used (was time=)". latency_ms
    only delays a node from the moment a message reaches *it*, so it buys a
    cushion against jitter, not a shared instant. Driving the nodes'
    sequencers (a common tempo plus ticks=, with the transport started
    together) is the route that modern AMY does support.
    """
    if _socket is None:
        print("[alles] no socket module on this build")
        return None
    global _sync_index
    best = {}
    for _ in range(max(1, rounds)):
        _sync_index = (_sync_index + 1) % 100
        t0, found = _collect(timeout_ms, _sync_index)
        for q, (f, rtt, ip) in found.items():
            prev = best.get(ip)
            if prev is not None and prev["rtt_ms"] <= rtt:
                continue
            clock = f.get("U", 0)
            best[ip] = {
                "client": f.get("g", -1),
                "clock_ms": clock,
                # The node wrote `clock` roughly rtt/2 after we sent at t0.
                "offset_ms": clock - (t0 + rtt // 2),
                "rtt_ms": rtt,
            }
    return best
