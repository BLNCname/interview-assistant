from collections.abc import Callable, Iterable
from dataclasses import dataclass
from threading import Lock
from typing import Protocol, cast

import numpy as np
from numpy.typing import NDArray

from interview_assistant.audio.models import AudioSource


@dataclass(frozen=True, slots=True)
class TranscriptHypothesis:
    source: AudioSource
    text: str
    language: str
    is_final: bool
    started_at: float
    ended_at: float


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    text: str
    language: str
    language_probability: float


class TranscriptionEngine(Protocol):
    def transcribe(
        self,
        audio: NDArray[np.float32],
        *,
        beam_size: int,
        condition_on_previous_text: bool,
    ) -> TranscriptionResult: ...


class _WhisperSegment(Protocol):
    text: str


class _WhisperInfo(Protocol):
    language: str
    language_probability: float


class _WhisperModel(Protocol):
    def transcribe(
        self,
        audio: NDArray[np.float32],
        **kwargs: object,
    ) -> tuple[Iterable[_WhisperSegment], _WhisperInfo]: ...


_ModelFactory = Callable[..., _WhisperModel]


class WhisperEngine:
    def __init__(
        self,
        model_name: str = "large-v3-turbo",
        *,
        device: str = "cuda",
        compute_type: str = "float16",
        model_factory: _ModelFactory | None = None,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self._model_factory = model_factory or _create_whisper_model
        self._model: _WhisperModel | None = None
        self._model_lock = Lock()

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def transcribe(
        self,
        audio: NDArray[np.float32],
        *,
        beam_size: int,
        condition_on_previous_text: bool,
        **decode_options: object,
    ) -> TranscriptionResult:
        if beam_size <= 0:
            raise ValueError("beam_size must be positive")
        samples = np.asarray(audio, dtype=np.float32)
        options = {
            "beam_size": beam_size,
            "condition_on_previous_text": condition_on_previous_text,
            "multilingual": True,
            **decode_options,
        }
        segments, info = self._get_model().transcribe(samples, **options)
        text = " ".join(
            segment_text
            for segment in segments
            if (segment_text := segment.text.strip())
        )
        language = str(getattr(info, "language", "")).strip() or "unknown"
        probability = float(getattr(info, "language_probability", 0.0))
        return TranscriptionResult(text, language, probability)

    def _get_model(self) -> _WhisperModel:
        model = self._model
        if model is not None:
            return model
        with self._model_lock:
            if self._model is None:
                self._model = self._model_factory(
                    self.model_name,
                    device=self.device,
                    compute_type=self.compute_type,
                )
            return self._model


def _create_whisper_model(
    model_name: str,
    *,
    device: str,
    compute_type: str,
) -> _WhisperModel:
    from faster_whisper import WhisperModel  # type: ignore[import-untyped]

    return cast(
        _WhisperModel,
        WhisperModel(model_name, device=device, compute_type=compute_type),
    )


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
