# Overlay Interaction and Question Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Ribbon click-through by default, expose and persist every global hotkey, accept source-preserving RU/EN question triggers from both audio inputs, safely render streamed Markdown, provide visible one-shot screenshot state, and produce concise candidate-style answers with full technical exceptions.

**Architecture:** Keep the existing PyQt6 event-driven runtime. Extend typed configuration and the existing hotkey manager, record the audio source on detected questions, build role-aware context in `ContextBuilder`, and move Markdown display into a resource-blocking renderer that rerenders the accumulated answer. Preserve the current Windows HWND/capture-affinity lifecycle and release pipeline.

**Tech Stack:** Python 3.11/3.12, PyQt6, Pydantic v2, pytest/pytest-qt/pytest-asyncio, Ruff, PyInstaller, Inno Setup, PowerShell, faster-whisper/CTranslate2 CUDA 12.

## Global Constraints

- The application remains a Windows-only PyQt6 desktop application.
- `Interviewer` continues to mean system-audio loopback and `You` continues to mean the local microphone. Triggering a request must not erase this role data.
- The normal overlay mode is click-through. Mouse interaction is available only in an explicit edit mode selected with a configurable global hotkey.
- Capture affinity must be reapplied and verified if changing a window flag recreates the native HWND.
- Existing configurations that do not contain a `hotkeys` section remain valid and receive documented defaults.
- Tokens and other secrets remain in Windows Credential Manager and never enter `config.yaml`, logs, screenshots, source archives, or test fixtures.
- The installer and portable release continue to include the application and bundled STT model. LM Studio, the text/vision model, LM Link, and MCP setup remain user responsibilities.
- Normal conceptual or behavioral answers use 3–6 short sentences or at most five bullets and normally stay at or below 120 words.
- Code, algorithms, debugging, architecture/system design, derivations, and similarly complex technical tasks are exempt from the 120-word limit and must be answered completely.

## File structure

- `interview_assistant/config.py`: typed hotkey configuration and backwards-compatible defaults.
- `interview_assistant/utils/hotkeys.py`: canonical actions, defaults, chord formatting, and atomic binding validation.
- `interview_assistant/ui/settings.py`: discoverable hotkey editor, conflict feedback, and defaults reset.
- `interview_assistant/events.py`: overlay interaction toggle event.
- `interview_assistant/composition.py`: construct production hotkeys from saved configuration.
- `interview_assistant/transcript/detector.py`: source-preserving dual-input trigger detection and indirect RU/EN phrases.
- `interview_assistant/context/models.py`: context-kind contract for a candidate clarification trigger.
- `interview_assistant/context/builder.py`: role-aware required prompt items.
- `interview_assistant/ui/markdown.py`: safe Markdown browser and full-buffer renderer.
- `interview_assistant/ui/overlay.py`: passive/edit state, geometry persistence, contrast, and Markdown integration.
- `interview_assistant/runtime.py`: hotkey actions, screenshot lifecycle messages, and trigger-aware request flow.
- `prompts/interview_system.md`: concise candidate response contract and technical exception.
- `config.yaml`, `README.md`, `START_HERE.md`: visible defaults and user instructions.
- Existing focused unit/integration test modules: regression coverage for each responsibility.

---

### Task 1: Typed hotkey configuration and canonical actions

**Files:**
- Modify: `interview_assistant/utils/hotkeys.py`
- Modify: `interview_assistant/utils/__init__.py`
- Modify: `interview_assistant/config.py`
- Modify: `tests/unit/test_hotkeys.py`
- Modify: `tests/unit/test_config.py`

**Interfaces:**
- Produces: `HotkeyAction.OVERLAY_INTERACTION`.
- Produces: `DEFAULT_HOTKEY_BINDINGS: Mapping[HotkeyAction, str]`.
- Produces: `HotkeyChord.to_portable_text() -> str`.
- Produces: `normalize_bindings(bindings) -> dict[HotkeyAction, HotkeyChord]` with duplicate detection before mutation.
- Produces: `HotkeysConfig.as_bindings() -> dict[HotkeyAction, str]`.
- Preserves: configs without `hotkeys` and rejection of plaintext LM Studio secrets.

- [ ] **Step 1: Write failing hotkey action, formatting, duplicate, and config tests**

Add tests equivalent to:

```python
def test_default_hotkeys_cover_every_action_once() -> None:
    assert set(DEFAULT_HOTKEY_BINDINGS) == set(HotkeyAction)
    assert len(set(DEFAULT_HOTKEY_BINDINGS.values())) == len(HotkeyAction)
    assert DEFAULT_HOTKEY_BINDINGS[HotkeyAction.OVERLAY_INTERACTION] == "ctrl+shift+i"


def test_chord_has_stable_portable_text() -> None:
    chord = HotkeyChord.parse("Shift + Control + I")
    assert chord.to_portable_text() == "ctrl+shift+i"


def test_normalize_bindings_rejects_cross_action_collision() -> None:
    with pytest.raises(ValueError, match="Duplicate hotkey"):
        normalize_bindings(
            {
                HotkeyAction.FORCE_REQUEST: "ctrl+shift+x",
                HotkeyAction.SCREENSHOT: "control+shift+x",
            }
        )


def test_config_without_hotkeys_uses_complete_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("audio: {}\n", encoding="utf-8")
    config = AppConfig.load(path)
    assert config.hotkeys.as_bindings() == dict(DEFAULT_HOTKEY_BINDINGS)


def test_config_rejects_duplicate_hotkeys() -> None:
    with pytest.raises(ValidationError, match="Duplicate hotkey"):
        AppConfig.model_validate(
            {
                "hotkeys": {
                    "force_request": "ctrl+shift+x",
                    "screenshot": "control+shift+x",
                }
            }
        )
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
uv sync --extra dev --extra cuda --frozen
.\.venv\Scripts\python.exe -m pytest tests\unit\test_hotkeys.py tests\unit\test_config.py -q
```

