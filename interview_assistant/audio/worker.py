from collections.abc import Callable, Iterable
from queue import Empty, Full, Queue
from threading import Lock
from time import monotonic
from typing import Protocol

import numpy as np
import pyaudiowpatch as pyaudio  # type: ignore[import-untyped]
from numpy.typing import NDArray

from .models import AudioFrame, AudioSource

_TARGET_SAMPLE_RATE = 16_000
_DEFAULT_QUEUE_CAPACITY = 32
_DEFAULT_FRAMES_PER_BUFFER = 1_024

_AudioCallback = Callable[[bytes, int, int], None]


class _InputStream(Protocol):
    def start_stream(self) -> None: ...

    def stop_stream(self) -> None: ...

    def close(self) -> None: ...


class _AudioBackend(Protocol):
    def open_input_stream(
        self,
        *,
        device_id: str,
        frames_per_buffer: int,
        callback: _AudioCallback,
    ) -> _InputStream: ...

    def close(self) -> None: ...


class _PyAudioBackend:
    def __init__(self) -> None:
        self._pa = pyaudio.PyAudio()

    def open_input_stream(
        self,
        *,
        device_id: str,
        frames_per_buffer: int,
        callback: _AudioCallback,
    ) -> _InputStream:
        device_index = int(device_id)
        info = self._pa.get_device_info_by_index(device_index)
        channels = int(info.get("maxInputChannels", 0))
        sample_rate = int(round(float(info.get("defaultSampleRate", 0))))
        if channels <= 0:
            raise ValueError(f"audio device {device_id!r} has no input channels")
        if sample_rate <= 0:
            raise ValueError(f"audio device {device_id!r} has an invalid sample rate")

        def on_audio(
            in_data: bytes,
            frame_count: int,
            time_info: dict[str, float],
            status_flags: int,
        ) -> tuple[None, int]:
            del frame_count, time_info, status_flags
            callback(in_data, sample_rate, channels)
            return None, pyaudio.paContinue

        return self._pa.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=sample_rate,
            input=True,
            input_device_index=device_index,
            frames_per_buffer=frames_per_buffer,
            stream_callback=on_audio,
            start=False,
        )

    def close(self) -> None:
        self._pa.terminate()


