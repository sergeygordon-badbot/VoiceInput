from __future__ import annotations

import re

from .prompting import polish_communication_text


QUICK_ACTION_OPTIONS = {
    "message": "Сообщение",
    "email": "Письмо",
    "list": "Список",
    "task": "Задача",
}


def _sentences(text: str) -> list[str]:
    source = polish_communication_text(text)
    parts = re.split(r"(?<=[.!?])\s+|\n+", source)
    return [part.strip(" -\t") for part in parts if part.strip(" -\t")]


def apply_quick_action(text: str, action: str) -> str:
    source = text.strip()
    if not source:
        return ""
    if action == "message":
        return polish_communication_text(source)
    if action == "email":
        body = polish_communication_text(source)
        return f"Здравствуйте!\n\n{body}\n\nС уважением,"
    if action == "list":
        return "\n".join(f"• {item}" for item in _sentences(source))
    if action == "task":
        items = _sentences(source)
        return "\n".join(f"- [ ] {item}" for item in items)
    raise ValueError(f"Неизвестное быстрое действие: {action}")
