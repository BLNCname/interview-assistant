from __future__ import annotations

from pathlib import Path
import re

import pytest
from PyQt6.QtCore import QByteArray, QSettings, QSize, Qt
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import QGroupBox, QLabel, QPushButton

import interview_assistant.ui.settings as settings_ui
from interview_assistant.config import AppConfig, HotkeysConfig
from interview_assistant.diagnostics.readiness import CheckResult, ReadinessReport
from interview_assistant.ui.settings import (
    SETTINGS_PAGE_TITLES,
    SettingsBinding,
    SettingsChoice,
    SettingsWindow,
)
from interview_assistant.utils.hotkeys import DEFAULT_HOTKEY_BINDINGS, HotkeyAction


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


def _hotkey_text(window: SettingsWindow, action: HotkeyAction) -> str:
    return (
        window.hotkey_edits[action]
        .keySequence()
        .toString(QKeySequence.SequenceFormat.PortableText)
        .casefold()
    )


def test_settings_uses_approved_a1_sidebar_pages(qtbot, tmp_path) -> None:
    window, _, _, _ = _window(qtbot, tmp_path)

    assert [
        window.navigation_list.item(index).text()
        for index in range(window.navigation_list.count())
    ] == [
        "General",
        "Models",
        "Audio",
        "Hotkeys",
        "Appearance",
        "Diagnostics",
    ]
    assert window.page_stack.count() == 6
    assert window.page_stack.currentWidget() is window.general_page


def test_settings_exposes_graphite_object_names_and_persistent_actions(
    qtbot,
    tmp_path,
) -> None:
    window, _, _, _ = _window(qtbot, tmp_path)

    assert window.centralWidget().objectName() == "settingsRoot"
    assert window.navigation_list.objectName() == "settingsNavigation"
    assert window.page_stack.objectName() == "settingsPages"
    assert window.navigation_list.accessibleName() == "Settings navigation"
    assert window.page_stack.accessibleName() == "Settings pages"
    assert window.save_button.objectName() == "secondaryButton"
    assert window.readiness_button.objectName() == "secondaryButton"
    assert window.start_button.objectName() == "startButton"
    assert window.save_button.accessibleName() == "Save settings"
    assert window.readiness_button.accessibleName() == "Run readiness checks"
    assert window.start_button.accessibleName() == "Start interview assistant"
    assert window.save_button.parentWidget() is window.centralWidget()
    assert window.readiness_button.parentWidget() is window.centralWidget()
    assert window.start_button.parentWidget() is window.centralWidget()

    for title, page in zip(
        SETTINGS_PAGE_TITLES,
        (
            window.general_page,
            window.models_page,
            window.audio_page,
            window.hotkeys_page,
            window.appearance_page,
            window.diagnostics_page,
        ),
        strict=True,
    ):
        assert page.findChild(QLabel, "settingsPageTitle").text() == title
        assert len(page.findChildren(QGroupBox, "settingsCard")) == 1


def test_settings_show_applies_native_title_bar_without_changing_window(
    monkeypatch,
    qtbot,
    tmp_path,
) -> None:
    window, _, _, _ = _window(qtbot, tmp_path)
    calls = []

    monkeypatch.setattr(
        settings_ui,
        "apply_native_dark_title_bar",
        lambda widget: calls.append((widget, widget.geometry())),
    )

    window.show()
    qtbot.waitUntil(window.isVisible)

    assert calls == [(window, window.geometry())]
    assert window.isVisible()


def test_settings_show_contains_native_title_bar_helper_failure(
    monkeypatch,
    qtbot,
    tmp_path,
) -> None:
    window, _, _, _ = _window(qtbot, tmp_path)

    def fail_native_hook(_widget) -> None:
        raise RuntimeError("DWM unavailable")

    monkeypatch.setattr(settings_ui, "apply_native_dark_title_bar", fail_native_hook)

    window.show()
    qtbot.waitUntil(window.isVisible)

    assert window.isVisible()


