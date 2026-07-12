# Interview Assistant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Превратить текущий pre-alpha прототип в проверяемое Windows-приложение с dual-source streaming STT, LM Studio/LM Link inference, Liquid Ribbon overlay, event-driven screenshots и ограниченным MCP-поиском.

**Architecture:** Вся логика приложения выполняется одним Python/Qt-приложением на основном ПК с RTX 5070 Ti. Оно обращается к локальному LM Studio API, который через LM Link направляет только LLM/VLM inference на Strix Halo. Работа разбита на шесть вертикальных этапов; каждый этап завершается тестируемым состоянием и review checkpoint.

**Tech Stack:** Python 3.11, PyQt6, qasync, PyAudioWPatch/WASAPI, faster-whisper/CTranslate2 CUDA, Pydantic 2, httpx/SSE, MSS/Pillow/imagehash, pynput, pytest/pytest-qt/respx, PyInstaller, LM Studio REST API v1, Context7 MCP, DuckDuckGo MCP.

## Global Constraints

- Target OS: Windows 11 x64.
- Target conferencing app: Microsoft Teams Desktop with full-screen sharing.
- Main PC owns all application logic; Strix Halo owns only LLM/VLM inference.
- Process name remains `InterviewAssistant.exe`; no Task Manager masquerading.
- `WDA_EXCLUDEFROMCAPTURE` is best-effort and must expose failure through readiness.
- No bypass of protected capture, DLP notifications, or third-party screenshot controls.
- GUI changes occur only in the Qt main thread.
- Text and vision roles may share one model key and must then share one loaded instance.
- No automatic LLM fallback to the RTX machine.
- Web tools are read-only, allowlisted, bounded, and receive no full transcript or screenshots.
- No secret is stored in YAML, source control, logs, or command-line arguments.
- Implementation follows TDD: failing test, minimal implementation, passing test, commit.

---

## Planned File Structure

```text
interview_assistant/
├── __init__.py
├── app.py
├── config.py
├── events.py
├── state.py
├── audio/
│   ├── __init__.py
│   ├── devices.py
│   ├── models.py
│   └── worker.py
├── stt/
│   ├── __init__.py
│   ├── engine.py
│   ├── stable_prefix.py
│   └── worker.py
├── transcript/
│   ├── __init__.py
│   ├── detector.py
│   └── store.py
├── context/
│   ├── __init__.py
│   ├── builder.py
│   └── models.py
├── lmstudio/
│   ├── __init__.py
│   ├── client.py
│   ├── lifecycle.py
│   ├── models.py
│   └── registry.py
├── orchestration/
│   ├── __init__.py
│   └── coordinator.py
├── capture/
│   ├── __init__.py
│   ├── backend.py
│   ├── frame_validator.py
│   └── worker.py
├── retrieval/
│   ├── __init__.py
│   ├── policy.py
│   └── models.py
├── ui/
│   ├── __init__.py
│   ├── overlay.py
│   ├── settings.py
│   └── windows_affinity.py
└── diagnostics/
    ├── __init__.py
    └── readiness.py
tests/
├── contract/
├── fixtures/
├── integration/
└── unit/
scripts/
├── configure_mcp.py
├── verify_cuda.py
└── teams_acceptance.md
```

Существующие `src/*.py`, `main_optimized.py` и `utils/hotkeys.py` остаются до завершения вертикальной миграции. После Task 15 их рабочая логика удаляется, а `main.py` остаётся тонкой точкой входа.

---

# Phase 1 — Core Foundation

### Task 1: Project Metadata, Test Harness, and Importable Package

**Files:**
- Create: `pyproject.toml`
- Create: `interview_assistant/__init__.py`
- Create: `tests/unit/test_package.py`
- Create: `.gitignore`

**Interfaces:**
- Produces: importable package `interview_assistant`, pytest configuration, dependency groups.

- [ ] **Step 1: Initialize Git before any implementation commit**

Run:

```powershell
git init
git branch -M main
```

Expected: `.git` exists and `git branch --show-current` prints `main`.

- [ ] **Step 2: Write the failing package smoke test**

```python
# tests/unit/test_package.py
def test_package_exposes_version() -> None:
    import interview_assistant

    assert interview_assistant.__version__ == "0.1.0"
```

- [ ] **Step 3: Run the test and verify failure**

Run: `python -m pytest tests/unit/test_package.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'interview_assistant'`.

- [ ] **Step 4: Add package metadata and dependencies**

```toml
# pyproject.toml
[build-system]
requires = ["setuptools>=75", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "interview-assistant"
version = "0.1.0"
requires-python = ">=3.11,<3.13"
dependencies = [
  "PyQt6>=6.7,<7",
  "qasync>=0.27,<1",
  "pydantic>=2.8,<3",
  "PyYAML>=6.0,<7",
  "platformdirs>=4.2,<5",
  "keyring>=25,<26",
  "httpx>=0.27,<1",
  "numpy>=1.26,<3",
  "PyAudioWPatch>=0.2.12.7,<0.3",
  "faster-whisper>=1.1,<2",
  "mss>=9,<11",
  "Pillow>=10,<12",
  "ImageHash>=4.3,<5",
  "pynput>=1.7,<2",
  "pywin32>=308",
]

[project.optional-dependencies]
dev = [
  "pytest>=8,<9",
  "pytest-asyncio>=0.24,<1",
  "pytest-qt>=4.4,<5",
  "pytest-cov>=5,<7",
  "respx>=0.21,<1",
  "ruff>=0.6,<1",
  "mypy>=1.11,<2",
  "pyinstaller>=6.10,<7",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"

[tool.ruff]
line-length = 100
target-version = "py311"
```

```python
# interview_assistant/__init__.py
__version__ = "0.1.0"
```

```gitignore
# .gitignore additions
.venv/
__pycache__/
.pytest_cache/
.ruff_cache/
.mypy_cache/
.coverage
htmlcov/
build/
dist/
*.spec
screenshots/
logs/
.superpowers/
```

- [ ] **Step 5: Install and verify**

Run:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest tests/unit/test_package.py -v
```

Expected: `1 passed`.

- [ ] **Step 6: Commit**

```powershell
git add pyproject.toml .gitignore interview_assistant tests/unit/test_package.py
git commit -m "build: add package metadata and test harness"
```

---

### Task 2: Typed Configuration and Secret Storage

**Files:**
- Create: `interview_assistant/config.py`
- Create: `tests/unit/test_config.py`
- Modify: `config.yaml`

**Interfaces:**
- Produces: `AppConfig.load(path: Path) -> AppConfig`, `SecretStore.get_lm_token() -> str | None`.

- [ ] **Step 1: Write failing configuration tests**

```python
# tests/unit/test_config.py
from pathlib import Path
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
```

- [ ] **Step 2: Run and verify failure**

Run: `.venv\Scripts\python -m pytest tests/unit/test_config.py -v`

Expected: FAIL because `interview_assistant.config` does not exist.

- [ ] **Step 3: Implement the typed schema and keyring wrapper**

```python
# interview_assistant/config.py
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
```

- [ ] **Step 4: Replace `config.yaml` with schema-aligned non-secret values**

```yaml
audio:
  system_device_id: null
  microphone_device_id: null
  sample_rate: 16000
  language: auto
  stt_model: large-v3-turbo
