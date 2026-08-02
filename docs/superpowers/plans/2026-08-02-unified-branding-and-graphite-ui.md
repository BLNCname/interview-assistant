# Unified Branding and Graphite UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship an instructor-ready `0.1.1` Windows release whose application, Ribbon, Settings window, executable, shortcuts, and installer consistently use the approved transparent liquid-glass logo and A1 Graphite Glass interface.

**Architecture:** Introduce one focused Qt theme module, retain `SettingsWindow` as the owner of its existing controls while reorganizing them into sidebar-selected stacked pages, and make the Ribbon consume the same semantic palette without changing capture or interaction behavior. Generate branding deterministically from the approved PNG, keep PyInstaller and Inno Setup as consumers of one multi-frame ICO, and preserve the strict distribution inventory/release validation boundary.

**Tech Stack:** Python 3.11/3.12, PyQt6, Pillow, pytest/pytest-qt, Ruff, PowerShell, uv, PyInstaller 6, Inno Setup 6, Windows DWM, CUDA 12 user-space runtime, faster-whisper `large-v3-turbo`.

## Global Constraints

- The approved visual direction is A1 Graphite Glass with sidebar pages.
- The UI language remains English; no localization switch is introduced.
- Graphite is the dominant surface color and `#50DE73` is the restrained brand accent.
- Retain native Windows title-bar controls; do not implement custom frameless Settings chrome.
- Preserve Ribbon click-through, edit mode, resize/move, temporary hide, display affinity, Markdown, and capture behavior.
- Preserve all seven hotkeys and their existing live-apply, persistence, rollback, and readiness semantics.
- Preserve readiness start gating; no warning or error may silently enable `Start`.
- Preserve Windows Credential Manager storage; never write the LM Studio token into source, config, logs, tests, or release artifacts.
- The installer contains the application, Python/native dependencies, CUDA/cuDNN user-space libraries, and the pinned STT model; it does not contain LM Studio, LM Link, an LLM, MCP servers, API tokens, or GPU drivers.
- The new release version is exactly `0.1.1` and the final root artifact is `InterviewAssistant-Setup-0.1.1-win64.exe`.
- Keep only source, the single root installer, and its one-line checksum manifest after release verification.
- Do not terminate LM Studio automatically. If it still owns files under `dist`, ask the user to close it before destructive cleanup.

## File Structure

### New files

- `interview_assistant/ui/theme.py` — semantic Graphite Glass palette, application QSS, and best-effort native dark-title-bar helper.
- `scripts/generate_brand_assets.py` — deterministic transparent PNG and multi-frame ICO generation/validation.
- `scripts/generate_release_inventory.py` — explicit, deterministic strict inventory generation for a reviewed release tree.
- `tests/unit/test_theme.py` — theme token, QSS, and DWM fallback tests.
- `docs/validation/release-verification-2026-08-02-v0.1.1.md` — evidence for source, packaged application, icon resources, installer, and cleanup.

### Modified files

- `assets/branding/interview-assistant-logo.png` — canonical transparent logo generated from the approved cutout.
- `assets/branding/interview-assistant.ico` — canonical 16–256 pixel Windows icon.
- `main.py` — apply the shared application stylesheet in addition to the application icon.
- `interview_assistant/ui/settings.py` — English copy, sidebar navigation, stacked pages, persistent footer, object names, and dark-title-bar application.
- `interview_assistant/ui/overlay.py` — shared palette consumption, English edit label, and compact header logo.
- `tests/unit/test_main.py` — application theme contract.
- `tests/unit/test_settings.py` — A1 structure, page switching, English copy, value preservation, readiness, and style contracts.
- `tests/unit/test_overlay.py` — shared palette/logo and behavioral regression contracts.
- `tests/unit/test_task16_packaging.py` — transparent branding and ICO-frame contracts.
- `tests/unit/test_release_packaging.py` — versioned installer-only handoff and inventory-generator contracts.
- `tests/unit/test_package.py` — package version contract.
- `pyproject.toml`, `interview_assistant/__init__.py`, `packaging/version_info.txt`, `packaging/interview_assistant.iss`, `scripts/build_installer.ps1`, `scripts/create_source_archive.ps1` — `0.1.1` version surfaces.
- `packaging/dist_inventory.json` — reviewed inventory of the new frozen distribution.
- `README.md`, `START_HERE.md`, `SHA256SUMS.txt` — final `0.1.1` instructor handoff.

---

### Task 1: Deterministic Transparent Branding Assets

**Files:**
- Create: `scripts/generate_brand_assets.py`
- Modify: `assets/branding/interview-assistant-logo.png`
- Modify: `assets/branding/interview-assistant.ico`
- Modify: `tests/unit/test_task16_packaging.py`

**Interfaces:**
- Consumes: `assets/branding/interview-assistant-logo-cutout.png` as the approved RGBA source.
- Produces: `FRAME_SIZES: tuple[int, ...]`, `build_brand_assets(source: Path, png_output: Path, ico_output: Path) -> None`, and canonical PNG/ICO files consumed by Qt, PyInstaller, and Inno Setup.

- [ ] **Step 1: Recreate the locked development environment**

Run:

```powershell
uv lock --check
uv sync --extra dev --extra cuda --frozen
```

Expected: exit 0 and `.venv\Scripts\python.exe` exists.

- [ ] **Step 2: Write failing branding-generation tests**

Add a temporary-output test and strengthen the release asset contract:

