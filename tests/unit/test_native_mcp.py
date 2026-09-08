import asyncio
import json
import os
from pathlib import Path
import sys

import pytest
import httpx
import respx

from interview_assistant.retrieval.models import SearchIntegration
from interview_assistant.retrieval.policy import SearchPolicy


FIXTURE_SERVER = Path(__file__).parents[1] / "fixtures" / "native_mcp_server.py"
FIRECRAWL_URL = "https://mcp.firecrawl.dev/v2/mcp"


def search_payload(*, description: str = "Current Python documentation") -> dict:
    return {"success": True, "data": {"web": [{
        "url": "https://www.python.org/", "title": "Python", "description": description,
    }]}}


def mock_firecrawl(
    router: respx.MockRouter,
    *,
    optional_fields: tuple[str, ...] = ("limit", "sources", "highlights"),
    payload: dict | None = None,
    structured: bool = False,
    is_error: bool = False,
    call_status: int = 200,
    call_error: Exception | None = None,
) -> tuple[respx.Route, list[dict]]:
    """Exercise the real SDK protocol over mocked HTTP, never a network listener.

    Search fields match the recorded official Firecrawl 3.24.1 tools/list schema.
    """
    messages: list[dict] = []

    def respond(request: httpx.Request) -> httpx.Response:
        message = json.loads(request.content)
        messages.append(message)
        if "id" not in message:
            return httpx.Response(202)
        method = message["method"]
        if method == "initialize":
            result = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "firecrawl-contract-fixture", "version": "1"}}
        elif method == "tools/list":
            properties = {"query": {"type": "string", "minLength": 1}}
            optional = {
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "sources": {"type": "array", "items": {
                    "type": "object", "properties": {"type": {
                        "type": "string", "enum": ["web", "images", "news"],
                    }}, "required": ["type"], "additionalProperties": False,
                }},
                "highlights": {"type": "boolean"},
            }
            properties.update({name: optional[name] for name in optional_fields})
            result = {"tools": [
                {"name": "firecrawl_search", "inputSchema": {
                    "type": "object", "properties": properties, "required": ["query"],
                }},
                {"name": "firecrawl_crawl", "inputSchema": {"type": "object"}},
            ]}
        else:
            assert method == "tools/call"
            assert message["params"]["name"] == "firecrawl_search"
            if call_error is not None:
                raise call_error
            if call_status != 200:
                return httpx.Response(call_status, text="upstream-secret-token",
                                      headers={"Retry-After": "120"})
            value = payload if payload is not None else search_payload()
            result = {"content": [] if structured else [{
                "type": "text", "text": json.dumps(value),
            }], "isError": is_error}
            if structured:
                result["structuredContent"] = value
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": result})

    return router.post(FIRECRAWL_URL).mock(side_effect=respond), messages


