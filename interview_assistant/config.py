import os
import sys
from collections.abc import Mapping
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal

import keyring
import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from interview_assistant.utils.hotkeys import (
    DEFAULT_HOTKEY_BINDINGS,
    HotkeyAction,
    normalize_bindings,
)


class AudioConfig(BaseModel):
    system_device_id: str | None = None
    microphone_device_id: str | None = None
    sample_rate: int = 16_000
    language: Literal["auto", "ru", "en"] = "auto"
    stt_model: str = "large-v3-turbo"
    device: Literal["cuda", "cpu"] = "cuda"
    compute_type: Literal["float16", "int8_float16", "int8", "float32"] = "float16"

    @model_validator(mode="after")
    def compatible_compute_type(self) -> "AudioConfig":
        if self.device == "cpu" and self.compute_type in {"float16", "int8_float16"}:
            raise ValueError("CPU STT requires int8 or float32 compute_type")
        return self


class OpenRouterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text_model: str = ""
    vision_model: str = ""
    max_tokens: int = Field(default=1200, ge=32, le=16384)
    temperature: float = Field(default=0.55, ge=0, le=2)
    timeout_seconds: float = Field(default=120, gt=0, le=300)


class MCPConfig(BaseModel):
    backend: Literal["native", "lmstudio", "off"] = "native"
    config_path: str | None = None


class LMStudioConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str = "127.0.0.1"
    port: int = 1234
    text_model: str = ""
    vision_model: str = ""
    preferred_device_name: str = ""

    @property
    def unique_model_keys(self) -> set[str]:
        return {key for key in (self.text_model, self.vision_model) if key}


class CaptureConfig(BaseModel):
    mode: Literal["event"] = "event"
    persistent_screenshots: bool = False
    black_frame_threshold: float = Field(default=0.92, ge=0.0, le=1.0)


class SearchConfig(BaseModel):
    mode: Literal["off", "auto", "forced"] = "auto"
    provider: Literal["firecrawl"] = "firecrawl"
    timeout_seconds: float = Field(default=5.0, gt=0, le=60)

    @field_validator("provider", mode="before")
    @classmethod
    def migrate_legacy_provider(cls, value: object) -> object:
        if isinstance(value, str) and value in {"exa", "duckduckgo", "searxng"}:
            return "firecrawl"
        return value


class OverlayConfig(BaseModel):
    opacity: float = Field(default=0.88, ge=0.2, le=1.0)
    max_height: int = Field(default=360, ge=120, le=900)


class HotkeysConfig(BaseModel):
    force_request: str = DEFAULT_HOTKEY_BINDINGS[HotkeyAction.FORCE_REQUEST]
    screenshot: str = DEFAULT_HOTKEY_BINDINGS[HotkeyAction.SCREENSHOT]
    pause: str = DEFAULT_HOTKEY_BINDINGS[HotkeyAction.PAUSE]
    overlay_visibility: str = DEFAULT_HOTKEY_BINDINGS[
        HotkeyAction.OVERLAY_VISIBILITY
    ]
    overlay_interaction: str = DEFAULT_HOTKEY_BINDINGS[
        HotkeyAction.OVERLAY_INTERACTION
    ]
    forced_web_search: str = DEFAULT_HOTKEY_BINDINGS[HotkeyAction.FORCED_WEB_SEARCH]
    clear_answer: str = DEFAULT_HOTKEY_BINDINGS[HotkeyAction.CLEAR_ANSWER]

    def _raw_bindings(self) -> dict[HotkeyAction | str, str]:
        return {action: getattr(self, action.value) for action in HotkeyAction}

    @classmethod
    def from_bindings(
        cls,
        bindings: Mapping[HotkeyAction | str, str],
    ) -> "HotkeysConfig":
        normalized = normalize_bindings(bindings)
        return cls.model_validate(
            {
                action.value: chord.to_portable_text()
                for action, chord in normalized.items()
            }
        )

    @model_validator(mode="after")
    def validate_bindings(self) -> "HotkeysConfig":
        normalize_bindings(self._raw_bindings())
        return self

    def as_bindings(self) -> dict[HotkeyAction, str]:
        bindings = normalize_bindings(self._raw_bindings())
        return {
            action: chord.to_portable_text() for action, chord in bindings.items()
        }


