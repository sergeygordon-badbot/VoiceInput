from __future__ import annotations

from dataclasses import dataclass


MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008

MODIFIER_ORDER = ("Ctrl", "Alt", "Shift", "Win")
MODIFIER_VALUES = {
    "Ctrl": MOD_CONTROL,
    "Alt": MOD_ALT,
    "Shift": MOD_SHIFT,
    "Win": MOD_WIN,
}
MODIFIER_ALIASES = {
    "control": "Ctrl",
    "ctrl": "Ctrl",
    "alt": "Alt",
    "shift": "Shift",
    "win": "Win",
    "windows": "Win",
    "meta": "Win",
}
KEY_ALIASES = {
    "space": "Space",
    "пробел": "Space",
    "enter": "Enter",
    "return": "Enter",
    "tab": "Tab",
    "insert": "Insert",
    "ins": "Insert",
    "delete": "Delete",
    "del": "Delete",
    "home": "Home",
    "end": "End",
    "pageup": "PageUp",
    "pgup": "PageUp",
    "pagedown": "PageDown",
    "pgdown": "PageDown",
    "esc": "Escape",
    "escape": "Escape",
    "backspace": "Backspace",
    "pause": "Pause",
}
KEY_VALUES = {
    "Space": 0x20,
    "Enter": 0x0D,
    "Tab": 0x09,
    "Insert": 0x2D,
    "Delete": 0x2E,
    "Home": 0x24,
    "End": 0x23,
    "PageUp": 0x21,
    "PageDown": 0x22,
    "Escape": 0x1B,
    "Backspace": 0x08,
    "Pause": 0x13,
}
KEY_LABELS = {
    "Space": "Пробел",
    "Enter": "Enter",
    "Tab": "Tab",
    "Insert": "Insert",
    "Delete": "Delete",
    "Home": "Home",
    "End": "End",
    "PageUp": "Page Up",
    "PageDown": "Page Down",
    "Escape": "Esc",
    "Backspace": "Backspace",
    "Pause": "Pause",
}

LEGACY_HOTKEYS = {
    "ctrl_alt_space": "Ctrl+Alt+Space",
    "ctrl_shift_space": "Ctrl+Shift+Space",
    "ctrl_alt_f8": "Ctrl+Alt+F8",
    "f8": "F8",
}

HOTKEY_OPTIONS = {
    "Ctrl+Alt+Space": "Ctrl + Alt + Пробел",
    "Ctrl+Shift+Space": "Ctrl + Shift + Пробел",
    "Ctrl+Alt+F8": "Ctrl + Alt + F8",
    "Ctrl+Shift+F8": "Ctrl + Shift + F8",
    "F8": "F8",
}


@dataclass(frozen=True, slots=True)
class HotkeySpec:
    canonical: str
    label: str
    modifiers: int
    virtual_key: int


def _normalize_key(value: str) -> tuple[str, int]:
    clean = value.strip()
    alias = KEY_ALIASES.get(clean.lower())
    if alias:
        return alias, KEY_VALUES[alias]

    upper = clean.upper()
    if len(upper) == 1 and "A" <= upper <= "Z":
        return upper, ord(upper)
    if len(upper) == 1 and "0" <= upper <= "9":
        return upper, ord(upper)
    if upper.startswith("F") and upper[1:].isdigit():
        number = int(upper[1:])
        if 1 <= number <= 24:
            return f"F{number}", 0x70 + number - 1
    raise ValueError(
        "Поддерживаются буквы A–Z, цифры 0–9, F1–F24, пробел, Enter, "
        "Tab, Insert, Delete, Home, End, Page Up/Down и Esc."
    )


def parse_hotkey(value: str, *, allow_unmodified: bool = False) -> HotkeySpec:
    if not isinstance(value, str):
        raise ValueError("Введите сочетание клавиш.")
    source = LEGACY_HOTKEYS.get(value.strip().lower(), value.strip())
    parts = [part.strip() for part in source.replace(" ", "").split("+") if part.strip()]
    if not parts:
        raise ValueError("Введите сочетание клавиш.")

    modifiers: set[str] = set()
    key_parts: list[str] = []
    for part in parts:
        modifier = MODIFIER_ALIASES.get(part.lower())
        if modifier:
            modifiers.add(modifier)
        else:
            key_parts.append(part)

    if len(key_parts) != 1:
        raise ValueError("Сочетание должно содержать одну основную клавишу.")

    key_name, virtual_key = _normalize_key(key_parts[0])
    if not allow_unmodified and not modifiers and not key_name.startswith("F"):
        raise ValueError("Добавьте Ctrl, Alt, Shift или Win.")

    ordered_modifiers = [name for name in MODIFIER_ORDER if name in modifiers]
    modifier_value = 0
    for name in ordered_modifiers:
        modifier_value |= MODIFIER_VALUES[name]

    canonical = "+".join([*ordered_modifiers, key_name])
    key_label = KEY_LABELS.get(key_name, key_name)
    label = " + ".join([*ordered_modifiers, key_label])
    return HotkeySpec(
        canonical=canonical,
        label=label,
        modifiers=modifier_value,
        virtual_key=virtual_key,
    )


def normalize_hotkey(value: str, fallback: str = "Ctrl+Alt+Space") -> str:
    try:
        return parse_hotkey(value).canonical
    except (TypeError, ValueError):
        return parse_hotkey(fallback).canonical


def hotkey_label(value: str) -> str:
    return parse_hotkey(value).label
