from __future__ import annotations

import re


def parse_action_command(text: str) -> str | None:
    """Return an app action only when the whole transcript is an explicit command."""
    normalized = re.sub(r"[^\w\s-]", " ", text.casefold().replace("ё", "е"))
    normalized = re.sub(r"\s+", " ", normalized).strip()
    commands = {
        "отмени": "undo",
        "отмена": "undo",
        "отмени последнее": "undo",
        "удали последнее": "undo",
        "удали последнюю вставку": "undo",
        "нажми enter": "enter",
        "нажми энтер": "enter",
        "enter": "enter",
        "энтер": "enter",
        "повтори запись": "repeat",
        "запиши заново": "repeat",
    }
    return commands.get(normalized)
