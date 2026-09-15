from __future__ import annotations

from evdev import ecodes as e

KEY_MAP: dict[str, int] = {
    "a": e.KEY_A, "b": e.KEY_B, "c": e.KEY_C, "d": e.KEY_D,
    "e": e.KEY_E, "f": e.KEY_F, "g": e.KEY_G, "h": e.KEY_H,
    "i": e.KEY_I, "j": e.KEY_J, "k": e.KEY_K, "l": e.KEY_L,
    "m": e.KEY_M, "n": e.KEY_N, "o": e.KEY_O, "p": e.KEY_P,
    "q": e.KEY_Q, "r": e.KEY_R, "s": e.KEY_S, "t": e.KEY_T,
    "u": e.KEY_U, "v": e.KEY_V, "w": e.KEY_W, "x": e.KEY_X,
    "y": e.KEY_Y, "z": e.KEY_Z,

    "0": e.KEY_0, "1": e.KEY_1, "2": e.KEY_2, "3": e.KEY_3,
    "4": e.KEY_4, "5": e.KEY_5, "6": e.KEY_6, "7": e.KEY_7,
    "8": e.KEY_8, "9": e.KEY_9,

    ".": e.KEY_DOT, ",": e.KEY_COMMA, ";": e.KEY_SEMICOLON,
    "'": e.KEY_APOSTROPHE, "-": e.KEY_MINUS, "=": e.KEY_EQUAL,
    "/": e.KEY_SLASH, "\\": e.KEY_BACKSLASH,
    "[": e.KEY_LEFTBRACE, "]": e.KEY_RIGHTBRACE,
    "`": e.KEY_GRAVE,

    "shift": e.KEY_LEFTSHIFT, "ctrl": e.KEY_LEFTCTRL, "alt": e.KEY_LEFTALT,
    "meta": e.KEY_LEFTMETA, "win": e.KEY_LEFTMETA,

    "f1": e.KEY_F1, "f2": e.KEY_F2, "f3": e.KEY_F3, "f4": e.KEY_F4,
    "f5": e.KEY_F5, "f6": e.KEY_F6, "f7": e.KEY_F7, "f8": e.KEY_F8,
    "f9": e.KEY_F9, "f10": e.KEY_F10, "f11": e.KEY_F11, "f12": e.KEY_F12,
    "f13": e.KEY_F13, "f14": e.KEY_F14, "f15": e.KEY_F15, "f16": e.KEY_F16,
    "f17": e.KEY_F17, "f18": e.KEY_F18, "f19": e.KEY_F19, "f20": e.KEY_F20,
    "f21": e.KEY_F21, "f22": e.KEY_F22, "f23": e.KEY_F23, "f24": e.KEY_F24,

    "up": e.KEY_UP, "down": e.KEY_DOWN, "left": e.KEY_LEFT, "right": e.KEY_RIGHT,
    "home": e.KEY_HOME, "end": e.KEY_END, "pageup": e.KEY_PAGEUP, "pagedown": e.KEY_PAGEDOWN,
    "insert": e.KEY_INSERT, "delete": e.KEY_DELETE, "esc": e.KEY_ESC, "tab": e.KEY_TAB,
    "enter": e.KEY_ENTER, "backspace": e.KEY_BACKSPACE, "space": e.KEY_SPACE,
}  # fmt: skip

MOUSE_MAP: dict[str, int] = {
    "left_mouse": e.BTN_LEFT,
    "right_mouse": e.BTN_RIGHT,
}

MODIFIER_KEYS = frozenset({"shift", "ctrl", "alt", "meta", "win"})

_UNSHIFTED = "abcdefghijklmnopqrstuvwxyz0123456789`-=[]\\;',./"

_SHIFTED = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ)!@#$%^&*(~_+{}|:"<>?'

CHARACTERS: dict[str, tuple[str, bool]] = {
    **{c: (c, False) for c in _UNSHIFTED},
    **{s: (u, True) for u, s in zip(_UNSHIFTED, _SHIFTED, strict=True)},
    " ": ("space", False),
    "\n": ("enter", False),
    "\t": ("tab", False),
}


def untypeable(text: str) -> list[str]:
    """The distinct characters of text that CHARACTERS cannot produce."""
    seen: dict[str, None] = {}
    for character in text:
        if character not in CHARACTERS:
            seen[character] = None
    return list(seen)


def is_known_key(name: str) -> bool:
    """Whether name names a key or a mouse button."""
    lowered = name.lower()
    return lowered in KEY_MAP or lowered in MOUSE_MAP


def key_code(name: str) -> tuple[int, bool]:
    """(code, is_mouse_button) for a key name; KeyError for an unknown one."""
    lowered = name.lower()
    if lowered in KEY_MAP:
        return KEY_MAP[lowered], False
    return MOUSE_MAP[lowered], True