```python
from scripts.generate_brand_assets import FRAME_SIZES, build_brand_assets

BRAND_CUTOUT_PATH = ROOT / "assets" / "branding" / "interview-assistant-logo-cutout.png"

def test_brand_generator_preserves_transparency_and_writes_all_ico_frames(tmp_path) -> None:
    png_output = tmp_path / "logo.png"
    ico_output = tmp_path / "logo.ico"

    build_brand_assets(BRAND_CUTOUT_PATH, png_output, ico_output)

    with Image.open(png_output) as logo:
        assert logo.mode == "RGBA"
        assert logo.size == (935, 935)
        assert logo.getchannel("A").getextrema() == (0, 255)
        assert all(
            logo.getchannel("A").getpixel(point) == 0
            for point in ((0, 0), (934, 0), (0, 934), (934, 934))
        )
    with Image.open(ico_output) as icon:
        assert set(icon.ico.sizes()) == {(size, size) for size in FRAME_SIZES}


def test_liquid_glass_branding_assets_are_release_ready() -> None:
    with Image.open(BRAND_PNG_PATH) as logo:
        assert logo.mode == "RGBA"
        assert logo.width == logo.height == 935
        assert logo.getchannel("A").getextrema() == (0, 255)
    with Image.open(BRAND_ICO_PATH) as icon:
        assert set(icon.ico.sizes()) == {
            (16, 16), (24, 24), (32, 32), (48, 48),
            (64, 64), (128, 128), (256, 256),
        }
```

- [ ] **Step 3: Run the tests to verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_task16_packaging.py::test_brand_generator_preserves_transparency_and_writes_all_ico_frames tests\unit\test_task16_packaging.py::test_liquid_glass_branding_assets_are_release_ready -q
```

Expected: FAIL because `scripts.generate_brand_assets` does not exist and the current canonical PNG still has an opaque black background.

- [ ] **Step 4: Implement the deterministic generator**

Create the exact public boundary and fail-closed validation:

```python
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


FRAME_SIZES = (16, 24, 32, 48, 64, 128, 256)


def build_brand_assets(source: Path, png_output: Path, ico_output: Path) -> None:
    with Image.open(source) as loaded:
        logo = loaded.convert("RGBA")
    if logo.width != logo.height or logo.width < 512:
        raise ValueError("Brand source must be a square image of at least 512 pixels")
    alpha = logo.getchannel("A")
    if alpha.getextrema() != (0, 255):
        raise ValueError("Brand source must contain transparent and opaque pixels")
    corners = ((0, 0), (logo.width - 1, 0), (0, logo.height - 1), (logo.width - 1, logo.height - 1))
    if any(alpha.getpixel(point) != 0 for point in corners):
        raise ValueError("Brand source corners must be transparent")

    png_output.parent.mkdir(parents=True, exist_ok=True)
    ico_output.parent.mkdir(parents=True, exist_ok=True)
    logo.save(png_output, format="PNG", optimize=True)
    logo.save(ico_output, format="ICO", sizes=[(size, size) for size in FRAME_SIZES])
```

Add CLI arguments `--source`, `--png-output`, and `--ico-output`, each typed as `Path`, and call `build_brand_assets` from `main()`.

- [ ] **Step 5: Generate the checked-in assets**

Run:

```powershell
.\.venv\Scripts\python.exe .\scripts\generate_brand_assets.py `
  --source .\assets\branding\interview-assistant-logo-cutout.png `
  --png-output .\assets\branding\interview-assistant-logo.png `
  --ico-output .\assets\branding\interview-assistant.ico
```

Expected: exit 0; the PNG is RGBA and the ICO contains all seven sizes.

- [ ] **Step 6: Run GREEN and lint**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_task16_packaging.py -q
.\.venv\Scripts\python.exe -m ruff check scripts\generate_brand_assets.py tests\unit\test_task16_packaging.py
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 7: Commit**

```powershell
git add scripts/generate_brand_assets.py assets/branding/interview-assistant-logo.png assets/branding/interview-assistant.ico tests/unit/test_task16_packaging.py
git commit -m "assets: generate transparent Windows branding"
```

---

### Task 2: Shared Graphite Glass Theme and Native Dark Title Bar

**Files:**
- Create: `interview_assistant/ui/theme.py`
- Create: `tests/unit/test_theme.py`
- Modify: `main.py`
- Modify: `tests/unit/test_main.py`

**Interfaces:**
- Consumes: a live `QWidget` and the existing `QApplication` initialization in `main.main`.
- Produces: `GraphitePalette`, `GRAPHITE`, `graphite_stylesheet() -> str`, and `apply_native_dark_title_bar(widget: QWidget) -> bool`.

- [ ] **Step 1: Write failing theme tests**

Create `tests/unit/test_theme.py` with explicit semantic-token and safe-fallback contracts:

```python
import interview_assistant.ui.theme as theme
from PyQt6.QtWidgets import QWidget

from interview_assistant.ui.theme import GRAPHITE, apply_native_dark_title_bar, graphite_stylesheet


def test_graphite_theme_exposes_approved_brand_tokens() -> None:
    assert GRAPHITE.accent == "#50DE73"
    assert GRAPHITE.surface_top == (52, 54, 58)
    assert GRAPHITE.surface_bottom == (37, 38, 42)
    assert GRAPHITE.text_primary == "#F8FAF9"


def test_application_stylesheet_covers_controls_and_semantic_states() -> None:
    source = graphite_stylesheet()
    for selector in ("QMainWindow", "QListWidget", "QComboBox", "QLineEdit", "QPushButton", "QTableWidget", "QScrollBar"):
        assert selector in source
    assert "#50DE73" in source
    assert '[readinessStatus="warning"]' in source
    assert '[readinessStatus="error"]' in source


def test_dark_title_bar_is_a_safe_noop_off_windows(monkeypatch, qtbot) -> None:
    widget = QWidget()
    qtbot.addWidget(widget)
    monkeypatch.setattr(theme.sys, "platform", "linux")
    assert not apply_native_dark_title_bar(widget)
```

