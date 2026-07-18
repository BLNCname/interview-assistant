import os
from collections.abc import Mapping
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal

import keyring
import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, model_validator

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


class LMStudioConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str = "127.0.0.1"
    port: int = 1234
    text_model: str = ""
    vision_model: str = ""
    preferred_device_name: str = "Strix Halo"

    @property
    def unique_model_keys(self) -> set[str]:
        return {key for key in (self.text_model, self.vision_model) if key}


class CaptureConfig(BaseModel):
    mode: Literal["event"] = "event"
    persistent_screenshots: bool = False
    black_frame_threshold: float = Field(default=0.92, ge=0.0, le=1.0)


class SearchConfig(BaseModel):
    mode: Literal["off", "auto", "forced"] = "auto"
    provider: Literal["duckduckgo", "searxng"] = "duckduckgo"
    timeout_seconds: float = 5.0


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

    @model_validator(mode="after")
    def validate_bindings(self) -> "HotkeysConfig":
        normalize_bindings(
            {
                HotkeyAction.FORCE_REQUEST: self.force_request,
                HotkeyAction.SCREENSHOT: self.screenshot,
                HotkeyAction.PAUSE: self.pause,
                HotkeyAction.OVERLAY_VISIBILITY: self.overlay_visibility,
                HotkeyAction.OVERLAY_INTERACTION: self.overlay_interaction,
                HotkeyAction.FORCED_WEB_SEARCH: self.forced_web_search,
                HotkeyAction.CLEAR_ANSWER: self.clear_answer,
            }
        )
        return self

    def as_bindings(self) -> dict[HotkeyAction, str]:
        bindings = normalize_bindings(
            {
                HotkeyAction.FORCE_REQUEST: self.force_request,
                HotkeyAction.SCREENSHOT: self.screenshot,
                HotkeyAction.PAUSE: self.pause,
                HotkeyAction.OVERLAY_VISIBILITY: self.overlay_visibility,
                HotkeyAction.OVERLAY_INTERACTION: self.overlay_interaction,
                HotkeyAction.FORCED_WEB_SEARCH: self.forced_web_search,
                HotkeyAction.CLEAR_ANSWER: self.clear_answer,
            }
        )
        return {
            action: chord.to_portable_text() for action, chord in bindings.items()
        }


class AppConfig(BaseModel):
    audio: AudioConfig = AudioConfig()
    lmstudio: LMStudioConfig = LMStudioConfig()
    capture: CaptureConfig = CaptureConfig()
    search: SearchConfig = SearchConfig()
    overlay: OverlayConfig = OverlayConfig()
    hotkeys: HotkeysConfig = HotkeysConfig()

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
    def load(cls, path: Path) -> "AppConfig":
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls.model_validate(raw)

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
    SERVICE = "InterviewAssistant"

    def get_lm_token(self) -> str | None:
        return keyring.get_password(self.SERVICE, "lmstudio_api_token")

    def has_lm_token(self) -> bool:
        return self.get_lm_token() is not None

    def set_lm_token(self, value: str) -> None:
        keyring.set_password(self.SERVICE, "lmstudio_api_token", value)
