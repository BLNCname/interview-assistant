import importlib
import importlib.util
import json
from pathlib import Path
import tomllib
from types import SimpleNamespace

import numpy as np
import pytest

from interview_assistant.stt.engine import TranscriptionResult, WhisperEngine


def _test_manifest_payload(
    *,
    file_name: str = "model.bin",
    content: bytes = b"test-model",
) -> dict[str, object]:
    import hashlib

    return {
        "schema_version": 1,
        "name": "test-model",
        "repository": "owner/test-model",
        "revision": "a" * 40,
        "license": "MIT",
        "bundle_subdirectory": "models/stt/test-model",
        "files": [
            {
                "path": file_name,
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "runtime_required": True,
            }
        ],
    }


def test_stt_bundle_module_is_available() -> None:
    assert importlib.util.find_spec("interview_assistant.stt.bundle") is not None


def test_stt_manifest_validation_rejects_invalid_schema_without_leaking_path(
    tmp_path: Path,
) -> None:
    module = importlib.import_module("interview_assistant.stt.bundle")
    manifest_path = tmp_path / "private-model-location" / "manifest.json"
    manifest_path.parent.mkdir()
    manifest_path.write_text(json.dumps({"name": "large-v3-turbo"}), encoding="utf-8")

    error_type = getattr(module, "SttManifestError", None)
    load_manifest = getattr(module, "load_stt_manifest", None)
    assert isinstance(error_type, type)
    assert callable(load_manifest)
    with pytest.raises(error_type) as captured:
        load_manifest(manifest_path)

    assert str(manifest_path) not in str(captured.value)
    assert "private-model-location" not in str(captured.value)


def test_stt_manifest_validation_loads_a_valid_schema(tmp_path: Path) -> None:
    module = importlib.import_module("interview_assistant.stt.bundle")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_test_manifest_payload()), encoding="utf-8")

    manifest = module.load_stt_manifest(manifest_path)

    assert manifest.name == "test-model"
    assert manifest.repository == "owner/test-model"
    assert manifest.revision == "a" * 40
    assert manifest.license == "MIT"
    assert manifest.bundle_subdirectory == "models/stt/test-model"
    assert [(item.path, item.size, item.runtime_required) for item in manifest.files] == [
        ("model.bin", 10, True)
    ]


@pytest.mark.parametrize(
    "file_name",
    [
        "C:model.bin",
        "model.bin:alternate-stream",
        "CON",
        "nul.json",
        "COM1.bin",
        "LPT9.txt",
        "trailing-dot.",
        "trailing-space ",
        "model?.bin",
        "../model.bin",
        "..\\model.bin",
        "/model.bin",
        "subdirectory/model.bin",
    ],
)
def test_stt_manifest_rejects_windows_unsafe_or_escaping_filenames(
    tmp_path: Path,
    file_name: str,
) -> None:
    module = importlib.import_module("interview_assistant.stt.bundle")
    manifest_path = tmp_path / "private-manifest" / "manifest.json"
    manifest_path.parent.mkdir()
    manifest_path.write_text(
        json.dumps(_test_manifest_payload(file_name=file_name)),
        encoding="utf-8",
    )

    with pytest.raises(module.SttManifestError) as captured:
        module.load_stt_manifest(manifest_path)

    assert str(captured.value) == "STT model manifest is invalid"
    assert file_name not in str(captured.value)
    assert "private-manifest" not in str(captured.value)


def test_stt_manifest_rejects_case_insensitive_filename_collisions(
    tmp_path: Path,
) -> None:
    module = importlib.import_module("interview_assistant.stt.bundle")
    payload = _test_manifest_payload()
    files = payload["files"]
    assert isinstance(files, list)
    files.append(
        {
            "path": "MODEL.BIN",
            "size": 10,
            "sha256": "92cba8675b9f27a7d3c8b397227778a7b7d72851e5685e635036e919a568ec5a",
            "runtime_required": True,
        }
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(module.SttManifestError, match="manifest is invalid"):
        module.load_stt_manifest(manifest_path)


def test_complete_stt_bundle_resolves_to_explicit_directory(tmp_path: Path) -> None:
    module = importlib.import_module("interview_assistant.stt.bundle")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_test_manifest_payload()), encoding="utf-8")
    manifest = module.load_stt_manifest(manifest_path)
    root = tmp_path / "runtime-root"
    bundle_directory = root / "models" / "stt" / "test-model"
    bundle_directory.mkdir(parents=True)
    (bundle_directory / "model.bin").write_bytes(b"test-model")

    resolved = module.resolve_stt_model(
        "test-model",
        roots=(root,),
        manifest=manifest,
    )

    assert resolved == str(bundle_directory)


