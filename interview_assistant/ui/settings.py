from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable, Mapping
from typing import Literal, Protocol, cast

from PyQt6.QtCore import QByteArray, QSettings, Qt, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QKeySequence
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from interview_assistant.config import (
    AppConfig,
    AudioConfig,
    HotkeysConfig,
    LMStudioConfig,
    OverlayConfig,
    SearchConfig,
)
from interview_assistant.diagnostics.readiness import ReadinessReport
from interview_assistant.utils.hotkeys import (
    DEFAULT_HOTKEY_BINDINGS,
    HotkeyAction,
    HotkeyChord,
    normalize_bindings,
)


SETTINGS_PAGE_TITLES = (
    "General",
    "Models",
    "Audio",
    "Hotkeys",
    "Appearance",
    "Diagnostics",
)


HOTKEY_COPY = {
    HotkeyAction.FORCE_REQUEST: (
        "Submit conversation context",
        "Submits the latest instructor turn and recent dialogue from both speakers.",
    ),
    HotkeyAction.SCREENSHOT: (
        "Capture next screenshot",
        "Attaches the next usable screen capture to the model context once.",
    ),
    HotkeyAction.PAUSE: (
        "Pause or resume recognition",
        "Pauses or continues processing for both audio sources.",
    ),
    HotkeyAction.OVERLAY_VISIBILITY: (
        "Show or hide assistant",
        "Temporarily hides the Ribbon without closing the application.",
    ),
    HotkeyAction.OVERLAY_INTERACTION: (
        "Move or resize assistant",
        "Toggles click-through and Ribbon edit mode.",
    ),
    HotkeyAction.FORCED_WEB_SEARCH: (
        "Force web search for next request",
        "Enables web search only for the next answer.",
    ),
    HotkeyAction.CLEAR_ANSWER: (
        "Clear answer and conversation",
        "Removes the current answer and transcript history from application memory.",
    ),
}


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

    def __init__(
        self,
        config: AppConfig,
        secret_store: SecretStoreProtocol,
        *,
        persist: Callable[[AppConfig], None] | None = None,
        apply_hotkeys: Callable[[Mapping[HotkeyAction, str]], None] | None = None,
    ) -> None:
        self.config = config
        self._secret_store = secret_store
        self._persist = persist
        self._apply_hotkeys = apply_hotkeys

    def bind_hotkey_updater(
        self,
        updater: Callable[[Mapping[HotkeyAction, str]], None],
    ) -> None:
        self._apply_hotkeys = updater

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
        hotkeys: Mapping[HotkeyAction | str, str],
        token: str,
    ) -> bool:
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
        hotkeys_config = HotkeysConfig.from_bindings(hotkeys)

        candidate = self.config.model_copy(deep=True)
        candidate.audio = audio
        candidate.lmstudio = lmstudio
        candidate.search = search
        candidate.overlay = overlay
        candidate.hotkeys = hotkeys_config
        hotkeys_changed = candidate.hotkeys != self.config.hotkeys
        non_hotkey_changed = candidate.model_dump(exclude={"hotkeys"}) != self.config.model_dump(
            exclude={"hotkeys"}
        )
        hotkey_only = hotkeys_changed and not non_hotkey_changed and not token.strip()
        previous_bindings = self.config.hotkeys.as_bindings()
        live_updated = False
        if hotkeys_changed and self._apply_hotkeys is not None:
            try:
                self._apply_hotkeys(candidate.hotkeys.as_bindings())
            except Exception:
                self._restore_live_hotkeys(previous_bindings)
                raise
            live_updated = True
        try:
            if token.strip():
                self._secret_store.set_lm_token(token)
            if self._persist is not None:
                self._persist(candidate)
        except Exception:
            if live_updated:
                self._restore_live_hotkeys(previous_bindings)
            raise
        self.config.audio = candidate.audio
        self.config.lmstudio = candidate.lmstudio
        self.config.search = candidate.search
        self.config.overlay = candidate.overlay
        self.config.hotkeys = candidate.hotkeys
        return not hotkey_only

    def _restore_live_hotkeys(self, bindings: Mapping[HotkeyAction, str]) -> None:
        if self._apply_hotkeys is None:
            return
        try:
            self._apply_hotkeys(bindings)
        except Exception:
            pass