Extend the fake QApplication in `tests/unit/test_main.py` with `setStyleSheet`, then assert that `main()` applies exactly `graphite_stylesheet()`.

- [ ] **Step 2: Run RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_theme.py tests\unit\test_main.py::test_main_applies_project_branding_to_qapplication -q
```

Expected: FAIL because `interview_assistant.ui.theme` and stylesheet application do not exist.

- [ ] **Step 3: Implement the theme boundary**

Create a frozen dataclass and application stylesheet:

```python
@dataclass(frozen=True, slots=True)
class GraphitePalette:
    accent: str = "#50DE73"
    surface_top: tuple[int, int, int] = (52, 54, 58)
    surface_bottom: tuple[int, int, int] = (37, 38, 42)
    surface_deep: str = "#171A19"
    text_primary: str = "#F8FAF9"
    text_secondary: str = "#AEB8B2"
    warning: str = "#F2C66D"
    error: str = "#FB7185"


GRAPHITE = GraphitePalette()


def graphite_stylesheet() -> str:
    return f"""
    QMainWindow, QWidget#settingsRoot {{ color: {GRAPHITE.text_primary}; background-color: {GRAPHITE.surface_deep}; }}
    QListWidget#settingsNavigation {{ background: #121514; border: 0; border-right: 1px solid rgba(255,255,255,24); padding: 8px; }}
    QListWidget#settingsNavigation::item {{ color: {GRAPHITE.text_secondary}; padding: 9px 10px; border-radius: 7px; }}
    QListWidget#settingsNavigation::item:selected {{ color: {GRAPHITE.text_primary}; background: rgba(80,222,115,28); border-left: 2px solid {GRAPHITE.accent}; }}
    QPushButton#startButton[readyToStart="true"] {{ background: {GRAPHITE.accent}; color: #07110A; border: 0; }}
    QLabel[readinessStatus="warning"] {{ color: {GRAPHITE.warning}; }}
    QLabel[readinessStatus="error"] {{ color: {GRAPHITE.error}; }}
    """
```

Complete the QSS with the selectors asserted by the test, visible keyboard focus, disabled states, inputs, tables, headers, and narrow scrollbars. Implement `apply_native_dark_title_bar` as a best-effort Windows-only call to `dwmapi.DwmSetWindowAttribute`, trying attribute 20 and then 19, returning `False` on any unavailable API or nonzero result rather than raising.

- [ ] **Step 4: Apply the stylesheet in `main.main`**

Immediately after constructing `QApplication` and setting its icon:

```python
qt_app.setStyleSheet(graphite_stylesheet())
```

Do not move controller creation or event-loop ownership.

- [ ] **Step 5: Run GREEN and the startup regression tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_theme.py tests\unit\test_main.py -q
.\.venv\Scripts\python.exe -m ruff check interview_assistant\ui\theme.py main.py tests\unit\test_theme.py tests\unit\test_main.py
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 6: Commit**

```powershell
git add interview_assistant/ui/theme.py main.py tests/unit/test_theme.py tests/unit/test_main.py
git commit -m "feat: add shared graphite application theme"
```

---

### Task 3: A1 Settings Sidebar and English Information Architecture

**Files:**
- Modify: `interview_assistant/ui/settings.py`
- Modify: `tests/unit/test_settings.py`

**Interfaces:**
- Consumes: existing `SettingsBinding`, `SettingsChoice`, all existing control attributes, `HOTKEY_COPY`, and `GRAPHITE` theme behavior.
- Produces: `SETTINGS_PAGE_TITLES`, `navigation_list: QListWidget`, `page_stack: QStackedWidget`, six stable page widgets, and the unchanged public save/readiness/hotkey interfaces.

- [ ] **Step 1: Write failing structure and English-copy tests**

Add contracts that preserve existing widget identities:

```python
def test_settings_uses_approved_a1_sidebar_pages(qtbot, tmp_path) -> None:
    window, _, _, _ = _window(qtbot, tmp_path)
    assert [window.navigation_list.item(index).text() for index in range(window.navigation_list.count())] == [
        "General", "Models", "Audio", "Hotkeys", "Appearance", "Diagnostics"
    ]
    assert window.page_stack.count() == 6
    assert window.page_stack.currentWidget() is window.general_page


def test_settings_page_switching_preserves_unsaved_values(qtbot, tmp_path) -> None:
    window, _, _, _ = _window(qtbot, tmp_path)
    window.text_model_combo.setCurrentIndex(window.text_model_combo.findData("gemma"))
    window.navigation_list.setCurrentRow(3)
    window.navigation_list.setCurrentRow(1)
    assert window.page_stack.currentWidget() is window.models_page
    assert window.text_model_combo.currentData() == "gemma"


def test_settings_hotkey_copy_is_fully_english(qtbot, tmp_path) -> None:
    window, _, _, _ = _window(qtbot, tmp_path)
    assert window.hotkey_labels[HotkeyAction.FORCE_REQUEST].text() == "Submit conversation context"
    assert "latest instructor turn" in window.hotkey_help[HotkeyAction.FORCE_REQUEST].text().casefold()
    assert window.hotkey_labels[HotkeyAction.SCREENSHOT].text() == "Capture next screenshot"
    assert all(not re.search(r"[А-Яа-яЁё]", label.text()) for label in window.hotkey_labels.values())
