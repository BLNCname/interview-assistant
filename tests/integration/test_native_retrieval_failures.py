import pytest

from interview_assistant.app import InterviewApplication
from tests.unit.test_runtime_lifecycle import _ParityCapture, _hypothesis, _runtime


@pytest.mark.parametrize(("code", "expected_reason"), [
    ("key_required", "requires an API key"),
    ("credentials", "FIRECRAWL_API_KEY"),
    ("credits", "credits or quota"),
    ("rate_limit", "rate limit"),
    ("timeout", "timed out"),
    ("unavailable", "Web retrieval unavailable"),
    ("private-api-key", "Web retrieval unavailable"),
])
async def test_known_native_search_failures_show_safe_reason_and_still_answer(
    qtbot, tmp_path, code, expected_reason,
):
    from interview_assistant.retrieval.mcp_client import NativeMCPError

    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    runtime, services, client = _runtime(app, capture=_ParityCapture(tmp_path / "unused.png"))
    notifications = []
    app.events.notification.connect(notifications.append)

    class Retrieval:
        async def retrieve(self, integration):
            assert integration.id == "mcp/firecrawl"
            raise NativeMCPError("untrusted upstream details: private-api-key", code=code)

        async def aclose(self):
            pass

    services.retrieval = Retrieval()
    services.search_policy.force_next()
    try:
        await runtime.start()
        await runtime.submit_hypothesis(_hypothesis("What is the latest Python release?"))
        assert len(client.payloads) == 1
        assert "integrations" not in client.payloads[0]
        assert app.ribbon.answer_text == "answer"
        assert app.states.state.value == "listening"
        assert app.ribbon.source_texts == ()
        assert expected_reason in " ".join(notifications)
        assert "private-api-key" not in " ".join(notifications)
    finally:
        await runtime.shutdown()


async def test_arbitrary_search_exception_cannot_supply_ui_text_or_error_code(qtbot, tmp_path):
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    runtime, services, client = _runtime(app, capture=_ParityCapture(tmp_path / "unused.png"))
    notifications = []
    app.events.notification.connect(notifications.append)

    class UntrustedError(RuntimeError):
        code = "credentials"

    class Retrieval:
        async def retrieve(self, integration):
            raise UntrustedError("private-api-key")

        async def aclose(self):
            pass

    services.retrieval = Retrieval()
    services.search_policy.force_next()
    try:
        await runtime.start()
        await runtime.submit_hypothesis(_hypothesis("What is the latest Python release?"))
        assert len(client.payloads) == 1
        assert app.ribbon.answer_text == "answer"
        assert notifications == ["Web retrieval unavailable; continuing without it."]
    finally:
        await runtime.shutdown()
