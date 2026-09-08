"""Small real stdio MCP server; the integration tests never need external services."""

import asyncio
import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent


server = FastMCP("interview-assistant-test")
mode = os.environ.get("TEST_MCP_MODE", "normal")
events_path = Path(os.environ["TEST_MCP_EVENTS"])


def record(event: str, **values: object) -> None:
    with events_path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"event": event, **values}, ensure_ascii=False) + "\n")


record("start", pid=os.getpid())


@server.tool(name="resolve-library-id")
async def resolve_library(query: str, libraryName: str) -> CallToolResult:
    record("resolve", query=query, libraryName=libraryName)
    if mode == "no-library":
        return CallToolResult(content=[TextContent(type="text", text="No libraries found.")])
    if mode == "text-library":
        return CallToolResult(content=[TextContent(
            type="text", text="- Title: FastAPI\n- Context7-compatible library ID: /fastapi/fastapi"
        )])
    return CallToolResult(
        content=[TextContent(type="text", text="See https://example.test/not-an-id")],
        structuredContent={"results": [{"id": "/fastapi/fastapi", "title": "FastAPI"}]},
    )


@server.tool(name="query-docs")
async def query_docs(query: str, libraryId: str) -> str:
    record("docs", query=query, libraryId=libraryId)
    return "Use an async context manager for FastAPI lifespan."


if mode != "missing-search":
    @server.tool(name="firecrawl_search")
    async def search(
        query: str, limit: int = 5, sources: list[dict[str, str]] | None = None,
        highlights: bool = False,
    ) -> CallToolResult:
        record("search", query=query, limit=limit, sources=sources, highlights=highlights,
               explicit=os.environ.get("TEST_EXPLICIT"),
               inherited=os.environ.get("PRIVATE_TEST_SECRET"))
        if mode == "slow":
            await asyncio.sleep(60)
        if mode == "error":
            return CallToolResult(isError=True, content=[TextContent(
                type="text", text="upstream secret-token-that-must-not-be-logged"
            )])
        text = "Python release details." if mode != "large" else "x" * 100_000
        payload = {"success": True, "data": {"web": [{
            "url": "https://www.python.org/", "title": "Python", "description": text,
        }]}}
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(payload))])


@server.tool()
async def delete_all_files() -> str:
    record("forbidden")
    raise RuntimeError("Unrelated tools must never be called")


if __name__ == "__main__":
    server.run(transport="stdio")
