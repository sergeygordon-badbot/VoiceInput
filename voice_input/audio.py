from __future__ import annotations

import math
import queue
import tempfile
import threading
import time
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import av
import numpy as np
import sounddevice as sd
from av.audio.resampler import AudioResampler


TARGET_SAMPLE_RATE = 16_000
NORMALIZATION_TARGET_RMS = 0.055
NORMALIZATION_MAX_GAIN = 8.0
NORMALIZATION_MIN_RMS = 1e-5
PREVIEW_BUFFER_SECONDS = 20
SPOOL_QUEUE_CHUNKS = 1_024
RESAMPLE_BLOCK_FRAMES = 65_536


@dataclass(slots=True)
class AudioClip:
    samples: np.ndarray
    duration_seconds: float
    rms: float
    start_sample: int = 0
    total_samples: int = 0
    status_messages: tuple[str, ...] = ()
    peak: float = 0.0
    clipped_fraction: float = 0.0


@dataclass(frozen=True, slots=True)
class AudioQuality:
    rms: float
    peak: float
    dbfs: float
    clipped_fraction: float


def analyze_audio(samples: np.ndarray) -> AudioQuality:
    prepared = np.asarray(samples, dtype=np.float32).reshape(-1)
    if prepared.size == 0:
        return AudioQuality(
            rms=0.0,
            peak=0.0,
            dbfs=-140.0,
            clipped_fraction=0.0,
        )

    finite = np.nan_to_num(prepared, copy=True)
    peak = float(np.max(np.abs(finite)))
    clipped_fraction = float(np.mean(np.abs(finite) >= 0.98))
    finite -= float(np.mean(finite, dtype=np.float64))
    rms = float(np.sqrt(np.mean(np.square(finite), dtype=np.float64)))
    dbfs = 20.0 * math.log10(max(rms, 1e-7))
    return AudioQuality(
        rms=rms,
        peak=peak,
        dbfs=max(-140.0, dbfs),
        clipped_fraction=clipped_fraction,
    )


def has_recordable_signal(samples: np.ndarray) -> bool:
    """Reject only digital silence; Whisper VAD decides whether speech exists."""
    quality = analyze_audio(samples)
    return quality.rms >= 1e-5 or quality.peak >= 3e-5


def resample_audio(
    samples: np.ndarray,
    source_rate: int,
    target_rate: int = TARGET_SAMPLE_RATE,
) -> np.ndarray:
    samples = np.asarray(samples, dtype=np.float32).reshape(-1)
    if source_rate <= 0 or target_rate <= 0:
        raise ValueError("Частота дискретизации должна быть положительной")
    if samples.size == 0 or source_rate == target_rate:
        return samples.astype(np.float32, copy=False)

    return _resample_audio_chunks(
        (samples,),
        source_rate=source_rate,
        source_frame_count=samples.size,
        target_rate=target_rate,
    )


def _resample_audio_chunks(
    chunks: Iterable[np.ndarray],
    *,
    source_rate: int,
    source_frame_count: int,
    target_rate: int,
) -> np.ndarray:
    target_length = max(
        1,
        round(source_frame_count * target_rate / source_rate),
    )
    resampler = AudioResampler(
        format="flt",
        layout="mono",
        rate=target_rate,
    )
    output_frames: list[np.ndarray] = []
    for chunk in chunks:
        prepared = np.asarray(chunk, dtype=np.float32).reshape(-1)
        if prepared.size == 0:
            continue
        frame = av.AudioFrame.from_ndarray(
            np.ascontiguousarray(prepared.reshape(1, -1)),
            format="flt",
            layout="mono",
        )
        frame.sample_rate = source_rate
        output_frames.extend(
            item.to_ndarray().reshape(-1)
            for item in resampler.resample(frame)
        )
    output_frames.extend(
        item.to_ndarray().reshape(-1)
        for item in resampler.resample(None)
    )
    if not output_frames:
        return np.zeros(target_length, dtype=np.float32)

    result = np.concatenate(output_frames).astype(np.float32, copy=False)
    if result.size > target_length:
        result = result[:target_length]
    elif result.size < target_length:
        result = np.pad(
            result,
            (0, target_length - result.size),
            mode="edge" if result.size else "constant",
        )
    return np.ascontiguousarray(result, dtype=np.float32)


