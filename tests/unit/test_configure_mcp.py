import json
import re
from pathlib import Path, PureWindowsPath

import pytest


CONTEXT7_URL = "https://mcp.context7.com/mcp"


def _executable(tmp_path: Path, name: str = "duckduckgo-mcp-server.exe") -> Path:
    executable = tmp_path / name
    executable.write_bytes(b"MZ-test-placeholder")
    return executable.resolve()


def _desired_config(executable: Path) -> dict[str, object]:
    return {
        "mcpServers": {
            "context7": {"url": CONTEXT7_URL},
            "duckduckgo": {"command": str(executable), "args": []},
        }
    }


def test_configure_mcp_writes_only_the_exact_documented_config(tmp_path: Path) -> None:
    from scripts.configure_mcp import configure_mcp

    executable = _executable(tmp_path)

    output_path = configure_mcp(tmp_path, executable)

    assert output_path == tmp_path / "mcp.json"
    assert json.loads(output_path.read_text(encoding="utf-8")) == _desired_config(
        executable
    )
    assert output_path.read_bytes().endswith(b"\n")
    assert {path.name for path in tmp_path.iterdir()} == {
        executable.name,
        "mcp.json",
    }


def test_known_config_is_backed_up_with_collision_safe_utc_names(
    tmp_path: Path,
) -> None:
    from scripts.configure_mcp import configure_mcp

    executable = _executable(tmp_path)
    old_executable = _executable(tmp_path, "old-duckduckgo.exe")
    config_path = tmp_path / "mcp.json"
    original = {
        "mcpServers": {
            "context7": {"url": CONTEXT7_URL},
            "duckduckgo": {"command": str(old_executable), "args": []},
        }
    }
    config_path.write_text(json.dumps(original), encoding="utf-8")

    configure_mcp(tmp_path, executable)
    configure_mcp(tmp_path, executable)

    backups = sorted(tmp_path.glob("mcp.json.backup-*"))
    assert len(backups) == 2
    assert len({path.name for path in backups}) == 2
    assert all(
        re.fullmatch(
            r"mcp\.json\.backup-\d{8}T\d{12}Z(?:-\d+)?",
            path.name,
        )
        for path in backups
    )
    assert any(
        json.loads(path.read_text(encoding="utf-8")) == original
        for path in backups
    )
    assert json.loads(config_path.read_text(encoding="utf-8")) == _desired_config(
        executable
    )


@pytest.mark.parametrize(
    ("case", "existing"),
    [
        pytest.param("invalid-json", "{not-json", id="invalid-json"),
        pytest.param(
            "unexpected-top-level",
            {"mcpServers": {}, "future": {}},
            id="unexpected-top-level",
        ),
        pytest.param(
            "unknown-server",
            {"mcpServers": {"filesystem": {"command": "server.exe"}}},
            id="unknown-server",
        ),
        pytest.param(
            "secret-field",
            {
                "mcpServers": {
                    "context7": {
                        "url": CONTEXT7_URL,
                        "headers": {"Authorization": "super-secret-value"},
                    }
                }
            },
            id="secret-field",
        ),
        pytest.param(
            "relative-command",
            {
                "mcpServers": {
                    "duckduckgo": {
                        "command": "duckduckgo-mcp-server.exe",
                        "args": [],
                    }
                }
            },
            id="relative-command",
        ),
        pytest.param(
            "invalid-server-schema",
            {"mcpServers": {"context7": CONTEXT7_URL}},
            id="invalid-server-schema",
        ),
    ],
)
def test_invalid_existing_config_fails_before_backup_or_write(
    tmp_path: Path,
    case: str,
    existing: str | dict[str, object],
) -> None:
    from scripts.configure_mcp import MCPConfigError, configure_mcp

    executable = _executable(tmp_path)
    config_path = tmp_path / "mcp.json"
    raw = existing if isinstance(existing, str) else json.dumps(existing)
    config_path.write_text(raw, encoding="utf-8")
    original_bytes = config_path.read_bytes()

    with pytest.raises(MCPConfigError) as captured:
        configure_mcp(tmp_path, executable)

    expected_markers = {
        "invalid-json": "valid json",
        "unexpected-top-level": "top-level",
        "unknown-server": "unknown",
        "secret-field": "secret-like",
        "relative-command": "absolute existing file",
        "invalid-server-schema": "schema",
    }
    message = str(captured.value).casefold()
    assert expected_markers[case] in message
    assert "super-secret-value" not in message
    assert config_path.read_bytes() == original_bytes
    assert list(tmp_path.glob("mcp.json.backup-*")) == []
    assert list(tmp_path.glob(".mcp.json.*.tmp")) == []


