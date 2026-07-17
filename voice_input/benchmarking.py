from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    case_id: str
    audio_path: Path
    reference: str
    language: str = "ru"
    custom_terms: str = ""
    tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TranscriptScore:
    word_edits: int
    reference_words: int
    hypothesis_words: int
    char_edits: int
    reference_chars: int
    wer: float
    cer: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def normalize_for_scoring(text: str) -> str:
    text = text.casefold().replace("ё", "е")
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def edit_distance(reference: list[str] | str, hypothesis: list[str] | str) -> int:
    previous = list(range(len(hypothesis) + 1))
    for reference_index, reference_item in enumerate(reference, start=1):
        current = [reference_index]
        for hypothesis_index, hypothesis_item in enumerate(hypothesis, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[hypothesis_index] + 1,
                    previous[hypothesis_index - 1]
                    + (reference_item != hypothesis_item),
                )
            )
        previous = current
    return previous[-1]


def score_transcript(reference: str, hypothesis: str) -> TranscriptScore:
    normalized_reference = normalize_for_scoring(reference)
    normalized_hypothesis = normalize_for_scoring(hypothesis)
    reference_words = normalized_reference.split()
    hypothesis_words = normalized_hypothesis.split()
    word_edits = edit_distance(reference_words, hypothesis_words)
    char_reference = normalized_reference.replace(" ", "")
    char_hypothesis = normalized_hypothesis.replace(" ", "")
    char_edits = edit_distance(char_reference, char_hypothesis)
    return TranscriptScore(
        word_edits=word_edits,
        reference_words=len(reference_words),
        hypothesis_words=len(hypothesis_words),
        char_edits=char_edits,
        reference_chars=len(char_reference),
        wer=word_edits / max(1, len(reference_words)),
        cer=char_edits / max(1, len(char_reference)),
    )


def aggregate_scores(scores: list[TranscriptScore]) -> dict[str, object]:
    word_edits = sum(score.word_edits for score in scores)
    reference_words = sum(score.reference_words for score in scores)
    char_edits = sum(score.char_edits for score in scores)
    reference_chars = sum(score.reference_chars for score in scores)
    silence_scores = [score for score in scores if score.reference_words == 0]
    return {
        "cases": len(scores),
        "word_edits": word_edits,
        "reference_words": reference_words,
        "wer": word_edits / max(1, reference_words),
        "char_edits": char_edits,
        "reference_chars": reference_chars,
        "cer": char_edits / max(1, reference_chars),
        "silence_cases": len(silence_scores),
        "hallucinated_words": sum(
            score.hypothesis_words for score in silence_scores
        ),
    }


def load_benchmark_manifest(path: Path) -> list[BenchmarkCase]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_cases = payload.get("cases") if isinstance(payload, dict) else None
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("В benchmark-манифесте нужен непустой список cases")

    cases: list[BenchmarkCase] = []
    seen_ids: set[str] = set()
    for index, raw_case in enumerate(raw_cases, start=1):
        if not isinstance(raw_case, dict):
            raise ValueError(f"Тест #{index} должен быть объектом")
        case_id = str(raw_case.get("id", "")).strip()
        audio = str(raw_case.get("audio", "")).strip()
        reference = raw_case.get("reference")
        if not case_id or case_id in seen_ids:
            raise ValueError(f"Некорректный или повторный id теста #{index}")
        if not audio or not isinstance(reference, str):
            raise ValueError(f"Для теста {case_id} нужны audio и reference")
        audio_path = (path.parent / audio).resolve()
        if not audio_path.is_file():
            raise FileNotFoundError(f"Аудиофайл теста {case_id} не найден: {audio_path}")
        tags = raw_case.get("tags", [])
        if not isinstance(tags, list):
            raise ValueError(f"tags теста {case_id} должен быть списком")
        cases.append(
            BenchmarkCase(
                case_id=case_id,
                audio_path=audio_path,
                reference=reference,
                language=str(raw_case.get("language", "ru")),
                custom_terms=str(raw_case.get("custom_terms", "")),
                tags=tuple(str(tag) for tag in tags),
            )
        )
        seen_ids.add(case_id)
    return cases
