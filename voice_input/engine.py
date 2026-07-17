from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from collections.abc import Callable
from pathlib import Path

import numpy as np

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

from faster_whisper import WhisperModel
from faster_whisper.vad import VadOptions, get_speech_timestamps

from .config import (
    MODEL_DOWNLOAD_DESCRIPTIONS,
    MODEL_FILES,
    MODEL_REPOSITORIES,
    bundled_model_path,
    downloaded_model_path,
)
from .audio import prepare_audio_for_whisper
from .hardware import InferenceProfile, detect_inference_profile
from .model_download import download_model_files, model_is_complete


StatusCallback = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    start: float
    end: float
    text: str


@dataclass(frozen=True, slots=True)
class VadProfile:
    threshold: float
    min_speech_duration_ms: int
    min_silence_duration_ms: int
    speech_pad_ms: int

    def to_dict(self) -> dict[str, float | int]:
        return {
            "threshold": self.threshold,
            "min_speech_duration_ms": self.min_speech_duration_ms,
            "min_silence_duration_ms": self.min_silence_duration_ms,
            "speech_pad_ms": self.speech_pad_ms,
        }


DEFAULT_VAD_PROFILE = "balanced"
PREVIEW_VAD_PROFILE = "sensitive"
VAD_PROFILES: dict[str, VadProfile | None] = {
    # The balanced profile preserves the production thresholds that were used
    # before speech detection moved in front of Whisper.
    "balanced": VadProfile(
        threshold=0.35,
        min_speech_duration_ms=120,
        min_silence_duration_ms=450,
        speech_pad_ms=300,
    ),
    # A live preview may end in the middle of a word, so it needs shorter
    # minimum regions and slightly more sensitivity.
    "sensitive": VadProfile(
        threshold=0.30,
        min_speech_duration_ms=80,
        min_silence_duration_ms=250,
        speech_pad_ms=180,
    ),
    # This profile is intentionally conservative and exists for benchmark A/B
    # runs on noisy recordings; it is not exposed in the main application UI.
    "strict": VadProfile(
        threshold=0.50,
        min_speech_duration_ms=180,
        min_silence_duration_ms=500,
        speech_pad_ms=250,
    ),
    "off": None,
}
_VAD_LOCK = threading.Lock()


def detect_speech_regions(
    samples: np.ndarray,
    profile_name: str = DEFAULT_VAD_PROFILE,
) -> tuple[tuple[int, int], ...]:
    if profile_name not in VAD_PROFILES:
        available = ", ".join(VAD_PROFILES)
        raise ValueError(f"Неизвестный VAD-профиль: {profile_name}. Доступны: {available}")

    prepared = np.ascontiguousarray(samples, dtype=np.float32).reshape(-1)
    if prepared.size == 0:
        return ()
    profile = VAD_PROFILES[profile_name]
    if profile is None:
        return ((0, prepared.size),)

    options = VadOptions(**profile.to_dict())
    with _VAD_LOCK:
        chunks = get_speech_timestamps(
            prepared,
            options,
            sampling_rate=16_000,
        )
    return tuple(
        (max(0, int(chunk["start"])), min(prepared.size, int(chunk["end"])))
        for chunk in chunks
        if int(chunk["end"]) > int(chunk["start"])
    )


@dataclass(frozen=True, slots=True)
class GlossaryEntry:
    canonical: str
    aliases: tuple[str, ...] = ()


def parse_custom_terms(value: str) -> tuple[GlossaryEntry, ...]:
    entries: list[GlossaryEntry] = []
    seen: set[str] = set()
    for group in re.split(r"[;\n]+", value):
        group = group.strip()
        if not group:
            continue
        if "=" in group:
            canonical, raw_aliases = group.split("=", 1)
            candidates = [(canonical.strip(), raw_aliases)]
        else:
            candidates = [(item.strip(), "") for item in group.split(",")]
        for canonical, raw_aliases in candidates:
            key = canonical.casefold()
            if not canonical or key in seen:
                continue
            aliases = tuple(
                alias.strip()
                for alias in re.split(r"[|,]", raw_aliases)
                if alias.strip() and alias.strip().casefold() != key
            )
            entries.append(GlossaryEntry(canonical=canonical, aliases=aliases))
            seen.add(key)
    return tuple(entries)


def apply_custom_terms(text: str, custom_terms: str) -> str:
    replacements: list[tuple[str, str]] = []
    for entry in parse_custom_terms(custom_terms):
        replacements.extend((alias, entry.canonical) for alias in entry.aliases)
        replacements.append((entry.canonical, entry.canonical))
    replacements.sort(key=lambda item: len(item[0]), reverse=True)
    for source, canonical in replacements:
        text = re.sub(
            rf"(?<!\w){re.escape(source)}(?!\w)",
            lambda _match, value=canonical: value,
            text,
            flags=re.IGNORECASE,
        )
    return text


def custom_terms_hotwords(custom_terms: str) -> str | None:
    terms = [entry.canonical for entry in parse_custom_terms(custom_terms)]
    return ", ".join(terms) or None


