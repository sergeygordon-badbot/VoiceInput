from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from voice_input import __version__  # noqa: E402


CHECKS = {
    "site/rechka/index.html": (
        re.compile(r"\b\d+\.\d+\.\d+\b"),
        "лендинг должен получать версию из GitHub Releases",
    ),
    "installer/Rechka.iss": (
        re.compile(
            r'#define\s+MyAppVersion\s+"\d+\.\d+\.\d+"',
            re.IGNORECASE,
        ),
        "версию установщику должен передавать build-installer.ps1",
    ),
    "README.md": (
        re.compile(
            r"(?:beta\s+|Rechka-Setup-)\d+\.\d+\.\d+",
            re.IGNORECASE,
        ),
        "README должен использовать X.Y.Z вместо текущей версии",
    ),
    "PRIVACY.md": (
        re.compile(r"редакция\s+\d+\.\d+\.\d+", re.IGNORECASE),
        "редакция политики не должна совпадать с версией приложения",
    ),
}


def main() -> int:
    errors: list[str] = []
    for relative_path, (pattern, explanation) in CHECKS.items():
        path = PROJECT_ROOT / relative_path
        source = path.read_text(encoding="utf-8")
        match = pattern.search(source)
        if match:
            errors.append(
                f"{relative_path}: найден ручной номер {match.group(0)!r}; "
                f"{explanation}"
            )

    init_source = (PROJECT_ROOT / "voice_input" / "__init__.py").read_text(
        encoding="utf-8"
    )
    expected_assignment = f'__version__ = "{__version__}"'
    if init_source.count(expected_assignment) != 1:
        errors.append(
            "voice_input/__init__.py должен содержать единственное присваивание "
            f"{expected_assignment!r}"
        )

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(
        "Single version source check passed: "
        f"voice_input/__init__.py ({__version__})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
