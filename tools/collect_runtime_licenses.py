from __future__ import annotations

import shutil
import urllib.request
from importlib import metadata
from pathlib import Path


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
GNU_LICENSES = {
    "LGPL-3.0.txt": "https://www.gnu.org/licenses/lgpl-3.0.txt",
    "GPL-3.0.txt": "https://www.gnu.org/licenses/gpl-3.0.txt",
}


def _is_license_file(path: Path) -> bool:
    lowered = [part.lower() for part in path.parts]
    if not any(part.endswith(".dist-info") for part in lowered):
        return False
    return any(
        part in {"license", "licenses"} or part.startswith(("license", "copying", "notice"))
        for part in lowered
    )


def _download_text(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "VoiceInput-License-Collector/0.3.1"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        text = response.read().decode("utf-8")
    if "GENERAL PUBLIC LICENSE" not in text:
        raise RuntimeError(f"Неожиданный текст лицензии: {url}")
    return text


def main() -> int:
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
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
    for filename, url in GNU_LICENSES.items():
        (qt_directory / filename).write_text(_download_text(url), encoding="utf-8")
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
