from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

import mss
import numpy as np
from numpy.typing import NDArray


class CaptureBackend(Protocol):
    def capture(self) -> NDArray[np.uint8]: ...

    def close(self) -> None: ...


class MSSCaptureBackend:
    """Capture the Windows virtual desktop directly through one MSS handle."""

    def __init__(self, *, mss_factory: Callable[[], Any] = mss.mss) -> None:
        self._handle = mss_factory()
        self._closed = False

    def capture(self) -> NDArray[np.uint8]:
        if self._closed:
            raise RuntimeError("capture backend is closed")
        monitors = self._handle.monitors
        if not monitors:
            raise RuntimeError("MSS did not report a virtual desktop")
        bgra = np.asarray(self._handle.grab(monitors[0]), dtype=np.uint8)
        if bgra.ndim != 3 or bgra.shape[2] < 3:
            raise RuntimeError("MSS returned an invalid frame")
        return bgra[..., :3][..., ::-1].copy()

    def close(self) -> None:
        if self._closed:
            return
        self._handle.close()
        self._closed = True
