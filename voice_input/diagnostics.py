from __future__ import annotations

import os
import platform
import sys
from datetime import datetime, timezone
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version

import sounddevice as sd

from . import __version__
from .audio import PREVIEW_BUFFER_SECONDS, TARGET_SAMPLE_RATE, list_input_devices
from .engine import DEFAULT_VAD_PROFILE, VAD_PROFILES
from .hardware import detect_inference_profile


_PACKAGE_MODULES = {
    "faster-whisper": "faster_whisper",
    "ctranslate2": "ctranslate2",
    "av": "av",
    "sounddevice": "sounddevice",
    "PySide6": "PySide6",
}


def _installed_version(package: str, module_name: str) -> str:
    try:
        return version(package)
    except PackageNotFoundError:
        # PyInstaller can omit distribution metadata even though the module and
        # its native libraries are bundled and importable.
        try:
            module = import_module(module_name)
        except Exception:
            return "not-installed"
        module_version = getattr(module, "__version__", None)
        return str(module_version) if module_version else "bundled"


def _package_versions() -> dict[str, str]:
    return {
        package: _installed_version(package, module_name)
        for package, module_name in _PACKAGE_MODULES.items()
    }


def _default_device_indices() -> list[int]:
    return [int(item) for item in sd.default.device]


def collect_diagnostics(
    *,
    selected_device_index: int | None = None,
    model_name: str | None = None,
) -> dict[str, object]:
    errors: list[str] = []
    try:
        inference_profile: dict[str, object] = (
            detect_inference_profile().to_dict()
        )
    except Exception as exc:
        inference_profile = {"error": str(exc)}
        errors.append(f"inference_profile: {exc}")

    try:
        default_device = _default_device_indices()
        input_devices = list_input_devices()
    except Exception as exc:
        default_device = []
        input_devices = []
        errors.append(f"audio_devices: {exc}")

    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "app_version": __version__,
        "python": sys.version,
        "platform": sys.platform,
        "windows": platform.platform(),
        "cpu_count": os.cpu_count(),
        "packages": _package_versions(),
        "inference_profile": inference_profile,
        "model": model_name,
        "selected_device_index": selected_device_index,
        "default_device": default_device,
        "input_devices": input_devices,
        "recording": {
            "target_sample_rate": TARGET_SAMPLE_RATE,
            "preview_buffer_seconds": PREVIEW_BUFFER_SECONDS,
            "temporary_local_spool": True,
        },
        "recognition": {
            "vad_profile": DEFAULT_VAD_PROFILE,
            "vad_parameters": VAD_PROFILES[DEFAULT_VAD_PROFILE].to_dict(),
            "condition_on_previous_text": "long-recordings-only",
        },
        "privacy": {
            "contains_audio": False,
            "contains_transcript": False,
            "contains_custom_terms": False,
        },
        "errors": errors,
    }
