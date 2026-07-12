from collections.abc import Callable
from queue import Empty
from threading import Event, Thread

import numpy as np
import pytest

from interview_assistant.audio.models import AudioFrame, AudioSource


def test_audio_frames_keep_source_identity() -> None:
    system = AudioFrame(AudioSource.SYSTEM, 1.0, np.zeros(160, dtype=np.float32))
    mic = AudioFrame(AudioSource.MICROPHONE, 1.1, np.ones(160, dtype=np.float32))

    assert system.source is AudioSource.SYSTEM
    assert mic.source is AudioSource.MICROPHONE
    assert not np.array_equal(system.samples, mic.samples)


class FakeDeviceEnumerator:
    def __init__(self, devices: list[dict[str, object]]) -> None:
        self._devices = devices

    def __enter__(self) -> "FakeDeviceEnumerator":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def get_device_info_generator(self):
        yield from self._devices


def test_device_discovery_returns_only_input_capable_devices() -> None:
    from interview_assistant.audio.devices import AudioDevice, AudioDeviceService

    enumerator = FakeDeviceEnumerator(
        [
            {
                "index": 3,
                "name": "Speakers (loopback)",
                "isLoopbackDevice": True,
                "maxInputChannels": 2,
            },
            {
                "index": 4,
                "name": "Microphone",
                "isLoopbackDevice": False,
                "maxInputChannels": 1,
            },
            {
                "index": 5,
                "name": "Output only",
                "isLoopbackDevice": False,
                "maxInputChannels": 0,
            },
        ]
    )

    devices = AudioDeviceService(pa_factory=lambda: enumerator).list_devices()

    assert devices == [
        AudioDevice("3", "Speakers (loopback)", True, 2),
        AudioDevice("4", "Microphone", False, 1),
    ]


class FakeInputStream:
    def __init__(
        self,
        device_id: str,
        callback: Callable[[bytes, int, int], None],
    ) -> None:
        self.device_id = device_id
        self._callback = callback
        self.start_count = 0
        self.stop_count = 0
        self.close_count = 0

    def start_stream(self) -> None:
        self.start_count += 1

    def stop_stream(self) -> None:
        self.stop_count += 1

    def close(self) -> None:
        self.close_count += 1

    def emit(self, samples: np.ndarray, *, sample_rate: int, channels: int) -> None:
        self._callback(samples.astype("<i2").tobytes(), sample_rate, channels)


class FakeAudioBackend:
    def __init__(self) -> None:
        self.streams: dict[str, FakeInputStream] = {}
        self.close_count = 0

    def open_input_stream(
        self,
        *,
        device_id: str,
        frames_per_buffer: int,
        callback: Callable[[bytes, int, int], None],
    ) -> FakeInputStream:
        assert frames_per_buffer > 0
        stream = FakeInputStream(device_id, callback)
        self.streams[device_id] = stream
        return stream

    def close(self) -> None:
        self.close_count += 1


class InactiveStopRaisesStream(FakeInputStream):
    def stop_stream(self) -> None:
        self.stop_count += 1
        if self.start_count == 0:
            raise RuntimeError("stream is not active")


class SecondOpenFailsBackend(FakeAudioBackend):
    def open_input_stream(
        self,
        *,
        device_id: str,
        frames_per_buffer: int,
        callback: Callable[[bytes, int, int], None],
    ) -> FakeInputStream:
        if device_id == "mic":
            raise OSError("microphone cannot be opened")
        assert frames_per_buffer > 0
        stream = InactiveStopRaisesStream(device_id, callback)
        self.streams[device_id] = stream
        return stream


class StartEmitsOrFailsStream(FakeInputStream):
    def __init__(
        self,
        device_id: str,
        callback: Callable[[bytes, int, int], None],
        *,
        emitted_samples: np.ndarray | None = None,
        start_error: Exception | None = None,
    ) -> None:
        super().__init__(device_id, callback)
        self._emitted_samples = emitted_samples
        self._start_error = start_error

    def start_stream(self) -> None:
        super().start_stream()
        if self._emitted_samples is not None:
            self.emit(self._emitted_samples, sample_rate=48_000, channels=1)
        if self._start_error is not None:
            raise self._start_error


