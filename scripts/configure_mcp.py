"""Generate the official Context7 and Firecrawl remote MCP configuration."""

from __future__ import annotations

import argparse
import json
import os
import re
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


CONTEXT7_URL = "https://mcp.context7.com/mcp"
FIRECRAWL_URL = "https://mcp.firecrawl.dev/v2/mcp"
EXA_URL = "https://mcp.exa.ai/mcp"
# Accept old generated search entries only to back them up and replace them.
KNOWN_SERVERS = frozenset({"context7", "firecrawl", "exa", "duckduckgo"})

_SECRET_KEY_NAMES = {
    "apikey",
    "auth",
    "authorization",
    "bearer",
    "clientsecret",
    "credential",
    "env",
    "environment",
    "header",
    "headers",
    "key",
    "password",
    "passwd",
    "secret",
    "signature",
    "token",
}
_SECRET_KEY_SUFFIXES = (
    "apikey",
    "authtoken",
    "clientsecret",
    "credential",
    "password",
    "secret",
    "signature",
    "token",
)


class MCPConfigError(ValueError):
    """Raised when configuration input is unsafe or malformed."""


def configure_mcp(
    config_dir: str | Path,
) -> Path:
    """Validate inputs, back up a known config, and atomically write mcp.json."""

    directory = Path(config_dir)
    if not directory.exists() or not directory.is_dir():
        raise MCPConfigError("Config directory must be an existing directory")

    config_path = directory / "mcp.json"
    if config_path.is_symlink():
        raise MCPConfigError("mcp.json must not be a symbolic link")
    if config_path.exists() and not config_path.is_file():
        raise MCPConfigError("mcp.json must be a regular file")

    existing_bytes: bytes | None = None
    if config_path.exists():
        existing_bytes = config_path.read_bytes()
        _validate_existing_config(existing_bytes)

    payload = {
        "mcpServers": {
            "context7": {"url": CONTEXT7_URL},
            "firecrawl": {"url": FIRECRAWL_URL},
        }
    }
    serialized = (
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")

    if existing_bytes is not None:
        _write_backup(config_path, existing_bytes)
    _atomic_write(config_path, serialized)
    return config_path


def _validate_existing_config(raw: bytes) -> None:
    try:
        text = raw.decode("utf-8")
        payload = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise MCPConfigError("Existing mcp.json must contain valid JSON") from None

    if not isinstance(payload, dict) or set(payload) != {"mcpServers"}:
        raise MCPConfigError("Existing mcp.json has unexpected top-level data")
    if _contains_secret_like_field(payload):
        raise MCPConfigError("Existing mcp.json contains a secret-like field")

    servers = payload["mcpServers"]
    if not isinstance(servers, dict):
        raise MCPConfigError("Existing mcpServers must use the documented schema")
    unknown_servers = set(servers) - KNOWN_SERVERS
    if unknown_servers:
        raise MCPConfigError("Existing mcp.json contains an unknown MCP server")

    context7 = servers.get("context7")
    if "context7" in servers:
        if (
            not isinstance(context7, dict)
            or set(context7) != {"url"}
            or context7.get("url") != CONTEXT7_URL
        ):
            raise MCPConfigError("Existing Context7 server has an invalid schema")

    exa = servers.get("exa")
    if "exa" in servers:
        if (
            not isinstance(exa, dict)
            or set(exa) != {"url"}
            or exa.get("url") != EXA_URL
        ):
            raise MCPConfigError("Existing Exa server has an invalid schema")

    if "firecrawl" in servers:
        firecrawl = servers["firecrawl"]
        if (
            not isinstance(firecrawl, dict)
            or set(firecrawl) != {"url"}
            or firecrawl.get("url") != FIRECRAWL_URL
        ):
            raise MCPConfigError("Existing Firecrawl server has an invalid schema")

    duckduckgo = servers.get("duckduckgo")
    if "duckduckgo" in servers:
        if (
            not isinstance(duckduckgo, dict)
            or set(duckduckgo) != {"command", "args"}
            or duckduckgo.get("args") != []
            or not isinstance(duckduckgo.get("command"), str)
        ):
            raise MCPConfigError(
                "Existing DuckDuckGo server has an invalid schema"
            )
        # The executable is never launched and may already have been removed.
        # Validate the old generated shape before backing up and dropping it.
        if not Path(duckduckgo["command"]).is_absolute():
            raise MCPConfigError("Existing DuckDuckGo command must be an absolute path")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MCPConfigError("Existing mcp.json contains a duplicate JSON key")
        result[key] = value
    return result


def _contains_secret_like_field(value: object) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).casefold())
            if normalized in _SECRET_KEY_NAMES or normalized.endswith(
                _SECRET_KEY_SUFFIXES
            ):
                return True
            if _contains_secret_like_field(child):
                return True
    elif isinstance(value, list):
        return any(_contains_secret_like_field(child) for child in value)
    return False


def _write_backup(config_path: Path, data: bytes) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    base_name = f"{config_path.name}.backup-{timestamp}"
    attempt = 0
    while True:
        suffix = "" if attempt == 0 else f"-{attempt}"
        backup_path = config_path.with_name(base_name + suffix)
        try:
            with backup_path.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            attempt += 1
            continue
        return backup_path


def _atomic_write(config_path: Path, data: bytes) -> None:
    temp_path = config_path.with_name(
        f".{config_path.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        with temp_path.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, config_path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Write official Context7 and keyless Firecrawl MCP endpoints."
    )
    parser.add_argument(
        "--config-dir",
        required=True,
        type=Path,
        help="Existing destination directory; LM Studio is optional.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        output_path = configure_mcp(
            args.config_dir,
        )
    except MCPConfigError as error:
        parser.error(str(error))
    print(output_path)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
