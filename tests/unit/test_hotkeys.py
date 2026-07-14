from __future__ import annotations

from collections.abc import Callable
from threading import Event, Thread

import pytest

from interview_assistant.events import EventBus
from interview_assistant.utils.hotkeys import (
    HotkeyAction,
    HotkeyChord,
    HotkeyManager,
)


class FakeListener:
    def __init__(
        self,
        on_press: Callable[[object], None],
        on_release: Callable[[object], None],
    ) -> None:
        self.on_press = on_press
        self.on_release = on_release
        self.start_calls = 0
        self.stop_calls = 0
        self.join_calls = 0

    def start(self) -> None:
        self.start_calls += 1

    def stop(self) -> None:
        self.stop_calls += 1

    def join(self, timeout: float | None = None) -> None:
        del timeout
        self.join_calls += 1


class ListenerFactory:
    def __init__(self) -> None:
        self.listeners: list[FakeListener] = []

    def __call__(
        self,
        *,
        on_press: Callable[[object], None],
        on_release: Callable[[object], None],
    ) -> FakeListener:
        listener = FakeListener(on_press, on_release)
        self.listeners.append(listener)
        return listener


def test_chord_normalizes_left_right_modifiers_and_distinguishes_function_keys() -> None:
    left = HotkeyChord.parse("Ctrl_L + Alt_R + F10")
    right = HotkeyChord.parse("control+alt+f10")

    assert left == right
    assert left.modifiers == frozenset({"ctrl", "alt"})
    assert left.key == "f10"
    assert HotkeyChord.parse("ctrl+alt+f10") != HotkeyChord.parse("ctrl+alt+f11")


@pytest.mark.parametrize(
    "invalid",
    [
        "",
        "a",
        "f10",
        "ctrl",
        "ctrl+shift",
        "ctrl+a+b",
        "ctrl++a",
        "ctrl+control+a",
    ],
)
def test_chord_rejects_missing_or_multiple_final_keys(invalid: str) -> None:
    with pytest.raises(ValueError):
        HotkeyChord.parse(invalid)


def test_duplicate_update_is_rejected_transactionally() -> None:
    events = EventBus()
    manager = HotkeyManager(
        events,
        {
            HotkeyAction.FORCE_REQUEST: "ctrl+alt+f10",
            HotkeyAction.SCREENSHOT: "ctrl+alt+f11",
        },
        listener_factory=ListenerFactory(),
    )
    original = manager.bindings

    with pytest.raises(ValueError, match="Duplicate hotkey"):
        manager.update_bindings(
            {
                HotkeyAction.FORCE_REQUEST: "ctrl+alt+f12",
                HotkeyAction.SCREENSHOT: "control+alt+f12",
            }
        )

    assert manager.bindings == original


def test_hotkey_auto_repeat_is_suppressed_until_final_key_release(qtbot) -> None:
    del qtbot
    events = EventBus()
    received: list[str] = []
    events.force_request.connect(lambda: received.append("force"))
    factory = ListenerFactory()
    manager = HotkeyManager(
        events,
        {HotkeyAction.FORCE_REQUEST: "ctrl+alt+f10"},
        listener_factory=factory,
    )
    manager.start()
    listener = factory.listeners[0]

    listener.on_press("Key.ctrl_l")
    listener.on_press("Key.alt_r")
    listener.on_press("Key.f10")
    listener.on_press("Key.f10")
    assert received == ["force"]

    listener.on_release("Key.f10")
    listener.on_press("Key.f10")
    assert received == ["force", "force"]


def test_releasing_one_physical_modifier_side_keeps_other_side_pressed(qtbot) -> None:
    del qtbot
    events = EventBus()
    received: list[str] = []
    events.pause_toggled.connect(lambda: received.append("pause"))
    factory = ListenerFactory()
    manager = HotkeyManager(
        events,
        {HotkeyAction.PAUSE: "ctrl+p"},
        listener_factory=factory,
    )
    manager.start()
    listener = factory.listeners[0]

    listener.on_press("Key.ctrl_l")
    listener.on_press("Key.ctrl_r")
    listener.on_release("Key.ctrl_l")
    listener.on_press("p")

    assert received == ["pause"]
    manager.stop()


def test_all_actions_emit_only_their_event_bus_signal(qtbot) -> None:
    del qtbot
    events = EventBus()
    received: list[str] = []
    signal_names = {
        HotkeyAction.FORCE_REQUEST: "force_request",
        HotkeyAction.SCREENSHOT: "screenshot_requested",
        HotkeyAction.PAUSE: "pause_toggled",
        HotkeyAction.OVERLAY_VISIBILITY: "overlay_visibility_toggled",
        HotkeyAction.FORCED_WEB_SEARCH: "forced_search_requested",
        HotkeyAction.CLEAR_ANSWER: "answer_clear_requested",
    }
    for action, signal_name in signal_names.items():
        getattr(events, signal_name).connect(lambda name=signal_name: received.append(name))

    factory = ListenerFactory()
    bindings = {
        action: f"ctrl+alt+f{index}"
        for index, action in enumerate(signal_names, start=7)
    }
    manager = HotkeyManager(events, bindings, listener_factory=factory)
    manager.start()
    listener = factory.listeners[0]
    listener.on_press("Key.ctrl_r")
    listener.on_press("Key.alt_l")
    for index, expected in enumerate(signal_names.values(), start=7):
        listener.on_press(f"Key.f{index}")
        listener.on_release(f"Key.f{index}")
        assert received[-1] == expected

    assert received == list(signal_names.values())