def apply_voice_commands(text: str) -> str:
    replacements = (
        (r"\bновый абзац\b", "\n\n"),
        (r"\bновая строка\b", "\n"),
        (
            r"\b(?:и\s+)?(?:поставь|поставить|ставь)\s+точку\s+с\s+запятой\b[.]?",
            ";",
        ),
        (r"\b(?:и\s+)?поставь точку\b[.]?", "."),
        (r"\b(?:и\s+)?поставь запятую\b[.]?", ","),
        (
            r"\b(?:и\s+)?(?:поставь\s+(?:в\s+)?)?"
            r"вопросительн(?:ый|ого|ых?)\s+знак\b[.]?",
            "?",
        ),
        (
            r"\b(?:и\s+)?(?:поставь\s+(?:в\s+)?)?"
            r"восклицательн(?:ый|ого|ых?)\s+знак\b[.]?",
            "!",
        ),
        (r"\b(?:и\s+)?поставь двоеточие\b[.]?", ":"),
        (
            r"\b(?:и\s+)?(?:поставь|поставить|ставь)\s+тире\b[.]?",
            " ⟦DASH⟧ ",
        ),
        (
            r"\b(?:и\s+)?(?:поставь|поставить|ставь)\s+дефис\b[.]?",
            "⟦HYPHEN⟧",
        ),
    )
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    text = re.sub(r"\s*⟦DASH⟧\s*", " — ", text)
    text = re.sub(r"\s*⟦HYPHEN⟧\s*", "-", text)
    text = re.sub(r"([!?])\s*[.]", r"\1", text)
    return text


def normalize_transcript(text: str, punctuation_commands: bool = True) -> str:
    text = text.strip()
    if punctuation_commands:
        text = apply_voice_commands(text)

    lines = []
    for line in text.splitlines():
        clean = re.sub(r"[ \t]+", " ", line).strip()
        clean = re.sub(r"\s+([,.;:!?])", r"\1", clean)
        clean = re.sub(r"([(\[«])\s+", r"\1", clean)
        clean = re.sub(r"(?<=\S)[ \t]*—[ \t]*(?=\S)", " — ", clean)
        clean = re.sub(r"^—[ \t]*", "— ", clean)
        for index, character in enumerate(clean):
            if character.isalpha():
                clean = clean[:index] + character.upper() + clean[index + 1 :]
                break
        lines.append(clean)
    text = "\n".join(lines).strip()
    return text


def merge_incremental_transcript(previous: str, current: str) -> str:
    previous_words = previous.strip().split()
    current_words = current.strip().split()
    if not previous_words:
        return current.strip()
    if not current_words:
        return previous.strip()

    maximum_overlap = min(12, len(previous_words), len(current_words))
    overlap = 0
    for size in range(maximum_overlap, 0, -1):
        left = [word.casefold().strip(".,!?;:«»\"'") for word in previous_words[-size:]]
        right = [word.casefold().strip(".,!?;:«»\"'") for word in current_words[:size]]
        if left == right:
            overlap = size
            break

    merged = [*previous_words, *current_words[overlap:]]
    return normalize_transcript(" ".join(merged), punctuation_commands=False)


def is_reliable_preview_text(text: str) -> bool:
    """Hide obvious live-preview hallucinations without affecting final text."""
    words = re.findall(
        r"[0-9a-zа-яё]+(?:-[0-9a-zа-яё]+)?",
        text.casefold(),
        flags=re.IGNORECASE,
    )
    if not words or sum(character.isalpha() for character in text) < 3:
        return False
    if len(words) < 3:
        return len("".join(words)) >= 4

    if len(words) >= 8 and len(set(words)) / len(words) < 0.34:
        return False

    if len(words) >= 12:
        bigrams = list(zip(words, words[1:]))
        most_common_bigram = max(
            (bigrams.count(bigram) for bigram in set(bigrams)),
            default=0,
        )
        if most_common_bigram >= 4:
            return False
    return True


def choose_chunk_length(sample_count: int, sample_rate: int = 16_000) -> int:
    del sample_count, sample_rate
    return 30


