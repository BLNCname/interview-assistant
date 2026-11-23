from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any


@contextmanager
def wasapi_endpoints() -> Iterator[list[Any]]:
    """Keep Core Audio discovery and recorder COM objects on their owner thread."""
    import pythoncom  # type: ignore[import-untyped]

    initialized = False
    try:
        try:
            pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
            initialized = True
        except pythoncom.com_error as error:
            if error.hresult != -2147417850:  # RPC_E_CHANGED_MODE: already initialized.
                raise
        import soundcard  # type: ignore[import-untyped]

        yield soundcard.all_microphones(include_loopback=True)
    finally:
        if initialized:
            pythoncom.CoUninitialize()


@dataclass(frozen=True)
class AudioDevice:
    id: str
    name: str
    is_loopback: bool
    max_input_channels: int


class AudioDeviceService:
    def list_devices(self) -> list[AudioDevice]:
        with wasapi_endpoints() as endpoints:
            return [
                AudioDevice(item.id, item.name, item.isloopback, item.channels)
                for item in endpoints if item.channels > 0
            ]