Expected: failures because `OVERLAY_INTERACTION`, `HotkeysConfig`, `to_portable_text`, and `normalize_bindings` do not exist.

- [ ] **Step 3: Implement the minimal typed configuration**

Add the action and a single source of defaults in `utils/hotkeys.py`:

```python
class HotkeyAction(StrEnum):
    FORCE_REQUEST = "force_request"
    SCREENSHOT = "screenshot"
    PAUSE = "pause"
    OVERLAY_VISIBILITY = "overlay_visibility"
    OVERLAY_INTERACTION = "overlay_interaction"
    FORCED_WEB_SEARCH = "forced_web_search"
    CLEAR_ANSWER = "clear_answer"


DEFAULT_HOTKEY_BINDINGS = MappingProxyType(
    {
        HotkeyAction.FORCE_REQUEST: "ctrl+shift+space",
        HotkeyAction.SCREENSHOT: "ctrl+shift+s",
        HotkeyAction.PAUSE: "ctrl+shift+p",
        HotkeyAction.OVERLAY_VISIBILITY: "ctrl+shift+o",
        HotkeyAction.OVERLAY_INTERACTION: "ctrl+shift+i",
        HotkeyAction.FORCED_WEB_SEARCH: "ctrl+shift+w",
        HotkeyAction.CLEAR_ANSWER: "ctrl+shift+c",
    }
)
```

Make formatting and validation deterministic:

```python
_MODIFIER_ORDER = ("ctrl", "shift", "alt", "win")

def to_portable_text(self) -> str:
    ordered = [name for name in _MODIFIER_ORDER if name in self.modifiers]
    return "+".join((*ordered, self.key))


def normalize_bindings(
    bindings: Mapping[HotkeyAction | str, str | HotkeyChord],
) -> dict[HotkeyAction, HotkeyChord]:
    normalized: dict[HotkeyAction, HotkeyChord] = {}
    reverse: dict[HotkeyChord, HotkeyAction] = {}
    for raw_action, raw_chord in bindings.items():
        action = HotkeyAction(raw_action)
        chord = HotkeyChord.parse(raw_chord)
        if previous := reverse.get(chord):
            raise ValueError(
                f"Duplicate hotkey for {previous.value} and {action.value}"
            )
        normalized[action] = chord
        reverse[chord] = action
    return normalized
```

Have `HotkeyManager.update_bindings()` call `normalize_bindings()` before taking the lock or replacing `_bindings`.

Add `HotkeysConfig` with all seven string fields, defaults copied from `DEFAULT_HOTKEY_BINDINGS`, an after-validator that validates the complete map, and `as_bindings()` returning portable strings keyed by `HotkeyAction`. Add `hotkeys: HotkeysConfig = HotkeysConfig()` to `AppConfig`.

- [ ] **Step 4: Verify GREEN and run serialization regression**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_hotkeys.py tests\unit\test_config.py -q
```

Expected: all focused tests pass; saving and loading a config preserves all seven chords and does not write a secret.

- [ ] **Step 5: Commit the task**

```powershell
git add interview_assistant\utils\hotkeys.py interview_assistant\utils\__init__.py interview_assistant\config.py tests\unit\test_hotkeys.py tests\unit\test_config.py
git commit -m "feat: add configurable hotkey schema"
```

---

### Task 2: Discoverable hotkey editor in Settings

**Files:**
- Modify: `interview_assistant/ui/settings.py`
- Modify: `tests/unit/test_settings.py`
- Modify: `tests/unit/test_application_controller.py`

**Interfaces:**
- Consumes: `HotkeysConfig`, `DEFAULT_HOTKEY_BINDINGS`, `HotkeyChord.to_portable_text()`.
- Produces: `SettingsWindow.hotkey_edits: dict[HotkeyAction, QKeySequenceEdit]`.
- Produces: `SettingsWindow.restore_default_hotkeys() -> None`.
- Extends: `SettingsBinding.apply(..., hotkeys: Mapping[HotkeyAction | str, str]) -> None`.

- [ ] **Step 1: Write failing Settings UI and persistence tests**

Add tests equivalent to:

```python
def test_settings_exposes_every_hotkey_with_help_text(qtbot) -> None:
    window, binding = make_settings(qtbot)
    assert set(window.hotkey_edits) == set(HotkeyAction)
    assert window.hotkey_labels[HotkeyAction.FORCE_REQUEST].text() == (
        "Отправить текущий контекст разговора"
    )
    assert "послед" in window.hotkey_help[HotkeyAction.FORCE_REQUEST].text().casefold()
    assert "следующ" in window.hotkey_help[HotkeyAction.SCREENSHOT].text().casefold()


def test_restore_default_hotkeys_updates_all_editors(qtbot) -> None:
    window, _ = make_settings(qtbot)
    window.hotkey_edits[HotkeyAction.SCREENSHOT].setKeySequence("Ctrl+Alt+F11")
    window.restore_default_hotkeys()
    assert hotkey_text(window, HotkeyAction.SCREENSHOT) == "ctrl+shift+s"
    assert hotkey_text(window, HotkeyAction.OVERLAY_INTERACTION) == "ctrl+shift+i"