class AudioWorker:
    def __init__(
        self,
        system_device_id: str,
        microphone_device_id: str,
        *,
        backend_factory: Callable[[], _AudioBackend] | None = None,
        queue_capacity: int = _DEFAULT_QUEUE_CAPACITY,
        frames_per_buffer: int = _DEFAULT_FRAMES_PER_BUFFER,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if queue_capacity <= 0:
            raise ValueError("queue_capacity must be positive")
        if frames_per_buffer <= 0:
            raise ValueError("frames_per_buffer must be positive")
        if system_device_id == microphone_device_id:
            raise ValueError("system and microphone devices must be distinct")

        self._device_ids = {
            AudioSource.SYSTEM: system_device_id,
            AudioSource.MICROPHONE: microphone_device_id,
        }
        self._backend_factory = backend_factory or _PyAudioBackend
        self._frames_per_buffer = frames_per_buffer
        self._clock = clock
        self._queues = {
            source: Queue[AudioFrame](maxsize=queue_capacity) for source in AudioSource
        }
        self._converters = {source: _StreamingAudioConverter() for source in AudioSource}
        self._lifecycle_lock = Lock()
        self._lock = Lock()
        self._backend: _AudioBackend | None = None
        self._streams: dict[AudioSource, _InputStream] = {}
        self._started = False
        self._paused = False
        self._stopped = False

    @property
    def system_queue(self) -> Queue[AudioFrame]:
        return self._queues[AudioSource.SYSTEM]

    @property
    def microphone_queue(self) -> Queue[AudioFrame]:
        return self._queues[AudioSource.MICROPHONE]

    def start(self) -> None:
        with self._lifecycle_lock:
            with self._lock:
                if self._stopped:
                    raise RuntimeError("stopped audio worker cannot be started")
                if self._started:
                    return
                self._started = True

            backend: _AudioBackend | None = None
            streams: dict[AudioSource, _InputStream] = {}
            try:
                backend = self._backend_factory()
                for source, device_id in self._device_ids.items():
                    streams[source] = backend.open_input_stream(
                        device_id=device_id,
                        frames_per_buffer=self._frames_per_buffer,
                        callback=self._callback_for(source),
                    )
                for stream in streams.values():
                    stream.start_stream()
            except BaseException:
                self._close_resources(streams.values(), backend, suppress_errors=True)
                with self._lock:
                    self._started = False
                raise

            with self._lock:
                self._backend = backend
                self._streams = streams

    def pause(self) -> None:
        with self._lifecycle_lock:
            with self._lock:
                if not self._started or self._paused or self._stopped:
                    return
                self._paused = True
                streams = tuple(self._streams.values())
            for stream in streams:
                stream.stop_stream()

    def resume(self) -> None:
        with self._lifecycle_lock:
            with self._lock:
                if not self._started or not self._paused or self._stopped:
                    return
                self._paused = False
                streams = tuple(self._streams.values())
            for stream in streams:
                stream.start_stream()

    def stop(self) -> None:
        with self._lifecycle_lock:
            with self._lock:
                if self._stopped:
                    return
                self._stopped = True
                self._paused = True
                self._started = False
                streams = tuple(self._streams.values())
                backend = self._backend
                self._streams = {}
                self._backend = None
            self._close_resources(streams, backend)

    def _callback_for(self, source: AudioSource) -> _AudioCallback:
        def callback(data: bytes, sample_rate: int, channels: int) -> None:
            samples = self._converters[source].convert(data, sample_rate, channels)
            frame = AudioFrame(source, self._clock(), samples)
            with self._lock:
                if not self._started or self._paused or self._stopped:
                    return
                queue = self._queues[source]
                try:
                    queue.put_nowait(frame)
                except Full:
                    try:
                        queue.get_nowait()
                    except Empty:
                        pass
                    queue.put_nowait(frame)

        return callback

    @staticmethod
    def _close_resources(
        streams: Iterable[_InputStream],
        backend: _AudioBackend | None,
        *,
        suppress_errors: bool = False,
    ) -> None:
        errors: list[Exception] = []
        for stream in streams:
            try:
                stream.stop_stream()
            except Exception as error:
                errors.append(error)
            try:
                stream.close()
            except Exception as error:
                errors.append(error)
        if backend is not None:
            try:
                backend.close()
            except Exception as error:
                errors.append(error)
        if errors and not suppress_errors:
            raise errors[0]


class _StreamingAudioConverter:
    def __init__(self) -> None:
        self._sample_rate: int | None = None
        self._input_position = 0
        self._next_output_position = 0.0
        self._previous_sample: np.float32 | None = None

    def convert(
        self,
        data: bytes,
        sample_rate: int,
        channels: int,
    ) -> NDArray[np.float32]:
        if sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if channels <= 0:
            raise ValueError("channels must be positive")
        if sample_rate != self._sample_rate:
            self._sample_rate = sample_rate
            self._input_position = 0
            self._next_output_position = 0.0
            self._previous_sample = None

        samples = _decode_and_downmix(data, channels)
        if samples.size == 0:
            return samples
        if sample_rate == _TARGET_SAMPLE_RATE:
            self._input_position += samples.size
            self._next_output_position = float(self._input_position)
            self._previous_sample = samples[-1]
            return samples

        start = self._input_position
        end = start + samples.size - 1
        if self._previous_sample is not None and self._next_output_position < start:
            values = np.concatenate(
                (np.array([self._previous_sample], dtype=np.float32), samples)
            )
            source_positions = np.arange(start - 1, end + 1, dtype=np.float64)
        else:
            values = samples
            source_positions = np.arange(start, end + 1, dtype=np.float64)

        step = sample_rate / _TARGET_SAMPLE_RATE
        output_count = max(
            0,
            int(np.floor((end - self._next_output_position) / step + 1e-12)) + 1,
        )
        target_positions = self._next_output_position + np.arange(output_count) * step
        output = np.interp(target_positions, source_positions, values).astype(np.float32)

        self._next_output_position += output_count * step
        self._input_position += samples.size
        self._previous_sample = samples[-1]
        return output


def _decode_and_downmix(data: bytes, channels: int) -> NDArray[np.float32]:
    complete_data = data[: len(data) - (len(data) % np.dtype("<i2").itemsize)]
    samples = np.frombuffer(complete_data, dtype="<i2").astype(np.float32)
    samples /= 32_768.0

    frame_count = samples.size // channels
    samples = samples[: frame_count * channels]
    if channels > 1:
        samples = samples.reshape(frame_count, channels).mean(axis=1, dtype=np.float32)
    return samples
