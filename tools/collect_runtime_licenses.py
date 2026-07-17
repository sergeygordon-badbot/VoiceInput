from __future__ import annotations

import shutil
import os
import stat
import time
import urllib.request
from importlib import metadata
from pathlib import Path
from urllib.error import URLError
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "build" / "runtime-licenses"
RUNTIME_DISTRIBUTIONS = (
    "PySide6",
    "PySide6-Essentials",
    "PySide6-Addons",
    "shiboken6",
    "faster-whisper",
    "ctranslate2",
    "sounddevice",
    "httpx",
    "httpcore",
    "huggingface-hub",
    "onnxruntime",
    "av",
    "Pillow",
    "numpy",
    "tokenizers",
    "PyYAML",
    "tqdm",
    "click",
    "certifi",
    "anyio",
    "h11",
    "idna",
)
QT_LICENSES = {
    "LGPL-3.0.txt": "LICENSES/LGPL-3.0-only.txt",
    "GPL-3.0.txt": "LICENSES/GPL-3.0-only.txt",
}


def _is_license_file(path: Path) -> bool:
    lowered = [part.lower() for part in path.parts]
    if not any(part.endswith(".dist-info") for part in lowered):
        return False
    return any(
        part in {"license", "licenses"} or part.startswith(("license", "copying", "notice"))
        for part in lowered
    )


def _download_qt_license(path: str) -> str:
    url = (
        "https://api.github.com/repos/qt/qtbase/contents/"
        f"{quote(path, safe='/')}?ref=v6.11.1"
    )
    headers = {
        "Accept": "application/vnd.github.raw+json",
        "User-Agent": "Rechka-License-Collector/0.6.0",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        url,
        headers=headers,
    )
    last_error: Exception | None = None
    for attempt in range(1, 5):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                text = response.read().decode("utf-8")
            if "GENERAL PUBLIC LICENSE" not in text:
                raise RuntimeError(f"Неожиданный текст лицензии: {url}")
            return text
        except (OSError, UnicodeError, URLError) as exc:
            last_error = exc
            if attempt < 4:
                time.sleep(attempt * 2)
    raise RuntimeError(f"Не удалось получить лицензию Qt: {last_error}")


def main() -> int:
    if OUTPUT.exists():
        def remove_readonly(function, path, _error) -> None:
            os.chmod(path, stat.S_IWRITE)
            function(path)

        shutil.rmtree(OUTPUT, onexc=remove_readonly)
    OUTPUT.mkdir(parents=True)

    for distribution_name in RUNTIME_DISTRIBUTIONS:
        try:
            distribution = metadata.distribution(distribution_name)
        except metadata.PackageNotFoundError:
            continue

        destination = OUTPUT / f"{distribution.metadata['Name']}-{distribution.version}"
        copied = 0
        for relative in distribution.files or ():
            relative_path = Path(str(relative))
            if not _is_license_file(relative_path):
                continue
            source = Path(distribution.locate_file(relative))
            if not source.is_file():
                continue
            destination.mkdir(parents=True, exist_ok=True)
            safe_name = "__".join(relative_path.parts[-3:])
            shutil.copy2(source, destination / safe_name)
            copied += 1
        if copied == 0 and destination.exists():
            destination.rmdir()

    qt_directory = OUTPUT / "Qt-for-Python-6.11.1"
    qt_directory.mkdir(parents=True, exist_ok=True)
    for filename, path in QT_LICENSES.items():
        (qt_directory / filename).write_text(
            _download_qt_license(path),
            encoding="utf-8",
        )
    (qt_directory / "SOURCE-AND-NOTICE.txt").write_text(
        "VoiceInput uses the unmodified community distribution of Qt for Python "
        "(PySide6) 6.11.1 under LGPLv3/GPLv3.\n\n"
        "Official source code for this exact version:\n"
        "https://download.qt.io/official_releases/QtForPython/pyside6/"
        "PySide6-6.11.1-src/\n\n"
        "Qt for Python licensing documentation:\n"
        "https://doc.qt.io/qtforpython-6/index.html\n\n"
        "The Qt libraries are shipped as separate dynamically loaded DLL files "
        "inside the application directory. VoiceInput does not restrict lawful "
        "reverse engineering for debugging modifications to LGPL components.\n",
        encoding="utf-8",
    )

    print(OUTPUT.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
