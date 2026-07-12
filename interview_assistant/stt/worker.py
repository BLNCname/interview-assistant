import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from queue import Empty, Full, Queue
from threading import Event, Lock, RLock, Thread, current_thread
from typing import Protocol, TypeVar

import numpy as np
from numpy.typing import NDArray

from interview_assistant.audio.models import AudioFrame, AudioSource

from .engine import (
    LanguageLatch,
    TranscriptHypothesis,
    TranscriptionEngine,
    WhisperEngine,
)
from .stable_prefix import StablePrefix

_TARGET_SAMPLE_RATE = 16_000
_DEFAULT_INPUT_CAPACITY = 64
_DEFAULT_HYPOTHESIS_CAPACITY = 32
_DEFAULT_PARTIAL_INTERVAL_SECONDS = 0.5
_DEFAULT_PARTIAL_WINDOW_SECONDS = 8.0
_DEFAULT_PARTIAL_OVERLAP_SECONDS = 0.8
_DEFAULT_MAX_RETAINED_AUDIO_SECONDS = 12.0
_DEFAULT_SILENCE_DURATION_SECONDS = 0.6
_DEFAULT_LANGUAGE_THRESHOLD = 0.8
_DEFAULT_LANGUAGE_DETECTION_SECONDS = 1.5
_DEFAULT_VAD_THRESHOLD = 0.01
_QUEUE_POLL_SECONDS = 0.01

_HypothesisCallback = Callable[[TranscriptHypothesis], None]
_T = TypeVar("_T")


class VoiceActivityDetector(Protocol):
    def is_speech(self, frame: AudioFrame) -> bool: ...


class EnergyVoiceActivityDetector:
    def __init__(self, threshold: float = _DEFAULT_VAD_THRESHOLD) -> None:
        if threshold < 0:
            raise ValueError("VAD threshold must be non-negative")
        self._threshold = threshold

    def is_speech(self, frame: AudioFrame) -> bool:
        samples = np.asarray(frame.samples, dtype=np.float32)
        if samples.size == 0:
            return False
        rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
        return rms >= self._threshold


class _CoalescingHypothesisQueue(Queue[TranscriptHypothesis]):
    """Bounded hypothesis queue with atomic priority coalescing."""

    def __init__(self, maxsize: int) -> None:
        if maxsize <= 0:
            raise ValueError("hypothesis queue must be bounded")
        super().__init__(maxsize=maxsize)

    def put_coalesced(self, item: TranscriptHypothesis) -> None:
        with self.mutex:
            queued = list(self.queue)
            latest_by_source: dict[
                AudioSource,
                tuple[int, TranscriptHypothesis],
            ] = {}
            for sequence, candidate in enumerate((*queued, item)):
                previous = latest_by_source.get(candidate.source)
                if previous is None or _hypothesis_priority(
                    candidate
                ) >= _hypothesis_priority(previous[1]):
                    latest_by_source[candidate.source] = (sequence, candidate)

            retained_entries = sorted(
                latest_by_source.values(),
                key=lambda entry: (_hypothesis_priority(entry[1]), entry[0]),
                reverse=True,
            )[: self.maxsize]
            retained_existing = sum(
                sequence < len(queued) for sequence, _ in retained_entries
            )
            self._replace_locked(
                [candidate for _, candidate in retained_entries],
                discarded_existing=len(queued) - retained_existing,
                incoming_retained=any(
                    sequence == len(queued) for sequence, _ in retained_entries
                ),
            )

    def _replace_locked(
        self,
        retained: list[TranscriptHypothesis],
        *,
        discarded_existing: int,
        incoming_retained: bool,
    ) -> None:
        old_size = self._qsize()
        while self._qsize():
            self._get()
        for candidate in retained:
            self._put(candidate)

        unfinished_tasks = (
            self.unfinished_tasks
            - discarded_existing
            + int(incoming_retained)
        )
        if unfinished_tasks < 0:
            raise ValueError("task_done() called too many times")
        self.unfinished_tasks = unfinished_tasks

        new_size = self._qsize()
        if new_size > old_size:
            self.not_empty.notify()
        if new_size < old_size:
            self.not_full.notify_all()
        if unfinished_tasks == 0:
            self.all_tasks_done.notify_all()