def test_settings_page_switching_preserves_unsaved_values(qtbot, tmp_path) -> None:
    window, _, _, _ = _window(qtbot, tmp_path)
    window.text_model_combo.setCurrentIndex(window.text_model_combo.findData("gemma"))

    window.navigation_list.setCurrentRow(3)
    window.navigation_list.setCurrentRow(1)

    assert window.page_stack.currentWidget() is window.models_page
    assert window.text_model_combo.currentData() == "gemma"


def test_settings_controls_belong_to_the_approved_pages(qtbot, tmp_path) -> None:
    window, _, _, _ = _window(qtbot, tmp_path)

    assert window.general_page.isAncestorOf(window.language_combo)
    assert window.general_page.isAncestorOf(window.search_mode_combo)
    assert not window.audio_page.isAncestorOf(window.language_combo)

    assert window.models_page.isAncestorOf(window.text_model_combo)
    assert window.models_page.isAncestorOf(window.vision_model_combo)
    assert window.models_page.isAncestorOf(window.shared_instance_label)
    assert window.models_page.isAncestorOf(window.token_edit)
    assert not window.general_page.isAncestorOf(window.token_edit)

    assert window.audio_page.isAncestorOf(window.system_device_combo)
    assert window.audio_page.isAncestorOf(window.microphone_device_combo)


def test_appearance_explains_ribbon_edit_mode_geometry_persistence(
    qtbot,
    tmp_path,
) -> None:
    window, _, _, _ = _window(qtbot, tmp_path)

    help_text = window.ribbon_geometry_help_label.text().casefold()
    assert window.appearance_page.isAncestorOf(window.ribbon_geometry_help_label)
    assert window.ribbon_geometry_help_label.wordWrap()
    assert window.ribbon_geometry_help_label.accessibleName() == "Ribbon edit mode help"
    assert "ribbon edit mode" in help_text
    assert "position" in help_text
    assert "size" in help_text
    assert "persisted automatically" in help_text


def test_hidden_settings_pages_receive_dynamic_choice_refreshes(qtbot, tmp_path) -> None:
    window, _, _, _ = _window(qtbot, tmp_path)
    text_combo = window.text_model_combo
    vision_combo = window.vision_model_combo
    system_combo = window.system_device_combo
    microphone_combo = window.microphone_device_combo
    window.navigation_list.setCurrentRow(3)

    window.set_model_choices((SettingsChoice("mistral", "Mistral"),))
    window.set_audio_choices((SettingsChoice("headset", "Headset"),))

    assert window.page_stack.currentWidget() is window.hotkeys_page
    assert window.text_model_combo is text_combo
    assert window.vision_model_combo is vision_combo
    assert window.system_device_combo is system_combo
    assert window.microphone_device_combo is microphone_combo
    assert text_combo.findData("mistral") >= 0
    assert vision_combo.findData("mistral") >= 0
    assert system_combo.findData("headset") >= 0
    assert microphone_combo.findData("headset") >= 0


def test_settings_exposes_every_hotkey_with_help_text(qtbot, tmp_path) -> None:
    window, _, _, _ = _window(qtbot, tmp_path)

    assert set(window.hotkey_edits) == set(HotkeyAction)
    assert (
        window.hotkey_labels[HotkeyAction.FORCE_REQUEST].text()
        == "Submit conversation context"
    )
    assert (
        "latest instructor turn"
        in window.hotkey_help[HotkeyAction.FORCE_REQUEST].text().casefold()
    )
    assert (
        window.hotkey_labels[HotkeyAction.SCREENSHOT].text()
        == "Capture next screenshot"
    )
    assert all(
        not re.search(r"[А-Яа-яЁё]", label.text())
        for label in window.hotkey_labels.values()
    )
    assert all(
        not re.search(r"[А-Яа-яЁё]", group.title())
        for group in window.findChildren(QGroupBox)
    )
    assert all(
        not re.search(r"[А-Яа-яЁё]", button.text())
        for button in window.findChildren(QPushButton)
    )


def test_restore_default_hotkeys_updates_all_editors(qtbot, tmp_path) -> None:
    window, _, _, _ = _window(qtbot, tmp_path)
    window.hotkey_edits[HotkeyAction.SCREENSHOT].setKeySequence("Ctrl+Alt+F11")

    window.restore_default_hotkeys()

    assert _hotkey_text(window, HotkeyAction.SCREENSHOT) == "ctrl+shift+s"
    assert _hotkey_text(window, HotkeyAction.OVERLAY_INTERACTION) == "ctrl+shift+i"


