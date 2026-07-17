from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import data_dir


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    created_at: str
    text: str
    raw_text: str
    mode: str
    application: str = ""


def history_path() -> Path:
    return data_dir() / "history.json"


def load_history(path: Path | None = None, limit: int = 100) -> list[HistoryEntry]:
    target = (path or history_path()).resolve()
    if not target.exists():
        return []
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    entries: list[HistoryEntry] = []
    for item in payload[-max(0, limit) :]:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            continue
        entries.append(
            HistoryEntry(
                created_at=str(item.get("created_at", "")),
                text=item["text"],
                raw_text=str(item.get("raw_text", "")),
                mode=str(item.get("mode", "communication")),
                application=str(item.get("application", "")),
            )
        )
    return entries


def append_history(
    *,
    text: str,
    raw_text: str,
    mode: str,
    application: str = "",
    path: Path | None = None,
    max_entries: int = 100,
) -> HistoryEntry:
    target = (path or history_path()).resolve()
    entry = HistoryEntry(
        created_at=datetime.now(timezone.utc).isoformat(),
        text=text,
        raw_text=raw_text,
        mode=mode,
        application=application,
    )
    entries = load_history(target, limit=max_entries)
    entries.append(entry)
    entries = entries[-max(1, max_entries) :]
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    try:
        temporary.write_text(
            json.dumps(
                [asdict(item) for item in entries],
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return entry


def clear_history(path: Path | None = None) -> None:
    target = (path or history_path()).resolve()
    target.unlink(missing_ok=True)
