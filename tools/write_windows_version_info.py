from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from voice_input import __version__


def main() -> int:
    parts = tuple(int(part) for part in __version__.split("."))
    if len(parts) != 3:
        raise ValueError(f"Ожидалась версия X.Y.Z, получено: {__version__}")
    numeric_version = (*parts, 0)
    target = ROOT / "build" / "Rechka.version.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={numeric_version!r},
    prodvers={numeric_version!r},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '041904B0',
        [
          StringStruct('CompanyName', 'EBSF'),
          StringStruct('FileDescription', 'Речка — локальный голосовой ввод'),
          StringStruct('FileVersion', '{__version__}'),
          StringStruct('InternalName', 'Rechka'),
          StringStruct('LegalCopyright', '© 2026 EBSF'),
          StringStruct('OriginalFilename', 'Rechka.exe'),
          StringStruct('ProductName', 'Речка'),
          StringStruct('ProductVersion', '{__version__}')
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1049, 1200])])
  ]
)
""",
        encoding="utf-8",
    )
    print(target.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
