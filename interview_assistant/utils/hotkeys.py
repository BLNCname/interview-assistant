from __future__ import annotations

import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from threading import Condition, Event, Lock, RLock, current_thread, get_ident
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from interview_assistant.events import EventBus


class HotkeyAction(StrEnum):
    FORCE_REQUEST = "force_request"
    SCREENSHOT = "screenshot"
    PAUSE = "pause"
    OVERLAY_VISIBILITY = "overlay_visibility"
    OVERLAY_INTERACTION = "overlay_interaction"
    FORCED_WEB_SEARCH = "forced_web_search"
    CLEAR_ANSWER = "clear_answer"


DEFAULT_HOTKEY_BINDINGS = MappingProxyType(
    {
        HotkeyAction.FORCE_REQUEST: "ctrl+shift+space",
        HotkeyAction.SCREENSHOT: "ctrl+shift+s",
        HotkeyAction.PAUSE: "ctrl+shift+p",
        HotkeyAction.OVERLAY_VISIBILITY: "ctrl+shift+o",
        HotkeyAction.OVERLAY_INTERACTION: "ctrl+shift+i",
        HotkeyAction.FORCED_WEB_SEARCH: "ctrl+shift+w",
        HotkeyAction.CLEAR_ANSWER: "ctrl+shift+c",
    }
)


_MODIFIER_ALIASES = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "ctrl_l": "ctrl",
    "ctrl_r": "ctrl",
    "control_l": "ctrl",
    "control_r": "ctrl",
    "shift": "shift",
    "shift_l": "shift",
    "shift_r": "shift",
    "alt": "alt",
    "alt_l": "alt",
    "alt_r": "alt",
    "alt_gr": "alt",
    "cmd": "win",
    "cmd_l": "win",
    "cmd_r": "win",
    "super": "win",
    "win": "win",
    "windows": "win",
}
_FUNCTION_KEY = re.compile(r"f(?:[1-9]|1[0-9]|2[0-4])\Z")
_MODIFIER_ORDER = ("ctrl", "shift", "alt", "win")
recording_hotkey = Event()
MOUSE_KEYS = ("mouse_left", "mouse_right", "mouse_middle", "mouse_x1", "mouse_x2")
_NAMED_FINAL_KEYS = frozenset(
    {
        "backspace",
        "delete",
        "down",
        "end",
        "enter",
        "esc",
        "escape",
        "home",
        "insert",
        "left",
        "page_down",
        "page_up",
        "pause",
        "right",
        "space",
        "tab",
        "up",
    }
)


def _strip_key_prefix(value: str) -> str:
    normalized = value.strip().casefold()
    if normalized.startswith("key."):
        normalized = normalized[4:]
    if len(normalized) >= 2 and normalized[0] == normalized[-1] == "'":
        normalized = normalized[1:-1]
    return normalized


def _normalize_modifier(value: str) -> str | None:
    return _MODIFIER_ALIASES.get(_strip_key_prefix(value))


def _normalize_final(value: str) -> str:
    normalized = _strip_key_prefix(value)
    normalized = {"escape": "esc", "return": "enter", "del": "delete", "ins": "insert",
                  "pgup": "page_up", "pgdown": "page_down", "+": "plus"}.get(normalized, normalized)
    if (
        (len(normalized) == 1 and normalized.isprintable())
        or _FUNCTION_KEY.fullmatch(normalized)
        or normalized in _NAMED_FINAL_KEYS
        or normalized in MOUSE_KEYS
        or normalized in {"plus", "caps_lock", "num_lock", "scroll_lock", "print_screen", "menu"}
        or re.fullmatch(r"vk_[0-9a-f]{2}", normalized)
    ):
        return normalized
    raise ValueError(f"Unsupported final hotkey key: {value}")


