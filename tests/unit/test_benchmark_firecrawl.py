import asyncio
import json
from pathlib import Path

import httpx
import pytest
import respx

from interview_assistant.retrieval.policy import SearchPolicy


MCP_URL = "https://mcp.firecrawl.dev/v2/mcp"
CREDIT_URL = "https://api.firecrawl.dev/v2/team/credit-usage"


def mock_mcp(router: respx.MockRouter, *, payload: dict, structured: bool = True,
             timeout: bool = False, block_method: str | None = None,
             method_started: asyncio.Event | None = None) -> list[dict]:
    messages = []

    async def respond(request: httpx.Request) -> httpx.Response:
        message = json.loads(request.content)
        messages.append(message)
        if message["method"] == block_method:
            if method_started is not None:
                method_started.set()
            await asyncio.Event().wait()
        await asyncio.sleep(0.002)
        if "id" not in message:
            return httpx.Response(202)
        if message["method"] == "initialize":
            result = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "measurement-test", "version": "1"}}
        elif message["method"] == "tools/list":
            result = {"tools": [{"name": "firecrawl_search", "inputSchema": {
                "type": "object", "properties": {"query": {"type": "string"},
                "limit": {"type": "integer"}, "sources": {"type": "array"},
                "highlights": {"type": "boolean"}}, "required": ["query"],
            }}]}
        else:
            assert message["method"] == "tools/call"
            if timeout:
                raise httpx.ReadTimeout("private-upstream-error")
            result = {"content": [] if structured else [{
                "type": "text", "text": json.dumps(payload),
            }], "isError": payload.get("success") is False}
            if structured:
                result["structuredContent"] = payload
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": result})

    router.post(MCP_URL).mock(side_effect=respond)
    return messages


def search_payload(**extra: object) -> dict:
    return {"success": True, "data": {"web": [{
        "url": "https://docs.python.org/", "title": "Python", "description": "Documentation",
        "creditsUsed": 999,  # Search-page contents are not billing metadata.
    }]}, **extra}


@pytest.mark.asyncio
@pytest.mark.parametrize("structured", [False, True])
async def test_measured_client_retains_production_contract_and_captures_only_metrics(
    respx_mock: respx.MockRouter, structured: bool,
) -> None:
    from scripts.benchmark_firecrawl import MeasuredMCPClient

    messages = mock_mcp(respx_mock, payload=search_payload(creditsUsed=2), structured=structured)
    client = MeasuredMCPClient(environ={"FIRECRAWL_API_KEY": "private-key"})
    integration = SearchPolicy("forced").integrations_for(
        "My name is Alice Smith. Explain Python task cancellation?"
    )[0]
    result = await client.retrieve(integration)
    measurement = client.last_measurement
    assert "https://docs.python.org/" in result
    assert measurement is not None
    assert measurement["status"] == "ok"
    assert measurement["initialize_seconds"] > 0
    assert measurement["list_tools_seconds"] > 0
    assert measurement["total_seconds"] >= sum((measurement["initialize_seconds"],
                                               measurement["list_tools_seconds"]))
    assert measurement["result_count"] == 1
    assert measurement["result_characters"] == len(result)
    assert measurement["tool_calls"][0]["credits_used"] == 2
    assert measurement["tool_calls"][0]["credit_fields"] == {"creditsUsed": 2}
    assert measurement["tool_calls"][0]["seconds"] > 0
    assert "private-key" not in json.dumps(client.measurements)
    assert "Alice" not in json.dumps(client.measurements)
    assert "Documentation" not in json.dumps(client.measurements)
    tool_call = next(message["params"] for message in messages if message["method"] == "tools/call")
    assert tool_call == {"name": "firecrawl_search", "arguments": {
        "query": "Explain Python task cancellation?", "limit": 5,
        "sources": [{"type": "web"}], "highlights": False,
    }}


@pytest.mark.asyncio
@pytest.mark.parametrize("fields, expected", [({}, None), ({"creditsUsed": True}, None),
                                              ({"credits": 2}, 2)])
