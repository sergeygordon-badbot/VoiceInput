from __future__ import annotations

import os
import time
from collections.abc import Callable, Sequence
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

import httpx
from huggingface_hub import get_hf_file_metadata, hf_hub_url


StatusCallback = Callable[[str], None]
CHUNK_SIZE = 4 * 1024 * 1024
MAX_ATTEMPTS = 6


def _format_size(size: int) -> str:
    if size >= 1024**3:
        return f"{size / 1024**3:.2f} ГБ"
    return f"{size / 1024**2:.0f} МБ"


def model_is_complete(directory: Path, files: Sequence[str]) -> bool:
    return all((directory / filename).is_file() for filename in files)


def _download_file(
    client: httpx.Client,
    repo_id: str,
    filename: str,
    destination: Path,
    model_label: str,
    status: StatusCallback,
) -> None:
    target = destination / filename
    part = destination / f"{filename}.part"
    metadata_url = hf_hub_url(repo_id=repo_id, filename=filename)
    metadata = get_hf_file_metadata(metadata_url)
    expected_size = int(metadata.size or 0)

    if target.is_file() and (not expected_size or target.stat().st_size == expected_size):
        return

    if target.exists():
        if not part.exists() and target.stat().st_size < expected_size:
            target.replace(part)
        else:
            target.unlink()
    if part.exists() and expected_size and part.stat().st_size > expected_size:
        part.unlink()

    for attempt in range(1, MAX_ATTEMPTS + 1):
        downloaded = part.stat().st_size if part.exists() else 0
        if expected_size and downloaded == expected_size:
            part.replace(target)
            return

        metadata = get_hf_file_metadata(metadata_url)
        expected_size = int(metadata.size or expected_size)
        headers = {"User-Agent": "Rechka/0.3.3"}
        if downloaded:
            headers["Range"] = f"bytes={downloaded}-"

        try:
            with client.stream("GET", metadata.location, headers=headers) as response:
                if response.status_code == 416 and expected_size == downloaded:
                    part.replace(target)
                    return
                response.raise_for_status()

                if downloaded and response.status_code == 206:
                    mode = "ab"
                else:
                    mode = "wb"
                    downloaded = 0

                last_update = 0.0
                with part.open(mode) as output:
                    for chunk in response.iter_bytes(CHUNK_SIZE):
                        if not chunk:
                            continue
                        output.write(chunk)
                        downloaded += len(chunk)
                        now = time.monotonic()
                        if now - last_update >= 0.6:
                            if expected_size:
                                percent = min(100, round(downloaded * 100 / expected_size))
                                status(
                                    f"Скачивание {model_label}: {percent}% "
                                    f"({_format_size(downloaded)} из "
                                    f"{_format_size(expected_size)})"
                                )
                            else:
                                status(
                                    f"Скачивание {model_label}: "
                                    f"{_format_size(downloaded)}"
                                )
                            last_update = now
        except (httpx.HTTPError, OSError) as exc:
            if attempt >= MAX_ATTEMPTS:
                raise RuntimeError(
                    f"Не удалось скачать {filename}: {exc}"
                ) from exc
            status(
                f"Связь прервалась — продолжаю загрузку "
                f"(попытка {attempt + 1}/{MAX_ATTEMPTS})…"
            )
            time.sleep(min(2**attempt, 8))
            continue

        actual_size = part.stat().st_size if part.exists() else 0
        if not expected_size or actual_size == expected_size:
            part.replace(target)
            return
        if attempt < MAX_ATTEMPTS:
            status("Файл получен не полностью — продолжаю с места остановки…")

    raise RuntimeError(f"Загрузка {filename} не завершена")


def download_model_files(
    repo_id: str,
    destination: Path,
    files: Sequence[str],
    model_label: str,
    status: StatusCallback,
) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    timeout = httpx.Timeout(connect=30.0, read=60.0, write=60.0, pool=30.0)
    limits = httpx.Limits(max_connections=1, max_keepalive_connections=1)
    with httpx.Client(
        follow_redirects=True,
        timeout=timeout,
        limits=limits,
    ) as client:
        for filename in files:
            _download_file(
                client,
                repo_id,
                filename,
                destination,
                model_label,
                status,
            )

    if not model_is_complete(destination, files):
        raise RuntimeError("Загрузка модели не завершена")
    return destination
