import asyncio
from collections import OrderedDict
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal
from typing import Protocol

from ..events import EventBus
from ..lmstudio.models import ChatEvent
from ..retrieval.models import SearchIntegration


class StreamClient(Protocol):
    def stream_chat(
        self,
        payload: Mapping[str, object],
    ) -> AsyncIterator[ChatEvent]: ...


@dataclass(frozen=True, slots=True)
class RequestOutcome:
    request_id: int
    status: Literal["completed", "cancelled", "failed"]
    text: str = ""
    error_type: str | None = None


_MAX_RETAINED_COMPLETIONS = 64


class RequestCoordinator:
    def __init__(self, events: EventBus, client: StreamClient) -> None:
        self._events = events
        self._client = client
        self._next_request_id = 0
        self._active_id: int | None = None
        self._active_task: asyncio.Task[RequestOutcome] | None = None
        self._completions: OrderedDict[
            int,
            asyncio.Future[RequestOutcome],
        ] = OrderedDict()

    def submit(self, payload: Mapping[str, object]) -> int:
        if "integrations" in payload:
            raise ValueError(
                "Retrieval integrations must be submitted via submit_retrieval"
            )
        return self._submit_payload(payload)

    def _submit_payload(
        self,
        payload: Mapping[str, object],
        *,
        publish_output: bool = True,
    ) -> int:
        self._trim_completions()
        self._next_request_id += 1
        request_id = self._next_request_id
        self._active_id = request_id
        if self._active_task is not None:
            self._active_task.cancel()
        if publish_output:
            self._events.answer_reset.emit(request_id)
        completion = asyncio.get_running_loop().create_future()
        self._completions[request_id] = completion
        task = asyncio.get_running_loop().create_task(
            self._run(request_id, dict(payload), publish_output=publish_output)
        )
        self._active_task = task

        def finish_request(finished: asyncio.Task[RequestOutcome]) -> None:
            self._task_done(request_id, completion, finished)

        task.add_done_callback(finish_request)
        return request_id

    def submit_retrieval(
        self,
        model: str,
        integrations: Sequence[SearchIntegration],
    ) -> int:
        """Submit one MCP query without copying an answer-generation payload."""

        if not isinstance(model, str):
            raise TypeError("Retrieval model must be a string")
        model_key = model.strip()
        if not model_key:
            raise ValueError("Retrieval model must not be empty")
        if len(integrations) != 1:
            raise ValueError("Retrieval submission requires exactly one integration")

        integration = integrations[0]
        if type(integration) is not SearchIntegration:
            raise TypeError("Retrieval integration must be a SearchIntegration")
        return self._submit_payload(
            {
                "model": model_key,
                "input": integration._query_for_retrieval(),
                "integrations": [integration.to_lmstudio()],
                "store": False,
            },
            publish_output=False,
        )

    def _task_done(
        self,
        request_id: int,
        completion: asyncio.Future[RequestOutcome],
        task: asyncio.Task[RequestOutcome],
    ) -> None:
        try:
            outcome = task.result()
        except asyncio.CancelledError:
            outcome = RequestOutcome(request_id, "cancelled")
        except Exception:
            outcome = RequestOutcome(request_id, "failed", error_type="internal_error")
        if not completion.done():
            completion.set_result(outcome)
        if self._active_task is task:
            self._active_task = None
            if self._active_id == request_id:
                self._active_id = None

    def _trim_completions(self) -> None:
        while len(self._completions) >= _MAX_RETAINED_COMPLETIONS:
            removable = next(
                (
                    request_id
                    for request_id, future in self._completions.items()
                    if future.done()
                ),
                None,
            )
            if removable is None:
                raise RuntimeError("Too many answer requests are still completing")
            self._completions.pop(removable, None)

    async def wait(self, request_id: int) -> RequestOutcome:
        try:
            completion = self._completions[request_id]
        except KeyError:
            raise KeyError(f"Unknown request id: {request_id}") from None
        outcome = await asyncio.shield(completion)
        if self._completions.get(request_id) is completion:
            self._completions.pop(request_id, None)
        return outcome

    async def cancel_active(self) -> None:
        task = self._active_task
        self._active_id = None
        if task is None:
            return

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            if self._active_task is task:
                self._active_task = None

    async def _run(
        self,
        request_id: int,
        payload: Mapping[str, object],
        *,
        publish_output: bool,
    ) -> RequestOutcome:
        chunks: list[str] = []
        error_type: str | None = None
        saw_end = False
        try:
            stream = self._client.stream_chat(payload)
            try:
                async for event in stream:
                    if request_id != self._active_id:
                        return RequestOutcome(request_id, "cancelled")
                    if event.type == "message.delta":
                        chunks.append(event.content)
                        if publish_output:
                            self._events.answer_delta.emit(request_id, event.content)
                    elif event.type == "error":
                        error_type = (
                            event.error.type
                            if event.error is not None
                            else "unknown"
                        )
                    elif event.type == "chat.end":
                        saw_end = True
            finally:
                close = getattr(stream, "aclose", None)
                if close is not None:
                    await close()
        except asyncio.CancelledError:
            raise
        except Exception:
            if request_id != self._active_id:
                return RequestOutcome(request_id, "cancelled")
            if publish_output:
                self._events.notification.emit("Answer generation failed.")
            return RequestOutcome(request_id, "failed", error_type="internal_error")

        if error_type is not None or not saw_end:
            if publish_output and request_id == self._active_id:
                self._events.notification.emit("Answer generation failed.")
            return RequestOutcome(
                request_id,
                "failed",
                error_type=error_type or "unknown",
            )
        return RequestOutcome(request_id, "completed", text="".join(chunks))