@dataclass(slots=True)
class _SourceState:
    vad: VoiceActivityDetector
    language: LanguageLatch
    stable_prefix: StablePrefix = field(default_factory=StablePrefix)
    chunks: list[NDArray[np.float32]] = field(default_factory=list)
    started_at: float | None = None
    last_voiced_end: float = 0.0
    last_frame_end: float = 0.0
    voiced_seconds: float = 0.0
    last_partial_seconds: float = 0.0
    silence_seconds: float = 0.0
    samples_through_last_voice: int = 0
    total_samples: int = 0
    buffer_start_sample: int = 0
    partial_window_start_sample: int = 0
    last_partial_end_sample: int = 0
    committed_partial: str = ""
    last_raw_partial: str = ""
    last_published_partial: str = ""
    previous_language: str | None = None

    def reset_utterance(self) -> None:
        self.chunks.clear()
        self.started_at = None
        self.last_voiced_end = 0.0
        self.last_frame_end = 0.0
        self.voiced_seconds = 0.0
        self.last_partial_seconds = 0.0
        self.silence_seconds = 0.0
        self.samples_through_last_voice = 0
        self.total_samples = 0
        self.buffer_start_sample = 0
        self.partial_window_start_sample = 0
        self.last_partial_end_sample = 0
        self.committed_partial = ""
        self.last_raw_partial = ""
        self.last_published_partial = ""
        self.stable_prefix.reset()
        self.language.reset_utterance()


@dataclass(frozen=True, slots=True)
class _PartialWindowRoll:
    start_sample: int
    committed_partial: str