lmstudio:
  host: 127.0.0.1
  port: 1234
  text_model: ""
  vision_model: ""
  preferred_device_name: "Strix Halo"
capture:
  mode: event
  persistent_screenshots: false
  black_frame_threshold: 0.92
search:
  mode: auto
  provider: duckduckgo
  timeout_seconds: 5.0
overlay:
  opacity: 0.88
  max_height: 360
```

- [ ] **Step 5: Run tests**

Run: `.venv\Scripts\python -m pytest tests/unit/test_config.py -v`

Expected: `2 passed`.

- [ ] **Step 6: Commit**

```powershell
git add config.yaml interview_assistant/config.py tests/unit/test_config.py
git commit -m "feat: add typed configuration and secret storage"
```

---

### Task 3: Application State, Qt Signal Bus, and Correct Lifecycle

**Files:**
- Create: `interview_assistant/state.py`
- Create: `interview_assistant/events.py`
- Create: `interview_assistant/app.py`
- Create: `tests/unit/test_lifecycle.py`
- Modify: `main.py`

**Interfaces:**
- Produces: `ApplicationState`, `EventBus`, `InterviewApplication.start()`, `InterviewApplication.shutdown()`.

- [ ] **Step 1: Write failing lifecycle tests**

```python
# tests/unit/test_lifecycle.py
from interview_assistant.state import ApplicationState, StateMachine


def test_valid_state_transition() -> None:
    machine = StateMachine()
    machine.transition(ApplicationState.READY)
    machine.transition(ApplicationState.LISTENING)
    assert machine.state is ApplicationState.LISTENING


def test_shutdown_is_idempotent(qapp) -> None:
    from interview_assistant.app import InterviewApplication

    app = InterviewApplication.for_test()
    app.shutdown()
    app.shutdown()
    assert app.is_shutdown
```

- [ ] **Step 2: Run and verify failure**

Run: `.venv\Scripts\python -m pytest tests/unit/test_lifecycle.py -v`

Expected: FAIL because state/app modules do not exist.

- [ ] **Step 3: Implement state and event contracts**

```python
# interview_assistant/state.py
from enum import StrEnum


class ApplicationState(StrEnum):
    STARTING = "starting"
    READY = "ready"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    SEARCHING = "searching"
    GENERATING = "generating"
    RECOVERING = "recovering"
    OFFLINE = "offline"
    PAUSED = "paused"
    STOPPED = "stopped"


class StateMachine:
    def __init__(self) -> None:
        self.state = ApplicationState.STARTING

    def transition(self, target: ApplicationState) -> None:
        if self.state is ApplicationState.STOPPED and target is not ApplicationState.STOPPED:
            raise RuntimeError("stopped application cannot transition")
        self.state = target
```

```python
# interview_assistant/events.py
from PyQt6.QtCore import QObject, pyqtSignal


class EventBus(QObject):
    state_changed = pyqtSignal(str)
    transcript_partial = pyqtSignal(str, str)
    transcript_final = pyqtSignal(str, str)
    answer_reset = pyqtSignal(int)
    answer_delta = pyqtSignal(int, str)
    notification = pyqtSignal(str)
```

- [ ] **Step 4: Implement idempotent application lifecycle and create QApplication first**

```python
# interview_assistant/app.py
from dataclasses import dataclass

from PyQt6.QtWidgets import QApplication

from .events import EventBus
from .state import ApplicationState, StateMachine


@dataclass
class InterviewApplication:
    qt_app: QApplication
    events: EventBus
    states: StateMachine
    is_shutdown: bool = False

    @classmethod
    def for_test(cls) -> "InterviewApplication":
        qt = QApplication.instance() or QApplication([])
        return cls(qt, EventBus(), StateMachine())

    def start(self) -> None:
        self.states.transition(ApplicationState.READY)
        self.events.state_changed.emit(self.states.state.value)

    def shutdown(self) -> None:
        if self.is_shutdown:
            return
        self.states.transition(ApplicationState.STOPPED)
        self.is_shutdown = True
```

```python
# main.py
import sys
from PyQt6.QtWidgets import QApplication
from interview_assistant.app import InterviewApplication
from interview_assistant.events import EventBus
from interview_assistant.state import StateMachine


def main() -> int:
    qt_app = QApplication(sys.argv)
    app = InterviewApplication(qt_app, EventBus(), StateMachine())
    try:
        app.start()
        return qt_app.exec()
    finally:
        app.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run tests and compile**

Run:

```powershell
.venv\Scripts\python -m pytest tests/unit/test_lifecycle.py -v
.venv\Scripts\python -m compileall -q interview_assistant main.py
```

Expected: `2 passed`; compile command exits 0.

- [ ] **Step 6: Commit**

```powershell
git add main.py interview_assistant tests/unit/test_lifecycle.py
git commit -m "refactor: add safe Qt application lifecycle"
```

---

# Phase 2 — Dual-Source Streaming STT

### Task 4: Audio Device Discovery and Independent Source Frames

**Files:**
- Create: `interview_assistant/audio/__init__.py`
- Create: `interview_assistant/audio/models.py`
- Create: `interview_assistant/audio/devices.py`
- Create: `interview_assistant/audio/worker.py`
- Create: `tests/unit/test_audio_sources.py`

**Interfaces:**
- Produces: `AudioFrame`, `AudioSource`, `AudioDeviceService.list_devices()`, `AudioWorker`.

- [ ] **Step 1: Write failing source-separation test**

```python
# tests/unit/test_audio_sources.py
import numpy as np
from interview_assistant.audio.models import AudioFrame, AudioSource


def test_audio_frames_keep_source_identity() -> None:
    system = AudioFrame(AudioSource.SYSTEM, 1.0, np.zeros(160, dtype=np.float32))
    mic = AudioFrame(AudioSource.MICROPHONE, 1.1, np.ones(160, dtype=np.float32))
    assert system.source is AudioSource.SYSTEM
    assert mic.source is AudioSource.MICROPHONE
    assert not np.array_equal(system.samples, mic.samples)
```

- [ ] **Step 2: Verify failure**

Run: `.venv\Scripts\python -m pytest tests/unit/test_audio_sources.py -v`

Expected: FAIL because audio package does not exist.