@pytest.mark.asyncio
async def test_default_firecrawl_probe_uses_keyless_http_without_subprocess_or_search(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from interview_assistant.retrieval import mcp_client

    def forbidden_stdio(*args, **kwargs):
        pytest.fail("Default Firecrawl must not launch any subprocess")

    monkeypatch.setattr(mcp_client, "stdio_client", forbidden_stdio)
    route, messages = mock_firecrawl(respx_mock)
    client = mcp_client.NativeMCPClient(environ={
        "OPENROUTER_API_KEY": "unrelated-llm-key", "CONTEXT7_API_KEY": "unrelated-docs-key",
        "PRIVATE_TEST_SECRET": "unrelated-private-key",
    })
    assert await client.probe("mcp/firecrawl") is True
    await client.aclose()

    assert {message["method"] for message in messages} == {
        "initialize", "notifications/initialized", "tools/list",
    }
    for call in route.calls:
        assert "authorization" not in call.request.headers
        assert "x-api-key" not in call.request.headers
        assert "unrelated" not in str(call.request.headers)
        assert not call.request.url.query


@pytest.mark.asyncio
@pytest.mark.parametrize("optional_fields", [(), ("limit",), ("limit", "sources", "highlights")])
async def test_firecrawl_calls_only_the_allowlisted_tool_with_supported_bounded_arguments(
    respx_mock: respx.MockRouter, optional_fields: tuple[str, ...],
) -> None:
    from interview_assistant.retrieval.mcp_client import NativeMCPClient

    _, messages = mock_firecrawl(respx_mock, optional_fields=optional_fields)
    client = NativeMCPClient(environ={})
    assert "Current Python documentation" in await client.retrieve(search_request())
    arguments: dict[str, object] = {"query": "What is the latest Python release?"}
    for name, value in {"limit": 5, "sources": [{"type": "web"}], "highlights": False}.items():
        if name in optional_fields:
            arguments[name] = value
    calls = [message["params"] for message in messages if message["method"] == "tools/call"]
    assert calls == [{"name": "firecrawl_search", "arguments": arguments}]
    assert "Alice" not in json.dumps(messages)
    assert "private transcript" not in json.dumps(messages)


@pytest.mark.asyncio
async def test_firecrawl_api_key_is_only_forwarded_when_explicitly_configured(
    respx_mock: respx.MockRouter,
) -> None:
    from interview_assistant.retrieval.mcp_client import NativeMCPClient

    route, _ = mock_firecrawl(respx_mock)
    client = NativeMCPClient(environ={"FIRECRAWL_API_KEY": "explicit-firecrawl-test-key",
                                     "CONTEXT7_API_KEY": "other-service-key"})
    assert await client.probe("mcp/firecrawl") is True
    for call in route.calls:
        assert call.request.headers["authorization"] == "Bearer explicit-firecrawl-test-key"
        assert "x-api-key" not in call.request.headers
        assert "context7_api_key" not in call.request.headers
        assert "explicit-firecrawl-test-key" not in str(call.request.url)


@pytest.mark.asyncio
@pytest.mark.parametrize("status, error_message", [
    (429, "rate limit"), (401, "credentials"), (402, "credits"),
])
async def test_firecrawl_http_failures_stop_without_retry_or_paid_fallback(
    respx_mock: respx.MockRouter, caplog: pytest.LogCaptureFixture,
    status: int, error_message: str,
) -> None:
    from interview_assistant.retrieval.mcp_client import NativeMCPClient, NativeMCPError

    _, messages = mock_firecrawl(respx_mock, call_status=status)
    client = NativeMCPClient(environ={})
    with pytest.raises(NativeMCPError, match=error_message) as caught:
        await client.retrieve(search_request())
    assert "upstream-secret-token" not in str(caught.value)
    assert "upstream-secret-token" not in caplog.text
    assert caught.value.code == {401: "credentials", 402: "credits", 429: "rate_limit"}[status]
    assert len([message for message in messages if message["method"] == "tools/call"]) == 1


@pytest.mark.asyncio
async def test_firecrawl_http_timeout_is_bounded_and_does_not_retry(
    respx_mock: respx.MockRouter,
) -> None:
    from interview_assistant.retrieval.mcp_client import NativeMCPClient, NativeMCPError

    _, messages = mock_firecrawl(respx_mock, call_error=httpx.ReadTimeout("private-upstream-detail"))
    client = NativeMCPClient(environ={})
    with pytest.raises(NativeMCPError, match="timed out") as caught:
        await client.retrieve(search_request())
    assert "private-upstream-detail" not in str(caught.value)
    assert caught.value.code == "timeout"
    assert len([message for message in messages if message["method"] == "tools/call"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("structured", [False, True])
async def test_firecrawl_reads_only_bounded_web_fields(
    respx_mock: respx.MockRouter, structured: bool,
) -> None:
    from interview_assistant.retrieval.mcp_client import NativeMCPClient

    payload = {"success": True, "data": {
        "web": [{"url": f"https://example.test/{number}", "title": f"Result {number}",
                 "description": "x" * 9_000, "markdown": "DO NOT FORWARD SCRAPED CONTENT"}
                for number in range(8)],
        "images": [{"url": "https://images.test/private"}],
    }, "metadata": "DO NOT FORWARD METADATA"}
    mock_firecrawl(respx_mock, payload=payload, structured=structured)
    result = await NativeMCPClient(environ={}).retrieve(search_request())
    assert len(result) <= 12_000
    assert all(f"https://example.test/{number}" in result for number in range(5))
    assert all(f"https://example.test/{number}" not in result for number in range(5, 8))
    assert "DO NOT FORWARD" not in result
    assert "images.test" not in result


@pytest.mark.asyncio
async def test_firecrawl_discards_unsafe_urls_and_invalid_rows(respx_mock: respx.MockRouter) -> None:
    from interview_assistant.retrieval.mcp_client import NativeMCPClient

    urls = ["javascript:alert(1)", "file:///private", "https://user:secret@example.test/",
            "https://example.test/\nforged", "https://example.test:bad/", "https://[broken",
            "https://example.test/\\evil", "https://safe.example.test/"]
    payload = {"success": True, "data": {"web": [None, "invalid", *[
        {"url": url, "title": "safe title", "description": "safe description"} for url in urls
    ]]}}
    mock_firecrawl(respx_mock, payload=payload)
    result = await NativeMCPClient(environ={}).retrieve(search_request())
    assert "https://safe.example.test/" in result
    assert result.count("URL:") == 1
    assert all(value not in result for value in ("javascript", "file:", "secret", "forged"))


@pytest.mark.asyncio
@pytest.mark.parametrize("structured", [False, True])
@pytest.mark.parametrize("payload, code", [
    ({"success": False, "error": {"statusCode": 401, "message": "private-secret-token"}},
     "credentials"),
    ({"success": True, "data": {"success": False, "error": {
        "statusCode": 402, "message": "private-secret-token"}}}, "credits"),
    ({"success": False, "error": {"response": {"status": 429},
                                   "message": "private-secret-token"}}, "rate_limit"),
    ({"success": False, "error": "API key is required: private-secret-token"}, "credentials"),
    ({"success": False, "error": "Insufficient credits: private-secret-token"}, "credits"),
])
async def test_firecrawl_http_200_errors_are_classified_without_exposing_payloads(
    respx_mock: respx.MockRouter, caplog: pytest.LogCaptureFixture,
    structured: bool, payload: dict, code: str,
) -> None:
    from interview_assistant.retrieval.mcp_client import NativeMCPClient, NativeMCPError

    _, messages = mock_firecrawl(respx_mock, payload=payload, structured=structured)
    with pytest.raises(NativeMCPError) as caught:
        await NativeMCPClient(environ={}).retrieve(search_request())
    assert caught.value.code == code
    assert "private-secret-token" not in str(caught.value)
    assert "private-secret-token" not in caplog.text
    assert len([message for message in messages if message["method"] == "tools/call"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, {"success": True, "data": {"web": "invalid"}},
                                    {"success": False, "data": {"web": []}}])
async def test_firecrawl_rejects_malformed_or_unsuccessful_payloads(
    respx_mock: respx.MockRouter, payload: dict,
) -> None:
    from interview_assistant.retrieval.mcp_client import NativeMCPClient, NativeMCPError

    mock_firecrawl(respx_mock, payload=payload)
    with pytest.raises(NativeMCPError):
        await NativeMCPClient(environ={}).retrieve(search_request())


@pytest.mark.asyncio
async def test_firecrawl_empty_search_is_valid(respx_mock: respx.MockRouter) -> None:
    from interview_assistant.retrieval.mcp_client import NativeMCPClient

    mock_firecrawl(respx_mock, payload={"success": True, "data": {"web": []}})
    assert await NativeMCPClient(environ={}).retrieve(search_request()) == ""


@pytest.mark.asyncio
async def test_firecrawl_is_error_cannot_return_search_content(respx_mock: respx.MockRouter) -> None:
    from interview_assistant.retrieval.mcp_client import NativeMCPClient, NativeMCPError

    mock_firecrawl(respx_mock, is_error=True)
    with pytest.raises(NativeMCPError):
        await NativeMCPClient(environ={}).retrieve(search_request())


@pytest.mark.asyncio
@pytest.mark.parametrize("message", [None, "Anonymous keyless access is unavailable for this request.\n\n"
                                    "Fix: Create an API key at the official dashboard."])
async def test_firecrawl_keyless_access_error_requires_a_key(
    respx_mock: respx.MockRouter, message: str | None,
) -> None:
    from interview_assistant.retrieval.mcp_client import NativeMCPClient, NativeMCPError

    # Recorded live endpoint shape: HTTP 200, MCP isError + structuredContent.
    # The stable machine code is sufficient even if provider prose changes.
    payload = {"code": "KEYLESS_ACCESS_NOT_AVAILABLE", "request_id": "private-request-id",
               "auth_mode": "keyless", "docs_url": "https://docs.firecrawl.dev/"}
    if message is not None:
        payload["message"] = message
    _, messages = mock_firecrawl(respx_mock, payload=payload, structured=True, is_error=True)
    with pytest.raises(NativeMCPError) as caught:
        await NativeMCPClient(environ={}).retrieve(search_request())
    assert caught.value.code == "key_required"
    assert "FIRECRAWL_API_KEY" in str(caught.value)
    assert "private-request-id" not in str(caught.value)
    assert len([message for message in messages if message["method"] == "tools/call"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_code, code", [
    ("KEYLESS_QUOTA_EXHAUSTED", "credits"),
    ("KEYLESS_LIMIT_REACHED", "rate_limit"),
    ("CREDENTIAL_INVALID", "credentials"),
])
async def test_firecrawl_symbolic_error_codes_do_not_depend_on_provider_prose(
    respx_mock: respx.MockRouter, provider_code: str, code: str,
) -> None:
    from interview_assistant.retrieval.mcp_client import NativeMCPClient, NativeMCPError

    mock_firecrawl(respx_mock, payload={"code": provider_code}, structured=True, is_error=True)
    with pytest.raises(NativeMCPError) as caught:
        await NativeMCPClient(environ={}).retrieve(search_request())
    assert caught.value.code == code


@pytest.mark.asyncio
async def test_firecrawl_rejects_oversized_json_before_parsing(respx_mock: respx.MockRouter) -> None:
    from interview_assistant.retrieval.mcp_client import NativeMCPClient, NativeMCPError

    mock_firecrawl(respx_mock, payload=search_payload(description="x" * 256_000))
    with pytest.raises(NativeMCPError):
        await NativeMCPClient(environ={}).retrieve(search_request())


def configuration(tmp_path: Path, *, mode: str = "normal", **options: object) -> Path:
    server = {
        "command": sys.executable,
        "args": [str(FIXTURE_SERVER)],
        "env": {"TEST_MCP_EVENTS": str(tmp_path / "events.jsonl"), "TEST_MCP_MODE": mode},
        **options,
    }
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {"firecrawl": server, "context7": server}}),
                    encoding="utf-8")
    return path