async def test_absent_billing_is_unknown_and_page_credit_fields_are_ignored(
    respx_mock: respx.MockRouter, fields: dict, expected: int | None,
) -> None:
    from scripts.benchmark_firecrawl import MeasuredMCPClient

    mock_mcp(respx_mock, payload=search_payload(**fields))
    client = MeasuredMCPClient(environ={})
    await client.retrieve(SearchPolicy("forced").integrations_for("Explain TCP congestion control?")[0])
    assert client.last_measurement["tool_calls"][0]["credits_used"] == expected


@pytest.mark.asyncio
async def test_timeout_keeps_partial_timings_and_marks_possible_charge(respx_mock: respx.MockRouter):
    from scripts.benchmark_firecrawl import MeasuredMCPClient
    from interview_assistant.retrieval.mcp_client import NativeMCPError

    mock_mcp(respx_mock, payload={}, timeout=True)
    client = MeasuredMCPClient(environ={})
    with pytest.raises(NativeMCPError):
        await client.retrieve(SearchPolicy("forced").integrations_for("Explain TCP slow start?")[0])
    measurement = client.last_measurement
    assert measurement["error_code"] == "timeout"
    assert measurement["timeout_may_charge"] is True
    assert measurement["tool_calls"][0]["seconds"] > 0
    assert measurement["tool_calls"][0]["credits_used"] is None
    assert "private-upstream-error" not in json.dumps(measurement)


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_stage, may_charge, billing_observation", [
    ("initialize", False, "no_tool_submitted"),
    ("tools/call", True, "unknown"),
])
async def test_outer_wait_for_records_cancelled_search_as_potentially_billable(
    respx_mock: respx.MockRouter, blocked_stage: str, may_charge: bool, billing_observation: str,
) -> None:
    from scripts.benchmark_firecrawl import MeasuredMCPClient

    started = asyncio.Event()
    messages = mock_mcp(respx_mock, payload={}, block_method=blocked_stage, method_started=started)
    client = MeasuredMCPClient(environ={}, timeout_seconds=30)
    integration = SearchPolicy("forced").integrations_for("Explain TLS key exchange?")[0]
    task = asyncio.create_task(client.retrieve(integration))
    await asyncio.wait_for(started.wait(), timeout=5)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(task, timeout=0.02)
    measurement = client.last_measurement
    assert measurement["status"] == "cancelled"
    assert measurement["timeout_may_charge"] is may_charge
    assert measurement["billing_observation"] == billing_observation
    assert measurement["schema_version"] == 2
    assert len([message for message in messages if message["method"] == "tools/call"]) == int(may_charge)
    if may_charge:
        assert measurement["tool_calls"][0]["response_received"] is False
        assert measurement["tool_calls"][0]["credits_used"] is None
    await client.aclose()


@pytest.mark.asyncio
async def test_concurrent_measurements_are_separate(respx_mock: respx.MockRouter) -> None:
    from scripts.benchmark_firecrawl import MeasuredMCPClient

    mock_mcp(respx_mock, payload=search_payload(creditsUsed=2))
    client = MeasuredMCPClient(environ={})
    integrations = [SearchPolicy("forced").integrations_for(query)[0] for query in (
        "Explain database transaction isolation?", "Explain TCP flow control?",
    )]
    await asyncio.gather(*(client.retrieve(integration) for integration in integrations))
    assert len(client.measurements) == 2
    assert all(len(row["tool_calls"]) == 1 for row in client.measurements)
    assert all(row["status"] == "ok" for row in client.measurements)


@pytest.mark.asyncio
async def test_balance_reads_only_official_endpoint_and_reports_allowlisted_fields(
    respx_mock: respx.MockRouter,
) -> None:
    from scripts.benchmark_firecrawl import read_credit_balance

    route = respx_mock.get(CREDIT_URL).respond(200, json={"success": True, "data": {
        "remainingCredits": 450, "planCredits": 500, "private": "not-for-output",
    }})
    balance = await read_credit_balance({"FIRECRAWL_API_KEY": "private-key",
                                         "OPENROUTER_API_KEY": "other-key"})
    assert balance["remaining_credits"] == 450
    assert balance["plan_credits"] == 500
    assert route.calls[0].request.headers["authorization"] == "Bearer private-key"
    assert "other-key" not in str(route.calls[0].request.headers)
    assert "private" not in json.dumps(balance)
    assert "not-for-output" not in json.dumps(balance)