```

Add a hidden-page refresh test that calls `set_model_choices` and `set_audio_choices` while another page is selected and asserts the original combo instances receive the new entries.

- [ ] **Step 2: Run RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_settings.py -q
```

Expected: the new tests FAIL because the sidebar and page stack do not exist and hotkey copy is Russian.

- [ ] **Step 3: Define stable navigation and English hotkey copy**

Add:

```python
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
```

- [ ] **Step 4: Recompose `_build_ui` without recreating control behavior**

Use one horizontal content row with a sidebar and stack, then the existing action row below it:

```python
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
    self.general_page, self.models_page, self.audio_page,
    self.hotkeys_page, self.appearance_page, self.diagnostics_page,
):
    self.page_stack.addWidget(page)
self.navigation_list.currentRowChanged.connect(self.page_stack.setCurrentIndex)
self.navigation_list.setCurrentRow(0)
```

Move the existing widget construction into the six focused `_build_*_page` methods. Keep the attribute names `system_device_combo`, `microphone_device_combo`, `language_combo`, `text_model_combo`, `vision_model_combo`, `shared_instance_label`, `search_mode_combo`, `opacity_spin`, `max_height_spin`, `token_edit`, `hotkey_edits`, `readiness_status_label`, `notification_label`, and `readiness_table` unchanged so binding and tests keep the same references.

- [ ] **Step 5: Keep the footer persistent and semantics unchanged**

Place the existing `save_button`, `readiness_button`, and `start_button` after the sidebar/stack row in the root layout. Retain:

```python
self.save_button.clicked.connect(self.save)
self.readiness_button.clicked.connect(self.save)
self.start_button.clicked.connect(self._request_start)
```

Do not replace readiness invalidation, `_set_start_available`, or button tooltips.

- [ ] **Step 6: Run GREEN and the full Settings test module**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_settings.py -q
.\.venv\Scripts\python.exe -m ruff check interview_assistant\ui\settings.py tests\unit\test_settings.py
git diff --check
```

Expected: all Settings tests pass, including rollback, geometry, token, dynamic-choice, notification, and readiness tests.

- [ ] **Step 7: Commit**

```powershell
git add interview_assistant/ui/settings.py tests/unit/test_settings.py
git commit -m "feat: reorganize settings into graphite sidebar pages"
```

---

### Task 4: Settings Styling, Accessibility, and Readiness Presentation

**Files:**
- Modify: `interview_assistant/ui/settings.py`
- Modify: `interview_assistant/ui/theme.py`
- Modify: `tests/unit/test_settings.py`
- Modify: `tests/unit/test_theme.py`

**Interfaces:**
- Consumes: `graphite_stylesheet`, `apply_native_dark_title_bar`, the Task 3 page widgets, and `ReadinessReport`.
- Produces: stable Qt object names/properties for visual states and a safe `SettingsWindow.showEvent` dark-title-bar hook.

- [ ] **Step 1: Write failing style and accessibility tests**

Add:

```python
def test_settings_exposes_graphite_object_names_and_persistent_actions(qtbot, tmp_path) -> None:
    window, _, _, _ = _window(qtbot, tmp_path)
    assert window.centralWidget().objectName() == "settingsRoot"
    assert window.navigation_list.objectName() == "settingsNavigation"
    assert window.page_stack.objectName() == "settingsPages"
    assert window.save_button.parentWidget() is window.centralWidget()
    assert window.readiness_button.parentWidget() is window.centralWidget()
    assert window.start_button.parentWidget() is window.centralWidget()


def test_readiness_rows_receive_semantic_status_metadata(qtbot, tmp_path) -> None:
    window, _, _, _ = _window(qtbot, tmp_path)
    window.set_readiness_report(_startable_warning_report())
    statuses = {
        window.readiness_table.item(row, 1).data(Qt.ItemDataRole.UserRole)
        for row in range(window.readiness_table.rowCount())
    }
    assert statuses == {"ready", "warning"}
```

Add a monkeypatched `apply_native_dark_title_bar` test that shows the window and asserts the helper is called once without affecting visibility or geometry.

- [ ] **Step 2: Run RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_settings.py tests\unit\test_theme.py -q
```

Expected: new assertions FAIL because object names, readiness metadata, and the show hook are absent.

- [ ] **Step 3: Apply stable object names and page-card hierarchy**

Set:

```python
central.setObjectName("settingsRoot")
self.page_stack.setObjectName("settingsPages")
self.save_button.setObjectName("secondaryButton")
self.readiness_button.setObjectName("secondaryButton")
self.start_button.setObjectName("startButton")
```

Give page titles the `settingsPageTitle` object name and bounded form sections the `settingsCard` object name. Do not nest decorative cards or hide explanatory labels below 11 px.

- [ ] **Step 4: Store readiness semantics and keep full detail**

When populating each readiness row, keep the five text columns exactly as today and additionally store the normalized status:

```python
status_item.setData(Qt.ItemDataRole.UserRole, check.status)
status_item.setForeground(QColor(status_color(check.status)))
```

Implement `status_color(status: str) -> str` in `theme.py` with `ready -> #50DE73`, `warning -> #F2C66D`, `error -> #FB7185`, and a primary-text fallback. Do not convert warnings into errors or alter `report.start_allowed`.

- [ ] **Step 5: Apply the native dark title bar safely**

Override `showEvent` only to call the helper after the native handle exists:

```python
def showEvent(self, event: QShowEvent | None) -> None:
    super().showEvent(event)
    apply_native_dark_title_bar(self)
```

