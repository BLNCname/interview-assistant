# Readiness Warning UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make warning-only readiness reports visibly startable while retaining every diagnostic warning and blocking required failures.

**Architecture:** Keep `ReadinessReport.can_start` as the authorization source and derive presentation only inside `SettingsWindow.set_readiness_report()`. A Qt dynamic property controls the graphite Start style; no probe, controller, or runtime behavior changes.

**Tech Stack:** Python 3.12, PyQt6, pytest, pytest-qt

## Global Constraints

- Start remains blocked only when `ReadinessReport.can_start` is false.
- Warning rows and remediation text remain unchanged.
- The enabled accent is graphite, never blue.
- No readiness status enum or production dependency is added.

---

### Task 1: Startable warning presentation

**Files:**
- Modify: `tests/unit/test_settings.py`
- Modify: `interview_assistant/ui/settings.py`

**Interfaces:**
- Consumes: `ReadinessReport.status: Literal["ready", "warning", "failed"]` and `ReadinessReport.can_start: bool`.
- Produces: `SettingsWindow.set_readiness_report(report)` with explicit summary copy, tooltip, enabled state, and `readyToStart` Qt property.

- [ ] **Step 1: Write the failing warning-only UI test**

Add a report containing one `ready` required result and two required `warning` results. Assert:

```python
window.set_readiness_report(report)

assert window.start_button.isEnabled()
assert window.start_button.property("readyToStart") is True
assert window.readiness_status_label.text() == (
    "Readiness: ready — 2 non-blocking warnings"
)
assert "ready to start" in window.start_button.toolTip().casefold()
assert "#343a40" in window.start_button.styleSheet().casefold()
```

- [ ] **Step 2: Extend the required-failure test**

After applying a required failed report, assert:

```python
assert not window.start_button.isEnabled()
assert window.start_button.property("readyToStart") is False
assert window.readiness_status_label.text() == (
    "Readiness: failed — 1 blocking check"
)
assert "blocking" in window.start_button.toolTip().casefold()
```

- [ ] **Step 3: Run the focused tests and verify RED**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\unit\test_settings.py -q
```

Expected: the new assertions fail because the current label is `Readiness: warning` and the Start button has no ready property or graphite style.

- [ ] **Step 4: Implement the minimal presentation state**

Give Start the `startButton` object name, a `readyToStart=False` property, a disabled tooltip, and this button-local stylesheet:

```css
QPushButton#startButton[readyToStart="true"] {
    background-color: #343A40;
    color: #FFFFFF;
    border: 1px solid #272B30;
    padding: 4px 14px;
}
QPushButton#startButton[readyToStart="true"]:hover { background-color: #41474E; }
QPushButton#startButton[readyToStart="true"]:pressed { background-color: #272B30; }
```

Add a private method with this interface:

```python
def _set_start_available(self, available: bool, tooltip: str) -> None:
    self.start_button.setEnabled(available)
    self.start_button.setProperty("readyToStart", available)
    self.start_button.setToolTip(tooltip)
    style = self.start_button.style()
    style.unpolish(self.start_button)
    style.polish(self.start_button)
```

Use it from `clear_readiness()` and `set_readiness_report()`. Count required failed results for blocked copy; for a startable warning report count results whose status is not `ready`.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests\unit\test_settings.py -q
```

Expected: all settings tests pass.

- [ ] **Step 6: Run static and regression checks**

Run:

```powershell
.venv\Scripts\python.exe -m ruff check interview_assistant\ui\settings.py tests\unit\test_settings.py
.venv\Scripts\python.exe -m pytest tests\unit\test_readiness.py tests\unit\test_app.py tests\unit\test_application_controller.py -q
```

Expected: both commands exit 0.

- [ ] **Step 7: Commit the implementation**

```powershell
git add -- interview_assistant/ui/settings.py tests/unit/test_settings.py
git commit -m "fix: clarify startable readiness warnings"
```