def events(tmp_path: Path) -> list[dict[str, object]]:
    path = tmp_path / "events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def search_request() -> SearchIntegration:
    return SearchPolicy("auto").integrations_for(
        "My name is Alice Smith. What is the latest Python release?",
        full_transcript="private transcript never leaves policy",
    )[0]


@pytest.mark.asyncio
async def test_real_stdio_search_only_sends_the_policy_query(tmp_path: Path) -> None:
    from interview_assistant.retrieval.mcp_client import NativeMCPClient

    client = NativeMCPClient(configuration(tmp_path), timeout_seconds=15)
    result = await client.retrieve(search_request())
    await client.aclose()

    assert "Python release details." in result
    assert [row for row in events(tmp_path) if row["event"] != "start"] == [{
        "event": "search", "query": "What is the latest Python release?", "limit": 5,
        "sources": [{"type": "web"}], "highlights": False,
        "explicit": None, "inherited": None,
    }]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["normal", "text-library"])
async def test_context7_resolves_before_querying_docs(tmp_path: Path, mode: str) -> None:
    from interview_assistant.retrieval.mcp_client import NativeMCPClient

    client = NativeMCPClient(configuration(tmp_path, mode=mode), timeout_seconds=15)
    request = SearchPolicy("auto").integrations_for("How to configure FastAPI lifespan?")[0]
    result = await client.retrieve(request)

    assert "async context manager" in result
    assert [row for row in events(tmp_path) if row["event"] != "start"] == [
        {"event": "resolve", "query": "How to configure FastAPI lifespan?",
         "libraryName": "FastAPI"},
        {"event": "docs", "query": "How to configure FastAPI lifespan?",
         "libraryId": "/fastapi/fastapi"},
    ]


