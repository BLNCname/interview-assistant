from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Protocol, Self

import pyaudiowpatch as pyaudio  # type: ignore[import-untyped]


class _DeviceEnumerator(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> object: ...

    def get_device_info_generator(self) -> Iterator[Mapping[str, Any]]: ...


@dataclass(frozen=True)
class AudioDevice:
    id: str
    name: str
    is_loopback: bool
    max_input_channels: int


class AudioDeviceService:
    def __init__(self, pa_factory: Callable[[], _DeviceEnumerator] | None = None) -> None:
        self._pa_factory = pa_factory or pyaudio.PyAudio

    def list_devices(self) -> list[AudioDevice]:
        with self._pa_factory() as pa:
            return [
                AudioDevice(
                    id=str(info["index"]),
                    name=str(info["name"]),
                    is_loopback=bool(info.get("isLoopbackDevice", False)),
                    max_input_channels=int(info["maxInputChannels"]),
                )
                for info in pa.get_device_info_generator()
                if int(info.get("maxInputChannels", 0)) > 0
            ]
