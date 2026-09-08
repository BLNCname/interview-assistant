from collections.abc import Callable
from dataclasses import dataclass, field
from hmac import compare_digest, digest
import json
from secrets import token_bytes
from typing import final


CONTEXT7_ID = "mcp/context7"
CONTEXT7_TOOLS = ("resolve-library-id", "query-docs")
FIRECRAWL_ID = "mcp/firecrawl"
FIRECRAWL_TOOLS = ("firecrawl_search",)
FIRECRAWL_QUERY_LIMIT = 500

_TOOLS_BY_INTEGRATION = {
    CONTEXT7_ID: CONTEXT7_TOOLS,
    FIRECRAWL_ID: FIRECRAWL_TOOLS,
}


@final
@dataclass(frozen=True, slots=True, init=False)
class _SanitizedQuestion:
    """Opaque text issued only by the retrieval policy boundary."""

    _text: str
    _proof: object = field(repr=False, compare=False)
    _mac: bytes = field(repr=False, compare=False)

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("SanitizedQuestion values are policy-internal")

    @classmethod
    def _create(
        cls,
        text: str,
        proof: object,
        mac: bytes,
    ) -> "_SanitizedQuestion":
        value = object.__new__(cls)
        object.__setattr__(value, "_text", text)
        object.__setattr__(value, "_proof", proof)
        object.__setattr__(value, "_mac", mac)
        return value


def _make_question_authority() -> tuple[
    Callable[[str, str, tuple[str, ...]], _SanitizedQuestion],
    Callable[[object, str, tuple[str, ...]], str],
]:
    proof = object()
    secret = token_bytes(32)

    def signed_payload(
        text: str,
        integration_id: str,
        allowed_tools: tuple[str, ...],
    ) -> bytes:
        return json.dumps(
            (integration_id, allowed_tools, text),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

    def issue(
        text: str,
        integration_id: str,
        allowed_tools: tuple[str, ...],
    ) -> _SanitizedQuestion:
        if not text.strip():
            raise ValueError("Sanitized question must not be empty")
        mac = digest(
            secret,
            signed_payload(text, integration_id, allowed_tools),
            "sha256",
        )
        return _SanitizedQuestion._create(text, proof, mac)

    def unwrap(
        value: object,
        integration_id: str,
        allowed_tools: tuple[str, ...],
    ) -> str:
        if (
            type(value) is not _SanitizedQuestion
            or getattr(value, "_proof", None) is not proof
        ):
            raise TypeError("Search query must be sanitized by SearchPolicy")
        text = getattr(value, "_text", None)
        if not isinstance(text, str):
            raise TypeError("Search query must be sanitized by SearchPolicy")
        mac = getattr(value, "_mac", None)
        expected_mac = digest(
            secret,
            signed_payload(text, integration_id, allowed_tools),
            "sha256",
        )
        if not isinstance(mac, bytes) or not compare_digest(mac, expected_mac):
            raise TypeError("Search query must be sanitized by SearchPolicy")
        return text

    return issue, unwrap


_issue_sanitized_question, _unwrap_sanitized_question = (
    _make_question_authority()
)
del _make_question_authority


@final
@dataclass(frozen=True, slots=True, init=False)
class SearchIntegration:
    """A sanitized local query paired with one exact MCP tool allowlist."""

    id: str
    _sanitized_question: _SanitizedQuestion = field(repr=False)
    allowed_tools: tuple[str, ...]

    def __init__(
        self,
        id: str,
        query: _SanitizedQuestion,
        allowed_tools: tuple[str, ...],
    ) -> None:
        _unwrap_sanitized_question(query, id, allowed_tools)
        object.__setattr__(self, "id", id)
        object.__setattr__(self, "_sanitized_question", query)
        object.__setattr__(self, "allowed_tools", allowed_tools)
        self.__post_init__()

    def __post_init__(self) -> None:
        self._validate()

    def _validate(self) -> str:
        query = _unwrap_sanitized_question(
            self._sanitized_question,
            self.id,
            self.allowed_tools,
        )
        expected_tools = _TOOLS_BY_INTEGRATION.get(self.id)
        if expected_tools is None:
            raise ValueError("Unsupported search integration")
        if not isinstance(self.allowed_tools, tuple):
            raise TypeError("allowed_tools must be an immutable tuple")
        if self.allowed_tools != expected_tools:
            raise ValueError("Search integration tools must match the allowlist")
        if self.id == FIRECRAWL_ID and len(query) > FIRECRAWL_QUERY_LIMIT:
            raise ValueError("Search query exceeds the Firecrawl query limit")
        return query

    @property
    def query(self) -> str:
        return self._validate()

    def to_lmstudio(self) -> dict[str, object]:
        """Serialize only fields documented by LM Studio's plugin contract."""

        self._validate()
        return {
            "type": "plugin",
            "id": self.id,
            "allowed_tools": list(self.allowed_tools),
        }

    def _query_for_retrieval(self) -> str:
        return self._validate()


def _policy_search_integration(
    integration_id: str,
    query: str,
    allowed_tools: tuple[str, ...],
) -> SearchIntegration:
    """Private factory for policy-vetted, sanitized retrieval output."""

    return SearchIntegration(
        integration_id,
        _issue_sanitized_question(query, integration_id, allowed_tools),
        allowed_tools,
    )