- [ ] **Step 3: Implement immutable audio contracts**

```python
# interview_assistant/audio/models.py
from dataclasses import dataclass
from enum import StrEnum
import numpy as np
from numpy.typing import NDArray


class AudioSource(StrEnum):
    SYSTEM = "interviewer"
    MICROPHONE = "you"


@dataclass(frozen=True, slots=True)
class AudioFrame:
    source: AudioSource
    timestamp: float
    samples: NDArray[np.float32]
    sample_rate: int = 16_000
```

- [ ] **Step 4: Implement WASAPI discovery and worker boundary**

```python
# interview_assistant/audio/devices.py
from dataclasses import dataclass
import pyaudiowpatch as pyaudio


@dataclass(frozen=True)
class AudioDevice:
    id: str
    name: str
    is_loopback: bool
    max_input_channels: int


class AudioDeviceService:
    def list_devices(self) -> list[AudioDevice]:
        with pyaudio.PyAudio() as pa:
            result = []
            for info in pa.get_device_info_generator():
                if int(info.get("maxInputChannels", 0)) > 0:
                    result.append(AudioDevice(
                        id=str(info["index"]),
                        name=str(info["name"]),
                        is_loopback=bool(info.get("isLoopbackDevice", False)),
                        max_input_channels=int(info["maxInputChannels"]),
                    ))
            return result
```

`AudioWorker` must open two separate streams, convert Int16 to float32, downmix, resample to 16 kHz, and publish bounded `AudioFrame` queues. It must never mix source buffers and must expose `start()`, `pause()`, `resume()`, and idempotent `stop()`.

- [ ] **Step 5: Run tests**

Run: `.venv\Scripts\python -m pytest tests/unit/test_audio_sources.py -v`

Expected: `1 passed`.

- [ ] **Step 6: Commit**

```powershell
git add interview_assistant/audio tests/unit/test_audio_sources.py
git commit -m "feat: add dual-source WASAPI audio contracts"
```

---

### Task 5: Stable Prefix and Multilingual Streaming STT Worker

**Files:**
- Create: `interview_assistant/stt/__init__.py`
- Create: `interview_assistant/stt/stable_prefix.py`
- Create: `interview_assistant/stt/engine.py`
- Create: `interview_assistant/stt/worker.py`
- Create: `tests/unit/test_stable_prefix.py`
- Create: `tests/unit/test_language_hysteresis.py`

**Interfaces:**
- Consumes: `AudioFrame`.
- Produces: `TranscriptHypothesis`, `StablePrefix.update(text)`, `LanguageLatch.update()`.

- [ ] **Step 1: Write failing reconciliation tests**

```python
# tests/unit/test_stable_prefix.py
from interview_assistant.stt.stable_prefix import StablePrefix


def test_only_common_words_become_stable() -> None:
    prefix = StablePrefix()
    assert prefix.update("design a short") == ""
    assert prefix.update("design a short link") == "design a short"
    assert prefix.update("design a short link service") == "design a short link"
```

```python
# tests/unit/test_language_hysteresis.py
from interview_assistant.stt.engine import LanguageLatch


def test_low_confidence_does_not_flip_language() -> None:
    latch = LanguageLatch(threshold=0.80)
    assert latch.update("ru", 0.95) == "ru"
    assert latch.update("en", 0.55) == "ru"
    latch.reset_utterance()
    assert latch.update("en", 0.92) == "en"
```

- [ ] **Step 2: Verify failures**

Run: `.venv\Scripts\python -m pytest tests/unit/test_stable_prefix.py tests/unit/test_language_hysteresis.py -v`

Expected: FAIL because STT modules do not exist.

- [ ] **Step 3: Implement deterministic stable-prefix and language latch**

```python
# interview_assistant/stt/stable_prefix.py
class StablePrefix:
    def __init__(self) -> None:
        self._previous: list[str] = []

    def update(self, text: str) -> str:
        current = text.split()
        common = 0
        for left, right in zip(self._previous, current, strict=False):
            if left != right:
                break
            common += 1
        self._previous = current
        return " ".join(current[:common])

    def reset(self) -> None:
        self._previous.clear()
```

```python
# interview_assistant/stt/engine.py
from dataclasses import dataclass

from interview_assistant.audio.models import AudioSource


@dataclass(frozen=True, slots=True)
class TranscriptHypothesis:
    source: AudioSource
    text: str
    language: str
    is_final: bool
    started_at: float
    ended_at: float


class LanguageLatch:
    def __init__(self, threshold: float) -> None:
        self.threshold = threshold
        self.language: str | None = None

    def update(self, language: str, probability: float) -> str:
        if self.language is None or probability >= self.threshold:
            self.language = language
        return self.language

    def reset_utterance(self) -> None:
        self.language = None
```

- [ ] **Step 4: Implement the faster-whisper adapter and scheduler**

`WhisperEngine` loads `large-v3-turbo` once with `device="cuda"` and production `compute_type="float16"`. `StreamingSTTWorker` maintains one utterance buffer per `AudioSource`, uses VAD endpoints, runs partial greedy decoding, final beam decoding, and always dequeues system audio before microphone audio. All model calls remain outside the Qt thread.

The worker publishes:

- [ ] **Step 5: Run unit tests and a CPU fixture smoke**

Run:

```powershell
.venv\Scripts\python -m pytest tests/unit/test_stable_prefix.py tests/unit/test_language_hysteresis.py -v
.venv\Scripts\python -m pytest tests/integration/test_stt_fixture.py -v -m "not cuda"
```

Expected: unit tests pass; fixture produces one `interviewer` and one `you` final transcript.

- [ ] **Step 6: Commit**

```powershell
git add interview_assistant/stt tests/unit tests/integration/test_stt_fixture.py
git commit -m "feat: add multilingual streaming STT pipeline"
```

---

### Task 6: Transcript Store, Question Classification, and Context Builder

**Files:**
- Create: `interview_assistant/transcript/store.py`
- Create: `interview_assistant/transcript/detector.py`
- Create: `interview_assistant/context/models.py`
- Create: `interview_assistant/context/builder.py`
- Create: `tests/unit/test_detector.py`
- Create: `tests/unit/test_context_builder.py`

**Interfaces:**
- Produces: `DetectedQuestion`, `TranscriptStore`, `ContextBuilder.normal()`, `ContextBuilder.recovery()`.

- [ ] **Step 1: Write failing role and recovery tests**

```python
# tests/unit/test_detector.py
from interview_assistant.audio.models import AudioSource
from interview_assistant.transcript.detector import QuestionDetector


def test_microphone_never_auto_triggers() -> None:
    detector = QuestionDetector()
    assert detector.detect(AudioSource.MICROPHONE, "Как работает B-tree?") is None


def test_system_design_question_is_classified() -> None:
    detector = QuestionDetector()
    result = detector.detect(AudioSource.SYSTEM, "Спроектируйте сервис коротких ссылок")
    assert result is not None
    assert result.kind == "system_design"
```