class WhisperEngine:
    def __init__(
        self,
        cpu_threads: int | None = None,
        device_preference: str = "auto",
    ) -> None:
        self._model: WhisperModel | None = None
        self._model_name: str | None = None
        self._profile = detect_inference_profile(device_preference)
        detected_threads = self._profile.physical_cores
        self._detected_threads = detected_threads
        self._thread_limit = cpu_threads
        self._cpu_threads = max(1, min(detected_threads, cpu_threads or 4))
        self._lock = threading.Lock()
        self._transcribe_lock = threading.Lock()

    @property
    def model_name(self) -> str | None:
        return self._model_name

    @property
    def cpu_threads(self) -> int:
        return self._cpu_threads

    @property
    def inference_profile(self) -> InferenceProfile:
        return self._profile

    def _resolve_model(self, model_name: str, status: StatusCallback) -> Path:
        required_files = MODEL_FILES[model_name]
        bundled = bundled_model_path(model_name)
        if model_is_complete(bundled, required_files):
            status("Найдена локальная модель")
            return bundled

        destination = downloaded_model_path(model_name)
        if model_is_complete(destination, required_files):
            status("Найдена загруженная модель")
            return destination

        repository = MODEL_REPOSITORIES[model_name]
        destination.parent.mkdir(parents=True, exist_ok=True)
        size = MODEL_DOWNLOAD_DESCRIPTIONS[model_name]
        status(f"Скачивание модели ({size}) — не закрывайте программу…")
        return download_model_files(
            repo_id=repository,
            destination=destination,
            files=required_files,
            model_label=model_name.capitalize(),
            status=status,
        )

    def load(self, model_name: str, status: StatusCallback | None = None) -> None:
        callback = status or (lambda _message: None)
        with self._lock:
            if self._model is not None and self._model_name == model_name:
                return

            model_path = self._resolve_model(model_name, callback)
            if self._thread_limit is None:
                recommended_threads = 6 if model_name == "small" else 4
                self._cpu_threads = max(
                    1,
                    min(self._detected_threads, recommended_threads),
                )
            profile = self._profile
            accelerator = (
                f"CUDA {profile.compute_type}"
                if profile.device == "cuda"
                else f"CPU {profile.compute_type.upper()}, {self._cpu_threads} потоков"
            )
            callback(f"Загрузка модели в память: {accelerator}…")
            try:
                self._model = WhisperModel(
                    str(model_path),
                    device=profile.device,
                    device_index=profile.device_index,
                    compute_type=profile.compute_type,
                    cpu_threads=self._cpu_threads,
                    num_workers=1,
                )
            except Exception as exc:
                if profile.device != "cuda":
                    raise
                callback(f"CUDA недоступна ({exc}). Переключаюсь на CPU…")
                self._profile = detect_inference_profile("cpu")
                self._model = WhisperModel(
                    str(model_path),
                    device="cpu",
                    compute_type=self._profile.compute_type,
                    cpu_threads=self._cpu_threads,
                    num_workers=1,
                )
            self._model_name = model_name

    def transcribe(
        self,
        samples: np.ndarray,
        language: str = "ru",
        beam_size: int = 1,
        custom_terms: str = "",
        punctuation_commands: bool = True,
        preview: bool = False,
        vad_profile: str = DEFAULT_VAD_PROFILE,
    ) -> str:
        segments = self.transcribe_segments(
            samples,
            language=language,
            beam_size=beam_size,
            custom_terms=custom_terms,
            preview=preview,
            vad_profile=vad_profile,
        )
        text = " ".join(segment.text for segment in segments)
        text = apply_custom_terms(text, custom_terms)
        return normalize_transcript(text, punctuation_commands=punctuation_commands)

    def transcribe_segments(
        self,
        samples: np.ndarray,
        language: str = "ru",
        beam_size: int = 1,
        custom_terms: str = "",
        preview: bool = False,
        vad_profile: str = DEFAULT_VAD_PROFILE,
    ) -> list[TranscriptSegment]:
        model = self._model
        if model is None:
            raise RuntimeError("Модель ещё не загружена")

        selected_language = None if language == "auto" else language
        hotwords = custom_terms_hotwords(custom_terms)
        prepared = prepare_audio_for_whisper(samples)
        chunk_length = 5 if preview else choose_chunk_length(prepared.size)
        selected_vad_profile = (
            PREVIEW_VAD_PROFILE
            if preview and vad_profile == DEFAULT_VAD_PROFILE
            else vad_profile
        )
        speech_regions = detect_speech_regions(prepared, selected_vad_profile)
        if not speech_regions:
            return []
        profile = VAD_PROFILES[selected_vad_profile]
        transcription_audio = (
            np.concatenate(
                [prepared[start:end] for start, end in speech_regions],
                dtype=np.float32,
            )
            if profile is not None and not preview
            else prepared
        )
        with self._transcribe_lock:
            segments, _info = model.transcribe(
                transcription_audio,
                language=selected_language,
                task="transcribe",
                beam_size=max(1, min(5, beam_size)),
                best_of=1,
                temperature=0.0,
                # VAD regions are concatenated, matching faster-whisper's own
                # filtering strategy. Passing them as separate clip timestamps
                # measurably harms recognition context at region boundaries.
                vad_filter=False,
                condition_on_previous_text=(
                    False if preview else prepared.size > 28 * 16_000
                ),
                chunk_length=chunk_length,
                hotwords=hotwords,
                no_speech_threshold=0.55 if preview else 0.75,
                log_prob_threshold=-1.0,
                compression_ratio_threshold=2.4,
                # With timestamps disabled, faster-whisper may return only the
                # first configured chunk for recordings longer than 30 seconds.
                without_timestamps=False,
            )
            result = [
                TranscriptSegment(
                    start=max(0.0, float(segment.start)),
                    end=max(0.0, float(segment.end)),
                    text=apply_custom_terms(segment.text.strip(), custom_terms),
                )
                for segment in segments
                if segment.text.strip()
            ]
        return result
