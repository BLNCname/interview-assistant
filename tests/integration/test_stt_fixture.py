from collections.abc import Callable
from time import monotonic, sleep

import numpy as np

from interview_assistant.audio.models import AudioFrame, AudioSource
from interview_assistant.stt.engine import TranscriptionResult
from interview_assistant.stt.worker import StreamingSTTWorker


class ReplayEngine:
    """Deterministic replay backend; it never imports or downloads a model."""

    def transcribe(
        self,
        audio: np.ndarray,
        *,
        beam_size: int,
        condition_on_previous_text: bool,
    ) -> TranscriptionResult:
        assert condition_on_previous_text is False
        marker = float(audio[np.flatnonzero(audio)[0]])
        if marker < 0.4:
            text = "What is a Python decorator?"
            language = "en"
        else:
            text = "Декоратор оборачивает функцию."
            language = "ru"
        if beam_size == 1:
            text = text.removesuffix("?").removesuffix(".")
        return TranscriptionResult(text, language, 0.99)


def _wait_for(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if predicate():
            return
        sleep(0.005)
    raise AssertionError("timed out waiting for replay transcripts")


def test_dual_source_replay_produces_one_final_per_role_without_hardware() -> None:
    finals = []
    worker = StreamingSTTWorker(
        engine=ReplayEngine(),
        on_hypothesis=lambda item: finals.append(item) if item.is_final else None,
        partial_interval_seconds=0.5,
        silence_duration_seconds=0.6,
    )

    for source, timestamp, marker in (
        (AudioSource.SYSTEM, 10.0, 0.25),
        (AudioSource.MICROPHONE, 20.0, 0.5),
    ):
        worker.submit(
            AudioFrame(source, timestamp, np.full(9_600, marker, dtype=np.float32))
        )
        worker.submit(
            AudioFrame(source, timestamp + 0.6, np.zeros(9_600, dtype=np.float32))
        )

    worker.start()
    _wait_for(lambda: len(finals) == 2)
    worker.stop()

    assert [(item.source.value, item.text, item.language) for item in finals] == [
        ("interviewer", "What is a Python decorator?", "en"),
        ("you", "Декоратор оборачивает функцию.", "ru"),
    ]
