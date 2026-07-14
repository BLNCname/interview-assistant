import asyncio
import json

import httpx
import pytest
import respx
from pydantic import ValidationError

from interview_assistant.lmstudio import client as client_module
from interview_assistant.lmstudio.client import LMStudioClient
from interview_assistant.lmstudio.models import ChatEvent


SSE = b"""event: chat.start
data: {"type":"chat.start","model_instance_id":"qwen3.5"}

event: message.delta
data: {"type":"message.delta","content":"Hello"}

event: chat.end
data: {"type":"chat.end","result":{"stats":{"tokens_per_second":42}}}

"""


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


class BlockingStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.waiting = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False

    async def __aiter__(self):
        yield b'event: chat.start\ndata: {"type":"chat.start"}\n\n'
        self.waiting.set()
        await self.release.wait()
        yield b'event: chat.end\ndata: {"type":"chat.end"}\n\n'

    async def aclose(self) -> None:
        self.closed = True


@respx.mock
async def test_stream_yields_message_delta() -> None:
    respx.post("http://127.0.0.1:1234/api/v1/chat").mock(
        return_value=httpx.Response(200, content=SSE)
    )

    async with LMStudioClient("127.0.0.1", 1234, None) as client:
        events = [
            event async for event in client.stream_chat({"model": "qwen3.5"})
        ]

    assert [event.type for event in events] == [
        "chat.start",
        "message.delta",
        "chat.end",
    ]
    assert events[1].content == "Hello"


@respx.mock
async def test_stream_uses_native_endpoint_and_inherited_request_configuration() -> None:
    route = respx.post("http://lmstudio.local:1234/api/v1/chat").mock(
        return_value=httpx.Response(
            200,
            content=b'event: chat.end\ndata: {"type":"chat.end"}\n\n',
        )
    )
    payload: dict[str, object] = {
        "model": "qwen3.5",
        "stream": False,
        "input": [{"role": "user", "content": "question"}],
    }
    original = dict(payload)

    async with LMStudioClient("lmstudio.local", 1234, "api-token") as client:
        events = [event async for event in client.stream_chat(payload)]

    request = route.calls.last.request
    assert route.call_count == 1
    assert request.method == "POST"
    assert request.url.path == "/api/v1/chat"
    assert json.loads(request.content) == {
        "model": "qwen3.5",
        "stream": True,
        "input": [{"role": "user", "content": "question"}],
    }
    assert payload == original
    assert request.headers["Accept"] == "text/event-stream"
    assert request.headers["Authorization"] == "Bearer api-token"
    assert request.extensions["timeout"] == {
        "connect": 120.0,
        "read": 120.0,
        "write": 120.0,
        "pool": 120.0,
    }
    assert [event.type for event in events] == ["chat.end"]


@respx.mock
async def test_stream_handles_chunk_splits_crlf_comments_multiline_data_and_eof() -> None:
    body = (
        b": heartbeat\r\n\r\n"
        b"event: message.delta\r\n"
        b'data: {"type":\r\n'
        b'data: "message.delta", "content": "chunked", '
        b'"future": {"ok": true}}\r\n'
    )
    chunks = [body[:1], body[1:8], body[8:23], body[23:51], body[51:77], body[77:]]
    stream = ChunkStream(chunks)
    respx.post("http://127.0.0.1:1234/api/v1/chat").mock(
        return_value=httpx.Response(200, stream=stream)
    )

    async with LMStudioClient("127.0.0.1", 1234, None) as client:
        events = [event async for event in client.stream_chat({})]

    assert len(events) == 1
    assert events[0].type == "message.delta"
    assert events[0].content == "chunked"
    assert events[0].model_extra == {"future": {"ok": True}}
    assert stream.closed is True


@respx.mock
async def test_stream_yields_supported_families_and_ignores_other_official_events() -> None:
    event_payloads: list[tuple[str, dict[str, object]]] = [
        ("chat.start", {"type": "chat.start", "session_id": "session-1"}),
        ("message.start", {"type": "message.start"}),
        ("reasoning.start", {"type": "reasoning.start"}),
        ("reasoning.delta", {"type": "reasoning.delta", "content": "private"}),
        ("reasoning.end", {"type": "reasoning.end"}),
        ("message.delta", {"type": "message.delta", "content": "public"}),
        ("message.end", {"type": "message.end"}),
        ("model_load.start", {"type": "model_load.start", "stage": "loading"}),
        ("model_load.progress", {"type": "model_load.progress", "progress": 0.5}),
        ("model_load.end", {"type": "model_load.end"}),
        ("prompt_processing.start", {"type": "prompt_processing.start"}),
        ("prompt_processing.progress", {"type": "prompt_processing.progress"}),
        ("prompt_processing.end", {"type": "prompt_processing.end"}),
        ("tool_call.start", {"type": "tool_call.start", "tool": "lookup"}),
        (
            "tool_call.delta",
            {"type": "tool_call.delta", "arguments": {"query": "answer"}},
        ),
        ("tool_call.end", {"type": "tool_call.end", "output": "done"}),
        (
            "future.official",
            {
                "type": "future.official",
                "progress": "server-defined-future-shape",
                "additive": True,
            },
        ),
        (
            "error",
            {
                "type": "error",
                "error": {
                    "type": "internal_error",
                    "message": "model failed",
                    "code": "model_error",
                },
            },
        ),
        ("chat.end", {"type": "chat.end", "result": {"stats": {"tokens": 3}}}),
    ]
    body = b"".join(
        f"event: {name}\ndata: {json.dumps(payload)}\n\n".encode()
        for name, payload in event_payloads
    )
    respx.post("http://127.0.0.1:1234/api/v1/chat").mock(
        return_value=httpx.Response(200, content=body)
    )

    async with LMStudioClient("127.0.0.1", 1234, None) as client:
        events = [event async for event in client.stream_chat({})]

    assert [event.type for event in events] == [
        "chat.start",
        "message.delta",
        "model_load.start",
        "model_load.progress",
        "model_load.end",
        "prompt_processing.start",
        "prompt_processing.progress",
        "prompt_processing.end",
        "tool_call.start",
        "tool_call.delta",
        "tool_call.end",
        "error",
        "chat.end",
    ]
    assert events[0].model_extra == {"session_id": "session-1"}
    assert events[3].progress == 0.5
    assert events[9].arguments == {"query": "answer"}
    assert events[11].error is not None
    assert events[11].error.code == "model_error"


