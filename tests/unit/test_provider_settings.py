from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QLineEdit

from interview_assistant.config import AppConfig
from interview_assistant.diagnostics.readiness import CheckResult, ReadinessReport
from interview_assistant.ui.settings import SettingsBinding, SettingsChoice, SettingsWindow


class Secrets:
    def __init__(self):
        self.lm = []
        self.router = []

    def has_lm_token(self):
        return bool(self.lm)

    def set_lm_token(self, value):
        self.lm.append(value)

    def has_openrouter_token(self):
        return bool(self.router)

    def set_openrouter_token(self, value):
        self.router.append(value)


def window_for(qtbot, tmp_path, config=None):
    config = config or AppConfig()
    secrets = Secrets()
    persisted = []
    binding = SettingsBinding(config, secrets, persist=lambda value: persisted.append(value))
    window = SettingsWindow(
        binding,
        audio_devices=(),
        models=(),
        settings=QSettings(str(tmp_path / "provider.ini"), QSettings.Format.IniFormat),
    )
    qtbot.addWidget(window)
    return window, binding, secrets, persisted


def switch(window, value):
    window.provider_combo.setCurrentIndex(window.provider_combo.findData(value))


def test_cloud_model_ids_can_be_entered_and_saved_without_discovery(qtbot, tmp_path):
    config = AppConfig()
    config.lmstudio.text_model = "local/qwen"
    window, binding, secrets, persisted = window_for(qtbot, tmp_path, config)
    switch(window, "openrouter")
    window.text_model_combo.setEditText("openai/example")
    window.vision_model_combo.setEditText("vendor/vision")
    window.token_edit.setText("router-secret")
    assert window.save()
    assert config.provider == "openrouter"
    assert config.text_model == "openai/example"
    assert config.vision_model == "vendor/vision"
    assert config.lmstudio.text_model == "local/qwen"
    assert binding.unique_model_keys == ("openai/example", "vendor/vision")
    assert secrets.router == ["router-secret"] and secrets.lm == []
    assert persisted[-1].openrouter.text_model == "openai/example"
    assert window.token_edit.text() == ""
    assert window.token_edit.echoMode() == QLineEdit.EchoMode.Password
    assert "router-secret" not in repr(config.model_dump())


def test_provider_switch_preserves_each_model_draft_and_clears_pending_key(qtbot, tmp_path):
    config = AppConfig()
    config.lmstudio.text_model = "local/original"
    config.openrouter.text_model = "cloud/original"
    window, _, secrets, _ = window_for(qtbot, tmp_path, config)
    window.set_model_choices((SettingsChoice("local/new", "New local model"),))
    window.text_model_combo.setCurrentIndex(window.text_model_combo.findData("local/new"))
    window.token_edit.setText("local-unsaved-secret")
    switch(window, "openrouter")
    assert window.token_edit.text() == ""
    assert window.text_model_combo.currentData() == "cloud/original"
    window.text_model_combo.setEditText("cloud/new")
    switch(window, "lmstudio")
    assert window.text_model_combo.currentData() == "local/new"
    assert window.save()
    assert config.lmstudio.text_model == "local/new"
    assert config.openrouter.text_model == "cloud/new"
    assert secrets.lm == secrets.router == []


def test_run_checks_saves_selected_provider_before_emitting_readiness(qtbot, tmp_path):
    window, binding, _, _ = window_for(qtbot, tmp_path)
    observed = []
    window.readiness_requested.connect(lambda: observed.append(binding.config.provider))
    switch(window, "openrouter")
    window.text_model_combo.setEditText("vendor/model")
    assert window.run_checks()
    assert observed == ["openrouter"]
    assert not window.start_button.isEnabled()


def test_editing_cloud_model_text_invalidates_previous_readiness(qtbot, tmp_path):
    config = AppConfig(provider="openrouter")
    window, _, _, _ = window_for(qtbot, tmp_path, config)
    report = ReadinessReport(
        (
            CheckResult(
                name="lmstudio_auth",
                status="ready",
                message="ok",
                remediation="Check credentials",
                duration_ms=1,
                required=True,
                timed_out=False,
            ),
        )
    )
    window.set_readiness_report(report)
    assert window.start_button.isEnabled()
    assert "OpenRouter" in window.readiness_table.item(0, 0).text()
    window.text_model_combo.setEditText("vendor/different")
    assert window.readiness_report is None
    assert not window.start_button.isEnabled()


def test_readiness_displays_firecrawl_mcp_name(qtbot, tmp_path):
    window, _, _, _ = window_for(qtbot, tmp_path)
    window.set_readiness_report(ReadinessReport((CheckResult(
        name="firecrawl_mcp", status="warning", message="Firecrawl MCP unavailable",
        remediation="Check connectivity", duration_ms=1, required=False,
    ),)))
    assert window.readiness_table.item(0, 0).text() == "Firecrawl MCP"
    assert window.start_button.isEnabled()


def test_discovery_refresh_does_not_replace_manually_entered_model_id(qtbot, tmp_path):
    window, _, _, _ = window_for(qtbot, tmp_path, AppConfig(provider="openrouter"))
    window.text_model_combo.setEditText("vendor/unlisted")
    window.set_model_choices((SettingsChoice("vendor/listed", "Listed model"),))
    assert window.save()
    assert window._binding.config.text_model == "vendor/unlisted"


def test_audio_compute_choice_and_mcp_backend_are_persisted(qtbot, tmp_path):
    window, binding, _, _ = window_for(qtbot, tmp_path)
    window.stt_compute_combo.setCurrentIndex(window.stt_compute_combo.findData("int8_float16"))
    window.mcp_backend_combo.setCurrentIndex(window.mcp_backend_combo.findData("off"))
    assert window.save()
    assert binding.config.audio.compute_type == "int8_float16"
    assert binding.config.mcp.backend == "off"


def test_cpu_selection_automatically_chooses_compatible_compute_type(qtbot, tmp_path):
    window, binding, _, _ = window_for(qtbot, tmp_path)
    window.stt_device_combo.setCurrentIndex(window.stt_device_combo.findData("cpu"))
    assert window.stt_compute_combo.currentData() == "int8"
    assert window.save()
    assert binding.config.audio.device == "cpu"
    assert binding.config.audio.compute_type == "int8"


def test_cpu_precision_choices_exclude_unsupported_float16(qtbot, tmp_path):
    window, _, _, _ = window_for(qtbot, tmp_path)
    window.stt_device_combo.setCurrentIndex(window.stt_device_combo.findData("cpu"))
    assert window.stt_compute_combo.findData("float16") == -1
    assert window.stt_compute_combo.findData("int8_float16") == -1
    window.stt_device_combo.setCurrentIndex(window.stt_device_combo.findData("cuda"))
    assert window.stt_compute_combo.findData("int8_float16") >= 0


def test_stale_local_discovery_does_not_pollute_cloud_model_choices(qtbot, tmp_path):
    window, _, _, _ = window_for(qtbot, tmp_path)
    switch(window, "openrouter")
    window.text_model_combo.setEditText("vendor/manual")
    window.set_model_choices((SettingsChoice("local/model", "Local model"),))
    assert window.text_model_combo.findData("local/model") == -1
    assert window.text_model_combo.currentText() == "vendor/manual"