def test_duplicate_hotkeys_are_named_and_not_saved(qtbot) -> None:
    window, binding = make_settings(qtbot)
    window.hotkey_edits[HotkeyAction.FORCE_REQUEST].setKeySequence("Ctrl+Alt+F10")
    window.hotkey_edits[HotkeyAction.SCREENSHOT].setKeySequence("Ctrl+Alt+F10")
    assert not window.save()
    assert "конфликт" in window.notification_label.text().casefold()
    assert binding.config.hotkeys == HotkeysConfig()


def test_valid_hotkeys_are_persisted_with_other_settings(qtbot) -> None:
    window, binding = make_settings(qtbot)
    window.hotkey_edits[HotkeyAction.OVERLAY_VISIBILITY].setKeySequence("Ctrl+Alt+F9")
    assert window.save()
    assert binding.config.hotkeys.overlay_visibility == "ctrl+alt+f9"
```

- [ ] **Step 2: Run the Settings tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_settings.py tests\unit\test_application_controller.py -q
```

Expected: failures because the hotkey group and `hotkeys` apply argument are absent.

- [ ] **Step 3: Implement the hotkey group and form validation**

Use a dedicated `QGroupBox("Горячие клавиши")` below Configuration. Create one `QKeySequenceEdit` per action, call `setMaximumSequenceLength(1)`, and store it in `hotkey_edits`. Place a label and a wrapped help `QLabel` beside each editor. Use this copy map:

```python
HOTKEY_COPY = {
    HotkeyAction.FORCE_REQUEST: (
        "Отправить текущий контекст разговора",
        "Отправляет последнюю реплику преподавателя и недавний диалог обеих сторон.",
    ),
    HotkeyAction.SCREENSHOT: (
        "Подготовить снимок для следующего запроса",
        "Одноразово добавляет следующий пригодный снимок в контекст модели.",
    ),
    HotkeyAction.PAUSE: (
        "Пауза или возобновление распознавания",
        "Останавливает или продолжает обработку обоих аудиоисточников.",
    ),
    HotkeyAction.OVERLAY_VISIBILITY: (
        "Показать или скрыть окно помощника",
        "Временно скрывает Ribbon, не закрывая приложение.",
    ),
    HotkeyAction.OVERLAY_INTERACTION: (
        "Изменить положение или размер окна",
        "Переключает click-through и режим настройки Ribbon.",
    ),
    HotkeyAction.FORCED_WEB_SEARCH: (
        "Включить поиск для следующего запроса",
        "Принудительно разрешает web search только для следующего ответа.",
    ),
    HotkeyAction.CLEAR_ANSWER: (
        "Очистить ответ и историю разговора",
        "Удаляет текущий ответ и transcript history из памяти приложения.",
    ),
}
```

Normalize each `QKeySequence` via `QKeySequence.SequenceFormat.PortableText`, parse it with `HotkeyChord`, validate the complete map with `normalize_bindings`, and pass portable strings into `SettingsBinding.apply`. Show the real validation message in `notification_label`; do not collapse every failure into the generic secure-save message.

- [ ] **Step 4: Verify GREEN and controller compatibility**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_settings.py tests\unit\test_application_controller.py -q
```

Expected: all focused tests pass and a successful save still emits exactly one `settings_saved` and one `readiness_requested` signal.

- [ ] **Step 5: Commit the task**

```powershell
git add interview_assistant\ui\settings.py tests\unit\test_settings.py tests\unit\test_application_controller.py
git commit -m "feat: expose all hotkeys in settings"
```

---

### Task 3: Source-preserving dual-input question detection and context

**Files:**
- Modify: `interview_assistant/transcript/detector.py`
- Modify: `interview_assistant/context/models.py`
- Modify: `interview_assistant/context/builder.py`
- Modify: `tests/unit/test_detector.py`
- Modify: `tests/unit/test_context_builder.py`
- Modify: `tests/integration/test_application_flow.py`

**Interfaces:**
- Produces: `DetectedQuestion.trigger_source: AudioSource | None = None`.
- Consumes: final STT hypotheses from both `SYSTEM` and `MICROPHONE`.
- Produces: role-labelled `Latest interviewer request` and `Candidate clarification trigger` context items.
- Preserves: source-independent duplicate suppression and manual force behavior.

- [ ] **Step 1: Replace the obsolete microphone rejection test and add indirect phrase fixtures**

Add tests equivalent to:

```python
@pytest.mark.parametrize("source", [AudioSource.SYSTEM, AudioSource.MICROPHONE])
def test_final_question_from_either_source_preserves_trigger_source(source) -> None:
    result = QuestionDetector(cooldown_seconds=0).detect(source, "Как работает B-tree?")
    assert result is not None
    assert result.trigger_source is source


@pytest.mark.parametrize(
    "text",
    [
        "Хотелось бы услышать ваше мнение о CAP theorem",
        "Как вы считаете, когда нужна eventual consistency",
        "Что вы думаете о микросервисах",
        "Раскройте тему индексов в PostgreSQL",
        "Можете подробнее рассказать про optimistic locking",
        "I'd like to hear your opinion on eventual consistency",
        "What's your view on microservices",
        "Could you elaborate on optimistic locking",
        "Walk me through a B-tree lookup",
    ],
)
def test_indirect_ru_en_interview_requests_trigger(text: str) -> None:
    assert QuestionDetector(cooldown_seconds=0).detect(AudioSource.SYSTEM, text)