def test_existing_missing_absolute_command_fails_before_backup(
    tmp_path: Path,
) -> None:
    from scripts.configure_mcp import MCPConfigError, configure_mcp

    executable = _executable(tmp_path)
    missing = (tmp_path / "missing.exe").resolve()
    config_path = tmp_path / "mcp.json"
    existing = {
        "mcpServers": {
            "duckduckgo": {"command": str(missing), "args": []},
        }
    }
    config_path.write_text(json.dumps(existing), encoding="utf-8")
    original_bytes = config_path.read_bytes()

    with pytest.raises(MCPConfigError, match="existing file"):
        configure_mcp(tmp_path, executable)

    assert config_path.read_bytes() == original_bytes
    assert list(tmp_path.glob("mcp.json.backup-*")) == []


@pytest.mark.parametrize("kind", ["relative", "missing", "directory"])
def test_requested_executable_must_be_an_absolute_existing_file(
    tmp_path: Path,
    kind: str,
) -> None:
    from scripts.configure_mcp import MCPConfigError, configure_mcp

    if kind == "relative":
        executable = Path("duckduckgo-mcp-server.exe")
    elif kind == "missing":
        executable = (tmp_path / "missing.exe").resolve()
    else:
        executable = tmp_path.resolve()

    with pytest.raises(MCPConfigError, match="absolute existing file"):
        configure_mcp(tmp_path, executable)

    assert not (tmp_path / "mcp.json").exists()
    assert list(tmp_path.glob("mcp.json.backup-*")) == []


def test_atomic_replace_failure_preserves_original_and_removes_temp_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import configure_mcp as configure_module

    executable = _executable(tmp_path)
    config_path = tmp_path / "mcp.json"
    original = {"mcpServers": {"context7": {"url": CONTEXT7_URL}}}
    config_path.write_text(json.dumps(original), encoding="utf-8")
    original_bytes = config_path.read_bytes()

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("simulated atomic replacement failure")

    monkeypatch.setattr(configure_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="atomic replacement failure"):
        configure_module.configure_mcp(tmp_path, executable)

    assert config_path.read_bytes() == original_bytes
    assert len(list(tmp_path.glob("mcp.json.backup-*"))) == 1
    assert list(tmp_path.glob(".mcp.json.*.tmp")) == []


def test_cli_boundary_accepts_only_explicit_paths(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts.configure_mcp import main

    executable = _executable(tmp_path)

    exit_code = main(
        [
            "--config-dir",
            str(tmp_path),
            "--duckduckgo-executable",
            str(executable),
        ]
    )

    assert exit_code == 0
    assert capsys.readouterr().out.strip() == str(tmp_path / "mcp.json")


def test_repository_template_is_exact_valid_and_secret_free() -> None:
    template_path = Path(__file__).parents[2] / "config" / "mcp.template.json"

    raw = template_path.read_text(encoding="utf-8")
    payload = json.loads(raw)

    assert payload == {
        "mcpServers": {
            "context7": {"url": CONTEXT7_URL},
            "duckduckgo": {
                "command": "C:\\absolute\\path\\duckduckgo-mcp-server.exe",
                "args": [],
            },
        }
    }
    assert PureWindowsPath(
        payload["mcpServers"]["duckduckgo"]["command"]
    ).is_absolute()
    assert not any(
        marker in raw.casefold()
        for marker in ("api_key", "apikey", "token", "secret", "headers", "env")
    )