class SecondStartFailsBackend(FakeAudioBackend):
    def open_input_stream(
        self,
        *,
        device_id: str,
        frames_per_buffer: int,
        callback: Callable[[bytes, int, int], None],
    ) -> FakeInputStream:
        assert frames_per_buffer > 0
        stream = StartEmitsOrFailsStream(
            device_id,
            callback,
            emitted_samples=(
                np.array([100, 200], dtype=np.int16) if device_id == "system" else None
            ),
            start_error=(
                OSError("microphone cannot be started") if device_id == "mic" else None
            ),
        )
        self.streams[device_id] = stream
        return stream


class ResumeTrackingStream(FakeInputStream):
    def __init__(
        self,
        device_id: str,
        callback: Callable[[bytes, int, int], None],
        *,
        failing_start_count: int | None = None,
        fail_when_stopped: bool = False,
    ) -> None:
        super().__init__(device_id, callback)
        self._failing_start_count = failing_start_count
        self._fail_when_stopped = fail_when_stopped
        self._active = False

    def start_stream(self) -> None:
        self.start_count += 1
        if self._active:
            raise RuntimeError(f"{self.device_id} stream is already active")
        if self.start_count == self._failing_start_count:
            raise OSError("microphone cannot be resumed")
        self._active = True

    def stop_stream(self) -> None:
        self.stop_count += 1
        if not self._active and self._fail_when_stopped:
            raise RuntimeError(f"{self.device_id} stream is not active")
        self._active = False


class ResumeFailsOnceBackend(FakeAudioBackend):
    def open_input_stream(
        self,
        *,
        device_id: str,
        frames_per_buffer: int,
        callback: Callable[[bytes, int, int], None],
    ) -> FakeInputStream:
        assert frames_per_buffer > 0
        stream = ResumeTrackingStream(
            device_id,
            callback,
            failing_start_count=2 if device_id == "mic" else None,
            fail_when_stopped=device_id == "mic",
        )
        self.streams[device_id] = stream
        return stream


class BlockingOpenBackend(FakeAudioBackend):
    def __init__(self, stop_returned: Event) -> None:
        super().__init__()
        self.first_open_started = Event()
        self._stop_returned = stop_returned

    def open_input_stream(
        self,
        *,
        device_id: str,
        frames_per_buffer: int,
        callback: Callable[[bytes, int, int], None],
    ) -> FakeInputStream:
        if device_id == "system":
            self.first_open_started.set()
            self._stop_returned.wait(timeout=0.5)
        return super().open_input_stream(
            device_id=device_id,
            frames_per_buffer=frames_per_buffer,
            callback=callback,
        )


def test_worker_keeps_streams_and_queues_independent() -> None:
    from interview_assistant.audio.worker import AudioWorker

    backend = FakeAudioBackend()
    worker = AudioWorker(
        "system-device",
        "mic-device",
        backend_factory=lambda: backend,
        clock=iter([10.0, 20.0]).__next__,
    )

    worker.start()
    backend.streams["system-device"].emit(
        np.array([100, 200], dtype=np.int16), sample_rate=16_000, channels=1
    )
    backend.streams["mic-device"].emit(
        np.array([300, 400], dtype=np.int16), sample_rate=16_000, channels=1
    )

    system = worker.system_queue.get_nowait()
    microphone = worker.microphone_queue.get_nowait()
    assert backend.streams["system-device"] is not backend.streams["mic-device"]
    assert worker.system_queue is not worker.microphone_queue
    assert system.source is AudioSource.SYSTEM
    assert microphone.source is AudioSource.MICROPHONE
    assert system.timestamp == 10.0
    assert microphone.timestamp == 20.0
    assert not np.array_equal(system.samples, microphone.samples)

    worker.stop()


