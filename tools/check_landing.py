from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from voice_input import __version__  # noqa: E402


SITE_ROOT = PROJECT_ROOT / "site"
LANDING_ROOT = SITE_ROOT / "rechka"
LANDING_HTML = LANDING_ROOT / "index.html"
LANDING_SITEMAP = LANDING_ROOT / "sitemap.xml"
EXPECTED_CANONICAL = "https://ebsf.ru/rechka/"
EXPECTED_RELEASE_PAGE = (
    "https://github.com/sergeygordon-badbot/Rechka/releases/latest"
)
EXPECTED_RELEASE_API = (
    "https://api.github.com/repos/sergeygordon-badbot/Rechka/releases/latest"
)
RELEASE_SCRIPT = LANDING_ROOT / "release-v1.js"


class LandingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: list[str] = []
        self.fragment_links: list[str] = []
        self.local_resources: list[str] = []
        self.h1_texts: list[str] = []
        self.title_parts: list[str] = []
        self.description = ""
        self.canonicals: list[str] = []
        self.json_ld_blocks: list[str] = []
        self._active_h1: list[str] | None = None
        self._in_title = False
        self._json_ld_parts: list[str] | None = None

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = {key: value or "" for key, value in attrs}
        element_id = attributes.get("id")
        if element_id:
            self.ids.append(element_id)

        if tag == "h1":
            self._active_h1 = []
        elif tag == "title":
            self._in_title = True
        elif tag == "meta" and attributes.get("name") == "description":
            self.description = attributes.get("content", "").strip()
        elif tag == "link" and attributes.get("rel") == "canonical":
            self.canonicals.append(attributes.get("href", "").strip())
        elif (
            tag == "script"
            and attributes.get("type") == "application/ld+json"
        ):
            self._json_ld_parts = []

        for attribute_name in ("href", "src"):
            value = attributes.get(attribute_name, "").strip()
            if not value:
                continue
            if value.startswith("#"):
                self.fragment_links.append(value[1:])
                continue
            parsed = urlparse(value)
            if (
                not parsed.scheme
                and not value.startswith("/")
                and not value.startswith("mailto:")
            ):
                self.local_resources.append(parsed.path)

    def handle_endtag(self, tag: str) -> None:
        if tag == "h1" and self._active_h1 is not None:
            self.h1_texts.append(" ".join("".join(self._active_h1).split()))
            self._active_h1 = None
        elif tag == "title":
            self._in_title = False
        elif tag == "script" and self._json_ld_parts is not None:
            self.json_ld_blocks.append("".join(self._json_ld_parts))
            self._json_ld_parts = None

    def handle_data(self, data: str) -> None:
        if self._active_h1 is not None:
            self._active_h1.append(data)
        if self._in_title:
            self.title_parts.append(data)
        if self._json_ld_parts is not None:
            self._json_ld_parts.append(data)


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)


