import asyncio

import pytest

from interview_assistant.app import InterviewApplication
from interview_assistant.lmstudio.models import ChatError, ChatEvent
from tests.unit.test_runtime_lifecycle import _Client, _ParityCapture, _hypothesis, _runtime


COMPLETED = "I would use an index to avoid scanning every row."
DRAFT = "Unfinished alternative that must never enter follow-up context."


class ConversationClient(_Client):
    def __init__(self, *, second_outcome="completed"):
        super().__init__()
        self.second_outcome = second_outcome
        self.second_started = asyncio.Event()

    async def stream_chat(self, payload):
        index = len(self.payloads)
        self.payloads.append(dict(payload))
        yield ChatEvent(type="chat.start")
        if index == 0:
            yield ChatEvent(type="message.delta", content=COMPLETED)
        elif index == 1 and self.second_outcome != "completed":
            yield ChatEvent(type="message.delta", content=DRAFT)
            self.second_started.set()
            if self.second_outcome == "cancelled":
                await asyncio.Event().wait()
            else:
                yield ChatEvent(
                    type="error", error=ChatError(type="internal_error", message="test failure"),
                )
                return
        else:
            yield ChatEvent(type="message.delta", content="An index trades writes for faster reads.")
        yield ChatEvent(type="chat.end")


def _session(qtbot, tmp_path, *, second_outcome="completed"):
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    client = ConversationClient(second_outcome=second_outcome)
    runtime, _, _ = _runtime(app, client=client, capture=_ParityCapture(tmp_path / "unused.png"))
    runtime.config.lmstudio.vision_model = ""
    return app, runtime, client


async def test_completed_answer_is_included_in_next_follow_up(qtbot, tmp_path):
    _, runtime, client = _session(qtbot, tmp_path)
    try:
        await runtime.start()
        await runtime.submit_hypothesis(_hypothesis("How do database indexes work?"))
        await runtime.submit_hypothesis(_hypothesis("Why did you choose that approach?"))
        assert "Previous answer:" not in client.payloads[0]["input"]
        assert f"Previous answer:\n{COMPLETED}" in client.payloads[1]["input"]
    finally:
        await runtime.shutdown()


@pytest.mark.parametrize("clear_action", ["clear_signal", "clear_history"])
async def test_clear_discards_answer_before_next_follow_up(qtbot, tmp_path, clear_action):
    app, runtime, client = _session(qtbot, tmp_path)
    try:
        await runtime.start()
        await runtime.submit_hypothesis(_hypothesis("How do database indexes work?"))
        if clear_action == "clear_signal":
            app.events.answer_clear_requested.emit()
        else:
            runtime.clear_history()
        await runtime.submit_hypothesis(_hypothesis("Why did you choose that approach?"))
        assert "Previous answer:" not in client.payloads[1]["input"]
        assert COMPLETED not in client.payloads[1]["input"]
    finally:
        await runtime.shutdown()


async def test_shutdown_removes_completed_answer_from_runtime_memory(qtbot, tmp_path):
    _, runtime, _ = _session(qtbot, tmp_path)
    try:
        await runtime.start()
        await runtime.submit_hypothesis(_hypothesis("How do database indexes work?"))
        assert runtime._previous_answer == COMPLETED
    finally:
        await runtime.shutdown()
    assert runtime._previous_answer is None


@pytest.mark.parametrize("second_outcome", ["failed", "cancelled"])
async def test_failed_or_cancelled_answer_preserves_last_completed_context(
    qtbot, tmp_path, second_outcome,
):
    _, runtime, client = _session(qtbot, tmp_path, second_outcome=second_outcome)
    try:
        await runtime.start()
        await runtime.submit_hypothesis(_hypothesis("How do database indexes work?"))
        second = runtime.submit_hypothesis(_hypothesis("Why did you choose that approach?"))
        if second_outcome == "cancelled":
            await asyncio.wait_for(client.second_started.wait(), timeout=1)
        else:
            await second
        await runtime.submit_hypothesis(_hypothesis("Could you give a concrete example?"))
        await asyncio.gather(second, return_exceptions=True)
        assert f"Previous answer:\n{COMPLETED}" in client.payloads[2]["input"]
        assert DRAFT not in client.payloads[2]["input"]
    finally:
        await runtime.shutdown()
