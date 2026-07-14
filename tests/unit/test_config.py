from pathlib import Path

import pytest
from pydantic import ValidationError

from interview_assistant.config import AppConfig


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
