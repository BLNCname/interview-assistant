from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import wave
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest


ROOT = Path(__file__).parents[2]
SPEC_PATH = ROOT / "packaging" / "interview_assistant.spec"
STT_MANIFEST_PATH = ROOT / "packaging" / "stt_model_manifest.json"
BUILD_SCRIPT_PATH = ROOT / "scripts" / "build.ps1"
CUDA_SCRIPT_PATH = ROOT / "scripts" / "verify_cuda.py"
DIAGNOSTICS_MODULE_PATH = ROOT / "interview_assistant" / "diagnostics" / "cli.py"
FIXTURE_PATH = ROOT / "assets" / "diagnostics" / "stt-smoke.wav"
REQUIREMENTS_PATH = ROOT / "requirements.txt"
GITIGNORE_PATH = ROOT / ".gitignore"
UV_LOCK_PATH = ROOT / "uv.lock"


def test_stt_model_manifest_pins_exact_offline_bundle_metadata() -> None:
    manifest = json.loads(STT_MANIFEST_PATH.read_text(encoding="utf-8"))

    assert manifest == {
        "schema_version": 1,
        "name": "large-v3-turbo",
        "repository": "dropbox-dash/faster-whisper-large-v3-turbo",
        "revision": "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
        "license": "MIT",
        "bundle_subdirectory": "models/stt/large-v3-turbo",
        "files": [
            {
                "path": "config.json",
                "size": 2263,
                "sha256": "b0253ea6c0d3bea6b1e19e91a02acfd3b53f4467362efcb5a3e6b16c9b3a9b7e",
                "runtime_required": True,
            },
            {
                "path": "model.bin",
                "size": 1617884929,
                "sha256": "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da",
                "runtime_required": True,
            },
            {
                "path": "preprocessor_config.json",
                "size": 340,
                "sha256": "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711",
                "runtime_required": True,
            },
            {
                "path": "tokenizer.json",
                "size": 2710337,
                "sha256": "297b13372ac43916285644fb9687add3cc62ee2a1adb60da3dc25cc94c1871fd",
                "runtime_required": True,
            },
            {
                "path": "vocabulary.json",
                "size": 1068114,
                "sha256": "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1",
                "runtime_required": True,
            },
            {
                "path": "README.md",
                "size": 1445,
                "sha256": "b3068692728faed23580cce5cd569fc47ff76c690c032b2641ffd5554ea64d8f",
                "runtime_required": False,
            },
        ],
    }


