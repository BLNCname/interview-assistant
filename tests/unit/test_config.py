from pathlib import Path

import pytest
from pydantic import ValidationError

from interview_assistant.config import AppConfig
from interview_assistant import config as config_module


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

    assert AppConfig.load(path) == config
    assert replacements and replacements[0][1] == path
    assert replacements[0][0].parent == path.parent
    assert not replacements[0][0].exists()
    persisted = path.read_text(encoding="utf-8")
    assert "api_token" not in persisted