class StreamingSTTWorker:
    """Decode source audio and publish hypotheses from one background thread.

    ``on_hypothesis`` runs synchronously while publication is linearized against
    :meth:`stop`, so callbacks must be non-blocking. A callback may call ``stop()``
    reentrantly.
    """

    def __init__(
        self,
        system_queue: Queue[AudioFrame] | None = None,
        microphone_queue: Queue[AudioFrame] | None = None,
        *,
        engine: TranscriptionEngine | None = None,
        on_hypothesis: _HypothesisCallback | None = None,
        queue_capacity: int = _DEFAULT_INPUT_CAPACITY,
        hypothesis_capacity: int = _DEFAULT_HYPOTHESIS_CAPACITY,
        partial_interval_seconds: float = _DEFAULT_PARTIAL_INTERVAL_SECONDS,
        partial_window_seconds: float = _DEFAULT_PARTIAL_WINDOW_SECONDS,
        partial_overlap_seconds: float = _DEFAULT_PARTIAL_OVERLAP_SECONDS,
        max_retained_audio_seconds: float = _DEFAULT_MAX_RETAINED_AUDIO_SECONDS,
        silence_duration_seconds: float = _DEFAULT_SILENCE_DURATION_SECONDS,
        language_threshold: float = _DEFAULT_LANGUAGE_THRESHOLD,
        language_detection_seconds: float = _DEFAULT_LANGUAGE_DETECTION_SECONDS,
        vad_factory: Callable[[], VoiceActivityDetector] | None = None,
    ) -> None:
        if queue_capacity <= 0:
            raise ValueError("queue_capacity must be positive")
        if hypothesis_capacity <= 0:
            raise ValueError("hypothesis_capacity must be positive")
        if partial_interval_seconds <= 0:
            raise ValueError("partial_interval_seconds must be positive")
        if partial_window_seconds <= 0:
            raise ValueError("partial_window_seconds must be positive")
        if not 0 < partial_overlap_seconds < partial_window_seconds:
            raise ValueError(
                "partial_overlap_seconds must be positive and smaller than the window"
            )
        if max_retained_audio_seconds <= partial_window_seconds:
            raise ValueError(
                "max_retained_audio_seconds must be greater than the partial window"
            )
        if silence_duration_seconds <= 0:
            raise ValueError("silence_duration_seconds must be positive")
        if language_detection_seconds <= 0:
            raise ValueError("language_detection_seconds must be positive")

        self._queues = {
            AudioSource.SYSTEM: system_queue
            or Queue[AudioFrame](maxsize=queue_capacity),
            AudioSource.MICROPHONE: microphone_queue
            or Queue[AudioFrame](maxsize=queue_capacity),
        }
        if self._queues[AudioSource.SYSTEM] is self._queues[AudioSource.MICROPHONE]:
            raise ValueError("system and microphone queues must be distinct")
        if any(queue.maxsize <= 0 for queue in self._queues.values()):
            raise ValueError("input queues must be bounded")

        make_vad = vad_factory or EnergyVoiceActivityDetector
        self._states = {
            source: _SourceState(
                vad=make_vad(),
                language=LanguageLatch(language_threshold),
            )
            for source in AudioSource
        }
        self._engine = engine or WhisperEngine()
        self._on_hypothesis = on_hypothesis
        self._hypothesis_queue = _CoalescingHypothesisQueue(
            maxsize=hypothesis_capacity
        )
        self._partial_interval_seconds = partial_interval_seconds
        self._partial_window_samples = round(
            partial_window_seconds * _TARGET_SAMPLE_RATE
        )
        self._partial_overlap_samples = round(
            partial_overlap_seconds * _TARGET_SAMPLE_RATE
        )
        self._max_retained_audio_samples = round(
            max_retained_audio_seconds * _TARGET_SAMPLE_RATE
        )
        self._silence_duration_seconds = silence_duration_seconds
        self._language_detection_seconds = language_detection_seconds
        self._stop_event = Event()
        self._input_available = Event()
        self._publication_gate = RLock()
        self._lifecycle_lock = Lock()
        self._error_lock = Lock()
        self._thread: Thread | None = None
        self._stopped = False
        self._last_error: Exception | None = None

    @property
    def system_queue(self) -> Queue[AudioFrame]:
        return self._queues[AudioSource.SYSTEM]

    @property
    def microphone_queue(self) -> Queue[AudioFrame]:
        return self._queues[AudioSource.MICROPHONE]

    @property
    def hypothesis_queue(self) -> Queue[TranscriptHypothesis]:
        return self._hypothesis_queue

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def last_error(self) -> Exception | None:
        with self._error_lock:
            return self._last_error

    def submit(self, frame: AudioFrame) -> None:
        with self._lifecycle_lock:
            if self._stopped:
                raise RuntimeError("stopped STT worker cannot accept audio")
            queue = self._queues[frame.source]
            _put_latest(queue, frame)
            self._input_available.set()

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._stopped:
                raise RuntimeError("stopped STT worker cannot be started")
            if self.is_running:
                return
            thread = Thread(
                target=self._run,
                name="streaming-stt-worker",
                daemon=True,
            )
            self._thread = thread
            thread.start()

    def stop(self, timeout: float = 5.0) -> bool:
        if timeout < 0:
            raise ValueError("timeout must be non-negative")
        with self._publication_gate:
            with self._lifecycle_lock:
                self._stopped = True
                self._stop_event.set()
                self._input_available.set()
                thread = self._thread

        if thread is not None and thread is not current_thread():
            thread.join(timeout=timeout)
        stopped = thread is None or not thread.is_alive()
        if stopped:
            self._discard_pending_frames()
            for state in self._states.values():
                state.reset_utterance()
        return stopped

    def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                queued = self._dequeue_next()
                if queued is None:
                    continue
                expected_source, queue, frame = queued
                try:
                    if frame.source is not expected_source:
                        raise ValueError(
                            f"{expected_source.value} queue received {frame.source.value} frame"
                        )
                    self._process_frame(frame)
                except Exception as error:
                    self._record_error(error)
                finally:
                    queue.task_done()
        finally:
            self._discard_pending_frames()
            for state in self._states.values():
                state.reset_utterance()

    def _dequeue_next(
        self,
    ) -> tuple[AudioSource, Queue[AudioFrame], AudioFrame] | None:
        while not self._stop_event.is_set():
            for source in (AudioSource.SYSTEM, AudioSource.MICROPHONE):
                queue = self._queues[source]
                try:
                    return source, queue, queue.get_nowait()
                except Empty:
                    pass
            self._input_available.wait(timeout=_QUEUE_POLL_SECONDS)
            self._input_available.clear()
        return None

    def _process_frame(self, frame: AudioFrame) -> None:
        if frame.sample_rate != _TARGET_SAMPLE_RATE:
            raise ValueError(
                f"STT expects {_TARGET_SAMPLE_RATE} Hz audio, got {frame.sample_rate} Hz"
            )

        state = self._states[frame.source]
        samples = np.asarray(frame.samples, dtype=np.float32).reshape(-1)
        duration = samples.size / frame.sample_rate
        is_speech = state.vad.is_speech(frame)

        if (
            state.started_at is not None
            and frame.timestamp - state.last_frame_end
            >= self._silence_duration_seconds
        ):
            self._finish_utterance(frame.source, state)

        if state.started_at is None:
            if not is_speech:
                return
            state.started_at = frame.timestamp

        state.chunks.append(samples)
        state.total_samples += samples.size
        state.last_frame_end = frame.timestamp + duration
        if is_speech:
            state.voiced_seconds += duration
            state.silence_seconds = 0.0
            state.last_voiced_end = frame.timestamp + duration
            state.samples_through_last_voice = state.total_samples
            partial_due = (
                state.voiced_seconds - state.last_partial_seconds
                >= self._partial_interval_seconds
            )
            try:
                if partial_due:
                    self._decode_partial(frame.source, state)
            finally:
                if partial_due:
                    state.last_partial_seconds = state.voiced_seconds
                self._prune_retained_audio(state)
            return

        state.silence_seconds += duration
        if state.silence_seconds >= self._silence_duration_seconds:
            self._finish_utterance(frame.source, state)
        else:
            self._prune_retained_audio(state)

    def _decode_partial(self, source: AudioSource, state: _SourceState) -> None:
        roll = self._plan_partial_window_roll(state)
        start_sample = (
            state.partial_window_start_sample
            if roll is None
            else roll.start_sample
        )
        result = self._engine.transcribe(
            _utterance_audio(state, start_sample=start_sample),
            beam_size=1,
            condition_on_previous_text=False,
        )
        if roll is not None:
            state.committed_partial = roll.committed_partial
            state.partial_window_start_sample = roll.start_sample
            state.stable_prefix.reset()
        state.last_partial_end_sample = state.samples_through_last_voice
        state.last_raw_partial = result.text.strip()
        language = self._language_for_result(
            state,
            result.language,
            result.language_probability,
            is_final=False,
        )
        reconciled = state.stable_prefix.update(state.last_raw_partial)
        if not reconciled:
            return
        stable_text = _merge_transcript(state.committed_partial, reconciled)
        if stable_text == state.last_published_partial:
            return
        state.last_published_partial = stable_text
        self._publish(
            TranscriptHypothesis(
                source=source,
                text=stable_text,
                language=language,
                is_final=False,
                started_at=_started_at(state),
                ended_at=state.last_voiced_end,
            )
        )

    def _decode_final(self, source: AudioSource, state: _SourceState) -> None:
        result = self._engine.transcribe(
            _utterance_audio(
                state,
                start_sample=max(
                    state.buffer_start_sample,
                    state.partial_window_start_sample,
                ),
            ),
            beam_size=3,
            condition_on_previous_text=False,
        )
        raw_text = result.text.strip()
        if not raw_text:
            return
        text = _merge_transcript(state.committed_partial, raw_text)
        language = self._language_for_result(
            state,
            result.language,
            result.language_probability,
            is_final=True,
        )
        self._publish(
            TranscriptHypothesis(
                source=source,
                text=text,
                language=language,
                is_final=True,
                started_at=_started_at(state),
                ended_at=state.last_voiced_end,
            )
        )

    def _plan_partial_window_roll(
        self,
        state: _SourceState,
    ) -> _PartialWindowRoll | None:
        current_length = (
            state.samples_through_last_voice - state.partial_window_start_sample
        )
        if current_length <= self._partial_window_samples:
            return None
        committed_partial = state.committed_partial
        committed_words = [
            _normalized_word(word) for word in committed_partial.split()
        ]
        commit_candidate = state.last_published_partial
        candidate_words = [
            _normalized_word(word) for word in commit_candidate.split()
        ]
        if not commit_candidate or (
            committed_words[: len(candidate_words)] == candidate_words
        ):
            commit_candidate = state.last_raw_partial
            candidate_words = [
                _normalized_word(word) for word in commit_candidate.split()
            ]
        if commit_candidate and (
            committed_words[: len(candidate_words)] != candidate_words
        ):
            committed_partial = _merge_transcript(
                committed_partial,
                commit_candidate,
            )
        return _PartialWindowRoll(
            start_sample=max(
                state.buffer_start_sample,
                state.last_partial_end_sample - self._partial_overlap_samples,
                state.samples_through_last_voice - self._partial_window_samples,
            ),
            committed_partial=committed_partial,
        )

    def _prune_retained_audio(self, state: _SourceState) -> None:
        keep_from = max(
            0,
            state.samples_through_last_voice - self._max_retained_audio_samples,
        )
        if keep_from <= state.buffer_start_sample:
            return
        if state.last_raw_partial:
            state.committed_partial = _merge_transcript(
                state.committed_partial,
                state.last_raw_partial,
            )
        _prune_audio_before(state, keep_from)
        if state.partial_window_start_sample < state.buffer_start_sample:
            state.partial_window_start_sample = state.buffer_start_sample
            state.stable_prefix.reset()

    def _finish_utterance(
        self,
        source: AudioSource,
        state: _SourceState,
    ) -> None:
        try:
            self._decode_final(source, state)
        except Exception as error:
            self._record_error(error)
        finally:
            state.reset_utterance()

    def _language_for_result(
        self,
        state: _SourceState,
        language: str,
        probability: float,
        *,
        is_final: bool,
    ) -> str:
        if state.language.language is not None:
            return state.language.language
        if not is_final and state.voiced_seconds < self._language_detection_seconds:
            return state.previous_language or language
        if (
            state.previous_language is not None
            and probability < state.language.threshold
        ):
            selected = state.language.update(state.previous_language, 1.0)
        else:
            selected = state.language.update(language, probability)
        state.previous_language = selected
        return selected

    def _publish(self, hypothesis: TranscriptHypothesis) -> None:
        with self._publication_gate:
            if self._stop_event.is_set():
                return
            _put_hypothesis(self._hypothesis_queue, hypothesis)
            if self._on_hypothesis is None:
                return
            try:
                self._on_hypothesis(hypothesis)
            except Exception as error:
                self._record_error(error)

    def _record_error(self, error: Exception) -> None:
        with self._error_lock:
            self._last_error = error

    def _discard_pending_frames(self) -> None:
        for queue in self._queues.values():
            while True:
                try:
                    queue.get_nowait()
                except Empty:
                    break
                queue.task_done()