def test_missing_stt_bundle_falls_back_to_configured_alias(tmp_path: Path) -> None:
    module = importlib.import_module("interview_assistant.stt.bundle")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_test_manifest_payload()), encoding="utf-8")
    manifest = module.load_stt_manifest(manifest_path)

    resolved = module.resolve_stt_model(
        "test-model",
        roots=(tmp_path / "empty-runtime-root",),
        manifest=manifest,
    )

    assert resolved == "test-model"


@pytest.mark.parametrize("failure_mode", ["missing", "wrong_size", "wrong_hash"])
def test_existing_invalid_stt_bundle_fails_closed_without_leaking_path(
    tmp_path: Path,
    failure_mode: str,
) -> None:
    module = importlib.import_module("interview_assistant.stt.bundle")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_test_manifest_payload()), encoding="utf-8")
    manifest = module.load_stt_manifest(manifest_path)
    root = tmp_path / "private-runtime-root"
    bundle_directory = root / "models" / "stt" / "test-model"
    bundle_directory.mkdir(parents=True)
    if failure_mode == "wrong_size":
        (bundle_directory / "model.bin").write_bytes(b"wrong-model")
    elif failure_mode == "wrong_hash":
        (bundle_directory / "model.bin").write_bytes(b"wrong-data")

    error_type = getattr(module, "SttBundleValidationError", None)
    assert isinstance(error_type, type)
    with pytest.raises(error_type) as captured:
        module.resolve_stt_model("test-model", roots=(root,), manifest=manifest)

    assert str(root) not in str(captured.value)
    assert "private-runtime-root" not in str(captured.value)


@pytest.mark.parametrize("probe_name", ["exists", "is_symlink"])
@pytest.mark.parametrize("probe_error", [OSError, ValueError])
def test_candidate_probe_errors_fail_closed_without_leaking_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    probe_name: str,
    probe_error: type[Exception],
) -> None:
    module = importlib.import_module("interview_assistant.stt.bundle")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_test_manifest_payload()), encoding="utf-8")
    manifest = module.load_stt_manifest(manifest_path)
    root = tmp_path / "private-probe-root"
    candidate = root / "models" / "stt" / "test-model"
    original_probe = getattr(Path, probe_name)

    def failing_probe(path: Path) -> bool:
        if path == candidate:
            raise probe_error(f"private probe failed at {candidate}")
        return bool(original_probe(path))

    monkeypatch.setattr(Path, probe_name, failing_probe)

    with pytest.raises(module.SttBundleValidationError) as captured:
        module.resolve_stt_model("test-model", roots=(root,), manifest=manifest)

    assert str(captured.value) == "Bundled STT model is incomplete or corrupt"
    assert str(candidate) not in str(captured.value)
    assert "private-probe-root" not in str(captured.value)


def test_default_stt_roots_are_frozen_then_source_and_never_working_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("interview_assistant.stt.bundle")
    frozen_root = tmp_path / "frozen-root"
    working_directory = tmp_path / "unrelated-working-directory"
    working_directory.mkdir()
    monkeypatch.setattr(module.sys, "_MEIPASS", str(frozen_root), raising=False)
    monkeypatch.chdir(working_directory)

    default_roots = getattr(module, "default_stt_roots", None)
    assert callable(default_roots)
    roots = default_roots()

    assert module.__file__ is not None
    assert roots == (frozen_root, Path(module.__file__).resolve().parents[2])
    assert working_directory not in roots


