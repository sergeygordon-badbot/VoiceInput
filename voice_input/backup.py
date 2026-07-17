from __future__ import annotations

import json
from pathlib import Path

from .config import AppConfig


PERSONALIZATION_FIELDS = (
    "custom_terms",
    "snippets",
    "app_profiles",
    "project_context",
    "custom_instruction",
    "history_enabled",
)


def export_personalization(path: Path, config: AppConfig) -> None:
    payload = {
        "format": "rechka-personalization",
        "version": 1,
        "data": {
            field: getattr(config, field)
            for field in PERSONALIZATION_FIELDS
        },
    }
    target = path.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def import_personalization(path: Path) -> dict[str, str | bool]:
    payload = json.loads(path.resolve().read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("format") != "rechka-personalization":
        raise ValueError("Это не файл персонализации Речки")
    if payload.get("version") != 1 or not isinstance(payload.get("data"), dict):
        raise ValueError("Неподдерживаемая версия файла персонализации")
    data = payload["data"]
    result: dict[str, str | bool] = {}
    for field in PERSONALIZATION_FIELDS:
        if field not in data:
            continue
        value = data[field]
        if field == "history_enabled":
            if not isinstance(value, bool):
                raise ValueError("history_enabled должен быть логическим значением")
        elif not isinstance(value, str):
            raise ValueError(f"{field} должен быть строкой")
        result[field] = value
    return result