```python
# tests/unit/test_context_builder.py
def test_recovery_context_drops_old_answers(context_builder) -> None:
    snapshot = context_builder.recovery(max_tokens=2000)
    assert snapshot.question == "latest question"
    assert "old assistant answer" not in snapshot.prompt
    assert snapshot.estimated_tokens <= 2000
```

- [ ] **Step 2: Verify failures**

Run: `.venv\Scripts\python -m pytest tests/unit/test_detector.py tests/unit/test_context_builder.py -v`

Expected: FAIL because modules do not exist.

- [ ] **Step 3: Implement typed detector output**

```python
@dataclass(frozen=True, slots=True)
class DetectedQuestion:
    request_id: int
    kind: Literal["theory", "coding", "system_design", "behavioral", "screen_analysis", "manual"]
    text: str
    detected_at: float
```

`QuestionDetector.detect()` must return `None` for microphone source, apply RU/EN patterns only to final system utterances, classify code/system-design/behavioral intent, normalize whitespace, and deduplicate repeated text during cooldown.

- [ ] **Step 4: Implement bounded transcript and context snapshots**

`TranscriptStore` stores timestamped final utterances with a fixed maximum age. `ContextBuilder` estimates tokens with a conservative `ceil(len(text) / 3)` heuristic, drops oldest low-priority entries first, and never drops the system prompt or latest question. Recovery output is capped at 2,000 estimated text tokens.

- [ ] **Step 5: Run tests**

Run: `.venv\Scripts\python -m pytest tests/unit/test_detector.py tests/unit/test_context_builder.py -v`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```powershell
git add interview_assistant/transcript interview_assistant/context tests/unit
git commit -m "feat: add question detection and bounded context"
```

---

# Phase 3 — LM Studio and LM Link

### Task 7: LM Studio REST Contracts and Model Discovery

**Files:**
- Create: `interview_assistant/lmstudio/models.py`
- Create: `interview_assistant/lmstudio/client.py`
- Create: `tests/contract/test_lmstudio_models.py`
- Create: `tests/fixtures/lmstudio_models.json`

**Interfaces:**
- Produces: `LMStudioClient.list_models()`, `list_model_details()`, `load_model()`, `stream_chat()`.

- [ ] **Step 1: Save a contract fixture and failing test**

```json
// tests/fixtures/lmstudio_models.json
{"object":"list","data":[{"id":"qwen3.5","object":"model","owned_by":"organization-owner"}]}
```

```python
# tests/contract/test_lmstudio_models.py
import httpx
import respx
from interview_assistant.lmstudio.client import LMStudioClient


@respx.mock
async def test_list_models_uses_openai_compatible_endpoint() -> None:
    route = respx.get("http://127.0.0.1:1234/v1/models").mock(
        return_value=httpx.Response(200, json={"object": "list", "data": [{"id": "qwen3.5"}]})
    )
    async with LMStudioClient("127.0.0.1", 1234, None) as client:
        models = await client.list_models()
    assert route.called
    assert [model.key for model in models] == ["qwen3.5"]
```

- [ ] **Step 2: Verify failure**

Run: `.venv\Scripts\python -m pytest tests/contract/test_lmstudio_models.py -v`

Expected: FAIL because client does not exist.

- [ ] **Step 3: Implement REST model types and authenticated client**

```python
# interview_assistant/lmstudio/models.py
from pydantic import BaseModel, Field


class ModelSummary(BaseModel):
    key: str = Field(alias="id")


class LoadResult(BaseModel):
    type: str
    instance_id: str
    load_time_seconds: float
    status: str


class ModelInstance(BaseModel):
    key: str
    instance_id: str
    state: str
    device_name: str | None = None
```

```python
# interview_assistant/lmstudio/client.py
import httpx
from .models import ModelSummary


class LMStudioClient:
    def __init__(self, host: str, port: int, token: str | None) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._http = httpx.AsyncClient(base_url=f"http://{host}:{port}", headers=headers, timeout=120)

    async def __aenter__(self) -> "LMStudioClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._http.aclose()

    async def list_models(self) -> list[ModelSummary]:
        response = await self._http.get("/v1/models", timeout=5)
        response.raise_for_status()
        return [ModelSummary.model_validate(item) for item in response.json().get("data", [])]
```

- [ ] **Step 4: Add native v1 model details and load methods**

`list_model_details()` calls `/api/v1/models`; `load_model()` calls `/api/v1/models/load` with `model`, `context_length`, `flash_attention`, `offload_kv_cache_to_gpu`, and `echo_load_config=true`. Parse responses into Pydantic contracts and preserve `instance_id`.

- [ ] **Step 5: Run contract tests**

Run: `.venv\Scripts\python -m pytest tests/contract/test_lmstudio_models.py -v`

Expected: all tests pass with no real network calls.

- [ ] **Step 6: Commit**

```powershell
git add interview_assistant/lmstudio tests/contract tests/fixtures
git commit -m "feat: add LM Studio REST contracts"
```

---

### Task 8: Model Registry, Shared Instances, and Recovery

**Files:**
- Create: `interview_assistant/lmstudio/registry.py`
- Create: `interview_assistant/lmstudio/lifecycle.py`
- Create: `tests/unit/test_model_registry.py`
- Create: `tests/unit/test_model_recovery.py`

**Interfaces:**
- Produces: `ModelRegistry.ensure_ready(key)`, `ModelLifecycle.recover(key, request)`.

- [ ] **Step 1: Write failing duplicate-load test**

```python
# tests/unit/test_model_registry.py
import asyncio


async def test_same_text_and_vision_key_load_once(fake_lm_client) -> None:
    from interview_assistant.lmstudio.registry import ModelRegistry

    registry = ModelRegistry(fake_lm_client)
    text, vision = await asyncio.gather(
        registry.ensure_ready("qwen3.5"),
        registry.ensure_ready("qwen3.5"),
    )
    assert text.instance_id == vision.instance_id
    assert fake_lm_client.load_calls == ["qwen3.5"]
```

- [ ] **Step 2: Write failing recovery-collapse test**

```python
# tests/unit/test_model_recovery.py
async def test_recovery_replays_only_latest_request(recovery_fixture) -> None:
    recovery_fixture.submit(10, "old question")
    recovery_fixture.submit(11, "latest question")
    await recovery_fixture.recover()
    assert recovery_fixture.replayed == [(11, "latest question")]
```

- [ ] **Step 3: Verify failures**

Run: `.venv\Scripts\python -m pytest tests/unit/test_model_registry.py tests/unit/test_model_recovery.py -v`

