from pathlib import Path
from types import SimpleNamespace

import pytest

from interview_assistant.config import AppConfig, SecretStore, environment_values


@pytest.mark.parametrize("method, env_name, stored_name", [
    ("get_lm_token", "LMSTUDIO_API_TOKEN", "lmstudio_api_token"),
    ("get_openrouter_token", "OPENROUTER_API_KEY", "openrouter_api_key"),
])
def test_empty_template_key_uses_token_saved_in_settings(
    tmp_path, monkeypatch, method, env_name, stored_name,
):
    monkeypatch.delenv(env_name, raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text(f"{env_name}=\n", encoding="utf-8")
    calls = []

    def read_saved(service, name):
        calls.append((service, name))
        return "saved-test-token"

    monkeypatch.setattr("interview_assistant.config.keyring.get_password", read_saved)
    assert getattr(SecretStore(env_path=env_path), method)() == "saved-test-token"
    assert calls == [(SecretStore.SERVICE, stored_name)]


def test_env_overrides_yaml_without_serializing_key(tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr("interview_assistant.config.keyring.get_password", lambda *_args: None)
    path = tmp_path / "config.yaml"
    path.write_text("lmstudio:\n  text_model: local-model\n", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "LLM_PROVIDER=openrouter\nOPENROUTER_MODEL=vendor/model\n"
        "OPENROUTER_API_KEY=test-secret\nSTT_COMPUTE_TYPE=int8_float16\n",
        encoding="utf-8",
    )
    config = AppConfig.load(path)
    assert config.provider == "openrouter"
    assert config.text_model == "vendor/model"
    assert config.lmstudio.text_model == "local-model"
    assert config.audio.compute_type == "int8_float16"
    assert SecretStore(env_path=tmp_path / ".env").get_openrouter_token() == "test-secret"
    config.save(tmp_path / "saved.yaml")
    assert "test-secret" not in (tmp_path / "saved.yaml").read_text()
    assert "test-secret" not in repr(config)


def test_process_environment_wins_and_empty_vision_is_preserved(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("{}", encoding="utf-8")
    (tmp_path / ".env").write_text("OPENROUTER_MODEL=from-file\n", encoding="utf-8")
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_MODEL", "from-process")
    monkeypatch.setenv("OPENROUTER_VISION_MODEL", "")
    config = AppConfig.load(path)
    assert config.text_model == "from-process"
    assert config.vision_model == ""


def test_env_only_setup_and_relative_mcp_path(tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    (tmp_path / ".env").write_text(
        "LLM_PROVIDER=openrouter\nMCP_CONFIG=config/mcp.json\n", encoding="utf-8"
    )
    config = AppConfig.load(tmp_path / "missing.yaml")
    assert config.provider == "openrouter"
    assert Path(config.mcp.config_path) == tmp_path / "config" / "mcp.json"


def test_firecrawl_key_is_available_to_native_mcp_without_yaml_persistence(tmp_path, monkeypatch):
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text("FIRECRAWL_API_KEY=firecrawl-test-secret\n", encoding="utf-8")
    config = AppConfig.load(tmp_path / "config.yaml")

    assert config.search.provider == "firecrawl"
    assert environment_values(config.env_path)["FIRECRAWL_API_KEY"] == "firecrawl-test-secret"
    config.save(tmp_path / "saved.yaml")
    assert "firecrawl-test-secret" not in (tmp_path / "saved.yaml").read_text(encoding="utf-8")
    assert "firecrawl-test-secret" not in repr(config)


@pytest.mark.parametrize("values", [
    {"audio": {"device": "cpu", "compute_type": "float16"}},
    {"openrouter": {"api_key": "do-not-store"}},
    {"search": {"timeout_seconds": 0}},
])
def test_invalid_configuration_is_rejected(values):
    with pytest.raises(ValueError):
        AppConfig.model_validate(values)


def test_context_payload_separates_system_instructions_from_transcript():
    from interview_assistant.context.builder import ContextBuilder
    from interview_assistant.lmstudio.payload import build_context_payload
    from interview_assistant.transcript.detector import DetectedQuestion
    from interview_assistant.transcript.store import TranscriptStore

    question = DetectedQuestion(
        request_id=1, text="What is a transaction?", kind="theory", detected_at=1.0
    )
    context = ContextBuilder(TranscriptStore(), latest_question=question).normal()
    payload = build_context_payload("model", context)
    assert "system_prompt" in payload
    assert "What is a transaction?" not in payload["system_prompt"]
    assert "What is a transaction?" in payload["input"]
    assert "System:" not in payload["input"]


def test_frozen_executable_environment_drives_config_and_self_test(tmp_path, monkeypatch):
    from interview_assistant import config as config_module
    from interview_assistant.diagnostics.selftest import run_fast_self_test

    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("MCP_CONFIG", raising=False)
    app_dir = tmp_path / "portable"
    app_dir.mkdir()
    (app_dir / ".env").write_text(
        "LLM_PROVIDER=openrouter\nMCP_CONFIG=mcp.json\n", encoding="utf-8",
    )
    monkeypatch.setattr(config_module, "sys", SimpleNamespace(
        frozen=True, executable=str(app_dir / "InterviewAssistant.exe"),
    ), raising=False)
    path = tmp_path / "AppData" / "config.yaml"

    config = AppConfig.load(path)
    report = run_fast_self_test(config_path=path)

    assert config.provider == "openrouter"
    assert report["configuration"]["provider"] == "openrouter"
    assert config.env_path == app_dir / ".env"
    assert Path(config.mcp.config_path) == app_dir / "mcp.json"


def test_config_adjacent_environment_takes_precedence_over_executable(tmp_path, monkeypatch):
    from interview_assistant import config as config_module
    from interview_assistant.diagnostics.selftest import run_fast_self_test

    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    app_dir = tmp_path / "portable"
    app_dir.mkdir()
    (app_dir / ".env").write_text("LLM_PROVIDER=invalid-provider\n", encoding="utf-8")
    config_dir = tmp_path / "chosen"
    config_dir.mkdir()
    (config_dir / ".env").write_text("LLM_PROVIDER=openrouter\n", encoding="utf-8")
    monkeypatch.setattr(config_module, "sys", SimpleNamespace(
        frozen=True, executable=str(app_dir / "InterviewAssistant.exe"),
    ), raising=False)
    path = config_dir / "config.yaml"

    config = AppConfig.load(path)
    report = run_fast_self_test(config_path=path)

    assert config.env_path == config_dir / ".env"
    assert report["configuration"]["provider"] == "openrouter"


def test_source_root_environment_is_used_without_adjacent_file(tmp_path, monkeypatch):
    from interview_assistant import config as config_module
    from interview_assistant.diagnostics.selftest import run_fast_self_test

    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / ".env").write_text("LLM_PROVIDER=openrouter\n", encoding="utf-8")
    monkeypatch.setattr(config_module, "__file__", str(source_root / "interview_assistant" / "config.py"))
    monkeypatch.setattr(config_module, "sys", SimpleNamespace(frozen=False), raising=False)
    path = tmp_path / "AppData" / "config.yaml"

    config = AppConfig.load(path)
    report = run_fast_self_test(config_path=path)

    assert config.env_path == source_root / ".env"
    assert report["configuration"]["provider"] == "openrouter"


def test_invalid_executable_environment_fails_self_test(tmp_path, monkeypatch):
    from interview_assistant import config as config_module
    from interview_assistant.diagnostics.selftest import run_fast_self_test

    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    (tmp_path / ".env").write_text("LLM_PROVIDER=invalid-provider\n", encoding="utf-8")
    monkeypatch.setattr(config_module, "sys", SimpleNamespace(
        frozen=True, executable=str(tmp_path / "InterviewAssistant.exe"),
    ), raising=False)

    report = run_fast_self_test(config_path=tmp_path / "AppData" / "config.yaml")

    assert report["status"] == "error"
    assert report["configuration"] == {"status": "invalid"}