def test_duplicate_hotkeys_are_named_and_not_saved(qtbot, tmp_path) -> None:
    window, binding, _, _ = _window(qtbot, tmp_path)
    window.hotkey_edits[HotkeyAction.FORCE_REQUEST].setKeySequence("Ctrl+Alt+F10")
    window.hotkey_edits[HotkeyAction.SCREENSHOT].setKeySequence("Ctrl+Alt+F10")

    assert not window.save()

    assert "conflict" in window.notification_label.text().casefold()
    assert binding.config.hotkeys == HotkeysConfig()


def test_valid_hotkeys_are_persisted_with_other_settings(qtbot, tmp_path) -> None:
    window, binding, _, _ = _window(qtbot, tmp_path)
    window.hotkey_edits[HotkeyAction.OVERLAY_VISIBILITY].setKeySequence("Ctrl+Alt+F9")

    assert window.save()

    assert binding.config.hotkeys.overlay_visibility == "ctrl+alt+f9"


@pytest.mark.parametrize("sequence", ["", "Ctrl", "Ctrl++"])
def test_invalid_hotkey_identifies_its_settings_row(
    qtbot,
    tmp_path,
    sequence: str,
) -> None:
    window, binding, _, _ = _window(qtbot, tmp_path)
    action = HotkeyAction.FORCE_REQUEST
    window.hotkey_edits[action].setKeySequence(sequence)

    assert not window.save()

    assert window.hotkey_labels[action].text() in window.notification_label.text()
    assert binding.config.hotkeys == HotkeysConfig()


def test_hotkey_only_save_updates_live_map_without_invalidating_readiness(
    qtbot,
    tmp_path,
) -> None:
    config = AppConfig()
    live_updates: list[dict[HotkeyAction, str]] = []
    binding = SettingsBinding(
        config,
        FakeSecretStore(),
        apply_hotkeys=lambda bindings: live_updates.append(dict(bindings)),
    )
    window = SettingsWindow(
        binding,
        audio_devices=(),
        models=(),
        settings=QSettings(str(tmp_path / "hotkey-only.ini"), QSettings.Format.IniFormat),
    )
    qtbot.addWidget(window)
    report = _report("ready")
    window.set_readiness_report(report)
    reruns: list[bool] = []
    window.readiness_requested.connect(lambda: reruns.append(True))

    window.hotkey_edits[HotkeyAction.SCREENSHOT].setKeySequence("Ctrl+Alt+F11")
    assert window.save()

    assert live_updates == [config.hotkeys.as_bindings()]
    assert live_updates[0][HotkeyAction.SCREENSHOT] == "ctrl+alt+f11"
    assert window.readiness_report is report
    assert window.start_button.isEnabled()
    assert reruns == []


def test_run_checks_with_hotkey_only_edit_saves_and_requests_readiness_once(
    qtbot,
    tmp_path,
) -> None:
    config = AppConfig()
    persisted: list[AppConfig] = []
    live_updates: list[dict[HotkeyAction, str]] = []
    binding = SettingsBinding(
        config,
        FakeSecretStore(),
        persist=lambda candidate: persisted.append(candidate.model_copy(deep=True)),
        apply_hotkeys=lambda bindings: live_updates.append(dict(bindings)),
    )
    window = SettingsWindow(
        binding,
        audio_devices=(),
        models=(),
        settings=QSettings(str(tmp_path / "run-checks-hotkey.ini"), QSettings.Format.IniFormat),
    )
    qtbot.addWidget(window)
    window.set_readiness_report(_report("ready"))
    saved: list[bool] = []
    reruns: list[bool] = []
    window.settings_saved.connect(lambda: saved.append(True))
    window.readiness_requested.connect(lambda: reruns.append(True))

    window.hotkey_edits[HotkeyAction.SCREENSHOT].setKeySequence("Ctrl+Alt+F11")
    window.readiness_button.click()

    assert persisted == [config]
    assert live_updates == [config.hotkeys.as_bindings()]
    assert config.hotkeys.screenshot == "ctrl+alt+f11"
    assert saved == [True]
    assert reruns == [True]
    assert window.readiness_report is None
    assert not window.start_button.isEnabled()


