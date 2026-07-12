from pathlib import Path
from typing import Literal

import keyring
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class AppConfig(BaseModel):
    audio: AudioConfig = AudioConfig()
    lmstudio: LMStudioConfig = LMStudioConfig()
    capture: CaptureConfig = CaptureConfig()
    search: SearchConfig = SearchConfig()
    overlay: OverlayConfig = OverlayConfig()

    @model_validator(mode="before")
    @classmethod
    def reject_plaintext_secrets(cls, value: object) -> object:
        if isinstance(value, dict) and "api_token" in value.get("lmstudio", {}):
            raise ValueError("lmstudio.api_token must be stored in Windows Credential Manager")
        return value

    @classmethod
    def load(cls, path: Path) -> "AppConfig":
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls.model_validate(raw)


class SecretStore:
    SERVICE = "InterviewAssistant"

    def get_lm_token(self) -> str | None:
        return keyring.get_password(self.SERVICE, "lmstudio_api_token")

    def set_lm_token(self, value: str) -> None:
        keyring.set_password(self.SERVICE, "lmstudio_api_token", value)
