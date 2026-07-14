from __future__ import annotations

import asyncio
from pathlib import Path

from PyQt6.QtCore import QSettings

from interview_assistant.composition import create_production_controller
from interview_assistant.ui.windows_affinity import AffinityResult, WDA_EXCLUDEFROMCAPTURE


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
