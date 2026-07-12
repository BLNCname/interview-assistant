from collections.abc import Callable
from threading import Event, get_ident
from time import monotonic, sleep

import numpy as np
import pytest

from interview_assistant.audio.models import AudioFrame, AudioSource
from interview_assistant.stt.engine import TranscriptionResult
from interview_assistant.stt.worker import StreamingSTTWorker, _merge_transcript


def _wait_for(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        if predicate():
            return
        sleep(0.005)
    raise AssertionError("timed out waiting for STT worker")


def _frame(
    source: AudioSource,
    timestamp: float,
    value: float,
    duration_seconds: float,
) -> AudioFrame:
    return AudioFrame(
        source,
        timestamp,
        np.full(round(16_000 * duration_seconds), value, dtype=np.float32),
    )


class RecordingEngine:
    def __init__(self) -> None:
        self.calls: list[tuple[float, int, bool, int]] = []

    def transcribe(
        self,
        audio: np.ndarray,
        *,
        beam_size: int,
        condition_on_previous_text: bool,
    ) -> TranscriptionResult:
        marker = float(audio[np.flatnonzero(audio)[0]])
        self.calls.append(
            (marker, beam_size, condition_on_previous_text, get_ident())
        )
        language = "en" if marker < 0.4 else "ru"
        if marker < 0.4:
            text = "design a cache" if beam_size == 1 else "How would you design a cache?"
        else:
            text = "использую словарь" if beam_size == 1 else "Я использую словарь."
        return TranscriptionResult(text, language, 0.95)


def test_scheduler_prioritizes_system_and_decodes_only_in_background_thread() -> None:
    engine = RecordingEngine()
    worker = StreamingSTTWorker(engine=engine, partial_interval_seconds=0.5)
    caller_thread = get_ident()
    worker.submit(_frame(AudioSource.MICROPHONE, 20.0, 0.5, 0.5))
    worker.submit(_frame(AudioSource.SYSTEM, 10.0, 0.25, 0.5))

    worker.start()
    _wait_for(lambda: len(engine.calls) == 2)
    worker.stop()

    assert [call[0] for call in engine.calls] == [0.25, 0.5]
    assert all(call[1:3] == (1, False) for call in engine.calls)
    assert all(call[3] != caller_thread for call in engine.calls)


def test_worker_keeps_final_source_language_and_timestamps_separate() -> None:
    engine = RecordingEngine()
    published = []
    worker = StreamingSTTWorker(
        engine=engine,
        on_hypothesis=published.append,
        hypothesis_capacity=1,
        partial_interval_seconds=0.5,
        silence_duration_seconds=0.6,
    )
    for frame in (
        _frame(AudioSource.MICROPHONE, 20.0, 0.5, 0.3),
        _frame(AudioSource.SYSTEM, 10.0, 0.25, 0.3),
        _frame(AudioSource.MICROPHONE, 20.3, 0.5, 0.3),
        _frame(AudioSource.SYSTEM, 10.3, 0.25, 0.3),
        _frame(AudioSource.MICROPHONE, 20.6, 0.0, 0.3),
        _frame(AudioSource.SYSTEM, 10.6, 0.0, 0.3),
        _frame(AudioSource.MICROPHONE, 20.9, 0.0, 0.3),
        _frame(AudioSource.SYSTEM, 10.9, 0.0, 0.3),
    ):
        worker.submit(frame)

    worker.start()
    _wait_for(lambda: len(published) == 2)
    worker.stop()

    system, microphone = published
    assert (
        system.source,
        system.text,
        system.language,
        system.is_final,
        system.started_at,
        system.ended_at,
    ) == (
        AudioSource.SYSTEM,
        "How would you design a cache?",
        "en",
        True,
        10.0,
        pytest.approx(10.6),
    )
    assert (
        microphone.source,
        microphone.text,
        microphone.language,
        microphone.is_final,
        microphone.started_at,
        microphone.ended_at,
    ) == (
        AudioSource.MICROPHONE,
        "Я использую словарь.",
        "ru",
        True,
        20.0,
        pytest.approx(20.6),
    )
    assert [(call[1], call[2]) for call in engine.calls] == [
        (1, False),
        (3, False),
        (1, False),
        (3, False),
    ]
    assert worker.hypothesis_queue.maxsize == 1
    assert worker.hypothesis_queue.qsize() == 1
    assert worker.hypothesis_queue.get_nowait() is system


class PriorityEngine:
    def __init__(self) -> None:
        self.calls = 0

    def transcribe(
        self,
        audio: np.ndarray,
        *,
        beam_size: int,
        condition_on_previous_text: bool,
    ) -> TranscriptionResult:
        assert condition_on_previous_text is False
        self.calls += 1
        marker = float(audio[np.flatnonzero(audio)[0]])
        if beam_size == 3:
            return TranscriptionResult("critical system final", "en", 0.99)
        text = "system partial" if marker < 0.4 else "microphone partial"
        return TranscriptionResult(text, "en", 0.99)


def test_microphone_partial_does_not_evict_system_final_at_capacity_one() -> None:
    engine = PriorityEngine()
    published = []
    worker = StreamingSTTWorker(
        engine=engine,
        on_hypothesis=published.append,
        hypothesis_capacity=1,
        partial_interval_seconds=0.5,
        silence_duration_seconds=0.6,
    )
    for frame in (
        _frame(AudioSource.SYSTEM, 0.0, 0.25, 0.5),
        _frame(AudioSource.SYSTEM, 0.5, 0.0, 0.6),
        _frame(AudioSource.MICROPHONE, 2.0, 0.5, 0.5),
        _frame(AudioSource.MICROPHONE, 2.5, 0.5, 0.5),
    ):
        worker.submit(frame)

    worker.start()
    _wait_for(lambda: len(published) == 2)
    worker.stop()

    retained = worker.hypothesis_queue.get_nowait()
    assert retained.source is AudioSource.SYSTEM
    assert retained.is_final
    assert retained.text == "critical system final"


class FinalFailureEngine:
    def __init__(self) -> None:
        self.error = RuntimeError("final decode failed")
        self.final_audio: list[np.ndarray] = []

    def transcribe(
        self,
        audio: np.ndarray,
        *,
        beam_size: int,
        condition_on_previous_text: bool,
    ) -> TranscriptionResult:
        assert (beam_size, condition_on_previous_text) == (3, False)
        self.final_audio.append(audio.copy())
        if len(self.final_audio) == 1:
            raise self.error
        return TranscriptionResult("fresh utterance", "en", 0.99)


def test_final_decode_error_resets_endpoint_without_silence_retries() -> None:
    engine = FinalFailureEngine()
    finals = []
    worker = StreamingSTTWorker(
        engine=engine,
        on_hypothesis=lambda item: finals.append(item) if item.is_final else None,
        partial_interval_seconds=1.0,
        silence_duration_seconds=0.6,
    )
    for frame in (
        _frame(AudioSource.SYSTEM, 0.0, 0.25, 0.2),
        _frame(AudioSource.SYSTEM, 0.2, 0.0, 0.6),
        _frame(AudioSource.SYSTEM, 0.8, 0.0, 0.6),
        _frame(AudioSource.SYSTEM, 2.0, 0.75, 0.2),
        _frame(AudioSource.SYSTEM, 2.2, 0.0, 0.6),
    ):
        worker.submit(frame)

    worker.start()
    _wait_for(lambda: worker.system_queue.unfinished_tasks == 0)
    worker.stop()

    assert worker.last_error is engine.error
    assert len(engine.final_audio) == 2
    assert np.all(engine.final_audio[1] == np.float32(0.75))
    assert [(item.text, item.started_at) for item in finals] == [
        ("fresh utterance", 2.0)
    ]


class RollingMergeEngine:
    def __init__(self) -> None:
        self.partial_calls = 0
        self.final_audio_size = 0

    def transcribe(
        self,
        audio: np.ndarray,
        *,
        beam_size: int,
        condition_on_previous_text: bool,
    ) -> TranscriptionResult:
        assert condition_on_previous_text is False
        if beam_size == 3:
            self.final_audio_size = audio.size
            return TranscriptionResult("redis for caching", "en", 0.99)
        partials = ("Use Redis.", "Use Redis. cache", "redis for")
        text = partials[self.partial_calls]
        self.partial_calls += 1
        return TranscriptionResult(text, "en", 0.99)


def test_final_merges_normalized_overlap_after_real_window_roll() -> None:
    engine = RollingMergeEngine()
    finals = []
    worker = StreamingSTTWorker(
        engine=engine,
        on_hypothesis=lambda item: finals.append(item) if item.is_final else None,
        partial_interval_seconds=0.5,
        partial_window_seconds=1.0,
        partial_overlap_seconds=0.5,
        silence_duration_seconds=0.6,
    )
    for index in range(3):
        worker.submit(_frame(AudioSource.SYSTEM, index * 0.5, 0.25, 0.5))
    worker.submit(_frame(AudioSource.SYSTEM, 1.5, 0.0, 0.6))

    worker.start()
    _wait_for(lambda: len(finals) == 1)
    worker.stop()

    assert finals[0].text == "Use Redis. for caching"
    assert engine.final_audio_size == 16_000


class UnstableRollEngine:
    def __init__(self) -> None:
        self.partial_calls = 0

    def transcribe(
        self,
        audio: np.ndarray,
        *,
        beam_size: int,
        condition_on_previous_text: bool,
    ) -> TranscriptionResult:
        del audio
        assert condition_on_previous_text is False
        if beam_size == 3:
            return TranscriptionResult("tail wording completed", "en", 0.99)
        partials = (
            "draft early wording",
            "correct early wording",
            "tail wording",
        )
        text = partials[self.partial_calls]
        self.partial_calls += 1
        return TranscriptionResult(text, "en", 0.99)


def test_window_roll_preserves_raw_early_text_when_no_prefix_is_stable() -> None:
    finals = []
    worker = StreamingSTTWorker(
        engine=UnstableRollEngine(),
        on_hypothesis=lambda item: finals.append(item) if item.is_final else None,
        partial_interval_seconds=0.5,
        partial_window_seconds=1.0,
        partial_overlap_seconds=0.5,
        silence_duration_seconds=0.6,
    )
    for index in range(3):
        worker.submit(_frame(AudioSource.SYSTEM, index * 0.5, 0.25, 0.5))
    worker.submit(_frame(AudioSource.SYSTEM, 1.5, 0.0, 0.6))

    worker.start()
    _wait_for(lambda: len(finals) == 1)
    worker.stop()

    assert finals[0].text == "correct early wording tail wording completed"


def test_merge_matches_unicode_case_and_punctuation_without_rewriting_text() -> None:
    assert _merge_transcript("Use Straße,", "STRASSE safely") == "Use Straße, safely"


class LongRollingEngine:
    def __init__(self) -> None:
        self.partial_calls = 0
        self.final_audio_sizes: list[int] = []

    def transcribe(
        self,
        audio: np.ndarray,
        *,
        beam_size: int,
        condition_on_previous_text: bool,
    ) -> TranscriptionResult:
        assert condition_on_previous_text is False
        if beam_size == 3:
            self.final_audio_sizes.append(audio.size)
            return TranscriptionResult("eta theta iota", "en", 0.99)
        partials = (
            "alpha beta",
            "alpha beta gamma",
            "beta gamma delta",
            "gamma delta epsilon",
            "delta epsilon zeta",
            "epsilon zeta eta",
            "zeta eta theta",
            "eta theta iota",
        )
        text = partials[self.partial_calls]
        self.partial_calls += 1
        return TranscriptionResult(text, "en", 0.99)


def test_long_utterance_keeps_bounded_audio_and_complete_merged_final() -> None:
    engine = LongRollingEngine()
    finals = []
    worker = StreamingSTTWorker(
        engine=engine,
        on_hypothesis=lambda item: finals.append(item) if item.is_final else None,
        partial_interval_seconds=0.5,
        partial_window_seconds=1.0,
        partial_overlap_seconds=0.5,
        max_retained_audio_seconds=1.5,
        silence_duration_seconds=0.6,
    )
    for index in range(8):
        worker.submit(_frame(AudioSource.SYSTEM, index * 0.5, 0.25, 0.5))
    worker.submit(_frame(AudioSource.SYSTEM, 4.0, 0.0, 0.6))

    worker.start()
    _wait_for(lambda: len(finals) == 1)
    worker.stop()

    assert engine.final_audio_sizes == [pytest.approx(24_000, abs=8_000)]
    assert engine.final_audio_sizes[0] <= 24_000
    assert finals[0].text == (
        "alpha beta gamma delta epsilon zeta eta theta iota"
    )


class TimestampGapEngine:
    def __init__(self) -> None:
        self.final_markers: list[float] = []

    def transcribe(
        self,
        audio: np.ndarray,
        *,
        beam_size: int,
        condition_on_previous_text: bool,
    ) -> TranscriptionResult:
        assert (beam_size, condition_on_previous_text) == (3, False)
        marker = float(audio[0])
        self.final_markers.append(marker)
        text = "old utterance" if marker < 0.5 else "new utterance"
        return TranscriptionResult(text, "en", 0.99)


def test_timestamp_gap_at_endpoint_threshold_splits_utterances() -> None:
    engine = TimestampGapEngine()
    finals = []
    worker = StreamingSTTWorker(
        engine=engine,
        on_hypothesis=lambda item: finals.append(item) if item.is_final else None,
        partial_interval_seconds=1.0,
        silence_duration_seconds=0.6,
    )
    for frame in (
        _frame(AudioSource.SYSTEM, 0.0, 0.25, 0.2),
        _frame(AudioSource.SYSTEM, 10.0, 0.75, 0.2),
        _frame(AudioSource.SYSTEM, 10.2, 0.0, 0.6),
    ):
        worker.submit(frame)

    worker.start()
    _wait_for(lambda: worker.system_queue.unfinished_tasks == 0)
    worker.stop()

    assert engine.final_markers == [pytest.approx(0.25), pytest.approx(0.75)]
    assert [(item.text, item.started_at, item.ended_at) for item in finals] == [
        ("old utterance", 0.0, pytest.approx(0.2)),
        ("new utterance", 10.0, pytest.approx(10.2)),
    ]


class DelayedSecondPartialEngine:
    def __init__(self) -> None:
        self.calls = 0
        self.entered = Event()
        self.release = Event()

    def transcribe(
        self,
        audio: np.ndarray,
        *,
        beam_size: int,
        condition_on_previous_text: bool,
    ) -> TranscriptionResult:
        del audio
        assert (beam_size, condition_on_previous_text) == (1, False)
        self.calls += 1
        if self.calls == 1:
            return TranscriptionResult("keep this", "en", 0.99)
        self.entered.set()
        assert self.release.wait(timeout=2.0)
        return TranscriptionResult("keep this stable", "en", 0.99)


def test_timed_out_stop_eventually_cleans_up_without_late_publish() -> None:
    engine = DelayedSecondPartialEngine()
    published = []
    worker = StreamingSTTWorker(
        engine=engine,
        on_hypothesis=published.append,
        partial_interval_seconds=0.5,
    )
    for index in range(3):
        worker.submit(_frame(AudioSource.SYSTEM, index * 0.5, 0.25, 0.5))

    worker.start()
    assert engine.entered.wait(timeout=2.0)
    try:
        assert worker.stop(timeout=0.01) is False
    finally:
        engine.release.set()
    _wait_for(lambda: not worker.is_running)

    assert worker.system_queue.qsize() == 0
    assert worker.hypothesis_queue.qsize() == 0
    assert published == []
    assert all(state.started_at is None for state in worker._states.values())


class StablePartialEngine:
    def __init__(self) -> None:
        self.partial_calls = 0

    def transcribe(
        self,
        audio: np.ndarray,
        *,
        beam_size: int,
        condition_on_previous_text: bool,
    ) -> TranscriptionResult:
        del audio
        assert condition_on_previous_text is False
        if beam_size == 3:
            return TranscriptionResult("design a short link service", "en", 0.96)
        self.partial_calls += 1
        text = (
            "design a short link"
            if self.partial_calls == 1
            else "design a short link service"
        )
        return TranscriptionResult(text, "en", 0.96)


def test_worker_publishes_only_reconciled_partial_text() -> None:
    published = []
    worker = StreamingSTTWorker(
        engine=StablePartialEngine(),
        on_hypothesis=published.append,
        partial_interval_seconds=0.5,
        silence_duration_seconds=0.6,
    )
    for index in range(4):
        worker.submit(_frame(AudioSource.SYSTEM, index * 0.25, 0.25, 0.25))
    worker.submit(_frame(AudioSource.SYSTEM, 1.0, 0.0, 0.6))

    worker.start()
    _wait_for(lambda: len(published) == 2)
    worker.stop()

    partial, final = published
    assert (partial.text, partial.is_final) == ("design a short link", False)
    assert (final.text, final.is_final) == ("design a short link service", True)


class WindowRecordingEngine:
    def __init__(self) -> None:
        self.calls: list[tuple[int, float]] = []

    def transcribe(
        self,
        audio: np.ndarray,
        *,
        beam_size: int,
        condition_on_previous_text: bool,
    ) -> TranscriptionResult:
        assert (beam_size, condition_on_previous_text) == (1, False)
        self.calls.append((audio.size, float(audio[0])))
        return TranscriptionResult(f"partial number {len(self.calls)}", "en", 0.95)


def test_partial_decode_rolls_forward_with_eight_hundred_ms_overlap() -> None:
    engine = WindowRecordingEngine()
    worker = StreamingSTTWorker(
        engine=engine,
        partial_interval_seconds=0.5,
        partial_window_seconds=2.0,
        partial_overlap_seconds=0.8,
    )
    for index in range(7):
        worker.submit(
            _frame(AudioSource.SYSTEM, index * 0.5, (index + 1) / 10, 0.5)
        )

    worker.start()
    _wait_for(lambda: len(engine.calls) == 7)
    worker.stop()

    assert [size for size, _ in engine.calls] == [
        8_000,
        16_000,
        24_000,
        32_000,
        20_800,
        28_800,
        20_800,
    ]
    assert engine.calls[4][1] == pytest.approx(0.3)
    assert engine.calls[6][1] == pytest.approx(0.5)


class ChangingLanguageEngine:
    def __init__(self) -> None:
        self.calls = 0

    def transcribe(
        self,
        audio: np.ndarray,
        *,
        beam_size: int,
        condition_on_previous_text: bool,
    ) -> TranscriptionResult:
        del audio
        assert (beam_size, condition_on_previous_text) == (1, False)
        texts = (
            "one two",
            "one two three",
            "one two three four",
            "one two three four five",
        )
        languages = ("ru", "en", "ru", "en")
        result = TranscriptionResult(texts[self.calls], languages[self.calls], 0.95)
        self.calls += 1
        return result


def test_language_is_latched_after_one_and_a_half_seconds_of_voiced_audio() -> None:
    engine = ChangingLanguageEngine()
    published = []
    worker = StreamingSTTWorker(
        engine=engine,
        on_hypothesis=published.append,
        partial_interval_seconds=0.5,
        language_detection_seconds=1.5,
    )
    for index in range(4):
        worker.submit(_frame(AudioSource.SYSTEM, index * 0.5, 0.25, 0.5))

    worker.start()
    _wait_for(lambda: engine.calls == 4)
    worker.stop()

    assert [item.language for item in published] == ["en", "ru", "ru"]


class PreviousLanguageEngine:
    def __init__(self) -> None:
        self.calls = 0

    def transcribe(
        self,
        audio: np.ndarray,
        *,
        beam_size: int,
        condition_on_previous_text: bool,
    ) -> TranscriptionResult:
        del audio, condition_on_previous_text
        utterance = self.calls // 2
        self.calls += 1
        if utterance == 0:
            return TranscriptionResult("first answer", "en", 0.95)
        return TranscriptionResult("второй ответ", "ru", 0.40)


def test_low_confidence_new_utterance_keeps_previous_source_language() -> None:
    engine = PreviousLanguageEngine()
    finals = []
    worker = StreamingSTTWorker(
        engine=engine,
        on_hypothesis=lambda item: finals.append(item) if item.is_final else None,
        partial_interval_seconds=0.5,
        silence_duration_seconds=0.6,
        language_detection_seconds=1.5,
    )
    for frame in (
        _frame(AudioSource.SYSTEM, 0.0, 0.25, 0.6),
        _frame(AudioSource.SYSTEM, 0.6, 0.0, 0.6),
        _frame(AudioSource.SYSTEM, 2.0, 0.25, 0.6),
        _frame(AudioSource.SYSTEM, 2.6, 0.0, 0.6),
    ):
        worker.submit(frame)

    worker.start()
    _wait_for(lambda: len(finals) == 2)
    worker.stop()

    assert [item.language for item in finals] == ["en", "en"]


def test_worker_shutdown_is_idempotent_without_loading_model() -> None:
    class EngineMustNotRun:
        def transcribe(self, audio: np.ndarray, **kwargs: object) -> TranscriptionResult:
            raise AssertionError((audio, kwargs))

    worker = StreamingSTTWorker(engine=EngineMustNotRun())
    worker.start()
    worker.stop()
    worker.stop()

    assert not worker.is_running
