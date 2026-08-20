# An SSH-2 client for Tulip, in MicroPython.
#
# Why this is Python and not a C library: the two obvious C candidates (libssh2
# with the mbedTLS backend, wolfSSH) are 150-250KB of flash, and the TAB5 app
# slot has about 230KB free -- taking one would mean re-cutting the partition
# table, which changes the OTA layout under everyone's feet. The crypto this
# needs is either already in the firmware (AES and SHA-256 from mbedTLS, via
# cryptolib and hashlib) or cheap enough to do in Python: one X25519 scalar
# multiplication measured 338ms on the P4, and an RSA-2048 signature check is a
# single three-argument pow() at 35ms. Both happen once per connection.
#
# So the cipher suite is fixed, and deliberately minimal:
#
#   kex        curve25519-sha256   -- X25519 below; the group-14 DH alternative
#                                     is a 2048-bit modexp with a 2048-bit
#                                     exponent, which is minutes in Python
#   host key   rsa-sha2-256        -- verifiable with pow(); ssh-ed25519 is not,
#                                     because mbedTLS has no EdDSA and hashlib
#                                     here has no SHA-512, so Ed25519 would mean
#                                     writing both from scratch
#   cipher     aes128-ctr          -- cryptolib, C speed
#   mac        hmac-sha2-256       -- hashlib, C speed
#
# OpenSSH still ships an RSA host key by default, so rsa-sha2-256 connects to a
# stock server. A server with only an Ed25519 host key will fail in _kex() with
# a clear message rather than silently skipping verification.
#
# Usage:
#     import ssh
#     ssh.shell('192.168.1.10', 'user', 'password')   # interactive, on the TFB
#     print(ssh.run('192.168.1.10', 'user', 'password', 'uname -a'))

import socket
import os
import hashlib
import cryptolib
import binascii

VERSION = b'SSH-2.0-Tulip_1.0'

# RFC 4253 and friends.
MSG_DISCONNECT = 1
MSG_IGNORE = 2
MSG_UNIMPLEMENTED = 3
MSG_DEBUG = 4
MSG_SERVICE_REQUEST = 5
MSG_SERVICE_ACCEPT = 6
MSG_EXT_INFO = 7
MSG_KEXINIT = 20
MSG_NEWKEYS = 21
MSG_KEX_ECDH_INIT = 30
MSG_KEX_ECDH_REPLY = 31
MSG_USERAUTH_REQUEST = 50
MSG_USERAUTH_FAILURE = 51
MSG_USERAUTH_SUCCESS = 52
MSG_USERAUTH_BANNER = 53
MSG_USERAUTH_INFO_REQUEST = 60
MSG_USERAUTH_INFO_RESPONSE = 61
MSG_GLOBAL_REQUEST = 80
MSG_REQUEST_FAILURE = 82
MSG_CHANNEL_OPEN = 90
MSG_CHANNEL_OPEN_CONFIRMATION = 91
MSG_CHANNEL_OPEN_FAILURE = 92
MSG_CHANNEL_WINDOW_ADJUST = 93
MSG_CHANNEL_DATA = 94
MSG_CHANNEL_EXTENDED_DATA = 95
MSG_CHANNEL_EOF = 96
MSG_CHANNEL_CLOSE = 97
MSG_CHANNEL_REQUEST = 98
MSG_CHANNEL_SUCCESS = 99
MSG_CHANNEL_FAILURE = 100

_KEX_ALGS = b'curve25519-sha256,curve25519-sha256@libssh.org'
_HOSTKEY_ALGS = b'rsa-sha2-256'
_CIPHERS = b'aes128-ctr'
_MACS = b'hmac-sha2-256'
_AES_CTR = 6  # cryptolib mode number


class SSHError(Exception):
    pass


class HostKeyError(SSHError):
    pass


# ---------------------------------------------------------------- wire format

def _u32(n):
    return bytes((n >> 24 & 255, n >> 16 & 255, n >> 8 & 255, n & 255))


def _string(b):
    if isinstance(b, str):
        b = b.encode()
    return _u32(len(b)) + b


