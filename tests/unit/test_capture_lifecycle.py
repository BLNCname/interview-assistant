from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from threading import Event, Lock, get_ident, main_thread
from typing import Any
from uuid import UUID

import numpy as np
from numpy.typing import NDArray
from PIL import Image
import pytest

from interview_assistant.capture.backend import MSSCaptureBackend
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
            return self._owner.frames[call_index].copy()
        finally:
            with self._owner._lock:
                self._owner.active_captures -= 1

    def close(self) -> None:
        self._owner.close_calls += 1
        self._owner.close_threads.append(get_ident())


class FakeBackendFactory:
    def __init__(
        self,
        frames: Sequence[NDArray[np.uint8]],
        *,
        block_first_capture: bool = False,
    ) -> None:
        self.frames = tuple(frames)
        self.block_first_capture = block_first_capture
        self.factory_calls = 0
        self.capture_calls = 0
        self.close_calls = 0
        self.factory_threads: list[int] = []
        self.capture_threads: list[int] = []
        self.close_threads: list[int] = []
        self.handles: list[FakeCaptureHandle] = []
        self.active_captures = 0
        self.max_active_captures = 0
        self.capture_entered = Event()
        self.release_capture = Event()
        self._lock = Lock()

    def __call__(self) -> FakeCaptureHandle:
        self.factory_calls += 1
        self.factory_threads.append(get_ident())
        handle = FakeCaptureHandle(self)
        self.handles.append(handle)
        return handle


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
    assert after_protected.path is None
    assert worker.retained_paths == (first.path,)
    assert tuple(worker.temp_directory.iterdir()) == (first.path,)
    await worker.shutdown()


async def test_perceptual_duplicate_produces_no_second_jpeg() -> None:
    frame = _visual_frame(28)
    changed = frame.copy()
    changed[50, 80] = 255
    factory = FakeBackendFactory([frame, changed])
    worker = CaptureWorker(backend_factory=factory)

    first = await worker.capture_for_event("screen_analysis")
    duplicate = await worker.capture_for_event("screen_analysis")

    assert first.status == "captured"
    assert duplicate.status == "duplicate"
    assert duplicate.path is None
    assert tuple(worker.temp_directory.iterdir()) == (first.path,)
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