def _utterance_audio(
    state: _SourceState,
    *,
    start_sample: int = 0,
) -> NDArray[np.float32]:
    start_sample = max(start_sample, state.buffer_start_sample)
    end_sample = state.samples_through_last_voice
    if not state.chunks or end_sample <= start_sample:
        return np.empty(0, dtype=np.float32)

    pieces: list[NDArray[np.float32]] = []
    position = state.buffer_start_sample
    for chunk in state.chunks:
        chunk_end = position + chunk.size
        if chunk_end <= start_sample:
            position = chunk_end
            continue
        if position >= end_sample:
            break
        local_start = max(0, start_sample - position)
        local_end = min(chunk.size, end_sample - position)
        pieces.append(chunk[local_start:local_end])
        position = chunk_end
    if len(pieces) == 1:
        return pieces[0]
    return np.concatenate(pieces)


def _prune_audio_before(state: _SourceState, start_sample: int) -> None:
    keep_from = min(max(start_sample, state.buffer_start_sample), state.total_samples)
    samples_to_drop = keep_from - state.buffer_start_sample
    dropped_chunks = 0
    while dropped_chunks < len(state.chunks):
        chunk = state.chunks[dropped_chunks]
        if samples_to_drop < chunk.size:
            break
        samples_to_drop -= chunk.size
        dropped_chunks += 1
    if dropped_chunks:
        del state.chunks[:dropped_chunks]
    if samples_to_drop and state.chunks:
        state.chunks[0] = state.chunks[0][samples_to_drop:]
    state.buffer_start_sample = keep_from


