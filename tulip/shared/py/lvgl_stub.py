"""Minimal LVGL compatibility shim for Tulip bring-up.

This module is a best-effort fallback used when native `lvgl` bindings are
not compiled into firmware yet. It provides a small subset of the API used by
`tulip/shared/py/ui.py` so UI boot does not fail on `import lvgl`.
"""


class LvReferenceError(Exception):
    pass


TRACE_MISSING_APIS = True
_MISSING_API_KEYS = set()


def _trace_missing(kind, name):
    key = (kind, name)
    if key in _MISSING_API_KEYS:
        return
    _MISSING_API_KEYS.add(key)
    if TRACE_MISSING_APIS:
        print("lvgl_stub missing {}: {}".format(kind, name))


def get_missing_api_hits():
    """Return unique missing API hits observed since boot."""
    hits = []
    for kind, name in sorted(_MISSING_API_KEYS):
        hits.append("{}:{}".format(kind, name))
    return hits


def clear_missing_api_hits():
    _MISSING_API_KEYS.clear()


def set_missing_api_trace(enabled):
    global TRACE_MISSING_APIS
    TRACE_MISSING_APIS = bool(enabled)


class _Enum:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)

    def __getattr__(self, name):
        _trace_missing("enum", name)
        return 0


ALIGN = _Enum(
    TOP_LEFT=0,
    TOP_MID=1,
    TOP_RIGHT=1,
    OUT_LEFT_MID=2,
    OUT_RIGHT_MID=3,
    OUT_BOTTOM_LEFT=4,
    OUT_BOTTOM_MID=5,
    OUT_TOP_MID=6,
    OUT_RIGHT_TOP=9,
    BOTTOM_RIGHT=4,
    BOTTOM_LEFT=7,
    CENTER=8,
)

PART = _Enum(MAIN=0, INDICATOR=1, KNOB=2, CURSOR=3)
EVENT = _Enum(CLICKED=1, VALUE_CHANGED=2, PRESSED=3)
INDEV_TYPE = _Enum(KEYPAD=1)
TEXT_ALIGN = _Enum(CENTER=0)
OPA = _Enum(COVER=255)
DIR = _Enum(LEFT=0, BOTTOM=1)
ANIM = _Enum(OFF=0)
STATE = _Enum(CHECKED=1, FOCUSED=2)
COORD = _Enum(MAX=0x7FFFFFFF)
SYMBOL = _Enum(
    SHUFFLE="S",
    POWER="P",
    LIST="L",
    NEW_LINE="\n",
    BACKSPACE="\b",
    KEYBOARD="K",
    CLOSE="X",
    HOME="H",
    AUDIO="A",
    NEXT=">",
    FILE="F",
    WIFI="W",
    OK="O",
)

SIZE_CONTENT = -1
font_montserrat_12 = object()
font_montserrat_24 = object()
font_unscii_8 = object()
font_tulip_11 = object()
font_tulip_13 = object()


class _Color:
    def __init__(self, red=0, green=0, blue=0):
        self.red = red
        self.green = green
        self.blue = blue


def color_make(red, green, blue):
    return _Color(red, green, blue)


class _Event:
    def __init__(self, code=0, target=None):
        self._code = code
        self._target = target

    def get_code(self):
        return self._code

    def get_target_obj(self):
        return self._target


class _BaseObj:
    FLAG = _Enum(SCROLLABLE=1)

    def __init__(self, parent=None):
        self.parent = None
        self.children = []
        self._width = 0
        self._height = 0
        self._text = ""
        self._mode = 0
        self._value = 0
        self._flags = 0
        self._deleted = False
        self._callbacks = []
        if parent is not None:
            self.set_parent(parent)

    def set_parent(self, parent):
        if self.parent is not None and hasattr(self.parent, 'children'):
            try:
                self.parent.children.remove(self)
            except ValueError:
                pass
        if parent is not None and not hasattr(parent, 'children'):
            parent = None
        self.parent = parent
        if parent is not None and hasattr(parent, 'children'):
            parent.children.append(self)

    def invalidate(self):
        pass

    def delete(self):
        self._deleted = True
        for child in tuple(self.children):
            child.delete()
        self.children = []
        if self.parent is not None and hasattr(self.parent, 'children'):
            try:
                self.parent.children.remove(self)
            except ValueError:
                pass
        self.parent = None

    def add_event_cb(self, cb, event_code, user_data):
        self._callbacks.append((cb, event_code, user_data))

    def set_width(self, width):
        self._width = int(width)

    def set_height(self, height):
        self._height = int(height)

    def set_size(self, width, height):
        self._width = int(width)
        self._height = int(height)

    def get_width(self):
        return self._width

    def get_height(self):
        return self._height

    def get_style_pad_left(self, _part):
        return 0

    def get_style_pad_top(self, _part):
        return 0

    def set_style_pad_left(self, *_args):
        pass

    def set_style_pad_right(self, *_args):
        pass

    def set_style_pad_top(self, *_args):
        pass

    def set_style_pad_bottom(self, *_args):
        pass

    def set_style_pad_ver(self, *_args):
        pass

    def set_style_pad_hor(self, *_args):
        pass

    def set_style_margin_left(self, *_args):
        pass

    def set_style_margin_right(self, *_args):
        pass

    def set_style_margin_top(self, *_args):
        pass

    def set_style_margin_bottom(self, *_args):
        pass

    def set_style_radius(self, *_args):
        pass

    def set_style_border_width(self, *_args):
        pass

    def set_style_bg_color(self, *_args):
        pass

    def set_style_bg_opa(self, *_args):
        pass

    def set_style_text_font(self, *_args):
        pass

    def set_style_text_align(self, *_args):
        pass

    def set_style_text_color(self, *_args):
        pass

    def set_style_border_color(self, *_args):
        pass

    def add_flag(self, flag):
        self._flags |= int(flag)

    def remove_flag(self, flag):
        self._flags &= ~int(flag)

    def align_to(self, *_args):
        pass

    def align(self, *_args):
        pass

    def set_align(self, *_args):
        pass

    def set_pos(self, *_args):
        pass

    def center(self):
        self.align(ALIGN.CENTER, 0, 0)

    def get_index(self):
        if self.parent is None:
            return 0
        try:
            return self.parent.children.index(self)
        except ValueError:
            return 0

    def set_ext_click_area(self, *_args):
        pass

    def set_text(self, text):
        self._text = str(text)

    def get_text(self):
        return self._text

    def get_child_count(self):
        return len(self.children)

    def get_child(self, idx):
        if idx < 0 or idx >= len(self.children):
            return None
        return self.children[idx]

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        _trace_missing("method", "{}.{}".format(self.__class__.__name__, name))

        if name.startswith("get_"):
            return lambda *_args, **_kwargs: 0
        if name.startswith("is_"):
            return lambda *_args, **_kwargs: False

        return lambda *_args, **_kwargs: None