@pytest.mark.parametrize(
    "text",
    [
        "У него интересное мнение о микросервисах",
        "В документации встречается фраза как вы считаете",
        "My opinion on caching changed last year",
        "The guide contains the phrase walk me through",
        "Я использовал Redis в прошлом проекте",
    ],
)
def test_indirect_keywords_inside_narration_do_not_trigger(text: str) -> None:
    assert QuestionDetector().detect(AudioSource.MICROPHONE, text) is None


def test_same_question_from_both_sources_is_suppressed() -> None:
    detector = QuestionDetector(cooldown_seconds=15)
    assert detector.detect(AudioSource.SYSTEM, "What is a B-tree?") is not None
    assert detector.detect(AudioSource.MICROPHONE, "What is a B-tree?") is None
```

- [ ] **Step 2: Add failing role-aware context tests**

```python
def test_microphone_trigger_keeps_candidate_and_interviewer_roles() -> None:
    store = TranscriptStore()
    add_final(store, AudioSource.SYSTEM, "Хотелось бы услышать мнение о CAP", 10.0)
    add_final(store, AudioSource.MICROPHONE, "То есть про consistency trade-offs?", 11.0)
    question = DetectedQuestion(
        1,
        "theory",
        "То есть про consistency trade-offs?",
        11.0,
        AudioSource.MICROPHONE,
    )
    snapshot = ContextBuilder(store, latest_question=question).normal()
    assert "Latest interviewer request:\nХотелось бы услышать мнение о CAP" in snapshot.prompt
    assert "Candidate clarification trigger:\nТо есть про consistency trade-offs?" in snapshot.prompt
    assert snapshot.items[2].source is AudioSource.MICROPHONE


def test_system_trigger_is_labelled_as_interviewer_request() -> None:
    question = DetectedQuestion(
        1, "theory", "Что такое CAP?", 1.0, AudioSource.SYSTEM
    )
    snapshot = ContextBuilder(TranscriptStore(), latest_question=question).normal()
    assert "Latest interviewer request:\nЧто такое CAP?" in snapshot.prompt
    assert "Candidate clarification trigger" not in snapshot.prompt
```

- [ ] **Step 3: Run detector/context tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_detector.py tests\unit\test_context_builder.py tests\integration\test_application_flow.py -q
```

Expected: the microphone trigger, `trigger_source`, indirect phrases, and role-aware prompt assertions fail.

- [ ] **Step 4: Implement minimal source-aware detection**

Append the backwards-compatible field to the dataclass:

```python
@dataclass(frozen=True, slots=True)
class DetectedQuestion:
    request_id: int
    kind: QuestionKind
    text: str
    detected_at: float
    trigger_source: AudioSource | None = None
```

Change the detector gate to reject only partial hypotheses, pass `source` into automatic results, and keep `None` in `force()`. Add anchored/bounded phrase expressions for the exact agreed RU/EN families; do not match isolated `opinion`/`мнение` tokens. Keep `_recent` shared across sources.

In `ContextBuilder`, create required candidates with these rules:

```python
if question.trigger_source is AudioSource.MICROPHONE:
    required.append(
        ContextItem(
            "clarification",
            "Candidate clarification trigger",
            question_text,
            source=AudioSource.MICROPHONE,
            timestamp=question.detected_at,
        )
    )
    latest_interviewer = transcript_store.latest(AudioSource.SYSTEM)
    if latest_interviewer is not None:
        required.append(
            ContextItem(
                "question",
                "Latest interviewer request",
                latest_interviewer.text,
                source=AudioSource.SYSTEM,
                timestamp=latest_interviewer.ended_at,
            )
        )
else:
    required.append(
        ContextItem(
            "question",
            "Latest interviewer request",
            question_text,
            source=AudioSource.SYSTEM,
            timestamp=question.detected_at,
        )
    )
```

Skip exact source/text duplicates when appending optional history so the trigger and latest interviewer request do not appear twice. Apply the same labels in recovery context.

- [ ] **Step 5: Verify GREEN and integration behavior**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_detector.py tests\unit\test_context_builder.py tests\integration\test_application_flow.py -q
```

Expected: all focused tests pass; a microphone clarification submits one model request whose prompt retains both roles.

- [ ] **Step 6: Commit the task**

```powershell
git add interview_assistant\transcript\detector.py interview_assistant\context\models.py interview_assistant\context\builder.py tests\unit\test_detector.py tests\unit\test_context_builder.py tests\integration\test_application_flow.py
git commit -m "feat: detect role-aware questions from both audio sources"
```

---

### Task 4: Passive click-through and configurable edit mode

**Files:**
- Modify: `interview_assistant/events.py`
- Modify: `interview_assistant/utils/hotkeys.py`
- Modify: `interview_assistant/composition.py`
- Modify: `interview_assistant/runtime.py`
- Modify: `interview_assistant/ui/overlay.py`
- Modify: `tests/unit/test_hotkeys.py`
- Modify: `tests/unit/test_overlay.py`
- Modify: `tests/unit/test_runtime_lifecycle.py`
- Modify: `tests/unit/test_production_composition.py`
- Modify: `tests/unit/test_windows_affinity.py`

**Interfaces:**
- Produces: `EventBus.overlay_interaction_toggled`.
- Maps: `HotkeyAction.OVERLAY_INTERACTION -> overlay_interaction_toggled`.
- Produces: `LiquidRibbon.is_edit_mode: bool`.
- Produces: `LiquidRibbon.set_edit_mode(enabled: bool) -> None` and `toggle_edit_mode() -> None`.
- Consumes: `config.hotkeys.as_bindings()` in production composition.

- [ ] **Step 1: Write failing event, composition, and runtime wiring tests**

```python
def test_overlay_interaction_hotkey_emits_the_matching_signal() -> None:
    assert signal_name(HotkeyAction.OVERLAY_INTERACTION) == "overlay_interaction_toggled"