Expected: FAIL because registry/lifecycle modules do not exist.

- [ ] **Step 4: Implement single-flight model registry**

```python
class ModelRegistry:
    def __init__(self, client) -> None:
        self._client = client
        self._instances: dict[str, ModelInstance] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def ensure_ready(self, key: str) -> ModelInstance:
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            current = self._instances.get(key)
            if current and current.state == "ready":
                return current
            loaded = await self._client.find_loaded_instance(key)
            if loaded is None:
                loaded = await self._client.load_model(key)
            instance = ModelInstance(key=key, instance_id=loaded.instance_id, state="ready")
            self._instances[key] = instance
            return instance
```

- [ ] **Step 5: Implement bounded recovery**

`ModelLifecycle` cancels the active stream, retains the largest request id only, calls `ensure_ready()` under the same key lock, runs one warm-up, retries with delays `0.5`, `1.0`, `2.0` seconds, and emits `offline` after the third failure. It must reject a loaded instance whose device metadata is not the configured preferred Strix Halo device.

- [ ] **Step 6: Run tests and commit**

Run: `.venv\Scripts\python -m pytest tests/unit/test_model_registry.py tests/unit/test_model_recovery.py -v`

Expected: all tests pass.

```powershell
git add interview_assistant/lmstudio tests/unit
git commit -m "feat: add shared model lifecycle and recovery"
```

---

### Task 9: Native `/api/v1/chat` SSE Streaming and Request Cancellation

**Files:**
- Modify: `interview_assistant/lmstudio/client.py`
- Create: `interview_assistant/orchestration/coordinator.py`
- Create: `tests/contract/test_chat_stream.py`
- Create: `tests/unit/test_coordinator.py`

**Interfaces:**
- Produces: `ChatEvent`, `LMStudioClient.stream_chat()`, `RequestCoordinator.submit()`.

- [ ] **Step 1: Write failing SSE parsing test**

```python
# tests/contract/test_chat_stream.py
SSE = b"""event: chat.start\ndata: {\"type\":\"chat.start\",\"model_instance_id\":\"qwen3.5\"}\n\nevent: message.delta\ndata: {\"type\":\"message.delta\",\"content\":\"Hello\"}\n\nevent: chat.end\ndata: {\"type\":\"chat.end\",\"result\":{\"stats\":{\"tokens_per_second\":42}}}\n\n"""


async def test_stream_yields_message_delta(fake_stream_client) -> None:
    fake_stream_client.set_body(SSE)
    events = [event async for event in fake_stream_client.stream_chat({"model": "qwen3.5"})]
    assert [event.type for event in events] == ["chat.start", "message.delta", "chat.end"]
    assert events[1].content == "Hello"
```

- [ ] **Step 2: Verify failure**

Run: `.venv\Scripts\python -m pytest tests/contract/test_chat_stream.py -v`

Expected: FAIL because streaming method is missing.

- [ ] **Step 3: Implement named SSE parsing**

```python
# interview_assistant/lmstudio/models.py additions
class ChatEvent(BaseModel):
    type: str
    content: str = ""
    progress: float | None = None
    model_instance_id: str | None = None
    tool: str | None = None
    arguments: dict[str, object] | None = None
    output: str | None = None
    result: dict[str, object] | None = None
```

`stream_chat()` posts to `/api/v1/chat` with `stream=true`, parses `event:` plus `data:` blocks, validates JSON into `ChatEvent`, yields `message.delta`, `model_load.*`, `prompt_processing.*`, `tool_call.*`, `error`, and `chat.end`, and closes the response promptly on cancellation.

- [ ] **Step 4: Implement monotonic request coordination**

```python
class RequestCoordinator:
    def __init__(self, events, client) -> None:
        self._events = events
        self._client = client
        self._active_id = 0
        self._active_task: asyncio.Task[None] | None = None

    def submit(self, payload: dict) -> int:
        self._active_id += 1
        request_id = self._active_id
        if self._active_task:
            self._active_task.cancel()
        self._events.answer_reset.emit(request_id)
        self._active_task = asyncio.create_task(self._run(request_id, payload))
        return request_id

    async def _run(self, request_id: int, payload: dict) -> None:
        async for event in self._client.stream_chat(payload):
            if request_id != self._active_id:
                return
            if event.type == "message.delta":
                self._events.answer_delta.emit(request_id, event.content)
```

- [ ] **Step 5: Test stale-event rejection**

Run: `.venv\Scripts\python -m pytest tests/contract/test_chat_stream.py tests/unit/test_coordinator.py -v`

Expected: current request renders deltas; cancelled request renders none after replacement.

- [ ] **Step 6: Commit**

```powershell
git add interview_assistant/lmstudio interview_assistant/orchestration tests
git commit -m "feat: stream native LM Studio chat events"
```

---

# Phase 4 — Liquid Ribbon and Event Capture

### Task 10: Liquid Ribbon Overlay

**Files:**
- Create: `interview_assistant/ui/overlay.py`
- Create: `tests/unit/test_overlay.py`
- Modify: `interview_assistant/app.py`

**Interfaces:**
- Consumes: `EventBus` state and answer signals.
- Produces: `LiquidRibbon.show_state()`, `append_delta()`, `toggle_collapsed()`.

- [ ] **Step 1: Write failing Qt tests**

```python
# tests/unit/test_overlay.py
from interview_assistant.events import EventBus
from interview_assistant.ui.overlay import LiquidRibbon


def test_streaming_delta_appends_without_replacing(qtbot) -> None:
    bus = EventBus()
    ribbon = LiquidRibbon(bus)
    qtbot.addWidget(ribbon)
    bus.answer_reset.emit(7)
    bus.answer_delta.emit(7, "Hello ")
    bus.answer_delta.emit(7, "world")
    assert ribbon.answer_text == "Hello world"


def test_stale_delta_is_ignored(qtbot) -> None:
    bus = EventBus()
    ribbon = LiquidRibbon(bus)
    qtbot.addWidget(ribbon)
    bus.answer_reset.emit(8)
    bus.answer_delta.emit(7, "stale")
    assert ribbon.answer_text == ""
```

- [ ] **Step 2: Verify failure**

Run: `.venv\Scripts\python -m pytest tests/unit/test_overlay.py -v`

Expected: FAIL because overlay does not exist.

- [ ] **Step 3: Implement the Ribbon widget**

Create a frameless `QMainWindow` with translucent background, top horizontal layout, status chip, question label, streaming answer browser, source row, collapse button, drag/resize handlers, maximum configured height, and persisted geometry. Connect signals before showing the window. Escape all model text before converting allowed Markdown subsets to rich text.

- [ ] **Step 4: Apply the approved glass style**

