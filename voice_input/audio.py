from __future__ import annotations

import threading
import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import sounddevice as sd


TARGET_SAMPLE_RATE = 16_000
NORMALIZATION_TARGET_RMS = 0.055
NORMALIZATION_MAX_GAIN = 4.0


@dataclass(slots=True)
class AudioClip:
    samples: np.ndarray
    duration_seconds: float
    rms: float


def resample_audio(
    samples: np.ndarray,
    source_rate: int,
    target_rate: int = TARGET_SAMPLE_RATE,
) -> np.ndarray:
    samples = np.asarray(samples, dtype=np.float32).reshape(-1)
    if samples.size == 0 or source_rate == target_rate:
        return samples.astype(np.float32, copy=False)

    target_length = max(1, round(samples.size * target_rate / source_rate))
    source_positions = np.arange(samples.size, dtype=np.float64)
    target_positions = np.linspace(
        0,
        samples.size - 1,
        num=target_length,
        dtype=np.float64,
    )
    return np.interp(target_positions, source_positions, samples).astype(np.float32)


def prepare_audio_for_whisper(samples: np.ndarray) -> np.ndarray:
    prepared = np.asarray(samples, dtype=np.float32).reshape(-1).copy()
    if prepared.size == 0:
        return prepared

    np.nan_to_num(prepared, copy=False)
    prepared -= float(np.mean(prepared, dtype=np.float64))
    rms = float(np.sqrt(np.mean(np.square(prepared), dtype=np.float64)))
    if 0.001 <= rms < NORMALIZATION_TARGET_RMS:
        gain = min(NORMALIZATION_MAX_GAIN, NORMALIZATION_TARGET_RMS / rms)
        prepared *= gain
        np.clip(prepared, -0.98, 0.98, out=prepared)
    return prepared


def list_input_devices() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    default_input = sd.default.device[0]
    for index, device in enumerate(sd.query_devices()):
        if int(device["max_input_channels"]) <= 0:
            continue
        result.append(
            {
                "index": index,
                "name": str(device["name"]),
                "sample_rate": int(round(float(device["default_samplerate"]))),
                "is_default": index == default_input,
            }
        )
    return result


class AudioRecorder:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._chunks: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._sample_rate = TARGET_SAMPLE_RATE
        self._status_messages: list[str] = []
        self._current_level = 0.0

    @property
    def is_recording(self) -> bool:
        return self._stream is not None

    @property
    def current_level(self) -> float:
        with self._lock:
            return self._current_level

    def start(self, device_index: int | None = None) -> int:
        if self._stream is not None:
            raise RuntimeError("Запись уже идёт")

        selected_index = device_index
        if selected_index is None:
            selected_index = int(sd.default.device[0])

        device = sd.query_devices(selected_index, "input")
        sample_rate = int(round(float(device["default_samplerate"]))) or 48_000
        sd.check_input_settings(
            device=selected_index,
            channels=1,
            dtype="float32",
            samplerate=sample_rate,
        )

        with self._lock:
            self._chunks = []
            self._status_messages = []
            self._current_level = 0.0

        def callback(
            indata: np.ndarray,
            frames: int,
            time_info: Any,
            status: sd.CallbackFlags,
        ) -> None:
            del frames, time_info
            if status:
                with self._lock:
                    self._status_messages.append(str(status))
            channel = indata[:, 0]
            rms = float(np.sqrt(np.mean(np.square(channel), dtype=np.float64)))
            peak = float(np.max(np.abs(channel))) if channel.size else 0.0
            dbfs = 20.0 * math.log10(max(rms, 1e-7))
            rms_level = max(0.0, min(1.0, (dbfs + 58.0) / 38.0))
            peak_level = max(0.0, min(1.0, peak / 0.22))
            normalized_level = max(rms_level, peak_level * 0.72)
            with self._lock:
                self._chunks.append(channel.copy())
                if normalized_level >= self._current_level:
                    self._current_level = (
                        normalized_level * 0.78 + self._current_level * 0.22
                    )
                else:
                    self._current_level = max(
                        normalized_level,
                        self._current_level * 0.78,
                    )

        stream = sd.InputStream(
            device=selected_index,
            samplerate=sample_rate,
            channels=1,
            dtype="float32",
            callback=callback,
        )
        self._sample_rate = sample_rate
        stream.start()
        self._stream = stream
        return sample_rate

    def snapshot(self) -> AudioClip:
        with self._lock:
            chunks = [chunk.copy() for chunk in self._chunks]
            sample_rate = self._sample_rate

        if not chunks:
            return AudioClip(np.empty(0, dtype=np.float32), 0.0, 0.0)

        source = np.concatenate(chunks).astype(np.float32, copy=False)
        samples = resample_audio(source, sample_rate, TARGET_SAMPLE_RATE)
        duration = samples.size / TARGET_SAMPLE_RATE
        rms = (
            float(np.sqrt(np.mean(np.square(samples), dtype=np.float64)))
            if samples.size
            else 0.0
        )
        return AudioClip(samples=samples, duration_seconds=duration, rms=rms)

    def stop(self) -> AudioClip:
        stream = self._stream
        self._stream = None
        if stream is None:
            return AudioClip(np.empty(0, dtype=np.float32), 0.0, 0.0)

        try:
            stream.stop()
        finally:
            stream.close()

        with self._lock:
            chunks = self._chunks
            self._chunks = []
            self._current_level = 0.0

        if not chunks:
            return AudioClip(np.empty(0, dtype=np.float32), 0.0, 0.0)

        source = np.concatenate(chunks).astype(np.float32, copy=False)
        samples = resample_audio(source, self._sample_rate, TARGET_SAMPLE_RATE)
        duration = samples.size / TARGET_SAMPLE_RATE
        rms = float(np.sqrt(np.mean(np.square(samples), dtype=np.float64))) if samples.size else 0.0
        return AudioClip(samples=samples, duration_seconds=duration, rms=rms)

    def abort(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.abort()
            finally:
                stream.close()
        with self._lock:
            self._chunks = []
            self._current_level = 0.0