def test_listener_lifecycle_is_idempotent_and_can_restart() -> None:
    factory = ListenerFactory()
    manager = HotkeyManager(
        EventBus(),
        {HotkeyAction.PAUSE: "ctrl+alt+p"},
        listener_factory=factory,
    )

    manager.start()
    manager.start()
    assert len(factory.listeners) == 1
    assert factory.listeners[0].start_calls == 1
    assert manager.is_running

    manager.stop()
    manager.stop()
    assert factory.listeners[0].stop_calls == 1
    assert factory.listeners[0].join_calls == 1
    assert not manager.is_running

    manager.start()
    assert len(factory.listeners) == 2
    assert manager.is_running
    manager.stop()


def test_stale_listener_callbacks_are_inert_after_stop_and_restart(qtbot) -> None:
    del qtbot
    factory = ListenerFactory()
    events = EventBus()
    received: list[str] = []
    events.pause_toggled.connect(lambda: received.append("pause"))
    manager = HotkeyManager(
        events,
        {HotkeyAction.PAUSE: "ctrl+p"},
        listener_factory=factory,
    )
    manager.start()
    old_listener = factory.listeners[0]
    manager.stop()
    manager.start()
    new_listener = factory.listeners[1]

    old_listener.on_press("Key.ctrl_l")
    old_listener.on_press("p")
    assert received == []
    new_listener.on_press("Key.ctrl_r")
    new_listener.on_press("p")
    assert received == ["pause"]
    manager.stop()


def test_start_failure_rolls_back_and_best_effort_cleans_partial_listener() -> None:
    class FailingStartListener(FakeListener):
        def start(self) -> None:
            super().start()
            raise RuntimeError("hook failed")

    listener: FailingStartListener | None = None

    def factory(*, on_press, on_release) -> FailingStartListener:
        nonlocal listener
        listener = FailingStartListener(on_press, on_release)
        return listener

    manager = HotkeyManager(
        EventBus(),
        {HotkeyAction.PAUSE: "ctrl+p"},
        listener_factory=factory,
    )

    with pytest.raises(RuntimeError, match="hook failed"):
        manager.start()

    assert listener is not None
    assert listener.stop_calls == 1
    assert listener.join_calls == 1
    assert not manager.is_running


def test_stop_joins_even_when_listener_stop_raises() -> None:
    class FailingStopListener(FakeListener):
        def stop(self) -> None:
            super().stop()
            raise RuntimeError("stop failed")

    listener: FailingStopListener | None = None

    def factory(*, on_press, on_release) -> FailingStopListener:
        nonlocal listener
        listener = FailingStopListener(on_press, on_release)
        return listener

    manager = HotkeyManager(
        EventBus(),
        {HotkeyAction.PAUSE: "ctrl+p"},
        listener_factory=factory,
    )
    manager.start()

    with pytest.raises(RuntimeError, match="stop failed"):
        manager.stop()

    assert listener is not None
    assert listener.join_calls == 1
    assert not manager.is_running


def test_stop_waits_for_inflight_signal_emission_to_finish() -> None:
    emit_entered = Event()
    release_emit = Event()
    listener_stopped = Event()
    stop_returned = Event()
    timeline: list[str] = []

    class BlockingSignal:
        def emit(self) -> None:
            emit_entered.set()
            assert release_emit.wait(1.0)
            timeline.append("emitted")

    class BlockingEvents:
        pause_toggled = BlockingSignal()

    class ObservableStopListener(FakeListener):
        def stop(self) -> None:
            super().stop()
            listener_stopped.set()

    def factory(*, on_press, on_release) -> ObservableStopListener:
        return ObservableStopListener(on_press, on_release)

    manager = HotkeyManager(  # type: ignore[arg-type]
        BlockingEvents(),
        {HotkeyAction.PAUSE: "ctrl+p"},
        listener_factory=factory,
    )
    manager.start()
    listener = manager._listener
    assert isinstance(listener, ObservableStopListener)
    listener.on_press("Key.ctrl_l")

    callback_thread = Thread(target=listener.on_press, args=("p",))
    callback_thread.start()
    assert emit_entered.wait(1.0)

    def stop_manager() -> None:
        manager.stop()
        timeline.append("stopped")
        stop_returned.set()

    stop_thread = Thread(target=stop_manager)
    stop_thread.start()
    assert listener_stopped.wait(1.0)
    returned_before_emit_finished = stop_returned.is_set()

    release_emit.set()
    callback_thread.join(1.0)
    stop_thread.join(1.0)

    assert not callback_thread.is_alive()
    assert not stop_thread.is_alive()
    assert not returned_before_emit_finished
    assert timeline == ["emitted", "stopped"]
    assert not manager.is_running
