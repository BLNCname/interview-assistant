from __future__ import annotations

from collections.abc import Callable

from interview_assistant.audio.models import AudioSource
from interview_assistant.runtime import RuntimeHypothesisIngress
from interview_assistant.stt.engine import TranscriptHypothesis


class _DeferredLoop:
    def __init__(self) -> None:
        self.callbacks: list[Callable[[], None]] = []

    def call_soon_threadsafe(self, callback: Callable[[], None]) -> None:
        self.callbacks.append(callback)

    def drain(self) -> None:
        callbacks, self.callbacks = self.callbacks, []
        for callback in callbacks:
            callback()


def _hypothesis(
    text: str,
    *,
    final: bool,
    source: AudioSource = AudioSource.SYSTEM,
) -> TranscriptHypothesis:
    return TranscriptHypothesis(source, text, "en", final, 1.0, 2.0)


def test_final_then_partial_before_loop_drain_preserves_durable_final() -> None:
    loop = _DeferredLoop()
    ingress = RuntimeHypothesisIngress(loop)
    received: list[TranscriptHypothesis] = []
    ingress.bind(lambda hypothesis: received.append(hypothesis))
    final = _hypothesis("first final", final=True)
    partial = _hypothesis("next partial", final=False)

    ingress.publish(final)
    ingress.publish(partial)
    loop.drain()

    assert received == [final, partial]


def test_two_finals_before_loop_drain_remain_fifo() -> None:
    loop = _DeferredLoop()
    ingress = RuntimeHypothesisIngress(loop)
    received: list[TranscriptHypothesis] = []
    ingress.bind(lambda hypothesis: received.append(hypothesis))
    first = _hypothesis("first final", final=True)
    second = _hypothesis("second final", final=True)

    ingress.publish(first)
    ingress.publish(second)
    loop.drain()

    assert received == [first, second]


def test_final_supersedes_an_older_pending_partial_from_the_same_source() -> None:
    loop = _DeferredLoop()
    ingress = RuntimeHypothesisIngress(loop)
    received: list[TranscriptHypothesis] = []
    ingress.bind(lambda hypothesis: received.append(hypothesis))
    partial = _hypothesis("older partial", final=False)
    final = _hypothesis("durable final", final=True)

    ingress.publish(partial)
    ingress.publish(final)
    loop.drain()

    assert received == [final]
    assert ingress.partial_drop_count == 1


def test_capacity_pressure_evicts_partial_before_final_and_reports_final_overflow() -> None:
    loop = _DeferredLoop()
    ingress = RuntimeHypothesisIngress(loop, capacity=2)
    received: list[TranscriptHypothesis] = []
    ingress.bind(lambda hypothesis: received.append(hypothesis))
    partial = _hypothesis("partial", final=False, source=AudioSource.MICROPHONE)
    first = _hypothesis("first final", final=True)
    second = _hypothesis("second final", final=True)
    overflow = _hypothesis("overflow final", final=True)

    ingress.publish(partial)
    ingress.publish(first)
    ingress.publish(second)
    ingress.publish(overflow)
    loop.drain()

    assert received == [first, second]
    assert ingress.partial_drop_count == 1
    assert ingress.final_overflow_count == 1
