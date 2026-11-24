"""Fetch the pinned NVIDIA user-mode libraries into the user's cache."""

import json
import os
from collections.abc import Callable
from pathlib import Path, PurePosixPath
import shutil
from tempfile import NamedTemporaryFile
import zipfile

from filelock import FileLock
import httpx

from interview_assistant.windows_cuda import CUDA_RUNTIME_ID, REQUIRED_CUDA_DLLS, local_runtime, packaged_directories
from .bundle import default_stt_manifest_path
from .download import ModelFile, ModelUnavailable, _fetch, _valid, cache_root, progress_messages


def ensure_cuda_libraries(report: Callable[[str], None] = lambda _: None) -> None:
    doc = json.loads(default_stt_manifest_path().with_name("cuda_runtime_manifest.json").read_text("utf-8"))
    wheels = doc["wheels"]
    files = [ModelFile(**item) for w in wheels for item in w["files"]]
    if (doc["runtime"] != CUDA_RUNTIME_ID or {Path(f.path).name for f in files} != set(REQUIRED_CUDA_DLLS)
            or any(not w["url"].startswith("https://files.pythonhosted.org/") for w in wheels)):
        raise ModelUnavailable("Invalid NVIDIA runtime manifest")
    # A developer environment or an upgraded installation may already have the exact DLLs.
    if all(any(_valid(d / Path(f.path).name, f) for d in packaged_directories()) for f in files):
        return
    root = local_runtime()
    root.mkdir(parents=True, exist_ok=True)
    with FileLock(root / ".install.lock"), httpx.Client(timeout=30, follow_redirects=True) as client:
        for wheel in wheels:
            missing = [ModelFile(**f) for f in wheel["files"]
                       if not _valid(root / PurePosixPath(f["path"]).name, ModelFile(**f))]
            if not missing:
                continue
            wheel_file = ModelFile(wheel["display_name"], wheel["size"], wheel["sha256"])
            archive = cache_root() / "downloads" / (wheel_file.sha256 + ".zip")
            legacy = cache_root() / "InstallerCache" / archive.name
            if _valid(legacy, wheel_file):
                archive = legacy
            elif not _valid(archive, wheel_file):
                if os.environ.get("HF_HUB_OFFLINE", "").upper() in {"1", "YES", "TRUE", "ON"}:
                    raise ModelUnavailable("NVIDIA libraries are unavailable in the offline cache")
                archive.parent.mkdir(parents=True, exist_ok=True)
                progress = progress_messages(wheel_file.path.split("-")[0], report)
                _fetch(client, archive, wheel_file, "", lambda n: progress(n, wheel_file.size), urls=(wheel["url"],))
            with zipfile.ZipFile(archive) as z:
                for f in missing:
                    if z.getinfo(f.path).file_size != f.size:
                        raise ModelUnavailable("NVIDIA library size differs from the pinned manifest")
                    with NamedTemporaryFile(dir=root, delete=False) as out:
                        partial = Path(out.name)
                    try:
                        with z.open(f.path) as src, partial.open("wb") as dest:
                            shutil.copyfileobj(src, dest, length=1024 * 1024)
                        if not _valid(partial, f):
                            raise ModelUnavailable("NVIDIA library checksum mismatch")
                        partial.replace(root / Path(f.path).name)
                    finally:
                        partial.unlink(missing_ok=True)
        (root / "verified").touch()
