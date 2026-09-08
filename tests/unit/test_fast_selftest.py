import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize(("integration_id", "tools", "expected_status"), [
    ("mcp/firecrawl", ("firecrawl_search",), "ok"),
    ("mcp/duckduckgo", ("search",), "error"),
    ("mcp/other", ("firecrawl_search",), "error"),
    ("mcp/firecrawl", ("firecrawl_search", "unexpected_tool"), "error"),
])
def test_fast_self_test_enforces_firecrawl_identity_and_exact_tools(
    monkeypatch, integration_id, tools, expected_status,
):
    from interview_assistant.diagnostics.selftest import run_fast_self_test
    from interview_assistant.retrieval.policy import SearchPolicy

    original = SearchPolicy.integrations_for

    def changed_route(self, *args, **kwargs):
        integrations = original(self, *args, **kwargs)
        return [SimpleNamespace(id=integration_id, allowed_tools=tools, query=item.query)
                for item in integrations]

    monkeypatch.setattr(SearchPolicy, "integrations_for", changed_route)
    report = run_fast_self_test()
    assert report["status"] == expected_status
    failed = {row["id"] for row in report["checks"] if row["status"] == "failed"}
    assert failed == (set() if expected_status == "ok" else {"search_policy"})


def test_fast_self_test_detects_a_broken_privacy_policy(monkeypatch):
    from interview_assistant.diagnostics.selftest import run_fast_self_test
    from interview_assistant.retrieval.policy import SearchPolicy

    monkeypatch.setattr(SearchPolicy, "integrations_for", lambda *_args, **_kwargs: [])
    report = run_fast_self_test()
    assert report["status"] == "error"
    assert {row["id"] for row in report["checks"] if row["status"] == "failed"} == {
        "search_policy",
    }


def test_fast_self_test_redacts_configuration_and_does_not_validate_by_network(tmp_path, monkeypatch):
    from interview_assistant.diagnostics.selftest import run_fast_self_test

    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    path = tmp_path / "config.yaml"
    path.write_text("provider: openrouter\n", encoding="utf-8")
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=private-secret\n", encoding="utf-8")
    report = run_fast_self_test(config_path=path)
    assert report["status"] == "ok"
    assert report["configuration"]["provider"] == "openrouter"
    assert "private-secret" not in json.dumps(report)


def test_headless_cli_does_not_import_gui_gpu_network_or_pytest(tmp_path):
    script = """
import importlib.abc, sys
class ForbidHeavyImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'PyQt6', 'pytest', 'faster_whisper', 'ctranslate2', 'httpx', 'mcp'}:
            raise AssertionError('Unexpected self-test import: ' + fullname)
sys.meta_path.insert(0, ForbidHeavyImports())
import main
raise SystemExit(main.main(['interview-assistant', '--self-test', '--config', sys.argv[1]]))
"""
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path / "config.yaml")],
                            cwd=Path(__file__).parents[2], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "ok"