def resample_audio_file(
    path: Path,
    source_rate: int,
    source_frame_count: int,
    target_rate: int = TARGET_SAMPLE_RATE,
) -> np.ndarray:
    if source_rate <= 0 or target_rate <= 0:
        raise ValueError("Частота дискретизации должна быть положительной")
    expected_bytes = source_frame_count * np.dtype(np.float32).itemsize
    actual_bytes = path.stat().st_size
    if actual_bytes != expected_bytes:
        raise RuntimeError(
            "Временная запись повреждена: "
            f"ожидалось {expected_bytes} байт, получено {actual_bytes}"
        )
    if source_frame_count == 0:
        return np.empty(0, dtype=np.float32)

    mapped = np.memmap(
        path,
        dtype=np.float32,
        mode="r",
        shape=(source_frame_count,),
    )
    try:
        if source_rate == target_rate:
            return np.asarray(mapped, dtype=np.float32).copy()

        def chunks() -> Iterable[np.ndarray]:
            for start in range(0, source_frame_count, RESAMPLE_BLOCK_FRAMES):
                yield mapped[start : start + RESAMPLE_BLOCK_FRAMES]

        return _resample_audio_chunks(
            chunks(),
            source_rate=source_rate,
            source_frame_count=source_frame_count,
            target_rate=target_rate,
        )
    finally:
        del mapped


