"""Pinned, resumable downloads shared by Settings and the local STT engines."""

import hashlib
import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from filelock import FileLock, Timeout
from platformdirs import user_cache_path

from .bundle import SttBundleValidationError, default_stt_manifest_path, load_stt_manifest, resolve_stt_model

MODEL_LABELS = {
    "tiny": "Tiny · lowest memory",
    "base": "Base · low memory",
    "small": "Small · balanced",
    "large-v3-turbo": "Large v3 Turbo · highest memory",
}
GPU_ENCODERS = {"openvino": "OpenVINO", "directml": "DirectML"}
Progress = Callable[[int, int], None]


class ModelUnavailable(RuntimeError):
    """A user-facing STT error with no paths, URLs or credentials."""


@dataclass(frozen=True)
class ModelFile:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Snapshot:
    repository: str
    revision: str
    files: tuple[ModelFile, ...]


@lru_cache
def snapshot(model: str, component: str) -> Snapshot:
    manifest_path = default_stt_manifest_path()
    if component == "model" and model == "large-v3-turbo":
        manifest = load_stt_manifest(manifest_path)
        return Snapshot(manifest.repository, manifest.revision, tuple(
            ModelFile(f.path, f.size, f.sha256) for f in manifest.files if f.runtime_required
        ))
    entry = json.loads(manifest_path.with_name("stt_catalog.json").read_text("utf-8"))[model][component]
    return Snapshot(entry["repository"], entry["revision"], tuple(ModelFile(**f) for f in entry["files"]))


def _valid(path: Path, file: ModelFile) -> bool:
    if not path.is_file() or path.stat().st_size != file.size:
        return False
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() == file.sha256


def _endpoints() -> tuple[str, ...]:
    official = "https://huggingface.co"
    mirror = os.getenv("HF_ENDPOINT", "").strip().rstrip("/")
    parsed = urlsplit(mirror)
    if parsed.scheme == "https" and parsed.hostname and not (
        parsed.username or parsed.password or parsed.query or parsed.fragment
    ):
        return tuple(dict.fromkeys((mirror, official)))
    return (official,)


def _fetch(client: httpx.Client, target: Path, file: ModelFile, resource: str,
           progress: Callable[[int], None]) -> None:
    partial = target.with_suffix(target.suffix + ".part")
    endpoints = _endpoints()
    for attempt in range(3):
        offset = partial.stat().st_size if partial.exists() else 0
        if offset >= file.size:
            if _valid(partial, file):
                partial.replace(target)
                return
            partial.unlink()
            offset = 0
        url = endpoints[attempt % len(endpoints)] + resource
        headers = {"Accept-Encoding": "identity"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        try:
            # Fresh redirect on retry, including when a CDN signed URL has expired.
            params = {"download": "true", "retry": str(attempt)} if attempt else None
            with client.stream("GET", url, headers=headers, params=params) as response:
                if response.status_code == 416:
                    partial.unlink(missing_ok=True)
                response.raise_for_status()
                if response.status_code == 206:
                    span = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
                    if not span or int(span[1]) != offset or int(span[3]) != file.size:
                        raise ValueError("Unexpected byte range")
                elif response.status_code == 200:
                    offset = 0
                else:
                    raise ValueError("Missing response body")
                with partial.open("ab" if offset else "wb") as stream:
                    for chunk in response.iter_bytes(256 * 1024):
                        offset += len(chunk)
                        if offset > file.size:
                            raise ValueError("Excess model data")
                        stream.write(chunk)
                        progress(offset)
            if _valid(partial, file):
                partial.replace(target)
                return
            if offset >= file.size:
                partial.unlink()
        except (httpx.HTTPError, ValueError):
            pass
        if attempt < 2:
            time.sleep(0.5 * (attempt + 1))
    raise ModelUnavailable("Model download failed verification or was interrupted. Retry in Settings > Audio to resume.")


def prepare_snapshot(data: Snapshot, *, download: bool, progress: Callable[[int], None],
                     cache: Path | None = None) -> Path:
    from huggingface_hub import try_to_load_from_cache

    # Reuse snapshots created by earlier faster-whisper versions even when offline.
    cached = try_to_load_from_cache(data.repository, data.files[0].path, revision=data.revision)
    if isinstance(cached, str):
        root = Path(cached).parents[len(Path(data.files[0].path).parts) - 1]
        if all(_valid(root / f.path, f) for f in data.files):
            progress(sum(f.size for f in data.files))
            return root
    root = (cache or user_cache_path("InterviewAssistant", appauthor=False)) / "stt" / data.revision
    root.mkdir(parents=True, exist_ok=True)
    done = 0
    lock = FileLock(root / ".download.lock")
    try:
        acquired = lock.acquire(timeout=-1 if download else 0)
    except Timeout as error:
        raise ModelUnavailable("Model download is in progress. Run checks when it finishes.") from error
    with acquired, httpx.Client(follow_redirects=True, timeout=30) as client:
        for file in data.files:
            path = root / file.path
            if not _valid(path, file):
                if not download or os.getenv("HF_HUB_OFFLINE", "").upper() in {"1", "YES", "TRUE", "ON"}:
                    raise ModelUnavailable("Model files are missing or damaged. Open Settings > Audio and select Download model.")
                path.parent.mkdir(parents=True, exist_ok=True)
                resource = f"/{data.repository}/resolve/{data.revision}/{file.path}"
                _fetch(client, path, file, resource, lambda count: progress(done + count))
            done += file.size
            progress(done)
    return root


def prepare_model(model: str, device: str, *, download: bool,
                  progress: Progress = lambda *_: None) -> tuple[str, Path | None]:
    try:
        location = resolve_stt_model(model)
    except SttBundleValidationError:
        # Repair a damaged read-only installation using verified per-user files.
        location = model
    if model not in MODEL_LABELS:
        if device in GPU_ENCODERS:
            raise ModelUnavailable("Select a listed Whisper model for GPU encoding.")
        return location, None
    weights = snapshot(model, "model")
    encoder = snapshot(model, "encoder") if device in GPU_ENCODERS else None
    weight_bytes = sum(f.size for f in weights.files)
    total = weight_bytes + (sum(f.size for f in encoder.files) if encoder else 0)
    progress(0, total)
    if location == model:
        location = str(prepare_snapshot(weights, download=download, progress=lambda n: progress(n, total)))
    progress(weight_bytes, total)
    if not encoder:
        return location, None
    root = prepare_snapshot(encoder, download=download, progress=lambda n: progress(weight_bytes + n, total))
    return location, root / encoder.files[0].path