def test_worker_rejects_one_device_for_both_sources() -> None:
    from interview_assistant.audio.worker import AudioWorker

    with pytest.raises(ValueError, match="distinct"):
        AudioWorker("shared-device", "shared-device", backend_factory=FakeAudioBackend)


def test_worker_converts_downmixes_and_resamples_to_mono_float32() -> None:
    from interview_assistant.audio.worker import AudioWorker

    backend = FakeAudioBackend()
    worker = AudioWorker("system", "mic", backend_factory=lambda: backend)
    stereo_32khz = np.array(
        [
            [32_767, 32_767],
            [0, 0],
            [32_767, -32_768],
            [-32_768, -32_768],
        ],
        dtype=np.int16,
    )

    worker.start()
    backend.streams["system"].emit(stereo_32khz, sample_rate=32_000, channels=2)

    frame = worker.system_queue.get_nowait()
    assert frame.sample_rate == 16_000
    assert frame.samples.dtype == np.float32
    np.testing.assert_allclose(
        frame.samples,
        np.array([32_767 / 32_768, -0.5 / 32_768], dtype=np.float32),
        atol=1e-6,
    )

    worker.stop()


def test_worker_preserves_cumulative_sample_rate_across_callbacks() -> None:
    from interview_assistant.audio.worker import AudioWorker

    backend = FakeAudioBackend()
    worker = AudioWorker(
        "system", "mic", backend_factory=lambda: backend, queue_capacity=64
    )
    chunk = np.zeros(1_024, dtype=np.int16)

    worker.start()
    for _ in range(30):
        backend.streams["system"].emit(chunk, sample_rate=48_000, channels=1)

    output_samples = sum(
        worker.system_queue.get_nowait().samples.size for _ in range(30)
    )
    expected_samples = round(30 * chunk.size * 16_000 / 48_000)
    assert abs(output_samples - expected_samples) <= 1

    worker.stop()


def test_worker_bounds_each_source_queue_without_cross_source_eviction() -> None:
    from interview_assistant.audio.worker import AudioWorker

    backend = FakeAudioBackend()
    worker = AudioWorker(
        "system", "mic", backend_factory=lambda: backend, queue_capacity=1
    )

    worker.start()
    backend.streams["mic"].emit(
        np.array([30], dtype=np.int16), sample_rate=16_000, channels=1
    )
    backend.streams["system"].emit(
        np.array([10], dtype=np.int16), sample_rate=16_000, channels=1
    )
    backend.streams["system"].emit(
        np.array([20], dtype=np.int16), sample_rate=16_000, channels=1
    )

    system = worker.system_queue.get_nowait()
    microphone = worker.microphone_queue.get_nowait()
    assert worker.system_queue.maxsize == 1
    assert worker.microphone_queue.maxsize == 1
    np.testing.assert_allclose(system.samples, np.array([20 / 32_768], dtype=np.float32))
    np.testing.assert_allclose(microphone.samples, np.array([30 / 32_768], dtype=np.float32))

    worker.stop()


def test_worker_marks_internally_evicted_frames_done() -> None:
    from interview_assistant.audio.worker import AudioWorker

    backend = FakeAudioBackend()
    worker = AudioWorker(
        "system", "mic", backend_factory=lambda: backend, queue_capacity=1
    )

    worker.start()
    backend.streams["system"].emit(
        np.array([10], dtype=np.int16), sample_rate=16_000, channels=1
    )
    backend.streams["system"].emit(
        np.array([20], dtype=np.int16), sample_rate=16_000, channels=1
    )

    assert worker.system_queue.unfinished_tasks == 1
    worker.system_queue.get_nowait()
    worker.system_queue.task_done()
    assert worker.system_queue.unfinished_tasks == 0

    worker.stop()