def test_production_composition_uses_saved_hotkeys(app, config) -> None:
    config.hotkeys.overlay_interaction = "ctrl+alt+f8"
    components = build_production_components(app, config, None, loop=loop)
    assert components.runtime.services.hotkeys.bindings[
        HotkeyAction.OVERLAY_INTERACTION
    ] == HotkeyChord.parse("ctrl+alt+f8")


def test_runtime_toggles_overlay_edit_mode_from_event(runtime, app) -> None:
    assert not app.ribbon.is_edit_mode
    app.events.overlay_interaction_toggled.emit()
    assert app.ribbon.is_edit_mode
    app.events.overlay_interaction_toggled.emit()
    assert not app.ribbon.is_edit_mode
```

- [ ] **Step 2: Write failing overlay state and persistence tests**

```python
def test_ribbon_starts_click_through_and_edit_mode_restores_input(qtbot) -> None:
    ribbon = LiquidRibbon(EventBus(), settings=None)
    qtbot.addWidget(ribbon)
    assert ribbon.windowFlags() & Qt.WindowType.WindowTransparentForInput
    assert not ribbon.is_edit_mode
    ribbon.set_edit_mode(True)
    assert not (ribbon.windowFlags() & Qt.WindowType.WindowTransparentForInput)
    assert ribbon.is_edit_mode
    assert ribbon.property("editMode") is True


def test_edit_transition_preserves_geometry_content_and_visibility(qtbot) -> None:
    ribbon = LiquidRibbon(EventBus(), settings=None)
    qtbot.addWidget(ribbon)
    ribbon.setGeometry(100, 120, 780, 220)
    ribbon.answer_text = "answer"
    ribbon.show()
    before = ribbon.geometry()
    ribbon.set_edit_mode(True)
    assert ribbon.geometry() == before
    assert ribbon.answer_text == "answer"
    assert ribbon.isVisible()


def test_geometry_is_debounced_into_qsettings(qtbot) -> None:
    settings = memory_settings()
    ribbon = LiquidRibbon(EventBus(), settings=settings)
    qtbot.addWidget(ribbon)
    ribbon.set_edit_mode(True)
    ribbon.setGeometry(140, 160, 720, 240)
    qtbot.waitUntil(lambda: settings.contains(LiquidRibbon.GEOMETRY_KEY))
    restored = LiquidRibbon(EventBus(), settings=settings)
    qtbot.addWidget(restored)
    assert restored.geometry() == ribbon.geometry()
```

Add an affinity spy test that changes the native ID or emits `WinIdChange` during an edit transition and requires the current HWND to be reapplied before the transition completes.

- [ ] **Step 3: Run focused tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_hotkeys.py tests\unit\test_overlay.py tests\unit\test_runtime_lifecycle.py tests\unit\test_production_composition.py tests\unit\test_windows_affinity.py -q
```

Expected: failures for the missing action signal, default click-through flag, edit state, saved binding use, and geometry debounce.

- [ ] **Step 4: Implement wiring and click-through state**

Add the event signal and signal map entry, construct `HotkeyManager` from `config.hotkeys.as_bindings()`, and connect runtime actions using the existing connect/disconnect symmetry.

In `LiquidRibbon`, add:

```python
def set_edit_mode(self, enabled: bool) -> None:
    enabled = bool(enabled)
    if enabled == self.is_edit_mode:
        return
    was_visible = self.isVisible()
    geometry = self.geometry()
    self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, not enabled)
    self.is_edit_mode = enabled
    self.setProperty("editMode", enabled)
    self.surface.setProperty("editMode", enabled)
    self.setGeometry(geometry)
    if was_visible:
        self.show()
    self._update_edit_indicator()
    self._apply_capture_exclusion_to_current_hwnd()
```

Set `WindowTransparentForInput` in the initial flags and initialize `is_edit_mode = False`. Add a hidden edit label with `Режим настройки`, a `#50DE73` border when active, and ensure move/resize handlers return without handling input outside edit mode. A single-shot `QTimer` started by `moveEvent`/`resizeEvent` persists geometry after 250 ms, but remains disabled during construction and restore.

If the native window transition raises, restore flags, geometry, visibility, and the previous mode, then emit a concise notification. Preserve the existing `WinIdChange` affinity path.

- [ ] **Step 5: Verify GREEN and the real Windows affinity probe**

Run the focused tests again, then execute the existing real affinity probe from the Windows migration plan against a shown passive Ribbon and after two edit-mode transitions. Expected: both states return the supported exclusion affinity (`0x11`) on this machine and the HWND used for the current visible window is verified.

- [ ] **Step 6: Commit the task**

```powershell
git add interview_assistant\events.py interview_assistant\utils\hotkeys.py interview_assistant\composition.py interview_assistant\runtime.py interview_assistant\ui\overlay.py tests\unit\test_hotkeys.py tests\unit\test_overlay.py tests\unit\test_runtime_lifecycle.py tests\unit\test_production_composition.py tests\unit\test_windows_affinity.py
git commit -m "feat: add click-through ribbon edit mode"
```

---

### Task 5: Safe full-buffer Markdown and higher contrast

**Files:**
- Create: `interview_assistant/ui/markdown.py`
- Modify: `interview_assistant/ui/overlay.py`
- Modify: `tests/unit/test_overlay.py`

