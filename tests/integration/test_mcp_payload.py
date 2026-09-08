import asyncio
import json
from collections.abc import AsyncIterator, Mapping

import httpx
import pytest
import respx

from interview_assistant.lmstudio.client import LMStudioClient
from interview_assistant.lmstudio.models import ChatEvent
from interview_assistant.retrieval.models import SearchIntegration
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
                    "id": "mcp/firecrawl",
                    "allowed_tools": ["firecrawl_search"],
                }
            ],
            "store": False,
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


def test_retrieval_submission_rejects_forged_unbranded_query() -> None:
    from interview_assistant.orchestration.coordinator import RequestCoordinator

    integration = SearchPolicy("forced").integrations_for("safe question")[0]
    object.__setattr__(integration, "_sanitized_question", object())
    coordinator = RequestCoordinator(RecordingEvents(), RecordingClient())

    with pytest.raises(TypeError, match="sanitized"):
        coordinator.submit_retrieval("qwen3.5", [integration])


def test_retrieval_submission_rejects_reused_seal_after_text_mutation() -> None:
    from interview_assistant.orchestration.coordinator import RequestCoordinator

    integration = SearchPolicy("forced").integrations_for("safe question")[0]
    object.__setattr__(
        integration._sanitized_question,
        "_text",
        "raw private transcript",
    )
    coordinator = RequestCoordinator(RecordingEvents(), RecordingClient())

    with pytest.raises(TypeError, match="sanitized"):
        coordinator.submit_retrieval("qwen3.5", [integration])


@pytest.mark.parametrize(
    ("attribute", "value"),
    [
        ("id", "mcp/filesystem"),
        ("allowed_tools", ("fetch_content",)),
    ],
)
def test_retrieval_submission_revalidates_mutated_plugin_fields(
    attribute: str,
    value: object,
) -> None:
    from interview_assistant.orchestration.coordinator import RequestCoordinator

    integration = SearchPolicy("forced").integrations_for("safe question")[0]
    object.__setattr__(integration, attribute, value)
    coordinator = RequestCoordinator(RecordingEvents(), RecordingClient())

    with pytest.raises(TypeError, match="sanitized"):
        coordinator.submit_retrieval("qwen3.5", [integration])


def test_retrieval_submission_rejects_mutation_to_another_valid_plugin() -> None:
    from interview_assistant.orchestration.coordinator import RequestCoordinator

    integration = SearchPolicy("forced").integrations_for("safe question")[0]
    object.__setattr__(integration, "id", "mcp/context7")
    object.__setattr__(
        integration,
        "allowed_tools",
        ("resolve-library-id", "query-docs"),
    )
    coordinator = RequestCoordinator(RecordingEvents(), RecordingClient())

    with pytest.raises(TypeError, match="sanitized"):
        coordinator.submit_retrieval("qwen3.5", [integration])


def test_retrieval_submission_rejects_search_integration_subclass() -> None:
    from interview_assistant.orchestration.coordinator import RequestCoordinator

    valid = SearchPolicy("forced").integrations_for("safe question")[0]

    class SearchIntegrationSubclass(SearchIntegration):
        pass

    forged = SearchIntegrationSubclass(
        id=valid.id,
        query=valid._sanitized_question,
        allowed_tools=valid.allowed_tools,
    )
    coordinator = RequestCoordinator(RecordingEvents(), RecordingClient())

    with pytest.raises(TypeError, match="SearchIntegration"):
        coordinator.submit_retrieval("qwen3.5", [forged])


def test_generic_submission_cannot_bypass_the_retrieval_policy() -> None:
    from interview_assistant.orchestration.coordinator import RequestCoordinator

    coordinator = RequestCoordinator(RecordingEvents(), RecordingClient())

    with pytest.raises(ValueError, match="submit_retrieval"):
        coordinator.submit(
            {
                "model": "qwen3.5",
                "input": "raw private transcript",
                "integrations": [
                    {
                        "type": "plugin",
                        "id": "mcp/firecrawl",
                        "allowed_tools": ["firecrawl_search"],
                    }
                ],
            }
        )


async def test_generic_submission_still_accepts_non_mcp_payloads() -> None:
    from interview_assistant.orchestration.coordinator import RequestCoordinator

    client = RecordingClient()
    coordinator = RequestCoordinator(RecordingEvents(), client)
    payload = {"model": "qwen3.5", "input": "ordinary local question"}

    request_id = coordinator.submit(payload)
    await asyncio.wait_for(client.finished.wait(), timeout=1)

    assert request_id == 1
    assert client.payloads == [payload]


@respx.mock
async def test_retrieval_http_payload_contains_only_sanitized_native_fields() -> None:
    from interview_assistant.orchestration.coordinator import RequestCoordinator

    route = respx.post("http://127.0.0.1:1234/api/v1/chat").mock(
        return_value=httpx.Response(
            200,
            content=b'event: chat.end\ndata: {"type":"chat.end"}\n\n',
        )
    )
    integrations = SearchPolicy("auto").integrations_for(
        (
            "Previous answer: Alice Smith works at Acme. "
            "What is the latest Python release? "
            "https://example.test/reset/token_abcd1234"
        ),
        full_transcript="private-transcript-marker",
    )

    async with LMStudioClient("127.0.0.1", 1234, None) as client:
        coordinator = RequestCoordinator(RecordingEvents(), client)
        coordinator.submit_retrieval("qwen3.5", integrations)
        task = coordinator._active_task
        assert task is not None
        await asyncio.wait_for(task, timeout=1)

    assert route.call_count == 1
    request = route.calls.last.request
    assert request.url.path == "/api/v1/chat"
    assert json.loads(request.content) == {
        "model": "qwen3.5",
        "input": "What is the latest Python release?",
        "integrations": [
            {
                "type": "plugin",
                "id": "mcp/firecrawl",
                "allowed_tools": ["firecrawl_search"],
            }
        ],
        "store": False,
        "stream": True,
    }
    serialized = request.content.decode("utf-8")
    for forbidden in (
        "Alice Smith",
        "Acme",
        "private-transcript-marker",
        "token_abcd1234",
        '"query"',
    ):
        assert forbidden not in serialized