class AppConfig(BaseModel):
    provider: Literal["lmstudio", "openrouter"] = "lmstudio"
    audio: AudioConfig = AudioConfig()
    lmstudio: LMStudioConfig = LMStudioConfig()
    openrouter: OpenRouterConfig = OpenRouterConfig()
    mcp: MCPConfig = MCPConfig()
    capture: CaptureConfig = CaptureConfig()
    search: SearchConfig = SearchConfig()
    overlay: OverlayConfig = OverlayConfig()
    hotkeys: HotkeysConfig = HotkeysConfig()
    _env_path: Path | None = PrivateAttr(default=None)

    @property
    def text_model(self) -> str:
        return getattr(self, self.provider).text_model

    @property
    def vision_model(self) -> str:
        return getattr(self, self.provider).vision_model

    @property
    def env_path(self) -> Path | None:
        return self._env_path

    @model_validator(mode="before")
    @classmethod
    def reject_plaintext_secrets(cls, value: object) -> object:
        if isinstance(value, Mapping):
            lmstudio = value.get("lmstudio", {})
            if isinstance(lmstudio, Mapping) and "api_token" in lmstudio:
                raise ValueError(
                    "lmstudio.api_token must be stored in Windows Credential Manager"
                )
        return value

    @classmethod
    def load(cls, path: Path, *, env_path: Path | None = None) -> "AppConfig":
        raw = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}
        # Validate YAML before applying environment values: never mask an invalid file.
        config = cls.model_validate(raw)
        selected_env = env_path or resolve_environment_path(path)
        values = environment_values(selected_env)
        data = config.model_dump()
        for name, location in _ENV_CONFIG_FIELDS.items():
            if name not in values:
                continue
            section, field = location
            if section is None:
                data[field] = values[name]
            else:
                data[section][field] = values[name]
        result = cls.model_validate(data)
        if result.mcp.config_path:
            mcp_path = Path(result.mcp.config_path).expanduser()
            if not mcp_path.is_absolute():
                mcp_path = selected_env.parent / mcp_path
            result.mcp.config_path = str(mcp_path.resolve())
        result._env_path = selected_env if selected_env.is_file() else None
        return result

    def save(self, path: Path) -> None:
        """Atomically persist non-secret configuration beside the destination."""

        path.parent.mkdir(parents=True, exist_ok=True)
        serialized = yaml.safe_dump(
            self.model_dump(mode="json"),
            allow_unicode=True,
            sort_keys=False,
        )
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                "w",
                encoding="utf-8",
                newline="\n",
                prefix=f".{path.name}.",
                suffix=".tmp",
                dir=path.parent,
                delete=False,
            ) as temporary:
                temporary.write(serialized)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)