No exception from the helper may escape into application startup.

- [ ] **Step 6: Run GREEN and inspect focus traversal**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_settings.py tests\unit\test_theme.py -q
.\.venv\Scripts\python.exe -m ruff check interview_assistant\ui\settings.py interview_assistant\ui\theme.py tests\unit\test_settings.py tests\unit\test_theme.py
git diff --check
```

Then launch a source smoke window and use Tab/Shift+Tab to confirm sidebar, fields, hotkey editors, and footer buttons all receive visible focus without trapping the keyboard.

- [ ] **Step 7: Commit**

```powershell
git add interview_assistant/ui/settings.py interview_assistant/ui/theme.py tests/unit/test_settings.py tests/unit/test_theme.py
git commit -m "feat: style settings with accessible graphite states"
```

---

### Task 5: Unified Ribbon Branding Without Behavior Regressions

**Files:**
- Modify: `interview_assistant/ui/overlay.py`
- Modify: `tests/unit/test_overlay.py`

**Interfaces:**
- Consumes: `GRAPHITE`, the QApplication window icon set by `main`, and all existing Ribbon state/event interfaces.
- Produces: `brand_icon_label: QLabel` and a Ribbon that uses the shared palette while preserving its existing public API.

- [ ] **Step 1: Write failing Ribbon theme tests**

Add:

```python
BRAND_ICO_PATH = Path(__file__).parents[2] / "assets" / "branding" / "interview-assistant.ico"


def test_ribbon_uses_shared_graphite_palette_and_english_edit_copy(qtbot) -> None:
    ribbon = LiquidRibbon(EventBus(), settings=None)
    qtbot.addWidget(ribbon)
    assert ribbon.surface.gradient_top_rgb == GRAPHITE.surface_top
    assert ribbon.surface.gradient_bottom_rgb == GRAPHITE.surface_bottom
    assert ribbon.edit_mode_label.text() == "Edit mode"
    assert "#50DE73" in ribbon.edit_mode_label.styleSheet()


def test_ribbon_header_uses_application_icon_when_available(qtbot) -> None:
    QApplication.instance().setWindowIcon(QIcon(str(BRAND_ICO_PATH)))
    ribbon = LiquidRibbon(EventBus(), settings=None)
    qtbot.addWidget(ribbon)
    assert not ribbon.brand_icon_label.pixmap().isNull()
    assert ribbon.brand_icon_label.maximumSize() == QSize(20, 20)
```

Retain all existing capture, mask, edit-mode, click-through, Markdown, scroll, and geometry tests unchanged.

- [ ] **Step 2: Run RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_overlay.py::test_ribbon_uses_shared_graphite_palette_and_english_edit_copy tests\unit\test_overlay.py::test_ribbon_header_uses_application_icon_when_available -q
```

Expected: FAIL because the Ribbon still owns literal palette values, has Russian edit copy, and has no brand label.

- [ ] **Step 3: Consume the shared palette and neutralize the model chip**

Replace local graphite surface constants with aliases from `GRAPHITE`; change the blue model-chip RGB to the approved accent treatment:

```python
_SURFACE_TOP_RGB = GRAPHITE.surface_top
_SURFACE_BOTTOM_RGB = GRAPHITE.surface_bottom
_MODEL_CHIP_RGB: _RGB = (80, 222, 115)
_MODEL_CHIP_ALPHA = 12
```

Set edit copy to `Edit mode`. Preserve the existing contrast functions and minimum effective opacity.

- [ ] **Step 4: Add the compact header mark with a safe empty-icon fallback**

Import `QPixmap` from `PyQt6.QtGui`, then add the label before the status dot:

```python
self.brand_icon_label = QLabel(self.header_widget)
self.brand_icon_label.setMaximumSize(20, 20)
app = QApplication.instance()
pixmap = app.windowIcon().pixmap(20, 20) if app is not None else QPixmap()
self.brand_icon_label.setPixmap(pixmap)
self.brand_icon_label.setVisible(not pixmap.isNull())
header_layout.addWidget(self.brand_icon_label)
```

Do not enlarge the Ribbon minimum width; the logo must yield space before status/model text does.

- [ ] **Step 5: Run the complete Ribbon regression suite**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_overlay.py -q
.\.venv\Scripts\python.exe -m ruff check interview_assistant\ui\overlay.py tests\unit\test_overlay.py
git diff --check
```

Expected: all Ribbon tests pass, including click-through, capture affinity, edit transitions, compact sizing, Markdown, and long-token bounds.

- [ ] **Step 6: Commit**

```powershell
git add interview_assistant/ui/overlay.py tests/unit/test_overlay.py
git commit -m "feat: unify ribbon with graphite branding"
```

---

### Task 6: Patch Version 0.1.1 Across Source and Build Metadata

**Files:**
- Modify: `pyproject.toml`
- Modify: `interview_assistant/__init__.py`
- Modify: `packaging/version_info.txt`
- Modify: `packaging/interview_assistant.iss`
- Modify: `scripts/build_installer.ps1`
- Modify: `scripts/create_source_archive.ps1`
- Modify: `tests/unit/test_package.py`
- Modify: `tests/unit/test_release_packaging.py`

**Interfaces:**
- Consumes: the approved version `0.1.1`.
- Produces: a consistent package version, Windows file/product version, Inno default, and script defaults.

- [ ] **Step 1: Write failing version-consistency tests**

Update the package assertion and add a single-source consistency contract:

```python
def test_package_version() -> None:
    assert interview_assistant.__version__ == "0.1.1"


