from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, cast

from PyQt6.QtCore import QSettings, pyqtSignal
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from interview_assistant.config import (
    AppConfig,
    AudioConfig,
    LMStudioConfig,
    OverlayConfig,
    SearchConfig,
)
from interview_assistant.diagnostics.readiness import ReadinessReport


class SecretStoreProtocol(Protocol):
    def has_lm_token(self) -> bool: ...

    def set_lm_token(self, value: str) -> None: ...


@dataclass(frozen=True, slots=True)
class SettingsChoice:
    key: str
    label: str

    def __post_init__(self) -> None:
        if not self.key.strip() or not self.label.strip():
            raise ValueError("Settings choices need non-empty keys and labels")


class SettingsBinding:
    """Typed boundary between widgets, AppConfig, and the secret store."""

    def __init__(self, config: AppConfig, secret_store: SecretStoreProtocol) -> None:
        self.config = config
        self._secret_store = secret_store

    @property
    def unique_model_keys(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                key
                for key in (
                    self.config.lmstudio.text_model,
                    self.config.lmstudio.vision_model,
                )
                if key
            )
        )

    @property
    def has_lm_token(self) -> bool:
        return self._secret_store.has_lm_token()

    def apply(
        self,
        *,
        system_device_id: str | None,
        microphone_device_id: str | None,
        language: Literal["auto", "ru", "en"],
        text_model: str,
        vision_model: str,
        search_mode: Literal["off", "auto", "forced"],
        opacity: float,
        max_height: int,
        token: str,
    ) -> None:
        audio = AudioConfig.model_validate(
            {
                **self.config.audio.model_dump(),
                "system_device_id": system_device_id,
                "microphone_device_id": microphone_device_id,
                "language": language,
            }
        )
        lmstudio = LMStudioConfig.model_validate(
            {
                **self.config.lmstudio.model_dump(),
                "text_model": text_model,
                "vision_model": vision_model,
            }
        )
        search = SearchConfig.model_validate(
            {**self.config.search.model_dump(), "mode": search_mode}
        )
        overlay = OverlayConfig.model_validate(
            {
                **self.config.overlay.model_dump(),
                "opacity": opacity,
                "max_height": max_height,
            }
        )

        if token.strip():
            self._secret_store.set_lm_token(token)
        self.config.audio = audio
        self.config.lmstudio = lmstudio
        self.config.search = search
        self.config.overlay = overlay


