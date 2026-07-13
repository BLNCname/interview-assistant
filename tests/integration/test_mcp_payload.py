import asyncio
import json
from collections.abc import AsyncIterator, Mapping

import pytest

from interview_assistant.lmstudio.models import ChatEvent
from interview_assistant.retrieval.policy import SearchPolicy


class RecordingSignal:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def emit(self, *args: object) -> None:
        self.calls.append(args)


class RecordingEvents:
    def __init__(self) -> None:
        self.answer_reset = RecordingSignal()
        self.answer_delta = RecordingSignal()
        self.notification = RecordingSignal()


class RecordingClient:
    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []
        self.finished = asyncio.Event()

    async def stream_chat(
        self,
        payload: Mapping[str, object],
    ) -> AsyncIterator[ChatEvent]:
        try:
            self.payloads.append(dict(payload))
            yield ChatEvent(type="chat.end")
        finally:
            self.finished.set()


async def test_retrieval_submission_contains_only_model_sanitized_input_and_plugin() -> None:
    from interview_assistant.orchestration.coordinator import RequestCoordinator

    integrations = SearchPolicy("auto").integrations_for(
        (
            "Speaker 1 [00:01]: My name is Alice Smith. "
            "Email: alice@example.com. What is the latest Python release?"
        ),
        full_transcript="private-transcript-marker",
    )
    client = RecordingClient()
    coordinator = RequestCoordinator(RecordingEvents(), client)

    request_id = coordinator.submit_retrieval("qwen3.5", integrations)
    await asyncio.wait_for(client.finished.wait(), timeout=1)

    assert request_id == 1
    assert client.payloads == [
        {
            "model": "qwen3.5",
            "input": "What is the latest Python release?",
            "integrations": [
                {
                    "type": "plugin",
                    "id": "mcp/duckduckgo",
                    "allowed_tools": ["search"],
                }
            ],
        }
    ]
    serialized = json.dumps(client.payloads, ensure_ascii=False)
    assert "private-transcript-marker" not in serialized
    assert "alice@example.com" not in serialized
    assert "Alice Smith" not in serialized


async def test_context7_payload_uses_exact_native_allowlist() -> None:
    from interview_assistant.orchestration.coordinator import RequestCoordinator

    integrations = SearchPolicy("auto").integrations_for(
        "How does the current FastAPI API configure lifespan?"
    )
    client = RecordingClient()
    coordinator = RequestCoordinator(RecordingEvents(), client)

    coordinator.submit_retrieval("qwen3.5", integrations)
    await asyncio.wait_for(client.finished.wait(), timeout=1)

    assert client.payloads[0]["integrations"] == [
        {
            "type": "plugin",
            "id": "mcp/context7",
            "allowed_tools": ["resolve-library-id", "query-docs"],
        }
    ]


@pytest.mark.parametrize("model", ["", "   "])
def test_retrieval_submission_rejects_an_empty_model(model: str) -> None:
    from interview_assistant.orchestration.coordinator import RequestCoordinator

    integrations = SearchPolicy("forced").integrations_for("static question")
    coordinator = RequestCoordinator(RecordingEvents(), RecordingClient())

    with pytest.raises(ValueError, match="model"):
        coordinator.submit_retrieval(model, integrations)


@pytest.mark.parametrize("count", [0, 2])
def test_retrieval_submission_requires_exactly_one_integration(count: int) -> None:
    from interview_assistant.orchestration.coordinator import RequestCoordinator

    integration = SearchPolicy("forced").integrations_for("static question")[0]
    coordinator = RequestCoordinator(RecordingEvents(), RecordingClient())

    with pytest.raises(ValueError, match="exactly one"):
        coordinator.submit_retrieval("qwen3.5", [integration] * count)