def _event_key_name(key: object) -> str:
    if isinstance(key, str):
        return _strip_key_prefix(key)
    virtual_key = getattr(key, "vk", None)
    if sys.platform == "win32":
        if isinstance(virtual_key, int) and (
            0x30 <= virtual_key <= 0x39 or 0x41 <= virtual_key <= 0x5A
        ):
            # Windows char depends on Ctrl/Shift and the active layout (Ctrl+S
            # can be '\x13' or 'ы'). VK identity also stays stable on release.
            return chr(virtual_key).casefold()
        if isinstance(virtual_key, int) and (0x60 <= virtual_key <= 0x6F or 0xBA <= virtual_key <= 0xE2):
            return f"vk_{virtual_key:02x}"
    character = getattr(key, "char", None)
    if isinstance(character, str) and character:
        return _strip_key_prefix(character)
    name = getattr(key, "name", None)
    if isinstance(name, str) and name:
        try:
            return name if _normalize_modifier(name) else _normalize_final(name)
        except ValueError:
            virtual_key = getattr(getattr(key, "value", None), "vk", virtual_key)
    if isinstance(virtual_key, int) and 0 < virtual_key < 256:
        return f"vk_{virtual_key:02x}"
    return _strip_key_prefix(str(key))


@dataclass(frozen=True, slots=True)
class HotkeyChord:
    keys: frozenset[str]

    @classmethod
    def parse(cls, value: str | HotkeyChord) -> HotkeyChord:
        if isinstance(value, HotkeyChord):
            return value
        raw_tokens = value.split("+")
        if not raw_tokens or any(not token.strip() for token in raw_tokens):
            raise ValueError("Hotkey chord must include a final key")
        tokens = [_normalize_modifier(k) or _normalize_final(k) for k in raw_tokens]
        if len(set(tokens)) != len(tokens):
            raise ValueError("Duplicate key in hotkey")
        return cls(frozenset(tokens))

    def to_portable_text(self) -> str:
        return "+".join([k for k in _MODIFIER_ORDER if k in self.keys] + sorted(self.keys.difference(_MODIFIER_ORDER)))


def normalize_bindings(
    bindings: Mapping[HotkeyAction | str, str | HotkeyChord],
) -> dict[HotkeyAction, HotkeyChord]:
    normalized: dict[HotkeyAction, HotkeyChord] = {}
    reverse: dict[HotkeyChord, HotkeyAction] = {}
    for raw_action, raw_chord in bindings.items():
        action = HotkeyAction(raw_action)
        chord = HotkeyChord.parse(raw_chord)
        if previous := reverse.get(chord):
            raise ValueError(
                f"Duplicate hotkey for {previous.value} and {action.value}"
            )
        normalized[action] = chord
        reverse[chord] = action
    return normalized


class Listener(Protocol):
    def start(self) -> object: ...

    def stop(self) -> object: ...

    def join(self, timeout: float | None = None) -> object: ...


ListenerFactory = Callable[..., Listener]


def _pynput_listener_factory(
    *,
    on_press: Callable[[object], None],
    on_release: Callable[[object], None],
) -> Listener:
    # Deliberately lazy: importing this module or constructing HotkeyManager never
    # opens a Windows hook. Production composition opts in by calling start().
    from pynput import keyboard, mouse  # type: ignore[import-untyped]

    def click(_x, _y, button, pressed, *_extra):
        key = "mouse_" + button.name
        if key in MOUSE_KEYS:
            (on_press if pressed else on_release)(key)

    class InputListeners:
        def __init__(self):
            self.listeners = (keyboard.Listener(on_press=on_press, on_release=on_release),
                              mouse.Listener(on_click=click))

        def start(self):
            for listener in self.listeners:
                listener.start()

        def stop(self):
            for listener in self.listeners:
                listener.stop()

        def join(self, timeout=None):
            for listener in self.listeners:
                if listener.ident is not None and listener is not current_thread():
                    listener.join(timeout)

    return InputListeners()