@pytest.mark.parametrize("change_model", [False, True], ids=["no-change", "normal-change"])
def test_run_checks_saves_then_requests_readiness_once(
    qtbot,
    tmp_path,
    change_model: bool,
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
        audio_devices=(),
        models=(SettingsChoice("qwen-vl", "Qwen VL"),),
        settings=QSettings(str(tmp_path / f"run-checks-{change_model}.ini"), QSettings.Format.IniFormat),
    )
    qtbot.addWidget(window)
    saved: list[bool] = []
    reruns: list[bool] = []
    window.settings_saved.connect(lambda: saved.append(True))
    window.readiness_requested.connect(lambda: reruns.append(True))
    if change_model:
        window.text_model_combo.setCurrentIndex(window.text_model_combo.findData("qwen-vl"))

    window.readiness_button.click()

    assert persisted == [config]
    assert saved == [True]
    assert reruns == [True]


def test_run_checks_validation_failure_does_not_save_or_request_readiness(
    qtbot,
    tmp_path,
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
        audio_devices=(),
        models=(),
        settings=QSettings(str(tmp_path / "run-checks-invalid.ini"), QSettings.Format.IniFormat),
    )
    qtbot.addWidget(window)
    saved: list[bool] = []
    reruns: list[bool] = []
    window.settings_saved.connect(lambda: saved.append(True))
    window.readiness_requested.connect(lambda: reruns.append(True))
    window.hotkey_edits[HotkeyAction.SCREENSHOT].setKeySequence("Ctrl+Alt+F11")
    window.hotkey_edits[HotkeyAction.PAUSE].setKeySequence("Ctrl+Alt+F11")

    window.readiness_button.click()

    assert persisted == []
    assert config.hotkeys == HotkeysConfig()
    assert saved == []
    assert reruns == []
    assert "conflict" in window.notification_label.text().casefold()


def test_hotkey_manager_failure_restores_previous_live_map_and_config(
    qtbot,
    tmp_path,
) -> None:
    config = AppConfig()
    previous = config.hotkeys.as_bindings()
    live_updates: list[dict[HotkeyAction, str]] = []

    def fail_new_map(bindings) -> None:
        live_updates.append(dict(bindings))
        if len(live_updates) == 1:
            raise RuntimeError("listener rejected binding")

    binding = SettingsBinding(
        config,
        FakeSecretStore(),
        apply_hotkeys=fail_new_map,
    )
    window = SettingsWindow(
        binding,
        audio_devices=(),
        models=(),
        settings=QSettings(str(tmp_path / "manager-failure.ini"), QSettings.Format.IniFormat),
    )
    qtbot.addWidget(window)
    window.hotkey_edits[HotkeyAction.SCREENSHOT].setKeySequence("Ctrl+Alt+F11")

    assert not window.save()

    assert live_updates[0][HotkeyAction.SCREENSHOT] == "ctrl+alt+f11"
    assert live_updates[1] == previous
    assert config.hotkeys.as_bindings() == previous


def test_hotkey_persistence_failure_restores_previous_live_map_and_config(
    qtbot,
    tmp_path,
) -> None:
    config = AppConfig()
    previous = config.hotkeys.as_bindings()
    live_updates: list[dict[HotkeyAction, str]] = []

    def fail_persistence(_candidate: AppConfig) -> None:
        raise OSError("destination unavailable")

    binding = SettingsBinding(
        config,
        FakeSecretStore(),
        persist=fail_persistence,
        apply_hotkeys=lambda bindings: live_updates.append(dict(bindings)),
    )
    window = SettingsWindow(
        binding,
        audio_devices=(),
        models=(),
        settings=QSettings(str(tmp_path / "persistence-failure.ini"), QSettings.Format.IniFormat),
    )
    qtbot.addWidget(window)
    window.hotkey_edits[HotkeyAction.SCREENSHOT].setKeySequence("Ctrl+Alt+F11")

    assert not window.save()

    assert live_updates[0][HotkeyAction.SCREENSHOT] == "ctrl+alt+f11"
    assert live_updates[1] == previous
    assert config.hotkeys.as_bindings() == previous


