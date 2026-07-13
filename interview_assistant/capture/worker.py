from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from typing import Literal
from uuid import uuid4

from PIL import Image

from interview_assistant.transcript.detector import QuestionKind

from .backend import CaptureBackend, MSSCaptureBackend
from .frame_validator import FrameAssessment, FrameValidator

CaptureStatus = Literal["captured", "protected", "duplicate", "disallowed"]
BackendFactory = Callable[[], CaptureBackend]

_AUTOMATIC_CAPTURE_KINDS: frozenset[QuestionKind] = frozenset(
    {"coding", "system_design", "screen_analysis"}
)


@dataclass(frozen=True, slots=True)
class CaptureResult:
    status: CaptureStatus
    path: Path | None
    assessment: FrameAssessment | None


class CaptureWorker:
    """Perform event-driven captures on one backend-owning worker thread."""

    def __init__(
        self,
        *,
        backend_factory: BackendFactory = MSSCaptureBackend,
        validator: FrameValidator | None = None,
        max_retained: int = 3,
        jpeg_quality: int = 85,
    ) -> None:
        if max_retained <= 0:
            raise ValueError("max_retained must be positive")
        if not 1 <= jpeg_quality <= 95:
            raise ValueError("jpeg_quality must be between 1 and 95")
        self._backend_factory = backend_factory
        self._validator = validator if validator is not None else FrameValidator()
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
        self._shutdown = False

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
        async with self._single_flight:
            if self._shutdown:
                raise RuntimeError("capture worker is shut down")
            if not manual and kind not in _AUTOMATIC_CAPTURE_KINDS:
                return CaptureResult("disallowed", None, None)
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(self._executor, self._capture_sync)

    async def shutdown(self) -> None:
        async with self._single_flight:
            if self._shutdown:
                return
            self._shutdown = True
            close_error: BaseException | None = None
            try:
                if self._backend is not None:
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(self._executor, self._close_backend_sync)
            except BaseException as error:
                close_error = error
            finally:
                self._executor.shutdown(wait=True, cancel_futures=True)
                with self._retained_lock:
                    self._retained.clear()
                self._previous_usable_hash = None
                self._temporary_directory.cleanup()
            if close_error is not None:
                raise close_error

    def _capture_sync(self) -> CaptureResult:
        if self._backend is None:
            self._backend = self._backend_factory()
        frame = self._backend.capture()
        assessment = self._validator.classify(frame, self._previous_usable_hash)
        if assessment.status != "available":
            return CaptureResult(assessment.status, None, assessment)

        path = self._temp_path / f"{uuid4()}.jpg"
        try:
            Image.fromarray(frame).save(
                path,
                format="JPEG",
                quality=self._jpeg_quality,
            )
        except BaseException:
            path.unlink(missing_ok=True)
            raise

        self._previous_usable_hash = assessment.perceptual_hash
        with self._retained_lock:
            self._retained.append(path)
            while len(self._retained) > self._max_retained:
                self._retained.popleft().unlink(missing_ok=True)
        return CaptureResult("captured", path, assessment)

    def _close_backend_sync(self) -> None:
        if self._backend is None:
            return
        self._backend.close()