**Interfaces:**
- Produces: `SafeMarkdownBrowser(QTextBrowser)` whose `loadResource` never loads local or remote model-provided resources.
- Produces: `render_safe_markdown(browser, markdown, *, preserve_scroll=True) -> None`.
- Preserves: exact raw `LiquidRibbon.answer_text` and request-ID stale-delta protection.

- [ ] **Step 1: Write failing split-stream and Markdown feature tests**

```python
def test_markdown_split_across_deltas_renders_without_delimiters(qtbot) -> None:
    bus = EventBus()
    ribbon = LiquidRibbon(bus, settings=None)
    qtbot.addWidget(ribbon)
    bus.answer_reset.emit(7)
    bus.answer_delta.emit(7, "## **Структура")
    bus.answer_delta.emit(7, " проблемы**\n\n- Первый пункт\n- Второй пункт")
    assert ribbon.answer_text.startswith("## **Структура")
    assert "**" not in ribbon.answer_browser.toPlainText()
    assert "Структура проблемы" in ribbon.answer_browser.toPlainText()
    assert ribbon.answer_browser.document().blockCount() >= 3


def test_fenced_code_and_inline_code_receive_monospace_format(qtbot) -> None:
    ribbon = rendered_ribbon("Use `dict`:\n```python\nprint('ok')\n```")
    assert "print('ok')" in ribbon.answer_browser.toPlainText()
    assert cursor_at(ribbon.answer_browser, "dict").charFormat().fontFixedPitch()
    assert cursor_at(ribbon.answer_browser, "print").charFormat().fontFixedPitch()


def test_model_markdown_cannot_load_resources_or_activate_links(qtbot) -> None:
    ribbon = rendered_ribbon(
        "![x](file:///private.txt) [open](https://evil.invalid) "
        "<img src='https://evil.invalid/pixel'>"
    )
    assert ribbon.answer_browser.resource_requests == ()
    assert not ribbon.answer_browser.openLinks()
    assert not ribbon.answer_browser.openExternalLinks()
    assert "private.txt" not in ribbon.answer_browser.toPlainText()


def test_stream_rerender_preserves_manual_scroll_position(qtbot) -> None:
    ribbon = rendered_ribbon("line\n" * 100)
    ribbon.set_edit_mode(True)
    bar = ribbon.answer_browser.verticalScrollBar()
    bar.setValue(bar.maximum() // 3)
    before = bar.value() / bar.maximum()
    ribbon.append_delta(active_id(ribbon), "tail")
    after = bar.value() / bar.maximum()
    assert after == pytest.approx(before, abs=0.08)
```

- [ ] **Step 2: Add failing contrast assertions**

Reuse the existing contrast helpers to assert final primary and secondary composites are at least `4.5:1` on black and white desktop backgrounds at the minimum supported effective window opacity. Assert the answer browser font pixel size is 15 and headings are compact rather than document-sized.

- [ ] **Step 3: Run overlay tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_overlay.py -k "markdown or resource or contrast or scroll" -q
```

Expected: split Markdown, full Markdown features, resource blocking, and 15 px readability assertions fail.

- [ ] **Step 4: Implement the safe renderer**

Create `SafeMarkdownBrowser` with a deny-by-default `loadResource` override and a tuple used only for asserting attempted resource requests in tests. Before `QTextDocument.setMarkdown`, neutralize Markdown image syntax to its alt text and use Qt Markdown with `MarkdownNoHTML`. Keep links non-activating and remove link destinations from the displayed document if Qt creates anchors.

`render_safe_markdown` records whether the scrollbar was at the bottom and otherwise records its relative position, calls `document.setMarkdown(full_text, features)`, reapplies the compact default stylesheet, and restores bottom/relative position.

Replace `_safe_markdown(delta)` insertion in `append_delta` with:

```python
self.answer_text += delta
render_safe_markdown(self.answer_browser, self.answer_text, preserve_scroll=True)
```

Set the answer text to near-white, increase it to 15 px, darken the answer panel, and add compact paragraph/list/code spacing. Keep raw `answer_text` unchanged.

- [ ] **Step 5: Verify GREEN and the complete overlay suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_overlay.py -q
```

Expected: all overlay tests pass with no external resource access and no visible Markdown delimiters in split streams.

- [ ] **Step 6: Commit the task**

```powershell
git add interview_assistant\ui\markdown.py interview_assistant\ui\overlay.py tests\unit\test_overlay.py
git commit -m "feat: render safe readable streaming markdown"
```

---

### Task 6: Visible one-shot screenshot lifecycle

**Files:**
- Modify: `interview_assistant/runtime.py`
- Modify: `tests/unit/test_runtime_lifecycle.py`
- Modify: `tests/integration/test_application_flow.py`

**Interfaces:**
- Consumes: existing `CaptureService.capture_for_event("manual", manual=True)`.
- Preserves: `manual_image_path` as a single optional pending image.
- Produces: exact visible lifecycle messages for creating, ready, attached, and failure states.

- [ ] **Step 1: Write failing screenshot-state tests**

