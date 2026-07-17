from __future__ import annotations

import json
import os
import re
import wave
from pathlib import Path

import numpy as np


CASE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def validate_case_id(case_id: str) -> str:
    value = case_id.strip()
    if not CASE_ID_PATTERN.fullmatch(value):
        raise ValueError(
            "ID примера должен начинаться с буквы или цифры и содержать "
            "только латиницу, цифры, точку, дефис или подчёркивание"
        )
    return value


def write_mono_wav(
    path: Path,
    samples: np.ndarray,
    sample_rate: int = 16_000,
) -> None:
    if sample_rate <= 0:
        raise ValueError("Частота дискретизации должна быть положительной")
    prepared = np.asarray(samples, dtype=np.float32).reshape(-1).copy()
    np.nan_to_num(prepared, copy=False)
    np.clip(prepared, -1.0, 1.0, out=prepared)
    pcm = np.round(prepared * 32_767.0).astype("<i2")

    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with wave.open(str(temporary), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(sample_rate)
            output.writeframes(pcm.tobytes(order="C"))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def load_manifest_payload(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"version": 1, "cases": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise ValueError("Manifest должен содержать массив cases")
    if payload.get("version", 1) != 1:
        raise ValueError("Поддерживается только manifest версии 1")
    payload["version"] = 1
    return payload


def manifest_contains_case(path: Path, case_id: str) -> bool:
    validated = validate_case_id(case_id)
    return any(
        isinstance(case, dict) and case.get("id") == validated
        for case in load_manifest_payload(path).get("cases", [])
    )


def append_manifest_case(
    manifest_path: Path,
    *,
    case_id: str,
    audio_path: Path,
    reference: str,
    language: str = "ru",
    custom_terms: str = "",
    tags: tuple[str, ...] = (),
    overwrite: bool = False,
) -> dict[str, object]:
    validated = validate_case_id(case_id)
    manifest_path = manifest_path.resolve()
    audio_path = audio_path.resolve()
    payload = load_manifest_payload(manifest_path)
    cases = payload["cases"]
    assert isinstance(cases, list)

    relative_audio = Path(
        os.path.relpath(audio_path, manifest_path.parent)
    ).as_posix()
    clean_tags = tuple(dict.fromkeys(tag.strip() for tag in tags if tag.strip()))
    case: dict[str, object] = {
        "id": validated,
        "audio": relative_audio,
        "reference": reference.strip(),
        "language": language.strip() or "ru",
        "tags": list(clean_tags),
    }
    if custom_terms.strip():
        case["custom_terms"] = custom_terms.strip()

    matching_index = next(
        (
            index
            for index, existing in enumerate(cases)
            if isinstance(existing, dict) and existing.get("id") == validated
        ),
        None,
    )
    if matching_index is not None and not overwrite:
        raise ValueError(f"Пример с ID {validated!r} уже есть в manifest")
    if matching_index is None:
        cases.append(case)
    else:
        cases[matching_index] = case

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = manifest_path.with_name(f".{manifest_path.name}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(manifest_path)
    finally:
        temporary.unlink(missing_ok=True)
    return case
