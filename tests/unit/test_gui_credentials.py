from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QLabel, QLineEdit

from interview_assistant.config import AppConfig, SecretStore
from interview_assistant.ui.settings import SettingsBinding, SettingsWindow


CREDENTIALS = (
    ("lm", "LMSTUDIO_API_TOKEN", "lmstudio_api_token", "lmstudio"),
    ("openrouter", "OPENROUTER_API_KEY", "openrouter_api_key", "openrouter"),
    ("firecrawl", "FIRECRAWL_API_KEY", "firecrawl_api_key", None),
    ("context7", "CONTEXT7_API_KEY", "context7_api_key", None),
)


@pytest.fixture
def credential_backend(monkeypatch):
    values = {}
    for _, name, _, _ in CREDENTIALS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        "interview_assistant.config.keyring.get_password",
        lambda service, name: values.get((service, name)),
    )
    monkeypatch.setattr(
        "interview_assistant.config.keyring.set_password",
        lambda service, name, value: values.__setitem__((service, name), value),
    )
    monkeypatch.setattr(
        "interview_assistant.config.keyring.delete_password",
        lambda service, name: values.pop((service, name)),
    )
    return values


def make_window(qtbot, tmp_path, store):
    config = AppConfig()
    binding = SettingsBinding(config, store, persist=lambda value: value.save(tmp_path / "config.yaml"))
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = SettingsWindow(binding, audio_devices=(), models=(), settings=settings)
    qtbot.addWidget(window)
    return window, config, settings


def select_editor(window, method, provider):
    if provider is not None:
        window.provider_combo.setCurrentIndex(window.provider_combo.findData(provider))
        return window.token_edit, window.remove_token_checkbox
    return getattr(window, f"{method}_token_edit"), getattr(window, f"{method}_remove_token_checkbox")


@pytest.mark.parametrize("method,env_name,stored_name,provider", CREDENTIALS)
def test_stored_gui_key_wins_and_delete_restores_environment_fallback(
    tmp_path, monkeypatch, credential_backend, method, env_name, stored_name, provider,
):
    del provider
    env_file = tmp_path / ".env"
    env_file.write_text(f"{env_name}=file-key\n", encoding="utf-8")
    monkeypatch.setenv(env_name, "process-key")
    store = SecretStore(env_path=env_file)
    getattr(store, f"set_{method}_token")("  saved-key  ")

    assert getattr(SecretStore(env_path=env_file), f"get_{method}_token")() == "saved-key"
    assert credential_backend[(SecretStore.SERVICE, stored_name)] == "saved-key"
    getattr(store, f"delete_{method}_token")()
    getattr(store, f"delete_{method}_token")()
    assert getattr(store, f"get_{method}_token")() == "process-key"
    monkeypatch.delenv(env_name)
    assert getattr(store, f"get_{method}_token")() == "file-key"
    assert env_file.read_text(encoding="utf-8") == f"{env_name}=file-key\n"