```python
async def test_manual_screenshot_emits_creating_ready_and_attached_states(qtbot) -> None:
    runtime, app, capture, client = prepared_runtime_with_manual_image()
    messages: list[str] = []
    app.events.notification.connect(messages.append)
    await runtime.capture_manual_screenshot()
    assert messages[-2:] == [
        "Снимок создаётся",
        "Снимок готов для следующего запроса",
    ]
    await runtime.force_latest_request()
    assert "Снимок добавлен в запрос" in messages
    assert runtime.manual_image_path is None
    assert payload_has_image(client.payloads[-1])


async def test_pending_screenshot_is_consumed_only_once() -> None:
    runtime, _, capture, client = prepared_runtime_with_manual_image()
    await runtime.capture_manual_screenshot()
    await submit_question(runtime, "What is shown?")
    await submit_question(runtime, "Explain it again")
    assert payload_has_image(client.payloads[-2])
    assert not payload_has_image(client.payloads[-1])


async def test_failed_new_screenshot_clears_stale_pending_image() -> None:
    runtime, app, capture, _ = prepared_runtime_with_replacing_failure()
    await runtime.capture_manual_screenshot()
    capture.fail_next = True
    await runtime.capture_manual_screenshot()
    assert runtime.manual_image_path is None
    assert "Снимок не создан" in last_notification(app)
```

- [ ] **Step 2: Run runtime screenshot tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_runtime_lifecycle.py tests\integration\test_application_flow.py -k "screenshot or manual_image" -q
```

Expected: creating/attached messages and stale-image clearing assertions fail.

- [ ] **Step 3: Implement the lifecycle at the ownership boundaries**

At the beginning of `capture_manual_screenshot`, clear the previous pending reference and emit `Снимок создаётся`. On a usable result, assign only the new path and emit `Снимок готов для следующего запроса`. On unusable/protected/failure, keep the reference `None` and emit a concise `Снимок не создан: ...` message without paths.

In `_answer`, atomically take and clear `_manual_image_path`. Emit `Снимок добавлен в запрос` only after `build_chat_payload` has successfully produced the outbound payload containing the image. Never restore the reference after request failure.

- [ ] **Step 4: Verify GREEN and shutdown cancellation regression**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_runtime_lifecycle.py tests\integration\test_application_flow.py -q
```

Expected: all runtime/integration tests pass, including shutdown during a blocking manual capture.

- [ ] **Step 5: Commit the task**

```powershell
git add interview_assistant\runtime.py tests\unit\test_runtime_lifecycle.py tests\integration\test_application_flow.py
git commit -m "feat: show one-shot screenshot lifecycle"
```

---

### Task 7: Candidate-style prompt, documentation, and end-to-end contracts

**Files:**
- Modify: `prompts/interview_system.md`
- Modify: `config.yaml`
- Modify: `README.md`
- Modify: `START_HERE.md`
- Modify: `tests/unit/test_context_builder.py`
- Modify: `tests/unit/test_production_composition.py`
- Modify: `tests/unit/test_release_packaging.py`
- Modify: `tests/integration/test_application_flow.py`

**Interfaces:**
- Produces: packaged prompt with the 120-word normal rule, complete technical-answer exception, and microphone clarification instruction.
- Produces: documented seven-hotkey configuration and passive/edit Ribbon behavior.
- Preserves: prompt packaging in both source and frozen builds.

- [ ] **Step 1: Write failing prompt and release-content tests**

```python
def test_packaged_prompt_defines_concise_candidate_style_and_exception() -> None:
    prompt = Path("prompts/interview_system.md").read_text(encoding="utf-8")
    lowered = prompt.casefold()
    assert "120" in prompt
    assert "3–6" in prompt or "3-6" in prompt
    assert "candidate clarification trigger" in lowered
    assert "код" in lowered and "system design" in lowered
    assert "ограничение" in lowered and "не применяется" in lowered
    assert "как ии" in lowered


def test_example_config_documents_all_hotkeys() -> None:
    config = AppConfig.load(Path("config.yaml"))
    assert config.hotkeys.as_bindings() == dict(DEFAULT_HOTKEY_BINDINGS)


def test_release_archive_includes_updated_prompt_and_config(release_manifest) -> None:
    assert "prompts/interview_system.md" in release_manifest.source_files
    assert "config.yaml" in release_manifest.source_files
```

- [ ] **Step 2: Run prompt/release tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_context_builder.py tests\unit\test_production_composition.py tests\unit\test_release_packaging.py tests\integration\test_application_flow.py -q
```

Expected: prompt copy and example hotkey configuration assertions fail.

- [ ] **Step 3: Rewrite the prompt and update user documentation**

Make the prompt open with a role contract equivalent to:

```markdown
Ты формулируешь ответ от лица подготовленного кандидата на собеседовании.
Отвечай сразу по существу, без вступления о том, что ты помощник или ИИ.

Для обычного вопроса используй 3–6 коротких предложений или не более пяти
пунктов и обычно не превышай 120 слов.

Ограничение 120 слов не применяется к коду, алгоритмам, debugging, system
design, архитектуре, техническим выводам и другим задачам, где сокращение
сделает решение неполным. Для них дай полное корректное решение с кодом,
допущениями, trade-offs и сложностью, когда это уместно.

