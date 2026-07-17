from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from .config import bundled_root, data_dir


GITHUB_API = "https://api.github.com"
MAX_INSTALLER_SIZE = 1_500_000_000
REPOSITORY_PATTERN = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,98}[A-Za-z0-9])?/"
    r"[A-Za-z0-9_.-]{1,100}$"
)
INSTALLER_PATTERN = re.compile(
    r"^Rechka-Setup-(?P<version>\d+\.\d+\.\d+)\.exe$",
    re.IGNORECASE,
)
SHA256_PATTERN = re.compile(r"^sha256:(?P<digest>[a-fA-F0-9]{64})$")


class UpdateError(RuntimeError):
    """Raised when an update cannot be trusted or installed."""


@dataclass(frozen=True, slots=True)
class ReleaseAsset:
    name: str
    url: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class UpdateInfo:
    version: str
    page_url: str
    notes: str
    asset: ReleaseAsset


def configured_repository() -> str:
    """Read the public GitHub repository from an environment or bundled file."""
    environment_value = (
        os.environ.get("RECHKA_UPDATE_REPOSITORY")
        or os.environ.get("VOICE_INPUT_UPDATE_REPOSITORY", "")
    ).strip()
    if environment_value:
        return environment_value if REPOSITORY_PATTERN.fullmatch(environment_value) else ""

    path = bundled_root() / "release.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ""

    repository = str(payload.get("repository", "")).strip()
    return repository if REPOSITORY_PATTERN.fullmatch(repository) else ""


def parse_version(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:[-+][A-Za-z0-9.-]+)?", value.strip())
    if not match:
        raise UpdateError(f"Некорректная версия релиза: {value!r}")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def update_from_release_payload(
    payload: dict[str, Any],
    current_version: str,
) -> UpdateInfo | None:
    if payload.get("draft"):
        return None

    version = str(payload.get("tag_name", "")).strip().removeprefix("v")
    if parse_version(version) <= parse_version(current_version):
        return None

    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise UpdateError("В релизе отсутствует установщик.")

    expected_name = f"Rechka-Setup-{version}.exe".lower()
    selected: dict[str, Any] | None = None
    for item in assets:
        if not isinstance(item, dict):
            continue
        if str(item.get("name", "")).lower() == expected_name:
            selected = item
            break

    if selected is None:
        raise UpdateError(f"В релизе нет файла Rechka-Setup-{version}.exe.")

    name = str(selected.get("name", ""))
    if Path(name).name != name or not INSTALLER_PATTERN.fullmatch(name):
        raise UpdateError("Имя установщика в релизе небезопасно.")

    url = str(selected.get("browser_download_url", ""))
    parsed_url = urlparse(url)
    if parsed_url.scheme != "https" or parsed_url.hostname != "github.com":
        raise UpdateError("Установщик опубликован не на доверенном домене GitHub.")

    try:
        size = int(selected.get("size", 0))
    except (TypeError, ValueError) as exc:
        raise UpdateError("Некорректный размер установщика.") from exc
    if size <= 0 or size > MAX_INSTALLER_SIZE:
        raise UpdateError("Размер установщика выходит за допустимые пределы.")

    digest_match = SHA256_PATTERN.fullmatch(str(selected.get("digest", "")))
    if digest_match is None:
        raise UpdateError("GitHub не предоставил SHA-256 установщика.")

    page_url = str(payload.get("html_url", ""))
    page_host = urlparse(page_url).hostname
    if page_host not in {"github.com", None}:
        page_url = ""

    return UpdateInfo(
        version=version,
        page_url=page_url,
        notes=str(payload.get("body") or "").strip(),
        asset=ReleaseAsset(
            name=name,
            url=url,
            size=size,
            sha256=digest_match.group("digest").lower(),
        ),
    )


def check_for_update(repository: str, current_version: str) -> UpdateInfo | None:
    if not REPOSITORY_PATTERN.fullmatch(repository):
        raise UpdateError("Канал обновлений настроен некорректно.")

    headers = {
        "Accept": "application/vnd.github+json",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "User-Agent": f"Rechka/{current_version}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    timeout = httpx.Timeout(connect=8.0, read=20.0, write=10.0, pool=5.0)
    try:
        response = httpx.get(
            f"{GITHUB_API}/repos/{repository}/releases/latest",
            headers=headers,
            timeout=timeout,
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise UpdateError(f"Не удалось проверить обновления: {exc}") from exc

    if not isinstance(payload, dict):
        raise UpdateError("GitHub вернул неожиданный ответ.")
    return update_from_release_payload(payload, current_version)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def download_update(
    update: UpdateInfo,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    destination_dir = data_dir() / "updates"
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / update.asset.name
    partial = destination.with_suffix(destination.suffix + ".part")
    for candidate in destination_dir.glob("Rechka-Setup-*.exe*"):
        if candidate not in {destination, partial} and candidate.is_file():
            candidate.unlink(missing_ok=True)

    if destination.exists():
        if (
            destination.stat().st_size == update.asset.size
            and _file_sha256(destination) == update.asset.sha256
        ):
            if progress:
                progress(update.asset.size, update.asset.size)
            return destination
        destination.unlink()

    headers = {"User-Agent": f"Rechka-Updater/{update.version}"}
    timeout = httpx.Timeout(connect=15.0, read=90.0, write=30.0, pool=10.0)
    digest = hashlib.sha256()
    downloaded = 0

    try:
        with httpx.stream(
            "GET",
            update.asset.url,
            headers=headers,
            timeout=timeout,
            follow_redirects=True,
        ) as response:
            response.raise_for_status()
            with partial.open("wb") as stream:
                for chunk in response.iter_bytes(1024 * 1024):
                    if not chunk:
                        continue
                    downloaded += len(chunk)
                    if downloaded > MAX_INSTALLER_SIZE:
                        raise UpdateError("Загружаемый файл слишком большой.")
                    digest.update(chunk)
                    stream.write(chunk)
                    if progress:
                        progress(downloaded, update.asset.size)
    except (httpx.HTTPError, OSError):
        partial.unlink(missing_ok=True)
        raise
    except Exception:
        partial.unlink(missing_ok=True)
        raise

    if downloaded != update.asset.size:
        partial.unlink(missing_ok=True)
        raise UpdateError(
            f"Размер загруженного файла не совпал: {downloaded} вместо "
            f"{update.asset.size} байт."
        )
    if digest.hexdigest() != update.asset.sha256:
        partial.unlink(missing_ok=True)
        raise UpdateError("SHA-256 загруженного установщика не совпал.")

    partial.replace(destination)
    return destination


def launch_update_installer(path: Path) -> None:
    resolved = path.resolve()
    updates_root = (data_dir() / "updates").resolve()
    if resolved.parent != updates_root or resolved.suffix.lower() != ".exe":
        raise UpdateError("Недопустимый путь к установщику.")
    if not resolved.is_file():
        raise UpdateError("Загруженный установщик не найден.")

    creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(
        [
            str(resolved),
            "/SP-",
            "/VERYSILENT",
            "/SUPPRESSMSGBOXES",
            "/CLOSEAPPLICATIONS",
            "/FORCECLOSEAPPLICATIONS",
            "/NORESTART",
            "/UPDATE=1",
        ],
        close_fds=True,
        creationflags=creation_flags,
    )
