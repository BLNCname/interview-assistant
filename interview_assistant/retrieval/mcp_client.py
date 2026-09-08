"""Read-only MCP retrieval independent of the selected LLM provider.

Only SearchPolicy capabilities enter this boundary. Server tool descriptions and
results never decide which tools run or supply a new search query. Each operation
owns its SDK contexts in one asyncio task, including cancellation and shutdown.
"""

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import timedelta
import json
import math
import os
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, Tool

from .models import (
    CONTEXT7_ID,
    CONTEXT7_TOOLS,
    FIRECRAWL_ID,
    FIRECRAWL_TOOLS,
    SearchIntegration,
)


MAX_RESULT_CHARACTERS = 12_000
_MAX_SEARCH_JSON_CHARACTERS = 256_000
_ALLOWED_TOOLS = {CONTEXT7_ID: CONTEXT7_TOOLS, FIRECRAWL_ID: FIRECRAWL_TOOLS}
_ERROR_MESSAGES = {
    "key_required": "Firecrawl requires an API key for this request; configure FIRECRAWL_API_KEY",
    "credentials": "MCP server credentials are missing or invalid",
    "credits": "MCP server credits or search quota exhausted",
    "rate_limit": "MCP server rate limit reached; try again later",
    "timeout": "MCP retrieval timed out",
    "unavailable": "MCP server unavailable or returned an invalid response",
}
_ENVIRONMENT_VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_LIBRARY_ID = re.compile(r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?")
_LIBRARY_ID_LINE = re.compile(
    r"(?im)^\s*(?:-\s*)?(?:Context7-compatible\s+)?library\s*ID\s*:\s*"
    r"`?(/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?)`?\s*$"
)
_LIBRARY_NAMES = (
    "FastAPI", "Pydantic", "Django", "Flask", "Next.js", "React", "Angular", "Vue",
    "Svelte", "Express", "Node.js", "TypeScript", "JavaScript", "Python", "SQLAlchemy",
    "NumPy", "Pandas", "PyTorch", "TensorFlow", "Spring", "Stripe", "OpenAI", "PyQt6",
    "Redis", "PostgreSQL", "MongoDB", "Docker", "Kubernetes", "Tailwind", "Prisma",
)


class NativeMCPError(RuntimeError):
    """Safe, user-facing MCP failure without upstream payloads or credentials."""

    def __init__(self, message: str, *, code: str = "unavailable") -> None:
        super().__init__(message)
        self.code = code if code in _ERROR_MESSAGES else "unavailable"


class NativeMCPClient:
    def __init__(
        self,
        config_path: Path | None = None,
        *,
        timeout_seconds: float = 5.0,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("MCP timeout must be positive and finite")
        self._config_path = Path(config_path) if config_path is not None else None
        self._timeout = timeout_seconds
        self._environ = os.environ if environ is None else environ
        self._tasks: set[asyncio.Task[Any]] = set()
        self._closed = False

    async def retrieve(self, integration: SearchIntegration) -> str:
        if type(integration) is not SearchIntegration:
            raise TypeError("MCP retrieval requires a SearchPolicy integration")
        # Revalidate the sealed query and exact tool allowlist before any I/O.
        query = integration.query
        async with self._operation():
            async with self._session(integration.id) as session:
                tools = await self._required_tools(session, integration.id)
                if integration.id == FIRECRAWL_ID:
                    arguments: dict[str, object] = {"query": query}
                    properties = tools["firecrawl_search"].inputSchema.get("properties", {})
                    # Search snippets only: never scrape, crawl, or ask the model
                    # to choose options from an untrusted server description.
                    options = {"limit": 5, "sources": [{"type": "web"}], "highlights": False}
                    for name, value in options.items():
                        if name in properties:
                            arguments[name] = value
                    result = await session.call_tool("firecrawl_search", arguments)
                    return _firecrawl_result(result)
                else:
                    resolved = await session.call_tool("resolve-library-id", {
                        "query": query, "libraryName": _library_name(query),
                    })
                    _check_result(resolved)
                    library_id = _resolved_library_id(resolved)
                    if library_id is None:
                        raise NativeMCPError("Context7 could not resolve a documentation library")
                    result = await session.call_tool("query-docs", {
                        "query": query, "libraryId": library_id,
                    })
                _check_result(result)
                return _result_text(result)

    async def probe(self, integration_id: str) -> bool:
        """Check initialize/list_tools without submitting an interview question."""
        try:
            async with self._operation():
                async with self._session(integration_id) as session:
                    await self._required_tools(session, integration_id)
            return True
        except NativeMCPError:
            return False

    async def aclose(self) -> None:
        self._closed = True
        current = asyncio.current_task()
        tasks = [task for task in self._tasks if task is not current]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    @asynccontextmanager
    async def _operation(self) -> AsyncIterator[None]:
        if self._closed:
            raise NativeMCPError("MCP client is closed")
        task = asyncio.current_task()
        assert task is not None
        self._tasks.add(task)
        try:
            async with asyncio.timeout(self._timeout):
                yield
        except TimeoutError:
            raise _safe_failure("timeout") from None
        except NativeMCPError:
            raise
        except Exception as exc:
            # SDK task groups wrap protocol failures. Never stringify their
            # potentially sensitive response bodies, endpoint URLs or arguments.
            pending: list[BaseException] = [exc]
            while pending:
                failure = pending.pop()
                if isinstance(failure, BaseExceptionGroup):
                    pending.extend(failure.exceptions)
                elif isinstance(failure, NativeMCPError):
                    raise NativeMCPError(str(failure), code=failure.code) from None
                elif isinstance(failure, httpx.TimeoutException):
                    raise _safe_failure("timeout") from None
                elif isinstance(failure, httpx.HTTPStatusError):
                    code = _status_code(failure.response.status_code)
                    if code is not None:
                        raise _safe_failure(code) from None
            raise _safe_failure("unavailable") from None
        finally:
            self._tasks.discard(task)

    @asynccontextmanager
    async def _session(self, integration_id: str) -> AsyncIterator[ClientSession]:
        config = self._server_configuration(integration_id)
        async with AsyncExitStack() as stack:
            if "command" in config:
                params = StdioServerParameters(
                    command=config["command"], args=config.get("args", []),
                    env=config.get("env", {}), cwd=config.get("cwd"),
                )
                # The SDK passes only its platform environment allowlist plus
                # these explicit env values, and uses CREATE_NO_WINDOW on Windows.
                # Do not persist arbitrary server stderr (it may contain secrets).
                errlog = stack.enter_context(open(os.devnull, "w", encoding="utf-8"))
                read, write = await stack.enter_async_context(stdio_client(params, errlog=errlog))
            else:
                http = await stack.enter_async_context(httpx.AsyncClient(
                    headers=config.get("headers", {}), timeout=self._timeout,
                    follow_redirects=False,
                ))
                read, write, _ = await stack.enter_async_context(streamable_http_client(
                    config["url"], http_client=http,
                ))
            session = await stack.enter_async_context(ClientSession(
                read, write, read_timeout_seconds=timedelta(seconds=self._timeout),
            ))
            await session.initialize()
            yield session

    async def _required_tools(self, session: ClientSession, integration_id: str) -> dict[str, Tool]:
        required = _ALLOWED_TOOLS.get(integration_id)
        if required is None:
            raise NativeMCPError("Unsupported MCP integration")
        found: dict[str, Tool] = {}
        cursor: str | None = None
        # Servers may paginate tool discovery. Bound it even for broken servers.
        for _ in range(4):
            page = await session.list_tools(cursor=cursor)
            for tool in page.tools:
                if tool.name in required:
                    found[tool.name] = tool
            if all(name in found for name in required):
                return found
            cursor = page.nextCursor
            if not cursor:
                break
        raise NativeMCPError(f"MCP integration {integration_id} lacks required read-only tools")

    def _server_configuration(self, integration_id: str) -> dict[str, Any]:
        if integration_id not in _ALLOWED_TOOLS:
            raise NativeMCPError("Unsupported MCP integration")
        key = integration_id.removeprefix("mcp/")
        if self._config_path is None:
            if integration_id == CONTEXT7_ID:
                api_key = self._environ.get("CONTEXT7_API_KEY", "")
                return {
                    "url": "https://mcp.context7.com/mcp",
                    "headers": {"CONTEXT7_API_KEY": api_key} if api_key else {},
                }
            # Firecrawl's official remote endpoint. An explicitly supplied key
            # is optional here; the server decides anonymous access and limits.
            api_key = self._environ.get("FIRECRAWL_API_KEY", "")
            return {
                "url": "https://mcp.firecrawl.dev/v2/mcp",
                "headers": {"Authorization": f"Bearer {api_key}"} if api_key else {},
            }
        try:
            if self._config_path.stat().st_size > 256_000:
                raise NativeMCPError("MCP configuration file is too large")
            document = json.loads(self._config_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            raise NativeMCPError("Cannot read MCP configuration; check MCP_CONFIG JSON") from None
        servers = document.get("mcpServers") if isinstance(document, dict) else None
        if not isinstance(servers, dict):
            raise NativeMCPError("MCP configuration requires a mcpServers object")
        config = servers.get(integration_id, servers.get(key))
        if not isinstance(config, dict) or config.get("disabled") is True:
            raise NativeMCPError(f"MCP integration {key} is not configured or is disabled")
        config = self._expand(config)
        has_command, has_url = "command" in config, "url" in config
        if has_command == has_url:
            raise NativeMCPError("MCP server requires exactly one command or url")
        if has_command:
            if not isinstance(config["command"], str) or not config["command"].strip():
                raise NativeMCPError("MCP command must be a nonempty string")
            args = config.get("args", [])
            if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
                raise NativeMCPError("MCP args must be a list of strings")
            _string_mapping(config.get("env", {}), "env")
            if "cwd" in config:
                if not isinstance(config["cwd"], str):
                    raise NativeMCPError("MCP cwd must be a string")
                path = Path(config["cwd"])
                if not path.is_absolute():
                    config["cwd"] = str(self._config_path.parent / path)
        else:
            if not isinstance(config["url"], str):
                raise NativeMCPError("MCP url must be a string")
            url = urlsplit(config["url"])
            if url.scheme not in ("https", "http") or not url.hostname:
                raise NativeMCPError("MCP url requires an HTTP or HTTPS endpoint")
            if url.username or url.password or url.query or url.fragment:
                raise NativeMCPError("Put MCP credentials in headers, not endpoint URLs")
            _string_mapping(config.get("headers", {}), "headers")
        return config

    def _expand(self, value: Any) -> Any:
        if isinstance(value, str):
            def replace(match: re.Match[str]) -> str:
                name = match.group(1)
                resolved = self._environ.get(name)
                if resolved is None or resolved == "":
                    raise NativeMCPError(f"Required MCP environment variable {name} is missing")
                return resolved
            return _ENVIRONMENT_VARIABLE.sub(replace, value)
        if isinstance(value, list):
            return [self._expand(item) for item in value]
        if isinstance(value, dict):
            return {key: self._expand(item) for key, item in value.items()}
        return value


def _string_mapping(value: object, field: str) -> None:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise NativeMCPError(f"MCP {field} must contain string values")


def _library_name(query: str) -> str:
    matches = [(match.start(), name) for name in _LIBRARY_NAMES
               if (match := re.search(r"(?i)(?<!\w)" + re.escape(name) + r"(?!\w)", query))]
    return min(matches)[1] if matches else query[:160]


def _check_result(result: CallToolResult) -> None:
    if result.isError:
        raise NativeMCPError("MCP read-only tool returned an error")


def _safe_failure(code: str) -> NativeMCPError:
    return NativeMCPError(_ERROR_MESSAGES[code], code=code)


def _status_code(value: object) -> str | None:
    if isinstance(value, (int, str)):
        return {
            "401": "credentials", "402": "credits", "429": "rate_limit",
            "KEYLESS_ACCESS_NOT_AVAILABLE": "key_required",
            "KEYLESS_QUOTA_EXHAUSTED": "credits",
            "KEYLESS_LIMIT_REACHED": "rate_limit",
            "CREDENTIAL_INVALID": "credentials",
        }.get(str(value))
    return None


def _error_code(payload: object) -> str:
    """Classify bounded error fields; never interpolate upstream text into errors."""
    pending = [payload]
    for _ in range(32):
        if not pending:
            break
        value = pending.pop(0)
        if isinstance(value, dict):
            for name in ("statusCode", "status_code", "status", "code"):
                if code := _status_code(value.get(name)):
                    return code
            for name in ("error", "message", "detail", "data", "result", "response", "cause"):
                nested = value.get(name)
                if isinstance(nested, (dict, str)):
                    pending.append(nested)
        elif isinstance(value, str):
            text = value[:4_096].lower()
            if any(term in text for term in ("rate limit", "too many requests")):
                return "rate_limit"
            if any(term in text for term in (
                "insufficient credit", "insufficient balance", "quota exceeded", "credit limit",
                "no credits", "credits exhausted", "payment required",
            )):
                return "credits"
            if any(term in text for term in (
                "api key", "api_key", "apikey", "unauthorized", "authentication",
            )):
                return "credentials"
    return "unavailable"


def _firecrawl_payload(result: CallToolResult) -> dict[str, Any]:
    if isinstance(result.structuredContent, dict):
        return result.structuredContent
    for block in result.content[:8]:
        if block.type != "text" or not block.text:
            continue
        if len(block.text) > _MAX_SEARCH_JSON_CHARACTERS:
            raise _safe_failure("unavailable")
        try:
            value = json.loads(block.text)
        except (ValueError, RecursionError):
            raise _safe_failure(_error_code(block.text)) from None
        if isinstance(value, dict):
            return value
    raise _safe_failure("unavailable")


def _safe_web_url(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 2_048:
        return None
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 or char == "\\" for char in value):
        return None
    try:
        url = urlsplit(value)
        if (url.scheme not in ("http", "https") or not url.hostname
                or url.username is not None or url.password is not None):
            return None
        _ = url.port  # Validate malformed or out-of-range ports without opening a URL.
    except ValueError:
        return None
    return value


def _firecrawl_result(result: CallToolResult) -> str:
    payload = _firecrawl_payload(result)
    data = payload.get("data")
    if (result.isError or payload.get("success") is not True or not isinstance(data, dict)
            or "error" in payload or "error" in data or data.get("success") is False
            or not isinstance(data.get("web"), list)):
        raise _safe_failure(_error_code(payload))
    chunks: list[str] = []
    # Bound inspection as well as output. Ignore all non-web fields and any
    # optional scraped content, even if the server unexpectedly returns them.
    for item in data["web"][:50]:
        if not isinstance(item, dict) or (url := _safe_web_url(item.get("url"))) is None:
            continue
        title = item.get("title")
        description = item.get("description")
        title = title[:300].replace("\r", " ").replace("\n", " ") if isinstance(title, str) else ""
        description = description[:2_400] if isinstance(description, str) else ""
        chunk = f"Title: {title}\nURL: {url}\nDescription: {description}"
        chunks.append(chunk[:2_398])
        if len(chunks) == 5:
            break
    return "\n\n".join(chunks)[:MAX_RESULT_CHARACTERS]


def _result_text(result: CallToolResult) -> str:
    chunks: list[str] = []
    remaining = MAX_RESULT_CHARACTERS
    for item in result.content:
        if item.type == "text" and item.text:
            chunk = item.text[:remaining]
            chunks.append(chunk)
            remaining -= len(chunk) + 1
            if remaining <= 0:
                break
    if chunks:
        return "\n".join(chunks)[:MAX_RESULT_CHARACTERS]
    if result.structuredContent:
        # Bound serialization as well as the returned string.
        chunks = []
        remaining = MAX_RESULT_CHARACTERS
        for chunk in json.JSONEncoder(ensure_ascii=False).iterencode(result.structuredContent):
            chunks.append(chunk[:remaining])
            remaining -= len(chunk)
            if remaining <= 0:
                break
        return "".join(chunks)
    return ""


def _resolved_library_id(result: CallToolResult) -> str | None:
    pending: list[object] = [result.structuredContent]
    # Only inspect named structured IDs; never take an arbitrary slash from prose.
    for _ in range(100):
        if not pending:
            break
        item = pending.pop(0)
        if isinstance(item, dict):
            for key in ("libraryId", "id", "context7CompatibleLibraryID"):
                candidate = item.get(key)
                if isinstance(candidate, str) and _LIBRARY_ID.fullmatch(candidate):
                    return candidate
            for key in ("results", "libraries", "result", "data"):
                nested = item.get(key)
                if isinstance(nested, (dict, list)):
                    pending.append(nested)
        elif isinstance(item, list):
            pending.extend(item[:25])
    match = _LIBRARY_ID_LINE.search(_result_text(result))
    return match.group(1) if match else None