class obj(_BaseObj):
    pass


class button(_BaseObj):
    pass


class label(_BaseObj):
    def set_long_mode(self, *_args):
        pass


class slider(_BaseObj):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._range_min = 0
        self._range_max = 100

    def set_range(self, min_val, max_val):
        self._range_min = int(min_val)
        self._range_max = int(max_val)

    def set_value(self, value, _anim):
        self._value = int(value)

    def get_value(self):
        return self._value


class textarea(_BaseObj):
    def add_text(self, text):
        self._text += str(text)

    def set_placeholder_text(self, _text):
        pass

    def set_one_line(self, _enabled):
        pass

    def set_text(self, text):
        self._text = str(text)

    def scroll_to_y(self, *_args):
        pass


class checkbox(_BaseObj):
    def set_state(self, _state, enabled=True):
        if enabled:
            self._flags |= STATE.CHECKED
        else:
            self._flags &= ~STATE.CHECKED


class switch(_BaseObj):
    def get_state(self):
        return self._flags

    def set_state(self, _state, enabled=True):
        if enabled:
            self._flags |= STATE.CHECKED
        else:
            self._flags &= ~STATE.CHECKED


class dropdown(_BaseObj):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._options = []
        self._selected = 0

    def set_dir(self, *_args):
        pass

    def set_options(self, options_text):
        if options_text:
            self._options = options_text.split("\n")
        else:
            self._options = []
        if self._selected >= len(self._options):
            self._selected = max(0, len(self._options) - 1)

    def set_selected(self, index):
        self._selected = int(index)

    def get_selected(self):
        return self._selected


class keyboard(_BaseObj):
    def get_selected_button(self):
        return 0

    def get_button_text(self, _button_index):
        return "a"

    def get_mode(self):
        return self._mode


class list(_BaseObj):
    def clean(self):
        for child in tuple(self.children):
            child.delete()

    def add_button(self, symbol, text):
        btn = button(self)
        label(btn).set_text(symbol)
        label(btn).set_text(text)
        return btn


class _TabView(_BaseObj):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._content = obj(self)
        self._tab_bar = obj(self)

    def set_tab_bar_position(self, _position):
        pass

    def set_tab_bar_size(self, _size):
        pass

    def add_tab(self, _name):
        return obj(self._content)

    def get_content(self):
        return self._content

    def get_tab_bar(self):
        return self._tab_bar


def tabview(parent=None):
    return _TabView(parent)


class _Group:
    _default = None

    def set_default(self):
        _Group._default = self


def group_create():
    return _Group()


class indev_t:
    def __init__(self):
        self._type = INDEV_TYPE.KEYPAD
        self._group = None

    def get_next(self):
        return self

    def get_type(self):
        return self._type

    def set_group(self, group):
        self._group = group


def screen_load(_screen):
    pass


def group_focus_obj(_obj):
    pass


def __getattr__(name):
    # Provide broad fallback for unknown LVGL symbols during migration.
    if name.isupper():
        _trace_missing("const", name)
        return 0

    if name and name[0].islower():
        _trace_missing("widget", name)

        def _factory(parent=None, *_args, **_kwargs):
            return obj(parent)

        return _factory

    _trace_missing("symbol", name)
    raise AttributeError(name)


def _build_public_exports():
    names = []
    for name in globals():
        if name.startswith("_"):
            continue
        names.append(name)
    return names


# `from lvgl_stub import *` is used by `lvgl.py`, so keep this explicit.
__all__ = _build_public_exports()
