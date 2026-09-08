"""Bounded Firecrawl measurements, reusing the production retrieval boundary.

Run with ``python -m scripts.benchmark_firecrawl --balance-only`` to read credits,
or omit that flag for the curated sequential search benchmark. No upgrades, paid
fallbacks, retries, crawling, or scraping are performed. The standalone runner
requires a readable balance and stops below 100 credits. MeasuredMCPClient only
instruments retrieval; embedding runners must apply their own spending guard.

``initialize_seconds`` includes transport/session construction and the MCP
initialize handshake, matching a cold production request. ``total_seconds``
also includes production parsing and transport shutdown. Balance deltas belong
to the authenticated team and can include other activity; timeouts may charge.
Schema 2 also treats external cancellation after tool submission as potentially
billable: this wrapper cannot distinguish asyncio.wait_for from user cancellation.

Official contracts checked 2026-09-08:
https://docs.firecrawl.dev/api-reference/endpoint/credit-usage
https://docs.firecrawl.dev/api-reference/endpoint/search
https://docs.firecrawl.dev/billing
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
import json
import math
from pathlib import Path
from time import perf_counter
from typing import Any, cast

import httpx
from mcp import ClientSession
from mcp.types import CallToolResult, ListToolsResult

from interview_assistant.config import environment_values
from interview_assistant.retrieval.mcp_client import (
    NativeMCPClient,
    NativeMCPError,
    _firecrawl_payload,
    _safe_web_url,
)
from interview_assistant.retrieval.models import FIRECRAWL_ID, SearchIntegration
from interview_assistant.retrieval.policy import SearchPolicy


CREDIT_USAGE_URL = "https://api.firecrawl.dev/v2/team/credit-usage"
MIN_REMAINING_CREDITS = 100
DEFAULT_OUTPUT = Path("build/stress-2026-09-08/firecrawl-benchmark.json")
_measurement: ContextVar[dict[str, Any] | None] = ContextVar("mcp_measurement", default=None)

CURATED_QUERIES = (
    {"id": "python_concurrency_en", "language": "en", "query": (
        "Compare current Python free-threaded builds with asyncio and multiprocessing: "
        "how do cancellation, extension compatibility, shared mutable state, and CPU-bound "
        "work affect the design of a low-latency streaming service? Give practical tradeoffs."
    )},
    {"id": "postgres_concurrency_ru", "language": "ru", "query": (
        "Как сравнить уровни изоляции PostgreSQL, optimistic locking и SELECT FOR UPDATE "
        "для конкурентного обновления остатков? Объясни write skew, deadlock, повтор транзакции "
        "и идемпотентность; какие проверки выявят потерянные обновления и двойное списание?"
    )},
    {"id": "distributed_delivery_en", "language": "en", "query": (
        "How would you combine a transactional outbox, Kafka delivery, idempotency keys, "
        "and database transactions for an order workflow? Explain exactly-once claims, "
        "crash recovery, consumer rebalances, duplicate events, and measurable latency tradeoffs."
    )},
    {"id": "auth_boundaries_ru", "language": "ru", "query": (
        "Как спроектировать авторизацию веб-приложения с OAuth 2.0 Authorization Code и PKCE? "
        "Сравни хранение сессии в cookie и access token, ротацию refresh token, CSRF, XSS, "
        "проверку audience и issuer, отзыв доступа и требования к безопасному журналированию."
    )},
    {"id": "tls_http3_en", "language": "en", "query": (
        "Compare TLS 1.3 over HTTP/2 with QUIC and HTTP/3 under packet loss: explain stream "
        "multiplexing, head-of-line blocking, connection migration, certificate validation, "
        "0-RTT replay risks, and how to benchmark tail latency without confusing warm and cold paths."
    )},
    {"id": "kubernetes_resilience_ru", "language": "ru", "query": (
        "Как обеспечить устойчивость сервиса Kubernetes при медленной базе и перегрузке? "
        "Сравни readiness и liveness, requests и limits, backpressure, circuit breaker, "
        "тайм-ауты, graceful shutdown и retry budget; как отличить причину каскадного отказа?"
    )},
    {"id": "supply_chain_en", "language": "en", "query": (
        "How should a software team combine SBOMs, dependency pinning, artifact signing, "
        "build provenance, reproducible builds, and vulnerability response? Explain what "
        "each control proves, remaining trust assumptions, and practical validation in CI."
    )},
    {"id": "observability_ru", "language": "ru", "query": (
        "Как найти источник роста p99 задержки в распределённом приложении? Разбери очереди, "
        "насыщение пула соединений, GC, fan-out, coordinated omission и sampling трассировок; "
        "какие метрики и контролируемые эксперименты отделят корреляцию от причины?"
    )},
)


def _numeric(value: object) -> int | float | None:
    if type(value) not in (int, float):
        return None
    number = cast(int | float, value)
    if number < 0 or (isinstance(number, float) and not math.isfinite(number)):
        return None
    return number


def _response_metrics(result: CallToolResult) -> tuple[dict[str, int | float], int | None]:
    try:
        payload = _firecrawl_payload(result)
    except NativeMCPError:
        return {}, None
    # Only envelope billing fields, never similarly named content inside a page.
    credits: dict[str, int | float] = {}
    for field in ("creditsUsed", "credits"):
        if (number := _numeric(payload.get(field))) is not None:
            credits[field] = number
    data = payload.get("data")
    count = None
    if (not result.isError and payload.get("success") is True and isinstance(data, dict)
            and isinstance(data.get("web"), list)):
        count = min(5, sum(isinstance(row, dict) and _safe_web_url(row.get("url")) is not None
                           for row in data["web"][:50]))
    return credits, count


class _MeasuredSession:
    def __init__(self, session: ClientSession, measurement: dict[str, Any]) -> None:
        self.session = session
        self.measurement = measurement

    async def list_tools(self, *args: Any, **kwargs: Any) -> ListToolsResult:
        started = perf_counter()
        try:
            return await self.session.list_tools(*args, **kwargs)
        finally:
            self.measurement["list_tools_seconds"] += perf_counter() - started

    async def call_tool(self, name: str, *args: Any, **kwargs: Any) -> CallToolResult:
        call: dict[str, Any] = {
            "name": name, "seconds": None, "credits_used": None, "credit_fields": {},
            "result_count": None, "response_received": False,
        }
        self.measurement["tool_calls"].append(call)
        started = perf_counter()
        try:
            result = await self.session.call_tool(name, *args, **kwargs)
            call["response_received"] = True
            if name == "firecrawl_search":
                credits, count = _response_metrics(result)
                call["credit_fields"] = credits
                call["credits_used"] = credits.get("creditsUsed", credits.get("credits"))
                call["result_count"] = count
            return result
        finally:
            call["seconds"] = perf_counter() - started


class MeasuredMCPClient(NativeMCPClient):
    """Normal NativeMCPClient API plus safe, per-retrieval ``measurements``.

    No raw query, tool response, endpoint headers, or credentials are recorded.
    Concurrent tasks keep their measurements separate using a ContextVar.
    """

    def __init__(self, config_path: Path | None = None, *, timeout_seconds: float = 5.0,
                 environ: Mapping[str, str] | None = None) -> None:
        super().__init__(config_path, timeout_seconds=timeout_seconds, environ=environ)
        self.measurements: list[dict[str, Any]] = []

    @property
    def last_measurement(self) -> dict[str, Any] | None:
        return self.measurements[-1] if self.measurements else None

    async def retrieve(self, integration: SearchIntegration) -> str:
        measurement: dict[str, Any] = {
            "schema_version": 2,
            "integration_id": integration.id if type(integration) is SearchIntegration else None,
            "observed_at_utc": datetime.now(UTC).isoformat(),
            "total_seconds": None, "initialize_seconds": None, "list_tools_seconds": 0.0,
            "tool_calls": [], "result_count": None, "result_characters": 0,
            "status": "error", "error_code": None, "timeout_may_charge": False,
            "billing_observation": "no_tool_submitted",
        }
        started = perf_counter()
        token = _measurement.set(measurement)
        try:
            result = await super().retrieve(integration)
            measurement["status"] = "ok"
            measurement["result_characters"] = len(result)
            if integration.id == FIRECRAWL_ID and measurement["tool_calls"]:
                measurement["result_count"] = measurement["tool_calls"][-1]["result_count"]
            return result
        except NativeMCPError as error:
            measurement["error_code"] = error.code
            measurement["timeout_may_charge"] = (
                error.code == "timeout" and bool(measurement["tool_calls"])
            )
            raise
        except asyncio.CancelledError:
            measurement["status"] = "cancelled"
            measurement["error_code"] = "cancelled"
            # ApplicationRuntime uses an outer asyncio.wait_for. Its deadline
            # reaches this coroutine as cancellation, not NativeMCPError(timeout).
            # Losing the response does not undo a server-side search or charge.
            measurement["timeout_may_charge"] = bool(measurement["tool_calls"])
            raise
        except Exception:
            measurement["error_code"] = "unavailable"
            raise
        finally:
            measurement["total_seconds"] = perf_counter() - started
            if measurement["tool_calls"]:
                measurement["billing_observation"] = (
                    "reported" if all(call["credits_used"] is not None
                                      for call in measurement["tool_calls"]) else "unknown"
                )
            self.measurements.append(measurement)
            _measurement.reset(token)

    @asynccontextmanager
    async def _session(self, integration_id: str) -> AsyncIterator[ClientSession]:
        measurement = _measurement.get()
        if measurement is None:
            async with super()._session(integration_id) as session:
                yield session
            return
        started = perf_counter()
        try:
            async with super()._session(integration_id) as session:
                measurement["initialize_seconds"] = perf_counter() - started
                yield cast(ClientSession, _MeasuredSession(session, measurement))
        finally:
            if measurement["initialize_seconds"] is None:
                measurement["initialize_seconds"] = perf_counter() - started


async def read_credit_balance(environ: Mapping[str, str], *, timeout_seconds: float = 10.0
                              ) -> dict[str, Any]:
    """One authenticated, read-only GET; return only credit counts and safe status."""
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("Credit balance timeout must be positive and finite")
    balance: dict[str, Any] = {
        "observed_at_utc": datetime.now(UTC).isoformat(), "status": "error", "error_code": None,
        "remaining_credits": None, "plan_credits": None, "seconds": None,
    }
    started = perf_counter()
    key = environ.get("FIRECRAWL_API_KEY")
    try:
        if not key:
            balance["error_code"] = "key_required"
            return balance
        async with asyncio.timeout(timeout_seconds):
            async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False) as client:
                response = await client.get(CREDIT_USAGE_URL, headers={"Authorization": f"Bearer {key}"})
                response.raise_for_status()
                if len(response.content) > 64_000:
                    raise ValueError("Credit response exceeded the bounded schema")
                payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if payload.get("success") is not True or not isinstance(data, dict):
            raise ValueError("Invalid credit response")
        remaining = _numeric(data.get("remainingCredits"))
        if remaining is None:
            raise ValueError("Credit response does not provide a numeric balance")
        balance.update(status="ok", remaining_credits=remaining,
                       plan_credits=_numeric(data.get("planCredits")))
    except (TimeoutError, httpx.TimeoutException):
        balance["error_code"] = "timeout"
    except httpx.HTTPStatusError as error:
        balance["error_code"] = {401: "credentials", 403: "credentials", 402: "credits",
                                 429: "rate_limit"}.get(error.response.status_code, "unavailable")
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        balance["error_code"] = "unavailable"
    finally:
        balance["seconds"] = perf_counter() - started
    return balance


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


async def run_benchmark(*, environ: Mapping[str, str], output_path: Path = DEFAULT_OUTPUT,
                        count: int = 6, timeout_seconds: float = 10.0,
                        interval_seconds: float = 6.1) -> dict[str, Any]:
    """Run at most eight different curated cases sequentially with credit guards."""
    if not 1 <= count <= len(CURATED_QUERIES):
        raise ValueError("Benchmark count must be between one and eight")
    if not math.isfinite(interval_seconds) or interval_seconds < 0:
        raise ValueError("Benchmark interval must be nonnegative and finite")
    report: dict[str, Any] = {
        "observed_at_utc": datetime.now(UTC).isoformat(), "requested_cases": count,
        "measurements": [], "balances": [], "stopped_reason": None,
        "observed_balance_delta": None,
        "notes": ["Initialization includes transport setup and the MCP handshake.",
                  "Only server-provided creditsUsed/credits are recorded; absent means unknown.",
                  "Balance belongs to the team and may include concurrent external activity.",
                  "Timeouts may consume credits; no request is retried."],
        "sources": ["https://docs.firecrawl.dev/api-reference/endpoint/credit-usage",
                    "https://docs.firecrawl.dev/api-reference/endpoint/search",
                    "https://docs.firecrawl.dev/billing"],
    }
    balance = await read_credit_balance(environ)
    report["balances"].append(balance)
    client = MeasuredMCPClient(environ=environ, timeout_seconds=timeout_seconds)
    try:
        for case in CURATED_QUERIES[:count]:
            if balance["status"] != "ok":
                report["stopped_reason"] = "balance_unavailable"
                break
            if balance["remaining_credits"] < MIN_REMAINING_CREDITS:
                report["stopped_reason"] = "credit_floor"
                break
            if report["measurements"] and interval_seconds:
                await asyncio.sleep(interval_seconds)
            integration = SearchPolicy("forced").integrations_for(case["query"])[0]
            error_code = None
            try:
                await client.retrieve(integration)
            except NativeMCPError as error:
                error_code = error.code
            measurement = dict(client.last_measurement or {})
            measurement.update(case_id=case["id"], language=case["language"],
                               query_characters=len(integration.query))
            report["measurements"].append(measurement)
            balance = await read_credit_balance(environ)
            report["balances"].append(balance)
            first = report["balances"][0]
            if first["status"] == "ok" and balance["status"] == "ok":
                report["observed_balance_delta"] = first["remaining_credits"] - balance["remaining_credits"]
            _write_report(output_path, report)
            if error_code in ("credits", "rate_limit", "credentials", "key_required"):
                report["stopped_reason"] = error_code
                break
        if report["stopped_reason"] is None:
            if balance["status"] != "ok":
                report["stopped_reason"] = "balance_unavailable"
            elif balance["remaining_credits"] < MIN_REMAINING_CREDITS:
                report["stopped_reason"] = "credit_floor"
    finally:
        await client.aclose()
        _write_report(output_path, report)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, default=Path(__file__).resolve().parents[1] / ".env")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--balance-only", action="store_true")
    parser.add_argument("--count", type=int, choices=range(1, 9), default=6)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    args = parser.parse_args(argv)
    values = environment_values(args.env)
    if args.balance_only:
        report = asyncio.run(read_credit_balance(values, timeout_seconds=args.timeout_seconds))
        _write_report(args.output, report)
        succeeded = report["status"] == "ok"
    else:
        report = asyncio.run(run_benchmark(environ=values, output_path=args.output,
                                          count=args.count, timeout_seconds=args.timeout_seconds))
        succeeded = (report["stopped_reason"] is None
                     and all(row["status"] == "ok" for row in report["measurements"]))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