@pytest.mark.asyncio
async def test_context7_does_not_invent_a_library_id(tmp_path: Path) -> None:
    from interview_assistant.retrieval.mcp_client import NativeMCPClient, NativeMCPError

    client = NativeMCPClient(configuration(tmp_path, mode="no-library"), timeout_seconds=15)
    request = SearchPolicy("auto").integrations_for("How to configure FastAPI lifespan?")[0]
    with pytest.raises(NativeMCPError, match="library"):
        await client.retrieve(request)
    assert [row["event"] for row in events(tmp_path)] == ["start", "resolve"]


@pytest.mark.asyncio
async def test_only_explicit_environment_values_are_sent_to_stdio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from interview_assistant.retrieval.mcp_client import NativeMCPClient

    monkeypatch.setenv("PRIVATE_TEST_SECRET", "do-not-inherit")
    path = configuration(tmp_path, env={
        "TEST_MCP_EVENTS": str(tmp_path / "events.jsonl"),
        "TEST_EXPLICIT": "prefix-${TEST_VALUE}",
    })
    client = NativeMCPClient(path, timeout_seconds=15, environ={"TEST_VALUE": "approved"})
    await client.retrieve(search_request())
    search = next(row for row in events(tmp_path) if row["event"] == "search")
    assert search["explicit"] == "prefix-approved"
    assert search["inherited"] is None


