# Firecrawl MCP migration — 2026-09-08

General web search now uses the official Firecrawl hosted MCP service. Context7
remains the library-documentation service. The application uses the existing
official Python MCP SDK; no Firecrawl SDK, Node.js, local search server or LM Studio
plugin is required for the native backend.

## Provider contract and configuration

- Official setup: <https://docs.firecrawl.dev/mcp-server>.
- Endpoint: `https://mcp.firecrawl.dev/v2/mcp`, Streamable HTTP.
- `FIRECRAWL_API_KEY` is optional. The default client sends it in an Authorization
  Bearer header when present, and omits authentication when absent. Credentials
  are never placed in an endpoint URL.
- A custom `MCP_CONFIG` replaces the built-in definitions. To use a key in that
  mode, explicitly configure `Authorization: Bearer ${FIRECRAWL_API_KEY}` in its
  Firecrawl headers. This prevents forwarding account credentials to custom URLs.
- Only `firecrawl_search` is allowlisted for web retrieval, including when an
  account exposes additional tools. Search requests ask for five web results and
  plain descriptions; no page scraping, crawl, agent or feedback jobs are started.
- The current question is sanitized before routing, truncated to 500 characters
  for web search and sealed to its integration/tool allowlist. Context7 queries
  retain their existing behavior. The full interview transcript is not searched.
- Result evidence contains at most five web results and 12,000 characters. It is
  treated as untrusted reference material by the answer prompt.

The hosted schema was inspected through the official MCP SDK on 2026-09-08 local
time: server `firecrawl-fastmcp` version `3.24.1`. It declares `sources` as objects
such as `[{"type":"web"}]`, unlike some REST examples. The client only sends known
optional fields advertised by the connected server. This discovery performed no
search calls and does not constitute a functional search test.

## Limits and failure behavior

Keyless access has daily IP limits whose numerical values are not published.
It is distinct from the registered free account's 1,000 credits per monthly cycle.
The free account search rate is 10 requests per minute per team. Plain search with
1–10 returned results uses 2 credits, so 1,000 credits permit approximately 500
such searches if other account operations do not consume the allowance.

Sources checked on 2026-09-08: [pricing](https://www.firecrawl.dev/pricing),
[rate limits](https://docs.firecrawl.dev/rate-limits),
[search costs](https://docs.firecrawl.dev/features/search#cost-implications).

The application neither creates accounts nor upgrades plans. An explicitly
configured API key uses that account's existing terms. Search has a total deadline
and no automatic retry or alternate paid provider. A local timeout does not prove
that an upstream request consumed no credits.

Readiness performs initialize/list-tools only. It checks transport and the tool
contract without spending a search request, but cannot establish remaining quota
or whether the actual search tool will accept a credential.

## Migration and verification

Legacy YAML providers `exa`, `duckduckgo` and `searxng` normalize to `firecrawl`.
The optional configuration generator backs up and replaces recognized secret-free
Exa/DuckDuckGo entries. Custom or credentialed JSON definitions are rejected before
any overwrite. The previous Exa validation report remains historical evidence for
the earlier build, not evidence for this Firecrawl build.

### Live service results

One public English query was submitted through the official MCP SDK, using the
same endpoint and search arguments as the adapter, with TLS certificate validation
enabled and no key. Initialize completed in 0.813 seconds. The complete operation
took 9.283 seconds and returned an MCP `isError: true` response with structured
code `KEYLESS_ACCESS_NOT_AVAILABLE`. No search results were returned.

The account key was then configured in the ignored local `.env`. Two initial
production-client requests exceeded the 10-second budget. A diagnostic English
request with a 30-second ceiling succeeded in 3.536 seconds: initialize 0.998 s,
list-tools 1.291 s, search plus cleanup 1.247 s. A Russian request using the normal
10-second budget succeeded in 2.708 seconds. Both used `SearchPolicy` and the
production `NativeMCPClient`, returning five web results (1,533 and 1,490 characters).
No timeout default was increased and no automatic retries were introduced.

Both searches found official Python pages as well as older community articles.
The snippets disagree about the latest version: retrieval is not itself a verified
answer, and this test does not establish model answer accuracy or sustained search
reliability. The initial timeouts remain a material observation. No balance or
actual credit-consumption measurement was performed.

Evidence under `build/firecrawl-mcp-2026-09-08/`: `live-contract.json` (keyless
failure), `live-search.json` (initial account-key timeouts), `live-stages.json`
(successful English search), and `live-search-ru.json` (successful Russian search).
The reports contain public test queries and result snippets, not the credential.

### Offline verification

- Full suite: **1,067 passed, 5 skipped**, 92.05 seconds.
- Ruff: clean across the repository.
- Mypy: clean for `interview_assistant` and `main.py` (56 source files).
- Source-release YAML validator: passed with the Firecrawl template.
- Native MCP regression coverage includes real SDK transport with mocked HTTP,
  structured/text results, the observed keyless error, credentials/credit/rate
  errors including HTTP 200 responses, bounded results, cancellation and stdio
  process cleanup. Runtime integration tests verify that failed retrieval still
  produces a model answer and does not display raw exception bodies.

### Portable verification

New build: `dist/firecrawl-mcp-2026-09-08/InterviewAssistant/`. The complete folder,
including `_internal`, is required. The root August installer was not replaced.
The EXE's internal version remains 0.1.1; identify this preview by directory and hash.

- PyInstaller: exit 0, 164.77 seconds. The 69 snapshotted source inputs remained
  unchanged through the build and frozen verification.
- Embedded-code inspection confirms the Firecrawl endpoint and integration, with
  the previous Exa endpoint absent from the native client.
- Frozen offline self-test: exit 0, 1.564 seconds.
- Frozen diagnostics: exit 0, status `ok`, 1.873 seconds.
- Invalid provider in `.env`: rejected with exit 1. Retired local search-server
  command: rejected with exit 2.
- Required Qt and native MCP modules are present. The former project search
  server and `ddgs`, `primp`, `lxml` are absent.
- Full distribution inventory and pinned STT manifest validation: passed.
  Application directory: **4,161 files, 4,017,437,449 bytes**.
- The handoff contains a secret-free `.env.example` template, not the personal `.env`. The
  personal Firecrawl key was also checked against the EXE and output text/config
  files and was not found.

EXE SHA-256:
`d6919cefc380e1baf15ac9294ec3f1864bcfe5e81175663f4cf9da7e5bae036d`.

Build output still reports optional QtQuick helper DLL warnings; this application
uses Qt Widgets. A transient EXE append permission error was retried successfully
by PyInstaller. The frozen checks above do not constitute an interactive GUI
interview, live search from inside the frozen GUI, or a new GPU performance test.
The successful live searches exercised the source production MCP client.

The handoff root includes `SHA256SUMS.txt`, the distribution inventory, frozen
smoke evidence, live-search reports, and fresh pytest/Ruff/mypy outputs.