@pytest.mark.asyncio
@pytest.mark.parametrize("status, code", [(401, "credentials"), (402, "credits"), (429, "rate_limit")])
async def test_balance_errors_are_safe_and_never_retried(
    respx_mock: respx.MockRouter, status: int, code: str,
) -> None:
    from scripts.benchmark_firecrawl import read_credit_balance

    route = respx_mock.get(CREDIT_URL).respond(status, text="private-upstream-error")
    result = await read_credit_balance({"FIRECRAWL_API_KEY": "private-key"})
    assert result["error_code"] == code
    assert result["remaining_credits"] is None
    assert route.call_count == 1
    assert "private" not in json.dumps(result)


@pytest.mark.asyncio
async def test_balance_missing_key_does_not_start_http(respx_mock: respx.MockRouter) -> None:
    from scripts.benchmark_firecrawl import read_credit_balance

    balance = await read_credit_balance({})
    assert balance["error_code"] == "key_required"
    assert not respx_mock.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, True, -1, "500"])
async def test_invalid_balance_cannot_be_used_as_spending_permission(
    respx_mock: respx.MockRouter, value: object,
) -> None:
    from scripts.benchmark_firecrawl import read_credit_balance

    respx_mock.get(CREDIT_URL).respond(200, json={"success": True, "data": {"remainingCredits": value}})
    balance = await read_credit_balance({"FIRECRAWL_API_KEY": "private-key"})
    assert balance["status"] == "error"
    assert balance["remaining_credits"] is None


@pytest.mark.asyncio
async def test_benchmark_stops_before_search_with_low_or_unavailable_balance(
    respx_mock: respx.MockRouter, tmp_path: Path,
) -> None:
    from scripts.benchmark_firecrawl import run_benchmark

    respx_mock.get(CREDIT_URL).respond(200, json={"success": True, "data": {"remainingCredits": 99}})
    report = await run_benchmark(environ={"FIRECRAWL_API_KEY": "private-key"},
                                 output_path=tmp_path / "report.json", count=2, interval_seconds=0)
    assert report["stopped_reason"] == "credit_floor"
    assert report["measurements"] == []
    assert all(call.request.method == "GET" for call in respx_mock.calls)


@pytest.mark.asyncio
async def test_benchmark_stops_after_first_rate_limit_and_records_balance_delta(
    respx_mock: respx.MockRouter, tmp_path: Path,
) -> None:
    from scripts.benchmark_firecrawl import run_benchmark

    respx_mock.get(CREDIT_URL).mock(side_effect=[
        httpx.Response(200, json={"success": True, "data": {"remainingCredits": 500}}),
        httpx.Response(200, json={"success": True, "data": {"remainingCredits": 498}}),
    ])
    messages = mock_mcp(respx_mock, payload={"success": False, "code": "KEYLESS_LIMIT_REACHED"})
    report = await run_benchmark(environ={"FIRECRAWL_API_KEY": "private-key"},
                                 output_path=tmp_path / "report.json", count=2, interval_seconds=0)
    assert report["stopped_reason"] == "rate_limit"
    assert len(report["measurements"]) == 1
    assert report["observed_balance_delta"] == 2
    assert len([message for message in messages if message["method"] == "tools/call"]) == 1
    assert "private-key" not in (tmp_path / "report.json").read_text()


def test_cli_reports_failed_measurements_with_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from scripts import benchmark_firecrawl

    async def failed_benchmark(**kwargs):
        return {"stopped_reason": None, "measurements": [{"status": "error", "error_code": "timeout"}]}

    monkeypatch.setattr(benchmark_firecrawl, "environment_values", lambda path: {})
    monkeypatch.setattr(benchmark_firecrawl, "run_benchmark", failed_benchmark)
    assert benchmark_firecrawl.main(["--output", str(tmp_path / "report.json")]) == 1
