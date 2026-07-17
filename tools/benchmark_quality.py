from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from faster_whisper.audio import decode_audio

from voice_input.audio import prepare_audio_for_whisper
from voice_input.benchmarking import (
    TranscriptScore,
    aggregate_scores,
    load_benchmark_manifest,
    score_transcript,
)
from voice_input.engine import (
    DEFAULT_VAD_PROFILE,
    VAD_PROFILES,
    WhisperEngine,
    detect_speech_regions,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Измерить качество и скорость распознавания Речки",
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--model", default="base")
    parser.add_argument("--beam-size", type=int, default=1)
    parser.add_argument(
        "--vad-profile",
        choices=tuple(VAD_PROFILES),
        default=DEFAULT_VAD_PROFILE,
        help="Внутренний VAD-профиль для A/B-сравнения",
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--without-custom-terms",
        action="store_true",
        help="Отключить словарь для контрольного сравнения",
    )
    parser.add_argument(
        "--fail-on-hallucinations",
        action="store_true",
        help="Вернуть ненулевой код, если тишина породила хотя бы одно слово",
    )
    parser.add_argument(
        "--max-wer",
        type=float,
        help="Вернуть ненулевой код, если общий WER превышает этот порог (0..1)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "test-output" / "quality-benchmark.json",
    )
    args = parser.parse_args()
    if args.max_wer is not None and not 0.0 <= args.max_wer <= 1.0:
        parser.error("--max-wer должен быть от 0 до 1")

    cases = load_benchmark_manifest(args.manifest.resolve())
    if args.limit is not None:
        cases = cases[: max(0, args.limit)]
    if not cases:
        raise SystemExit("После применения --limit не осталось тестов")

    engine = WhisperEngine()
    engine.load(args.model, status=lambda message: print(message, flush=True))
    results: list[dict[str, object]] = []
    scores = []
    total_audio_seconds = 0.0
    total_transcription_seconds = 0.0
    tag_scores: dict[str, list[TranscriptScore]] = {}
    tag_audio_seconds: dict[str, float] = {}
    tag_transcription_seconds: dict[str, float] = {}
    tag_speech_seconds: dict[str, float] = {}
    total_speech_seconds = 0.0

    for case in cases:
        samples = decode_audio(str(case.audio_path), sampling_rate=16_000)
        audio_seconds = len(samples) / 16_000
        started = time.perf_counter()
        hypothesis = engine.transcribe(
            samples,
            language=case.language,
            beam_size=args.beam_size,
            custom_terms="" if args.without_custom_terms else case.custom_terms,
            vad_profile=args.vad_profile,
        )
        elapsed = time.perf_counter() - started
        speech_regions = detect_speech_regions(
            prepare_audio_for_whisper(samples),
            args.vad_profile,
        )
        speech_seconds = sum(end - start for start, end in speech_regions) / 16_000
        score = score_transcript(case.reference, hypothesis)
        scores.append(score)
        total_audio_seconds += audio_seconds
        total_transcription_seconds += elapsed
        total_speech_seconds += speech_seconds
        for tag in case.tags:
            tag_scores.setdefault(tag, []).append(score)
            tag_audio_seconds[tag] = tag_audio_seconds.get(tag, 0.0) + audio_seconds
            tag_transcription_seconds[tag] = (
                tag_transcription_seconds.get(tag, 0.0) + elapsed
            )
            tag_speech_seconds[tag] = (
                tag_speech_seconds.get(tag, 0.0) + speech_seconds
            )
        result = {
            "id": case.case_id,
            "audio": str(case.audio_path),
            "tags": list(case.tags),
            "reference": case.reference,
            "hypothesis": hypothesis,
            "audio_seconds": round(audio_seconds, 3),
            "transcription_seconds": round(elapsed, 3),
            "real_time_factor": elapsed / max(0.001, audio_seconds),
            "speech_regions": len(speech_regions),
            "speech_seconds": round(speech_seconds, 3),
            "speech_ratio": speech_seconds / max(0.001, audio_seconds),
            **score.to_dict(),
        }
        results.append(result)
        print(
            f"{case.case_id}: WER={score.wer:.1%}, "
            f"RTF={result['real_time_factor']:.2f}",
            flush=True,
        )

    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": args.model,
        "beam_size": args.beam_size,
        "vad_profile": args.vad_profile,
        "custom_terms_enabled": not args.without_custom_terms,
        "inference_profile": engine.inference_profile.to_dict(),
        "aggregate": {
            **aggregate_scores(scores),
            "audio_seconds": round(total_audio_seconds, 3),
            "transcription_seconds": round(total_transcription_seconds, 3),
            "real_time_factor": total_transcription_seconds
            / max(0.001, total_audio_seconds),
            "speech_seconds": round(total_speech_seconds, 3),
            "speech_ratio": total_speech_seconds / max(0.001, total_audio_seconds),
        },
        "by_tag": {
            tag: {
                **aggregate_scores(scores_for_tag),
                "audio_seconds": round(tag_audio_seconds[tag], 3),
                "transcription_seconds": round(
                    tag_transcription_seconds[tag],
                    3,
                ),
                "real_time_factor": tag_transcription_seconds[tag]
                / max(0.001, tag_audio_seconds[tag]),
                "speech_seconds": round(tag_speech_seconds[tag], 3),
                "speech_ratio": tag_speech_seconds[tag]
                / max(0.001, tag_audio_seconds[tag]),
            }
            for tag, scores_for_tag in sorted(tag_scores.items())
        },
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Отчёт: {args.output.resolve()}", flush=True)
    hallucinated_words = int(payload["aggregate"]["hallucinated_words"])
    if args.fail_on_hallucinations and hallucinated_words:
        print(
            f"Ошибка качества: на тишине выдумано слов: {hallucinated_words}",
            file=sys.stderr,
            flush=True,
        )
        return 2
    aggregate_wer = float(payload["aggregate"]["wer"])
    if args.max_wer is not None and aggregate_wer > args.max_wer:
        print(
            f"Ошибка качества: WER {aggregate_wer:.1%} выше {args.max_wer:.1%}",
            file=sys.stderr,
            flush=True,
        )
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