def test_stt_bundle_cli_requires_all_release_files_and_redacts_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = importlib.import_module("interview_assistant.stt.bundle")
    payload = _test_manifest_payload()
    files = payload["files"]
    assert isinstance(files, list)
    files.append(
        {
            "path": "README.md",
            "size": 6,
            "sha256": "59d35c5d5308cd7c38f2d2b1552615f09e40b0aa7069d6a4380f16e72b782b8f",
            "runtime_required": False,
        }
    )
    manifest_path = tmp_path / "private-manifest" / "manifest.json"
    manifest_path.parent.mkdir()
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    bundle_directory = tmp_path / "private-release-model"
    bundle_directory.mkdir()
    (bundle_directory / "model.bin").write_bytes(b"test-model")

    main = getattr(module, "main", None)
    assert callable(main)
    exit_code = main(
        [
            "--manifest",
            str(manifest_path),
            "--validate-bundle",
            str(bundle_directory),
            "--require-all-files",
        ]
    )

    output = capsys.readouterr()
    assert exit_code == 1
    assert output.out == ""
    assert output.err == "STT bundle validation failed\n"
    assert "private-manifest" not in output.err
    assert "private-release-model" not in output.err


def test_project_requires_whisper_version_with_multilingual_decode() -> None:
    pyproject = Path(__file__).parents[2] / "pyproject.toml"
    with pyproject.open("rb") as project_file:
        dependencies = tomllib.load(project_file)["project"]["dependencies"]

    assert "faster-whisper>=1.2.1,<2" in dependencies


class FakeWhisperModel:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def transcribe(
        self, audio: np.ndarray, **kwargs: object
    ) -> tuple[list[SimpleNamespace], SimpleNamespace]:
        assert audio.dtype == np.float32
        self.calls.append(kwargs)
        return (
            [SimpleNamespace(text=" design "), SimpleNamespace(text="a cache ")],
            SimpleNamespace(language="en", language_probability=0.93),
        )


def test_whisper_engine_loads_production_model_lazily_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = FakeWhisperModel()
    resolver_calls: list[str] = []
    factory_calls: list[tuple[str, str, str]] = []

    def resolver(model_name: str) -> str:
        resolver_calls.append(model_name)
        return model_name

    def factory(model_name: str, *, device: str, compute_type: str) -> FakeWhisperModel:
        factory_calls.append((model_name, device, compute_type))
        return model

    monkeypatch.setattr("interview_assistant.stt.engine.resolve_stt_model", resolver)
    engine = WhisperEngine(model_factory=factory)
    assert factory_calls == []

    first = engine.transcribe(
        np.zeros(1_600, dtype=np.float32),
        beam_size=1,
        condition_on_previous_text=False,
    )
    second = engine.transcribe(
        np.zeros(1_600, dtype=np.float32),
        beam_size=3,
        condition_on_previous_text=False,
    )

    assert resolver_calls == ["large-v3-turbo"]
    assert factory_calls == [("large-v3-turbo", "cuda", "float16")]
    assert first == second == TranscriptionResult("design a cache", "en", 0.93)
    assert model.calls == [
        {
            "beam_size": 1,
            "condition_on_previous_text": False,
            "multilingual": True,
            "temperature": 0.0,
        },
        {
            "beam_size": 3,
            "condition_on_previous_text": False,
            "multilingual": True,
        },
    ]


def test_whisper_engine_loads_resolved_local_bundle_offline_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = FakeWhisperModel()
    bundle_directory = tmp_path / "models" / "stt" / "large-v3-turbo"
    bundle_directory.mkdir(parents=True)
    resolver_calls: list[str] = []
    factory_calls: list[tuple[str, str, str, bool]] = []

    def resolver(model_name: str) -> str:
        resolver_calls.append(model_name)
        return str(bundle_directory)

    def factory(
        model_name: str,
        *,
        device: str,
        compute_type: str,
        local_files_only: bool,
    ) -> FakeWhisperModel:
        factory_calls.append((model_name, device, compute_type, local_files_only))
        return model

    monkeypatch.setattr("interview_assistant.stt.engine.resolve_stt_model", resolver)
    engine = WhisperEngine(model_factory=factory)

    engine.transcribe(
        np.zeros(1_600, dtype=np.float32),
        beam_size=1,
        condition_on_previous_text=False,
    )
    engine.transcribe(
        np.zeros(1_600, dtype=np.float32),
        beam_size=2,
        condition_on_previous_text=False,
    )

    assert resolver_calls == ["large-v3-turbo"]
    assert factory_calls == [
        (str(bundle_directory), "cuda", "float16", True),
    ]