def test_worker_pause_resume_and_stop_lifecycle_is_safe() -> None:
    from interview_assistant.audio.worker import AudioWorker

    backend = FakeAudioBackend()
    worker = AudioWorker("system", "mic", backend_factory=lambda: backend)

    worker.start()
    worker.start()
    worker.pause()
    backend.streams["system"].emit(
        np.array([1], dtype=np.int16), sample_rate=16_000, channels=1
    )
    with pytest.raises(Empty):
        worker.system_queue.get_nowait()

    worker.resume()
    backend.streams["system"].emit(
        np.array([2], dtype=np.int16), sample_rate=16_000, channels=1
    )
    assert worker.system_queue.get_nowait().source is AudioSource.SYSTEM

    worker.stop()
    worker.stop()

    assert set(backend.streams) == {"system", "mic"}
    assert all(stream.start_count == 2 for stream in backend.streams.values())
    assert all(stream.stop_count == 2 for stream in backend.streams.values())
    assert all(stream.close_count == 1 for stream in backend.streams.values())
    assert backend.close_count == 1


def test_worker_failed_resume_stays_paused_rolls_back_and_can_retry() -> None:
    from interview_assistant.audio.worker import AudioWorker

    backend = ResumeFailsOnceBackend()
    worker = AudioWorker("system", "mic", backend_factory=lambda: backend)

    worker.start()
    worker.pause()

    with pytest.raises(OSError, match="microphone cannot be resumed"):
        worker.resume()

    backend.streams["system"].emit(
        np.array([10], dtype=np.int16), sample_rate=16_000, channels=1
    )
    with pytest.raises(Empty):
        worker.system_queue.get_nowait()
    assert all(stream.stop_count == 2 for stream in backend.streams.values())

    worker.resume()

    assert all(stream.start_count == 3 for stream in backend.streams.values())
    backend.streams["system"].emit(
        np.array([20], dtype=np.int16), sample_rate=16_000, channels=1
    )
    assert worker.system_queue.get_nowait().source is AudioSource.SYSTEM

    worker.stop()


def test_worker_preserves_start_error_while_cleaning_partially_opened_streams() -> None:
    from interview_assistant.audio.worker import AudioWorker

    backend = SecondOpenFailsBackend()
    worker = AudioWorker("system", "mic", backend_factory=lambda: backend)

    with pytest.raises(OSError, match="microphone cannot be opened"):
        worker.start()

    assert backend.streams["system"].close_count == 1
    assert backend.close_count == 1


def test_worker_failed_second_stream_start_discards_callbacks_and_can_retry() -> None:
    from interview_assistant.audio.worker import AudioWorker

    failing_backend = SecondStartFailsBackend()
    retry_backend = FakeAudioBackend()
    worker = AudioWorker(
        "system",
        "mic",
        backend_factory=iter([failing_backend, retry_backend]).__next__,
    )

    with pytest.raises(OSError, match="microphone cannot be started"):
        worker.start()

    with pytest.raises(Empty):
        worker.system_queue.get_nowait()

    worker.start()
    retry_backend.streams["system"].emit(
        np.array([300, 400], dtype=np.int16), sample_rate=48_000, channels=1
    )

    frame = worker.system_queue.get_nowait()
    np.testing.assert_allclose(
        frame.samples,
        np.array([300 / 32_768], dtype=np.float32),
    )
    assert failing_backend.close_count == 1

    worker.stop()


def test_concurrent_stop_waits_for_start_then_closes_all_resources() -> None:
    from interview_assistant.audio.worker import AudioWorker

    stop_returned = Event()
    backend = BlockingOpenBackend(stop_returned)
    worker = AudioWorker("system", "mic", backend_factory=lambda: backend)
    start_errors: list[BaseException] = []

    def start_worker() -> None:
        try:
            worker.start()
        except BaseException as error:
            start_errors.append(error)

    def stop_worker() -> None:
        worker.stop()
        stop_returned.set()

    start_thread = Thread(target=start_worker)
    stop_thread = Thread(target=stop_worker)
    start_thread.start()
    assert backend.first_open_started.wait(timeout=1)
    stop_thread.start()
    start_thread.join(timeout=2)
    stop_thread.join(timeout=2)

    assert not start_thread.is_alive()
    assert not stop_thread.is_alive()
    assert start_errors == []
    assert all(stream.close_count == 1 for stream in backend.streams.values())
    assert backend.close_count == 1
