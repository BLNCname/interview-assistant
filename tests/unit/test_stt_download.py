import hashlib

import httpx
import pytest

from interview_assistant.stt import download


@pytest.mark.parametrize("supports_ranges", [True, False])
def test_interrupted_model_download_resumes_or_restarts(tmp_path, monkeypatch, supports_ranges):
    content = b"valid speech model"
    target = tmp_path / "model.bin"
    partial = target.with_suffix(".bin.part")
    partial.write_bytes(content[:5])
    file = download.ModelFile("model.bin", len(content), hashlib.sha256(content).hexdigest())
    monkeypatch.setattr(download.time, "sleep", lambda _: None)
    monkeypatch.setenv("HF_ENDPOINT", "https://mirror.example")
    hosts = []

    def serve(request):
        hosts.append(request.url.host)
        assert request.headers["range"] == "bytes=5-"
        if len(hosts) == 1:
            return httpx.Response(503)
        if supports_ranges:
            return httpx.Response(206, content=content[5:], headers={
                "Content-Range": f"bytes 5-{len(content)-1}/{len(content)}",
            })
        return httpx.Response(200, content=content)

    with httpx.Client(transport=httpx.MockTransport(serve)) as client:
        download._fetch(client, target, file, "/pinned", lambda _: None)
    assert hosts == ["mirror.example", "huggingface.co"]
    assert target.read_bytes() == content
    assert not partial.exists()


def test_invalid_download_is_never_published(tmp_path, monkeypatch):
    monkeypatch.setattr(download.time, "sleep", lambda _: None)
    file = download.ModelFile("weights", 3, hashlib.sha256(b"yes").hexdigest())
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"bad"))) as client:
        with pytest.raises(download.ModelUnavailable):
            download._fetch(client, tmp_path / "weights", file, "/pinned", lambda _: None)
    assert not (tmp_path / "weights").exists()


def test_catalog_contains_pinned_weights_and_matching_gpu_encoders():
    for name in download.MODEL_LABELS:
        for part in ("model", "encoder"):
            snapshot = download.snapshot(name, part)
            assert len(snapshot.revision) == 40
            assert all(len(f.sha256) == 64 and f.size > 0 for f in snapshot.files)
            if part == "encoder":
                assert snapshot.repository.endswith("/whisper-" + name)
