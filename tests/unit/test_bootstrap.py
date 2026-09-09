from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from PyQt6.QtCore import QSettings

from interview_assistant.composition import create_production_controller
from interview_assistant.ui.windows_affinity import AffinityResult, WDA_EXCLUDEFROMCAPTURE
from tests.unit.test_application_controller import _Components, _report


class _Secrets:
    def get_lm_token(self) -> None:
        return None

    def has_lm_token(self) -> bool:
        return False

    def set_lm_token(self, _value: str) -> None:
        return None


async def test_missing_config_opens_safe_defaults_without_writing_file(
    qapp,
    qtbot,
    tmp_path: Path,
) -> None:
    path = tmp_path / "missing.yaml"
    settings_store = QSettings(
        str(tmp_path / "bootstrap.ini"),
        QSettings.Format.IniFormat,
    )

    controller = create_production_controller(
        qapp,
        asyncio.get_running_loop(),
        config_path=path,
        settings_store=settings_store,
        affinity_applier=lambda _hwnd: AffinityResult(
            True,
            WDA_EXCLUDEFROMCAPTURE,
            None,
        ),
        secret_store=_Secrets(),
    )
    qtbot.addWidget(controller.application.ribbon)
    qtbot.addWidget(controller.settings)

    assert not path.exists()
    assert controller.config.lmstudio.text_model == ""
    assert "missing" in controller.settings.notification_label.text().casefold()
    await controller.shutdown()
    assert not path.exists()


async def test_invalid_config_is_never_silently_overwritten(
    qapp,
    qtbot,
    tmp_path: Path,
) -> None:
    path = tmp_path / "invalid.yaml"
    original = b"lmstudio: [invalid\n"
    path.write_bytes(original)
    controller = create_production_controller(
        qapp,
        asyncio.get_running_loop(),
        config_path=path,
        settings_store=QSettings(
            str(tmp_path / "invalid.ini"),
            QSettings.Format.IniFormat,
        ),
        affinity_applier=lambda _hwnd: AffinityResult(
            True,
            WDA_EXCLUDEFROMCAPTURE,
            None,
        ),
        secret_store=_Secrets(),
    )
    qtbot.addWidget(controller.application.ribbon)
    qtbot.addWidget(controller.settings)

    assert path.read_bytes() == original
    assert "could not be loaded" in controller.settings.notification_label.text().casefold()
    await controller.shutdown()
    assert path.read_bytes() == original


@pytest.mark.parametrize("initial_config", [None, "lmstudio: [invalid\n"])
@pytest.mark.parametrize("action", ["save", "run_checks"])
async def test_successful_persistence_clears_only_initial_configuration_notice(
    qapp, qtbot, tmp_path, monkeypatch, initial_config, action,
) -> None:
    path = tmp_path / "config.yaml"
    if initial_config is not None:
        path.write_text(initial_config, encoding="utf-8")
    monkeypatch.setattr("interview_assistant.config.environment_values", lambda _path=None: {})
    monkeypatch.setattr(
        "interview_assistant.composition.resolve_environment_path",
        lambda _path: tmp_path / ".env",
    )
    monkeypatch.setattr(
        "interview_assistant.composition.build_production_components",
        lambda *_args, **_kwargs: _Components(_report()),
    )
    controller = create_production_controller(
        qapp, asyncio.get_running_loop(), config_path=path,
        settings_store=QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat),
        affinity_applier=lambda _hwnd: AffinityResult(True, WDA_EXCLUDEFROMCAPTURE, None),
        secret_store=_Secrets(),
    )
    qtbot.addWidget(controller.application.ribbon)
    qtbot.addWidget(controller.settings)
    notice = controller.settings.notification_label
    assert not notice.isHidden()
    try:
        assert getattr(controller.settings, action)()
        await asyncio.wait_for(controller._readiness_task, timeout=2)
        assert path.is_file()
        assert "provider: lmstudio" in path.read_text(encoding="utf-8")
        assert notice.isHidden()
        assert notice.text() == ""
    finally:
        await controller.shutdown()


@pytest.mark.parametrize("failure", [False, True])
async def test_persistence_preserves_unrelated_notice_and_save_errors(
    qapp, qtbot, tmp_path, monkeypatch, failure,
) -> None:
    path = tmp_path / "config.yaml"
    monkeypatch.setattr("interview_assistant.config.environment_values", lambda _path=None: {})
    monkeypatch.setattr(
        "interview_assistant.composition.resolve_environment_path",
        lambda _path: tmp_path / ".env",
    )
    monkeypatch.setattr(
        "interview_assistant.composition.build_production_components",
        lambda *_args, **_kwargs: _Components(_report()),
    )
    controller = create_production_controller(
        qapp, asyncio.get_running_loop(), config_path=path,
        settings_store=QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat),
        affinity_applier=lambda _hwnd: AffinityResult(True, WDA_EXCLUDEFROMCAPTURE, None),
        secret_store=_Secrets(),
    )
    qtbot.addWidget(controller.application.ribbon)
    qtbot.addWidget(controller.settings)
    window = controller.settings
    if failure:
        original_notice = window.notification_label.text()

        def fail_persistence(_self, _path):
            raise OSError("private destination details")

        monkeypatch.setattr("interview_assistant.config.AppConfig.save", fail_persistence)
    else:
        original_notice = "Audio device disconnected; choose an available device."
        window.show_notification(original_notice)
    try:
        assert window.run_checks() is not failure
        if failure:
            assert controller._readiness_task is None
            assert not path.exists()
            assert "could not be saved" in window.readiness_status_label.text()
            assert "private destination details" not in window.readiness_status_label.text()
        else:
            await asyncio.wait_for(controller._readiness_task, timeout=2)
            assert path.is_file()
        assert not window.notification_label.isHidden()
        assert window.notification_label.text() == original_notice
    finally:
        await controller.shutdown()
