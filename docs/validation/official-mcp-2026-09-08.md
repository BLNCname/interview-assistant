# Official, free MCP search replacement — 2026-09-08

The application now connects to the official Exa hosted MCP server for web search
and to Upstash Context7 for library documentation, using the official Python MCP
SDK. The previous project-owned DuckDuckGo server and the `ddgs`, `primp` and now
unused `lxml` dependencies were removed from the source environment and lockfile.

## Provenance and cost

- [Exa's product page](https://exa.ai/mcp) documents free use without an API key
  and the endpoint `https://mcp.exa.ai/mcp`.
- [Exa's setup documentation](https://exa.ai/docs/reference/exa-mcp) describes
  anonymous rate limits and the `web_search_exa` tool. The hosted service is
  maintained by Exa; its deployed version is not pinned by this repository.
- [Context7](https://github.com/upstash/context7) remains the official Upstash
  service at `https://mcp.context7.com/mcp`.
- The [official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
  is locked to `mcp==1.30.0`; the SDK wheel SHA-256 is
  `666edb5009503e1047c9d60346a756f94b261f05cc2625f23d41c728ffc484d0`.

Exa receives no key by default. Explicitly setting `EXA_API_KEY` uses that
account's terms via an `x-api-key` header. The application never obtains a key,
enables billing, switches to a paid provider, or retries through another provider
after an anonymous limit. A network failure leaves retrieval unavailable while
the interview can continue without retrieved context.

## Application behavior

Only `web_search_exa` is allowlisted for general search. The query comes from the
existing privacy policy; full transcripts are not passed into this retrieval
boundary. Results remain untrusted and limited to 12,000 characters.

Exa and Context7 use HTTPS through the SDK. No local search subprocess is started
by the default configuration. The retired `--mcp-web-search-server` flag exits
with code 2 before starting the GUI. Generic explicitly configured MCP stdio
clients and test-only fixture servers remain supported.

Legacy YAML search providers `duckduckgo` and `searxng` migrate to `exa`.
The configuration generator now writes official HTTPS endpoints without requiring
a search executable; a recognized legacy entry is backed up and replaced even if
its old executable was already removed. Unknown configurations are not overwritten.
Current-version questions in Russian and English route to Exa; explicit library
documentation/API questions still route to Context7.

## Verification

- Full suite: **1,013 passed, 5 skipped in 82.68 seconds**.
- Ruff: no issues. Mypy: no issues in 56 source files.
- Source-release YAML validator passed with the Exa template.
- Targeted transport tests cover the official tool schema, anonymous requests,
  explicit credentials, HTTP 429, cancellation/timeouts and the fixed tool allowlist.
- Anonymous live English search through `NativeMCPClient` succeeded: 4,059
  characters, initially 3.338 seconds and 13.176 seconds on a later attempt.
- One Russian request timed out after 20.015 seconds. A subsequent direct official
  SDK check with a Russian public query succeeded: initialization 7.065 seconds,
  search 3.034 seconds (three results requested). The deployed schema advertises
  `query` and optional `numResults`. No API key or paid account was used.

These measurements show that free hosted search is available but latency varies;
they do not establish a guaranteed response time. Configured timeouts intentionally
bound how long the application waits for retrieval.

## Portable build

The updated application is delivered in
`dist/official-mcp-2026-09-08/InterviewAssistant/` with the bundled STT model.
Build and frozen validation evidence is recorded under
`build/official-mcp-2026-09-08/`. The previous `release-preview-2026-09-08` directory
and root 0.1.1 installer are older artifacts; they do not contain this replacement.

The build completed successfully in 159.25 seconds. Frozen validation passed:
offline self-test 1.567 seconds, no-GUI diagnostics 2.741 seconds, invalid `.env`
rejection, and rejection of the retired server command. Archive and filesystem
inspection found no old search server, `ddgs`, `primp` or `lxml`. The required
SDK client modules and diagnostics are present; the captured source inputs match.
Binary dependency paths stay within the project environment, its Python runtime
and Windows directories.

EXE SHA-256:
`5cea3936a6bf37a7d0f74abb9b9526b98552a0f7e3e9c0b811d0722303d579e2`.

Detailed results are in `frozen-smoke.json` and the distribution inventory.
Live service checks and frozen startup checks are separate; a full GUI interview
session is not part of this migration check. The internal EXE version remains
0.1.1; use this named distribution directory and checksum to identify this build.
