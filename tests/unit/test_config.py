from pathlib import Path

import pytest
from pydantic import ValidationError

from interview_assistant.config import AppConfig
from interview_assistant import config as config_module
from interview_assistant.utils.hotkeys import DEFAULT_HOTKEY_BINDINGS


def test_search_defaults_to_official_firecrawl_with_native_mcp() -> None:
    config = AppConfig()
    assert config.search.provider == "firecrawl"
    assert config.mcp.backend == "native"


@pytest.mark.parametrize("legacy_provider", ["exa", "duckduckgo", "searxng"])
def test_legacy_search_provider_migrates_and_persists_as_firecrawl(
    tmp_path: Path, legacy_provider: str,
) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        f"search:\n  provider: {legacy_provider}\n  mode: 'off'\n  timeout_seconds: 12\n",
        encoding="utf-8",
    )
    config = AppConfig.load(path)
    assert config.search.provider == "firecrawl"
    assert config.search.mode == "off"
    assert config.search.timeout_seconds == 12
    config.save(path)
    assert "provider: firecrawl" in path.read_text(encoding="utf-8")
    assert AppConfig.load(path).search == config.search


def test_unknown_search_provider_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AppConfig.model_validate({"search": {"provider": "unreviewed-search"}})


def test_provider_migration_preserves_custom_mcp_file_and_endpoint(tmp_path: Path) -> None:
    mcp_path = tmp_path / "custom-mcp.json"
    definition = '{"mcpServers":{"custom":{"type":"http","url":"https://custom.example/mcp"}}}'
    mcp_path.write_text(definition, encoding="utf-8")
    path = tmp_path / "config.yaml"
    path.write_text(
        "search:\n  provider: exa\nmcp:\n  config_path: custom-mcp.json\n",
        encoding="utf-8",
    )
    config = AppConfig.load(path, env_path=tmp_path / ".env")

    assert config.search.provider == "firecrawl"
    assert Path(config.mcp.config_path) == mcp_path
    assert mcp_path.read_text(encoding="utf-8") == definition


def test_config_without_hotkeys_uses_complete_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("audio: {}\n", encoding="utf-8")

    config = AppConfig.load(path)

    assert config.hotkeys.as_bindings() == dict(DEFAULT_HOTKEY_BINDINGS)


def test_config_rejects_duplicate_hotkeys() -> None:
    with pytest.raises(ValidationError, match="Duplicate hotkey"):
        AppConfig.model_validate(
            {
                "hotkeys": {
                    "force_request": "ctrl+shift+x",
                    "screenshot": "control+shift+x",
                }
            }
        )


def test_same_model_can_fill_both_roles(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        "lmstudio:\n  text_model: qwen3.5\n  vision_model: qwen3.5\n",
        encoding="utf-8",
    )
    config = AppConfig.load(path)
    assert config.lmstudio.unique_model_keys == {"qwen3.5"}


def test_secret_is_rejected_in_yaml(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("lmstudio:\n  api_token: plaintext\n", encoding="utf-8")
    try:
        AppConfig.load(path)
    except ValueError as exc:
        assert "api_token" in str(exc)
    else:
        raise AssertionError("plaintext token must be rejected")


def test_null_lmstudio_section_produces_validation_error_not_raw_type_error(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("lmstudio: null\n", encoding="utf-8")

    with pytest.raises(ValidationError):
        AppConfig.load(path)


def test_save_atomically_round_trips_without_plaintext_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("old: value\n", encoding="utf-8")
    config = AppConfig()
    config.audio.system_device_id = "loopback-1"
    config.lmstudio.text_model = "qwen-vl"
    replacements: list[tuple[Path, Path]] = []
    real_replace = config_module.os.replace

    def recording_replace(source: str | Path, destination: str | Path) -> None:
        replacements.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr(config_module.os, "replace", recording_replace)

    config.save(path)

    # This is a YAML round-trip test; the developer's adjacent application
    # .env and its private provenance must not affect the reloaded config.
    assert AppConfig.load(path, env_path=tmp_path / ".env") == config
    assert replacements and replacements[0][1] == path
    assert replacements[0][0].parent == path.parent
    assert not replacements[0][0].exists()
    persisted = path.read_text(encoding="utf-8")
    assert "api_token" not in persisted
