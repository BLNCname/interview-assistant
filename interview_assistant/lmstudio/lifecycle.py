import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from .models import ModelInstance


T = TypeVar("T")


@dataclass(frozen=True)
class RecoveryRequest(Generic[T]):
    request_id: int
    payload: T


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
        self._pending: dict[str, RecoveryRequest[T]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._offline: set[str] = set()
        self._last_replayed: dict[str, tuple[int, ModelInstance]] = {}
        self._successful_waves: dict[str, int] = {}

    def submit(self, key: str, request: RecoveryRequest[T]) -> None:
        pending = self._pending.get(key)
        last_replayed = self._last_replayed.get(key)
        if pending is not None and request.request_id <= pending.request_id:
            return
        if last_replayed is not None and request.request_id < last_replayed[0]:
            return
        self._pending[key] = request

    async def recover(
        self,
        key: str,
        request: RecoveryRequest[T] | None = None,
        manual_retry: bool = False,
    ) -> ModelInstance:
        arrival_wave = self._successful_waves.get(key, 0)
        if request is not None:
            self.submit(key, request)

        pending = self._pending.get(key)
        if pending is None:
            if request is None:
                raise NoRecoveryRequestError(key)
            target_id = request.request_id
        else:
            target_id = pending.request_id
            if request is not None:
                target_id = max(target_id, request.request_id)

        if key in self._offline and not manual_retry:
            raise ModelOfflineError(key)

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if key in self._offline and not manual_retry:
                raise ModelOfflineError(key)

            current = self._pending.get(key)
            if current is not None:
                target_id = max(target_id, current.request_id)
            replayed = self._last_replayed.get(key)
            if (
                self._successful_waves.get(key, 0) > arrival_wave
                and replayed is not None
                and replayed[0] >= target_id
            ):
                if current is not None and current.request_id <= replayed[0]:
                    self._pending.pop(key, None)
                return replayed[1]

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
                    await self._replay(instance, latest)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self._offline.add(key)
                    self._on_state("offline")
                    raise ModelRecoveryError(key, attempt) from error

                self._last_replayed[key] = (latest.request_id, instance)
                self._successful_waves[key] = self._successful_waves.get(key, 0) + 1

                still_pending = self._pending.get(key)
                if still_pending is not None and still_pending.request_id <= latest.request_id:
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
