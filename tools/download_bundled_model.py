from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from voice_input.config import MODEL_FILES, MODEL_REPOSITORIES
from voice_input.model_download import download_model_files


def main() -> int:
    for model_name in ("tiny", "base"):
        destination = ROOT / "models" / f"faster-whisper-{model_name}"
        download_model_files(
            repo_id=MODEL_REPOSITORIES[model_name],
            destination=destination,
            files=MODEL_FILES[model_name],
            model_label=f"Whisper {model_name.capitalize()}",
            status=print,
        )
        print(destination.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