def test_release_version_defaults_are_consistent() -> None:
    assert 'version = "0.1.1"' in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "StringStruct(u'FileVersion', u'0.1.1')" in (ROOT / "packaging/version_info.txt").read_text(encoding="utf-8")
    assert '#define AppVersion "0.1.1"' in ISS_PATH.read_text(encoding="utf-8")
    assert '[string]$Version = "0.1.1"' in INSTALLER_SCRIPT_PATH.read_text(encoding="utf-8")
    assert '[string]$Version = "0.1.1"' in ARCHIVE_SCRIPT_PATH.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_package.py tests\unit\test_release_packaging.py::test_release_version_defaults_are_consistent -q
```

Expected: FAIL on every current `0.1.0` source/default.

- [ ] **Step 3: Update exactly the authoritative source/default surfaces**

Change package, PE, ISS, and script defaults to `0.1.1`. Do not yet change `README.md`, `START_HERE.md`, or `SHA256SUMS.txt`; those handoff files remain authoritative for the existing root installer until Task 8 produces and hashes the new binary.

- [ ] **Step 4: Refresh the lock metadata and run GREEN**

Run:

```powershell
uv lock
uv lock --check
.\.venv\Scripts\python.exe -m pytest tests\unit\test_package.py tests\unit\test_release_packaging.py::test_release_version_defaults_are_consistent -q
.\.venv\Scripts\python.exe -m ruff check tests\unit\test_package.py tests\unit\test_release_packaging.py
git diff --check
```

Expected: all commands exit 0; dependency versions remain unchanged unless uv records the local package version.

- [ ] **Step 5: Commit**

```powershell
git add pyproject.toml uv.lock interview_assistant/__init__.py packaging/version_info.txt packaging/interview_assistant.iss scripts/build_installer.ps1 scripts/create_source_archive.ps1 tests/unit/test_package.py tests/unit/test_release_packaging.py
git commit -m "chore: bump graphite release to 0.1.1"
```

---

### Task 7: Explicit Reviewed Distribution Inventory Generator

**Files:**
- Create: `scripts/generate_release_inventory.py`
- Modify: `tests/unit/test_release_packaging.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: a completed `dist/InterviewAssistant` directory.
- Produces: `build_inventory(dist: Path) -> dict[str, object]` and an explicitly requested JSON output compatible with `scripts/validate_release_dist.py`.

- [ ] **Step 1: Write failing generator tests**

Build a small fake onedir tree and require normalized ordering and hashes:

```python
def test_inventory_generator_hashes_sorted_safe_relative_files(tmp_path) -> None:
    dist = tmp_path / "InterviewAssistant"
    (dist / "_internal").mkdir(parents=True)
    (dist / "InterviewAssistant.exe").write_bytes(b"exe")
    (dist / "_internal" / "runtime.dll").write_bytes(b"runtime")

    inventory = build_inventory(dist)

    assert inventory["schema_version"] == 1
    assert inventory["application"] == "InterviewAssistant"
    assert [entry["path"] for entry in inventory["files"]] == [
        "_internal/runtime.dll",
        "InterviewAssistant.exe",
    ]
    assert inventory["files"][0]["sha256"] == hashlib.sha256(b"runtime").hexdigest()


def test_inventory_generator_rejects_symlinks_and_wrong_top_level(tmp_path) -> None:
    with pytest.raises(ValueError):
        build_inventory(tmp_path / "not-InterviewAssistant")
```

Add a CLI contract requiring `--dist`, `--output`, and exact `--application InterviewAssistant`; refuse an output path inside the distribution.

- [ ] **Step 2: Run RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_release_packaging.py -k inventory_generator -q
```

Expected: FAIL because `scripts.generate_release_inventory` does not exist.

- [ ] **Step 3: Implement deterministic, fail-closed inventory generation**

Implement:

```python
def build_inventory(dist: Path) -> dict[str, object]:
    resolved = dist.resolve(strict=True)
    if resolved.name != "InterviewAssistant":
        raise ValueError("Distribution root must be named InterviewAssistant")
    entries: list[dict[str, object]] = []
    for path in sorted(resolved.rglob("*"), key=lambda item: item.relative_to(resolved).as_posix().casefold()):
        if path.is_symlink():
            raise ValueError("Distribution inventory refuses symbolic links")
        if path.is_file():
            relative = path.relative_to(resolved).as_posix()
            entries.append({"path": relative, "size": path.stat().st_size, "sha256": digest(path)})
    if not entries or not (resolved / "InterviewAssistant.exe").is_file():
        raise ValueError("Distribution is incomplete")
    return {"schema_version": 1, "application": "InterviewAssistant", "files": entries}
```

Write JSON as UTF-8 with stable indentation and a trailing newline. Refuse to overwrite an existing output unless the CLI receives `--replace`; require `--application InterviewAssistant` so generation is never an implicit build side effect.

- [ ] **Step 4: Document the review boundary**

In the installer-build section of `README.md`, add the exact regeneration command and state that it is valid only after source tests, frozen diagnostics, bundled model validation, and a human review of the inventory diff:

```powershell
.\.venv\Scripts\python.exe .\scripts\generate_release_inventory.py `
  --dist .\dist\InterviewAssistant `
  --output .\packaging\dist_inventory.json `
  --application InterviewAssistant `
  --replace
