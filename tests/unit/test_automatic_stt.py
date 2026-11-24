import hashlib
import json
import tomllib
from pathlib import Path
import zipfile

import pytest

from interview_assistant.config import AppConfig
from interview_assistant.stt import automatic, cuda_setup
from interview_assistant.stt.adapters import Adapter
from interview_assistant.stt.download import ModelUnavailable
from interview_assistant.stt.profile import defer_setup, load_profile, profile_path


RTX = Adapter("cuda", 1, "NVIDIA RTX", 8 << 30, 86)
INTEL = Adapter("openvino", 0, "Intel Graphics (iGPU)")
RADEON = Adapter("directml", 2, "AMD Radeon", 8 << 30)
CPU = Adapter("cpu", 0, "CPU")


@pytest.fixture
def preparation(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    (tmp_path / ".env").touch()
    for name in ("STT_MODEL", "STT_COMPUTE_TYPE", "STT_DEVICE", "STT_LANGUAGE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("INTERVIEW_ASSISTANT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(automatic, "hardware_signature", lambda: [[2, 0x1002, "AMD Radeon", 8 << 30]])
    weights, workers, libs = [], [], []
    monkeypatch.setattr(automatic, "prepare_model", lambda model, device, **_: weights.append((model, device)))
    monkeypatch.setattr(automatic, "ensure_cuda_libraries", lambda _: libs.append(True))
    monkeypatch.setattr(automatic, "_run_worker", lambda kind, model, gpu, precision: workers.append((kind, gpu, precision)))
    return path, workers, libs


@pytest.mark.parametrize("found,selected", [
    ([RTX, Adapter("directml", 0, RTX.name, RTX.memory)], RTX), ([INTEL], INTEL),
    ([RADEON, INTEL], RADEON), ([], CPU),
    ([Adapter("cuda", 0, "GTX", 4 << 30, 75), Adapter("directml", 3, "GTX", 4 << 30)],
     Adapter("directml", 3, "GTX", 4 << 30)),
])
def test_prepare_detects_compiles_and_persists_effective_profile(preparation, monkeypatch, found, selected):
    path, workers, libs = preparation
    monkeypatch.setattr(automatic, "detect_adapters", lambda: found)
    assert automatic.needs_first_run(path)
    result = automatic.prepare_hardware(path, lambda _: None)
    assert result["device"] == selected.device and result["gpu_index"] == selected.index
    assert result["speech_verified"] and result["stt_model"] == "base"
    assert [w[0] for w in workers] == (["cuda-encoder", "inference"] if selected.device == "cuda" else ["inference"])
    assert len(libs) == int(selected.device == "cuda")
    config = AppConfig.load(path)
    assert (config.audio.stt_model, config.audio.device, config.audio.gpu_index) == ("base", selected.device, selected.index)
    assert not automatic.needs_first_run(path)
    assert automatic.prepare_hardware(path, lambda _: None, if_needed=True) == result
    monkeypatch.setattr(automatic, "hardware_signature", lambda: [])
    assert automatic.needs_first_run(path)


def test_failure_tries_other_adapter_and_never_publishes_unverified_profile(preparation, monkeypatch):
    path, _, _ = preparation
    monkeypatch.setattr(automatic, "detect_adapters", lambda: [INTEL, RTX])
    def run(kind, model, gpu, precision):
        if gpu == RTX:
            raise ModelUnavailable("CUDA compiler failed")
    monkeypatch.setattr(automatic, "_run_worker", run)
    result = automatic.prepare_hardware(path, lambda _: None)
    assert result["device"] == "openvino" and result["fallbacks"][0]["device"] == "cuda"
    def fail(*_):
        raise ModelUnavailable("no inference")
    monkeypatch.setattr(automatic, "_run_worker", fail)
    with pytest.raises(ModelUnavailable, match="preparation failed"):
        automatic.prepare_hardware(path, lambda _: None)
    assert load_profile(path) == result


def test_manual_config_preserved_and_deferred_portable_can_retry(preparation, monkeypatch):
    path, _, _ = preparation
    path.write_text("audio: {device: cpu, stt_model: tiny, language: ru}\n", encoding="utf-8")
    monkeypatch.setattr(automatic, "detect_adapters", lambda: [RTX])
    defer_setup(path)
    assert not automatic.needs_first_run(path)
    original = path.read_bytes()
    result = automatic.prepare_hardware(path, lambda _: None)
    assert result["device"] == "cpu" and result["stt_model"] == "tiny"
    assert path.read_bytes() == original and not path.with_suffix(".stt-deferred.json").exists()
    assert profile_path(path).is_file()
    assert AppConfig.load(path).audio.language == "ru"


def test_nv_library_cache_verifies_members_and_requires_no_network_on_retry(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    monkeypatch.setattr(cuda_setup, "local_runtime", lambda: root)
    monkeypatch.setattr(cuda_setup, "cache_root", lambda: tmp_path)
    monkeypatch.setattr(cuda_setup, "packaged_directories", lambda: ())
    monkeypatch.setattr(cuda_setup, "default_stt_manifest_path", lambda: tmp_path / "stt.json")
    seed = tmp_path / "seed.zip"
    files = []
    with zipfile.ZipFile(seed, "w") as z:
        for dll in cuda_setup.REQUIRED_CUDA_DLLS:
            data = dll.encode()
            member = "nvidia/bin/" + dll
            files.append({"path": member, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
            z.writestr(member, data)
    digest = hashlib.sha256(seed.read_bytes()).hexdigest()
    wheels = [{"display_name": "fixture.whl", "url": "https://files.pythonhosted.org/fixture.whl",
               "sha256": digest, "size": seed.stat().st_size, "files": files}]
    (tmp_path / "cuda_runtime_manifest.json").write_text(json.dumps({"runtime": cuda_setup.CUDA_RUNTIME_ID, "wheels": wheels}))
    (tmp_path / "InstallerCache").mkdir()
    seed.rename(tmp_path / "InstallerCache" / (digest+".zip"))
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    cuda_setup.ensure_cuda_libraries()
    assert (root / "verified").exists()
    (root / files[0]["path"].split("/")[-1]).write_bytes(b"damaged")
    cuda_setup.ensure_cuda_libraries()
    assert {p.name for p in root.glob("*.dll")} == set(cuda_setup.REQUIRED_CUDA_DLLS)
    assert all((root / d).read_bytes() == d.encode() for d in cuda_setup.REQUIRED_CUDA_DLLS)


def test_public_cuda_runtime_manifest_matches_locked_vendor_wheels():
    root = Path(__file__).resolve().parents[2]
    runtime = json.loads((root / "packaging/cuda_runtime_manifest.json").read_text())
    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    locked = {w["url"]: w for p in lock["package"] for w in p.get("wheels", ())}
    for wheel in runtime["wheels"]:
        source = locked[wheel["url"]]
        assert source["hash"] == "sha256:" + wheel["sha256"]
        assert source["size"] == wheel["size"]
    assert {Path(f["path"]).name for w in runtime["wheels"] for f in w["files"]} == set(cuda_setup.REQUIRED_CUDA_DLLS)


def test_cuda_forces_ptx_only_for_encoder_not_vendor_decoder(monkeypatch):
    seen = []
    def run(args, *, env, **options):
        from types import SimpleNamespace
        seen.append((args, env))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(automatic.subprocess, "run", run)
    for kind in ("cuda-encoder", "inference"):
        automatic._run_worker(kind, "base", RTX, "int8_float16")
    assert seen[0][1]["CUDA_FORCE_PTX_JIT"] == "1"
    assert "CUDA_FORCE_PTX_JIT" not in seen[1][1]
    assert all(args[args.index("--adapter") + 1] == "1" for args, _ in seen)
