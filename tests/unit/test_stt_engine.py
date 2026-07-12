from pathlib import Path
import tomllib
from types import SimpleNamespace

import numpy as np

from interview_assistant.stt.engine import TranscriptionResult, WhisperEngine


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


def test_whisper_engine_loads_production_model_lazily_once() -> None:
    model = FakeWhisperModel()
    factory_calls: list[tuple[str, str, str]] = []

    def factory(model_name: str, *, device: str, compute_type: str) -> FakeWhisperModel:
        factory_calls.append((model_name, device, compute_type))
        return model

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