```

Keep the warning that the inventory must never be silently regenerated merely to bypass validation.

- [ ] **Step 5: Run GREEN and validator regression tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit\test_release_packaging.py -q
.\.venv\Scripts\python.exe -m ruff check scripts\generate_release_inventory.py tests\unit\test_release_packaging.py
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 6: Commit**

```powershell
git add scripts/generate_release_inventory.py tests/unit/test_release_packaging.py README.md
git commit -m "build: add reviewed release inventory generator"
```

---

### Task 8: Full Verification, Application Build, Installer 0.1.1, and Clean Handoff

**Files:**
- Modify: `packaging/dist_inventory.json`
- Modify: `README.md`
- Modify: `START_HERE.md`
- Modify: `SHA256SUMS.txt`
- Modify: `tests/unit/test_release_packaging.py`
- Create: `docs/validation/release-verification-2026-08-02-v0.1.1.md`
- Delete after verification: root `InterviewAssistant-Setup-0.1.0-win64.exe`
- Delete after verification: `dist/`, `build/`, `.venv/`, `models/`, `.superpowers/`, Python caches, pytest cache, and Ruff cache
- Produce: root `InterviewAssistant-Setup-0.1.1-win64.exe`

**Interfaces:**
- Consumes: all prior tasks, `packaging/stt_model_manifest.json`, `packaging/source_release_config.yaml`, Inno Setup 6, the NVIDIA runtime, and the approved installer-only handoff policy.
- Produces: verified `0.1.1` application/installer evidence, reviewed inventory, one root installer, and one authoritative SHA-256 line.

- [ ] **Step 1: Run the complete source gate before packaging**

Run:

```powershell
uv lock --check
uv sync --extra dev --extra cuda --frozen
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check . --exclude dist --exclude build
git diff --check
```

Expected: every command exits 0. Record the exact test count and elapsed time in the release report.

- [ ] **Step 2: Obtain and validate the pinned STT bundle**

Run:

```powershell
$ModelPath = Join-Path $PWD 'models\stt\large-v3-turbo'
.\.venv\Scripts\hf.exe download dropbox-dash/faster-whisper-large-v3-turbo `
  --revision 0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf `
  --local-dir $ModelPath
.\.venv\Scripts\python.exe -m interview_assistant.stt.bundle `
  --validate-bundle $ModelPath `
  --require-all-files
```

Expected: exact six-file manifest validation succeeds. Do not accept a moving revision or substitute model.

- [ ] **Step 3: Remove or release stale build output safely**

Resolve every deletion target beneath the workspace before deletion. Attempt to remove the old `dist` and `build` trees with native PowerShell/.NET operations. If the two known DLLs are still loaded, enumerate the exact owning process/module paths; ask the user to close LM Studio and retry. Do not terminate LM Studio automatically.

- [ ] **Step 4: Build and verify the frozen application with CUDA/STT**

Run:

```powershell
.\scripts\build.ps1 `
  -SttModelPath $ModelPath `
  -VerifyCuda `
  -ConfigPath .\packaging\source_release_config.yaml
```

Expected: PyInstaller exits 0, packaged headless diagnostics report `status=ok` and `frozen=true`, the bundled STT validator succeeds, CUDA exposes the NVIDIA device, and real bundled-model inference completes.

- [ ] **Step 5: Inspect GUI and extracted EXE icon**

Launch `dist\InterviewAssistant\InterviewAssistant.exe`, inspect Settings A1 and the Ribbon on the target Windows desktop, and verify:

- dark native Settings title bar and new application icon;
- sidebar order and switching;
- English labels and all seven hotkeys;
- persistent Save/Run checks/Start footer;
- readable diagnostics table and warning/error colors;
- Ribbon logo, graphite palette, Markdown, edit mode, hide/show, click-through, resize, and capture exclusion.

Extract the associated icon without altering the executable:

```powershell
Add-Type -AssemblyName System.Drawing
$icon = [System.Drawing.Icon]::ExtractAssociatedIcon((Resolve-Path '.\dist\InterviewAssistant\InterviewAssistant.exe').Path)
$icon.ToBitmap().Save((Join-Path $PWD 'build\exe-icon-smoke.png'))
$icon.Dispose()
```

Open the PNG and confirm the new rounded transparent logo is recognizable at the extracted Windows size.

- [ ] **Step 6: Generate and review the new strict inventory**

Run:

```powershell
.\.venv\Scripts\python.exe .\scripts\generate_release_inventory.py `
  --dist .\dist\InterviewAssistant `
  --output .\packaging\dist_inventory.json `
  --application InterviewAssistant `
  --replace
git diff --stat -- packaging\dist_inventory.json
git diff -- packaging\dist_inventory.json | Select-Object -First 240
.\.venv\Scripts\python.exe .\scripts\validate_release_dist.py `
  --dist .\dist\InterviewAssistant `
  --manifest .\packaging\stt_model_manifest.json `
  --inventory .\packaging\dist_inventory.json
```

Expected: inventory generation and validation exit 0. Review that changed paths are explained by the source/theme/icon/version delta and that no token, config, transcript, screenshot, cache, or unrelated model is present.

- [ ] **Step 7: Build installer 0.1.1**

Resolve Inno Setup and run:

```powershell
$Iscc = (Get-Command ISCC.exe -ErrorAction Stop).Source
.\scripts\build_installer.ps1 `
  -IsccPath $Iscc `
  -Version 0.1.1 `
  -DistPath .\dist\InterviewAssistant `
  -OutputPath .\dist\installer
```

Expected: `dist\installer\InterviewAssistant-Setup-0.1.1-win64.exe` exists and the builder validated the complete distribution/model inventory before invoking Inno Setup.

- [ ] **Step 8: Run isolated installer smoke and inspect installer icon**

Run:

```powershell
.\scripts\smoke_installer.ps1 `
  -InstallerPath .\dist\installer\InterviewAssistant-Setup-0.1.1-win64.exe `
  -ReportPath .\build\installer-smoke.json