class SettingsWindow(QMainWindow):
    start_requested = pyqtSignal()
    settings_saved = pyqtSignal()
    readiness_requested = pyqtSignal()
    close_requested = pyqtSignal()
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
        self._controller_close = False
        self.setWindowTitle("Interview Assistant Settings")
        self.resize(820, 620)
        self._build_ui(audio_devices, models)
        self._restore_geometry()
        self._connect_invalidators()

    def bind_hotkey_updater(
        self,
        updater: Callable[[Mapping[HotkeyAction, str]], None],
    ) -> None:
        self._binding.bind_hotkey_updater(updater)

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

        content = QHBoxLayout()
        self.navigation_list = QListWidget(central)
        self.navigation_list.setObjectName("settingsNavigation")
        self.navigation_list.addItems(SETTINGS_PAGE_TITLES)
        self.page_stack = QStackedWidget(central)

        self.general_page = self._build_general_page()
        self.models_page = self._build_models_page(models)
        self.audio_page = self._build_audio_page(audio_devices)
        self.hotkeys_page = self._build_hotkeys_page()
        self.appearance_page = self._build_appearance_page()
        self.diagnostics_page = self._build_diagnostics_page()
        for page in (
            self.general_page,
            self.models_page,
            self.audio_page,
            self.hotkeys_page,
            self.appearance_page,
            self.diagnostics_page,
        ):
            self.page_stack.addWidget(page)
        self.navigation_list.currentRowChanged.connect(self.page_stack.setCurrentIndex)
        self.navigation_list.setCurrentRow(0)
        content.addWidget(self.navigation_list)
        content.addWidget(self.page_stack, 1)
        root.addLayout(content, 1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.save_button = QPushButton("Save", central)
        self.readiness_button = QPushButton("Run checks", central)
        self.start_button = QPushButton("Start", central)
        self.start_button.setObjectName("startButton")
        self.start_button.setStyleSheet(
            """
            QPushButton#startButton[readyToStart="true"] {
                background-color: #343A40;
                color: #FFFFFF;
                border: 1px solid #272B30;
                padding: 4px 14px;
            }
            QPushButton#startButton[readyToStart="true"]:hover {
                background-color: #41474E;
            }
            QPushButton#startButton[readyToStart="true"]:pressed {
                background-color: #272B30;
            }
            """
        )
        self._set_start_available(
            False,
            "Run readiness checks before starting.",
        )
        actions.addWidget(self.save_button)
        actions.addWidget(self.readiness_button)
        actions.addWidget(self.start_button)
        root.addLayout(actions)

        self.save_button.clicked.connect(self.save)
        self.readiness_button.clicked.connect(self.save)
        self.start_button.clicked.connect(self._request_start)

    def _build_general_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        general = QGroupBox("General", page)
        form = QFormLayout(general)

        self.search_mode_combo = QComboBox(general)
        for label, key in (
            ("Off", "off"),
            ("Automatic", "auto"),
            ("Forced for next request", "forced"),
        ):
            self.search_mode_combo.addItem(label, key)
        self._select_data(self.search_mode_combo, self._binding.config.search.mode)

        self.token_edit = QLineEdit(general)
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._refresh_token_placeholder()

        form.addRow("Web search", self.search_mode_combo)
        form.addRow("LM Studio token", self.token_edit)
        layout.addWidget(general)
        layout.addStretch(1)
        return page

    def _build_models_page(self, models: tuple[SettingsChoice, ...]) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        models_group = QGroupBox("Models", page)
        form = QFormLayout(models_group)

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
        self.shared_instance_label = QLabel(models_group)
        self.shared_instance_label.setWordWrap(True)
        self._update_shared_instance_annotation()

        form.addRow("Text model", self.text_model_combo)
        form.addRow("Vision model", self.vision_model_combo)
        form.addRow("Model instances", self.shared_instance_label)
        layout.addWidget(models_group)
        layout.addStretch(1)
        return page

    def _build_audio_page(
        self,
        audio_devices: tuple[SettingsChoice, ...],
    ) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        audio = QGroupBox("Audio", page)
        form = QFormLayout(audio)

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
        self.language_combo = QComboBox(audio)
        for label, key in (
            ("Auto (Russian / English)", "auto"),
            ("Russian", "ru"),
            ("English", "en"),
        ):
            self.language_combo.addItem(label, key)
        self._select_data(self.language_combo, self._binding.config.audio.language)

        form.addRow("System audio", self.system_device_combo)
        form.addRow("Microphone", self.microphone_device_combo)
        form.addRow("Recognition language", self.language_combo)
        layout.addWidget(audio)
        layout.addStretch(1)
        return page

    def _build_hotkeys_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(self._build_hotkey_group(page))
        layout.addStretch(1)
        return page

    def _build_appearance_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        appearance = QGroupBox("Appearance", page)
        form = QFormLayout(appearance)

        self.opacity_spin = QDoubleSpinBox(appearance)
        self.opacity_spin.setRange(0.2, 1.0)
        self.opacity_spin.setSingleStep(0.01)
        self.opacity_spin.setDecimals(2)
        self.opacity_spin.setValue(self._binding.config.overlay.opacity)
        self.max_height_spin = QSpinBox(appearance)
        self.max_height_spin.setRange(120, 900)
        self.max_height_spin.setValue(self._binding.config.overlay.max_height)

        form.addRow("Overlay opacity", self.opacity_spin)
        form.addRow("Overlay maximum height", self.max_height_spin)
        layout.addWidget(appearance)
        layout.addStretch(1)
        return page

    def _build_diagnostics_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        readiness = QGroupBox("Readiness", page)
        readiness_layout = QVBoxLayout(readiness)
        self.readiness_status_label = QLabel("Run readiness checks before starting", readiness)
        self.readiness_status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.notification_label = QLabel("", readiness)
        self.notification_label.setTextFormat(Qt.TextFormat.PlainText)
        self.notification_label.setWordWrap(True)
        self.notification_label.hide()
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
        readiness_layout.addWidget(self.notification_label)
        readiness_layout.addWidget(self.readiness_table)
        layout.addWidget(readiness)
        return page

    def _build_hotkey_group(self, parent: QWidget) -> QGroupBox:
        hotkeys = QGroupBox("Hotkeys", parent)
        layout = QGridLayout(hotkeys)
        layout.setColumnStretch(1, 1)
        self.hotkey_edits: dict[HotkeyAction, QKeySequenceEdit] = {}
        self.hotkey_labels: dict[HotkeyAction, QLabel] = {}
        self.hotkey_help: dict[HotkeyAction, QLabel] = {}
        bindings = self._binding.config.hotkeys.as_bindings()
        for row, action in enumerate(HotkeyAction):
            label_text, help_text = HOTKEY_COPY[action]
            label = QLabel(label_text, hotkeys)
            label.setWordWrap(True)
            help_label = QLabel(help_text, hotkeys)
            help_label.setWordWrap(True)
            editor = QKeySequenceEdit(hotkeys)
            editor.setMaximumSequenceLength(1)
            editor.setKeySequence(
                QKeySequence(
                    bindings[action],
                    QKeySequence.SequenceFormat.PortableText,
                )
            )
            self.hotkey_labels[action] = label
            self.hotkey_help[action] = help_label
            self.hotkey_edits[action] = editor
            layout.addWidget(label, row, 0)
            layout.addWidget(help_label, row, 1)
            layout.addWidget(editor, row, 2)

        restore_defaults = QPushButton("Restore defaults", hotkeys)
        restore_defaults.clicked.connect(self.restore_default_hotkeys)
        layout.addWidget(restore_defaults, len(HotkeyAction), 2)
        return hotkeys

    def restore_default_hotkeys(self) -> None:
        for action, editor in self.hotkey_edits.items():
            editor.setKeySequence(
                QKeySequence(
                    DEFAULT_HOTKEY_BINDINGS[action],
                    QKeySequence.SequenceFormat.PortableText,
                )
            )

    def _hotkey_bindings(self) -> dict[HotkeyAction, str]:
        portable_bindings: dict[HotkeyAction, str] = {}
        for action, editor in self.hotkey_edits.items():
            try:
                portable_bindings[action] = HotkeyChord.parse(
                    editor.keySequence().toString(
                        QKeySequence.SequenceFormat.PortableText
                    )
                ).to_portable_text()
            except ValueError as error:
                raise ValueError(f"{HOTKEY_COPY[action][0]}: {error}") from error
        normalized = normalize_bindings(portable_bindings)
        return {action: chord.to_portable_text() for action, chord in normalized.items()}

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

    def set_audio_choices(self, devices: tuple[SettingsChoice, ...]) -> None:
        system_key = cast(str | None, self.system_device_combo.currentData())
        microphone_key = cast(str | None, self.microphone_device_combo.currentData())
        self._populate_choice_combo(
            self.system_device_combo,
            devices,
            current=system_key,
            empty_label="Not selected",
            empty_data=None,
        )
        self._populate_choice_combo(
            self.microphone_device_combo,
            devices,
            current=microphone_key,
            empty_label="Not selected",
            empty_data=None,
        )
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
        self.text_model_combo.currentIndexChanged.connect(self._update_shared_instance_annotation)
        self.vision_model_combo.currentIndexChanged.connect(self._update_shared_instance_annotation)
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
        self.clear_readiness("Run readiness checks again after settings changes")

    def clear_readiness(self, message: str = "Running readiness checks...") -> None:
        self._readiness_report = None
        self.readiness_table.setRowCount(0)
        self._set_start_available(
            False,
            "Run readiness checks before starting.",
        )
        self.readiness_status_label.setText(message)

    def _set_start_available(self, available: bool, tooltip: str) -> None:
        self.start_button.setEnabled(available)
        self.start_button.setProperty("readyToStart", available)
        self.start_button.setToolTip(tooltip)
        style = self.start_button.style()
        assert style is not None
        style.unpolish(self.start_button)
        style.polish(self.start_button)

    def set_readiness_report(self, report: ReadinessReport) -> None:
        self._readiness_report = report
        if report.status != "ready":
            self.navigation_list.setCurrentRow(
                self.page_stack.indexOf(self.diagnostics_page)
            )
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
        if not report.can_start:
            blocking_count = sum(
                result.required and result.status == "failed" for result in report.checks
            )
            suffix = "check" if blocking_count == 1 else "checks"
            self.readiness_status_label.setText(
                f"Readiness: failed — {blocking_count} blocking {suffix}"
            )
            self._set_start_available(
                False,
                f"Start is blocked by {blocking_count} blocking {suffix}.",
            )
        elif report.status == "warning":
            warning_count = sum(result.status != "ready" for result in report.checks)
            suffix = "warning" if warning_count == 1 else "warnings"
            self.readiness_status_label.setText(
                f"Readiness: ready — {warning_count} non-blocking {suffix}"
            )
            self._set_start_available(
                True,
                f"Ready to start; {warning_count} non-blocking {suffix} remain.",
            )
        else:
            self.readiness_status_label.setText("Readiness: ready")
            self._set_start_available(True, "Ready to start.")

    def show_notification(self, message: str) -> None:
        self.notification_label.setText(str(message))
        self.notification_label.show()
        self.navigation_list.setCurrentRow(self.page_stack.indexOf(self.diagnostics_page))

    def save(self) -> bool:
        token = self.token_edit.text()
        try:
            hotkeys = self._hotkey_bindings()
        except ValueError as error:
            message = str(error)
            if message.startswith("Duplicate hotkey"):
                message = f"Hotkey conflict: {message}"
            self.show_notification(message)
            return False
        try:
            requires_readiness = self._binding.apply(
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
                hotkeys=hotkeys,
                token=token,
            )
        except Exception:
            self.readiness_status_label.setText("Settings could not be saved securely")
            self.navigation_list.setCurrentRow(
                self.page_stack.indexOf(self.diagnostics_page)
            )
            return False
        finally:
            if token.strip():
                self.token_edit.clear()
                self._refresh_token_placeholder()
        self._update_shared_instance_annotation()
        self.settings_saved.emit()
        if requires_readiness:
            self.clear_readiness()
            self.readiness_requested.emit()
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
        if not isinstance(geometry, (QByteArray, bytes, bytearray, memoryview)):
            return
        try:
            self.restoreGeometry(geometry)
        except (TypeError, ValueError, RuntimeError):
            return

    def close_from_controller(self) -> None:
        self._controller_close = True
        try:
            self.close()
        finally:
            self._controller_close = False

    def closeEvent(self, event: QCloseEvent | None) -> None:
        self._settings.setValue(self.GEOMETRY_KEY, self.saveGeometry())
        self._settings.sync()
        if not self._controller_close:
            self.close_requested.emit()
        super().closeEvent(event)