class SecretStore:
    """User-saved credentials take precedence; dotenv/process values are fallback."""

    SERVICE = "InterviewAssistant"

    def __init__(self, *, env_path: Path | None = None) -> None:
        self._env_path = env_path

    def get_lm_token(self) -> str | None:
        return self._get_token("lmstudio_api_token", "LMSTUDIO_API_TOKEN")

    def get_openrouter_token(self) -> str | None:
        return self._get_token("openrouter_api_key", "OPENROUTER_API_KEY")

    def get_firecrawl_token(self) -> str | None:
        return self._get_token("firecrawl_api_key", "FIRECRAWL_API_KEY")

    def get_context7_token(self) -> str | None:
        return self._get_token("context7_api_key", "CONTEXT7_API_KEY")

    def _get_token(self, name: str, environment_name: str) -> str | None:
        fallback = environment_values(self._env_path).get(environment_name, "").strip() or None
        try:
            stored = keyring.get_password(self.SERVICE, name)
        except Exception:
            # Existing env-only setups also work when the OS store is unavailable.
            if fallback is not None:
                return fallback
            raise
        return (stored.strip() if stored else "") or fallback

    def _set_token(self, name: str, value: str) -> None:
        value = value.strip()
        if not value:
            raise ValueError("A stored API key must not be empty")
        keyring.set_password(self.SERVICE, name, value)

    def _delete_token(self, name: str) -> None:
        # Deleting only the stored value leaves external .env/process keys intact.
        if keyring.get_password(self.SERVICE, name) is not None:
            keyring.delete_password(self.SERVICE, name)

    def has_openrouter_token(self) -> bool:
        return bool(self.get_openrouter_token())

    def set_openrouter_token(self, value: str) -> None:
        self._set_token("openrouter_api_key", value)

    def has_lm_token(self) -> bool:
        return bool(self.get_lm_token())

    def set_lm_token(self, value: str) -> None:
        self._set_token("lmstudio_api_token", value)

    def has_firecrawl_token(self) -> bool:
        return bool(self.get_firecrawl_token())

    def has_context7_token(self) -> bool:
        return bool(self.get_context7_token())

    def set_firecrawl_token(self, value: str) -> None:
        self._set_token("firecrawl_api_key", value)

    def set_context7_token(self, value: str) -> None:
        self._set_token("context7_api_key", value)

    def delete_lm_token(self) -> None:
        self._delete_token("lmstudio_api_token")

    def delete_openrouter_token(self) -> None:
        self._delete_token("openrouter_api_key")

    def delete_firecrawl_token(self) -> None:
        self._delete_token("firecrawl_api_key")

    def delete_context7_token(self) -> None:
        self._delete_token("context7_api_key")


def resolve_environment_path(config_path: Path) -> Path:
    """Choose the same dotenv file for GUI, offline checks and provider secrets."""
    adjacent = config_path.expanduser().resolve().parent / ".env"
    if adjacent.is_file():
        return adjacent
    app_root = (
        Path(sys.executable).resolve().parent
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parents[1]
    )
    fallback = app_root / ".env"
    return fallback if fallback.is_file() else adjacent


def environment_values(path: Path | None = None) -> dict[str, str]:
    """Read one explicit dotenv file, without mutating process environment."""
    from dotenv import dotenv_values

    file_values = dotenv_values(path, interpolate=False) if path and path.is_file() else {}
    return {**{k: v for k, v in file_values.items() if v is not None}, **os.environ}


_ENV_CONFIG_FIELDS = {
    "LLM_PROVIDER": (None, "provider"),
    "OPENROUTER_MODEL": ("openrouter", "text_model"),
    "OPENROUTER_VISION_MODEL": ("openrouter", "vision_model"),
    "OPENROUTER_MAX_TOKENS": ("openrouter", "max_tokens"),
    "OPENROUTER_TEMPERATURE": ("openrouter", "temperature"),
    "OPENROUTER_TIMEOUT_SECONDS": ("openrouter", "timeout_seconds"),
    "LMSTUDIO_HOST": ("lmstudio", "host"),
    "LMSTUDIO_PORT": ("lmstudio", "port"),
    "LMSTUDIO_MODEL": ("lmstudio", "text_model"),
    "LMSTUDIO_VISION_MODEL": ("lmstudio", "vision_model"),
    "STT_MODEL": ("audio", "stt_model"),
    "STT_DEVICE": ("audio", "device"),
    "STT_COMPUTE_TYPE": ("audio", "compute_type"),
    "STT_LANGUAGE": ("audio", "language"),
    "SYSTEM_AUDIO_DEVICE": ("audio", "system_device_id"),
    "MICROPHONE_DEVICE": ("audio", "microphone_device_id"),
    "MCP_BACKEND": ("mcp", "backend"),
    "MCP_CONFIG": ("mcp", "config_path"),
    "SEARCH_MODE": ("search", "mode"),
    "SEARCH_TIMEOUT_SECONDS": ("search", "timeout_seconds"),
}
