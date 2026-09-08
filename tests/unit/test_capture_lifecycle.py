from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from threading import Event, Lock, Timer, get_ident, main_thread
from typing import Any
from uuid import UUID

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageDraw
import pytest

from interview_assistant.capture.backend import MSSCaptureBackend
from interview_assistant.capture.frame_validator import FrameValidator
from interview_assistant.capture.worker import CaptureWorker
from interview_assistant.transcript.detector import QuestionKind


class FakeMSSHandle:
    def __init__(self) -> None:
        self.monitors = [{"left": 0, "top": 0, "width": 2, "height": 1}]
        self.grabbed_monitor: Any | None = None
        self.close_calls = 0

    def grab(self, monitor: Any) -> np.ndarray:
        self.grabbed_monitor = monitor
        return np.array([[[10, 20, 30, 255], [40, 50, 60, 255]]], dtype=np.uint8)

    def close(self) -> None:
        self.close_calls += 1


def test_mss_backend_captures_virtual_desktop_as_rgb() -> None:
    handle = FakeMSSHandle()
    backend = MSSCaptureBackend(mss_factory=lambda: handle)

    frame = backend.capture()
    backend.close()

    assert handle.grabbed_monitor is handle.monitors[0]
    assert frame.tolist() == [[[30, 20, 10], [60, 50, 40]]]
    assert handle.close_calls == 1


def _visual_frame(seed: int) -> NDArray[np.uint8]:
    return np.random.default_rng(seed).integers(
        32,
        224,
        (120, 160, 3),
        dtype=np.uint8,
    )


class FakeCaptureHandle:
    def __init__(self, owner: FakeBackendFactory) -> None:
        self._owner = owner

    def capture(self) -> NDArray[np.uint8]:
        thread_id = get_ident()
        with self._owner._lock:
            call_index = self._owner.capture_calls
            self._owner.capture_calls += 1
            self._owner.capture_threads.append(thread_id)
            self._owner.active_captures += 1
            self._owner.max_active_captures = max(
                self._owner.max_active_captures,
                self._owner.active_captures,
            )
        try:
            if call_index == 0 and self._owner.block_first_capture:
                self._owner.capture_entered.set()
                if not self._owner.release_capture.wait(timeout=2.0):
                    raise TimeoutError("test did not release fake capture")
            if self._owner.capture_error is not None:
                raise self._owner.capture_error
            return self._owner.frames[call_index].copy()
        finally:
            with self._owner._lock:
                self._owner.active_captures -= 1

    def close(self) -> None:
        self._owner.close_calls += 1
        self._owner.close_threads.append(get_ident())
        if self._owner.close_error is not None:
            raise self._owner.close_error