def test_custom_hotkeys_save_to_disk_and_reload(qtbot, tmp_path: Path) -> None:
    config = AppConfig()
    path = tmp_path / "config.yaml"
    binding = SettingsBinding(
        config,
        FakeSecretStore(),
        persist=lambda candidate: candidate.save(path),
        apply_hotkeys=lambda _bindings: None,
    )
    window = SettingsWindow(
        binding,
        audio_devices=(),
        models=(),
        settings=QSettings(str(tmp_path / "reload.ini"), QSettings.Format.IniFormat),
    )
    qtbot.addWidget(window)
    window.hotkey_edits[HotkeyAction.SCREENSHOT].setKeySequence("Ctrl+Alt+F11")
    window.hotkey_edits[HotkeyAction.PAUSE].setKeySequence("Alt+Shift+F12")

    assert window.save()

    reloaded = AppConfig.load(path)
    assert reloaded.hotkeys.as_bindings() == config.hotkeys.as_bindings()
    assert reloaded.hotkeys.screenshot == "ctrl+alt+f11"
    assert reloaded.hotkeys.pause == "shift+alt+f12"
    assert set(reloaded.hotkeys.as_bindings()) == set(DEFAULT_HOTKEY_BINDINGS)


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
    window.system_device_combo.setCurrentIndex(window.system_device_combo.findData("system-1"))
    window.microphone_device_combo.setCurrentIndex(window.microphone_device_combo.findData("mic-1"))
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
    assert all(sentinel not in str(settings.value(key)) for key in settings.allKeys())
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
    assert window.readiness_status_label.text() == ("Readiness: failed — 1 blocking check")
    assert "blocking" in window.start_button.toolTip().casefold()
    assert window.readiness_table.item(0, 2).text() == "Affinity result"
    assert window.readiness_table.item(0, 3).text() == "Enable capture exclusion"

    window.set_readiness_report(_startable_warning_report())
    assert window.start_button.isEnabled()
    assert window.start_button.property("readyToStart") is True
    assert window.readiness_status_label.text() == ("Readiness: ready — 2 non-blocking warnings")
    assert "ready to start" in window.start_button.toolTip().casefold()
    assert window.start_button.styleSheet() == ""
    qtbot.mouseClick(window.start_button, Qt.MouseButton.LeftButton)
    assert starts == [True]


def test_readiness_rows_receive_semantic_status_metadata(qtbot, tmp_path) -> None:
    window, _, _, _ = _window(qtbot, tmp_path)

    window.set_readiness_report(_startable_warning_report())

    statuses = {
        window.readiness_table.item(row, 1).data(Qt.ItemDataRole.UserRole)
        for row in range(window.readiness_table.rowCount())
    }
    assert statuses == {"ready", "warning"}


def test_readiness_warning_surfaces_diagnostics_before_enabling_start(
    qtbot,
    tmp_path,
) -> None:
    window, _, _, _ = _window(qtbot, tmp_path)
    window.navigation_list.setCurrentRow(4)

    window.set_readiness_report(_startable_warning_report())

    assert window.page_stack.currentWidget() is window.diagnostics_page
    assert window.readiness_status_label.isVisibleTo(window)
    assert window.start_button.isEnabled()


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
    window.system_device_combo.setCurrentIndex(window.system_device_combo.findData("system-1"))
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
    window.navigation_list.setCurrentRow(1)

    window.save_button.click()

    assert config.lmstudio.text_model == ""
    assert reruns == []
    assert window.page_stack.currentWidget() is window.diagnostics_page
    assert window.readiness_status_label.isVisibleTo(window)
    assert "could not be saved" in window.readiness_status_label.text().casefold()


def test_settings_notification_surface_is_visible_plain_text(qtbot, tmp_path: Path) -> None:
    window, _, _, _ = _window(qtbot, tmp_path)
    message = "Start failed <b>not markup</b>"

    window.show_notification(message)

    assert window.notification_label.isVisibleTo(window)
    assert window.notification_label.text() == message
    assert window.notification_label.textFormat() is Qt.TextFormat.PlainText