@pytest.mark.parametrize("method,env_name,stored_name,provider", CREDENTIALS)
def test_masked_gui_key_round_trip_replace_delete_and_no_plaintext_persistence(
    qtbot, tmp_path, credential_backend, method, env_name, stored_name, provider,
):
    del env_name
    store = SecretStore()
    window, config, settings = make_window(qtbot, tmp_path, store)
    editor, removal = select_editor(window, method, provider)
    assert editor.echoMode() == QLineEdit.EchoMode.Password
    assert editor.text() == ""
    editor.setText(f"{method}-first-sentinel")
    assert window.save()
    assert getattr(store, f"get_{method}_token")() == f"{method}-first-sentinel"
    assert editor.text() == ""
    assert window.save()  # An empty field keeps the saved key.
    assert getattr(store, f"get_{method}_token")() == f"{method}-first-sentinel"
    editor.setText(f"{method}-replacement-sentinel")
    assert window.save()
    assert getattr(store, f"get_{method}_token")() == f"{method}-replacement-sentinel"
    removal.setChecked(True)
    assert not editor.isEnabled()
    assert (SecretStore.SERVICE, stored_name) in credential_backend  # Save applies removal.
    assert window.save()
    assert getattr(store, f"get_{method}_token")() is None
    assert not removal.isChecked()
    assert editor.isEnabled()
    window.close_from_controller()
    settings.sync()
    assert "sentinel" not in repr(config.model_dump())
    assert "sentinel" not in (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert "sentinel" not in (tmp_path / "settings.ini").read_text(encoding="utf-8")
    assert all("sentinel" not in label.text() for label in window.findChildren(QLabel))


def test_switching_model_provider_cannot_delete_or_save_the_other_providers_key(
    qtbot, tmp_path, credential_backend,
):
    store = SecretStore()
    store.set_lm_token("local-key")
    store.set_openrouter_token("cloud-key")
    window, _, _ = make_window(qtbot, tmp_path, store)
    window.remove_token_checkbox.setChecked(True)
    window.provider_combo.setCurrentIndex(window.provider_combo.findData("openrouter"))
    assert not window.remove_token_checkbox.isChecked()
    assert window.token_edit.isEnabled()
    assert window.save()
    assert store.get_lm_token() == "local-key"
    assert store.get_openrouter_token() == "cloud-key"


def test_mcp_key_failure_keeps_model_config_and_clears_inputs_without_exposing_error(
    qtbot, tmp_path, monkeypatch, credential_backend,
):
    window, config, _ = make_window(qtbot, tmp_path, SecretStore())
    window.provider_combo.setCurrentIndex(window.provider_combo.findData("openrouter"))
    window.text_model_combo.setEditText("vendor/new-model")
    window.firecrawl_token_edit.setText("secret-error-sentinel")
    window.context7_token_edit.setText("docs-secret-sentinel")

    def fail(*_args):
        raise RuntimeError("keyring backend secret-error-sentinel")

    monkeypatch.setattr("interview_assistant.config.keyring.set_password", fail)
    assert not window.save()
    assert config.provider == "lmstudio"
    assert not (tmp_path / "config.yaml").exists()
    assert window.firecrawl_token_edit.text() == window.context7_token_edit.text() == ""
    visible = " ".join(label.text() for label in window.findChildren(QLabel))
    assert "sentinel" not in visible
    assert "Credential Manager" in visible


@pytest.mark.parametrize("method,env_name,stored_name,provider", CREDENTIALS)
def test_environment_key_still_works_when_credential_manager_cannot_be_read(
    monkeypatch, credential_backend, method, env_name, stored_name, provider,
):
    del stored_name, provider
    monkeypatch.setenv(env_name, "external-fallback")

    def fail(*_args):
        raise RuntimeError("unavailable credential backend")

    monkeypatch.setattr("interview_assistant.config.keyring.get_password", fail)
    assert getattr(SecretStore(), f"get_{method}_token")() == "external-fallback"


@pytest.mark.parametrize("action", ["save", "run_checks"])
async def test_saving_model_provider_key_rebuilds_with_correct_fresh_credential(
    qtbot, tmp_path, credential_backend, action,
):
    from interview_assistant.app import InterviewApplication
    from interview_assistant.composition import ApplicationController
    from tests.unit.test_application_controller import _Components, _report

    store = SecretStore()
    window, config, _ = make_window(qtbot, tmp_path, store)
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    received = []

    def build(candidate, token):
        persisted = (tmp_path / "config.yaml").read_text(encoding="utf-8")
        assert f"provider: {candidate.provider}" in persisted
        assert token not in persisted
        received.append((candidate.provider, token))
        return _Components(_report())

    controller = ApplicationController(
        app, window, config, store, component_factory=build, loop=asyncio.get_running_loop(),
    )
    try:
        for provider, token in (
            ("lmstudio", "gui-lm-key"), ("openrouter", "gui-router-key"),
            ("lmstudio", "replaced-lm-key"),
        ):
            window.provider_combo.setCurrentIndex(window.provider_combo.findData(provider))
            window.token_edit.setText(token)
            assert getattr(window, action)()
            task = controller._readiness_task
            assert task is not None
            await asyncio.wait_for(task, timeout=2)
            assert received[-1] == (provider, token)
        assert len(received) == 3
        assert store.get_openrouter_token() == "gui-router-key"
    finally:
        await controller.shutdown()


async def test_gui_mcp_keys_reach_native_http_on_component_rebuild_without_other_service_keys(
    qtbot, tmp_path, credential_backend, respx_mock,
):
    from interview_assistant.app import InterviewApplication
    from interview_assistant.composition import build_production_components
    from tests.unit.test_native_mcp import mock_firecrawl

    store = SecretStore()
    window, config, _ = make_window(qtbot, tmp_path, store)
    window.firecrawl_token_edit.setText("gui-firecrawl-key")
    window.context7_token_edit.setText("gui-context7-key")
    assert window.save()
    store.set_lm_token("unrelated-local-key")
    store.set_openrouter_token("unrelated-cloud-key")
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    firecrawl_route, _ = mock_firecrawl(respx_mock)

    def context7_response(request):
        message = json.loads(request.content)
        if "id" not in message:
            return httpx.Response(202)
        if message["method"] == "initialize":
            result = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "context7-fixture", "version": "1"}}
        else:
            assert message["method"] == "tools/list"
            result = {"tools": [{"name": name, "inputSchema": {"type": "object"}}
                                for name in ("resolve-library-id", "query-docs")]}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": message["id"], "result": result})

    context7_route = respx_mock.post("https://mcp.context7.com/mcp").mock(side_effect=context7_response)
    for suffix in ("", "-replaced"):
        if suffix:
            window.firecrawl_token_edit.setText("gui-firecrawl-key" + suffix)
            assert window.save()
        components = build_production_components(
            app, config, None, loop=asyncio.get_running_loop(), secret_store=store,
        )
        try:
            retrieval = components.runtime.services.retrieval
            assert retrieval is not None
            assert await retrieval.probe("mcp/firecrawl")
            assert await retrieval.probe("mcp/context7")
            assert firecrawl_route.calls[-1].request.headers["authorization"] == (
                "Bearer gui-firecrawl-key" + suffix
            )
            assert context7_route.calls[-1].request.headers["context7_api_key"] == "gui-context7-key"
        finally:
            await components.aclose()
    for route in (firecrawl_route, context7_route):
        for call in route.calls:
            assert "unrelated" not in str(call.request.headers)
    app.shutdown()
