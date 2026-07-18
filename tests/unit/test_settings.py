from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtCore import QByteArray, QSettings, QSize, Qt

from interview_assistant.config import AppConfig
from interview_assistant.diagnostics.readiness import CheckResult, ReadinessReport
from interview_assistant.ui.settings import SettingsBinding, SettingsChoice, SettingsWindow


class FakeSecretStore:
    def __init__(self, *, has_token: bool = False) -> None:
        self.has_token = has_token
        self.saved: list[str] = []

    def has_lm_token(self) -> bool:
        return self.has_token

    def set_lm_token(self, value: str) -> None:
        self.saved.append(value)
        self.has_token = True


def _report(status: str) -> ReadinessReport:
    return ReadinessReport(
        (
            CheckResult(
                name="display_affinity",
                status=status,  # type: ignore[arg-type]
                message="Affinity result",
                remediation="Enable capture exclusion",
                duration_ms=1.25,
                required=True,
                timed_out=False,
            ),
        )
    )


def _startable_warning_report() -> ReadinessReport:
    return ReadinessReport(
        tuple(
            CheckResult(
                name=name,
                status=status,  # type: ignore[arg-type]
                message=f"{name} result",
                remediation=f"Remediate {name}",
                duration_ms=1.25,
                required=True,
                timed_out=False,
            )
            for name, status in (
                ("windows_dwm", "ready"),
                ("stt_ru_fixture", "warning"),
                ("context7", "warning"),
            )
        )
    )


def _window(
    qtbot,
    tmp_path: Path,
    *,
    config: AppConfig | None = None,
    secret_store: FakeSecretStore | None = None,
) -> tuple[SettingsWindow, SettingsBinding, FakeSecretStore, QSettings]:
    active_secret_store = secret_store or FakeSecretStore()
    binding = SettingsBinding(config or AppConfig(), active_secret_store)
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = SettingsWindow(
        binding,
        audio_devices=(
            SettingsChoice("system-1", "Speakers (loopback)"),
            SettingsChoice("mic-1", "Microphone"),
        ),
        models=(
            SettingsChoice("qwen-vl", "Qwen VL"),
            SettingsChoice("gemma", "Gemma"),
        ),
        settings=settings,
    )
    qtbot.addWidget(window)
    return window, binding, active_secret_store, settings


def test_same_model_can_be_selected_twice_and_is_annotated_as_shared(qtbot, tmp_path) -> None:
    config = AppConfig()
    config.lmstudio.text_model = "qwen-vl"
    config.lmstudio.vision_model = "qwen-vl"
    window, binding, _, _ = _window(qtbot, tmp_path, config=config)

    assert window.text_model_combo.currentData() == "qwen-vl"
    assert window.vision_model_combo.currentData() == "qwen-vl"
    assert "shared" in window.shared_instance_label.text().casefold()
    assert "qwen-vl" in window.shared_instance_label.text()
    assert binding.unique_model_keys == ("qwen-vl",)

    window.vision_model_combo.setCurrentIndex(window.vision_model_combo.findData("gemma"))
    window.save()
    assert binding.unique_model_keys == ("qwen-vl", "gemma")


def test_settings_save_updates_typed_config_and_never_persists_token(qtbot, tmp_path) -> None:
    sentinel = "TOKEN-MUST-NOT-ENTER-QSETTINGS"
    window, binding, secret_store, settings = _window(qtbot, tmp_path)
    window.system_device_combo.setCurrentIndex(
        window.system_device_combo.findData("system-1")
    )
    window.microphone_device_combo.setCurrentIndex(
        window.microphone_device_combo.findData("mic-1")
    )
    window.language_combo.setCurrentIndex(window.language_combo.findData("ru"))
    window.text_model_combo.setCurrentIndex(window.text_model_combo.findData("qwen-vl"))
    window.vision_model_combo.setCurrentIndex(window.vision_model_combo.findData("gemma"))
    window.search_mode_combo.setCurrentIndex(window.search_mode_combo.findData("forced"))
    window.opacity_spin.setValue(0.71)
    window.max_height_spin.setValue(420)
    window.token_edit.setText(sentinel)

    window.save()
    window.close()
    settings.sync()

    assert binding.config.audio.system_device_id == "system-1"
    assert binding.config.audio.microphone_device_id == "mic-1"
    assert binding.config.audio.language == "ru"
    assert binding.config.lmstudio.text_model == "qwen-vl"
    assert binding.config.lmstudio.vision_model == "gemma"
    assert binding.config.search.mode == "forced"
    assert binding.config.overlay.opacity == 0.71
    assert binding.config.overlay.max_height == 420
    assert secret_store.saved == [sentinel]
    assert window.token_edit.text() == ""
    assert sentinel.encode() not in (tmp_path / "settings.ini").read_bytes()
    assert all(
        sentinel not in str(settings.value(key))
        for key in settings.allKeys()
    )
    assert "api_token" not in binding.config.model_dump()