Use a semi-transparent dark gradient, one-pixel light border, subtle cyan/violet edge highlight, rounded 18px corners, readable 12–14px text, and no animation that delays tokens. Keep opacity configurable and preserve WCAG-readable foreground contrast at the minimum allowed opacity.

- [ ] **Step 5: Run headless Qt tests**

Run:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.venv\Scripts\python -m pytest tests/unit/test_overlay.py -v
Remove-Item Env:QT_QPA_PLATFORM
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```powershell
git add interview_assistant/ui interview_assistant/app.py tests/unit/test_overlay.py
git commit -m "feat: add liquid ribbon streaming overlay"
```

---

### Task 11: Windows Capture Exclusion and Honest Readiness Signal

**Files:**
- Create: `interview_assistant/ui/windows_affinity.py`
- Create: `tests/unit/test_windows_affinity.py`
- Modify: `interview_assistant/ui/overlay.py`

**Interfaces:**
- Produces: `apply_capture_exclusion(hwnd) -> AffinityResult`.

- [ ] **Step 1: Write failing Win32 wrapper test**

```python
# tests/unit/test_windows_affinity.py
def test_affinity_failure_is_reported(fake_user32) -> None:
    fake_user32.SetWindowDisplayAffinity.return_value = 0
    from interview_assistant.ui.windows_affinity import apply_capture_exclusion

    result = apply_capture_exclusion(123, user32=fake_user32)
    assert not result.ok
    assert result.error_code is not None
```

- [ ] **Step 2: Verify failure**

Run: `.venv\Scripts\python -m pytest tests/unit/test_windows_affinity.py -v`

Expected: FAIL because wrapper does not exist.

- [ ] **Step 3: Implement Set/GetWindowDisplayAffinity verification**

```python
from ctypes import WinDLL, byref, c_uint
from dataclasses import dataclass

WDA_EXCLUDEFROMCAPTURE = 0x00000011


@dataclass(frozen=True)
class AffinityResult:
    ok: bool
    applied_value: int | None
    error_code: int | None


def apply_capture_exclusion(hwnd: int, user32=None) -> AffinityResult:
    api = user32 or WinDLL("user32", use_last_error=True)
    if not api.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE):
        from ctypes import get_last_error
        return AffinityResult(False, None, get_last_error())
    current = c_uint(0)
    if not api.GetWindowDisplayAffinity(hwnd, byref(current)):
        from ctypes import get_last_error
        return AffinityResult(False, None, get_last_error())
    return AffinityResult(current.value == WDA_EXCLUDEFROMCAPTURE, current.value, None)
```

- [ ] **Step 4: Integrate after top-level HWND creation**

Call the wrapper only after `winId()` exists. A failed result changes readiness to warning/failed and remains visible in Settings; do not log success unless `GetWindowDisplayAffinity` returns the expected value.

- [ ] **Step 5: Run tests and manual capture smoke**

Run: `.venv\Scripts\python -m pytest tests/unit/test_windows_affinity.py -v`

Expected: unit test passes. Manual smoke captures the desktop with Windows Snipping Tool and records whether the ribbon is absent; Teams acceptance remains a later independent check.

- [ ] **Step 6: Commit**

```powershell
git add interview_assistant/ui tests/unit/test_windows_affinity.py
git commit -m "feat: verify Windows capture exclusion"
```

---

### Task 12: Event-Driven Screenshot, Deduplication, and Protected-Frame Detection

**Files:**
- Create: `interview_assistant/capture/backend.py`
- Create: `interview_assistant/capture/frame_validator.py`
- Create: `interview_assistant/capture/worker.py`
- Create: `tests/unit/test_frame_validator.py`
- Create: `tests/unit/test_capture_lifecycle.py`

**Interfaces:**
- Produces: `CaptureWorker.capture_for_event()`, `FrameValidator.classify()`.

- [ ] **Step 1: Write failing black-frame and duplicate tests**

```python
# tests/unit/test_frame_validator.py
import numpy as np
from interview_assistant.capture.frame_validator import FrameValidator


def test_uniform_black_frame_is_protected() -> None:
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    assert FrameValidator().classify(frame).status == "protected"


def test_normal_frame_is_available() -> None:
    rng = np.random.default_rng(7)
    frame = rng.integers(0, 255, (720, 1280, 3), dtype=np.uint8)
    assert FrameValidator().classify(frame).status == "available"
```

- [ ] **Step 2: Verify failures**

Run: `.venv\Scripts\python -m pytest tests/unit/test_frame_validator.py -v`

Expected: FAIL because capture package does not exist.

- [ ] **Step 3: Implement frame classification**

```python
# interview_assistant/capture/frame_validator.py
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class FrameAssessment:
    status: Literal["available", "protected", "duplicate"]
    mean: float
    variance: float
    entropy: float
    near_black_ratio: float
    perceptual_hash: str
```

Calculate grayscale mean, variance, histogram entropy, near-black pixel ratio, and perceptual hash. Mark protected only when near-black ratio exceeds the configured threshold and variance/entropy are below calibrated bounds. Never classify from a single black pixel region alone.

- [ ] **Step 4: Implement serialized event capture and temp lifecycle**

Use one MSS handle inside its worker thread, one `asyncio.Lock`/thread lock for all capture calls, `tempfile.TemporaryDirectory`, UUID filenames, JPEG output, perceptual duplicate rejection, and explicit deletion on buffer eviction. Trigger only for code/system-design/screen-analysis events or manual hotkey.

- [ ] **Step 5: Run tests**

Run: `.venv\Scripts\python -m pytest tests/unit/test_frame_validator.py tests/unit/test_capture_lifecycle.py -v`

Expected: black frame is skipped, duplicates produce one stored frame, shutdown removes temporary files.

- [ ] **Step 6: Commit**

```powershell
git add interview_assistant/capture tests/unit
git commit -m "feat: add safe event-driven screen capture"
```

---

# Phase 5 — MCP Retrieval

### Task 13: Search Policy, Context7, and Local DuckDuckGo MCP

**Files:**
- Create: `interview_assistant/retrieval/models.py`
- Create: `interview_assistant/retrieval/policy.py`
- Create: `scripts/configure_mcp.py`
- Create: `config/mcp.template.json`
- Create: `tests/unit/test_search_policy.py`
- Create: `tests/integration/test_mcp_payload.py`
- Modify: `interview_assistant/orchestration/coordinator.py`

**Interfaces:**
- Produces: `SearchPolicy.integrations_for(question)`, generated LM Studio `mcp.json` entries.

- [ ] **Step 1: Write failing routing/privacy tests**

