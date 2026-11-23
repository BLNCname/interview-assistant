import hashlib
import json
import zipfile

import pytest

from scripts.build_portable_archives import build_archives


def test_portable_core_and_cuda_pack_reconstruct_exact_distribution(tmp_path):
    dist = tmp_path / "InterviewAssistant"
    items = []
    files = {"InterviewAssistant.exe": None, "_internal/models/stt/large/model.bin": "model",
             "_internal/nvidia/cudnn/bin/runtime.dll": "gpu"}
    for name, artifact in files.items():
        p = dist / name
        p.parent.mkdir(parents=True, exist_ok=True)
        data = name.encode()
        p.write_bytes(data)
        items.append(dict(path=name, size=len(data), sha256=hashlib.sha256(data).hexdigest(), artifact=artifact))
    manifest = tmp_path / "payload.json"
    manifest.write_text(json.dumps({"artifacts_verified": True, "artifacts": [{"id": "gpu", "cuda": True}],
                                    "files": items}))
    core, cuda = build_archives(dist, manifest, tmp_path / "release", "0.1.6")
    with zipfile.ZipFile(core) as a, zipfile.ZipFile(cuda) as b:
        assert set(a.namelist()) == {f"{dist.name}/{name}" for name, key in files.items() if key != "gpu"}
        assert set(b.namelist()) == {f"{dist.name}/{name}" for name, key in files.items() if key == "gpu"}
        for f in items:
            assert (b if f["artifact"] == "gpu" else a).read(f"{dist.name}/{f['path']}") == (dist / f["path"]).read_bytes()
    (dist / "InterviewAssistant.exe").write_bytes(b"damaged")
    with pytest.raises(ValueError, match="differs"):
        build_archives(dist, manifest, tmp_path / "bad", "0.1.6")
    assert not list((tmp_path / "bad").iterdir())
