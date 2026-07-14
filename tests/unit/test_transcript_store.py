from typing import cast

import pytest

from interview_assistant.audio.models import AudioSource
from interview_assistant.stt.engine import TranscriptHypothesis
from interview_assistant.transcript.store import TranscriptStore


class ManualClock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def hypothesis(
    source: AudioSource,
    text: str,
    *,
    started_at: float,
    ended_at: float,
    is_final: bool = True,
) -> TranscriptHypothesis:
    return TranscriptHypothesis(
        source=source,
        text=text,
        language="en",
        is_final=is_final,
        started_at=started_at,
        ended_at=ended_at,
    )


def test_store_accepts_only_final_transcript_hypotheses() -> None:
    store = TranscriptStore(clock=lambda: 10.0)

    with pytest.raises(ValueError, match="final"):
        store.add(
            hypothesis(
                AudioSource.SYSTEM,
                "partial",
                started_at=1.0,
                ended_at=2.0,
                is_final=False,
            )
        )
    with pytest.raises(TypeError, match="TranscriptHypothesis"):
        store.add(cast(TranscriptHypothesis, "not a hypothesis"))

    assert store.snapshot() == ()


def test_store_separates_sources_and_returns_chronological_immutable_snapshots() -> None:
    store = TranscriptStore(clock=lambda: 100.0)
    last = hypothesis(AudioSource.SYSTEM, "third", started_at=30.0, ended_at=31.0)
    first = hypothesis(AudioSource.SYSTEM, "first", started_at=10.0, ended_at=11.0)
    middle = hypothesis(AudioSource.MICROPHONE, "second", started_at=20.0, ended_at=21.0)

    store.add(last)
    store.add(first)
    store.add(middle)

    assert store.snapshot() == (first, middle, last)
    assert store.snapshot(AudioSource.SYSTEM) == (first, last)
    assert store.snapshot(AudioSource.MICROPHONE) == (middle,)
    assert store.latest(AudioSource.SYSTEM) is last
    assert store.latest(AudioSource.MICROPHONE) is middle
    assert isinstance(store.snapshot(), tuple)


def test_store_prunes_entries_older_than_fixed_max_age_using_injected_clock() -> None:
    clock = ManualClock(100.0)
    store = TranscriptStore(max_age_seconds=10.0, clock=clock)
    expired = hypothesis(AudioSource.SYSTEM, "expired", started_at=88.0, ended_at=89.0)
    boundary = hypothesis(AudioSource.SYSTEM, "boundary", started_at=89.0, ended_at=90.0)
    current = hypothesis(AudioSource.MICROPHONE, "current", started_at=98.0, ended_at=99.0)

    store.add(expired)
    store.add(boundary)
    store.add(current)

    assert store.snapshot() == (boundary, current)

    clock.now = 101.0

    assert store.snapshot() == (current,)
    assert store.latest(AudioSource.SYSTEM) is None


def test_store_rejects_non_positive_max_age() -> None:
    with pytest.raises(ValueError, match="max_age_seconds"):
        TranscriptStore(max_age_seconds=0.0)


def test_clear_can_remove_one_source_or_the_entire_history() -> None:
    store = TranscriptStore(clock=lambda: 10.0)
    system = hypothesis(AudioSource.SYSTEM, "system", started_at=8.0, ended_at=9.0)
    microphone = hypothesis(
        AudioSource.MICROPHONE,
        "microphone",
        started_at=8.5,
        ended_at=9.5,
    )
    store.add(system)
    store.add(microphone)

    store.clear(AudioSource.MICROPHONE)

    assert store.snapshot() == (system,)

    store.clear()

    assert store.snapshot() == ()
