from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from typing import Literal, TypeVar
from uuid import uuid4

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from interview_assistant.transcript.detector import QuestionKind

from .backend import CaptureBackend, MSSCaptureBackend
from .frame_validator import FrameAssessment, FrameValidator

CaptureStatus = Literal["captured", "protected", "duplicate", "disallowed"]
BackendFactory = Callable[[], CaptureBackend]
FrameSaver = Callable[[NDArray[np.uint8], Path, int], None]
_ResultT = TypeVar("_ResultT")

_AUTOMATIC_CAPTURE_KINDS: frozenset[QuestionKind] = frozenset(
    {"coding", "system_design", "screen_analysis"}
)


@dataclass(frozen=True, slots=True)
class CaptureResult:
    status: CaptureStatus
    path: Path | None
    assessment: FrameAssessment | None


def _save_jpeg(frame: NDArray[np.uint8], path: Path, quality: int) -> None:
    Image.fromarray(frame).save(path, format="JPEG", quality=quality)


async def _await_completion(
    future: asyncio.Future[_ResultT],
) -> _ResultT:
    """Drain a shielded operation before propagating caller cancellation.

    If the operation itself fails, that operational error takes precedence over
    cancellation. This keeps cleanup failures observable and deterministic.
    """

    cancellation: asyncio.CancelledError | None = None
    while not future.done():
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError as error:
            if cancellation is None:
                cancellation = error
        except Exception:
            # The operation is done with an error; future.result() re-raises it below.
            break
    result = future.result()
    if cancellation is not None:
        raise cancellation
    return result


class CaptureWorker:
    """Perform event-driven captures on one backend-owning worker thread."""

    def __init__(
        self,
        *,
        backend_factory: BackendFactory = MSSCaptureBackend,
        validator: FrameValidator | None = None,
        frame_saver: FrameSaver = _save_jpeg,
        max_retained: int = 3,
        jpeg_quality: int = 85,
    ) -> None:
        if max_retained <= 0:
            raise ValueError("max_retained must be positive")
        if not 1 <= jpeg_quality <= 95:
            raise ValueError("jpeg_quality must be between 1 and 95")
        self._backend_factory = backend_factory
        self._validator = validator if validator is not None else FrameValidator()
        self._frame_saver = frame_saver
        self._max_retained = max_retained
        self._jpeg_quality = jpeg_quality
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="capture-worker",
        )
        self._single_flight = asyncio.Lock()
        self._temporary_directory = TemporaryDirectory(
            prefix="interview-assistant-capture-"
        )
        self._temp_path = Path(self._temporary_directory.name)
        self._retained: deque[Path] = deque()
        self._retained_lock = Lock()
        self._backend: CaptureBackend | None = None
        self._previous_usable_hash: str | None = None
        self._shutdown_requested = False
        self._shutdown_task: asyncio.Task[None] | None = None

    @property
    def temp_directory(self) -> Path:
        return self._temp_path

    @property
    def retained_paths(self) -> tuple[Path, ...]:
        with self._retained_lock:
            return tuple(self._retained)

    async def capture_for_event(
        self,
        kind: QuestionKind,
        *,
        manual: bool = False,
    ) -> CaptureResult:
        if self._shutdown_requested:
            raise RuntimeError("capture worker is shut down")
        async with self._single_flight:
            if self._shutdown_requested:
                raise RuntimeError("capture worker is shut down")
            if not manual and kind not in _AUTOMATIC_CAPTURE_KINDS:
                return CaptureResult("disallowed", None, None)
            loop = asyncio.get_running_loop()
            operation = loop.run_in_executor(self._executor, self._capture_sync)
            return await _await_completion(operation)

    async def shutdown(self) -> None:
        self._shutdown_requested = True
        task = self._shutdown_task
        if task is None:
            task = asyncio.create_task(self._shutdown_once())
            self._shutdown_task = task
        await _await_completion(task)

    async def _shutdown_once(self) -> None:
        async with self._single_flight:
            operation_error: Exception | None = None
            try:
                loop = asyncio.get_running_loop()
                finalization = loop.run_in_executor(
                    self._executor,
                    self._finalize_owner_thread,
                )
                await _await_completion(finalization)
            except Exception as error:
                operation_error = error
            finally:
                try:
                    self._executor.shutdown(wait=True, cancel_futures=True)
                except Exception as error:
                    if operation_error is None:
                        operation_error = error
                with self._retained_lock:
                    self._retained.clear()
                self._previous_usable_hash = None
                try:
                    self._temporary_directory.cleanup()
                except Exception as error:
                    if operation_error is None:
                        operation_error = error
            if operation_error is not None:
                raise operation_error

    def _capture_sync(self) -> CaptureResult:
        if self._backend is None:
            self._backend = self._backend_factory()
        frame = self._backend.capture()
        assessment = self._validator.classify(frame, self._previous_usable_hash)
        if assessment.status != "available":
            return CaptureResult(assessment.status, None, assessment)

        path = self._temp_path / f"{uuid4()}.jpg"
        try:
            self._frame_saver(frame, path, self._jpeg_quality)
        except Exception:
            path.unlink(missing_ok=True)
            raise

        self._previous_usable_hash = assessment.perceptual_hash
        with self._retained_lock:
            self._retained.append(path)
            while len(self._retained) > self._max_retained:
                self._retained.popleft().unlink(missing_ok=True)
        return CaptureResult("captured", path, assessment)

    def _finalize_owner_thread(self) -> None:
        if self._backend is None:
            return
        self._backend.close()
