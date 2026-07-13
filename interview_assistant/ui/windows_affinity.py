from __future__ import annotations

from collections.abc import Callable
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any

WDA_EXCLUDEFROMCAPTURE = 0x00000011
ERROR_GEN_FAILURE = 31


@dataclass(frozen=True)
class AffinityResult:
    ok: bool
    applied_value: int | None
    error_code: int | None


AffinityApplier = Callable[[int], AffinityResult]


def affinity_failure_from_exception(error: Exception) -> AffinityResult:
    for attribute in ("winerror", "errno"):
        error_code = getattr(error, attribute, None)
        if isinstance(error_code, int) and error_code > 0:
            return AffinityResult(False, None, error_code)
    return AffinityResult(False, None, ERROR_GEN_FAILURE)


def _configure_signatures(api: Any) -> None:
    api.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    api.SetWindowDisplayAffinity.restype = wintypes.BOOL
    api.GetWindowDisplayAffinity.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    api.GetWindowDisplayAffinity.restype = wintypes.BOOL


def apply_capture_exclusion(hwnd: int, user32: Any | None = None) -> AffinityResult:
    try:
        api = user32 if user32 is not None else ctypes.WinDLL("user32", use_last_error=True)
        _configure_signatures(api)
        if not api.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE):
            return AffinityResult(False, None, ctypes.get_last_error())
        current = wintypes.DWORD(0)
        if not api.GetWindowDisplayAffinity(hwnd, ctypes.byref(current)):
            return AffinityResult(False, None, ctypes.get_last_error())
    except OSError as error:
        return affinity_failure_from_exception(error)
    return AffinityResult(
        current.value == WDA_EXCLUDEFROMCAPTURE,
        current.value,
        None,
    )
