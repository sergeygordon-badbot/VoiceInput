from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass

from .config import OUTPUT_MODE_OPTIONS


@dataclass(frozen=True, slots=True)
class Snippet:
    trigger: str
    expansion: str


@dataclass(frozen=True, slots=True)
class AppProfile:
    process_pattern: str
    output_mode: str
    custom_terms: str = ""

    @property
    def name(self) -> str:
        return self.process_pattern


def normalize_spoken_key(text: str) -> str:
    normalized = text.casefold().replace("ё", "е")
    normalized = re.sub(r"[^\w\s-]", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def parse_snippets(value: str) -> tuple[Snippet, ...]:
    snippets: list[Snippet] = []
    seen: set[str] = set()
    for line_number, raw_line in enumerate(value.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=>" not in line:
            raise ValueError(
                f"Сниппеты, строка {line_number}: используйте «фраза => текст»"
            )
        trigger, expansion = (part.strip() for part in line.split("=>", 1))
        key = normalize_spoken_key(trigger)
        if not key or not expansion:
            raise ValueError(
                f"Сниппеты, строка {line_number}: фраза и текст не должны быть пустыми"
            )
        if key in seen:
            raise ValueError(f"Сниппеты: фраза {trigger!r} указана дважды")
        snippets.append(
            Snippet(
                trigger=trigger,
                expansion=expansion.replace("\\n", "\n"),
            )
        )
        seen.add(key)
    return tuple(snippets)


def expand_snippet(text: str, value: str) -> str | None:
    key = normalize_spoken_key(text)
    for snippet in parse_snippets(value):
        if normalize_spoken_key(snippet.trigger) == key:
            return snippet.expansion
    return None


def parse_app_profiles(value: str) -> tuple[AppProfile, ...]:
    profiles: list[AppProfile] = []
    seen: set[str] = set()
    for line_number, raw_line in enumerate(value.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|", 2)]
        if len(parts) < 2:
            raise ValueError(
                f"Профили, строка {line_number}: используйте «app.exe | режим | слова»"
            )
        pattern, mode = parts[:2]
        custom_terms = parts[2] if len(parts) == 3 else ""
        if not pattern or mode not in OUTPUT_MODE_OPTIONS:
            modes = ", ".join(OUTPUT_MODE_OPTIONS)
            raise ValueError(
                f"Профили, строка {line_number}: режим должен быть одним из {modes}"
            )
        key = pattern.casefold()
        if key in seen:
            raise ValueError(f"Профили: шаблон {pattern!r} указан дважды")
        profiles.append(
            AppProfile(
                process_pattern=pattern,
                output_mode=mode,
                custom_terms=custom_terms,
            )
        )
        seen.add(key)
    return tuple(profiles)


def match_app_profile(process_name: str, value: str) -> AppProfile | None:
    candidate = process_name.casefold()
    if not candidate:
        return None
    for profile in parse_app_profiles(value):
        if fnmatch.fnmatchcase(candidate, profile.process_pattern.casefold()):
            return profile
    return None


def combine_custom_terms(base: str, extra: str) -> str:
    values = [item.strip() for item in (base, extra) if item.strip()]
    return "; ".join(values)