```

Expected: install exit 0, installed inventory/model hashes validate, frozen diagnostics exit 0, uninstall exit 0, and the install tree is removed.

Extract and inspect the installer icon:

```powershell
Add-Type -AssemblyName System.Drawing
$icon = [System.Drawing.Icon]::ExtractAssociatedIcon((Resolve-Path '.\dist\installer\InterviewAssistant-Setup-0.1.1-win64.exe').Path)
$icon.ToBitmap().Save((Join-Path $PWD 'build\installer-icon-smoke.png'))
$icon.Dispose()
```

- [ ] **Step 9: Update the instructor handoff contracts only after the binary exists**

Change `README.md`, `START_HERE.md`, and `tests/unit/test_release_packaging.py` from the `0.1.0` installer name to `InterviewAssistant-Setup-0.1.1-win64.exe`. Copy the verified installer to the repository root, remove the old root installer, and write the real digest:

```powershell
Copy-Item -LiteralPath .\dist\installer\InterviewAssistant-Setup-0.1.1-win64.exe -Destination .\InterviewAssistant-Setup-0.1.1-win64.exe
$hash = (Get-FileHash .\InterviewAssistant-Setup-0.1.1-win64.exe -Algorithm SHA256).Hash
Set-Content -LiteralPath .\SHA256SUMS.txt -Encoding ascii -NoNewline -Value "$hash  InterviewAssistant-Setup-0.1.1-win64.exe`n"
```

The checksum test must require exactly:

```python
assert re.fullmatch(
    r"[0-9A-F]{64}  InterviewAssistant-Setup-0\.1\.1-win64\.exe",
    lines[0],
)
```

- [ ] **Step 10: Write the release verification report**

Create `docs/validation/release-verification-2026-08-02-v0.1.1.md` with:

- source commit and version;
- full pytest/Ruff counts;
- branding PNG/ICO validation and frame sizes;
- frozen diagnostics and CUDA/STT result;
- GUI A1/Ribbon observations;
- inventory file/directory totals;
- installer build and isolated smoke result;
- EXE and installer icon inspection result;
- installer byte size and SHA-256;
- statement that observed live interview/Teams acceptance remains an instructor/target-machine exercise.

- [ ] **Step 11: Run the final tracked-tree and handoff gates**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check . --exclude dist --exclude build --exclude models
git diff --check
$expected = ((Get-Content .\SHA256SUMS.txt -Raw).Trim() -split '\s+', 2)[0]
$actual = (Get-FileHash .\InterviewAssistant-Setup-0.1.1-win64.exe -Algorithm SHA256).Hash
if ($actual -ne $expected) { throw 'Installer SHA-256 does not match SHA256SUMS.txt' }
```

Expected: all tests and lint pass; hash comparison succeeds.

- [ ] **Step 12: Commit the reviewed release metadata**

```powershell
git add packaging/dist_inventory.json README.md START_HERE.md SHA256SUMS.txt tests/unit/test_release_packaging.py docs/validation/release-verification-2026-08-02-v0.1.1.md
git commit -m "release: prepare instructor installer 0.1.1"
```

The installer remains intentionally ignored and visible in the root folder.

- [ ] **Step 13: Remove generated workspace clutter and audit the final root**

After all verification evidence is recorded, stop the visual-companion process recorded under `.superpowers/brainstorm/*/state/server.pid`, verify that the PID is the Node `server.cjs` instance recorded for this workspace, and then safely remove only verified workspace descendants: `dist/`, `build/`, `.venv/`, `models/`, `.superpowers/`, `.pytest_cache/`, `.ruff_cache/`, `interview_assistant.egg-info/`, and every `__pycache__` directory. Preserve `.git`, source, tests, docs, assets, packaging definitions, the root installer, and `SHA256SUMS.txt`.

Run:

```powershell
git status --short --ignored
git clean -ndX
git grep -l -I -P 'sk-lm-[A-Za-z0-9_-]{4,}:[A-Za-z0-9_-]{8,}' -- .
Get-ChildItem -Force | Sort-Object Name | Select-Object Mode, Name
```

Expected:

- no tracked modifications;
- no credential-shaped LM Studio token matches;
- the only ignored release artifact is `InterviewAssistant-Setup-0.1.1-win64.exe`;
- no `dist`, `build`, `.venv`, `models`, portable EXE, `_internal`, source ZIP, or cache remains;
- the root installer hash still matches the single line in `SHA256SUMS.txt`.

---

## Final Review Checklist

- [ ] The approved cutout is the only source of the canonical PNG and ICO.
- [ ] The ICO contains exactly the required seven sizes and transparent corners.
- [ ] `main`, PyInstaller, Inno Setup, installed shortcuts, and Add or Remove Programs consume the same icon.
- [ ] Settings implements the six A1 pages without changing existing control identities or behavior.
- [ ] All Settings and Ribbon user-facing copy is English.
- [ ] The Graphite theme uses `#50DE73` only as a restrained semantic accent.
- [ ] Native title-bar theming is best effort and cannot prevent startup.
- [ ] All seven hotkeys and every readiness/capture behavior have regression coverage.
- [ ] Version `0.1.1` is consistent across Python, PE, ISS, and build-script surfaces.
- [ ] Inventory regeneration is explicit, reviewed, and validated before installer creation.
- [ ] The installer contains the pinned STT model and passes isolated install/uninstall smoke.
- [ ] The final root contains only source, the `0.1.1` installer, and its authoritative checksum.