def test_token_is_never_prefilled_and_blank_save_leaves_secret_unchanged(qtbot, tmp_path) -> None:
    secret_store = FakeSecretStore(has_token=True)
    window, _, _, _ = _window(qtbot, tmp_path, secret_store=secret_store)

    assert window.token_edit.text() == ""
    assert "stored" in window.token_edit.placeholderText().casefold()
    window.save()
    assert secret_store.saved == []


def test_readiness_failure_disables_start_but_warning_is_visible_and_permits_it(
    qtbot,
    tmp_path,
) -> None:
    window, _, _, _ = _window(qtbot, tmp_path)
    starts: list[bool] = []
    window.start_requested.connect(lambda: starts.append(True))

    assert not window.start_button.isEnabled()
    window.start_button.click()
    assert starts == []

    window.set_readiness_report(_report("failed"))
    assert not window.start_button.isEnabled()
    assert window.start_button.property("readyToStart") is False
    assert window.readiness_status_label.text() == (
        "Readiness: failed — 1 blocking check"
    )
    assert "blocking" in window.start_button.toolTip().casefold()
    assert window.readiness_table.item(0, 2).text() == "Affinity result"
    assert window.readiness_table.item(0, 3).text() == "Enable capture exclusion"

    window.set_readiness_report(_startable_warning_report())
    assert window.start_button.isEnabled()
    assert window.start_button.property("readyToStart") is True
    assert window.readiness_status_label.text() == (
        "Readiness: ready — 2 non-blocking warnings"
    )
    assert "ready to start" in window.start_button.toolTip().casefold()
    assert "#343a40" in window.start_button.styleSheet().casefold()
    qtbot.mouseClick(window.start_button, Qt.MouseButton.LeftButton)
    assert starts == [True]


def test_readiness_affecting_edit_invalidates_stale_report(qtbot, tmp_path) -> None:
    window, _, _, _ = _window(qtbot, tmp_path)
    window.set_readiness_report(_report("ready"))
    assert window.start_button.isEnabled()

    window.opacity_spin.setValue(0.69)

    assert window.readiness_report is None
    assert not window.start_button.isEnabled()
    assert "run" in window.readiness_status_label.text().casefold()
    window.start_button.click()
    assert not window.start_button.isEnabled()


def test_window_geometry_is_persisted_without_storing_fields(qtbot, tmp_path) -> None:
    window, _, _, settings = _window(qtbot, tmp_path)
    window.resize(777, 555)
    window.move(120, 140)
    window.close()
    settings.sync()

    assert settings.contains(SettingsWindow.GEOMETRY_KEY)
    keys = set(settings.allKeys())
    assert keys == {SettingsWindow.GEOMETRY_KEY}


def test_secret_store_failure_clears_token_without_leaking_it(qtbot, tmp_path) -> None:
    sentinel = "FAILED-SECRET-SENTINEL"

    class FailingSecretStore(FakeSecretStore):
        def set_lm_token(self, value: str) -> None:
            assert value == sentinel
            raise RuntimeError(f"credential write failed: {sentinel}")

    window, _, _, settings = _window(
        qtbot,
        tmp_path,
        secret_store=FailingSecretStore(),
    )
    window.token_edit.setText(sentinel)

    assert not window.save()
    window.close()
    settings.sync()

    assert window.token_edit.text() == ""
    visible = " ".join(
        (
            window.token_edit.placeholderText(),
            window.readiness_status_label.text(),
            window.shared_instance_label.text(),
        )
    )
    assert sentinel not in visible
    assert sentinel.encode() not in (tmp_path / "settings.ini").read_bytes()