```python
# tests/unit/test_search_policy.py
from interview_assistant.retrieval.policy import SearchPolicy


def test_library_question_uses_context7_only() -> None:
    integrations = SearchPolicy("auto").integrations_for(
        "Как сейчас настраивается lifespan в FastAPI?",
        full_transcript="private interview transcript",
    )
    assert [item.id for item in integrations] == ["mcp/context7"]
    assert "private interview transcript" not in integrations[0].query


def test_off_mode_exposes_no_tools() -> None:
    assert SearchPolicy("off").integrations_for("latest Python release", "secret") == []
```

- [ ] **Step 2: Verify failure**

Run: `.venv\Scripts\python -m pytest tests/unit/test_search_policy.py -v`

Expected: FAIL because retrieval package does not exist.

- [ ] **Step 3: Implement bounded integration payloads**

```python
# interview_assistant/retrieval/models.py
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SearchIntegration:
    id: str
    query: str
    allowed_tools: tuple[str, ...]

    def to_lmstudio(self) -> dict[str, object]:
        return {
            "type": "plugin",
            "id": self.id,
            "allowed_tools": list(self.allowed_tools),
        }
```

Context7 integration exposes only its library-resolution and documentation-query tools. DuckDuckGo integration exposes only the search tool; do not expose arbitrary page fetch in the first release. `Auto` activates tools only for temporal markers, versions, documentation, releases, standards, or explicit current-information language. `Forced` activates DuckDuckGo for one request. Search query contains the current question only after removal of names, email-like strings, URLs with tokens, and transcript metadata.

- [ ] **Step 4: Generate reproducible MCP configuration**

`scripts/configure_mcp.py` accepts an explicit LM Studio config directory and an already-installed absolute DuckDuckGo executable path. It writes:

```json
{
  "mcpServers": {
    "context7": {"url": "https://mcp.context7.com/mcp"},
    "duckduckgo": {"command": "C:\\absolute\\path\\duckduckgo-mcp-server.exe", "args": []}
  }
}
```

The script refuses relative commands, refuses overwriting unknown existing servers, creates a timestamped backup, and never writes API keys.

- [ ] **Step 5: Install a pinned DuckDuckGo MCP environment**

Install `nickclyde/duckduckgo-mcp-server` release `v0.5.0` at commit `8992977d65a086995c82826ceead42e890aa17c1` into `tools/duckduckgo-mcp/.venv`. Export hashes into `tools/duckduckgo-mcp/requirements.lock`. Before installation, verify the tag still resolves to that commit; abort on mismatch. Do not use `uvx ...@latest` at runtime.

- [ ] **Step 6: Run tests and commit**

Run:

```powershell
.venv\Scripts\python -m pytest tests/unit/test_search_policy.py tests/integration/test_mcp_payload.py -v
```

Expected: Context7/DDG payloads include only allowlisted tools; no transcript or screenshot fields are present.

```powershell
git add interview_assistant/retrieval interview_assistant/orchestration scripts config tests
git commit -m "feat: add bounded Context7 and DuckDuckGo retrieval"
```

---

# Phase 6 — Readiness, Packaging, and System Acceptance

### Task 14: Settings, Hotkeys, and Readiness Diagnostics

**Files:**
- Create: `interview_assistant/ui/settings.py`
- Create: `interview_assistant/diagnostics/readiness.py`
- Create: `tests/unit/test_readiness.py`
- Modify: `interview_assistant/app.py`
- Modify: `utils/hotkeys.py`

**Interfaces:**
- Produces: `ReadinessReport`, settings model binding, hotkey actions.

- [ ] **Step 1: Write failing readiness aggregation test**

```python
# tests/unit/test_readiness.py
async def test_required_failure_blocks_session(readiness_fixture) -> None:
    readiness_fixture.fail("display_affinity", "unsupported")
    report = await readiness_fixture.run()
    assert report.status == "failed"
    assert report.by_name("display_affinity").message == "unsupported"
```

- [ ] **Step 2: Verify failure**

Run: `.venv\Scripts\python -m pytest tests/unit/test_readiness.py -v`

Expected: FAIL because diagnostics package does not exist.

- [ ] **Step 3: Implement readiness contracts**

```python
@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Literal["ready", "warning", "failed"]
    message: str
    duration_ms: float


@dataclass(frozen=True)
class ReadinessReport:
    checks: tuple[CheckResult, ...]

    @property
    def status(self) -> str:
        if any(item.status == "failed" for item in self.checks):
            return "failed"
        if any(item.status == "warning" for item in self.checks):
            return "warning"
        return "ready"
```

Run checks for Windows/DWM, two audio devices, CUDA/STT, LM API auth, LM Link preferred device, model discovery/load, duplicate instances, MCP connectivity, hotkeys, affinity, event capture, and streaming TTFT. Each check has an independent timeout and user-readable remediation.

- [ ] **Step 4: Implement Settings and hotkey actions**

Settings provides device selectors, language, text/vision model selectors with shared-instance annotation, search mode, opacity/geometry, token setup through `SecretStore`, and a readiness page. Hotkeys: force request, screenshot, pause, overlay visibility, forced web search, clear answer. Callbacks emit Qt events rather than touching widgets directly.

- [ ] **Step 5: Run tests**

Run: `.venv\Scripts\python -m pytest tests/unit/test_readiness.py tests/unit/test_overlay.py -v`

Expected: required failures block start; warning checks remain visible but do not crash Settings.

- [ ] **Step 6: Commit**

```powershell
git add interview_assistant/ui interview_assistant/diagnostics interview_assistant/app.py utils/hotkeys.py tests/unit
git commit -m "feat: add settings and readiness diagnostics"
```

---

### Task 15: Remove Legacy Paths and Complete Application Wiring

**Files:**
- Modify: `interview_assistant/app.py`
- Modify: `main.py`
- Delete after parity verification: `main_optimized.py`
- Delete after parity verification: `src/audio_capture.py`, `src/transcriber.py`, `src/question_detector.py`, `src/context_aggregator.py`, `src/multimodal_client.py`, `src/multimodal_client_optimized.py`, `src/screen_capture.py`, `src/overlay_gui.py`, `src/performance_monitor.py`
- Create: `tests/integration/test_application_flow.py`

**Interfaces:**
- Consumes all prior phase interfaces.
- Produces one supported entry point and one shutdown path.

- [ ] **Step 1: Write failing vertical flow test**

```python
# tests/integration/test_application_flow.py
async def test_question_to_streaming_overlay(application_fixture) -> None:
    await application_fixture.feed_system_audio("Спроектируйте сервис коротких ссылок")
    await application_fixture.wait_for_final_transcript()
    await application_fixture.wait_for_answer("API Gateway")
    assert application_fixture.overlay_text.endswith("API Gateway")
    assert application_fixture.capture_count == 1
    assert application_fixture.model_load_count == 1
```

- [ ] **Step 2: Verify failure**

Run: `.venv\Scripts\python -m pytest tests/integration/test_application_flow.py -v`

