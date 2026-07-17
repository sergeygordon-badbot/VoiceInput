from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from voice_input.benchmark_corpus import append_manifest_case, write_mono_wav


SAMPLE_RATE = 16_000


def _edge_cases() -> dict[str, tuple[np.ndarray, tuple[str, ...]]]:
    rng = np.random.default_rng(20_260_717)
    duration = 6
    timeline = np.arange(duration * SAMPLE_RATE, dtype=np.float32) / SAMPLE_RATE

    silence = np.zeros_like(timeline)
    room_noise = (
        rng.normal(0.0, 0.0012, timeline.size)
        + 0.0006 * np.sin(2 * np.pi * 50 * timeline)
    ).astype(np.float32)
    fan_hum = (
        0.006 * np.sin(2 * np.pi * 120 * timeline)
        + 0.0025 * np.sin(2 * np.pi * 240 * timeline)
        + rng.normal(0.0, 0.0009, timeline.size)
    ).astype(np.float32)
    keyboard = rng.normal(0.0, 0.0005, timeline.size).astype(np.float32)
    click_shape = np.exp(-np.arange(420, dtype=np.float32) / 55.0)
    for index, start in enumerate(np.linspace(0.6, 5.2, 12)):
        offset = round(float(start) * SAMPLE_RATE)
        end = min(keyboard.size, offset + click_shape.size)
        sign = -1.0 if index % 2 else 1.0
        keyboard[offset:end] += sign * 0.16 * click_shape[: end - offset]
    np.clip(keyboard, -0.95, 0.95, out=keyboard)

    return {
        "silence-digital": (silence, ("silence", "generated")),
        "noise-room-low": (room_noise, ("silence", "noise", "generated")),
        "noise-fan-hum": (fan_hum, ("silence", "noise", "generated")),
        "noise-keyboard-clicks": (
            keyboard,
            ("silence", "noise", "impulses", "generated"),
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Создать воспроизводимые шумовые примеры для benchmark",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "test-output" / "vad-edge-cases",
    )
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    manifest_path = output_dir / "manifest.json"
    for case_id, (samples, tags) in _edge_cases().items():
        audio_path = output_dir / "audio" / f"{case_id}.wav"
        write_mono_wav(audio_path, samples, SAMPLE_RATE)
        append_manifest_case(
            manifest_path,
            case_id=case_id,
            audio_path=audio_path,
            reference="",
            tags=tags,
            overwrite=True,
        )
    print(f"Создано {len(_edge_cases())} примера: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
