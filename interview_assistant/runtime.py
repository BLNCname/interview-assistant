from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Literal, Protocol

from PyQt6.QtCore import QObject, pyqtSlot

from interview_assistant.app import InterviewApplication
from interview_assistant.audio.models import AudioSource
from interview_assistant.capture.worker import CaptureResult
from interview_assistant.config import AppConfig
from interview_assistant.context.builder import ContextBuilder
from interview_assistant.lmstudio.lifecycle import ModelLifecycle, RecoveryRequest
from interview_assistant.lmstudio.models import ModelInstance
from interview_assistant.lmstudio.payload import build_context_payload as build_chat_payload
from interview_assistant.orchestration.coordinator import (
    RequestCoordinator,
    RequestOutcome,
)
from interview_assistant.retrieval.models import CONTEXT7_ID, FIRECRAWL_ID, SearchIntegration
from interview_assistant.retrieval.policy import SearchPolicy
from interview_assistant.state import ApplicationState
from interview_assistant.stt.engine import TranscriptHypothesis
from interview_assistant.transcript.detector import (
    DetectedQuestion,
    QuestionDetector,
    QuestionKind,
)
from interview_assistant.transcript.store import TranscriptStore
from interview_assistant.utils.hotkeys import HotkeyAction


def _retrieval_error_type(error: Exception) -> str:
    from interview_assistant.retrieval.mcp_client import NativeMCPError

    if isinstance(error, TimeoutError):
        return "retrieval_timeout"
    if isinstance(error, NativeMCPError) and error.code in (
        "key_required", "credentials", "credits", "rate_limit", "timeout",
    ):
        return f"retrieval_{error.code}"
    return "retrieval_unavailable"


def _retrieval_unavailable_message(error_type: str | None, integration_id: str) -> str:
    # Use finite error codes and fixed labels, never an upstream exception body.
    provider = {FIRECRAWL_ID: "Firecrawl", CONTEXT7_ID: "Context7"}.get(integration_id, "Search")
    key_name = "FIRECRAWL_API_KEY" if integration_id == FIRECRAWL_ID else "CONTEXT7_API_KEY"
    reasons = {
        "retrieval_key_required": f"{provider} requires an API key (configure {key_name})",
        "retrieval_credentials": f"{provider} API key is missing or invalid (check {key_name})",
        "retrieval_credits": f"{provider} credits or quota are exhausted",
        "retrieval_rate_limit": f"{provider} rate limit reached",
        "retrieval_timeout": f"{provider} request timed out",
        "timeout": f"{provider} request timed out",
    }
    reason = reasons.get(error_type or "")
    if reason is None:
        return "Web retrieval unavailable; continuing without it."
    return f"Web retrieval unavailable: {reason}; continuing without it."


