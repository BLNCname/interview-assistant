"""Exercise the spec's MCP client collection without invoking compiler stages."""

from pathlib import Path
from types import SimpleNamespace
import os
import runpy
import sys

import PyInstaller.utils.hooks as hooks


def test_frozen_mcp_collects_client_transports_without_bundled_server_or_search(monkeypatch):
    original_collect = hooks.collect_all
    collected = []
    requested = []
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))

    def collect_runtime(package, **kwargs):
        requested.append(package)
        if package not in ("mcp", "mcp.client"):
            return [], [], []
        result = original_collect(package, **kwargs)
        collected.extend(result[2])
        return result

    monkeypatch.setattr(hooks, "collect_all", collect_runtime)
    monkeypatch.setattr(hooks, "copy_metadata", lambda _name: [])
    monkeypatch.delenv("INTERVIEW_ASSISTANT_STT_MODEL_PATH", raising=False)
    packaging = Path(__file__).parents[2] / "packaging"
    namespace = runpy.run_path(str(packaging / "interview_assistant.spec"), init_globals={
        "SPECPATH": str(packaging),
        "Analysis": lambda *args, **kwargs: SimpleNamespace(
            pure=[], scripts=[], binaries=[], datas=[],
        ),
        "PYZ": lambda *args, **kwargs: None,
        "EXE": lambda *args, **kwargs: None,
        "COLLECT": lambda *args, **kwargs: None,
    })
    assert "mcp.client.stdio" in collected
    assert "mcp.client.streamable_http" in collected
    assert not any(name == "mcp.server" or name.startswith("mcp.server.") for name in collected)
    assert not any(name == "mcp.cli" or name.startswith("mcp.cli.") for name in collected)
    assert "mcp.client.__main__" not in collected
    assert "ddgs" not in requested
    assert "primp" not in requested
    assert "mcp" in namespace["DISTRIBUTIONS"]
    assert not {"ddgs", "primp"}.intersection(namespace["DISTRIBUTIONS"])
    assert "interview_assistant.retrieval.web_search_server" not in namespace["hiddenimports"]
    # main.py loads this command-line branch lazily using import_module.
    assert "interview_assistant.diagnostics.cli" in namespace["hiddenimports"]
    assert "interview_assistant.diagnostics.selftest" in namespace["hiddenimports"]


def test_frozen_build_excludes_unrelated_application_dll_paths(monkeypatch, tmp_path):
    bundled_qt = tmp_path / "site-packages" / "PyQt6"
    qt_bin = bundled_qt / "Qt6" / "bin"
    qt_bin.mkdir(parents=True)
    (qt_bin / "Qt6Core.dll").write_bytes(b"reviewed Qt")
    foreign_qt = tmp_path / "unrelated-installed-application"
    foreign_qt.mkdir()
    (foreign_qt / "Qt6Core.dll").write_bytes(b"incompatible Qt")
    tools_path = tmp_path / "tools"
    tools_path.mkdir()
    monkeypatch.setenv("PATH", os.pathsep.join((str(foreign_qt), str(tools_path))))
    monkeypatch.setattr(hooks, "get_package_paths", lambda _name: (str(tmp_path), str(bundled_qt)))
    monkeypatch.setattr(hooks, "collect_all", lambda *args, **kwargs: ([], [], []))
    monkeypatch.setattr(hooks, "copy_metadata", lambda _name: [])
    monkeypatch.delenv("INTERVIEW_ASSISTANT_STT_MODEL_PATH", raising=False)
    packaging = Path(__file__).parents[2] / "packaging"
    runpy.run_path(str(packaging / "interview_assistant.spec"), init_globals={
        "SPECPATH": str(packaging),
        "Analysis": lambda *args, **kwargs: SimpleNamespace(
            pure=[], scripts=[], binaries=[], datas=[],
        ),
        "PYZ": lambda *args, **kwargs: None,
        "EXE": lambda *args, **kwargs: None,
        "COLLECT": lambda *args, **kwargs: None,
    })
    dependency_paths = os.environ["PATH"].split(os.pathsep)
    assert dependency_paths[0] == str(qt_bin)
    assert str(Path(sys.executable).parent) in dependency_paths
    assert str(tools_path) not in dependency_paths
    assert str(foreign_qt) not in dependency_paths