def _load_cuda_module() -> ModuleType:
    assert CUDA_SCRIPT_PATH.is_file(), "Task 16 CUDA verifier has not been created"
    spec = importlib.util.spec_from_file_location("verify_cuda", CUDA_SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_diagnostics_module() -> ModuleType:
    assert DIAGNOSTICS_MODULE_PATH.is_file(), "Task 16 diagnostics module is missing"
    spec = importlib.util.spec_from_file_location(
        "interview_assistant.diagnostics.cli",
        DIAGNOSTICS_MODULE_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pyinstaller_spec_declares_reproducible_onedir_windowed_bundle() -> None:
    assert SPEC_PATH.is_file(), "Task 16 PyInstaller spec has not been created"
    source = SPEC_PATH.read_text(encoding="utf-8")

    assert "name=\"InterviewAssistant\"" in source
    assert "console=False" in source
    assert "COLLECT(" in source
    assert "upx=False" in source
    assert "version=" in source
    for package in ("PyQt6", "faster_whisper", "ctranslate2", "mss", "keyring"):
        assert package in source
    assert "stt-smoke.wav" in source
    assert "MODEL_WEIGHT_SUFFIXES" in source
    assert "copy_metadata" in source
    assert "ROOT = Path(SPECPATH).resolve().parent\n" in source


def test_build_script_runs_packaged_headless_diagnostics_and_optional_cuda() -> None:
    assert BUILD_SCRIPT_PATH.is_file(), "Task 16 build script has not been created"
    source = BUILD_SCRIPT_PATH.read_text(encoding="utf-8")

    assert "$ErrorActionPreference = \"Stop\"" in source
    assert "python -m PyInstaller" in source
    assert "InterviewAssistant.exe" in source
    assert "--diagnostics" in source
    assert "--no-gui" in source
    assert "--diagnostics-output" in source
    assert "verify_cuda.py" in source
    assert "SOURCE_DATE_EPOCH" in source
    assert "$IsWindows" not in source
    assert "Get-Command uv" in source
    assert "Get-Command uv -CommandType Application -ErrorAction SilentlyContinue | " in source
    assert "Select-Object -First 1" in source
    assert "uv is required" in source
    assert "& $uv.Source lock --check" in source
    assert "& $uv.Source sync --extra dev --frozen" in source
    lock_check_index = source.index("& $uv.Source lock --check")
    frozen_sync_index = source.index("& $uv.Source sync --extra dev --frozen")
    assert lock_check_index < frozen_sync_index
    assert frozen_sync_index < source.index(
        '$python = Join-Path $root ".venv\\Scripts\\python.exe"'
    )
    assert '"--config"' in source
    assert "$smokeConfig" in source
    assert '$diagnostics.config -ne "missing"' in source


def test_optional_stt_bundle_wiring_is_validated_scoped_and_weight_free_by_default() -> None:
    build_source = BUILD_SCRIPT_PATH.read_text(encoding="utf-8")
    spec_source = SPEC_PATH.read_text(encoding="utf-8")

    assert '[string]$SttModelPath = ""' in build_source
    assert '$sttModelEnvironmentName = "INTERVIEW_ASSISTANT_STT_MODEL_PATH"' in build_source
    assert "$previousSttModelEnvironment" in build_source
    assert "Resolve-Path -LiteralPath $SttModelPath" in build_source
    assert "-m interview_assistant.stt.bundle" in build_source
    assert "--validate-bundle $resolvedSttModelPath" in build_source
    assert "--require-all-files" in build_source
    strict_cuda_guard = (
        "if ($VerifyCuda -and [string]::IsNullOrWhiteSpace($SttModelPath))"
    )
    assert strict_cuda_guard in build_source
    assert 'throw "-VerifyCuda requires -SttModelPath' in build_source
    assert (
        '$packagedRuntimeRoot = Join-Path $distRoot "InterviewAssistant\\_internal"'
        in build_source
    )
    assert "--bundle-root $packagedRuntimeRoot" in build_source
    assert "Set-Item `" in build_source
    assert "Remove-Item `" in build_source
    assert build_source.count('-LiteralPath "Env:$sttModelEnvironmentName"') >= 4
    assert build_source.index("--validate-bundle $resolvedSttModelPath") < build_source.index(
        "-m PyInstaller"
    )
    assert build_source.index(strict_cuda_guard) < build_source.index("-m PyInstaller")
    assert build_source.count("finally {") >= 2

    assert 'STT_MODEL_ENVIRONMENT = "INTERVIEW_ASSISTANT_STT_MODEL_PATH"' in spec_source
    assert 'MANIFEST_PATH = ROOT / "packaging" / "stt_model_manifest.json"' in spec_source
    assert "def _optional_stt_bundle_datas():" in spec_source
    assert "if not source_value:" in spec_source
    assert "return []" in spec_source
    assert 'destination = manifest["bundle_subdirectory"]' in spec_source
    assert 'for entry in manifest["files"]:' in spec_source
    assert '(str(source_file), destination)' in spec_source
    assert "datas.extend(_optional_stt_bundle_datas())" in spec_source
    assert '(str(MANIFEST_PATH), "packaging")' in spec_source
    assert "rglob(" not in spec_source
    assert "copytree(" not in spec_source
    assert "MODEL_WEIGHT_SUFFIXES" in spec_source


def test_uv_lock_is_current_and_contains_the_dev_extra() -> None:
    assert UV_LOCK_PATH.is_file(), "uv.lock must be checked in for frozen installs"
    source = UV_LOCK_PATH.read_text(encoding="utf-8")
    for dependency in ("mypy", "pyinstaller", "pytest", "ruff"):
        assert f'name = "{dependency}"' in source

    result = subprocess.run(
        ["uv", "lock", "--check"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_requirements_file_delegates_to_canonical_pyproject_metadata() -> None:
    assert REQUIREMENTS_PATH.read_text(encoding="utf-8") == (
        "# Compatibility-only install path; this file is not a locked environment.\n"
        "# Reproducible installs use: uv sync --extra dev --frozen\n"
        "-e .\n"
    )


def test_readme_documents_the_frozen_uv_workflow_truthfully() -> None:
    source = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "uv sync --extra dev --frozen" in source
    assert "uv lock --check" in source
    assert "https://docs.astral.sh/uv/concepts/projects/sync/" in source
    assert "requirements.txt" in source
    assert "не lock-файл" in source
    assert "не обещает побайтово одинаковый EXE" in source
    assert 'pip install -e ".[dev]"' not in source


def test_packaging_spec_is_explicitly_unignored() -> None:
    source = GITIGNORE_PATH.read_text(encoding="utf-8").splitlines()

    assert "*.spec" in source
    assert "!packaging/interview_assistant.spec" in source


def test_readme_and_gitignore_cover_lightweight_and_offline_stt_builds() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    ignored = GITIGNORE_PATH.read_text(encoding="utf-8").splitlines()

    assert "models/" in ignored
    assert ".\\scripts\\build.ps1 -SttModelPath" in readme
    assert "Сборка без `-SttModelPath` остаётся лёгкой" in readme
    assert "не скачивает веса" in readme
    assert "Одна многоязычная модель `large-v3-turbo` обслуживает RU/EN" in readme
    assert "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf" in readme
    assert "`README.md` с model card" in readme
    assert "лицензии `MIT`" in readme


def test_bundled_stt_fixture_is_deterministic_two_second_pcm() -> None:
    assert FIXTURE_PATH.is_file(), "Task 16 STT fixture has not been created"
    with wave.open(str(FIXTURE_PATH), "rb") as fixture:
        assert fixture.getnchannels() == 1
        assert fixture.getsampwidth() == 2
        assert fixture.getframerate() == 16_000
        assert fixture.getnframes() == 32_000
    assert hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest() == (
        "a2863a4c7343b61bfaee89045f02c1d3beb00f73c949247d3d80e22787f8417c"
    )


def test_no_gui_diagnostics_atomically_write_redacted_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_diagnostics_module()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "audio:\n  stt_model: private-model-name\n"
        "lmstudio:\n  text_model: confidential-model-key\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "report.json"
    monkeypatch.setattr(
        module,
        "_dependency_version",
        lambda _module, _distribution: "test-version",
    )
    monkeypatch.setattr(module, "_cpu_compute_types", lambda: ("float32",))
    monkeypatch.setattr(module, "_cuda_device_count", lambda: 0)

    exit_code = module.run_no_gui_diagnostics(
        config_path=config_path,
        output_path=output_path,
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    serialized = json.dumps(payload)
    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["config"] == "ready"
    assert payload["cpu"] == {"status": "ready", "compute_types": ["float32"]}
    assert payload["cuda"] == {"status": "warning", "device_count": 0}
    assert "private-model-name" not in serialized
    assert "confidential-model-key" not in serialized
    assert str(config_path) not in serialized
    assert list(tmp_path.glob(".report.json.*.tmp")) == []


def test_no_gui_diagnostics_preserve_existing_report_when_atomic_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_diagnostics_module()
    output_path = tmp_path / "report.json"
    output_path.write_bytes(b"previous-report")
    monkeypatch.setattr(module, "_dependency_version", lambda *_args: "test")
    monkeypatch.setattr(module, "_cpu_compute_types", lambda: ("float32",))
    monkeypatch.setattr(module, "_cuda_device_count", lambda: 0)

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replacement failure"):
        module.run_no_gui_diagnostics(config_path=None, output_path=output_path)

    assert output_path.read_bytes() == b"previous-report"
    assert list(tmp_path.glob(".report.json.*.tmp")) == []


class _Segment:
    text = "fixture transcript must not be emitted"


class _Info:
    language = "en"
    language_probability = 0.75


class _Model:
    def __init__(self) -> None:
        self.transcribe_calls: list[tuple[np.ndarray, dict[str, object]]] = []

    def transcribe(
        self,
        audio: np.ndarray,
        **options: object,
    ) -> tuple[Iterator[_Segment], _Info]:
        self.transcribe_calls.append((audio, options))
        return iter((_Segment(),)), _Info()


def test_cuda_verifier_uses_configured_model_and_reports_timing_without_text(
    tmp_path: Path,
) -> None:
    module = _load_cuda_module()
    config_path = tmp_path / "config.yaml"
    config_path.write_text("audio:\n  stt_model: bilingual-test-model\n", encoding="utf-8")
    model = _Model()
    factory_calls: list[tuple[str, str, str]] = []

    def model_factory(name: str, *, device: str, compute_type: str) -> _Model:
        factory_calls.append((name, device, compute_type))
        return model

    ticks = iter((10.0, 11.25, 20.0, 20.5))

    report = module.run_verification(
        config_path,
        FIXTURE_PATH,
        model_factory=model_factory,
        clock=lambda: next(ticks),
    )

    assert factory_calls == [("bilingual-test-model", "cuda", "float16")]
    assert report == {
        "status": "ok",
        "device": "cuda",
        "model_source": "configured",
        "fixture_seconds": 2.0,
        "model_load_seconds": 1.25,
        "transcription_seconds": 0.5,
        "real_time_factor": 0.25,
        "language": "en",
        "language_probability": 0.75,
        "text_characters": 38,
    }
    assert "bilingual-test-model" not in json.dumps(report)
    assert len(model.transcribe_calls) == 1
    audio, options = model.transcribe_calls[0]
    assert audio.dtype == np.float32
    assert audio.shape == (32_000,)
    assert options == {
        "beam_size": 1,
        "condition_on_previous_text": False,
        "multilingual": True,
        "temperature": 0.0,
    }
    assert "fixture transcript" not in json.dumps(report)


def test_cuda_verifier_resolves_bundled_model_offline_without_identity_leaks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_cuda_module()
    configured_name = "private-configured-model"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f"audio:\n  stt_model: {configured_name}\n", encoding="utf-8")
    private_bundle = tmp_path / "private-release-root" / "models" / "stt" / "model"
    model = _Model()
    resolver_calls: list[str] = []
    factory_calls: list[tuple[str, str, str, bool]] = []

    def resolver(model_name: str) -> str:
        resolver_calls.append(model_name)
        return str(private_bundle)

    def model_factory(
        name: str,
        *,
        device: str,
        compute_type: str,
        local_files_only: bool,
    ) -> _Model:
        factory_calls.append((name, device, compute_type, local_files_only))
        return model

    monkeypatch.setattr(module, "resolve_stt_model", resolver)
    ticks = iter((10.0, 11.0, 20.0, 20.5))

    report = module.run_verification(
        config_path,
        FIXTURE_PATH,
        model_factory=model_factory,
        clock=lambda: next(ticks),
    )

    assert resolver_calls == [configured_name]
    assert factory_calls == [(str(private_bundle), "cuda", "float16", True)]
    assert report["model_source"] == "bundled"
    serialized = json.dumps(report)
    assert configured_name not in serialized
    assert str(private_bundle) not in serialized
    assert "private-release-root" not in serialized


def test_cuda_verifier_passes_packaged_runtime_root_to_shared_resolver(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_cuda_module()
    config_path = tmp_path / "config.yaml"
    config_path.write_text("audio:\n  stt_model: test-model\n", encoding="utf-8")
    packaged_runtime_root = tmp_path / "dist" / "InterviewAssistant" / "_internal"
    resolver_calls: list[tuple[str, tuple[Path, ...]]] = []

    def resolver(model_name: str, *, roots: tuple[Path, ...]) -> str:
        resolver_calls.append((model_name, roots))
        return str(packaged_runtime_root / "models" / "stt" / model_name)

    monkeypatch.setattr(module, "resolve_stt_model", resolver)
    ticks = iter((10.0, 11.0, 20.0, 20.5))

    module.run_verification(
        config_path,
        FIXTURE_PATH,
        model_factory=lambda *_args, **_kwargs: _Model(),
        clock=lambda: next(ticks),
        bundle_roots=(packaged_runtime_root,),
    )

    assert resolver_calls == [("test-model", (packaged_runtime_root,))]


def test_cuda_verifier_bundle_root_fails_before_alias_factory_without_leaks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_cuda_module()
    configured_name = "private-hub-model"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f"audio:\n  stt_model: {configured_name}\n", encoding="utf-8")
    bundle_root = tmp_path / "private-release-root"
    factory_calls: list[str] = []

    def resolver(model_name: str, *, roots: tuple[Path, ...]) -> str:
        assert roots == (bundle_root.resolve(),)
        return model_name

    def model_factory(name: str, **_kwargs: object) -> _Model:
        factory_calls.append(name)
        return _Model()

    monkeypatch.setattr(module, "resolve_stt_model", resolver)
    monkeypatch.setattr(module, "_create_model", model_factory)

    exit_code = module.main(
        [
            "--config",
            str(config_path),
            "--fixture",
            str(FIXTURE_PATH),
            "--bundle-root",
            str(bundle_root),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload == {
        "status": "error",
        "device": "cuda",
        "error_type": "RuntimeError",
        "message": "CUDA STT verification failed",
    }
    serialized = json.dumps(payload)
    assert configured_name not in serialized
    assert str(bundle_root) not in serialized
    assert factory_calls == []


def test_cuda_verifier_cli_returns_nonzero_and_sanitized_json_when_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _load_cuda_module()
    config_path = tmp_path / "config.yaml"
    config_path.write_text("audio:\n  stt_model: unavailable-model\n", encoding="utf-8")

    def unavailable(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("secret local cache path and driver details")

    monkeypatch.setattr(module, "_create_model", unavailable)

    exit_code = module.main(
        ["--config", str(config_path), "--fixture", str(FIXTURE_PATH)]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code != 0
    assert payload == {
        "status": "error",
        "device": "cuda",
        "error_type": "RuntimeError",
        "message": "CUDA STT verification failed",
    }
    assert "secret" not in json.dumps(payload)


@pytest.mark.parametrize("document", ["README.md", "OPTIMIZATIONS.md", "PROJECT_ANALYSIS_REPORT.md"])
def test_primary_documents_do_not_claim_stealth_or_capture_bypass(document: str) -> None:
    text = (ROOT / document).read_text(encoding="utf-8").casefold()
    forbidden = (
        "скрыто от демонстрации экрана и диспетчера задач",
        "suppression of screenshot notifications",
        "обход защищенного контента",
        "невидимый overlay",
    )
    assert not any(claim in text for claim in forbidden)


def test_readme_links_observed_acceptance_without_treating_self_test_as_evidence() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "scripts/teams_acceptance.md" in text
    assert "docs/validation/acceptance-template.md" in text
    assert "benchmark_session.py --mode self-test" in text
    assert "benchmark_session.py --mode event-input" in text
    assert "overall_acceptance: NOT_RUN" in text