def _started_at(state: _SourceState) -> float:
    if state.started_at is None:
        raise RuntimeError("utterance has no start timestamp")
    return state.started_at


def _merge_transcript(committed: str, current: str) -> str:
    if not committed:
        return current
    committed_words = committed.split()
    current_words = current.split()
    overlap = 0
    for count in range(min(len(committed_words), len(current_words)), 0, -1):
        committed_overlap = [_normalized_word(word) for word in committed_words[-count:]]
        current_overlap = [_normalized_word(word) for word in current_words[:count]]
        if committed_overlap == current_overlap:
            overlap = count
            break
    return " ".join((*committed_words, *current_words[overlap:]))


def _normalized_word(word: str) -> str:
    normalized = unicodedata.normalize("NFKC", word).casefold()
    return "".join(
        character
        for character in normalized
        if not unicodedata.category(character).startswith("P")
    )


def _put_hypothesis(
    queue: _CoalescingHypothesisQueue,
    item: TranscriptHypothesis,
) -> None:
    queue.put_coalesced(item)


def _hypothesis_priority(hypothesis: TranscriptHypothesis) -> int:
    if not hypothesis.is_final:
        return 0
    if hypothesis.source is AudioSource.MICROPHONE:
        return 1
    return 2


def _put_latest(queue: Queue[_T], item: _T) -> None:
    try:
        queue.put_nowait(item)
        return
    except Full:
        pass

    try:
        queue.get_nowait()
    except Empty:
        pass
    else:
        queue.task_done()
    try:
        queue.put_nowait(item)
    except Full:
        # Another producer won the race; the queue remains bounded and current.
        pass
