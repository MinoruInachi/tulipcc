"""An SSH terminal, as a switchable Tulip app.

The connection form is LVGL; the session itself runs on the text console,
because that is where a terminal belongs -- it scrolls, it has the fonts, and
ssh.py already writes to it.

The session is pumped from tulip.frame_callback() rather than from a loop. A
UIScreen app has to return from run() and do its work in callbacks, and here
that is not just etiquette: the task bar is the only way to quit or switch away
while the keyboard is being handed to the remote, so it has to keep drawing.
ssh.shell() is the blocking version of the same thing, for the REPL.
"""

import sys
import micropython
import tulip
import ssh
from tulip import lv, pal_to_lv

# Everything except the password, which is not written to disk at all.
_SAVED = ('host', 'user', 'port', 'key')
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

        self.status = lv.label(self.form)
        self.status.set_pos(0, y + row_h + 10)
        self.status.set_width(self.form.get_width() - 20)
        self.status.set_long_mode(0)   # LV_LABEL_LONG_WRAP
        self.set_status('A password or a key file. In a session, ~. hangs up and the task bar quits.')

        self.load_config()
        # LVGL adds each widget to the default group as it is created, which
        # UIScreen has already pointed at this screen. Start on the first empty
        # field so a keyboard user can just type, and so the on-screen keyboard
        # has somewhere to type before anything has been tapped.
        try:
            lv.group_focus_obj(self.first_empty())
        except Exception:
            pass

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
        try:
            port = int(self.value('port') or 22)
        except ValueError:
            self.set_status('Port must be a number', _ERROR_COLOR)
            return
        key = self.value('key') or None
        password = self.value('password') or None
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
        if client is None or self.busy:
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
            while len(data) < 2048 and not client.closed and self.poller.poll(0):
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