Expected: FAIL until application wiring is complete.

- [ ] **Step 3: Wire components in dependency order**

`InterviewApplication.start()` must load config/secrets, create Ribbon, apply affinity, construct bounded queues/workers, run readiness, start audio/STT/hotkeys, and enter listening only after required checks pass. `shutdown()` cancels the active request, stops audio/STT/capture, closes HTTP, deletes temporary files, closes overlay, and remains idempotent after partial initialization. MCP subprocesses launched from LM Studio `mcp.json` remain owned and terminated by LM Studio, not by Interview Assistant.

- [ ] **Step 4: Verify feature parity and remove legacy modules**

Before deletion, map every supported user action to the new implementation: pause/resume, force request, manual screenshot, model selection, cache/history clear, overlay toggle, settings, and shutdown. Delete legacy modules only after the vertical flow and corresponding unit tests pass.

- [ ] **Step 5: Run the complete automated suite**

Run:

```powershell
.venv\Scripts\python -m pytest -v --cov=interview_assistant --cov-report=term-missing
.venv\Scripts\python -m ruff check interview_assistant tests scripts
.venv\Scripts\python -m mypy interview_assistant
.venv\Scripts\python -m compileall -q interview_assistant main.py
```

Expected: zero failed tests, zero Ruff errors, zero mypy errors, compile exit 0; core modules have at least 80% line coverage.

- [ ] **Step 6: Commit**

```powershell
git add -A
git commit -m "refactor: complete Interview Assistant application wiring"
```

---

### Task 16: Windows Packaging and Reproducible Installation

**Files:**
- Create: `packaging/interview_assistant.spec`
- Create: `scripts/build.ps1`
- Create: `scripts/verify_cuda.py`
- Modify: `README.md`

**Interfaces:**
- Produces: `dist/InterviewAssistant/InterviewAssistant.exe` onedir distribution.

- [ ] **Step 1: Create a packaging smoke check**

```powershell
# scripts/build.ps1
$ErrorActionPreference = 'Stop'
& .venv\Scripts\python -m pytest -q
& .venv\Scripts\pyinstaller --noconfirm packaging\interview_assistant.spec
& dist\InterviewAssistant\InterviewAssistant.exe --diagnostics --no-gui
if ($LASTEXITCODE -ne 0) { throw 'packaged diagnostics failed' }
```

- [ ] **Step 2: Add an onedir PyInstaller specification**

Collect PyQt6, faster-whisper, CTranslate2, MSS, keyring backends and required model assets explicitly. Use `console=False`, product name `InterviewAssistant`, and version metadata. Do not use onefile mode because CUDA/native extraction increases startup latency and complicates diagnostics.

- [ ] **Step 3: Add CUDA verification**

`scripts/verify_cuda.py` loads the configured STT model with `device="cuda"`, transcribes a bundled two-second non-sensitive fixture, prints model load time and real-time factor as JSON, and exits non-zero when CUDA inference is unavailable.

- [ ] **Step 4: Document prerequisites and installation**

README must include Windows 11, NVIDIA driver, LM Studio 0.4+, LM Link setup, preferred Strix Halo device, local API authentication, model selection, MCP setup, explicit privacy limitations, and Teams full-screen test instructions. Remove claims that `Qt.Tool` alone hides the window from sharing.

- [ ] **Step 5: Build and verify on both development and production machines**

Run `scripts\build.ps1` on the AMD development machine for CPU/no-GUI diagnostics, then on the RTX 5070 Ti machine for CUDA diagnostics and full GUI smoke.

Expected: packaged app starts, reports missing optional production hardware cleanly on AMD, and passes CUDA fixture on RTX.

- [ ] **Step 6: Commit**

```powershell
git add packaging scripts README.md
git commit -m "build: package reproducible Windows application"
```

---

### Task 17: Hardware Benchmarks and Teams Acceptance

**Files:**
- Create: `scripts/benchmark_session.py`
- Create: `scripts/teams_acceptance.md`
- Create: `docs/validation/acceptance-template.md`

**Interfaces:**
- Produces recorded evidence for STT latency, TTFT, model recovery, capture exclusion and shutdown.

- [ ] **Step 1: Implement structured session benchmark output**

`benchmark_session.py` records JSONL events for audio-start, first partial, final transcript, request submitted, prompt-processing end, first answer token, chat end, tokens/sec, capture duration, model load duration and recovery duration. It excludes transcript content and images.

- [ ] **Step 2: Run RU/EN STT benchmark on RTX 5070 Ti**

Use at least 20 Russian and 20 English technical utterances with consent. Record p50/p95 first-partial latency, finalization latency and word error rate. Pass criteria: p95 first partial ≤1.0 s and p95 finalization after pause ≤0.8 s; if missed, adjust window/VAD/beam settings and repeat.

- [ ] **Step 3: Run LM Link and recovery benchmark**

Measure normal TTFT and tokens/sec for selected models. Unload the model on Strix Halo during a controlled request, verify exactly one reload, verify preferred device, verify only the latest queued request replays, and record total recovery time without imposing an unrealistic fixed upper bound for a 95 GB model.

- [ ] **Step 4: Execute Teams full-screen acceptance**

Use a second participant device. Share the entire primary display in Teams, open/move/resize/collapse the Ribbon, stream an answer, trigger an event screenshot, and review a Teams recording. Record whether overlay is absent in both live view and recording. If visible, mark acceptance failed and do not claim invisibility; collect Windows/Teams versions and capture-affinity diagnostics.

- [ ] **Step 5: Validate protected content and screenshot behavior**

Open an application that legitimately returns a protected black frame, confirm the assistant reports `Protected content`, confirm no image reaches VLM, and confirm audio/manual text paths continue. Observe and record any third-party notification; do not suppress it.

- [ ] **Step 6: Run final verification and commit evidence**

Run:

```powershell
.venv\Scripts\python -m pytest -v
.venv\Scripts\python scripts\benchmark_session.py --report docs\validation\latest.jsonl
```

Expected: all automated tests pass and acceptance template contains dated hardware/software versions plus pass/fail evidence for every criterion.

```powershell
git add scripts docs/validation
git commit -m "test: record hardware and Teams acceptance"
```

---

## Execution Checkpoints

1. **After Phase 1:** configuration and lifecycle review; no hardware required.
2. **After Phase 2:** RTX STT benchmark review before LM integration.
3. **After Phase 3:** LM Link model dedup/recovery and SSE contract review.
4. **After Phase 4:** visual Ribbon review plus Windows capture-affinity smoke.
5. **After Phase 5:** MCP tool allowlist and privacy review.
6. **After Phase 6:** packaged app, benchmarks and Teams acceptance review.

Do not proceed past a checkpoint with failing required tests or an undocumented acceptance failure.
