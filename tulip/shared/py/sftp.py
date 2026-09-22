# File transfer over SSH, in MicroPython. Talks to ssh.py's connection.
#
# Why SFTP and not scp: scp's wire protocol is the `scp -t` / `scp -f` mode of
# the remote scp binary, and OpenSSH has been walking away from it since 9.0 --
# its own client stopped using it by default, and the man page has called the
# protocol outdated ever since. SFTP is a subsystem of the server rather than a
# program it has to find on the far end's PATH, every OpenSSH build ships it,
# and it is the one that gives directory listings and sizes as well as bytes.
#
# Version 3 of the protocol, which is what OpenSSH speaks and what every other
# client asks for. Later versions exist on paper and essentially nowhere else.
#
# Usage:
#     import sftp
#     sftp.get('192.168.1.10', 'me', '/etc/hostname', '/user/hostname',
#              password='secret')
#     sftp.put('192.168.1.10', 'me', '/user/song.wav', 'song.wav',
#              key='/user/id_rsa')
#
#     # Or hold the connection open, which is worth doing for more than one
#     # file -- the handshake is about a second of X25519 and RSA.
#     s = sftp.connect('192.168.1.10', 'me', key='/user/id_rsa')
#     for name in s.listdir('/var/log'):
#         print(name)
#     s.get('/var/log/system.log')
#     s.close()

import os
import ssh
from ssh import _u32, _string, _Reader, SSHError

# draft-ietf-secsh-filexfer-02, the document OpenSSH implements as version 3.
FXP_INIT = 1
FXP_VERSION = 2
FXP_OPEN = 3
FXP_CLOSE = 4
FXP_READ = 5
FXP_WRITE = 6
FXP_LSTAT = 7
FXP_FSTAT = 8
FXP_SETSTAT = 9
FXP_FSETSTAT = 10
FXP_OPENDIR = 11
FXP_READDIR = 12
FXP_REMOVE = 13
FXP_MKDIR = 14
FXP_RMDIR = 15
FXP_REALPATH = 16
FXP_STAT = 17
FXP_RENAME = 18
FXP_STATUS = 101
FXP_HANDLE = 102
FXP_DATA = 103
FXP_NAME = 104
FXP_ATTRS = 105

FX_OK = 0
FX_EOF = 1
FX_NO_SUCH_FILE = 2
FX_PERMISSION_DENIED = 3
FX_FAILURE = 4

_STATUS_TEXT = {
    1: 'end of file',
    2: 'no such file',
    3: 'permission denied',
    4: 'failure',
    5: 'bad message',
    6: 'no connection',
    7: 'connection lost',
    8: 'operation not supported',
}

# Open flags (the pflags word of FXP_OPEN).
FXF_READ = 0x01
FXF_WRITE = 0x02
FXF_APPEND = 0x04
FXF_CREAT = 0x08
FXF_TRUNC = 0x10
FXF_EXCL = 0x20

# Which fields an ATTRS structure carries.
ATTR_SIZE = 0x01
ATTR_UIDGID = 0x02
ATTR_PERMISSIONS = 0x04
ATTR_ACMODTIME = 0x08
ATTR_EXTENDED = 0x80000000

S_IFMT = 0o170000
S_IFDIR = 0o040000
S_IFREG = 0o100000
S_IFLNK = 0o120000

# Bytes per READ and per WRITE. The channel was opened with a maximum packet
# of 32KB, so a chunk this size plus its SFTP header still crosses the wire as
# one packet -- 32KB would straddle two for no gain. Each chunk is a round
# trip, which at this link's ~77KB/s is nowhere near the limiting factor.
_CHUNK = 16384


class SFTPError(SSHError):
    """Something the server refused. `code` is its SSH_FX_* number, if any."""

    def __init__(self, message, code=None):
        # super(), not SSHError.__init__: MicroPython's built-in exception
        # types do not carry an __init__ to reach through the class.
        super().__init__(message)
        self.code = code


def _u64(n):
    return bytes((n >> 56 & 255, n >> 48 & 255, n >> 40 & 255, n >> 32 & 255,
                  n >> 24 & 255, n >> 16 & 255, n >> 8 & 255, n & 255))