def _int_to_bytes(n, length):
    # int.to_bytes() with a length argument is not reliably available for
    # arbitrary-precision ints across MicroPython builds, and this is only ever
    # called on a handful of values per connection.
    out = bytearray(length)
    for i in range(length - 1, -1, -1):
        out[i] = n & 0xFF
        n >>= 8
    return bytes(out)


def _bit_length(n):
    # int.bit_length() is a CPython builtin MicroPython does not have, and
    # every caller here asks about a key-sized number, so step down in 16-bit
    # chunks first rather than shifting a 2048-bit value one bit at a time.
    bits = 0
    while n >= 0x10000:
        n >>= 16
        bits += 16
    while n:
        n >>= 1
        bits += 1
    return bits


def _mpint(n):
    if n == 0:
        return _u32(0)
    # One more byte than the value strictly needs, so the top bit is never set
    # and the number never reads as negative.
    return _string(_int_to_bytes(n, (_bit_length(n) // 8) + 1))


class _Reader:
    def __init__(self, data):
        self.d = data
        self.i = 0

    def byte(self):
        self.i += 1
        return self.d[self.i - 1]

    def u32(self):
        i = self.i
        self.i = i + 4
        return int.from_bytes(self.d[i:i + 4], 'big')

    def string(self):
        n = self.u32()
        i = self.i
        self.i = i + n
        return self.d[i:i + n]

    def mpint(self):
        return int.from_bytes(self.string(), 'big')

    def name_list(self):
        s = self.string()
        return s.split(b',') if s else []

    def rest(self):
        return self.d[self.i:]


# -------------------------------------------------------------------- crypto

_P = 2 ** 255 - 19
_A24 = 121665


def _x25519(scalar, u):
    """RFC 7748 X25519. About 340ms on an ESP32-P4."""
    k = bytearray(scalar)
    k[0] &= 248
    k[31] &= 127
    k[31] |= 64
    k = int.from_bytes(bytes(k), 'little')
    x1 = u
    x2, z2, x3, z3 = 1, 0, u, 1
    swap = 0
    for t in range(254, -1, -1):
        kt = (k >> t) & 1
        swap ^= kt
        if swap:
            x2, x3 = x3, x2
            z2, z3 = z3, z2
        swap = kt
        a = (x2 + z2) % _P
        aa = a * a % _P
        b = (x2 - z2) % _P
        bb = b * b % _P
        e = (aa - bb) % _P
        c = (x3 + z3) % _P
        d = (x3 - z3) % _P
        da = d * a % _P
        cb = c * b % _P
        x3 = (da + cb) % _P
        x3 = x3 * x3 % _P
        z3 = (da - cb) % _P
        z3 = x1 * z3 % _P * z3 % _P
        x2 = aa * bb % _P
        z2 = e * (aa + _A24 * e) % _P
    if swap:
        x2, x3 = x3, x2
        z2, z3 = z3, z2
    return x2 * pow(z2, _P - 2, _P) % _P


def _int_to_bytes_le(n, length):
    out = bytearray(length)
    for i in range(length):
        out[i] = n & 0xFF
        n >>= 8
    return bytes(out)


def _x25519_bytes(scalar, u_bytes):
    # X25519 speaks little-endian throughout (RFC 7748), so this builds the
    # result that way directly -- MicroPython has no [::-1] to reverse it with.
    u = int.from_bytes(u_bytes, 'little') & ((1 << 255) - 1)
    return _int_to_bytes_le(_x25519(scalar, u), 32)


def _hmac_sha256(key, msg):
    if len(key) > 64:
        key = hashlib.sha256(key).digest()
    ipad = bytearray(64)
    opad = bytearray(64)
    for i in range(64):
        k = key[i] if i < len(key) else 0
        ipad[i] = k ^ 0x36
        opad[i] = k ^ 0x5C
    inner = hashlib.sha256(bytes(ipad) + msg).digest()
    return hashlib.sha256(bytes(opad) + inner).digest()


# PKCS#1 v1.5 DigestInfo prefix for SHA-256.
_SHA256_DIGESTINFO = (b'\x30\x31\x30\x0d\x06\x09\x60\x86\x48\x01\x65'
                      b'\x03\x04\x02\x01\x05\x00\x04\x20')


def _rsa_verify(n, e, sig, message):
    """PKCS#1 v1.5 verify over `message`. One pow(), 35ms at 2048 bits.

    The message here is the exchange hash H, and PKCS#1 signs the SHA-256 *of*
    it -- H is what gets signed, not what goes in the DigestInfo.
    """
    digest = hashlib.sha256(message).digest()
    k = (_bit_length(n) + 7) // 8
    if len(sig) != k:
        return False
    m = pow(int.from_bytes(sig, 'big'), e, n)
    em = _int_to_bytes(m, k)
    expect = (b'\x00\x01' + b'\xff' * (k - 3 - len(_SHA256_DIGESTINFO) - 32) +
              b'\x00' + _SHA256_DIGESTINFO + digest)
    return em == expect


def _rsa_sign(k, message):
    """PKCS#1 v1.5 sign with SHA-256, through the CRT.

    The CRT halves are two 1024-bit exponentiations instead of one 2048-bit
    one, which is about four times less work -- worth having when the whole
    thing is Python bignums.
    """
    n = k['n']
    size = (_bit_length(n) + 7) // 8
    digest = hashlib.sha256(message).digest()
    em = (b'\x00\x01' + b'\xff' * (size - 3 - len(_SHA256_DIGESTINFO) - 32) +
          b'\x00' + _SHA256_DIGESTINFO + digest)
    m = int.from_bytes(em, 'big')
    p, q = k['p'], k['q']
    if p and q:
        mp = pow(m % p, k['d'] % (p - 1), p)
        mq = pow(m % q, k['d'] % (q - 1), q)
        s = mq + ((k['iqmp'] * (mp - mq)) % p) * q
    else:
        s = pow(m, k['d'], n)
    return _int_to_bytes(s, size)


def load_key(path):
    """Read an unencrypted OpenSSH RSA private key ("openssh-key-v1")."""
    with open(path) as f:
        body = []
        for line in f:
            line = line.strip()
            if line and not line.startswith('-----'):
                body.append(line)
    raw = binascii.a2b_base64(''.join(body))
    if not raw.startswith(b'openssh-key-v1\x00'):
        raise SSHError('%s is not an OpenSSH private key' % path)
    r = _Reader(raw[15:])
    cipher = r.string()
    r.string()  # kdf name
    r.string()  # kdf options
    if cipher != b'none':
        raise SSHError('%s is passphrase-protected; this client cannot decrypt '
                       'it (ssh-keygen -p removes the passphrase)' % path)
    if r.u32() != 1:
        raise SSHError('%s holds more than one key' % path)
    blob = r.string()
    priv = _Reader(r.string())
    priv.u32()
    priv.u32()
    kind = priv.string()
    if kind != b'ssh-rsa':
        raise SSHError('%s is a %s key; only ssh-rsa can be used here'
                       % (path, kind.decode()))
    # The private half stores n, e, d, iqmp, p, q -- a different order from the
    # public blob, which is e then n.
    n = priv.mpint()
    priv.mpint()
    d = priv.mpint()
    iqmp = priv.mpint()
    pp = priv.mpint()
    qq = priv.mpint()
    return {'blob': blob, 'n': n, 'd': d, 'p': pp, 'q': qq, 'iqmp': iqmp}


# --------------------------------------------------------------- known hosts

def _known_hosts_path():
    for d in ('/user', '/sys'):
        try:
            os.stat(d)
            return d + '/known_hosts'
        except OSError:
            pass
    return 'known_hosts'


def _fingerprint(host_key):
    h = hashlib.sha256(host_key).digest()
    return ''.join('%02x' % b for b in h)


def _check_known_host(host, host_key, accept_new):
    path = _known_hosts_path()
    fp = _fingerprint(host_key)
    try:
        f = open(path)
    except OSError:
        f = None
    if f:
        try:
            for line in f:
                parts = line.split()
                if len(parts) == 2 and parts[0] == host:
                    if parts[1] == fp:
                        return
                    raise HostKeyError(
                        'host key for %s changed! expected %s got %s (fix %s)'
                        % (host, parts[1], fp, path))
        finally:
            f.close()
    if not accept_new:
        raise HostKeyError('unknown host %s (%s)' % (host, fp))
    print('ssh: new host %s, fingerprint sha256 %s' % (host, fp))
    try:
        with open(path, 'a') as f:
            f.write('%s %s\n' % (host, fp))
    except OSError as ex:
        print('ssh: could not record host key in %s (%s)' % (path, ex))


# ------------------------------------------------------------------ transport

class _Transport:
    def __init__(self, sock):
        self.sock = sock
        self.in_seq = 0
        self.out_seq = 0
        self.in_cipher = None
        self.out_cipher = None
        self.in_mac = None
        self.out_mac = None
        self.in_block = 8
        self.out_block = 8

    def _recv_exact(self, n):
        if n <= 0:
            # A 12-byte packet is entirely inside the first cipher block, so
            # there is nothing left to read -- USERAUTH_SUCCESS is one.
            return b''
        chunks = []
        got = 0
        while got < n:
            b = self.sock.recv(n - got)
            if not b:
                raise SSHError('connection closed by server')
            chunks.append(b)
            got += len(b)
        return b''.join(chunks) if len(chunks) > 1 else chunks[0]

    def _send_all(self, data):
        view = memoryview(data)
        sent = 0
        while sent < len(data):
            sent += self.sock.send(view[sent:])

    def send(self, payload):
        bs = self.out_block
        n = len(payload) + 5
        pad = bs - (n % bs)
        if pad < 4:
            pad += bs
        pkt = (_u32(len(payload) + 1 + pad) + bytes((pad,)) + payload +
               os.urandom(pad))
        mac = b''
        if self.out_mac:
            mac = _hmac_sha256(self.out_mac, _u32(self.out_seq) + pkt)
        if self.out_cipher:
            pkt = self.out_cipher.encrypt(pkt)
        self._send_all(pkt + mac)
        self.out_seq = (self.out_seq + 1) & 0xFFFFFFFF

    def recv_raw(self):
        if self.in_cipher:
            first = self.in_cipher.decrypt(self._recv_exact(16))
            plen = int.from_bytes(first[:4], 'big')
            if plen < 12 or plen > 300000:
                raise SSHError('bad packet length %d' % plen)
            body = self.in_cipher.decrypt(self._recv_exact(plen + 4 - 16))
            pkt = first + body
            mac = self._recv_exact(32)
            if _hmac_sha256(self.in_mac, _u32(self.in_seq) + pkt) != mac:
                raise SSHError('MAC mismatch')
        else:
            head = self._recv_exact(8)
            plen = int.from_bytes(head[:4], 'big')
            if plen < 12 or plen > 300000:
                raise SSHError('bad packet length %d' % plen)
            pkt = head + self._recv_exact(plen + 4 - 8)
        self.in_seq = (self.in_seq + 1) & 0xFFFFFFFF
        padlen = pkt[4]
        return pkt[5:4 + plen - padlen]

    def recv(self):
        """A packet with the housekeeping messages already dealt with."""
        while True:
            p = self.recv_raw()
            if not p:
                continue
            t = p[0]
            if t == MSG_IGNORE or t == MSG_DEBUG or t == MSG_UNIMPLEMENTED:
                continue
            if t == MSG_EXT_INFO:
                continue
            if t == MSG_DISCONNECT:
                r = _Reader(p[1:])
                code = r.u32()
                raise SSHError('disconnected by server (%d): %s'
                               % (code, r.string().decode()))
            if t == MSG_GLOBAL_REQUEST:
                r = _Reader(p[1:])
                r.string()
                if r.byte():
                    self.send(bytes((MSG_REQUEST_FAILURE,)))
                continue
            return p


# --------------------------------------------------------------------- client

class SSHClient:
    def __init__(self, sock, host):
        self.sock = sock
        self.host = host
        self.t = _Transport(sock)
        self.chan = None
        self.remote_chan = None
        self.out_window = 0
        self.max_packet = 32768
        self.in_window = 0
        self.closed = False
        self.exit_status = None

    # -- connection setup

    def _versions(self):
        self.t._send_all(VERSION + b'\r\n')
        line = b''
        while True:
            c = self.sock.recv(1)
            if not c:
                raise SSHError('connection closed during version exchange')
            if c == b'\n':
                line = line.rstrip(b'\r')
                if line.startswith(b'SSH-'):
                    break
                line = b''  # a pre-banner line, keep reading
            else:
                line += c
        if not line.startswith(b'SSH-2.0'):
            raise SSHError('server is not SSH-2: %s' % line)
        return line

    def _kexinit_payload(self):
        return (bytes((MSG_KEXINIT,)) + os.urandom(16) +
                _string(_KEX_ALGS) + _string(_HOSTKEY_ALGS) +
                _string(_CIPHERS) + _string(_CIPHERS) +
                _string(_MACS) + _string(_MACS) +
                _string(b'none') + _string(b'none') +
                _string(b'') + _string(b'') +
                b'\x00' + _u32(0))

    def _agree(self, ours, theirs, what):
        for a in ours.split(b','):
            if a in theirs:
                return a
        raise SSHError('no common %s (we offer %s, server offers %s)'
                       % (what, ours.decode(),
                          b','.join(theirs).decode()))

    def _kex(self, host, accept_new):
        v_c = VERSION
        v_s = self._versions()

        i_c = self._kexinit_payload()
        self.t.send(i_c)
        i_s = self.t.recv()
        if i_s[0] != MSG_KEXINIT:
            raise SSHError('expected KEXINIT, got %d' % i_s[0])
        r = _Reader(i_s[17:])
        s_kex = r.name_list()
        s_hostkey = r.name_list()
        s_enc_cs = r.name_list()
        s_enc_sc = r.name_list()
        s_mac_cs = r.name_list()
        s_mac_sc = r.name_list()
        r.name_list()
        r.name_list()
        r.name_list()
        r.name_list()
        guess = r.byte()

        kex_alg = self._agree(_KEX_ALGS, s_kex, 'key exchange')
        self._agree(_HOSTKEY_ALGS, s_hostkey, 'host key algorithm')
        # Both directions: a server is free to offer different lists each way,
        # and only one cipher and one MAC are implemented here.
        self._agree(_CIPHERS, s_enc_cs, 'cipher')
        self._agree(_CIPHERS, s_enc_sc, 'cipher')
        self._agree(_MACS, s_mac_cs, 'MAC')
        self._agree(_MACS, s_mac_sc, 'MAC')
        if guess and s_kex and s_kex[0] != kex_alg:
            self.t.recv()  # the server guessed wrong; drop its guessed packet

        priv = os.urandom(32)
        q_c = _x25519_bytes(priv, b'\x09' + b'\x00' * 31)
        self.t.send(bytes((MSG_KEX_ECDH_INIT,)) + _string(q_c))

        p = self.t.recv()
        if p[0] != MSG_KEX_ECDH_REPLY:
            raise SSHError('expected KEX_ECDH_REPLY, got %d' % p[0])
        r = _Reader(p[1:])
        k_s = r.string()
        q_s = r.string()
        sig = r.string()

        secret = int.from_bytes(_x25519_bytes(priv, q_s), 'big')
        if secret == 0:
            raise SSHError('degenerate shared secret')
        k_mpint = _mpint(secret)

        h = hashlib.sha256()
        h.update(_string(v_c) + _string(v_s) + _string(i_c) + _string(i_s) +
                 _string(k_s) + _string(q_c) + _string(q_s) + k_mpint)
        exch = h.digest()

        # The host key blob is still an "ssh-rsa" blob even though the
        # signature is rsa-sha2-256 -- RFC 8332 changed the signature
        # algorithm name only.
        kr = _Reader(k_s)
        if kr.string() != b'ssh-rsa':
            raise SSHError('server sent a host key we cannot check')
        e = kr.mpint()
        n = kr.mpint()
        sr = _Reader(sig)
        alg = sr.string()
        if alg != b'rsa-sha2-256':
            raise SSHError('server signed with %s, not rsa-sha2-256'
                           % alg.decode())
        if not _rsa_verify(n, e, sr.string(), exch):
            raise HostKeyError('host key signature did not verify')
        _check_known_host(host, k_s, accept_new)

        self.session_id = exch
        self.t.send(bytes((MSG_NEWKEYS,)))
        p = self.t.recv()
        if p[0] != MSG_NEWKEYS:
            raise SSHError('expected NEWKEYS, got %d' % p[0])

        def key(letter, want):
            out = hashlib.sha256(k_mpint + exch + letter + exch).digest()
            while len(out) < want:
                out += hashlib.sha256(k_mpint + exch + out).digest()
            return out[:want]

        self.t.out_cipher = cryptolib.aes(key(b'C', 16), _AES_CTR, key(b'A', 16))
        self.t.in_cipher = cryptolib.aes(key(b'D', 16), _AES_CTR, key(b'B', 16))
        self.t.out_mac = key(b'E', 32)
        self.t.in_mac = key(b'F', 32)
        self.t.in_block = 16
        self.t.out_block = 16

    def _auth(self, user, password, key):
        self.t.send(bytes((MSG_SERVICE_REQUEST,)) + _string(b'ssh-userauth'))
        p = self.t.recv()
        if p[0] != MSG_SERVICE_ACCEPT:
            raise SSHError('server refused ssh-userauth')

        if key is not None:
            req = (bytes((MSG_USERAUTH_REQUEST,)) + _string(user) +
                   _string(b'ssh-connection') + _string(b'publickey') +
                   b'\x01' + _string(b'rsa-sha2-256') + _string(key['blob']))
            sig = _rsa_sign(key, _string(self.session_id) + req)
            self.t.send(req + _string(_string(b'rsa-sha2-256') + _string(sig)))
            if self._auth_result() is None:
                return
            if password is None:
                raise SSHError('public key authentication failed')

        if password is None:
            raise SSHError('no password and no key to authenticate with')

        self.t.send(bytes((MSG_USERAUTH_REQUEST,)) + _string(user) +
                    _string(b'ssh-connection') + _string(b'password') +
                    b'\x00' + _string(password))
        methods = self._auth_result()
        if methods is None:
            return
        # Some servers only offer the password through keyboard-interactive.
        if b'keyboard-interactive' not in methods:
            raise SSHError('password authentication failed')
        self.t.send(bytes((MSG_USERAUTH_REQUEST,)) + _string(user) +
                    _string(b'ssh-connection') + _string(b'keyboard-interactive') +
                    _string(b'') + _string(b''))
        while True:
            p = self.t.recv()
            if p[0] == MSG_USERAUTH_BANNER:
                continue
            if p[0] == MSG_USERAUTH_SUCCESS:
                return
            if p[0] == MSG_USERAUTH_FAILURE:
                raise SSHError('authentication failed')
            if p[0] == MSG_USERAUTH_INFO_REQUEST:
                r = _Reader(p[1:])
                r.string()
                r.string()
                r.string()
                count = r.u32()
                out = bytes((MSG_USERAUTH_INFO_RESPONSE,)) + _u32(count)
                for _ in range(count):
                    r.string()
                    r.byte()
                    out += _string(password)
                self.t.send(out)
                continue
            raise SSHError('unexpected message %d during auth' % p[0])

    def _auth_result(self):
        """None on success, else the list of methods the server will still take."""
        while True:
            p = self.t.recv()
            if p[0] == MSG_USERAUTH_BANNER:
                continue
            if p[0] == MSG_USERAUTH_SUCCESS:
                return None
            if p[0] == MSG_USERAUTH_FAILURE:
                return _Reader(p[1:]).name_list()
            raise SSHError('unexpected message %d during auth' % p[0])

    def _open_session(self):
        self.chan = 0
        self.in_window = 1 << 21
        self.t.send(bytes((MSG_CHANNEL_OPEN,)) + _string(b'session') +
                    _u32(self.chan) + _u32(self.in_window) + _u32(32768))
        while True:
            p = self.t.recv()
            if p[0] == MSG_CHANNEL_OPEN_CONFIRMATION:
                r = _Reader(p[1:])
                r.u32()
                self.remote_chan = r.u32()
                self.out_window = r.u32()
                self.max_packet = min(r.u32(), 32768)
                return
            if p[0] == MSG_CHANNEL_OPEN_FAILURE:
                r = _Reader(p[1:])
                r.u32()
                code = r.u32()
                raise SSHError('channel open failed (%d): %s'
                               % (code, r.string().decode()))
            raise SSHError('unexpected message %d opening channel' % p[0])

    def _channel_request(self, kind, payload, want_reply=True):
        self.t.send(bytes((MSG_CHANNEL_REQUEST,)) + _u32(self.remote_chan) +
                    _string(kind) + (b'\x01' if want_reply else b'\x00') +
                    payload)
        if not want_reply:
            return True
        while True:
            p = self.t.recv()
            if p[0] == MSG_CHANNEL_SUCCESS:
                return True
            if p[0] == MSG_CHANNEL_FAILURE:
                return False
            self._handle_channel(p)

    # -- channel i/o

    def request_pty(self, cols, rows, term='xterm'):
        return self._channel_request(
            b'pty-req',
            _string(term) + _u32(cols) + _u32(rows) + _u32(0) + _u32(0) +
            _string(b'\x00'))

    def resize(self, cols, rows):
        self._channel_request(b'window-change',
                              _u32(cols) + _u32(rows) + _u32(0) + _u32(0),
                              want_reply=False)

    def start_shell(self):
        if not self._channel_request(b'shell', b''):
            raise SSHError('server refused a shell')

    def exec_command(self, cmd):
        if not self._channel_request(b'exec', _string(cmd)):
            raise SSHError('server refused to run the command')

    def _handle_channel(self, p):
        """Returns any channel data in the packet, or b''."""
        t = p[0]
        if t == MSG_CHANNEL_DATA:
            r = _Reader(p[1:])
            r.u32()
            return self._consume(r.string())
        if t == MSG_CHANNEL_EXTENDED_DATA:
            r = _Reader(p[1:])
            r.u32()
            r.u32()
            return self._consume(r.string())
        if t == MSG_CHANNEL_WINDOW_ADJUST:
            r = _Reader(p[1:])
            r.u32()
            self.out_window += r.u32()
            return b''
        if t == MSG_CHANNEL_EOF:
            return b''
        if t == MSG_CHANNEL_CLOSE:
            self.closed = True
            return b''
        if t == MSG_CHANNEL_REQUEST:
            r = _Reader(p[1:])
            r.u32()
            kind = r.string()
            if kind == b'exit-status':
                r.byte()
                self.exit_status = r.u32()
            return b''
        return b''

    def _consume(self, data):
        # Hand the window back in big lumps rather than per packet.
        self.in_window -= len(data)
        if self.in_window < (1 << 20):
            add = (1 << 21) - self.in_window
            self.t.send(bytes((MSG_CHANNEL_WINDOW_ADJUST,)) +
                        _u32(self.remote_chan) + _u32(add))
            self.in_window += add
        return data

    def read(self):
        """One packet's worth of channel data. Blocks. b'' if there was none."""
        if self.closed:
            return b''
        return self._handle_channel(self.t.recv())

    def write(self, data):
        while data:
            n = min(len(data), self.max_packet, self.out_window)
            if n <= 0:
                # Out of window: the only thing that opens it again is a
                # WINDOW_ADJUST, so read until one turns up.
                self._handle_channel(self.t.recv())
                continue
            self.t.send(bytes((MSG_CHANNEL_DATA,)) + _u32(self.remote_chan) +
                        _string(data[:n]))
            self.out_window -= n
            data = data[n:]

    def eof(self):
        self.t.send(bytes((MSG_CHANNEL_EOF,)) + _u32(self.remote_chan))

    def close(self):
        try:
            if self.remote_chan is not None and not self.closed:
                self.t.send(bytes((MSG_CHANNEL_CLOSE,)) + _u32(self.remote_chan))
            self.t.send(bytes((MSG_DISCONNECT,)) + _u32(11) +
                        _string(b'bye') + _string(b''))
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass
        self.closed = True

def connect(host, user, password=None, key=None, port=22, accept_new=True):
    """Open a session channel on `host`. Authenticates with `key` if given
    (a path to an unencrypted OpenSSH RSA key, or what load_key() returned),
    otherwise with `password`."""
    if isinstance(key, str):
        key = load_key(key)
    addr = socket.getaddrinfo(host, port)[0][-1]
    s = socket.socket()
    s.connect(addr)
    c = SSHClient(s, host)
    try:
        # Two servers on one machine have two host keys, so known_hosts has to
        # tell them apart. OpenSSH writes the port in brackets for anything but
        # 22 and leaves 22 bare; do the same, so the file stays readable next to
        # the one on a desktop.
        c._kex(host if port == 22 else '[%s]:%d' % (host, port), accept_new)
        c._auth(user.encode() if isinstance(user, str) else user,
                password.encode() if isinstance(password, str) else password,
                key)
        c._open_session()
    except Exception:
        c.close()
        raise
    return c


def run(host, user, command, password=None, key=None, port=22,
        accept_new=True):
    """Run one command over SSH and return its output as bytes."""
    c = connect(host, user, password, key, port, accept_new)
    try:
        c.exec_command(command)
        out = []
        while not c.closed:
            try:
                d = c.read()
            except SSHError:
                break
            if d:
                out.append(d)
        return b''.join(out)
    finally:
        c.close()


# ------------------------------------------------------------------- terminal

def _utf8_split(buf):
    """(text that is complete, bytes held back as a partial character)."""
    cut = len(buf)
    for back in range(1, 5):
        if back > len(buf):
            break
        b = buf[len(buf) - back]
        if b < 0x80:
            break                       # plain ASCII, nothing is pending
        if b >= 0xC0:                   # a start byte
            need = 2 if b < 0xE0 else (3 if b < 0xF0 else 4)
            if back < need:
                cut = len(buf) - back   # the rest is in the next packet
            break
    try:
        return buf[:cut].decode(), buf[cut:]
    except Exception:
        # Not UTF-8 at all -- a binary byte from the remote. Show what can be
        # shown rather than dropping the whole chunk.
        return (''.join(chr(x) if x < 128 else '.' for x in buf[:cut]),
                buf[cut:])


def _key_bytes(k):
    """One Tulip key code as the bytes a terminal would send."""
    if k == 259:
        return b'\x1b[A'
    if k == 258:
        return b'\x1b[B'
    if k == 261:
        return b'\x1b[C'
    if k == 260:
        return b'\x1b[D'
    if k == 262:
        return b'\x1b[3~'
    if k == 8:
        return b'\x7f'     # remote stty erase is ^? nearly everywhere
    if k < 128:
        return bytes((k,))
    if k > 0x10FFFF:
        return b''
    return chr(k).encode()


def shell(host, user, password=None, key=None, port=22, accept_new=True,
          term='xterm'):
    """An interactive remote shell, on the text console."""
    import sys
    import select
    import tulip
    import micropython

    cols, rows = tulip.tfb_size()
    c = connect(host, user, password, key, port, accept_new)
    keys = []
    poller = select.poll()
    poller.register(c.sock, select.POLLIN)
    try:
        c.request_pty(cols, rows, term)
        c.start_shell()
        # Take the keyboard off the REPL for the duration. With the screen no
        # longer acting as the REPL the keys arrive only through this callback,
        # and kbd_intr(-1) stops Ctrl-C from raising KeyboardInterrupt in here
        # so it can be forwarded to the remote instead -- which is where a
        # Ctrl-C typed into a remote shell belongs.
        tulip.set_screen_as_repl(0)
        tulip.keyboard_callback(keys.append)
        micropython.kbd_intr(-1)
        tail = b''
        while not c.closed:
            data = b''
            while len(data) < 4096 and poller.poll(0) and not c.closed:
                data += c.read()
            if data:
                text, tail = _utf8_split(tail + data)
                if text:
                    sys.stdout.write(text)
            out = b''
            while keys:
                out += _key_bytes(keys.pop(0))
            if out:
                c.write(out)
            elif not data:
                poller.poll(20)     # idle: wait on the socket rather than spin
    except SSHError as e:
        print('\nssh: %s' % e)
    finally:
        micropython.kbd_intr(3)
        tulip.keyboard_callback()
        tulip.set_screen_as_repl(1)
        c.close()
        print('')
    return c.exit_status
