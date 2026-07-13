import ctypes
from collections.abc import Iterator
from ctypes import wintypes
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def fake_user32() -> SimpleNamespace:
    return SimpleNamespace(
        SetWindowDisplayAffinity=Mock(return_value=1),
        GetWindowDisplayAffinity=Mock(return_value=1),
    )


@pytest.fixture(autouse=True)
def _restore_thread_last_error() -> Iterator[None]:
    previous = ctypes.get_last_error()
    try:
        yield
    finally:
        ctypes.set_last_error(previous)


def test_affinity_failure_is_reported(fake_user32: SimpleNamespace) -> None:
    from interview_assistant.ui.windows_affinity import apply_capture_exclusion

    def fail_set(_hwnd: int, _value: int) -> int:
        ctypes.set_last_error(5)
        return 0

    fake_user32.SetWindowDisplayAffinity.side_effect = fail_set
    ctypes.set_last_error(999)

    result = apply_capture_exclusion(123, user32=fake_user32)

    assert not result.ok
    assert result.applied_value is None
    assert result.error_code == 5
    fake_user32.SetWindowDisplayAffinity.assert_called_once_with(123, 0x00000011)
    fake_user32.GetWindowDisplayAffinity.assert_not_called()


def test_exact_capture_exclusion_readback_is_verified(fake_user32: SimpleNamespace) -> None:
    from interview_assistant.ui.windows_affinity import (
        AffinityResult,
        WDA_EXCLUDEFROMCAPTURE,
        apply_capture_exclusion,
    )

    def return_expected_affinity(_hwnd: int, current: object) -> int:
        pointer = ctypes.cast(current, ctypes.POINTER(wintypes.DWORD))
        pointer.contents.value = WDA_EXCLUDEFROMCAPTURE
        return 1

    fake_user32.GetWindowDisplayAffinity.side_effect = return_expected_affinity

    result = apply_capture_exclusion(123, user32=fake_user32)

    assert result == AffinityResult(True, WDA_EXCLUDEFROMCAPTURE, None)
    fake_user32.SetWindowDisplayAffinity.assert_called_once_with(
        123,
        WDA_EXCLUDEFROMCAPTURE,
    )
    fake_user32.GetWindowDisplayAffinity.assert_called_once()


def test_get_failure_is_reported_with_last_error(fake_user32: SimpleNamespace) -> None:
    from interview_assistant.ui.windows_affinity import apply_capture_exclusion

    def fail_get(_hwnd: int, _current: object) -> int:
        ctypes.set_last_error(1400)
        return 0

    fake_user32.GetWindowDisplayAffinity.side_effect = fail_get
    ctypes.set_last_error(999)

    result = apply_capture_exclusion(123, user32=fake_user32)

    assert not result.ok
    assert result.applied_value is None
    assert result.error_code == 1400


def test_oserror_is_normalized_to_typed_failure(fake_user32: SimpleNamespace) -> None:
    from interview_assistant.ui.windows_affinity import AffinityResult, apply_capture_exclusion

    fake_user32.SetWindowDisplayAffinity.side_effect = OSError(5, "access denied")

    try:
        result = apply_capture_exclusion(123, user32=fake_user32)
    except OSError as error:
        pytest.fail(f"OSError escaped the typed wrapper: {error}")

    assert result == AffinityResult(False, None, 5)


def test_win32_calls_use_x64_safe_signatures(fake_user32: SimpleNamespace) -> None:
    from interview_assistant.ui.windows_affinity import apply_capture_exclusion

    fake_user32.SetWindowDisplayAffinity.return_value = 0

    apply_capture_exclusion(123, user32=fake_user32)

    assert fake_user32.SetWindowDisplayAffinity.argtypes == [
        wintypes.HWND,
        wintypes.DWORD,
    ]
    assert fake_user32.SetWindowDisplayAffinity.restype is wintypes.BOOL
    assert fake_user32.GetWindowDisplayAffinity.argtypes == [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    assert fake_user32.GetWindowDisplayAffinity.restype is wintypes.BOOL


def test_high_x64_hwnd_is_not_truncated() -> None:
    from interview_assistant.ui.windows_affinity import (
        AffinityResult,
        WDA_EXCLUDEFROMCAPTURE,
        apply_capture_exclusion,
    )

    high_hwnd = 0x0000001200000123
    captured: dict[str, int] = {}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.DWORD)
    def set_affinity(hwnd: int, value: int) -> int:
        captured["set_hwnd"] = hwnd
        captured["value"] = value
        return 1

    @ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    )
    def get_affinity(hwnd: int, current: object) -> int:
        captured["get_hwnd"] = hwnd
        pointer = ctypes.cast(current, ctypes.POINTER(wintypes.DWORD))
        pointer.contents.value = WDA_EXCLUDEFROMCAPTURE
        return 1

    fake_user32 = SimpleNamespace(
        SetWindowDisplayAffinity=set_affinity,
        GetWindowDisplayAffinity=get_affinity,
    )

    result = apply_capture_exclusion(high_hwnd, user32=fake_user32)

    assert result == AffinityResult(True, WDA_EXCLUDEFROMCAPTURE, None)
    assert captured == {
        "set_hwnd": high_hwnd,
        "get_hwnd": high_hwnd,
        "value": WDA_EXCLUDEFROMCAPTURE,
    }


@pytest.mark.parametrize(
    "reported_value",
    [0x00000000, 0x00000001, 0x00000010, 0xDEADBEEF],
    ids=["WDA_NONE", "WDA_MONITOR", "legacy-unknown", "unknown"],
)
def test_non_exact_readback_is_reported_as_mismatch(
    fake_user32: SimpleNamespace,
    reported_value: int,
) -> None:
    from interview_assistant.ui.windows_affinity import (
        WDA_EXCLUDEFROMCAPTURE,
        apply_capture_exclusion,
    )

    def return_mismatched_affinity(_hwnd: int, current: object) -> int:
        pointer = ctypes.cast(current, ctypes.POINTER(wintypes.DWORD))
        pointer.contents.value = reported_value
        return 1

    fake_user32.GetWindowDisplayAffinity.side_effect = return_mismatched_affinity

    result = apply_capture_exclusion(123, user32=fake_user32)

    assert not result.ok
    assert result.applied_value == reported_value
    assert result.error_code is None
    fake_user32.SetWindowDisplayAffinity.assert_called_once_with(
        123,
        WDA_EXCLUDEFROMCAPTURE,
    )


def test_affinity_result_is_frozen() -> None:
    from interview_assistant.ui.windows_affinity import AffinityResult

    result = AffinityResult(True, 0x00000011, None)

    with pytest.raises(FrozenInstanceError):
        setattr(result, "ok", False)
