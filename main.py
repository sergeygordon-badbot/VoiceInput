from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def safe_print(text: str, *, error: bool = False) -> None:
    stream = sys.stderr if error else sys.stdout
    if stream is not None:
        print(text, file=stream, flush=True)


def write_json_file(path: Path | None, payload: dict[str, object]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def run_self_test(output_path: Path | None = None) -> int:
    from voice_input.diagnostics import collect_diagnostics

    payload = collect_diagnostics()
    write_json_file(output_path, payload)
    safe_print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def transcribe_file(
    path: Path,
    model_name: str,
    language: str,
    output_path: Path | None = None,
) -> int:
    from faster_whisper.audio import decode_audio

    from voice_input.engine import WhisperEngine

    if not path.exists():
        payload = {"ok": False, "error": f"Файл не найден: {path}"}
        write_json_file(output_path, payload)
        safe_print(payload["error"], error=True)
        return 2

    engine = WhisperEngine()
    started = time.perf_counter()
    statuses: list[str] = []
    try:
        def status(message: str) -> None:
            statuses.append(message)
            safe_print(message)

        engine.load(model_name, status=status)
        loaded = time.perf_counter()
        samples = decode_audio(str(path), sampling_rate=16_000)
        text = engine.transcribe(samples, language=language)
        finished = time.perf_counter()
        payload = {
            "ok": True,
            "text": text,
            "statuses": statuses,
            "inference_profile": engine.inference_profile.to_dict(),
            "model_load_seconds": round(loaded - started, 3),
            "transcription_seconds": round(finished - loaded, 3),
        }
        write_json_file(output_path, payload)
        safe_print(text)
        safe_print(json.dumps(payload, ensure_ascii=False))
        return 0
    except Exception as exc:
        payload = {
            "ok": False,
            "error": str(exc),
            "statuses": statuses,
        }
        write_json_file(output_path, payload)
        safe_print(str(exc), error=True)
        return 1


def run_gui(minimized: bool) -> int:
    from PySide6.QtWidgets import QApplication

    from voice_input.app import VoiceInputApp
    from voice_input.windows import (
        close_handle,
        create_show_settings_event,
        create_single_instance_mutex,
        message_box,
        signal_show_settings_event,
    )

    mutex, already_running = create_single_instance_mutex()
    if already_running:
        if not signal_show_settings_event():
            message_box(
                "Программа уже запущена. Откройте «Речку» через значок "
                "в области уведомлений."
            )
        close_handle(mutex)
        return 0

    show_settings_event = create_show_settings_event()
    application = QApplication(sys.argv)
    application.setQuitOnLastWindowClosed(False)
    app = VoiceInputApp(
        application,
        start_minimized=minimized,
        show_settings_event=show_settings_event,
    )
    try:
        return application.exec()
    finally:
        if not app._closing:
            app.exit_app()
        close_handle(show_settings_event)
        close_handle(mutex)


def main() -> int:
    from voice_input.config import MODEL_OPTIONS

    parser = argparse.ArgumentParser(description="Локальный голосовой ввод для Windows")
    parser.add_argument("--minimized", action="store_true", help="Запустить в трее")
    parser.add_argument("--self-test", action="store_true", help="Проверить окружение")
    parser.add_argument("--transcribe", type=Path, help="Распознать аудиофайл")
    parser.add_argument(
        "--model",
        choices=tuple(MODEL_OPTIONS),
        default="small",
    )
    parser.add_argument("--language", default="ru")
    parser.add_argument("--output", type=Path, help="Записать диагностику в JSON")
    args = parser.parse_args()

    if args.self_test:
        return run_self_test(args.output)
    if args.transcribe:
        return transcribe_file(
            args.transcribe,
            args.model,
            args.language,
            args.output,
        )
    return run_gui(args.minimized)


if __name__ == "__main__":
    raise SystemExit(main())
