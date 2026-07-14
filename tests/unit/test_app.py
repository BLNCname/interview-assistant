from __future__ import annotations

from interview_assistant.app import InterviewApplication
from interview_assistant.diagnostics.readiness import CheckResult, ReadinessReport
from interview_assistant.state import ApplicationState


def _report(status: str) -> ReadinessReport:
    return ReadinessReport(
        (
            CheckResult(
                name="display_affinity",
                status=status,  # type: ignore[arg-type]
                message="affinity checked",
                remediation="Enable capture exclusion",
                duration_ms=1.0,
                required=True,
            ),
        )
    )


def test_failed_readiness_then_ready_report_starts_without_poisoning_state(qtbot) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    notifications: list[str] = []
    app.events.notification.connect(notifications.append)
    app.set_readiness_report(_report("failed"))

    app.start()

    assert not app.can_start_session
    assert not app.ribbon.isVisible()
    assert app.states.state is ApplicationState.STARTING
    assert notifications == ["Readiness checks must pass before starting"]

    app.set_readiness_report(_report("ready"))
    app.start()
    qtbot.waitExposed(app.ribbon)

    assert app.can_start_session
    assert app.ribbon.isVisible()
    assert app.states.state is ApplicationState.READY
    app.shutdown()


def test_warning_readiness_report_permits_application_start(qtbot) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    app.set_readiness_report(_report("warning"))

    app.start()
    qtbot.waitExposed(app.ribbon)

    assert app.can_start_session
    assert app.ribbon.isVisible()
    assert app.states.state is ApplicationState.READY
    app.shutdown()


def test_legacy_application_without_report_remains_backward_compatible(qtbot) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)

    assert app.readiness_report is None
    assert app.can_start_session
    app.shutdown()


def test_application_without_report_is_fail_closed_by_default(qtbot) -> None:
    from PyQt6.QtWidgets import QApplication

    from interview_assistant.events import EventBus
    from interview_assistant.state import StateMachine
    from interview_assistant.ui.windows_affinity import (
        AffinityResult,
        WDA_EXCLUDEFROMCAPTURE,
    )

    qt_app = QApplication.instance()
    assert isinstance(qt_app, QApplication)
    app = InterviewApplication(
        qt_app,
        EventBus(),
        StateMachine(),
        overlay_settings=None,
        affinity_applier=lambda _hwnd: AffinityResult(
            True,
            WDA_EXCLUDEFROMCAPTURE,
            None,
        ),
    )
    qtbot.addWidget(app.ribbon)

    app.start()

    assert not app.can_start_session
    assert app.states.state is ApplicationState.STARTING
    assert not app.ribbon.isVisible()
    app.shutdown()
