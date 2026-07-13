from dataclasses import dataclass


CONTEXT7_ID = "mcp/context7"
CONTEXT7_TOOLS = ("resolve-library-id", "query-docs")
DUCKDUCKGO_ID = "mcp/duckduckgo"
DUCKDUCKGO_TOOLS = ("search",)

_TOOLS_BY_INTEGRATION = {
    CONTEXT7_ID: CONTEXT7_TOOLS,
    DUCKDUCKGO_ID: DUCKDUCKGO_TOOLS,
}


@dataclass(frozen=True, slots=True)
class SearchIntegration:
    """A sanitized local query paired with one exact LM Studio tool allowlist."""

    id: str
    query: str
    allowed_tools: tuple[str, ...]

    def __post_init__(self) -> None:
        expected_tools = _TOOLS_BY_INTEGRATION.get(self.id)
        if expected_tools is None:
            raise ValueError("Unsupported search integration")
        if not isinstance(self.allowed_tools, tuple):
            raise TypeError("allowed_tools must be an immutable tuple")
        if self.allowed_tools != expected_tools:
            raise ValueError("Search integration tools must match the allowlist")
        if not self.query.strip():
            raise ValueError("Search integration query must not be empty")

    def to_lmstudio(self) -> dict[str, object]:
        """Serialize only fields documented by LM Studio's plugin contract."""

        return {
            "type": "plugin",
            "id": self.id,
            "allowed_tools": list(self.allowed_tools),
        }