def _attrs(r):
    """The ATTRS structure, as a dict of whatever the server chose to send."""
    flags = r.u32()
    a = {}
    if flags & ATTR_SIZE:
        a['size'] = r.u64()
    if flags & ATTR_UIDGID:
        a['uid'] = r.u32()
        a['gid'] = r.u32()
    if flags & ATTR_PERMISSIONS:
        a['mode'] = r.u32()
    if flags & ATTR_ACMODTIME:
        a['atime'] = r.u32()
        a['mtime'] = r.u32()
    if flags & ATTR_EXTENDED:
        for _ in range(r.u32()):
            r.string()
            r.string()
    return a


def is_dir(attrs):
    return (attrs.get('mode', 0) & S_IFMT) == S_IFDIR


def basename(path):
    i = path.rfind('/')
    return path[i + 1:] if i >= 0 else path


def _status_error(what, code, r):
    # Version 3 puts a human-readable message after the code; sftp-server fills
    # it in with strerror(), which is better than anything written here.
    try:
        msg = r.string().decode()
    except Exception:
        msg = ''
    if not msg:
        msg = _STATUS_TEXT.get(code, 'error %d' % code)
    return SFTPError('%s: %s' % (what, msg), code)


class SFTP:
    """An open SFTP session. Paths are the remote's, relative ones from $HOME."""

    def __init__(self, client):
        self.c = client
        # The subsystem's packets are on stdout; anything the far end writes to
        # stderr (a talkative shell profile, a "you have mail") is not part of
        # them and must not be read as though it were.
        client.merge_stderr = False
        self.buf = b''
        self.pos = 0
        self.seq = 0
        self.version = 0
        client.start_subsystem(b'sftp')
        self.c.write(_u32(5) + bytes((FXP_INIT,)) + _u32(3))
        kind, r = self._message()
        if kind != FXP_VERSION:
            raise SFTPError('expected a VERSION, got message %d' % kind)
        self.version = r.u32()
        if self.version < 3:
            raise SFTPError('server speaks SFTP version %d; this client needs 3'
                            % self.version)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def close(self):
        self.c.close()

    # ------------------------------------------------------------ the wire

    def _why_closed(self):
        text = self.c.stderr
        if not text:
            return ''
        try:
            text = text.decode()
        except Exception:
            text = str(text)
        text = ' '.join(text.split())
        return (' (%s)' % text) if text else ''

    def _read_exact(self, n):
        # A packet arrives in as many channel packets as the far end felt like
        # using, so what is left over belongs to the next one. This is a bytes
        # and an offset rather than a bytearray to chop the front off, because
        # MicroPython's bytearray has no slice deletion.
        while len(self.buf) - self.pos < n:
            data = self.c.read()
            if data:
                if self.pos:
                    self.buf = self.buf[self.pos:]
                    self.pos = 0
                self.buf += data
            elif self.c.closed:
                raise SFTPError('the sftp channel closed%s' % self._why_closed())
        out = self.buf[self.pos:self.pos + n]
        self.pos += n
        if self.pos == len(self.buf):
            self.buf = b''
            self.pos = 0
        return out

    def _message(self):
        length = int.from_bytes(self._read_exact(4), 'big')
        # The largest thing the server sends is a DATA reply to our own read,
        # so anything far over the chunk size means the stream is out of step.
        if length < 5 or length > _CHUNK + 65536:
            raise SFTPError('bad sftp packet length %d' % length)
        body = self._read_exact(length)
        return body[0], _Reader(body[1:])

    def _request(self, kind, payload):
        self.seq = (self.seq + 1) & 0xFFFFFFFF
        want = self.seq
        self.c.write(_u32(len(payload) + 5) + bytes((kind,)) + _u32(want) +
                     payload)
        while True:
            t, r = self._message()
            if r.u32() == want:
                return t, r
            # Requests go out one at a time and each is answered before the
            # next, so this is only reachable if a server answers something
            # twice. Skipping it keeps the stream in step.

    def _ok(self, t, r, what):
        if t != FXP_STATUS:
            raise SFTPError('%s: expected a status, got message %d' % (what, t))
        code = r.u32()
        if code != FX_OK:
            raise _status_error(what, code, r)

    def _handle(self, t, r, what):
        if t == FXP_STATUS:
            raise _status_error(what, r.u32(), r)
        if t != FXP_HANDLE:
            raise SFTPError('%s: expected a handle, got message %d' % (what, t))
        return r.string()

    def _open(self, path, pflags):
        t, r = self._request(FXP_OPEN, _string(path) + _u32(pflags) + _u32(0))
        return self._handle(t, r, 'opening ' + path)

    def _close_handle(self, handle):
        t, r = self._request(FXP_CLOSE, _string(handle))
        self._ok(t, r, 'closing')

    # -------------------------------------------------------- the file tree

    def realpath(self, path='.'):
        """What the server calls `path` -- '.' gives the login directory."""
        t, r = self._request(FXP_REALPATH, _string(path))
        if t == FXP_STATUS:
            raise _status_error('resolving ' + path, r.u32(), r)
        r.u32()                       # one name, by definition
        return r.string().decode()

    def stat(self, path, follow=True):
        """The remote's attributes for `path`, as a dict."""
        t, r = self._request(FXP_STAT if follow else FXP_LSTAT, _string(path))
        if t == FXP_STATUS:
            raise _status_error('stat ' + path, r.u32(), r)
        return _attrs(r)

    def fstat(self, handle):
        t, r = self._request(FXP_FSTAT, _string(handle))
        if t == FXP_STATUS:
            raise _status_error('stat', r.u32(), r)
        return _attrs(r)

    def exists(self, path):
        try:
            self.stat(path)
            return True
        except SFTPError:
            return False

    def isdir(self, path):
        try:
            return is_dir(self.stat(path))
        except SFTPError:
            return False

    def ls(self, path='.'):
        """[(name, longname, attrs), ...] for a directory, . and .. left out.

        `longname` is the server's own `ls -l` line for the entry, which is the
        thing to put on screen -- it already has the mode, size and date in the
        format the far end would have printed them.
        """
        t, r = self._request(FXP_OPENDIR, _string(path))
        handle = self._handle(t, r, 'listing ' + path)
        out = []
        try:
            while True:
                t, r = self._request(FXP_READDIR, _string(handle))
                if t == FXP_STATUS:
                    code = r.u32()
                    if code == FX_EOF:
                        break
                    raise _status_error('listing ' + path, code, r)
                if t != FXP_NAME:
                    raise SFTPError('listing %s: unexpected message %d'
                                    % (path, t))
                for _ in range(r.u32()):
                    name = r.string().decode()
                    longname = r.string().decode()
                    attrs = _attrs(r)
                    if name != '.' and name != '..':
                        out.append((name, longname, attrs))
        finally:
            self._close_handle(handle)
        return out

    def listdir(self, path='.'):
        """Just the names in a directory."""
        return [name for name, _long, _attrs in self.ls(path)]

    def mkdir(self, path):
        t, r = self._request(FXP_MKDIR, _string(path) + _u32(0))
        self._ok(t, r, 'mkdir ' + path)

    def rmdir(self, path):
        t, r = self._request(FXP_RMDIR, _string(path))
        self._ok(t, r, 'rmdir ' + path)

    def remove(self, path):
        t, r = self._request(FXP_REMOVE, _string(path))
        self._ok(t, r, 'rm ' + path)

    def rename(self, old, new):
        t, r = self._request(FXP_RENAME, _string(old) + _string(new))
        self._ok(t, r, 'rename ' + old)

    # ---------------------------------------------------------- the transfer

    def _local_target(self, local, remote):
        if local is None:
            return basename(remote)
        # A directory as the destination means "in here", the way cp reads it.
        try:
            if (os.stat(local)[0] & S_IFMT) == S_IFDIR:
                return local.rstrip('/') + '/' + basename(remote)
        except OSError:
            pass
        return local

    def _remote_target(self, remote, local):
        if remote is None:
            return basename(local)
        if remote.endswith('/'):
            return remote + basename(local)
        if self.isdir(remote):
            return remote + '/' + basename(local)
        return remote

    def get(self, remote, local=None, progress=None):
        """Copy a remote file to `local`. Returns the bytes written.

        `local` defaults to the file's own name in the current directory, and
        naming a directory puts it in there. `progress(done, total)` is called
        once a chunk, with `total` None if the server did not say.
        """
        local = self._local_target(local, remote)
        handle = self._open(remote, FXF_READ)
        done = 0
        try:
            try:
                total = self.fstat(handle).get('size')
            except SFTPError:
                total = None
            f = open(local, 'wb')
            try:
                while True:
                    t, r = self._request(
                        FXP_READ, _string(handle) + _u64(done) + _u32(_CHUNK))
                    if t == FXP_STATUS:
                        code = r.u32()
                        if code == FX_EOF:
                            break
                        raise _status_error('reading ' + remote, code, r)
                    if t != FXP_DATA:
                        raise SFTPError('reading %s: unexpected message %d'
                                        % (remote, t))
                    data = r.string()
                    if not data:
                        break       # a short read means end of file too
                    f.write(data)
                    done += len(data)
                    if progress:
                        progress(done, total)
            finally:
                f.close()
        finally:
            self._close_handle(handle)
        return done

    def put(self, local, remote=None, progress=None):
        """Copy a local file to the remote. Returns the bytes sent.

        `remote` defaults to the file's own name in the login directory; a path
        that names a directory, or ends in '/', puts it in there.
        """
        try:
            total = os.stat(local)[6]
        except OSError as e:
            raise SFTPError('cannot read %s (%s)' % (local, e))
        remote = self._remote_target(remote, local)
        handle = self._open(remote, FXF_WRITE | FXF_CREAT | FXF_TRUNC)
        done = 0
        try:
            f = open(local, 'rb')
            try:
                while True:
                    data = f.read(_CHUNK)
                    if not data:
                        break
                    t, r = self._request(
                        FXP_WRITE, _string(handle) + _u64(done) + _string(data))
                    self._ok(t, r, 'writing ' + remote)
                    done += len(data)
                    if progress:
                        progress(done, total)
            finally:
                f.close()
        finally:
            self._close_handle(handle)
        return done

    def read_file(self, remote, limit=1 << 20):
        """A whole remote file as bytes, for the small ones. Refuses past `limit`."""
        handle = self._open(remote, FXF_READ)
        out = []
        done = 0
        try:
            while True:
                t, r = self._request(
                    FXP_READ, _string(handle) + _u64(done) + _u32(_CHUNK))
                if t == FXP_STATUS:
                    code = r.u32()
                    if code == FX_EOF:
                        break
                    raise _status_error('reading ' + remote, code, r)
                data = r.string()
                if not data:
                    break
                out.append(data)
                done += len(data)
                if done > limit:
                    raise SFTPError('%s is larger than %d bytes; use get()'
                                    % (remote, limit))
        finally:
            self._close_handle(handle)
        return b''.join(out)

    def write_file(self, remote, data):
        """Write bytes straight to a remote file."""
        if isinstance(data, str):
            data = data.encode()
        handle = self._open(remote, FXF_WRITE | FXF_CREAT | FXF_TRUNC)
        try:
            done = 0
            while done < len(data):
                chunk = data[done:done + _CHUNK]
                t, r = self._request(
                    FXP_WRITE, _string(handle) + _u64(done) + _string(chunk))
                self._ok(t, r, 'writing ' + remote)
                done += len(chunk)
        finally:
            self._close_handle(handle)
        return len(data)


def connect(host, user, password=None, key=None, port=22, accept_new=True):
    """Open an SFTP session. Same arguments as ssh.connect()."""
    client = ssh.connect(host, user, password, key, port, accept_new)
    try:
        return SFTP(client)
    except Exception:
        client.close()
        raise


# One-shot versions, for when there is a single file to move. Each of these is
# a whole connection -- about a second of handshake -- so use connect() for
# more than one.

def get(host, user, remote, local=None, password=None, key=None, port=22,
        accept_new=True, progress=None):
    """Fetch one file over SFTP. Returns the bytes written."""
    s = connect(host, user, password, key, port, accept_new)
    try:
        return s.get(remote, local, progress)
    finally:
        s.close()


def put(host, user, local, remote=None, password=None, key=None, port=22,
        accept_new=True, progress=None):
    """Send one file over SFTP. Returns the bytes sent."""
    s = connect(host, user, password, key, port, accept_new)
    try:
        return s.put(local, remote, progress)
    finally:
        s.close()


def listdir(host, user, path='.', password=None, key=None, port=22,
            accept_new=True):
    """The names in one remote directory."""
    s = connect(host, user, password, key, port, accept_new)
    try:
        return s.listdir(path)
    finally:
        s.close()