@pytest.mark.asyncio
async def test_missing_environment_value_fails_before_starting_server(tmp_path: Path) -> None:
    from interview_assistant.retrieval.mcp_client import NativeMCPClient, NativeMCPError

    path = configuration(tmp_path, args=["${MISSING_MCP_COMMAND_ARG}"])
    with pytest.raises(NativeMCPError, match="MISSING_MCP_COMMAND_ARG"):
        client = NativeMCPClient(path, environ={})
        await client.retrieve(search_request())
    assert not (tmp_path / "events.jsonl").exists()


@pytest.mark.asyncio
async def test_probe_requires_the_allowlisted_tools_but_calls_none(tmp_path: Path) -> None:
    from interview_assistant.retrieval.mcp_client import NativeMCPClient

    client = NativeMCPClient(configuration(tmp_path, mode="missing-search"), timeout_seconds=15)
    assert await client.probe("mcp/firecrawl") is False
    assert await client.probe("mcp/context7") is True
    assert {row["event"] for row in events(tmp_path)} == {"start"}


@pytest.mark.asyncio
async def test_result_is_bounded_before_becoming_model_context(tmp_path: Path) -> None:
    from interview_assistant.retrieval.mcp_client import NativeMCPClient

    client = NativeMCPClient(configuration(tmp_path, mode="large"), timeout_seconds=15)
    result = await client.retrieve(search_request())
    assert "x" * 100 in result
    assert 100 <= len(result) <= 12_000


