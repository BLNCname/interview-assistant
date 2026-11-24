import hashlib
import json
import zipfile

import pytest

from scripts.build_portable_archives import build_archives


def test_portable_excludes_weights_and_cuda_and_verifies_core(tmp_path):
    dist = tmp_path / "InterviewAssistant"
    items = []
    files = {"InterviewAssistant.exe": None, "_internal/models/stt/large/model.bin": "model",
             "_internal/nvidia/cudnn/bin/runtime.dll": "gpu", "_internal/packaging/stt_catalog.json": None}
    for name, artifact in files.items():
        path = dist / name
        path.parent.mkdir(parents=True, exist_ok=True)
        data = name.encode()
        path.write_bytes(data)
        items.append(dict(path=name, size=len(data), sha256=hashlib.sha256(data).hexdigest(), artifact=artifact))
    manifest = tmp_path / "payload.json"
    payload = {"artifacts_verified": True, "artifacts": [{"id": "gpu", "cuda": True},
               {"id": "model", "kind": "file"}], "files": items}
    manifest.write_text(json.dumps(payload))
    core, = build_archives(dist, manifest, tmp_path / "release", "0.1.7")
    with zipfile.ZipFile(core) as archive:
        assert set(archive.namelist()) == {f"{dist.name}/{name}" for name, group in files.items() if group is None}
        for name in archive.namelist():
            assert archive.read(name) == (tmp_path / name).read_bytes()
    (dist / "InterviewAssistant.exe").write_bytes(b"damaged")
    with pytest.raises(ValueError, match="differs"):
        build_archives(dist, manifest, tmp_path / "bad", "0.1.7")
    assert not list((tmp_path / "bad").iterdir())
    payload["files"] = [dict(items[1], artifact=None)]
    manifest.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="weights"):
        build_archives(dist, manifest, tmp_path / "unclassified", "0.1.7")
