"""Build-time cache rejects corrupt or unbounded external artifacts."""

import hashlib
import io
from pathlib import Path

import pytest

from scripts.fetch_online_payload import fetch_artifact


def artifact(data: bytes) -> dict:
    digest = hashlib.sha256(data).hexdigest()
    return {"url": "https://files.pythonhosted.org/packages/test.whl",
            "sha256": digest, "size": len(data), "cache_name": digest + ".zip"}


def test_verified_cache_is_reused_without_network(tmp_path: Path) -> None:
    data = b"a checked wheel"
    entry = artifact(data)
    (tmp_path / entry["cache_name"]).write_bytes(data)
    def unexpected(*args, **kwargs):
        raise AssertionError("cache hit must not download")
    assert fetch_artifact(entry, tmp_path, opener=unexpected) == "cached"


def test_corrupt_download_is_never_published(tmp_path: Path) -> None:
    entry = artifact(b"good")
    with pytest.raises(ValueError, match="verification"):
        fetch_artifact(entry, tmp_path, opener=lambda *a, **kw: io.BytesIO(b"evil"), attempts=1)
    assert not (tmp_path / entry["cache_name"]).exists()
    assert not list(tmp_path.glob("*.partial"))


def test_bad_cache_is_replaced_by_verified_download(tmp_path: Path) -> None:
    entry = artifact(b"good")
    (tmp_path / entry["cache_name"]).write_bytes(b"evil")
    assert fetch_artifact(entry, tmp_path, opener=lambda *a, **kw: io.BytesIO(b"good")) == "downloaded"
    assert (tmp_path / entry["cache_name"]).read_bytes() == b"good"


@pytest.mark.parametrize("field,value", [
    ("cache_name", "../outside.zip"),
    ("url", "http://files.pythonhosted.org/package.whl"),
    ("url", "https://example.org/package.whl"),
    ("size", -1),
    ("sha256", "f"),
])
def test_invalid_artifact_rejected_before_network(tmp_path: Path, field: str, value) -> None:
    entry = artifact(b"good")
    entry[field] = value
    def unexpected(*args, **kwargs):
        raise AssertionError("invalid input must not download")
    with pytest.raises(ValueError):
        fetch_artifact(entry, tmp_path, opener=unexpected)


def test_response_size_is_bounded(tmp_path: Path) -> None:
    entry = artifact(b"good")
    with pytest.raises(ValueError, match="size"):
        fetch_artifact(entry, tmp_path, opener=lambda *a, **kw: io.BytesIO(b"oversized"), attempts=1)
    assert not (tmp_path / entry["cache_name"]).exists()