`Candidate clarification trigger` — это уточнение кандидата о текущей теме.
Используй `Latest interviewer request` и transcript, чтобы подготовить ответ
кандидата преподавателю; не отвечай кандидату как отдельному собеседнику.
```

Remove the old 300–400-token default and long generic examples that encourage verbose assistant prose. Retain hallucination, screenshot, language, and untrusted-tool-output safety rules.

Add the complete `hotkeys:` mapping to `config.yaml`. Update README/START_HERE with the Settings-based assignment workflow, passive/edit modes, screenshot statuses, both-source detection, and the current defaults. State that defaults are visible fallbacks and users should rely on Settings rather than memorizing the table.

- [ ] **Step 4: Verify GREEN and no-secret documentation scan**

Run the focused tests, then:

```powershell
rg -n "sk-lm-|CONTEXT7_API_KEY\s*[:=]\s*[^<%$]" README.md START_HERE.md config.yaml prompts docs tests
```

Expected: tests pass and the secret scan returns no credential values.

- [ ] **Step 5: Commit the task**

```powershell
git add prompts\interview_system.md config.yaml README.md START_HERE.md tests\unit\test_context_builder.py tests\unit\test_production_composition.py tests\unit\test_release_packaging.py tests\integration\test_application_flow.py
git commit -m "feat: tune candidate responses and document controls"
```

---

### Task 8: Full verification, Windows smoke tests, and release replacement

**Files:**
- Replace generated delivery artifacts at repository root:
  - `InterviewAssistant.exe`
  - `_internal/`
  - `InterviewAssistant-Setup-0.1.0-win64.exe`
  - `InterviewAssistant-source-0.1.0.zip`
  - `SHA256SUMS.txt`

**Interfaces:**
- Consumes: all prior task contracts.
- Produces: fresh test, lint, CUDA, affinity, portable, installer, archive, and checksum evidence.
- Preserves: source archive has one top-level directory, no Git remotes/reflogs, bundled STT exactly once, a generated sanitized `config.yaml`, and no credentials or live machine configuration.

- [ ] **Step 1: Run the complete quality suite**

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
```

Expected: zero failed tests and Ruff exits 0. Record the exact test count from fresh output.

- [ ] **Step 2: Run source diagnostics and real CUDA STT**

```powershell
.\.venv\Scripts\python.exe main.py --diagnostics --no-gui --diagnostics-output .\build\source-diagnostics.json
.\.venv\Scripts\python.exe scripts\verify_cuda.py --config .\config.yaml --fixture .\assets\diagnostics\stt-smoke.wav --output .\build\cuda-stt.json
```

Expected: diagnostics `status=ok`; CUDA exposes at least one device; bundled STT model loads and produces non-empty final inference without exposing its path.

- [ ] **Step 3: Run real Qt/Windows click-through and affinity smoke**

Launch a controlled Qt probe that shows the Ribbon, verifies passive `WindowTransparentForInput`, records the current HWND/affinity, toggles edit mode twice through the event signal, moves/resizes it, and verifies the final HWND has exclusion affinity. Expected: geometry is restored in a second Ribbon instance and the final affinity is supported (`0x11` on the validated machine).

- [ ] **Step 4: Build the fresh onedir portable application**

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build.ps1
```

Expected: exit 0; `dist\InterviewAssistant\InterviewAssistant.exe` and bundled `_internal` runtime/STT/branding exist.

- [ ] **Step 5: Smoke-test the frozen application**

```powershell
.\dist\InterviewAssistant\InterviewAssistant.exe --diagnostics --no-gui --diagnostics-output .\build\packaged-diagnostics.json
```

Expected: exit 0, `status=ok`, `frozen=true`. Start the GUI, require it to remain alive for 15 seconds, then close it normally. Verify Settings contains all seven hotkeys and the Ribbon starts passive.

- [ ] **Step 6: Build and isolate-test the installer**

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_installer.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\smoke_installer.ps1 -InstallerPath .\dist\installer\InterviewAssistant-Setup-0.1.0-win64.exe
```

Install silently into the verified workspace-contained smoke path, revalidate the exact installed inventory and every STT file size/SHA-256 against the manifest, run installed frozen diagnostics, then silently uninstall. Expected: validation/install/diagnostics/uninstall exit 0 and installer-owned runtime files are removed.

- [ ] **Step 7: Rebuild and inspect the instructor source archive**

```powershell
$sourceCommit = git rev-parse HEAD
powershell -ExecutionPolicy Bypass -File .\scripts\create_source_archive.ps1 -SourceCommit $sourceCommit
```

Inspect the ZIP with the same explicit source commit before replacement. Expected: one top-level directory; `START_HERE.md`, updated prompt/docs, generated sanitized `config.yaml` with all seven hotkeys, branding, source/tests, and six STT files including one `model.bin`; no live machine config, `_internal`, build caches, Credential Manager data, token-shaped values, private keys, remotes, reflogs, or unreachable Git objects. Scan every reachable Git blob and commit/tag object, not only unreachable objects.

- [ ] **Step 8: Replace root artifacts and regenerate checksums**

Copy the verified onedir executable/runtime, installer, and source archive into the established root handoff layout. Generate `SHA256SUMS.txt` from those three visible artifacts, then independently recalculate each SHA-256 and require exact equality.

- [ ] **Step 9: Verify repository content and handoff layout**

Run:

```powershell
git diff --check
git status --short
Get-FileHash InterviewAssistant.exe, InterviewAssistant-Setup-0.1.0-win64.exe, InterviewAssistant-source-0.1.0.zip -Algorithm SHA256
```

Expected: no unexpected source changes or untracked files, no source bytecode/build caches, and the three hashes match `SHA256SUMS.txt`. The root EXE remains documented as requiring the adjacent `_internal` directory.

- [ ] **Step 10: Commit release metadata and generated handoff changes**

Stage only reviewed source, documentation, and intended tracked release metadata. Do not commit secrets, temporary diagnostics, caches, or installer smoke directories.

```powershell
git add SHA256SUMS.txt
git commit -m "release: record rebuilt interview assistant artifacts"
```

If the large root binaries are intentionally ignored rather than tracked, leave them in the verified handoff layout and record their fresh hashes in the final response instead of forcing them into Git.
