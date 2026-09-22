"""An SSH terminal, as a switchable Tulip app.

The connection form is LVGL; the session itself runs on the text console,
because that is where a terminal belongs -- it scrolls, it has the fonts, and
ssh.py already writes to it.

The session is pumped from tulip.frame_callback() rather than from a loop. A
UIScreen app has to return from run() and do its work in callbacks, and here
that is not just etiquette: the task bar is the only way to quit or switch away
while the keyboard is being handed to the remote, so it has to keep drawing.
ssh.shell() is the blocking version of the same thing, for the REPL.

The form has a second page behind the Files button: a directory listing and
file transfer, over SFTP. It is a page rather than more rows because the login
fields already use both columns, and a transfer is something you do either
before opening a shell or instead of one -- it makes its own connection, so it
does not disturb a session that is running.
"""

import sys
import micropython
import tulip
import ssh
import sftp
from tulip import lv, pal_to_lv

# Everything except the password, which is not written to disk at all.
_SAVED = ('host', 'user', 'port', 'key', 'remote', 'local')
_CONFIG = 'sshterm.conf'

_LABEL_COLOR = 250
_STATUS_COLOR = 244
_ERROR_COLOR = 224


class SSHTerm:
    def __init__(self, screen):
        self.screen = screen
        self.client = None
        self.poller = None
        self.fields = {}
        self.form = None
        self.status = None
        self.connect_button = None
        self.last_field = None
        # The login fields and the file page's, so one can be hidden while the
        # other is up. Hidden widgets are skipped by the focus group too, which
        # is what keeps Tab off the page that is not showing.
        self.login_widgets = []
        self.files_widgets = []
        self.file_status = None
        self.listing = None
        # Which transfer is running, if any. A transfer blocks for as long as
        # the file takes, and LVGL keeps running during it (lv_task_handler is
        # scheduled, not called from here), so the buttons stay live and can be
        # pressed again -- see pump() on the same problem.
        self.transfer = None
        # The typed password, kept in RAM so the file page can make its own
        # connection after the field has been cleared. It is never written to
        # disk and goes when the app quits.
        self.password = None
        # Session state
        self.keys = []
        self.tail = b''
        self.line_start = True
        self.tilde = False
        self.busy = False
        # The window size the remote was last told about. See check_size().
        self.size = None
        # The callback objects themselves, made once and kept. See take_keyboard().
        self.key_cb = None
        self.pump_cb = None

    # ---------------------------------------------------------------- the form

    def build(self):
        big = tulip.board() == 'TAB5'
        font = getattr(lv, 'font_montserrat_18' if big else 'font_montserrat_12',
                       lv.font_montserrat_12)
        row_h = 56 if big else 40
        label_w = 150 if big else 110
        entry_w = 380 if big else 300
        pad = 20
        # Two columns, because the on-screen keyboard takes the bottom half of the
        # screen. Down one column the fifth field and the Connect button end up
        # underneath it, and a form you have to put the keyboard away to submit is
        # a form that does not work on a tablet.
        col_w = label_w + entry_w + (40 if big else 30)

        width, height = tulip.screen_size()
        self.form = lv.obj(self.screen.group)
        self.form.set_size(width - 2 * pad, height - 2 * pad - 40)
        self.form.set_pos(pad, pad + 40)
        self.form.set_style_bg_color(pal_to_lv(self.screen.bg_color), lv.PART.MAIN)
        self.form.set_style_border_width(0, lv.PART.MAIN)
        self.form.set_style_text_font(font, 0)
        self.form.remove_flag(lv.obj.FLAG.SCROLLABLE)

        title = lv.label(self.form)
        title.set_text('SSH')
        title.set_style_text_color(pal_to_lv(_LABEL_COLOR), 0)
        title.set_pos(0, 0)
        self.login_widgets.append(title)

        rows = (('host', 'Host', '', 0, 1), ('user', 'User', '', 0, 2),
                ('password', 'Password', '', 0, 3), ('key', 'Key file', '', 1, 1),
                ('port', 'Port', '22', 1, 2))
        for name, text, default, col, row in rows:
            x = col * col_w
            y = row * row_h
            label = lv.label(self.form)
            label.set_text(text)
            label.set_style_text_color(pal_to_lv(_LABEL_COLOR), 0)
            label.set_pos(x, y + 10)
            entry = lv.textarea(self.form)
            entry.set_one_line(True)
            entry.set_size(entry_w if name != 'port' else 120, row_h - 8)
            entry.set_pos(x + label_w, y)
            entry.set_text(default)
            if name == 'password':
                entry.set_password_mode(True)
            # Which field the on-screen keyboard types into is whichever one was
            # last tapped -- and by the time the keyboard button is pressed the
            # focus has moved to that button, so remember it as it happens.
            entry.add_event_cb(self.field_focus_cb, lv.EVENT.FOCUSED, None)
            # Enter submits, the way it does in every other login form. A
            # one-line text area sends READY when it takes a return, from the
            # hardware keyboard and from the on-screen keyboard's return key
            # alike, so hooking it here covers both.
            entry.add_event_cb(self.connect_cb, lv.EVENT.READY, None)
            self.fields[name] = entry
            self.login_widgets.append(label)
            self.login_widgets.append(entry)

        y = 3 * row_h
        self.connect_button = lv.button(self.form)
        self.connect_button.set_pos(col_w + label_w, y + 10)
        connect_label = lv.label(self.connect_button)
        connect_label.set_text('Connect')
        self.connect_button.add_event_cb(self.connect_cb, lv.EVENT.CLICKED, None)

        kb_button = lv.button(self.form)
        kb_button.set_pos(col_w + label_w + 160, y + 10)
        kb_label = lv.label(kb_button)
        kb_label.set_text(lv.SYMBOL.KEYBOARD)
        kb_button.add_event_cb(self.keyboard_cb, lv.EVENT.CLICKED, None)

        files_button = lv.button(self.form)
        files_button.set_pos(col_w + label_w + 230, y + 10)
        files_label = lv.label(files_button)
        files_label.set_text(lv.SYMBOL.DIRECTORY + ' Files')
        files_button.add_event_cb(self.files_cb, lv.EVENT.CLICKED, None)

        self.status = lv.label(self.form)
        self.status.set_pos(0, y + row_h + 10)
        self.status.set_width(self.form.get_width() - 20)
        self.status.set_long_mode(0)   # LV_LABEL_LONG_WRAP
        self.set_status('A password or a key file. In a session, ~. hangs up and the task bar quits.')
        self.login_widgets.append(self.connect_button)
        self.login_widgets.append(kb_button)
        self.login_widgets.append(files_button)
        self.login_widgets.append(self.status)

        self.build_files(row_h, label_w, entry_w, col_w, big)

        self.load_config()
        # LVGL adds each widget to the default group as it is created, which
        # UIScreen has already pointed at this screen. Start on the first empty
        # field so a keyboard user can just type, and so the on-screen keyboard
        # has somewhere to type before anything has been tapped.
        try:
            lv.group_focus_obj(self.first_empty())
        except Exception:
            pass

    def build_files(self, row_h, label_w, entry_w, col_w, big):
        """The second page: a remote listing, and a file each way.

        Built at the same time as the form and then hidden, because the two
        pages share the same field-focus and on-screen-keyboard plumbing and
        there is nothing here worth building twice.
        """
        def label_at(text, x, y, color=_LABEL_COLOR):
            lab = lv.label(self.form)
            lab.set_text(text)
            lab.set_style_text_color(pal_to_lv(color), 0)
            lab.set_pos(x, y)
            self.files_widgets.append(lab)
            return lab

        def button_at(text, x, y, cb):
            b = lv.button(self.form)
            b.set_pos(x, y)
            lab = lv.label(b)
            lab.set_text(text)
            b.add_event_cb(cb, lv.EVENT.CLICKED, None)
            self.files_widgets.append(b)
            return b

        label_at('Files', 0, 0)
        # The local side defaults to wherever this board keeps user files, so
        # on a Tulip the only thing to type is the remote name.
        for name, text, default, row in (('remote', 'Remote', '', 1),
                                         ('local', 'Local', ssh.user_path(''), 2)):
            y = row * row_h
            label_at(text, 0, y + 10)
            entry = lv.textarea(self.form)
            entry.set_one_line(True)
            entry.set_size(entry_w, row_h - 8)
            entry.set_pos(label_w, y)
            entry.set_text(default)
            entry.add_event_cb(self.field_focus_cb, lv.EVENT.FOCUSED, None)
            self.fields[name] = entry
            self.files_widgets.append(entry)

        y = 3 * row_h + 10
        step = 100 if big else 80
        # Back to the login page on the left, where the labels are: the right
        # of this row is underneath the listing box.
        button_at(lv.SYMBOL.LEFT + ' Login', 0, y, self.login_cb)
        button_at('Get', label_w, y, self.get_cb)
        button_at('Put', label_w + step, y, self.put_cb)
        button_at('List', label_w + 2 * step, y, self.list_cb)
        button_at(lv.SYMBOL.KEYBOARD, label_w + 3 * step + 20, y, self.keyboard_cb)

        self.file_status = label_at('Get fetches Remote into Local; Put sends Local to Remote.',
                                    0, 4 * row_h + 20, _STATUS_COLOR)
        self.file_status.set_width(col_w - 20)
        self.file_status.set_long_mode(0)     # LV_LABEL_LONG_WRAP

        box_w = self.form.get_width() - col_w - 10
        box = lv.obj(self.form)
        box.set_pos(col_w, row_h)
        box.set_size(box_w, self.form.get_height() - row_h - 20)
        box.set_style_bg_color(pal_to_lv(self.screen.bg_color), lv.PART.MAIN)
        # Long names are cut off rather than scrolled sideways: a horizontal
        # scrollbar under a list of file names is something to get rid of, not
        # something to use.
        box.set_scroll_dir(lv.DIR.VER)
        self.files_widgets.append(box)
        self.listing = lv.label(box)
        # box.get_width() is not the width it was just given until LVGL has
        # laid the box out, and a label with a width of nothing wraps every
        # character onto a line of its own.
        self.listing.set_width(box_w - 30)
        self.listing.set_long_mode(0)
        self.listing.set_style_text_color(pal_to_lv(_LABEL_COLOR), 0)
        self.listing.set_text('List shows the remote directory named above,\n'
                              'or the login directory if that is empty.')

        for w in self.files_widgets:
            w.add_flag(lv.obj.FLAG.HIDDEN)

    def set_status(self, text, color=_STATUS_COLOR):
        if self.status is not None:
            self.status.set_style_text_color(pal_to_lv(color), 0)
            self.status.set_text(text)

    def value(self, name):
        return self.fields[name].get_text().strip()

    def first_empty(self):
        """The field to start in: the first one there is nothing in yet."""
        for name in ('host', 'user', 'password', 'key'):
            if not self.value(name):
                return self.fields[name]
        return self.fields['host']

    def field_focus_cb(self, e):
        self.last_field = e.get_target_obj()

    def keyboard_cb(self, e):
        import ui
        # Pressing this button moved the focus onto the button, so the field to
        # type into is the one that had it before -- or, nothing having been
        # tapped yet, the one build() started on.
        ui.keyboard(self.last_field if self.last_field is not None else self.first_empty())

    # A config file so a tablet user types the host once. The password is
    # deliberately not in it -- a plain text file on a shared filesystem is no
    # place for one, and a key file is the better answer anyway.
    def load_config(self):
        try:
            import json
            with open(ssh.user_path(_CONFIG)) as f:
                saved = json.load(f)
        except Exception:
            return
        for name in _SAVED:
            if saved.get(name):
                self.fields[name].set_text(str(saved[name]))

    def save_config(self):
        try:
            import json
            saved = {}
            for name in _SAVED:
                saved[name] = self.value(name)
            with open(ssh.user_path(_CONFIG), 'w') as f:
                json.dump(saved, f)
        except Exception as e:
            print('sshterm: could not save %s (%s)' % (_CONFIG, e))

    # ---------------------------------------------------------- file transfer

    def set_file_status(self, text, color=_STATUS_COLOR):
        if self.file_status is not None:
            self.file_status.set_style_text_color(pal_to_lv(color), 0)
            self.file_status.set_text(text)

    def show_page(self, files):
        for w in self.login_widgets:
            if files:
                w.add_flag(lv.obj.FLAG.HIDDEN)
            else:
                w.remove_flag(lv.obj.FLAG.HIDDEN)
        for w in self.files_widgets:
            if files:
                w.remove_flag(lv.obj.FLAG.HIDDEN)
            else:
                w.add_flag(lv.obj.FLAG.HIDDEN)
        # The on-screen keyboard types into the last field that was focused,
        # and that field is now behind the other page.
        self.last_field = None
        try:
            lv.group_focus_obj(self.fields['remote'] if files else self.first_empty())
        except Exception:
            pass

    def files_cb(self, e):
        self.show_page(True)

    def login_cb(self, e):
        if self.transfer is not None:
            return
        self.show_page(False)

    def port_number(self):
        try:
            return int(self.value('port') or 22)
        except ValueError:
            return None

    def credentials(self):
        """(password, key file), remembering a typed password for the file page.

        do_connect() clears the password field once it has connected, which is
        the right thing for a form left on screen -- but a transfer afterwards
        needs it again, and asking for it twice is worse than holding it in RAM
        for as long as the app is running. It is never written to disk.
        """
        typed = self.value('password')
        if typed:
            self.password = typed
        return self.password, (self.value('key') or None)

    def get_cb(self, e):
        self.start_transfer('get')

    def put_cb(self, e):
        self.start_transfer('put')

    def list_cb(self, e):
        self.start_transfer('list')

    def start_transfer(self, what):
        # LVGL goes on running while a transfer blocks, so the buttons are
        # still live and this is reachable from inside one. See pump().
        if self.transfer is not None:
            return
        if not self.value('host') or not self.value('user'):
            self.set_file_status('Host and user are required', _ERROR_COLOR)
            return
        if self.port_number() is None:
            self.set_file_status('Port must be a number', _ERROR_COLOR)
            return
        password, key = self.credentials()
        if password is None and key is None:
            self.set_file_status('Give a password or a key file on the login page',
                                 _ERROR_COLOR)
            return
        if what == 'get' and not self.value('remote'):
            self.set_file_status('Which remote file?', _ERROR_COLOR)
            return
        if what == 'put' and not self.value('local'):
            self.set_file_status('Which local file?', _ERROR_COLOR)
            return
        self.transfer = what
        self.set_file_status('Connecting to %s...' % self.value('host'))
        # As on the login page: let LVGL paint that before the handshake, which
        # is about a second of X25519 and RSA.
        tulip.defer(self.run_transfer, None, 100)

    def progress(self, done, total):
        if total:
            self.set_file_status('%s %d%% -- %d of %d KB'
                                 % (self.transfer, done * 100 // total,
                                    done >> 10, total >> 10))
        else:
            self.set_file_status('%s %d KB' % (self.transfer, done >> 10))

    def run_transfer(self, _arg=None):
        what = self.transfer
        if what is None or self.form is None:
            return
        password, key = self.credentials()
        remote = self.value('remote')
        local = self.value('local')
        try:
            session = sftp.connect(self.value('host'), self.value('user'),
                                   password, key, self.port_number())
        except Exception as e:
            self.transfer = None
            self.set_file_status('%s: %s' % (type(e).__name__, e), _ERROR_COLOR)
            return
        # The host and paths are worth keeping now that they are known good.
        self.save_config()
        try:
            if what == 'list':
                self.show_listing(session, remote or '.')
            elif what == 'get':
                n = session.get(remote, local or None, self.progress)
                self.set_file_status('Got %s (%d bytes)'
                                     % (sftp.basename(remote), n))
            else:
                n = session.put(local, remote or None, self.progress)
                self.set_file_status('Sent %s (%d bytes)'
                                     % (sftp.basename(local), n))
        except Exception as e:
            self.set_file_status('%s: %s' % (type(e).__name__, e), _ERROR_COLOR)
        finally:
            try:
                session.close()
            except Exception:
                pass
            self.transfer = None

    def show_listing(self, session, path):
        rows = session.ls(path)
        rows.sort(key=lambda row: row[0])
        lines = []
        for name, _longname, attrs in rows[:200]:
            lines.append(name + ('/' if sftp.is_dir(attrs) else ''))
        if len(rows) > 200:
            lines.append('... and %d more' % (len(rows) - 200))
        if self.listing is not None:
            self.listing.set_text('\n'.join(lines) if lines else '(empty)')
        self.set_file_status('%s: %d %s' % (session.realpath(path), len(rows),
                                            'entry' if len(rows) == 1 else 'entries'))

    # ------------------------------------------------------------- connecting

    def connect_cb(self, e):
        if self.client is not None:
            return
        if not self.value('host') or not self.value('user'):
            self.set_status('Host and user are required', _ERROR_COLOR)
            return
        self.set_status('Connecting to %s...' % self.value('host'))
        # Let LVGL paint that before the handshake, which blocks for about a
        # second (X25519 and RSA) and rather longer with a key to sign with.
        tulip.defer(self.do_connect, None, 100)

    def do_connect(self, _arg=None):
        port = self.port_number()
        if port is None:
            self.set_status('Port must be a number', _ERROR_COLOR)
            return
        password, key = self.credentials()
        if key is None and password is None:
            self.set_status('Give a password or a key file', _ERROR_COLOR)
            return
        try:
            client = ssh.connect(self.value('host'), self.value('user'),
                                 password, key, port)
        except Exception as e:
            self.set_status('%s: %s' % (type(e).__name__, e), _ERROR_COLOR)
            return
        self.save_config()
        self.client = client
        # The password stays only as long as the form is on screen.
        self.fields['password'].set_text('')
        try:
            self.size = tulip.tfb_size()
            cols, rows = self.size
            client.request_pty(cols, rows)
            client.start_shell()
        except Exception as e:
            self.set_status('%s: %s' % (type(e).__name__, e), _ERROR_COLOR)
            self.disconnect()
            return
        import select
        self.poller = select.poll()
        self.poller.register(client.sock, select.POLLIN)
        self.tail = b''
        self.line_start = True
        self.tilde = False
        del self.keys[:]
        self.form.add_flag(lv.obj.FLAG.HIDDEN)
        self.screen.keep_tfb = True
        tulip.tfb_start()
        # The console becomes a terminal for the duration: cursor addressing, a
        # scroll region, an alternate screen to put vi on. It starts by clearing
        # the screen, so the banner goes on after. \r\n rather than \n, because
        # a terminal's line feed moves down and keeps the column: putting the
        # \r in front of it is the pty's job, and the pty is not in this line.
        tulip.term_start()
        sys.stdout.write('-- ssh %s@%s --\r\n' % (self.value('user'), self.value('host')))
        self.take_keyboard()

    def disconnect(self, why=None):
        if self.client is None and self.form is not None and \
                not self.form.has_flag(lv.obj.FLAG.HIDDEN):
            return                      # already back at the form
        self.release_keyboard()
        self.poller = None
        ended = False
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass
            self.client = None
            ended = True
        # The console gets its own screen back here, so the note about the
        # session ending goes on after: written before this, it would be on the
        # session's screen, which is the one being taken away.
        tulip.term_stop()
        if ended:
            sys.stdout.write('\r\n-- disconnected%s --\r\n'
                             % ('' if why is None else ': ' + why))
        self.screen.keep_tfb = False
        tulip.tfb_stop()
        if self.form is not None:
            self.form.remove_flag(lv.obj.FLAG.HIDDEN)
            self.set_status('Disconnected' if why is None else why)

    # ---------------------------------------------------------- the session

    def take_keyboard(self):
        # Hold the bound methods rather than making a new one per call. On a
        # board whose C side keeps its keyboard callback in a plain global --
        # every target except this one -- that slot is not a GC root, and a
        # bound method nothing else refers to gets collected out from under it.
        if self.key_cb is None:
            self.key_cb = self.keys.append
        if self.pump_cb is None:
            self.pump_cb = self.pump
        tulip.keyboard_callback(self.key_cb)
        # Ctrl-C belongs to the remote shell while a session is up, so stop it
        # raising KeyboardInterrupt here. Quitting is the task bar's job.
        micropython.kbd_intr(-1)
        tulip.frame_callback(self.pump_cb)

    def release_keyboard(self):
        tulip.frame_callback()
        tulip.keyboard_callback()
        micropython.kbd_intr(3)

    def to_remote(self, k, flags=0):
        """One Tulip key code as bytes, minding the ~. escape."""
        if self.tilde:
            self.tilde = False
            if k == ord('.'):
                self.disconnect('closed by ~.')
                return b''
            return b'~' + ssh.key_bytes(k, flags)
        if self.line_start and k == ord('~'):
            self.tilde = True
            return b''
        out = ssh.key_bytes(k, flags)
        self.line_start = out in (b'\r', b'\n')
        return out

    def pump(self, _arg=None):
        client = self.client
        # disconnect() drops the poller before it closes the client, and
        # closing blocks -- long enough for a pump scheduled just before it to
        # run in the middle. Without the poller there is nothing to pump.
        if client is None or self.poller is None or self.busy:
            return
        # Re-entrancy is not hypothetical here. recv(), send() and a write to
        # the console all block, and a blocking call inside a scheduled
        # callback lets MicroPython run the scheduler again -- including the
        # next frame's pump. Two of them interleaved take turns on one AES-CTR
        # stream and hand each other half a packet, and if one disconnects
        # while the other is still inside read() the second comes back to a
        # closed client and a deleted form.
        self.busy = True
        try:
            data = b''
            # Bounded, so one burst of output cannot hold up the frame. At 60
            # frames a second this is well past the 77KB/s the link measures.
            while len(data) < 2048 and not client.closed and (
                    client.pending() or self.poller.poll(0)):
                data += client.read()
            if data:
                text, self.tail = ssh.utf8_split(self.tail + data)
                if text:
                    sys.stdout.write(text)
                    self.check_size()
            # Whatever the terminal owes the far end -- a device attributes
            # answer, a cursor position report. It is the terminal that is
            # asked and the session that knows where the answer goes.
            out = tulip.term_reply()
            flags = tulip.term_flags()
            while self.keys:
                out += self.to_remote(self.keys.pop(0), flags)
                if self.client is None:
                    return          # ~. closed it out from under us
            if out:
                client.write(out)
            if client.closed:
                self.disconnect('remote closed')
        except Exception as e:
            self.disconnect('%s: %s' % (type(e).__name__, e))
        finally:
            self.busy = False

    def check_size(self):
        """Keep the remote's idea of the window and the console's the same.

        The console picks its own font: the first character the current one
        cannot draw promotes it to a Japanese one, and a font whose cells are a
        different size is a different number of columns and rows. Say nothing
        and the remote goes on wrapping at the width it was given, which puts
        every line after that in the wrong place.
        """
        size = tulip.tfb_size()
        if size == self.size or self.client is None:
            return
        self.size = size
        try:
            self.client.resize(size[0], size[1])
        except Exception:
            # A window-change the far end will not take is not worth ending a
            # session over; the display is wrong either way.
            pass

    # ------------------------------------------------------ app lifecycle

    def activate(self, screen):
        if self.client is not None:
            # Back into terminal mode without resetting it: the scroll region,
            # the modes and the alternate screen are the session's and it is
            # still running. Whatever was in front may have drawn over the
            # screen; the remote redraws it the next time it writes.
            tulip.term_start(False)
            self.take_keyboard()

    def deactivate(self, screen):
        # Switching away leaves the session open but stops reading it -- the
        # remote fills the window and waits, which is what a backgrounded
        # terminal does anyway. Terminal mode goes with it: whatever has the
        # console next expects a \n to start a line, not to move down a row.
        if self.client is not None:
            tulip.term_stop(False)
            self.release_keyboard()

    def quit(self, screen):
        self.disconnect()
        self.form = None
        self.status = None
        self.file_status = None
        self.listing = None
        self.password = None


# The running instance, so the REPL can reach it -- sshterm.term.fields['host']
# and so on. worldui keeps its screen the same way.
term = None


def run(screen):
    global term
    term = SSHTerm(screen)
    screen.handle_keyboard = True
    screen.activate_callback = term.activate
    screen.deactivate_callback = term.deactivate
    screen.quit_callback = term.quit
    term.build()
    screen.present()
