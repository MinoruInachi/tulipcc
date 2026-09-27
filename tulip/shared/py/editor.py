# editor.py
# Tulip C editor wrapped in UIScreen.
# One day (not today), we should re-write the editor in pure python here too (still using the TFB)
# That will let us do LVGL stuff for saving/searching/etc

import tulip
editor = None
# True while edit() is still constructing the Editor. See activate_editor_cb.
_starting = False


def draw(screen):
    """Nudge the task bar buttons after the TFB switch painted over them.

    Takes the screen rather than reaching for the module global: this is
    deferred out of present(), which runs inside Editor.__init__, so it can
    fire before edit() has assigned `editor` at all. Both buttons are also
    legitimately None -- alttab only exists while more than one app is
    running, and neither is made before draw_task_bar() has run. Either way
    the AttributeError landed inside the defer queue, which prints and
    swallows it, so the buttons quietly never got redrawn.
    """
    for button in (screen.alttab_button, screen.quit_button):
        if button is not None:
            button.invalidate()

class Editor(tulip.UIScreen):
    def __init__(self, filename):
        self.filename = filename
        # Make sure to turn off the offsets for the task bar
        super().__init__('edit', bg_color=36, keep_tfb=True, offset_x=0, offset_y=0)
        self.quit_callback = self.quit_editor_cb
        self.deactivate_callback = self.deactivate_editor_cb
        self.activate_callback = self.activate_editor_cb
        self.first_run = True
        self.present()

    def deactivate_editor_cb(self, screen):
        tulip.keyboard_callback()
        tulip.tfb_restore()
 

    def quit_editor_cb(self, screen):
        tulip.deinit_editor()

    def activate_editor_cb(self,screen):
        # When edit() is typed at the REPL, the REPL prints its >>> prompt as soon
        # as edit() returns, onto whatever the TFB shows by then. present() only
        # defers this callback, but the defer queue is drained by scheduled
        # callbacks, and MicroPython runs those between bytecodes -- so when
        # LVGL's render of the new screen is slow (about 0.4s on Tab5) this whole
        # activation, paint included, ran inside edit(), and the prompt then
        # landed on the editor's first line. Wait until edit() has returned, so
        # the prompt is already out and tfb_save() keeps it for the way back.
        if _starting:
            tulip.defer(self.activate_editor_cb, screen, 50)
            return
        # Only load in the file on first run
        tulip.tfb_save()
        if(self.first_run):
            self.first_run = False
            if(self.filename is None):
                tulip.run_editor()
            else:
                tulip.run_editor(self.filename)

        tulip.keyboard_callback(tulip.key_editor)
        # The TFB switches over, but the REPL will print >>> after this runs, 
        # overwriting the first line. So wait a bit and activate then
        tulip.defer(tulip.activate_editor, None, 50)
        # And because of the TFB clearing, the buttons may get destroyed, so re-draw them
        tulip.defer(draw, screen, 100)

# Launch the tulip editor as a UIScreen
def edit(filename=None):
    global editor, _starting
    if 'edit' in tulip.running_apps:
        print("Editor already running.")
        return
    else:
        _starting = True
        try:
            editor = Editor(filename)
        finally:
            _starting = False
