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


def test_external_stop_and_reentrant_emitter_stop_do_not_deadlock() -> None:
    emit_entered = Event()
    release_reentrant_stop = Event()
    listener_stopped = Event()
    reentrant_stop_returned = Event()
    manager: HotkeyManager

    class ReentrantSignal:
        def emit(self) -> None:
            emit_entered.set()
            assert release_reentrant_stop.wait(1.0)
            manager.stop()
            reentrant_stop_returned.set()

    class ReentrantEvents:
        pause_toggled = ReentrantSignal()

    class ObservableStopListener(FakeListener):
        def stop(self) -> None:
            super().stop()
            listener_stopped.set()

    def factory(*, on_press, on_release) -> ObservableStopListener:
        return ObservableStopListener(on_press, on_release)

    manager = HotkeyManager(  # type: ignore[arg-type]
        ReentrantEvents(),
        {HotkeyAction.PAUSE: "ctrl+p"},
        listener_factory=factory,
    )
    manager.start()
    listener = manager._listener
    assert isinstance(listener, ObservableStopListener)
    listener.on_press("Key.ctrl_l")
    callback_thread = Thread(target=listener.on_press, args=("p",), daemon=True)
    callback_thread.start()
    assert emit_entered.wait(1.0)

    stop_thread = Thread(target=manager.stop, daemon=True)
    stop_thread.start()
    assert listener_stopped.wait(1.0)
    release_reentrant_stop.set()
    callback_thread.join(0.5)
    stop_thread.join(0.5)

    assert reentrant_stop_returned.is_set()
    assert not callback_thread.is_alive()
    assert not stop_thread.is_alive()
    assert not manager.is_running


def test_reentrant_emitter_stop_keeps_barrier_visible_to_external_stop() -> None:
    reentrant_stop_returned = Event()
    release_emit = Event()
    external_stop_started = Event()
    external_stop_returned = Event()
    manager: HotkeyManager

    class ReentrantBlockingSignal:
        def emit(self) -> None:
            manager.stop()
            reentrant_stop_returned.set()
            assert release_emit.wait(1.0)

    class ReentrantEvents:
        pause_toggled = ReentrantBlockingSignal()

    factory = ListenerFactory()
    manager = HotkeyManager(  # type: ignore[arg-type]
        ReentrantEvents(),
        {HotkeyAction.PAUSE: "ctrl+p"},
        listener_factory=factory,
    )
    manager.start()
    listener = factory.listeners[0]
    listener.on_press("Key.ctrl_l")
    callback_thread = Thread(target=listener.on_press, args=("p",), daemon=True)
    callback_thread.start()
    assert reentrant_stop_returned.wait(1.0)

    def stop_externally() -> None:
        external_stop_started.set()
        manager.stop()
        external_stop_returned.set()

    external_thread = Thread(target=stop_externally, daemon=True)
    external_thread.start()
    assert external_stop_started.wait(1.0)
    returned_before_emit_finished = external_stop_returned.wait(0.05)

    release_emit.set()
    callback_thread.join(0.5)
    external_thread.join(0.5)

    assert not returned_before_emit_finished
    assert external_stop_returned.is_set()
    assert not callback_thread.is_alive()
    assert not external_thread.is_alive()
    assert not manager.is_running


def test_start_waits_until_stop_drain_is_fully_finalized() -> None:
    emit_entered = Event()
    release_emit = Event()
    listener_stopped = Event()
    second_factory_called = Event()

    class BlockingSignal:
        def emit(self) -> None:
            emit_entered.set()
            assert release_emit.wait(1.0)

    class BlockingEvents:
        pause_toggled = BlockingSignal()

    class ObservableStopListener(FakeListener):
        def stop(self) -> None:
            super().stop()
            listener_stopped.set()

    listeners: list[ObservableStopListener] = []

    def factory(*, on_press, on_release) -> ObservableStopListener:
        listener = ObservableStopListener(on_press, on_release)
        listeners.append(listener)
        if len(listeners) == 2:
            second_factory_called.set()
        return listener

    manager = HotkeyManager(  # type: ignore[arg-type]
        BlockingEvents(),
        {HotkeyAction.PAUSE: "ctrl+p"},
        listener_factory=factory,
    )
    manager.start()
    first_listener = listeners[0]
    first_listener.on_press("Key.ctrl_l")
    callback_thread = Thread(target=first_listener.on_press, args=("p",), daemon=True)
    callback_thread.start()
    assert emit_entered.wait(1.0)

    stop_thread = Thread(target=manager.stop, daemon=True)
    stop_thread.start()
    assert listener_stopped.wait(1.0)
    start_thread = Thread(target=manager.start, daemon=True)
    start_thread.start()
    restarted_before_emit_finished = second_factory_called.wait(0.05)

    release_emit.set()
    callback_thread.join(0.5)
    stop_thread.join(0.5)
    start_thread.join(0.5)

    assert not restarted_before_emit_finished
    assert second_factory_called.is_set()
    assert not callback_thread.is_alive()
    assert not stop_thread.is_alive()
    assert not start_thread.is_alive()
    assert manager.is_running
    manager.stop()


def test_start_failure_cleanup_does_not_deadlock_reentrant_emitter_stop() -> None:
    emit_entered = Event()
    release_reentrant_stop = Event()
    reentrant_stop_returned = Event()
    manager: HotkeyManager

    class ReentrantSignal:
        def emit(self) -> None:
            emit_entered.set()
            assert release_reentrant_stop.wait(1.0)
            manager.stop()
            reentrant_stop_returned.set()

    class ReentrantEvents:
        pause_toggled = ReentrantSignal()

    class FailingStartListener(FakeListener):
        callback_thread: Thread | None = None

        def start(self) -> None:
            super().start()

            def emit_hotkey() -> None:
                self.on_press("Key.ctrl_l")
                self.on_press("p")

            self.callback_thread = Thread(target=emit_hotkey, daemon=True)
            self.callback_thread.start()
            assert emit_entered.wait(1.0)
            release_reentrant_stop.set()
            raise RuntimeError("start exploded")

        def join(self, timeout: float | None = None) -> None:
            super().join(timeout)
            assert self.callback_thread is not None
            self.callback_thread.join(timeout)

    listener: FailingStartListener | None = None

    def factory(*, on_press, on_release) -> FailingStartListener:
        nonlocal listener
        listener = FailingStartListener(on_press, on_release)
        return listener

    manager = HotkeyManager(  # type: ignore[arg-type]
        ReentrantEvents(),
        {HotkeyAction.PAUSE: "ctrl+p"},
        listener_factory=factory,
    )

    with pytest.raises(RuntimeError, match="start exploded"):
        manager.start()

    assert listener is not None
    assert listener.callback_thread is not None
    assert not listener.callback_thread.is_alive()
    assert reentrant_stop_returned.is_set()
    assert listener.stop_calls == 1
    assert listener.join_calls == 1
    assert not manager.is_running