def test_model_refresh_preserves_stable_selected_keys_and_missing_key(qtbot, tmp_path) -> None:
    config = AppConfig()
    config.lmstudio.text_model = "missing-model"
    config.lmstudio.vision_model = "qwen-vl"
    window, _, _, _ = _window(qtbot, tmp_path, config=config)

    assert window.text_model_combo.currentData() == "missing-model"
    assert "unavailable" in window.text_model_combo.currentText().casefold()
    window.set_model_choices(
        (
            SettingsChoice("gemma", "Gemma"),
            SettingsChoice("qwen-vl", "Qwen VL"),
        )
    )

    assert window.text_model_combo.currentData() == "missing-model"
    assert window.vision_model_combo.currentData() == "qwen-vl"
    assert window.windowType() == Qt.WindowType.Window


@pytest.mark.parametrize(
    "corrupt_geometry",
    ["not-a-qbytearray", QByteArray(b"invalid-geometry")],
    ids=["wrong-type", "invalid-bytes"],
)
def test_corrupt_settings_geometry_falls_back_to_default(
    qtbot,
    tmp_path,
    corrupt_geometry: object,
) -> None:
    settings = QSettings(str(tmp_path / "corrupt.ini"), QSettings.Format.IniFormat)
    settings.setValue(SettingsWindow.GEOMETRY_KEY, corrupt_geometry)
    settings.sync()
    binding = SettingsBinding(AppConfig(), FakeSecretStore())

    window = SettingsWindow(
        binding,
        audio_devices=(),
        models=(),
        settings=settings,
    )
    qtbot.addWidget(window)

    assert window.size() == QSize(820, 620)


def test_successful_save_persists_config_then_requests_fresh_readiness(
    qtbot,
    tmp_path: Path,
) -> None:
    config = AppConfig()
    persisted: list[AppConfig] = []
    binding = SettingsBinding(
        config,
        FakeSecretStore(),
        persist=lambda candidate: persisted.append(candidate.model_copy(deep=True)),
    )
    window = SettingsWindow(
        binding,
        audio_devices=(SettingsChoice("system-1", "Loopback"),),
        models=(SettingsChoice("qwen-vl", "Qwen VL"),),
        settings=QSettings(str(tmp_path / "signals.ini"), QSettings.Format.IniFormat),
    )
    qtbot.addWidget(window)
    saved: list[bool] = []
    reruns: list[bool] = []
    window.settings_saved.connect(lambda: saved.append(True))
    window.readiness_requested.connect(lambda: reruns.append(True))
    window.system_device_combo.setCurrentIndex(
        window.system_device_combo.findData("system-1")
    )
    window.text_model_combo.setCurrentIndex(window.text_model_combo.findData("qwen-vl"))

    window.save_button.click()

    assert persisted == [config]
    assert persisted[0] is not config
    assert saved == [True]
    assert reruns == [True]
    assert not window.start_button.isEnabled()
    assert "running" in window.readiness_status_label.text().casefold()


def test_persistence_failure_keeps_live_config_and_does_not_request_readiness(
    qtbot,
    tmp_path: Path,
) -> None:
    config = AppConfig()

    def fail(_candidate: AppConfig) -> None:
        raise OSError("private destination")

    binding = SettingsBinding(config, FakeSecretStore(), persist=fail)
    window = SettingsWindow(
        binding,
        audio_devices=(),
        models=(SettingsChoice("qwen-vl", "Qwen VL"),),
        settings=QSettings(str(tmp_path / "failure.ini"), QSettings.Format.IniFormat),
    )
    qtbot.addWidget(window)
    reruns: list[bool] = []
    window.readiness_requested.connect(lambda: reruns.append(True))
    window.text_model_combo.setCurrentIndex(window.text_model_combo.findData("qwen-vl"))

    window.save_button.click()

    assert config.lmstudio.text_model == ""
    assert reruns == []
    assert "could not be saved" in window.readiness_status_label.text().casefold()


def test_settings_notification_surface_is_visible_plain_text(qtbot, tmp_path: Path) -> None:
    window, _, _, _ = _window(qtbot, tmp_path)
    message = "Start failed <b>not markup</b>"

    window.show_notification(message)

    assert window.notification_label.isVisibleTo(window)
    assert window.notification_label.text() == message
    assert window.notification_label.textFormat() is Qt.TextFormat.PlainText