_SIGNAL_BY_ACTION = {
    HotkeyAction.FORCE_REQUEST: "force_request",
    HotkeyAction.SCREENSHOT: "screenshot_requested",
    HotkeyAction.PAUSE: "pause_toggled",
    HotkeyAction.OVERLAY_VISIBILITY: "overlay_visibility_toggled",
    HotkeyAction.OVERLAY_INTERACTION: "overlay_interaction_toggled",
    HotkeyAction.FORCED_WEB_SEARCH: "forced_search_requested",
    HotkeyAction.CLEAR_ANSWER: "answer_clear_requested",
}

_LifecycleState = Literal["stopped", "starting", "running", "stopping"]


class HotkeyManager:
    """Thread-safe hotkey state machine whose callbacks only emit Qt signals."""

    def __init__(
        self,
        events: EventBus,
        bindings: Mapping[HotkeyAction | str, str | HotkeyChord],
        *,
        listener_factory: ListenerFactory | None = None,
    ) -> None:
        self._events = events
        self._listener_factory = listener_factory or _pynput_listener_factory
        self._lock = RLock()
        self._emission_condition = Condition(self._lock)
        self._lifecycle_condition = Condition(Lock())
        self._lifecycle_state: _LifecycleState = "stopped"
        self._stop_teardown_complete = False
        self._bindings: dict[HotkeyAction, HotkeyChord] = {}
        self._pressed: dict[str, str] = {}
        self._pending: HotkeyAction | None = None
        self._listener: Listener | None = None
        self._generation = 0
        self._active_generation: int | None = None
        self._inflight_emissions: dict[int, int] = {}
        self.update_bindings(bindings)

    @property
    def bindings(self) -> Mapping[HotkeyAction, HotkeyChord]:
        with self._lock:
            return MappingProxyType(dict(self._bindings))

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._listener is not None

    def update_bindings(
        self,
        bindings: Mapping[HotkeyAction | str, str | HotkeyChord],
    ) -> None:
        normalized = normalize_bindings(bindings)

        with self._lock:
            self._bindings = normalized
            self._pressed.clear()
            self._pending = None

    def start(self) -> None:
        caller = get_ident()
        with self._lifecycle_condition:
            while self._lifecycle_state in {"starting", "stopping"}:
                if self._thread_has_inflight_emission(caller):
                    return
                self._lifecycle_condition.wait()
            if self._lifecycle_state == "running":
                return
            self._lifecycle_state = "starting"
            with self._lock:
                self._generation += 1
                generation = self._generation

        try:
            listener = self._listener_factory(
                on_press=lambda key: self._on_press(generation, key),
                on_release=lambda key: self._on_release(generation, key),
            )
        except BaseException:
            self._finish_start_attempt("stopped")
            raise

        with self._lock:
            self._listener = listener
            self._active_generation = generation
        try:
            listener.start()
        except BaseException:
            with self._lock:
                if self._listener is listener:
                    self._listener = None
                    self._active_generation = None
                    self._pressed.clear()
                    self._pending = None
            self._best_effort_stop_and_join(listener)
            self._wait_for_inflight_emissions()
            self._finish_start_attempt("stopped")
            raise
        self._finish_start_attempt("running")

    def stop(self) -> None:
        caller = get_ident()
        with self._lifecycle_condition:
            while True:
                if self._lifecycle_state == "stopped":
                    return
                if self._lifecycle_state in {"starting", "stopping"}:
                    if self._thread_has_inflight_emission(caller):
                        return
                    self._lifecycle_condition.wait()
                    continue

                self._lifecycle_state = "stopping"
                self._stop_teardown_complete = False
                with self._lock:
                    listener = self._listener
                    self._listener = None
                    self._active_generation = None
                    self._pressed.clear()
                    self._pending = None
                break

        stop_error: BaseException | None = None
        join_error: BaseException | None = None
        if listener is not None:
            try:
                listener.stop()
            except BaseException as error:
                stop_error = error
            try:
                listener.join(timeout=1.0)
            except BaseException as error:
                join_error = error

        with self._lifecycle_condition:
            self._stop_teardown_complete = True
            self._lifecycle_condition.notify_all()
        self._wait_for_inflight_emissions()
        self._finalize_stop_if_ready()
        if stop_error is not None:
            raise stop_error
        if join_error is not None:
            raise join_error

    def _finish_start_attempt(self, state: Literal["stopped", "running"]) -> None:
        with self._lifecycle_condition:
            self._lifecycle_state = state
            self._lifecycle_condition.notify_all()

    def _thread_has_inflight_emission(self, thread_id: int) -> bool:
        with self._lock:
            return self._inflight_emissions.get(thread_id, 0) > 0

    def _finalize_stop_if_ready(self) -> None:
        with self._lock:
            emissions_drained = not self._inflight_emissions
        if not emissions_drained:
            return
        with self._lifecycle_condition:
            if self._lifecycle_state != "stopping" or not self._stop_teardown_complete:
                return
            self._lifecycle_state = "stopped"
            self._stop_teardown_complete = False
            self._lifecycle_condition.notify_all()

    @staticmethod
    def _best_effort_stop_and_join(listener: Listener) -> None:
        try:
            listener.stop()
        except BaseException:
            pass
        try:
            listener.join(timeout=1.0)
        except BaseException:
            pass

    def _wait_for_inflight_emissions(self) -> None:
        caller = get_ident()
        with self._emission_condition:
            while any(
                count > 0
                for thread_id, count in self._inflight_emissions.items()
                if thread_id != caller
            ):
                self._emission_condition.wait()

    def _finish_emission(self, thread_id: int) -> None:
        with self._emission_condition:
            remaining = self._inflight_emissions.get(thread_id, 0) - 1
            if remaining > 0:
                self._inflight_emissions[thread_id] = remaining
            else:
                self._inflight_emissions.pop(thread_id, None)
            self._emission_condition.notify_all()
        self._finalize_stop_if_ready()

    def _on_press(self, generation: int, key: object) -> None:
        self._input(generation, key, True)

    def _on_release(self, generation: int, key: object) -> None:
        self._input(generation, key, False)

    def _input(self, generation: int, key: object, pressed: bool) -> None:
        name = _event_key_name(key)
        try:
            token = _normalize_modifier(name) or _normalize_final(name)
        except ValueError:
            return
        with self._lock:
            if generation != self._active_generation:
                return
            if recording_hotkey.is_set():
                self._pressed.clear()
                self._pending = None
                return
            if pressed:
                if name in self._pressed:
                    return
                already_held = token in self._pressed.values()
                self._pressed[name] = token
                if already_held:
                    return
                keys = frozenset(self._pressed.values())
                action = self._match(keys)
                if action is None:
                    literal = getattr(key, "char", None)
                    if isinstance(literal, str) and literal.isprintable():
                        keys = keys.difference({token}).union({literal.casefold()})
                        action = self._match(keys)
                self._pending = None
                # A shorter binding only wins once the user releases a key.
                if action and (keys.issubset(_MODIFIER_ORDER) or any(
                    keys < chord.keys for chord in self._bindings.values()
                )):
                    self._pending = action
                    return
            else:
                if self._pressed.pop(name, None) is None or token in self._pressed.values():
                    return
                action, self._pending = self._pending, None
            if action is None:
                return
            thread_id = get_ident()
            self._inflight_emissions[thread_id] = self._inflight_emissions.get(thread_id, 0) + 1
        try:
            getattr(self._events, _SIGNAL_BY_ACTION[action]).emit()
        finally:
            self._finish_emission(thread_id)

    def _match(self, keys: frozenset[str]) -> HotkeyAction | None:
        return next((action for action, chord in self._bindings.items() if chord.keys == keys), None)
