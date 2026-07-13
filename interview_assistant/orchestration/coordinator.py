import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Protocol

from ..events import EventBus
from ..lmstudio.models import ChatEvent
from ..retrieval.models import SearchIntegration


class StreamClient(Protocol):
    def stream_chat(
        self,
        payload: Mapping[str, object],
    ) -> AsyncIterator[ChatEvent]: ...


class RequestCoordinator:
    def __init__(self, events: EventBus, client: StreamClient) -> None:
        self._events = events
        self._client = client
        self._next_request_id = 0
        self._active_id: int | None = None
        self._active_task: asyncio.Task[None] | None = None

    def submit(self, payload: Mapping[str, object]) -> int:
        if "integrations" in payload:
            raise ValueError(
                "Retrieval integrations must be submitted via submit_retrieval"
            )
        return self._submit_payload(payload)

    def _submit_payload(self, payload: Mapping[str, object]) -> int:
        self._next_request_id += 1
        request_id = self._next_request_id
        self._active_id = request_id
        if self._active_task is not None:
            self._active_task.cancel()
        self._events.answer_reset.emit(request_id)
        task = asyncio.get_running_loop().create_task(
            self._run(request_id, dict(payload))
        )
        self._active_task = task
        task.add_done_callback(self._task_done)
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
            }
        )

    def _task_done(self, task: asyncio.Task[None]) -> None:
        try:
            task.exception()
        except asyncio.CancelledError:
            pass
        if self._active_task is task:
            self._active_task = None

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
    ) -> None:
        try:
            stream = self._client.stream_chat(payload)
            try:
                async for event in stream:
                    if request_id != self._active_id:
                        return
                    if event.type == "message.delta":
                        self._events.answer_delta.emit(request_id, event.content)
            finally:
                close = getattr(stream, "aclose", None)
                if close is not None:
                    await close()
        except asyncio.CancelledError:
            raise
        except Exception:
            if request_id != self._active_id:
                return
            self._events.notification.emit("Answer generation failed.")
