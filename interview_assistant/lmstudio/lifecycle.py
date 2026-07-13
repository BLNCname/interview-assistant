import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, Literal, Protocol, TypeVar

from .models import ModelInstance


T = TypeVar("T")


@dataclass(frozen=True)
class RecoveryRequest(Generic[T]):
    request_id: int
    payload: T


@dataclass(frozen=True)
class _QueuedRequest(Generic[T]):
    request: RecoveryRequest[T]
    submission_seq: int


@dataclass(frozen=True)
class _ReplayedRequest:
    request_id: int
    submission_seq: int
    instance: ModelInstance


@dataclass(frozen=True)
class _SubmissionOutcome:
    disposition: Literal["accepted", "targeted", "stale_completed"]
    target_submission_seq: int


class RecoveryRegistry(Protocol):
    async def refresh(self, key: str) -> ModelInstance: ...


class ModelRecoveryError(RuntimeError):
    def __init__(self, key: str, attempts: int) -> None:
        self.key = key
        self.attempts = attempts
        super().__init__(f"model {key!r} recovery failed after {attempts} attempts")


class ModelOfflineError(RuntimeError):
    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"model {key!r} is offline; manual retry is required")


class NoRecoveryRequestError(RuntimeError):
    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"model {key!r} has no queued recovery request")


def _ignore_state(_state: str) -> None:
    return None


class ModelLifecycle(Generic[T]):
    _DELAYS = (0.5, 1.0, 2.0)

    def __init__(
        self,
        registry: RecoveryRegistry,
        *,
        cancel_active: Callable[[], Awaitable[None]],
        warm_up: Callable[[ModelInstance], Awaitable[None]],
        replay: Callable[[ModelInstance, RecoveryRequest[T]], Awaitable[None]],
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        on_state: Callable[[str], None] = _ignore_state,
    ) -> None:
        self._registry = registry
        self._cancel_active = cancel_active
        self._warm_up = warm_up
        self._replay = replay
        self._sleeper = sleeper
        self._on_state = on_state
        self._pending: dict[str, _QueuedRequest[T]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._offline: set[str] = set()
        self._last_replayed: dict[str, _ReplayedRequest] = {}
        self._submission_sequences: dict[str, int] = {}

    def submit(self, key: str, request: RecoveryRequest[T]) -> None:
        self._submit(key, request)

    def _submit(
        self,
        key: str,
        request: RecoveryRequest[T],
    ) -> _SubmissionOutcome:
        pending = self._pending.get(key)
        if pending is not None and request.request_id <= pending.request.request_id:
            return _SubmissionOutcome("targeted", pending.submission_seq)

        last_replayed = self._last_replayed.get(key)
        if pending is None and last_replayed is not None:
            if request.request_id < last_replayed.request_id:
                return _SubmissionOutcome(
                    "stale_completed",
                    last_replayed.submission_seq,
                )

        submission_seq = self._submission_sequences.get(key, 0) + 1
        self._submission_sequences[key] = submission_seq
        self._pending[key] = _QueuedRequest(request, submission_seq)
        return _SubmissionOutcome("accepted", submission_seq)

    async def recover(
        self,
        key: str,
        request: RecoveryRequest[T] | None = None,
        manual_retry: bool = False,
    ) -> ModelInstance:
        if request is not None:
            submission = self._submit(key, request)
            target_submission_seq = submission.target_submission_seq
        else:
            pending = self._pending.get(key)
            if pending is None:
                raise NoRecoveryRequestError(key)
            submission = _SubmissionOutcome("targeted", pending.submission_seq)
            target_submission_seq = pending.submission_seq

        if key in self._offline and not manual_retry:
            raise ModelOfflineError(key)

        if submission.disposition == "stale_completed":
            completed = self._last_replayed[key]
            return completed.instance

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if key in self._offline and not manual_retry:
                raise ModelOfflineError(key)

            replayed = self._last_replayed.get(key)
            if (
                replayed is not None
                and replayed.submission_seq >= target_submission_seq
            ):
                return replayed.instance

            await self._cancel_active()
            self._on_state("recovering")

            last_error: Exception | None = None
            for attempt, delay in enumerate(self._DELAYS, start=1):
                await self._sleeper(delay)
                try:
                    instance = await self._registry.refresh(key)
                    await self._warm_up(instance)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    last_error = error
                    continue

                latest = self._pending.get(key)
                if latest is None:
                    raise NoRecoveryRequestError(key)
                try:
                    await self._replay(instance, latest.request)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self._offline.add(key)
                    self._on_state("offline")
                    raise ModelRecoveryError(key, attempt) from error

                self._last_replayed[key] = _ReplayedRequest(
                    latest.request.request_id,
                    latest.submission_seq,
                    instance,
                )

                still_pending = self._pending.get(key)
                if (
                    still_pending is not None
                    and still_pending.submission_seq <= latest.submission_seq
                ):
                    self._pending.pop(key, None)
                self._offline.discard(key)
                self._on_state("ready")
                return instance

            self._offline.add(key)
            self._on_state("offline")
            recovery_error = ModelRecoveryError(key, len(self._DELAYS))
            if last_error is None:  # pragma: no cover - every failed attempt records one
                raise recovery_error
            raise recovery_error from last_error
