from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from .hotkeys import HOTKEY_OPTIONS, normalize_hotkey


APP_DIR_NAME = "Rechka"
LEGACY_APP_DIR_NAME = "VoiceInput"

RECOGNITION_MODE_OPTIONS = {
    "auto": "Авто — подобрать по компьютеру",
    "cloud": "Онлайн — быстрее, нужен интернет",
    "local": "Локально — без отправки аудио",
}

MODEL_OPTIONS = {
    "tiny": "Tiny — самый лёгкий резерв для слабых компьютеров",
    "base": "Base — рекомендуется для CPU: быстро",
    "small": "Small — точнее Base, но заметно медленнее",
    "turbo": "Turbo — очень медленно на CPU, нужна мощная видеокарта (~1,6 ГБ)",
    "medium": "Medium — медленно на CPU (~1,5 ГБ)",
}

MODEL_REPOSITORIES = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "turbo": "dropbox-dash/faster-whisper-large-v3-turbo",
    "medium": "Systran/faster-whisper-medium",
}

MODEL_FILES = {
    "tiny": (
        "config.json",
        "model.bin",
        "tokenizer.json",
        "vocabulary.txt",
    ),
    "base": (
        "config.json",
        "model.bin",
        "tokenizer.json",
        "vocabulary.txt",
    ),
    "small": (
        "config.json",
        "model.bin",
        "tokenizer.json",
        "vocabulary.txt",
    ),
    "turbo": (
        "config.json",
        "preprocessor_config.json",
        "model.bin",
        "tokenizer.json",
        "vocabulary.json",
    ),
    "medium": (
        "config.json",
        "model.bin",
        "tokenizer.json",
        "vocabulary.txt",
    ),
}

MODEL_DOWNLOAD_DESCRIPTIONS = {
    "tiny": "~75 МБ",
    "base": "~150 МБ",
    "small": "~470 МБ",
    "turbo": "~1,6 ГБ",
    "medium": "~1,5 ГБ",
}

DECODING_OPTIONS = {
    "fast": "Быстро — минимальная задержка",
    "balanced": "Баланс — немного точнее",
    "accurate": "Точно — заметно медленнее",
}

DECODING_BEAM_SIZES = {
    "fast": 1,
    "balanced": 2,
    "accurate": 3,
}

OUTPUT_MODE_OPTIONS = {
    "verbatim": "Дословно — только распознавание",
    "communication": "Общение — близко к оригиналу",
    "ai_prompt": "Промпт для AI — структурированная задача",
    "custom": "Свой режим — локальная инструкция",
}

AI_TARGET_OPTIONS = {
    "universal": "Универсальный промпт",
    "chatgpt": "ChatGPT",
    "claude": "Claude",
    "gemini": "Gemini",
}

LANGUAGE_OPTIONS = {
    "ru": "Русский",
    "auto": "Автоопределение",
    "en": "Английский",
}

INSERTION_OPTIONS = {
    "paste": "Буфер обмена + Ctrl+V",
    "type": "Прямая печать (не меняет буфер)",
    "clipboard": "Только скопировать в буфер",
}

@dataclass(slots=True)
class AppConfig:
    recognition_mode: str = "auto"
    model: str = "base"
    decoding_mode: str = "balanced"
    output_mode: str = "communication"
    ai_target: str = "universal"
    language: str = "ru"
    device_index: int | None = None
    insertion_mode: str = "paste"
    hotkey: str = "Ctrl+Space"
    append_space: bool = True
    punctuation_commands: bool = True
    start_minimized: bool = False
    autostart: bool = False
    sound_feedback: bool = False
    custom_terms: str = ""
    snippets: str = ""
    app_profiles: str = ""
    project_context: str = ""
    custom_instruction: str = ""
    history_enabled: bool = False
    use_local_ai: bool = False
    ollama_model: str = "qwen3:4b"
    beam_size: int = 2
    onboarding_complete: bool = False
    settings_revision: int = 7


def data_dir() -> Path:
    override = os.environ.get("RECHKA_DATA_DIR") or os.environ.get(
        "VOICE_INPUT_DATA_DIR"
    )
    if override:
        return Path(override).expanduser().resolve()

    root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if root:
        target = Path(root) / APP_DIR_NAME
        legacy = Path(root) / LEGACY_APP_DIR_NAME
        if not target.exists() and legacy.is_dir():
            try:
                legacy.rename(target)
            except OSError:
                # Keep the existing settings available if Windows temporarily
                # blocks the one-time directory migration.
                return legacy
        return target
    return Path.home() / f".{APP_DIR_NAME.lower()}"


def settings_path() -> Path:
    return data_dir() / "settings.json"


def bundled_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def bundled_model_path(model_name: str) -> Path:
    return bundled_root() / "models" / f"faster-whisper-{model_name}"


def downloaded_model_path(model_name: str) -> Path:
    return data_dir() / "models" / f"faster-whisper-{model_name}"


def load_config() -> AppConfig:
    path = settings_path()
    if not path.exists():
        return AppConfig()

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return AppConfig()

    allowed = {item.name for item in fields(AppConfig)}
    clean: dict[str, Any] = {key: value for key, value in payload.items() if key in allowed}
    is_legacy_config = "decoding_mode" not in payload
    try:
        settings_revision = int(payload.get("settings_revision", 0) or 0)
    except (TypeError, ValueError):
        settings_revision = 0
    is_performance_legacy = settings_revision < 2
    is_hotkey_legacy = settings_revision < 4
    is_decoding_legacy = settings_revision < 5
    is_existing_before_onboarding = "onboarding_complete" not in payload
    config = AppConfig(**clean)

    if config.recognition_mode not in RECOGNITION_MODE_OPTIONS:
        config.recognition_mode = "auto"
    if config.model not in MODEL_OPTIONS:
        config.model = "base"
    elif (
        is_legacy_config or is_performance_legacy
    ) and config.model in {"medium", "turbo"}:
        config.model = "base"
    if config.decoding_mode not in DECODING_OPTIONS:
        config.decoding_mode = "balanced"
    elif is_decoding_legacy and config.decoding_mode == "fast":
        config.decoding_mode = "balanced"
    if config.output_mode not in OUTPUT_MODE_OPTIONS:
        config.output_mode = "communication"
    if config.ai_target not in AI_TARGET_OPTIONS:
        config.ai_target = "universal"
    if config.language not in LANGUAGE_OPTIONS:
        config.language = "ru"
    if config.insertion_mode not in INSERTION_OPTIONS:
        config.insertion_mode = "paste"
    config.hotkey = normalize_hotkey(config.hotkey)
    if is_hotkey_legacy and config.hotkey == "Ctrl+Alt+Space":
        config.hotkey = "Ctrl+Space"
    if is_performance_legacy:
        config.use_local_ai = False
    if is_existing_before_onboarding:
        config.onboarding_complete = True
    config.settings_revision = 7
    config.beam_size = DECODING_BEAM_SIZES[config.decoding_mode]
    return config


def save_config(config: AppConfig) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(asdict(config), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)
