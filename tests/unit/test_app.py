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


def test_failed_readiness_report_defensively_blocks_application_start(qtbot) -> None:
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    notifications: list[str] = []
    app.events.notification.connect(notifications.append)
    app.set_readiness_report(_report("failed"))

    app.start()

    assert not app.can_start_session
    assert not app.ribbon.isVisible()
    assert app.states.state is ApplicationState.OFFLINE
    assert notifications == ["Readiness checks must pass before starting"]
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