@pytest.mark.asyncio
async def test_upstream_error_details_do_not_escape(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    from interview_assistant.retrieval.mcp_client import NativeMCPClient, NativeMCPError

    client = NativeMCPClient(configuration(tmp_path, mode="error"), timeout_seconds=15)
    with pytest.raises(NativeMCPError) as caught:
        await client.retrieve(search_request())
    assert "secret-token" not in str(caught.value)
    assert "secret-token" not in caplog.text


@pytest.mark.asyncio
async def test_disabled_search_is_explicitly_unavailable(tmp_path: Path) -> None:
    from interview_assistant.retrieval.mcp_client import NativeMCPClient, NativeMCPError

    path = tmp_path / "mcp.json"
    path.write_text('{"mcpServers": {"firecrawl": {"disabled": true}}}', encoding="utf-8")
    client = NativeMCPClient(path, environ={})
    assert await client.probe("mcp/firecrawl") is False
    with pytest.raises(NativeMCPError, match="firecrawl"):
        await client.retrieve(search_request())


@pytest.mark.asyncio
async def test_forged_integration_is_rejected_before_transport(tmp_path: Path) -> None:
    from interview_assistant.retrieval.mcp_client import NativeMCPClient

    client = NativeMCPClient(configuration(tmp_path))
    request = search_request()
    object.__setattr__(request, "allowed_tools", ("delete_all_files",))
    with pytest.raises((TypeError, ValueError)):
        await client.retrieve(request)
    assert not (tmp_path / "events.jsonl").exists()


def process_running(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        api.OpenProcess.restype = wintypes.HANDLE
        api.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        api.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = api.OpenProcess(0x00100000, False, pid)
        if not handle:
            return False
        try:
            return api.WaitForSingleObject(handle, 0) == 258
        finally:
            api.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def wait_for_search(tmp_path: Path) -> int:
    async with asyncio.timeout(15):
        while True:
            if (tmp_path / "events.jsonl").exists():
                rows = events(tmp_path)
                if any(row["event"] == "search" for row in rows):
                    return int(rows[0]["pid"])
            await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_cancelled_retrieval_closes_real_subprocess(tmp_path: Path) -> None:
    from interview_assistant.retrieval.mcp_client import NativeMCPClient

    client = NativeMCPClient(configuration(tmp_path, mode="slow"), timeout_seconds=30)
    task = asyncio.create_task(client.retrieve(search_request()))
    pid = await wait_for_search(tmp_path)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not process_running(pid)


@pytest.mark.asyncio
async def test_close_cancels_inflight_retrieval_and_refuses_new_work(tmp_path: Path) -> None:
    from interview_assistant.retrieval.mcp_client import NativeMCPClient, NativeMCPError

    client = NativeMCPClient(configuration(tmp_path, mode="slow"), timeout_seconds=30)
    task = asyncio.create_task(client.retrieve(search_request()))
    pid = await wait_for_search(tmp_path)
    await client.aclose()
    assert task.cancelled()
    assert not process_running(pid)
    with pytest.raises(NativeMCPError, match="closed"):
        await client.retrieve(search_request())


@pytest.mark.asyncio
async def test_timeout_cleans_up_the_transport(tmp_path: Path) -> None:
    from interview_assistant.retrieval.mcp_client import NativeMCPClient, NativeMCPError

    client = NativeMCPClient(configuration(tmp_path, mode="slow"), timeout_seconds=3)
    with pytest.raises(NativeMCPError, match="timed out"):
        await client.retrieve(search_request())
    rows = events(tmp_path)
    assert not process_running(int(rows[0]["pid"]))


@pytest.mark.asyncio
@respx.mock
async def test_streamable_http_uses_explicit_headers_and_sanitized_arguments(tmp_path: Path) -> None:
    from interview_assistant.retrieval.mcp_client import NativeMCPClient

    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {"firecrawl": {
        "url": "https://mcp.example.test/mcp", "headers": {"Authorization": "Bearer ${MCP_KEY}"},
    }}}), encoding="utf-8")
    messages = []

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer private-api-key"
        message = json.loads(request.content)
        messages.append(message)
        if "id" not in message:
            return httpx.Response(202)
        method = message["method"]
        if method == "initialize":
            result = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "remote-test", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": [{"name": "firecrawl_search", "inputSchema": {
                "type": "object", "properties": {"query": {"type": "string"}},
                "required": ["query"],
            }}]}
        else:
            assert method == "tools/call"
            result = {"content": [{"type": "text", "text": json.dumps(search_payload())}]}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": result})

    respx.post("https://mcp.example.test/mcp").mock(side_effect=respond)
    client = NativeMCPClient(path, environ={"MCP_KEY": "private-api-key"})
    assert "Current Python documentation" in await client.retrieve(search_request())
    calls = [message["params"] for message in messages if message["method"] == "tools/call"]
    assert calls == [{"name": "firecrawl_search", "arguments": {
        "query": "What is the latest Python release?",
    }}]


@pytest.mark.asyncio
@respx.mock
async def test_default_context7_uses_optional_api_key_without_configuration() -> None:
    from interview_assistant.retrieval.mcp_client import NativeMCPClient

    route = respx.post("https://mcp.context7.com/mcp").respond(503)
    client = NativeMCPClient(environ={"CONTEXT7_API_KEY": "private-context7-key"})
    assert await client.probe("mcp/context7") is False
    assert route.calls[0].request.headers["CONTEXT7_API_KEY"] == "private-context7-key"


@pytest.mark.asyncio
@pytest.mark.parametrize("server", [
    {"command": "python", "url": "https://mcp.example.test/mcp"},
    {"command": "python", "args": "not-a-list"},
    {"url": "file:///private"},
    {"url": "https://mcp.example.test/mcp?api_key=secret"},
])
async def test_invalid_configuration_fails_before_io(tmp_path: Path, server: dict) -> None:
    from interview_assistant.retrieval.mcp_client import NativeMCPClient, NativeMCPError

    path = tmp_path / "invalid.json"
    path.write_text(json.dumps({"mcpServers": {"firecrawl": server}}), encoding="utf-8")
    client = NativeMCPClient(path, environ={})
    with pytest.raises(NativeMCPError) as caught:
        await client.retrieve(search_request())
    assert "secret" not in str(caught.value)