def main() -> int:
    source = LANDING_HTML.read_text(encoding="utf-8")
    parser = LandingParser()
    parser.feed(source)
    errors: list[str] = []

    title = " ".join("".join(parser.title_parts).split())
    if not title:
        errors.append("Отсутствует title")
    if not parser.description:
        errors.append("Отсутствует meta description")
    if len(parser.h1_texts) != 1:
        errors.append(f"Ожидался один H1, найдено: {len(parser.h1_texts)}")
    if parser.h1_texts != ["Не печатайте. Скажите."]:
        errors.append(f"Неожиданный H1: {parser.h1_texts!r}")

    duplicate_ids = sorted(
        element_id
        for element_id, count in Counter(parser.ids).items()
        if count > 1
    )
    if duplicate_ids:
        errors.append(f"Повторяющиеся id: {', '.join(duplicate_ids)}")

    missing_fragments = sorted(
        fragment
        for fragment in set(parser.fragment_links)
        if fragment and fragment not in set(parser.ids)
    )
    if missing_fragments:
        errors.append(f"Сломанные якоря: {', '.join(missing_fragments)}")

    missing_resources = sorted(
        {
            resource
            for resource in parser.local_resources
            if not (LANDING_ROOT / resource).is_file()
        }
    )
    if missing_resources:
        errors.append(
            f"Не найдены локальные ресурсы: {', '.join(missing_resources)}"
        )

    if parser.canonicals != [EXPECTED_CANONICAL]:
        errors.append(f"Некорректный canonical: {parser.canonicals!r}")

    structured_types: set[str] = set()
    software_payload: dict[str, object] | None = None
    for index, block in enumerate(parser.json_ld_blocks, start=1):
        try:
            payload = json.loads(block)
        except json.JSONDecodeError as exc:
            errors.append(f"JSON-LD блок {index}: {exc}")
            continue
        value = payload.get("@type")
        if isinstance(value, str):
            structured_types.add(value)
            if value == "SoftwareApplication":
                software_payload = payload
        elif isinstance(value, list):
            structured_types.update(item for item in value if isinstance(item, str))

    required_types = {"SoftwareApplication"}
    missing_types = sorted(required_types - structured_types)
    if missing_types:
        errors.append(
            f"Нет обязательных типов JSON-LD: {', '.join(missing_types)}"
        )
    if software_payload is not None:
        expected_values = {
            "downloadUrl": EXPECTED_RELEASE_PAGE,
        }
        for key, expected in expected_values.items():
            if software_payload.get(key) != expected:
                errors.append(
                    f"JSON-LD {key}: ожидалось {expected!r}, "
                    f"получено {software_payload.get(key)!r}"
                )
        for dynamic_key in ("softwareVersion", "fileSize"):
            if dynamic_key in software_payload:
                errors.append(
                    f"JSON-LD {dynamic_key} должен заполняться из GitHub Releases"
                )

    if source.count(EXPECTED_RELEASE_PAGE) != 4:
        errors.append(
            "Резервная ссылка последнего релиза должна встречаться четыре раза"
        )
    expected_markers = {
        "data-release-download": 3,
        "data-release-version": 2,
        "data-release-size": 2,
    }
    for marker, expected_count in expected_markers.items():
        actual_count = source.count(marker)
        if actual_count != expected_count:
            errors.append(
                f"Маркер {marker}: ожидалось {expected_count}, "
                f"получено {actual_count}"
            )
    hardcoded_versions = sorted(set(re.findall(r"\b\d+\.\d+\.\d+\b", source)))
    if hardcoded_versions:
        errors.append(
            "На лендинге нельзя фиксировать номер релиза вручную: "
            + ", ".join(hardcoded_versions)
        )
    if __version__ in source:
        errors.append("Текущая версия приложения продублирована в лендинге")

    try:
        release_source = RELEASE_SCRIPT.read_text(encoding="utf-8")
    except OSError as exc:
        errors.append(f"Не найден загрузчик данных релиза: {exc}")
    else:
        required_release_snippets = (
            EXPECTED_RELEASE_API,
            "[data-release-download]",
            "[data-release-version]",
            "[data-release-size]",
            "softwareVersion",
            "fileSize",
        )
        for snippet in required_release_snippets:
            if snippet not in release_source:
                errors.append(
                    f"Загрузчик релиза не обновляет обязательное поле: {snippet}"
                )

    required_release_texts = (
        "Ctrl + Пробел",
        "Whisper Base",
    )
    for text in required_release_texts:
        if text not in source:
            errors.append(f"На лендинге отсутствует актуальное значение: {text}")

    try:
        sitemap = ET.parse(LANDING_SITEMAP)
        namespace = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        sitemap_urls = {
            element.text
            for element in sitemap.findall(".//sm:loc", namespace)
            if element.text
        }
        if EXPECTED_CANONICAL not in sitemap_urls:
            errors.append("Лендинг отсутствует в rechka/sitemap.xml")
    except (ET.ParseError, OSError) as exc:
        errors.append(f"Некорректный rechka/sitemap.xml: {exc}")

    if errors:
        for error in errors:
            fail(error)
        return 1

    print(f"Landing SEO check passed: {EXPECTED_CANONICAL}")
    print(f"title ({len(title)}): {title}")
    print(f"description ({len(parser.description)}): {parser.description}")
    print(f"H1: {parser.h1_texts[0]}")
    print(f"JSON-LD: {', '.join(sorted(structured_types))}")
    print("Release metadata: GitHub Releases (automatic)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
