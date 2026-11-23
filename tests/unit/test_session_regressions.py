import asyncio

from interview_assistant.app import InterviewApplication
from interview_assistant.capture.worker import CaptureResult, _await_completion
from interview_assistant.composition import ApplicationController
from interview_assistant.lmstudio.models import ChatEvent
from interview_assistant.orchestration.coordinator import RequestCoordinator
from tests.unit.test_application_controller import _Components, _Secrets, _config, _report, _settings
from tests.unit.test_runtime_lifecycle import _BlockingClient, _Client, _ParityCapture, _hypothesis, _runtime


async def test_clear_during_generation_discards_late_answer_and_context(qtbot, tmp_path):
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    client = _BlockingClient()
    runtime, _, _ = _runtime(app, client=client, capture=_ParityCapture(tmp_path / "unused"))
    runtime.config.lmstudio.vision_model = ""
    try:
        await runtime.start()
        active = runtime.submit_hypothesis(_hypothesis("What is binary search?"))
        await asyncio.wait_for(client.started.wait(), 2)
        app.events.answer_clear_requested.emit()
        client.release.set()
        await asyncio.gather(active, return_exceptions=True)
        assert not app.ribbon.answer_text and runtime._previous_answer is None
        await runtime.submit_hypothesis(_hypothesis("How does sorting work?"))
        assert "Previous answer:" not in client.payloads[-1]["input"]
        assert "binary search" not in client.payloads[-1]["input"]
    finally:
        await runtime.shutdown()


async def test_two_coordinators_share_ribbon_sequence(qtbot):
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)

    class Echo(_Client):
        async def stream_chat(self, payload):
            yield ChatEvent(type="message.delta", content=payload["input"])
            yield ChatEvent(type="chat.end")

    first = RequestCoordinator(app.events, Echo())
    for text in ("old one", "old two"):
        await first.wait(first.submit({"input": text}))
    second = RequestCoordinator(app.events, Echo())
    await second.wait(second.submit({"input": "current"}))
    assert app.ribbon.answer_text == app.ribbon.answer_browser.toPlainText() == "current"


async def test_cancelled_middle_question_never_reaches_provider(qtbot, tmp_path):
    class HeldCapture(_ParityCapture):
        def __init__(self):
            super().__init__(tmp_path / "unused")
            self.entered = asyncio.Event()
            self.complete = asyncio.get_running_loop().create_future()

        async def capture_for_event(self, kind, *, manual=False):
            self.entered.set()
            return await _await_completion(self.complete)

    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    capture = HeldCapture()
    runtime, _, client = _runtime(app, capture=capture)
    try:
        await runtime.start()
        first = runtime.submit_hypothesis(_hypothesis("How does binary search work?"))
        await asyncio.wait_for(capture.entered.wait(), 2)
        middle = runtime.submit_hypothesis(_hypothesis("What is a distributed transaction?"))
        await asyncio.sleep(0)
        last = runtime.submit_hypothesis(_hypothesis("Why use a linked list?"))
        await asyncio.sleep(0)
        capture.complete.set_result(CaptureResult("disallowed", None, None))
        await asyncio.gather(first, middle, last, return_exceptions=True)
        assert first.cancelled() and middle.cancelled() and not last.cancelled()
        assert len(client.payloads) == 1
        assert "Latest interviewer request:\nWhy use a linked list?" in client.payloads[0]["input"]
    finally:
        await runtime.shutdown()


async def test_save_unchanged_during_session_keeps_components(qtbot, tmp_path):
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    config, secrets = _config(), _Secrets()
    window = _settings(qtbot, tmp_path, config, secrets)
    original = _Components(_report())
    controller = ApplicationController(app, window, config, secrets, component_factory=lambda *_: original,
                                       loop=asyncio.get_running_loop())
    try:
        await controller.initialize()
        await controller.start_session()
        app.events.settings_requested.emit()
        assert window.save()
        await asyncio.sleep(0)
        assert controller.components is original and original.close_count == 0
        assert original.readiness.run_count == original.runtime.start_count == 1
    finally:
        await controller.shutdown()


async def test_readiness_result_expires_when_user_changes_model_mid_check(qtbot, tmp_path):
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    config, secrets = _config(), _Secrets()
    window = _settings(qtbot, tmp_path, config, secrets)
    components = _Components(_report())
    entered, release = asyncio.Event(), asyncio.Event()

    async def wait_for_checks():
        entered.set()
        await release.wait()
        return _report()

    components.readiness.run = wait_for_checks
    controller = ApplicationController(app, window, config, secrets, component_factory=lambda *_: components,
                                       loop=asyncio.get_running_loop())
    try:
        task = asyncio.create_task(controller.initialize())
        await asyncio.wait_for(entered.wait(), 2)
        window.stt_model_combo.setCurrentIndex(window.stt_model_combo.findData("tiny"))
        release.set()
        await task
        assert not window.start_button.isEnabled() and window.readiness_report is None
        await controller.start_session()
        assert components.runtime.start_count == 0
    finally:
        await controller.shutdown()
