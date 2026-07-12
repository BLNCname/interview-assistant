from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite
from threading import RLock
from time import monotonic
from typing import TypeAlias

from interview_assistant.audio.models import AudioSource
from interview_assistant.stt.engine import TranscriptHypothesis

FinalTranscriptEntry: TypeAlias = TranscriptHypothesis
TranscriptSnapshot: TypeAlias = tuple[FinalTranscriptEntry, ...]


@dataclass(frozen=True, slots=True)
class _StoredEntry:
    sequence: int
    hypothesis: FinalTranscriptEntry


class TranscriptStore:
    """Keep an age-bounded, source-separated record of final STT utterances."""

    def __init__(
        self,
        max_age_seconds: float = 300.0,
        *,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if not isfinite(max_age_seconds) or max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be a finite positive number")
        self.max_age_seconds = max_age_seconds
        self._clock = clock
        self._entries: dict[AudioSource, list[_StoredEntry]] = {
            source: [] for source in AudioSource
        }
        self._next_sequence = 0
        self._lock = RLock()

    def add(self, hypothesis: TranscriptHypothesis) -> None:
        """Store one final hypothesis without changing its typed immutable value."""

        if not isinstance(hypothesis, TranscriptHypothesis):
            raise TypeError("TranscriptStore accepts only TranscriptHypothesis values")
        if not hypothesis.is_final:
            raise ValueError("TranscriptStore accepts only final hypotheses")

        with self._lock:
            entry = _StoredEntry(self._next_sequence, hypothesis)
            self._next_sequence += 1
            self._entries[hypothesis.source].append(entry)
            self._prune(self._clock())

    def append(self, hypothesis: TranscriptHypothesis) -> None:
        """Alias for ``add`` for queue-like callers."""

        self.add(hypothesis)

    def snapshot(self, source: AudioSource | None = None) -> TranscriptSnapshot:
        """Return a chronological immutable view, optionally for one audio source."""

        with self._lock:
            self._prune(self._clock())
            if source is None:
                stored = [entry for entries in self._entries.values() for entry in entries]
            else:
                stored = list(self._entries[source])
            stored.sort(
                key=lambda entry: (
                    entry.hypothesis.started_at,
                    entry.hypothesis.ended_at,
                    entry.sequence,
                )
            )
            return tuple(entry.hypothesis for entry in stored)

    def latest(self, source: AudioSource) -> FinalTranscriptEntry | None:
        """Return the latest non-expired final utterance from ``source``."""

        entries = self.snapshot(source)
        return entries[-1] if entries else None

    def __len__(self) -> int:
        return len(self.snapshot())

    def _prune(self, now: float) -> None:
        cutoff = now - self.max_age_seconds
        for source, entries in self._entries.items():
            self._entries[source] = [
                entry for entry in entries if entry.hypothesis.ended_at >= cutoff
            ]