class FakeBackendFactory:
    def __init__(
        self,
        frames: Sequence[NDArray[np.uint8]],
        *,
        block_factory: bool = False,
        block_first_capture: bool = False,
        factory_error: Exception | None = None,
        capture_error: Exception | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self.frames = tuple(frames)
        self.block_factory = block_factory
        self.block_first_capture = block_first_capture
        self.factory_error = factory_error
        self.capture_error = capture_error
        self.close_error = close_error
        self.factory_calls = 0
        self.capture_calls = 0
        self.close_calls = 0
        self.factory_threads: list[int] = []
        self.capture_threads: list[int] = []
        self.close_threads: list[int] = []
        self.handles: list[FakeCaptureHandle] = []
        self.active_captures = 0
        self.max_active_captures = 0
        self.factory_entered = Event()
        self.release_factory = Event()
        self.capture_entered = Event()
        self.release_capture = Event()
        self._lock = Lock()

    def __call__(self) -> FakeCaptureHandle:
        self.factory_calls += 1
        self.factory_threads.append(get_ident())
        if self.block_factory:
            self.factory_entered.set()
            if not self.release_factory.wait(timeout=2.0):
                raise TimeoutError("test did not release fake factory")
        if self.factory_error is not None:
            raise self.factory_error
        handle = FakeCaptureHandle(self)
        self.handles.append(handle)
        return handle


class PartialFailingSaver:
    def __init__(self, error: OSError) -> None:
        self.error = error
        self.calls = 0
        self.threads: list[int] = []

    def __call__(
        self,
        frame: NDArray[np.uint8],
        path: Path,
        quality: int,
    ) -> None:
        assert frame.shape == (120, 160, 3)
        assert quality == 85
        self.calls += 1
        self.threads.append(get_ident())
        path.write_bytes(b"partial-jpeg")
        raise self.error


@pytest.mark.parametrize("kind", ["coding", "system_design", "screen_analysis"])
async def test_allowed_question_event_captures_once(kind: QuestionKind) -> None:
    factory = FakeBackendFactory([_visual_frame(21)])
    worker = CaptureWorker(backend_factory=factory)

    result = await worker.capture_for_event(kind)

    assert result.status == "captured"
    assert result.path is not None and result.path.exists()
    assert factory.capture_calls == 1
    await worker.shutdown()


async def test_disallowed_events_do_not_capture_but_manual_hotkey_overrides() -> None:
    factory = FakeBackendFactory([_visual_frame(22)])
    worker = CaptureWorker(backend_factory=factory)

    automatic = [
        await worker.capture_for_event(kind)
        for kind in ("theory", "behavioral", "manual")
    ]

    assert [result.status for result in automatic] == ["disallowed"] * 3
    assert factory.factory_calls == 0
    assert tuple(worker.temp_directory.iterdir()) == ()

    manual = await worker.capture_for_event("theory", manual=True)

    assert manual.status == "captured"
    assert factory.factory_calls == 1
    await worker.shutdown()


async def test_one_backend_handle_stays_on_one_dedicated_owner_thread() -> None:
    factory = FakeBackendFactory([_visual_frame(23), _visual_frame(24)])
    worker = CaptureWorker(backend_factory=factory)

    first = await worker.capture_for_event("coding")
    second = await worker.capture_for_event("screen_analysis")
    await worker.shutdown()

    assert [first.status, second.status] == ["captured", "captured"]
    assert factory.factory_calls == 1
    assert len(factory.handles) == 1
    assert factory.close_calls == 1
    owner_thread = factory.factory_threads[0]
    assert owner_thread != main_thread().ident
    assert set(factory.capture_threads) == {owner_thread}
    assert factory.close_threads == [owner_thread]


async def test_concurrent_requests_serialize_the_capture_pipeline() -> None:
    factory = FakeBackendFactory(
        [_visual_frame(25), _visual_frame(26)],
        block_first_capture=True,
    )
    worker = CaptureWorker(backend_factory=factory)
    first = asyncio.create_task(worker.capture_for_event("coding"))
    await asyncio.sleep(0)
    assert factory.capture_entered.wait(timeout=2.0)

    second = asyncio.create_task(worker.capture_for_event("system_design"))
    await asyncio.sleep(0)

    assert factory.capture_calls == 1
    factory.release_capture.set()
    results = await asyncio.gather(first, second)

    assert [result.status for result in results] == ["captured", "captured"]
    assert factory.max_active_captures == 1
    assert len(worker.retained_paths) == 2
    await worker.shutdown()


async def test_protected_frame_is_skipped_without_replacing_usable_prior() -> None:
    available = _visual_frame(27)
    factory = FakeBackendFactory(
        [available, np.zeros_like(available), available.copy()]
    )
    worker = CaptureWorker(backend_factory=factory)

    first = await worker.capture_for_event("coding")
    protected = await worker.capture_for_event("coding")
    after_protected = await worker.capture_for_event("coding")

    assert first.status == "captured"
    assert protected.status == "protected"
    assert protected.path is None
    assert after_protected.status == "duplicate"
    assert after_protected.path == first.path
    assert worker.retained_paths == (first.path,)
    assert tuple(worker.temp_directory.iterdir()) == (first.path,)
    await worker.shutdown()


async def test_identical_pixels_produce_no_second_jpeg() -> None:
    frame = _visual_frame(28)
    factory = FakeBackendFactory([frame, frame.copy()])
    worker = CaptureWorker(backend_factory=factory)

    first = await worker.capture_for_event("screen_analysis")
    duplicate = await worker.capture_for_event("screen_analysis")

    assert first.status == "captured"
    assert duplicate.status == "duplicate"
    assert duplicate.path == first.path
    assert tuple(worker.temp_directory.iterdir()) == (first.path,)
    await worker.shutdown()


async def test_changed_answer_with_duplicate_perceptual_hash_saves_current_pixels() -> None:
    frames = []
    for text in ("answer = 1", "answer = 2"):
        screen = Image.new("RGB", (640, 360), color="white")
        ImageDraw.Draw(screen).text((30, 30), text, fill="black")
        frames.append(np.asarray(screen, dtype=np.uint8))
    validator = FrameValidator()
    previous_hash = validator.classify(frames[0]).perceptual_hash
    assert validator.classify(frames[1], previous_hash).status == "duplicate"
    worker = CaptureWorker(backend_factory=FakeBackendFactory([*frames, frames[1].copy()]))
    try:
        first = await worker.capture_for_event("screen_analysis")
        changed = await worker.capture_for_event("screen_analysis")
        repeated = await worker.capture_for_event("screen_analysis")

        assert first.status == changed.status == "captured"
        assert first.path is not None and changed.path is not None
        assert first.path != changed.path
        assert first.path.read_bytes() != changed.path.read_bytes()
        assert repeated.status == "duplicate"
        assert repeated.path == changed.path
        assert worker.retained_paths == (first.path, changed.path)
    finally:
        await worker.shutdown()


async def test_equal_pixel_bytes_with_different_dimensions_are_a_new_capture() -> None:
    frame = _visual_frame(28)
    reshaped = frame.reshape(160, 120, 3)
    worker = CaptureWorker(backend_factory=FakeBackendFactory([frame, reshaped]))
    try:
        first = await worker.capture_for_event("screen_analysis")
        second = await worker.capture_for_event("screen_analysis")

        assert first.status == second.status == "captured"
        assert second.path is not None and second.path != first.path
        with Image.open(second.path) as image:
            assert image.size == (120, 160)
    finally:
        await worker.shutdown()


async def test_duplicate_with_missing_latest_jpeg_never_falls_back_to_an_older_screen() -> None:
    latest = _visual_frame(28)
    factory = FakeBackendFactory([_visual_frame(27), latest, latest])
    worker = CaptureWorker(backend_factory=factory)
    try:
        first = await worker.capture_for_event("screen_analysis")
        second = await worker.capture_for_event("screen_analysis")
        assert first.path is not None and second.path is not None
        assert first.path != second.path
        second.path.unlink()

        duplicate = await worker.capture_for_event("screen_analysis")

        assert duplicate.status == "duplicate"
        assert duplicate.path is None
        assert first.path.is_file()
    finally:
        await worker.shutdown()


async def test_accepted_frames_are_uuid_named_temporary_jpegs() -> None:
    factory = FakeBackendFactory([_visual_frame(29)])
    worker = CaptureWorker(backend_factory=factory)

    result = await worker.capture_for_event("system_design")

    assert result.path is not None
    assert result.path.parent == worker.temp_directory
    assert result.path.suffix == ".jpg"
    assert UUID(result.path.stem).version == 4
    with Image.open(result.path) as image:
        assert image.format == "JPEG"
        assert image.size == (160, 120)
    assert worker.retained_paths == (result.path,)
    await worker.shutdown()


async def test_buffer_eviction_explicitly_unlinks_oldest_jpeg() -> None:
    factory = FakeBackendFactory(
        [_visual_frame(30), _visual_frame(31), _visual_frame(32)]
    )
    worker = CaptureWorker(backend_factory=factory, max_retained=2)

    results = [
        await worker.capture_for_event("coding")
        for _ in range(3)
    ]
    paths = [result.path for result in results]

    assert all(result.status == "captured" for result in results)
    assert all(isinstance(path, Path) for path in paths)
    first, second, third = paths
    assert first is not None and not first.exists()
    assert second is not None and second.exists()
    assert third is not None and third.exists()
    assert worker.retained_paths == (second, third)
    await worker.shutdown()


async def test_shutdown_closes_owner_resource_and_removes_temp_directory_once() -> None:
    factory = FakeBackendFactory([_visual_frame(33)])
    worker = CaptureWorker(backend_factory=factory)
    result = await worker.capture_for_event("coding")
    temporary_directory = worker.temp_directory

    await worker.shutdown()
    await worker.shutdown()

    assert result.path is not None and not result.path.exists()
    assert not temporary_directory.exists()
    assert worker.retained_paths == ()
    assert factory.close_calls == 1
    assert factory.close_threads == factory.factory_threads


async def test_capture_after_shutdown_fails_clearly_without_opening_backend() -> None:
    factory = FakeBackendFactory([_visual_frame(34)])
    worker = CaptureWorker(backend_factory=factory)
    temporary_directory = worker.temp_directory

    await worker.shutdown()

    with pytest.raises(RuntimeError, match="shut down"):
        await worker.capture_for_event("coding")
    assert factory.factory_calls == 0
    assert not temporary_directory.exists()


async def test_cancelled_slow_factory_is_drained_before_shutdown_finalizes() -> None:
    factory = FakeBackendFactory([_visual_frame(35)], block_factory=True)
    worker = CaptureWorker(backend_factory=factory)
    temporary_directory = worker.temp_directory
    capture = asyncio.create_task(worker.capture_for_event("coding"))
    await asyncio.sleep(0)
    assert factory.factory_entered.wait(timeout=2.0)

    capture.cancel()
    release_timer = Timer(0.1, factory.release_factory.set)
    release_timer.start()
    shutdown = asyncio.create_task(worker.shutdown())
    try:
        with pytest.raises(asyncio.CancelledError):
            await capture
        await shutdown
    finally:
        factory.release_factory.set()
        release_timer.cancel()
        release_timer.join(timeout=1.0)

    assert factory.factory_calls == 1
    assert factory.capture_calls == 1
    assert factory.close_calls == 1
    owner_thread = factory.factory_threads[0]
    assert factory.capture_threads == [owner_thread]
    assert factory.close_threads == [owner_thread]
    assert not temporary_directory.exists()


async def test_repeated_capture_cancellation_cannot_queue_executor_backlog() -> None:
    factory = FakeBackendFactory(
        [_visual_frame(seed) for seed in range(36, 40)],
        block_first_capture=True,
    )
    worker = CaptureWorker(backend_factory=factory)
    temporary_directory = worker.temp_directory
    first = asyncio.create_task(worker.capture_for_event("coding"))
    await asyncio.sleep(0)
    assert factory.capture_entered.wait(timeout=2.0)

    first.cancel()
    await asyncio.sleep(0)
    first.cancel()
    await asyncio.sleep(0)
    cancellation_returned_before_pipeline_settled = first.done()
    extras: list[asyncio.Task[Any]] = []
    for _ in range(3):
        task = asyncio.create_task(worker.capture_for_event("coding"))
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        extras.append(task)

    shutdown = asyncio.create_task(worker.shutdown())
    await asyncio.sleep(0)
    factory.release_capture.set()
    results = await asyncio.gather(first, *extras, return_exceptions=True)
    await shutdown

    assert all(isinstance(result, asyncio.CancelledError) for result in results)
    assert cancellation_returned_before_pipeline_settled is False
    assert factory.capture_calls == 1
    assert factory.close_calls == 1
    assert not temporary_directory.exists()


async def test_cancelled_shutdown_waits_for_cleanup_before_propagating() -> None:
    factory = FakeBackendFactory([_visual_frame(40)], block_first_capture=True)
    worker = CaptureWorker(backend_factory=factory)
    temporary_directory = worker.temp_directory
    capture = asyncio.create_task(worker.capture_for_event("coding"))
    await asyncio.sleep(0)
    assert factory.capture_entered.wait(timeout=2.0)

    shutdown = asyncio.create_task(worker.shutdown())
    await asyncio.sleep(0)
    shutdown.cancel()
    await asyncio.sleep(0)
    shutdown.cancel()
    await asyncio.sleep(0)
    returned_before_capture_settled = shutdown.done()

    factory.release_capture.set()
    await capture
    with pytest.raises(asyncio.CancelledError):
        await shutdown
    cleaned_before_fallback = (
        factory.close_calls == 1 and not temporary_directory.exists()
    )
    if temporary_directory.exists():
        await worker.shutdown()

    assert returned_before_capture_settled is False
    assert cleaned_before_fallback
    assert factory.close_threads == factory.factory_threads


async def test_shutdown_request_rejects_admission_before_lock_is_available() -> None:
    factory = FakeBackendFactory([_visual_frame(41)], block_first_capture=True)
    worker = CaptureWorker(backend_factory=factory)
    capture = asyncio.create_task(worker.capture_for_event("coding"))
    await asyncio.sleep(0)
    assert factory.capture_entered.wait(timeout=2.0)

    shutdown = asyncio.create_task(worker.shutdown())
    await asyncio.sleep(0)
    late_capture = asyncio.create_task(worker.capture_for_event("coding"))
    await asyncio.sleep(0)
    rejected_without_waiting = late_capture.done()
    if not rejected_without_waiting:
        late_capture.cancel()

    factory.release_capture.set()
    await capture
    await shutdown

    assert rejected_without_waiting
    with pytest.raises(RuntimeError, match="shut down"):
        await late_capture
    assert factory.capture_calls == 1


async def test_factory_failure_propagates_and_shutdown_removes_tempdir() -> None:
    error = RuntimeError("factory failed")
    factory = FakeBackendFactory([_visual_frame(42)], factory_error=error)
    worker = CaptureWorker(backend_factory=factory)
    temporary_directory = worker.temp_directory

    with pytest.raises(RuntimeError) as raised:
        await worker.capture_for_event("coding")
    assert raised.value is error
    assert tuple(temporary_directory.iterdir()) == ()

    await worker.shutdown()

    assert factory.factory_calls == 1
    assert factory.capture_calls == 0
    assert factory.close_calls == 0
    assert not temporary_directory.exists()


async def test_capture_failure_propagates_then_owner_thread_closes_backend() -> None:
    error = RuntimeError("capture failed")
    factory = FakeBackendFactory([_visual_frame(43)], capture_error=error)
    worker = CaptureWorker(backend_factory=factory)
    temporary_directory = worker.temp_directory

    with pytest.raises(RuntimeError) as raised:
        await worker.capture_for_event("coding")
    assert raised.value is error
    assert tuple(temporary_directory.iterdir()) == ()

    await worker.shutdown()

    assert factory.close_calls == 1
    assert factory.close_threads == factory.factory_threads
    assert not temporary_directory.exists()


async def test_partial_jpeg_save_is_unlinked_before_error_propagates() -> None:
    error = OSError("jpeg save failed")
    saver = PartialFailingSaver(error)
    factory = FakeBackendFactory([_visual_frame(44)])
    worker = CaptureWorker(backend_factory=factory, frame_saver=saver)
    temporary_directory = worker.temp_directory

    with pytest.raises(OSError) as raised:
        await worker.capture_for_event("coding")
    assert raised.value is error
    assert tuple(temporary_directory.iterdir()) == ()

    await worker.shutdown()

    assert saver.calls == 1
    assert saver.threads == factory.factory_threads
    assert factory.close_threads == factory.factory_threads
    assert not temporary_directory.exists()


async def test_all_shutdown_callers_observe_close_failure_after_cleanup() -> None:
    error = RuntimeError("close failed")
    factory = FakeBackendFactory([_visual_frame(45)], close_error=error)
    worker = CaptureWorker(backend_factory=factory)
    result = await worker.capture_for_event("coding")
    temporary_directory = worker.temp_directory

    first = asyncio.create_task(worker.shutdown())
    second = asyncio.create_task(worker.shutdown())
    outcomes = await asyncio.gather(first, second, return_exceptions=True)

    assert outcomes == [error, error]
    assert result.path is not None and not result.path.exists()
    assert not temporary_directory.exists()
    assert factory.close_calls == 1
    with pytest.raises(RuntimeError) as repeated:
        await worker.shutdown()
    assert repeated.value is error


async def test_close_failure_wins_over_shutdown_caller_cancellation() -> None:
    error = RuntimeError("close failed during cancellation")
    factory = FakeBackendFactory(
        [_visual_frame(46)],
        block_first_capture=True,
        close_error=error,
    )
    worker = CaptureWorker(backend_factory=factory)
    temporary_directory = worker.temp_directory
    capture = asyncio.create_task(worker.capture_for_event("coding"))
    await asyncio.sleep(0)
    assert factory.capture_entered.wait(timeout=2.0)

    shutdown = asyncio.create_task(worker.shutdown())
    await asyncio.sleep(0)
    shutdown.cancel()
    await asyncio.sleep(0)
    factory.release_capture.set()
    await capture
    observed: BaseException | None = None
    try:
        await shutdown
    except asyncio.CancelledError as cancelled:
        observed = cancelled
    except RuntimeError as close_failure:
        observed = close_failure
    cleaned_before_fallback = (
        factory.close_calls == 1 and not temporary_directory.exists()
    )
    if temporary_directory.exists():
        with pytest.raises(RuntimeError):
            await worker.shutdown()

    assert observed is error
    assert cleaned_before_fallback
    assert factory.close_calls == 1
