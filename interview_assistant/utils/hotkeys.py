from __future__ import annotations

import re
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from threading import Condition, Lock, RLock, get_ident
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
    if normalized == "escape":
        normalized = "esc"
    if (
        len(normalized) == 1
        or _FUNCTION_KEY.fullmatch(normalized)
        or normalized in _NAMED_FINAL_KEYS
    ):
        return normalized
    raise ValueError(f"Unsupported final hotkey key: {value}")


def _event_key_name(key: object) -> str:
    if isinstance(key, str):
        return _strip_key_prefix(key)
    if sys.platform == "win32":
        virtual_key = getattr(key, "vk", None)
        if isinstance(virtual_key, int) and (
            0x30 <= virtual_key <= 0x39 or 0x41 <= virtual_key <= 0x5A
        ):
            # Windows char depends on Ctrl/Shift and the active layout (Ctrl+S
            # can be '\x13' or 'ы'). VK identity also stays stable on release.
            return chr(virtual_key).casefold()
    character = getattr(key, "char", None)
    if isinstance(character, str) and character:
        return _strip_key_prefix(character)
    name = getattr(key, "name", None)
    if isinstance(name, str) and name:
        return _strip_key_prefix(name)
    return _strip_key_prefix(str(key))


@dataclass(frozen=True, slots=True)
class HotkeyChord:
    modifiers: frozenset[str]
    key: str

    def __post_init__(self) -> None:
        modifiers = frozenset(self.modifiers)
        object.__setattr__(self, "modifiers", modifiers)
        if not modifiers:
            raise ValueError("Global hotkey chord must include a modifier")
        if any(modifier not in {"ctrl", "shift", "alt", "win"} for modifier in modifiers):
            raise ValueError("Hotkey chord contains a non-canonical modifier")
        if _normalize_final(self.key) != self.key:
            raise ValueError("Hotkey chord final key must be canonical")

    @classmethod
    def parse(cls, value: str | HotkeyChord) -> HotkeyChord:
        if isinstance(value, HotkeyChord):
            return value
        raw_tokens = value.split("+")
        if not raw_tokens or any(not token.strip() for token in raw_tokens):
            raise ValueError("Hotkey chord must include a final key")
        tokens = [token.strip() for token in raw_tokens]
        modifiers: set[str] = set()
        final_keys: list[str] = []
        for token in tokens:
            modifier = _normalize_modifier(token)
            if modifier is not None:
                if modifier in modifiers:
                    raise ValueError(f"Duplicate hotkey modifier: {modifier}")
                modifiers.add(modifier)
            else:
                final_keys.append(_normalize_final(token))
        if len(final_keys) != 1:
            raise ValueError("Hotkey chord must include exactly one final key")
        if not modifiers:
            raise ValueError("Global hotkey chord must include a modifier")
        return cls(frozenset(modifiers), final_keys[0])

    def to_portable_text(self) -> str:
        ordered = [name for name in _MODIFIER_ORDER if name in self.modifiers]
        return "+".join((*ordered, self.key))


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
    from pynput import keyboard  # type: ignore[import-untyped]

    return keyboard.Listener(on_press=on_press, on_release=on_release)


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
        self._pressed_modifier_keys: dict[str, set[str]] = {}
        self._latched_final_keys: set[str] = set()
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
            self._pressed_modifier_keys.clear()
            self._latched_final_keys.clear()

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
                    self._pressed_modifier_keys.clear()
                    self._latched_final_keys.clear()
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
                    self._pressed_modifier_keys.clear()
                    self._latched_final_keys.clear()
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
        name = _event_key_name(key)
        modifier = _normalize_modifier(name)
        action: HotkeyAction | None = None
        emission_thread: int | None = None
        with self._lock:
            if generation != self._active_generation:
                return
            if modifier is not None:
                self._pressed_modifier_keys.setdefault(modifier, set()).add(name)
                return
            try:
                final_key = _normalize_final(name)
            except ValueError:
                return
            if final_key in self._latched_final_keys:
                return
            if not self._pressed_modifier_keys:
                return
            chord = HotkeyChord(frozenset(self._pressed_modifier_keys), final_key)
            action = next(
                (
                    candidate
                    for candidate, binding in self._bindings.items()
                    if binding == chord
                ),
                None,
            )
            if action is None and sys.platform == "win32":
                # Keep explicitly configured character shortcuts (e.g. ctrl+ы
                # or ctrl+shift+!) when no binding uses the stable VK name.
                character = getattr(key, "char", None)
                if isinstance(character, str) and character:
                    try:
                        literal = HotkeyChord(chord.modifiers, _normalize_final(character))
                    except ValueError:
                        pass
                    else:
                        action = next(
                            (candidate for candidate, binding in self._bindings.items()
                             if binding == literal),
                            None,
                        )
            if action is not None:
                self._latched_final_keys.add(final_key)
                emission_thread = get_ident()
                self._inflight_emissions[emission_thread] = (
                    self._inflight_emissions.get(emission_thread, 0) + 1
                )
        if action is not None:
            assert emission_thread is not None
            try:
                signal_name = _SIGNAL_BY_ACTION[action]
                getattr(self._events, signal_name).emit()
            finally:
                self._finish_emission(emission_thread)

    def _on_release(self, generation: int, key: object) -> None:
        name = _event_key_name(key)
        modifier = _normalize_modifier(name)
        with self._lock:
            if generation != self._active_generation:
                return
            if modifier is not None:
                physical_keys = self._pressed_modifier_keys.get(modifier)
                if physical_keys is not None:
                    physical_keys.discard(name)
                    if not physical_keys:
                        self._pressed_modifier_keys.pop(modifier, None)
                return
            try:
                final_key = _normalize_final(name)
            except ValueError:
                return
            self._latched_final_keys.discard(final_key)