class SettingsWindow(QMainWindow):
    start_requested = pyqtSignal()
    GEOMETRY_KEY = "settings/geometry"

    def __init__(
        self,
        binding: SettingsBinding,
        *,
        audio_devices: tuple[SettingsChoice, ...],
        models: tuple[SettingsChoice, ...],
        settings: QSettings | None = None,
    ) -> None:
        super().__init__()
        self._binding = binding
        self._settings = (
            settings
            if settings is not None
            else QSettings("InterviewAssistant", "InterviewAssistant")
        )
        self._readiness_report: ReadinessReport | None = None
        self.setWindowTitle("Interview Assistant Settings")
        self.resize(820, 620)
        self._build_ui(audio_devices, models)
        self._restore_geometry()
        self._connect_invalidators()

    @property
    def readiness_report(self) -> ReadinessReport | None:
        return self._readiness_report

    def _build_ui(
        self,
        audio_devices: tuple[SettingsChoice, ...],
        models: tuple[SettingsChoice, ...],
    ) -> None:
        central = QWidget(self)
        root = QVBoxLayout(central)
        self.setCentralWidget(central)

        configuration = QGroupBox("Configuration", central)
        form = QFormLayout(configuration)

        self.system_device_combo = self._choice_combo(
            audio_devices,
            current=self._binding.config.audio.system_device_id,
            empty_label="Not selected",
            empty_data=None,
        )
        self.microphone_device_combo = self._choice_combo(
            audio_devices,
            current=self._binding.config.audio.microphone_device_id,
            empty_label="Not selected",
            empty_data=None,
        )
        self.language_combo = QComboBox(configuration)
        for label, key in (("Auto (Russian / English)", "auto"), ("Russian", "ru"), ("English", "en")):
            self.language_combo.addItem(label, key)
        self._select_data(self.language_combo, self._binding.config.audio.language)

        self.text_model_combo = self._choice_combo(
            models,
            current=self._binding.config.lmstudio.text_model,
            empty_label="Not selected",
            empty_data="",
        )
        self.vision_model_combo = self._choice_combo(
            models,
            current=self._binding.config.lmstudio.vision_model,
            empty_label="Not selected",
            empty_data="",
        )
        self.shared_instance_label = QLabel(configuration)
        self.shared_instance_label.setWordWrap(True)
        self._update_shared_instance_annotation()

        self.search_mode_combo = QComboBox(configuration)
        for label, key in (("Off", "off"), ("Automatic", "auto"), ("Forced for next request", "forced")):
            self.search_mode_combo.addItem(label, key)
        self._select_data(self.search_mode_combo, self._binding.config.search.mode)

        self.opacity_spin = QDoubleSpinBox(configuration)
        self.opacity_spin.setRange(0.2, 1.0)
        self.opacity_spin.setSingleStep(0.01)
        self.opacity_spin.setDecimals(2)
        self.opacity_spin.setValue(self._binding.config.overlay.opacity)
        self.max_height_spin = QSpinBox(configuration)
        self.max_height_spin.setRange(120, 900)
        self.max_height_spin.setValue(self._binding.config.overlay.max_height)

        self.token_edit = QLineEdit(configuration)
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._refresh_token_placeholder()

        form.addRow("System audio", self.system_device_combo)
        form.addRow("Microphone", self.microphone_device_combo)
        form.addRow("Recognition language", self.language_combo)
        form.addRow("Text model", self.text_model_combo)
        form.addRow("Vision model", self.vision_model_combo)
        form.addRow("Model instances", self.shared_instance_label)
        form.addRow("Web search", self.search_mode_combo)
        form.addRow("Overlay opacity", self.opacity_spin)
        form.addRow("Overlay maximum height", self.max_height_spin)
        form.addRow("LM Studio token", self.token_edit)
        root.addWidget(configuration)

        readiness = QGroupBox("Readiness", central)
        readiness_layout = QVBoxLayout(readiness)
        self.readiness_status_label = QLabel("Run readiness checks before starting", readiness)
        self.readiness_table = QTableWidget(0, 5, readiness)
        self.readiness_table.setHorizontalHeaderLabels(
            ("Check", "Status", "Message", "Remediation", "Duration")
        )
        header = self.readiness_table.horizontalHeader()
        assert header is not None
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        self.readiness_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        readiness_layout.addWidget(self.readiness_status_label)
        readiness_layout.addWidget(self.readiness_table)
        root.addWidget(readiness, 1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.save_button = QPushButton("Save", central)
        self.start_button = QPushButton("Start", central)
        self.start_button.setEnabled(False)
        actions.addWidget(self.save_button)
        actions.addWidget(self.start_button)
        root.addLayout(actions)

        self.save_button.clicked.connect(self.save)
        self.start_button.clicked.connect(self._request_start)

    @staticmethod
    def _choice_combo(
        choices: tuple[SettingsChoice, ...],
        *,
        current: str | None,
        empty_label: str,
        empty_data: str | None,
    ) -> QComboBox:
        combo = QComboBox()
        SettingsWindow._populate_choice_combo(
            combo,
            choices,
            current=current,
            empty_label=empty_label,
            empty_data=empty_data,
        )
        return combo

    @staticmethod
    def _populate_choice_combo(
        combo: QComboBox,
        choices: tuple[SettingsChoice, ...],
        *,
        current: str | None,
        empty_label: str,
        empty_data: str | None,
    ) -> None:
        keys = [choice.key for choice in choices]
        if len(keys) != len(set(keys)):
            raise ValueError("Settings choice keys must be unique")
        previous_signal_state = combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItem(empty_label, empty_data)
            key_set = {choice.key for choice in choices}
            if current and current not in key_set:
                combo.addItem(f"{current} (unavailable)", current)
            for choice in choices:
                combo.addItem(choice.label, choice.key)
            SettingsWindow._select_data(
                combo,
                current if current is not None else empty_data,
            )
        finally:
            combo.blockSignals(previous_signal_state)

    def set_model_choices(self, models: tuple[SettingsChoice, ...]) -> None:
        text_key = str(self.text_model_combo.currentData() or "")
        vision_key = str(self.vision_model_combo.currentData() or "")
        self._populate_choice_combo(
            self.text_model_combo,
            models,
            current=text_key,
            empty_label="Not selected",
            empty_data="",
        )
        self._populate_choice_combo(
            self.vision_model_combo,
            models,
            current=vision_key,
            empty_label="Not selected",
            empty_data="",
        )
        self._update_shared_instance_annotation()
        self._invalidate_readiness()

    @staticmethod
    def _select_data(combo: QComboBox, value: object) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _connect_invalidators(self) -> None:
        combos = (
            self.system_device_combo,
            self.microphone_device_combo,
            self.language_combo,
            self.text_model_combo,
            self.vision_model_combo,
            self.search_mode_combo,
        )
        for combo in combos:
            combo.currentIndexChanged.connect(self._invalidate_readiness)
        self.text_model_combo.currentIndexChanged.connect(
            self._update_shared_instance_annotation
        )
        self.vision_model_combo.currentIndexChanged.connect(
            self._update_shared_instance_annotation
        )
        self.opacity_spin.valueChanged.connect(self._invalidate_readiness)
        self.max_height_spin.valueChanged.connect(self._invalidate_readiness)
        self.token_edit.textChanged.connect(self._invalidate_readiness)

    def _update_shared_instance_annotation(self, _value: int | None = None) -> None:
        text_key = str(self.text_model_combo.currentData() or "")
        vision_key = str(self.vision_model_combo.currentData() or "")
        if text_key and text_key == vision_key:
            self.shared_instance_label.setText(f"Shared instance: {text_key} (loaded once)")
        else:
            count = len(tuple(dict.fromkeys(key for key in (text_key, vision_key) if key)))
            self.shared_instance_label.setText(f"Unique model instances: {count}")

    def _invalidate_readiness(self, _value: object = None) -> None:
        if self._readiness_report is None:
            return
        self._readiness_report = None
        self.start_button.setEnabled(False)
        self.readiness_status_label.setText("Run readiness checks again after settings changes")

    def set_readiness_report(self, report: ReadinessReport) -> None:
        self._readiness_report = report
        self.readiness_table.setRowCount(len(report.checks))
        for row, result in enumerate(report.checks):
            values = (
                result.name,
                result.status,
                result.message,
                result.remediation,
                f"{result.duration_ms:.1f} ms",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.readiness_table.setItem(row, column, item)
        self.readiness_status_label.setText(f"Readiness: {report.status}")
        self.start_button.setEnabled(report.can_start)

    def save(self) -> bool:
        token = self.token_edit.text()
        try:
            self._binding.apply(
                system_device_id=cast(str | None, self.system_device_combo.currentData()),
                microphone_device_id=cast(str | None, self.microphone_device_combo.currentData()),
                language=cast(
                    Literal["auto", "ru", "en"],
                    self.language_combo.currentData(),
                ),
                text_model=str(self.text_model_combo.currentData() or ""),
                vision_model=str(self.vision_model_combo.currentData() or ""),
                search_mode=cast(
                    Literal["off", "auto", "forced"],
                    self.search_mode_combo.currentData(),
                ),
                opacity=self.opacity_spin.value(),
                max_height=self.max_height_spin.value(),
                token=token,
            )
        except Exception:
            self.readiness_status_label.setText("Settings could not be saved securely")
            return False
        finally:
            if token.strip():
                self.token_edit.clear()
                self._refresh_token_placeholder()
        self._update_shared_instance_annotation()
        return True

    def _refresh_token_placeholder(self) -> None:
        try:
            has_token = self._binding.has_lm_token
        except Exception:
            self.token_edit.setPlaceholderText("Secure token status unavailable")
        else:
            self.token_edit.setPlaceholderText(
                "Token stored in Windows Credential Manager"
                if has_token
                else "Enter token (stored securely)"
            )

    def _request_start(self) -> None:
        if self._readiness_report is not None and self._readiness_report.can_start:
            self.start_requested.emit()

    def _restore_geometry(self) -> None:
        geometry = self._settings.value(self.GEOMETRY_KEY)
        if geometry is not None:
            self.restoreGeometry(geometry)

    def closeEvent(self, event: QCloseEvent | None) -> None:
        self._settings.setValue(self.GEOMETRY_KEY, self.saveGeometry())
        self._settings.sync()
        super().closeEvent(event)