def test_chat_event_known_fields_are_strict() -> None:
    with pytest.raises(ValidationError):
        ChatEvent.model_validate(
            {"type": "model_load.progress", "progress": "0.5"}
        )


def test_protocol_error_type_is_available() -> None:
    assert issubclass(client_module.LMStudioProtocolError, RuntimeError)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            b"event: message.delta\ndata: {payload-secret-marker\n\n",
            id="invalid-json",
        ),
        pytest.param(
            b'event: message.delta\ndata: ["payload-secret-marker"]\n\n',
            id="non-object-json",
        ),
        pytest.param(
            b'data: {"type":"message.delta","content":"payload-secret-marker"}\n\n',
            id="missing-event",
        ),
        pytest.param(
            b"event: message.delta\nid: payload-secret-marker\n\n",
            id="missing-data",
        ),
        pytest.param(
            b'event: message.delta\ndata: {"content":"payload-secret-marker"}\n\n',
            id="missing-json-type",
        ),
        pytest.param(
            b'event: message.delta\ndata: {"type":"chat.end",'
            b'"content":"payload-secret-marker"}\n\n',
            id="mismatched-type",
        ),
    ],
)
@respx.mock
async def test_malformed_blocks_raise_sanitized_protocol_error(body: bytes) -> None:
    token = "top-secret-token"
    respx.post("http://127.0.0.1:1234/api/v1/chat").mock(
        return_value=httpx.Response(200, content=body)
    )

    with pytest.raises(client_module.LMStudioProtocolError) as captured:
        async with LMStudioClient("127.0.0.1", 1234, token) as client:
            _ = [event async for event in client.stream_chat({"secret": token})]

    message = str(captured.value)
    assert "payload-secret-marker" not in message
    assert token not in message


@respx.mock
async def test_http_error_is_raised_and_response_is_closed() -> None:
    token = "top-secret-token"
    stream = ChunkStream([b'{"error":"payload-secret-marker"}'])
    respx.post("http://127.0.0.1:1234/api/v1/chat").mock(
        return_value=httpx.Response(503, stream=stream)
    )

    async with LMStudioClient("127.0.0.1", 1234, token) as client:
        with pytest.raises(httpx.HTTPStatusError) as captured:
            _ = [event async for event in client.stream_chat({"secret": token})]
        assert stream.closed is True

    assert token not in str(captured.value)
    assert "payload-secret-marker" not in str(captured.value)


@respx.mock
async def test_response_is_closed_when_parser_raises() -> None:
    stream = ChunkStream(
        [b"event: message.delta\ndata: payload-secret-marker\n\n"]
    )
    respx.post("http://127.0.0.1:1234/api/v1/chat").mock(
        return_value=httpx.Response(200, stream=stream)
    )

    async with LMStudioClient("127.0.0.1", 1234, None) as client:
        with pytest.raises(client_module.LMStudioProtocolError):
            _ = [event async for event in client.stream_chat({})]
        assert stream.closed is True


@respx.mock
async def test_response_is_closed_when_consumer_closes_generator_early() -> None:
    stream = ChunkStream(
        [
            b'event: chat.start\ndata: {"type":"chat.start"}\n\n'
            b'event: chat.end\ndata: {"type":"chat.end"}\n\n'
        ]
    )
    respx.post("http://127.0.0.1:1234/api/v1/chat").mock(
        return_value=httpx.Response(200, stream=stream)
    )

    async with LMStudioClient("127.0.0.1", 1234, None) as client:
        events = client.stream_chat({})
        first = await anext(events)
        await events.aclose()
        assert first.type == "chat.start"
        assert stream.closed is True


@respx.mock
async def test_response_is_closed_when_consumer_task_is_cancelled() -> None:
    stream = BlockingStream()
    respx.post("http://127.0.0.1:1234/api/v1/chat").mock(
        return_value=httpx.Response(200, stream=stream)
    )

    async with LMStudioClient("127.0.0.1", 1234, None) as client:
        consuming = asyncio.create_task(
            anext_after_start(client.stream_chat({}))
        )
        await asyncio.wait_for(stream.waiting.wait(), timeout=1)
        consuming.cancel()
        with pytest.raises(asyncio.CancelledError):
            await consuming
        assert stream.closed is True


async def anext_after_start(events) -> None:
    assert (await anext(events)).type == "chat.start"
    await anext(events)
