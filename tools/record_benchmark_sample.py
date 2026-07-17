from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from voice_input.audio import AudioRecorder, analyze_audio, list_input_devices
from voice_input.benchmark_corpus import (
    append_manifest_case,
    manifest_contains_case,
    validate_case_id,
    write_mono_wav,
)


def _print_devices() -> None:
    devices = list_input_devices()
    if not devices:
        print("Входные аудиоустройства не найдены")
        return
    for device in devices:
        default = " · по умолчанию" if device["is_default"] else ""
        print(
            f"{device['index']}: {device['name']} · "
            f"{device['sample_rate']} Гц{default}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Записать локальный пример для benchmark Речки",
    )
    parser.add_argument(
        "manifest",
        nargs="?",
        type=Path,
        default=ROOT / "benchmarks" / "user" / "manifest.json",
    )
    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument("--id", dest="case_id")
    parser.add_argument("--reference")
    parser.add_argument("--seconds", type=float, default=8.0)
    parser.add_argument("--countdown", type=int, default=3)
    parser.add_argument("--device", type=int)
    parser.add_argument("--language", default="ru")
    parser.add_argument("--custom-terms", default="")
    parser.add_argument("--tags", default="real,clean")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    if args.list_devices:
        _print_devices()
        return 0
    if args.case_id is None or args.reference is None:
        parser.error("для записи нужны --id и --reference")
    if not 0.5 <= args.seconds <= 120.0:
        parser.error("--seconds должен быть от 0.5 до 120")

    case_id = validate_case_id(args.case_id)
    manifest_path = args.manifest.resolve()
    if manifest_contains_case(manifest_path, case_id) and not args.overwrite:
        raise SystemExit(
            f"Пример {case_id!r} уже существует. Для перезаписи добавьте --overwrite"
        )
    audio_path = manifest_path.parent / "audio" / f"{case_id}.wav"
    if audio_path.exists() and not args.overwrite:
        raise SystemExit(
            f"Файл {audio_path} уже существует. Для перезаписи добавьте --overwrite"
        )

    print(f"Текст: {args.reference or '[тишина — ничего не говорите]'}")
    for remaining in range(max(0, args.countdown), 0, -1):
        print(f"Запись через {remaining}…", flush=True)
        time.sleep(1)

    recorder = AudioRecorder()
    try:
        sample_rate = recorder.start(args.device)
        print(
            f"Запись {args.seconds:g} сек. · источник {sample_rate} Гц…",
            flush=True,
        )
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            health_error = recorder.health_error
            if health_error:
                raise RuntimeError(health_error)
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        clip = recorder.stop()
    except BaseException:
        if recorder.is_recording:
            recorder.abort()
        raise

    quality = analyze_audio(clip.samples)
    write_mono_wav(audio_path, clip.samples)
    case = append_manifest_case(
        manifest_path,
        case_id=case_id,
        audio_path=audio_path,
        reference=args.reference,
        language=args.language,
        custom_terms=args.custom_terms,
        tags=tuple(args.tags.split(",")),
        overwrite=args.overwrite,
    )
    print(
        f"Готово: {audio_path}\n"
        f"Manifest: {manifest_path}\n"
        f"Уровень: {quality.dbfs:.1f} dBFS · "
        f"клиппинг {quality.clipped_fraction:.2%}\n"
        f"Теги: {', '.join(case['tags']) or 'нет'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