def prepare_audio_for_whisper(samples: np.ndarray) -> np.ndarray:
    prepared = np.asarray(samples, dtype=np.float32).reshape(-1).copy()
    if prepared.size == 0:
        return prepared

    np.nan_to_num(prepared, copy=False)
    prepared -= float(np.mean(prepared, dtype=np.float64))
    rms = float(np.sqrt(np.mean(np.square(prepared), dtype=np.float64)))
    if NORMALIZATION_MIN_RMS <= rms < NORMALIZATION_TARGET_RMS:
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
        self._chunks: deque[np.ndarray] = deque()
        self._stream: sd.InputStream | None = None
        self._sample_rate = TARGET_SAMPLE_RATE
        self._total_frames = 0
        self._buffer_start_frame = 0
        self._buffered_frames = 0
        self._status_messages: list[str] = []
        self._current_level = 0.0
        self._started_at = 0.0
        self._last_callback_at = 0.0
        self._fatal_error = ""
        self._spool_path: Path | None = None
        self._spool_queue: queue.Queue[np.ndarray | None] | None = None
        self._spool_thread: threading.Thread | None = None
        self._spool_written_frames = 0

    @property
    def is_recording(self) -> bool:
        return self._stream is not None

    @property
    def current_level(self) -> float:
        with self._lock:
            return self._current_level

    @property
    def sample_count(self) -> int:
        with self._lock:
            return round(
                self._total_frames * TARGET_SAMPLE_RATE / self._sample_rate
            )

    @property
    def health_error(self) -> str | None:
        stream = self._stream
        if stream is None:
            return None
        with self._lock:
            fatal_error = self._fatal_error
            started_at = self._started_at
            last_callback_at = self._last_callback_at
        if fatal_error:
            return fatal_error
        try:
            active = bool(stream.active)
        except Exception as exc:
            return f"Не удалось проверить микрофон: {exc}"
        elapsed = time.monotonic() - started_at
        if not active and elapsed >= 0.5:
            return "Микрофон отключён или поток записи остановлен"
        if elapsed >= 2.0 and (
            not last_callback_at or time.monotonic() - last_callback_at >= 2.0
        ):
            return "Микрофон перестал передавать звук"
        return None

    def _append_preview_chunk_locked(self, chunk: np.ndarray) -> None:
        self._chunks.append(chunk)
        self._buffered_frames += chunk.size
        maximum_frames = max(
            1,
            round(self._sample_rate * PREVIEW_BUFFER_SECONDS),
        )
        while self._buffered_frames > maximum_frames and self._chunks:
            excess = self._buffered_frames - maximum_frames
            first = self._chunks[0]
            if first.size <= excess:
                removed = self._chunks.popleft()
                self._buffer_start_frame += removed.size
                self._buffered_frames -= removed.size
            else:
                self._chunks[0] = first[excess:].copy()
                self._buffer_start_frame += excess
                self._buffered_frames -= excess

    def _start_spool(self) -> None:
        temporary = tempfile.NamedTemporaryFile(
            prefix="rechka-recording-",
            suffix=".f32",
            delete=False,
        )
        path = Path(temporary.name)
        temporary.close()
        spool_queue: queue.Queue[np.ndarray | None] = queue.Queue(
            maxsize=SPOOL_QUEUE_CHUNKS,
        )
        self._spool_path = path
        self._spool_queue = spool_queue
        self._spool_written_frames = 0

        def writer() -> None:
            try:
                with path.open("wb", buffering=1024 * 1024) as output:
                    while True:
                        chunk = spool_queue.get()
                        try:
                            if chunk is None:
                                return
                            output.write(chunk.tobytes(order="C"))
                            with self._lock:
                                self._spool_written_frames += chunk.size
                        finally:
                            spool_queue.task_done()
            except Exception as exc:
                with self._lock:
                    self._fatal_error = f"Не удалось сохранить запись: {exc}"

        thread = threading.Thread(
            target=writer,
            name="audio-spool-writer",
            daemon=True,
        )
        self._spool_thread = thread
        thread.start()

    def _finish_spool(self) -> tuple[Path | None, int, str]:
        spool_queue = self._spool_queue
        thread = self._spool_thread
        path = self._spool_path
        if spool_queue is not None and thread is not None and thread.is_alive():
            try:
                spool_queue.put(None, timeout=5)
            except queue.Full:
                with self._lock:
                    self._fatal_error = "Очередь записи не успела сохраниться"
            thread.join(timeout=15)
            if thread.is_alive():
                with self._lock:
                    self._fatal_error = "Не удалось завершить сохранение записи"
        with self._lock:
            written_frames = self._spool_written_frames
            fatal_error = self._fatal_error
        self._spool_queue = None
        self._spool_thread = None
        self._spool_path = None
        return path, written_frames, fatal_error

    @staticmethod
    def _delete_spool(path: Path | None) -> None:
        if path is None:
            return
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

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
            self._chunks = deque()
            self._total_frames = 0
            self._buffer_start_frame = 0
            self._buffered_frames = 0
            self._status_messages = []
            self._current_level = 0.0
            self._started_at = time.monotonic()
            self._last_callback_at = 0.0
            self._fatal_error = ""
        self._sample_rate = sample_rate
        self._start_spool()

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
            captured = channel.copy()
            with self._lock:
                self._append_preview_chunk_locked(captured)
                self._total_frames += captured.size
                self._last_callback_at = time.monotonic()
                if normalized_level >= self._current_level:
                    self._current_level = (
                        normalized_level * 0.78 + self._current_level * 0.22
                    )
                else:
                    self._current_level = max(
                        normalized_level,
                        self._current_level * 0.78,
                    )

            spool_queue = self._spool_queue
            if spool_queue is not None:
                try:
                    spool_queue.put_nowait(captured)
                except queue.Full:
                    with self._lock:
                        self._fatal_error = (
                            "Не удалось сохранить аудио без пропусков: "
                            "диск не успевает за записью"
                        )

        stream: sd.InputStream | None = None
        try:
            stream = sd.InputStream(
                device=selected_index,
                samplerate=sample_rate,
                channels=1,
                dtype="float32",
                callback=callback,
            )
            stream.start()
        except Exception:
            if stream is not None:
                stream.close()
            path, _written, _error = self._finish_spool()
            self._delete_spool(path)
            raise
        self._stream = stream
        return sample_rate

    def snapshot(self, start_sample: int = 0) -> AudioClip:
        with self._lock:
            sample_rate = self._sample_rate
            total_frames = self._total_frames
            requested_start = min(
                total_frames,
                max(
                    0,
                    int(start_sample * sample_rate / TARGET_SAMPLE_RATE),
                ),
            )
            source_start = max(self._buffer_start_frame, requested_start)
            chunks: list[np.ndarray] = []
            cursor = self._buffer_start_frame
            for chunk in self._chunks:
                chunk_end = cursor + chunk.size
                if chunk_end > source_start:
                    offset = max(0, source_start - cursor)
                    chunks.append(chunk[offset:].copy())
                cursor = chunk_end
            status_messages = tuple(self._status_messages)

        actual_start = round(
            source_start * TARGET_SAMPLE_RATE / sample_rate
        )
        total_samples = round(
            total_frames * TARGET_SAMPLE_RATE / sample_rate
        )

        if not chunks:
            return AudioClip(
                np.empty(0, dtype=np.float32),
                0.0,
                0.0,
                start_sample=actual_start,
                total_samples=total_samples,
                status_messages=status_messages,
            )

        source = np.concatenate(chunks).astype(np.float32, copy=False)
        samples = resample_audio(source, sample_rate, TARGET_SAMPLE_RATE)
        duration = samples.size / TARGET_SAMPLE_RATE
        quality = analyze_audio(samples)
        return AudioClip(
            samples=samples,
            duration_seconds=duration,
            rms=quality.rms,
            start_sample=actual_start,
            total_samples=total_samples,
            status_messages=status_messages,
            peak=quality.peak,
            clipped_fraction=quality.clipped_fraction,
        )

    def stop(self) -> AudioClip:
        stream = self._stream
        self._stream = None
        if stream is None:
            return AudioClip(np.empty(0, dtype=np.float32), 0.0, 0.0)

        stream_error = ""
        try:
            stream.stop()
        except Exception as exc:
            stream_error = f"Микрофон завершил запись с ошибкой: {exc}"
        try:
            stream.close()
        except Exception as exc:
            if not stream_error:
                stream_error = f"Не удалось закрыть микрофон: {exc}"

        spool_path, written_frames, spool_error = self._finish_spool()

        with self._lock:
            self._chunks = deque()
            total_frames = self._total_frames
            self._total_frames = 0
            self._buffer_start_frame = 0
            self._buffered_frames = 0
            status_messages = tuple(self._status_messages)
            if stream_error:
                status_messages = (*status_messages, stream_error)
            self._status_messages = []
            self._current_level = 0.0
            self._started_at = 0.0
            self._last_callback_at = 0.0
            self._fatal_error = ""

        if spool_error:
            self._delete_spool(spool_path)
            raise RuntimeError(spool_error)
        if written_frames != total_frames:
            self._delete_spool(spool_path)
            raise RuntimeError(
                "Запись сохранена не полностью: "
                f"{written_frames} из {total_frames} аудиофреймов"
            )
        if total_frames == 0 or spool_path is None:
            self._delete_spool(spool_path)
            return AudioClip(
                np.empty(0, dtype=np.float32),
                0.0,
                0.0,
                total_samples=round(
                    total_frames * TARGET_SAMPLE_RATE / self._sample_rate
                ),
                status_messages=status_messages,
            )

        try:
            samples = resample_audio_file(
                spool_path,
                self._sample_rate,
                total_frames,
                TARGET_SAMPLE_RATE,
            )
        finally:
            self._delete_spool(spool_path)
        duration = samples.size / TARGET_SAMPLE_RATE
        quality = analyze_audio(samples)
        return AudioClip(
            samples=samples,
            duration_seconds=duration,
            rms=quality.rms,
            total_samples=samples.size,
            status_messages=status_messages,
            peak=quality.peak,
            clipped_fraction=quality.clipped_fraction,
        )

    def abort(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.abort()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
        spool_path, _written_frames, _spool_error = self._finish_spool()
        self._delete_spool(spool_path)
        with self._lock:
            self._chunks = deque()
            self._total_frames = 0
            self._buffer_start_frame = 0
            self._buffered_frames = 0
            self._status_messages = []
            self._current_level = 0.0
            self._started_at = 0.0
            self._last_callback_at = 0.0
            self._fatal_error = ""
