from interview_assistant.state import ApplicationState, StateMachine


def test_valid_state_transition() -> None:
    machine = StateMachine()
    machine.transition(ApplicationState.READY)
    machine.transition(ApplicationState.LISTENING)
    assert machine.state is ApplicationState.LISTENING


def test_shutdown_is_idempotent(qapp) -> None:
    from interview_assistant.app import InterviewApplication

    app = InterviewApplication.for_test()
    app.shutdown()
    app.shutdown()
    assert app.is_shutdown