class AudioService(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...

    def pause(self) -> None: ...

    def resume(self) -> None: ...


class STTService(Protocol):
    def start(self) -> None: ...

    def stop(self, timeout: float = 5.0) -> bool: ...


class HotkeyService(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...

    def update_bindings(self, bindings: Mapping[HotkeyAction | str, str]) -> None: ...


class CaptureService(Protocol):
    async def capture_for_event(
        self,
        kind: QuestionKind,
        *,
        manual: bool = False,
    ) -> CaptureResult: ...

    async def shutdown(self) -> None: ...


class RegistryService(Protocol):
    async def ensure_ready(
        self,
        key: str,
        refresh: bool = False,
    ) -> ModelInstance: ...

    async def refresh(self, key: str) -> ModelInstance: ...


class ClientService(Protocol):
    async def aclose(self) -> None: ...


class RetrievalService(Protocol):
    async def retrieve(self, integration: SearchIntegration) -> str: ...

    async def aclose(self) -> None: ...


WarmUp = Callable[[ModelInstance], Awaitable[None]]
Sleeper = Callable[[float], Awaitable[None]]
HypothesisConsumer = Callable[[TranscriptHypothesis], object]


class ThreadsafeScheduler(Protocol):
    def call_soon_threadsafe(self, callback: Callable[[], None]) -> object: ...


async def _noop_warm_up(_instance: ModelInstance) -> None:
    return None


class RuntimeHypothesisIngress:
    """Coalesce worker-thread hypotheses before handing them to the event loop."""

    def __init__(
        self,
        loop: ThreadsafeScheduler,
        *,
        capacity: int = 32,
    ) -> None:
        if capacity <= 0:
            raise ValueError("Hypothesis ingress capacity must be positive")
        self._loop = loop
        self._capacity = capacity
        self._lock = Lock()
        self._consumer: HypothesisConsumer | None = None
        self._finals: deque[TranscriptHypothesis] = deque()
        self._partials: OrderedDict[AudioSource, TranscriptHypothesis] = OrderedDict()
        self._scheduled = False
        self._closed = False
        self._partial_drop_count = 0
        self._final_overflow_count = 0

    @property
    def partial_drop_count(self) -> int:
        with self._lock:
            return self._partial_drop_count

    @property
    def final_overflow_count(self) -> int:
        with self._lock:
            return self._final_overflow_count

    def bind(self, consumer: HypothesisConsumer) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("Hypothesis ingress is closed")
            if self._consumer is not None:
                raise RuntimeError("Hypothesis ingress is already bound")
            self._consumer = consumer

    def publish(self, hypothesis: TranscriptHypothesis) -> None:
        with self._lock:
            if self._closed:
                return
            if hypothesis.is_final:
                if self._partials.pop(hypothesis.source, None) is not None:
                    self._partial_drop_count += 1
                while self._pending_count() >= self._capacity and self._partials:
                    self._partials.popitem(last=False)
                    self._partial_drop_count += 1
                if self._pending_count() >= self._capacity:
                    self._final_overflow_count += 1
                    return
                self._finals.append(hypothesis)
            elif hypothesis.source in self._partials:
                self._partials[hypothesis.source] = hypothesis
            elif self._pending_count() >= self._capacity:
                self._partial_drop_count += 1
                return
            else:
                self._partials[hypothesis.source] = hypothesis
            if self._scheduled:
                return
            self._scheduled = True
        self._loop.call_soon_threadsafe(self._drain)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._consumer = None
            self._finals.clear()
            self._partials.clear()

    def _pending_count(self) -> int:
        return len(self._finals) + len(self._partials)

    def _drain(self) -> None:
        with self._lock:
            pending = tuple(self._finals) + tuple(self._partials.values())
            self._finals.clear()
            self._partials.clear()
            self._scheduled = False
            consumer = self._consumer
            closed = self._closed
        if closed or consumer is None:
            return
        for hypothesis in pending:
            try:
                consumer(hypothesis)
            except RuntimeError:
                # A shutdown may close the runtime after this drain was queued.
                return


@dataclass(slots=True)
class RuntimeServices:
    audio: AudioService
    stt: STTService
    hotkeys: HotkeyService
    capture: CaptureService
    registry: RegistryService
    client: ClientService
    coordinator: RequestCoordinator
    transcript_store: TranscriptStore
    question_detector: QuestionDetector
    search_policy: SearchPolicy
    warm_up: WarmUp = _noop_warm_up
    recovery_sleeper: Sleeper = asyncio.sleep
    hypothesis_ingress: RuntimeHypothesisIngress | None = None
    retrieval: RetrievalService | None = None


@dataclass(frozen=True, slots=True)
class _PreparedRequest:
    question: DetectedQuestion
    image_path: Path | None
    stage: Literal["answer", "retrieval"] = "answer"
    integration: SearchIntegration | None = None


class ApplicationRuntime(QObject):
    """Main-thread orchestration for one configured interview session."""

    def __init__(
        self,
        application: InterviewApplication,
        config: AppConfig,
        services: RuntimeServices,
    ) -> None:
        super().__init__()
        self.application = application
        self.config = config
        self.services = services
        self._instances: dict[str, ModelInstance] = {}
        self._started = False
        self._closing = False
        self._question_task: asyncio.Task[None] | None = None
        self._shutdown_task: asyncio.Task[None] | None = None
        self._shutdown_completed: set[str] = set()
        self._action_tasks: set[asyncio.Task[None]] = set()
        self._manual_image_path: Path | None = None
        self._manual_capture_generation = 0
        self._paused = False
        self._desired_state = ApplicationState.STARTING
        self._recovered_search_results: dict[int, str] = {}
        self._previous_answer: str | None = None
        self._actions_connected = False
        self._model_lifecycle: ModelLifecycle[_PreparedRequest] = ModelLifecycle(
            services.registry,
            cancel_active=services.coordinator.cancel_active,
            warm_up=services.warm_up,
            replay=self._replay_recovered,
            sleeper=services.recovery_sleeper,
            on_state=self._on_recovery_state,
        )
        self._connect_actions()

    @property
    def is_closing(self) -> bool:
        return self._closing

    @property
    def is_started(self) -> bool:
        return self._started

    @property
    def prepared_instances(self) -> dict[str, ModelInstance]:
        return dict(self._instances)

    @property
    def manual_image_path(self) -> Path | None:
        return self._manual_image_path

    def update_hotkey_bindings(
        self,
        bindings: Mapping[HotkeyAction | str, str],
    ) -> None:
        self.services.hotkeys.update_bindings(bindings)

    def _connect_actions(self) -> None:
        events = self.application.events
        events.force_request.connect(self._on_force_request)
        events.screenshot_requested.connect(self._on_screenshot_requested)
        events.pause_toggled.connect(self._on_pause_toggled)
        events.overlay_visibility_toggled.connect(self._on_overlay_visibility_toggled)
        events.overlay_interaction_toggled.connect(self._on_overlay_interaction_toggled)
        events.forced_search_requested.connect(self._on_forced_search_requested)
        events.answer_clear_requested.connect(self._on_answer_clear_requested)
        self._actions_connected = True

    def _disconnect_actions(self) -> None:
        if not self._actions_connected:
            return
        events = self.application.events
        for signal, slot in (
            (events.force_request, self._on_force_request),
            (events.screenshot_requested, self._on_screenshot_requested),
            (events.pause_toggled, self._on_pause_toggled),
            (
                events.overlay_visibility_toggled,
                self._on_overlay_visibility_toggled,
            ),
            (
                events.overlay_interaction_toggled,
                self._on_overlay_interaction_toggled,
            ),
            (events.forced_search_requested, self._on_forced_search_requested),
            (events.answer_clear_requested, self._on_answer_clear_requested),
        ):
            try:
                signal.disconnect(slot)
            except TypeError:
                # Qt reports an already-disconnected exact slot as TypeError.
                continue
        self._actions_connected = False

    async def start(self) -> None:
        if self._closing or self._started:
            return
        if self.application.is_shutdown:
            raise RuntimeError("Application is already shut down")
        if not self.application.can_start_session:
            raise RuntimeError("Readiness checks must pass before session start")
        keys = tuple(
            dict.fromkeys(
                key
                for key in (
                    self.config.text_model,
                    self.config.vision_model,
                )
                if key
            )
        )
        if not keys:
            raise RuntimeError("At least one LM Studio model must be selected")
        self.application.start()
        if not self.application.can_start_session:
            raise RuntimeError("Readiness checks must pass before session start")
        affinity = self.application.ribbon.affinity_result
        if (
            self.application.states.state is ApplicationState.OFFLINE
            or affinity is None
            or not affinity.ok
        ):
            raise RuntimeError("Overlay capture exclusion must remain verified")
        for key in keys:
            self._instances[key] = await self.services.registry.ensure_ready(key)
        started: list[Callable[[], object]] = []
        try:
            self.services.stt.start()
            started.append(self.services.stt.stop)
            self.services.audio.start()
            started.append(self.services.audio.stop)
            self.services.hotkeys.start()
            started.append(self.services.hotkeys.stop)
        except BaseException:
            for stop in reversed(started):
                try:
                    stop()
                except BaseException:
                    pass
            raise
        self._started = True
        self._set_state(ApplicationState.LISTENING)

    def submit_hypothesis(
        self,
        hypothesis: TranscriptHypothesis,
    ) -> asyncio.Task[None]:
        """Replace stale transcript work without blocking an STT callback."""

        if self._closing:
            raise RuntimeError("Application runtime is closing")
        question = self._record_hypothesis(hypothesis)
        if question is None:
            return asyncio.create_task(self._completed_hypothesis())
        return self._queue_question(question)

    def _queue_question(self, question: DetectedQuestion) -> asyncio.Task[None]:
        previous = self._question_task
        if previous is not None:
            previous.cancel()
        task = asyncio.create_task(
            self._replace_question(previous, question),
            name="interview-question",
        )
        self._question_task = task
        task.add_done_callback(self._question_task_done)
        return task

    @staticmethod
    async def _completed_hypothesis() -> None:
        return None

    async def _replace_question(
        self,
        previous: asyncio.Task[None] | None,
        question: DetectedQuestion,
    ) -> None:
        if previous is not None:
            try:
                await previous
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            await self.services.coordinator.cancel_active()
        await self._answer(question)

    def _question_task_done(self, task: asyncio.Task[None]) -> None:
        error: BaseException | None = None
        try:
            error = task.exception()
        except asyncio.CancelledError:
            pass
        is_current = self._question_task is task
        if is_current:
            self._question_task = None
        if error is not None and is_current and not self._closing:
            self.application.events.notification.emit("Request processing failed.")
            if self.application.states.state is not ApplicationState.OFFLINE:
                self._set_state(ApplicationState.LISTENING)

    async def handle_hypothesis(self, hypothesis: TranscriptHypothesis) -> None:
        if self._closing or not self._started:
            return
        question = self._record_hypothesis(hypothesis)
        if question is not None:
            await self._answer(question)

    def _record_hypothesis(
        self,
        hypothesis: TranscriptHypothesis,
    ) -> DetectedQuestion | None:
        if self._closing or not self._started or self._paused:
            return None
        source = hypothesis.source.value
        if hypothesis.is_final:
            self.application.events.transcript_final.emit(source, hypothesis.text)
            self.services.transcript_store.add(hypothesis)
        else:
            self.application.events.transcript_partial.emit(source, hypothesis.text)
            return None

        return self.services.question_detector.detect(
            hypothesis.source,
            hypothesis.text,
            is_final=True,
        )

    async def _answer(self, question: DetectedQuestion) -> None:
        self.application.ribbon.set_question(question.text)
        self._set_state(ApplicationState.TRANSCRIBING)
        manual_image_path = self._manual_image_path
        self._manual_image_path = None
        image_path = manual_image_path
        if image_path is None and self.config.vision_model:
            capture = await self.services.capture.capture_for_event(question.kind)
            image_path = self._usable_image(capture)
        if image_path is not None and not self.config.vision_model:
            image_path = None
            self.application.events.notification.emit(
                "No vision model selected; answering without the screenshot."
            )

        text_model_key = self.config.text_model
        if not text_model_key:
            text_model_key = self.config.vision_model
        text_instance = self._instances[text_model_key]
        search_results: tuple[str, ...] = ()
        backend_available = self.config.mcp.backend != "off" and not (
            self.config.provider == "openrouter" and self.config.mcp.backend == "lmstudio"
        )
        integrations = (
            self.services.search_policy.integrations_for(question.text)
            if backend_available else ()
        )
        if integrations:
            self._set_state(ApplicationState.SEARCHING)
            if self.services.retrieval is not None:
                try:
                    text = await asyncio.wait_for(
                        self.services.retrieval.retrieve(integrations[0]),
                        timeout=self.config.search.timeout_seconds,
                    )
                    retrieval = RequestOutcome(0, "completed", text=text)
                except Exception as error:
                    retrieval = RequestOutcome(0, "failed", error_type=_retrieval_error_type(error))
            else:
                retrieval_id = self.services.coordinator.submit_retrieval(
                    text_instance.instance_id, integrations,
                )
                retrieval = await self._wait_with_timeout(
                    retrieval_id, self.config.search.timeout_seconds,
                )
            if retrieval.status == "failed" and retrieval.error_type == "model_not_found":
                await self._model_lifecycle.recover(
                    text_model_key,
                    RecoveryRequest(
                        question.request_id,
                        _PreparedRequest(
                            question,
                            None,
                            stage="retrieval",
                            integration=integrations[0],
                        ),
                    ),
                )
                recovered = self._recovered_search_results.pop(
                    question.request_id,
                    "",
                )
                retrieval = RequestOutcome(
                    retrieval.request_id,
                    "completed" if recovered else "failed",
                    text=recovered,
                    error_type=None if recovered else "unknown",
                )
            if retrieval.status == "completed" and retrieval.text.strip():
                search_results = (retrieval.text,)
                self.application.ribbon.set_sources(integration.id for integration in integrations)
            else:
                self.application.events.notification.emit(
                    _retrieval_unavailable_message(retrieval.error_type, integrations[0].id)
                )
                self.application.ribbon.set_sources(())
        else:
            self.application.ribbon.set_sources(())

        context = ContextBuilder(
            self.services.transcript_store,
            latest_question=question,
            search_results=search_results,
            previous_answer=self._previous_answer,
        ).normal()
        model_key = (
            self.config.vision_model
            if image_path is not None
            else self.config.text_model
        )
        if not model_key:
            model_key = self.config.text_model or self.config.vision_model
        instance = self._instances[model_key]
        payload = await asyncio.to_thread(
            build_chat_payload,
            instance.instance_id,
            context,
            image_path,
        )
        if manual_image_path is not None and image_path is not None:
            self.application.events.notification.emit("Screenshot added to the request.")
        self._set_state(ApplicationState.GENERATING)
        request_id = self.services.coordinator.submit(payload)
        outcome = await self.services.coordinator.wait(request_id)
        if outcome.status == "completed":
            self._previous_answer = outcome.text[:6000]
        if outcome.status == "failed" and outcome.error_type == "model_not_found":
            await self._model_lifecycle.recover(
                model_key,
                RecoveryRequest(
                    question.request_id,
                    _PreparedRequest(question, image_path),
                ),
            )
        if not self._closing:
            self._set_state(ApplicationState.LISTENING)

    async def _replay_recovered(
        self,
        instance: ModelInstance,
        request: RecoveryRequest[_PreparedRequest],
    ) -> None:
        prepared = request.payload
        self._instances[instance.key] = instance
        if prepared.stage == "retrieval":
            integration = prepared.integration
            if integration is None:
                raise RuntimeError("Recovery retrieval is missing its sealed integration")
            retrieval_id = self.services.coordinator.submit_retrieval(
                instance.instance_id,
                (integration,),
            )
            outcome = await self._wait_with_timeout(
                retrieval_id,
                self.config.search.timeout_seconds,
            )
            if outcome.status != "completed":
                raise RuntimeError("Recovered retrieval did not complete")
            self._recovered_search_results[prepared.question.request_id] = outcome.text
            return
        context = ContextBuilder(
            self.services.transcript_store,
            latest_question=prepared.question,
        ).recovery()
        payload = await asyncio.to_thread(
            build_chat_payload,
            instance.instance_id,
            context,
            prepared.image_path,
        )
        request_id = self.services.coordinator.submit(payload)
        outcome = await self.services.coordinator.wait(request_id)
        if outcome.status != "completed":
            raise RuntimeError("Recovered LM Studio request did not complete")
        self._previous_answer = outcome.text[:6000]

    async def _wait_with_timeout(
        self,
        request_id: int,
        timeout_seconds: float,
    ) -> RequestOutcome:
        try:
            return await asyncio.wait_for(
                self.services.coordinator.wait(request_id),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            await self.services.coordinator.cancel_active()
            await self.services.coordinator.wait(request_id)
            return RequestOutcome(request_id, "failed", error_type="timeout")

    def _on_recovery_state(self, state: str) -> None:
        if self._closing:
            return
        mapped = {
            "recovering": ApplicationState.RECOVERING,
            "ready": ApplicationState.LISTENING,
            "offline": ApplicationState.OFFLINE,
        }[state]
        self._set_state(mapped)

    def _usable_image(self, result: CaptureResult) -> Path | None:
        if (
            result.status in ("captured", "duplicate")
            and result.path is not None
            and result.path.is_file()
        ):
            return result.path
        if result.status == "protected":
            self.application.events.notification.emit(
                "Protected content; continuing without an image."
            )
        return None

    def _set_state(self, state: ApplicationState) -> None:
        self._desired_state = state
        visible_state = (
            ApplicationState.PAUSED
            if self._paused and state is not ApplicationState.STOPPED
            else state
        )
        self.application.states.transition(visible_state)
        self.application.events.state_changed.emit(visible_state.value)

    @pyqtSlot()
    def _on_pause_toggled(self) -> None:
        if self._closing or not self._started:
            return
        if self._paused:
            self.services.audio.resume()
            self._paused = False
            self._set_state(self._desired_state)
            return
        self.services.audio.pause()
        self._paused = True
        self.application.states.transition(ApplicationState.PAUSED)
        self.application.events.state_changed.emit(ApplicationState.PAUSED.value)

    @pyqtSlot()
    def _on_overlay_visibility_toggled(self) -> None:
        if self._closing:
            return
        if self.application.ribbon.isVisible():
            self.application.ribbon.hide()
        else:
            self.application.ribbon.show()

    @pyqtSlot()
    def _on_overlay_interaction_toggled(self) -> None:
        if self._closing:
            return
        self.application.ribbon.toggle_edit_mode()

    @pyqtSlot()
    def _on_forced_search_requested(self) -> None:
        if self._closing:
            return
        self.services.search_policy.force_next()

    @pyqtSlot()
    def _on_answer_clear_requested(self) -> None:
        if self._closing:
            return
        self.application.ribbon.clear_answer()
        self.services.transcript_store.clear()
        self._previous_answer = None

    @pyqtSlot()
    def _on_force_request(self) -> None:
        self._schedule_action(self.force_latest_request)

    @pyqtSlot()
    def _on_screenshot_requested(self) -> None:
        self._schedule_action(self.capture_manual_screenshot)

    def _schedule_action(self, operation: Callable[[], Awaitable[None]]) -> None:
        if self._closing:
            return
        if len(self._action_tasks) >= 8:
            self.application.events.notification.emit(
                "Too many assistant actions are already running."
            )
            return

        async def invoke_action() -> None:
            await operation()

        task: asyncio.Task[None] = asyncio.create_task(
            invoke_action(),
            name="interview-user-action",
        )
        self._action_tasks.add(task)
        task.add_done_callback(self._action_task_done)

    def _action_task_done(self, task: asyncio.Task[None]) -> None:
        self._action_tasks.discard(task)
        try:
            error = task.exception()
        except asyncio.CancelledError:
            return
        if error is not None and not self._closing:
            self.application.events.notification.emit("Assistant action failed.")

    async def force_latest_request(self) -> None:
        if self._closing:
            return
        latest = self.services.transcript_store.latest(AudioSource.SYSTEM)
        if latest is None or not latest.text.strip():
            self.application.events.notification.emit(
                "No final interviewer transcript is available."
            )
            return
        question = self.services.question_detector.force_request(latest.text)
        await self._queue_question(question)

    async def capture_manual_screenshot(self) -> None:
        if self._closing:
            return
        self._manual_capture_generation += 1
        generation = self._manual_capture_generation
        previous_image_path = self._manual_image_path
        self._manual_image_path = None
        self.application.events.notification.emit("Capturing screenshot...")
        try:
            result = await self.services.capture.capture_for_event("manual", manual=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            if generation != self._manual_capture_generation:
                return
            self.application.events.notification.emit(
                "Screenshot was not captured: capture error."
            )
            return
        if generation != self._manual_capture_generation:
            return
        image_path = self._usable_image(result)
        if (
            image_path is None
            and result.status == "duplicate"
            and result.path is None
            and previous_image_path is not None
            and previous_image_path.is_file()
        ):
            image_path = previous_image_path
        if image_path is None:
            self.application.events.notification.emit(
                "Screenshot was not captured: screenshot unavailable."
            )
            return
        self._manual_image_path = image_path
        self.application.events.notification.emit(
            "Screenshot is ready for the next request."
        )

    def clear_history(self) -> None:
        self._previous_answer = None
        self.services.transcript_store.clear()

    async def shutdown(self, *, close_application: bool = True) -> None:
        task = self._shutdown_task
        if task is None:
            self._closing = True
            self._manual_capture_generation += 1
            task = asyncio.create_task(
                self._shutdown_once(),
                name="interview-application-shutdown",
            )
            self._shutdown_task = task
            task.add_done_callback(self._shutdown_attempt_done)
        cleanup_error: BaseException | None = None
        try:
            await asyncio.shield(task)
        except BaseException as error:
            cleanup_error = error
        application_error: BaseException | None = None
        if close_application:
            try:
                self.application.shutdown()
            except BaseException as error:
                application_error = error
        if cleanup_error is not None:
            raise cleanup_error
        if application_error is not None:
            raise application_error

    def _shutdown_attempt_done(self, task: asyncio.Task[None]) -> None:
        try:
            error = task.exception()
        except asyncio.CancelledError:
            error = RuntimeError("Shutdown attempt was cancelled")
        if error is not None and self._shutdown_task is task:
            self._shutdown_task = None

    async def _shutdown_once(self) -> None:
        errors: list[BaseException] = []

        if "actions" not in self._shutdown_completed:
            try:
                self._disconnect_actions()
            except BaseException as error:
                errors.append(error)
            else:
                self._shutdown_completed.add("actions")
        if (
            self.services.hypothesis_ingress is not None
            and "hypothesis_ingress" not in self._shutdown_completed
        ):
            try:
                self.services.hypothesis_ingress.close()
            except BaseException as error:
                errors.append(error)
            else:
                self._shutdown_completed.add("hypothesis_ingress")
        for name, stop in (
            ("hotkey", self.services.hotkeys.stop),
            ("audio", self.services.audio.stop),
            ("stt", self.services.stt.stop),
        ):
            if name in self._shutdown_completed:
                continue
            try:
                stopped = await asyncio.to_thread(stop)
                if name == "stt" and stopped is False:
                    raise RuntimeError("STT worker did not stop before timeout")
            except BaseException as error:
                errors.append(error)
            else:
                self._shutdown_completed.add(name)
        if "question_task" not in self._shutdown_completed:
            question_task = self._question_task
            self._question_task = None
            if question_task is not None and question_task is not asyncio.current_task():
                question_task.cancel()
                try:
                    await question_task
                except asyncio.CancelledError:
                    pass
                except BaseException as error:
                    errors.append(error)
            self._shutdown_completed.add("question_task")
        if "action_tasks" not in self._shutdown_completed:
            action_tasks = tuple(self._action_tasks)
            for action_task in action_tasks:
                action_task.cancel()
            if action_tasks:
                await asyncio.gather(*action_tasks, return_exceptions=True)
            self._shutdown_completed.add("action_tasks")
        if "coordinator" not in self._shutdown_completed:
            try:
                await self.services.coordinator.cancel_active()
            except BaseException as error:
                errors.append(error)
            else:
                self._shutdown_completed.add("coordinator")
        if "model_lifecycle" not in self._shutdown_completed:
            try:
                self._model_lifecycle.close()
            except BaseException as error:
                errors.append(error)
            else:
                self._shutdown_completed.add("model_lifecycle")
        for name, operation in (
            ("capture", self.services.capture.shutdown),
            ("client", self.services.client.aclose),
            *(([("retrieval", self.services.retrieval.aclose)])
              if self.services.retrieval is not None else []),
        ):
            if name in self._shutdown_completed:
                continue
            try:
                await operation()
            except BaseException as error:
                errors.append(error)
            else:
                self._shutdown_completed.add(name)
        self._manual_image_path = None
        self._previous_answer = None
        self._started = False

        if errors:
            raise errors[0]
