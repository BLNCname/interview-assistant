from pathlib import Path
import sys

import pytest

from interview_assistant.audio.models import AudioSource
from interview_assistant.context.builder import ContextBuilder
from interview_assistant.context.models import ContextBudgetError
from interview_assistant.stt.engine import TranscriptHypothesis
from interview_assistant.transcript.detector import DetectedQuestion
from interview_assistant.transcript.store import TranscriptStore


def add_final(
    store: TranscriptStore,
    source: AudioSource,
    text: str,
    timestamp: float,
) -> None:
    store.add(
        TranscriptHypothesis(
            source=source,
            text=text,
            language="en",
            is_final=True,
            started_at=timestamp,
            ended_at=timestamp + 0.5,
        )
    )


@pytest.fixture
def context_builder() -> ContextBuilder:
    store = TranscriptStore(max_age_seconds=200.0, clock=lambda: 100.0)
    add_final(store, AudioSource.SYSTEM, "old transcript", 10.0)
    add_final(store, AudioSource.MICROPHONE, "old microphone note", 20.0)
    add_final(store, AudioSource.MICROPHONE, "latest clarification", 90.0)
    return ContextBuilder(
        store,
        latest_question=DetectedQuestion(1, "theory", "latest question", 99.0),
        resume="Python backend engineer",
        job_description="Distributed storage role",
        stack="Python, PostgreSQL",
        screenshot="relevant screenshot",
        search_results=("relevant search result",),
        previous_answer="old assistant answer",
    )


def test_recovery_context_drops_old_answers(context_builder: ContextBuilder) -> None:
    snapshot = context_builder.recovery(max_tokens=2_000)

    assert snapshot.question == "latest question"
    assert "old assistant answer" not in snapshot.prompt
    assert snapshot.estimated_tokens <= 2_000


def test_packaged_prompt_defines_concise_candidate_style_and_exception() -> None:
    prompt = Path("prompts/interview_system.md").read_text(encoding="utf-8")
    lowered = prompt.casefold()

    assert "120" in prompt
    assert "3–6" in prompt or "3-6" in prompt
    assert "candidate clarification trigger" in lowered
    assert "код" in lowered and "system design" in lowered
    assert "ограничение" in lowered and "не применяется" in lowered
    assert "как ии" in lowered


def test_context_builder_prefers_frozen_packaged_candidate_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen_prompt = tmp_path / "frozen" / "prompts" / "interview_system.md"
    frozen_prompt.parent.mkdir(parents=True)
    frozen_prompt.write_text("Frozen candidate contract", encoding="utf-8")
    monkeypatch.setattr(sys, "_MEIPASS", str(frozen_prompt.parents[1]), raising=False)

    snapshot = ContextBuilder(
        TranscriptStore(),
        latest_question=DetectedQuestion(1, "theory", "latest question", 1.0),
    ).normal()

    assert snapshot.items[0].text == "Frozen candidate contract"


def test_normal_context_is_deterministic_and_includes_supplied_fields(
    context_builder: ContextBuilder,
) -> None:
    first = context_builder.normal(max_tokens=2_000)
    second = context_builder.normal(max_tokens=2_000)

    assert first == second
    assert first.question == "latest question"
    assert first.task_kind == "theory"
    assert first.estimated_tokens == -(-len(first.prompt) // 3)
    assert {
        "system",
        "task",
        "question",
        "transcript",
        "resume",
        "job",
        "stack",
        "screenshot",
        "search",
        "previous_answer",
    } <= {item.kind for item in first.items}
    assert "old transcript" in first.prompt
    assert "latest clarification" in first.prompt
    assert "old assistant answer" in first.prompt


def test_microphone_trigger_keeps_candidate_and_interviewer_roles() -> None:
    store = TranscriptStore(clock=lambda: 11.5)
    add_final(store, AudioSource.SYSTEM, "Хотелось бы услышать мнение о CAP", 10.0)
    add_final(
        store,
        AudioSource.MICROPHONE,
        "То есть про consistency trade-offs?",
        11.0,
    )
    question = DetectedQuestion(
        1,
        "theory",
        "То есть про consistency trade-offs?",
        11.0,
        AudioSource.MICROPHONE,
    )

    snapshot = ContextBuilder(store, latest_question=question).normal()

    assert "Latest interviewer request:\nХотелось бы услышать мнение о CAP" in snapshot.prompt
    assert (
        "Candidate clarification trigger:\nТо есть про consistency trade-offs?" in snapshot.prompt
    )
    assert snapshot.items[2].source is AudioSource.MICROPHONE
    assert snapshot.prompt.count("То есть про consistency trade-offs?") == 1
    assert snapshot.prompt.count("Хотелось бы услышать мнение о CAP") == 1


def test_system_trigger_is_labelled_as_interviewer_request() -> None:
    question = DetectedQuestion(
        1,
        "theory",
        "Что такое CAP?",
        1.0,
        AudioSource.SYSTEM,
    )

    snapshot = ContextBuilder(
        TranscriptStore(),
        latest_question=question,
    ).normal()

    assert "Latest interviewer request:\nЧто такое CAP?" in snapshot.prompt
    assert all(item.label != "Candidate clarification trigger" for item in snapshot.items)


def test_recovery_context_uses_role_aware_request_labels() -> None:
    store = TranscriptStore(clock=lambda: 11.5)
    add_final(store, AudioSource.SYSTEM, "Расскажите о CAP", 10.0)
    question = DetectedQuestion(
        1,
        "theory",
        "То есть про consistency?",
        11.0,
        AudioSource.MICROPHONE,
    )

    snapshot = ContextBuilder(store, latest_question=question).recovery()

    assert "Latest interviewer request:\nРасскажите о CAP" in snapshot.prompt
    assert "Candidate clarification trigger:\nТо есть про consistency?" in snapshot.prompt


def test_recovery_system_trigger_keeps_microphone_history_non_trigger_label() -> None:
    store = TranscriptStore(clock=lambda: 11.0)
    add_final(store, AudioSource.MICROPHONE, "Уточняю детали CAP", 10.0)
    question = DetectedQuestion(
        1,
        "theory",
        "Что такое CAP?",
        11.0,
        AudioSource.SYSTEM,
    )

    snapshot = ContextBuilder(store, latest_question=question).recovery()

    assert "Latest clarification from You:\nУточняю детали CAP" in snapshot.prompt
    assert all(item.label != "Candidate clarification trigger" for item in snapshot.items)


def test_recovery_source_less_question_keeps_microphone_history_non_trigger_label() -> None:
    store = TranscriptStore(clock=lambda: 11.0)
    add_final(store, AudioSource.MICROPHONE, "Уточняю детали CAP", 10.0)
    question = DetectedQuestion(1, "manual", "Объясни CAP", 11.0)

    snapshot = ContextBuilder(store, latest_question=question).recovery()

    assert "Latest clarification from You:\nУточняю детали CAP" in snapshot.prompt
    assert all(item.label != "Candidate clarification trigger" for item in snapshot.items)


def test_normal_context_drops_oldest_low_priority_history_first() -> None:
    full_store = TranscriptStore(clock=lambda: 100.0)
    add_final(full_store, AudioSource.SYSTEM, "oldest history " * 12, 10.0)
    add_final(full_store, AudioSource.MICROPHONE, "middle history " * 12, 20.0)
    add_final(full_store, AudioSource.SYSTEM, "newest history " * 12, 30.0)
    question = DetectedQuestion(1, "theory", "latest question", 99.0)
    full_builder = ContextBuilder(full_store, latest_question=question)

    recent_store = TranscriptStore(clock=lambda: 100.0)
    add_final(recent_store, AudioSource.MICROPHONE, "middle history " * 12, 20.0)
    add_final(recent_store, AudioSource.SYSTEM, "newest history " * 12, 30.0)
    recent_builder = ContextBuilder(recent_store, latest_question=question)
    recent_only = recent_builder.normal(max_tokens=2_000)

    pruned = full_builder.normal(max_tokens=recent_only.estimated_tokens)

    assert "oldest history" not in pruned.prompt
    assert "middle history" in pruned.prompt
    assert "newest history" in pruned.prompt
    assert pruned.estimated_tokens <= recent_only.estimated_tokens


def test_normal_context_never_drops_mandatory_items_and_rejects_impossible_budget() -> None:
    builder = ContextBuilder(
        TranscriptStore(clock=lambda: 100.0),
        latest_question=DetectedQuestion(1, "coding", "latest question", 99.0),
    )
    minimum = builder.normal(max_tokens=2_000)

    exact = builder.normal(max_tokens=minimum.estimated_tokens)

    assert {item.kind for item in exact.items} == {"system", "task", "question"}
    assert exact.question in exact.prompt
    with pytest.raises(ContextBudgetError, match="mandatory"):
        builder.normal(max_tokens=minimum.estimated_tokens - 1)


def test_recovery_is_capped_and_contains_only_minimal_current_context(
    context_builder: ContextBuilder,
) -> None:
    snapshot = context_builder.recovery(max_tokens=10_000)

    assert snapshot.max_tokens == 2_000
    assert snapshot.estimated_tokens <= snapshot.max_tokens
    assert "latest question" in snapshot.prompt
    assert "latest clarification" in snapshot.prompt
    assert "relevant screenshot" in snapshot.prompt
    assert "old transcript" not in snapshot.prompt
    assert "old microphone note" not in snapshot.prompt
    assert "old assistant answer" not in snapshot.prompt
    assert "relevant search result" not in snapshot.prompt
    assert "Python backend engineer" not in snapshot.prompt
    assert {item.kind for item in snapshot.items} == {
        "system",
        "task",
        "question",
        "clarification",
        "screenshot",
    }


def test_recovery_omits_oversized_optional_screenshot_within_hard_cap() -> None:
    builder = ContextBuilder(
        TranscriptStore(clock=lambda: 100.0),
        latest_question=DetectedQuestion(1, "screen_analysis", "latest question", 99.0),
        screenshot="x" * 6_000,
    )

    snapshot = builder.recovery(max_tokens=10_000)

    assert snapshot.max_tokens == 2_000
    assert snapshot.estimated_tokens <= snapshot.max_tokens
    assert {item.kind for item in snapshot.items} == {"system", "task", "question"}


def test_recovery_drops_clarification_before_screenshot_under_tight_budget() -> None:
    question = DetectedQuestion(1, "screen_analysis", "latest question", 99.0)
    screenshot = "relevant screenshot"
    screenshot_only = ContextBuilder(
        TranscriptStore(clock=lambda: 100.0),
        latest_question=question,
        screenshot=screenshot,
    ).recovery()

    clarification_store = TranscriptStore(clock=lambda: 100.0)
    add_final(
        clarification_store,
        AudioSource.MICROPHONE,
        "latest clarification",
        90.0,
    )
    clarification_only = ContextBuilder(
        clarification_store,
        latest_question=question,
    ).recovery()
    budget = max(
        screenshot_only.estimated_tokens,
        clarification_only.estimated_tokens,
    )
    builder = ContextBuilder(
        clarification_store,
        latest_question=question,
        screenshot=screenshot,
    )

    snapshot = builder.recovery(max_tokens=budget)

    assert "relevant screenshot" in snapshot.prompt
    assert "latest clarification" not in snapshot.prompt
    assert {item.kind for item in snapshot.items} == {
        "system",
        "task",
        "question",
        "screenshot",
    }


def test_recovery_raises_only_when_mandatory_context_cannot_fit() -> None:
    builder = ContextBuilder(
        TranscriptStore(clock=lambda: 100.0),
        latest_question=DetectedQuestion(1, "coding", "latest question", 99.0),
    )
    minimum = builder.recovery()

    with pytest.raises(ContextBudgetError, match="mandatory"):
        builder.recovery(max_tokens=minimum.estimated_tokens - 1)


def test_normal_overrides_can_clear_stored_optional_context_for_next_question(
    context_builder: ContextBuilder,
) -> None:
    first = context_builder.normal(max_tokens=2_000)
    second = context_builder.normal(
        max_tokens=2_000,
        latest_question=DetectedQuestion(2, "coding", "second question", 100.0),
        resume=None,
        job_description="",
        stack=None,
        screenshot="",
        search_results=(),
        previous_answer=None,
    )

    assert "relevant screenshot" in first.prompt
    assert "old assistant answer" in first.prompt
    assert "relevant screenshot" not in second.prompt
    assert "old assistant answer" not in second.prompt
    assert "Python backend engineer" not in second.prompt
    assert "Distributed storage role" not in second.prompt
    assert "Python, PostgreSQL" not in second.prompt
    assert "relevant search result" not in second.prompt
    assert second.question == "second question"


def test_recovery_screenshot_override_can_clear_stored_default(
    context_builder: ContextBuilder,
) -> None:
    default = context_builder.recovery()
    cleared_with_none = context_builder.recovery(screenshot=None)
    cleared_with_empty = context_builder.recovery(screenshot="")

    assert "relevant screenshot" in default.prompt
    assert "relevant screenshot" not in cleared_with_none.prompt
    assert "relevant screenshot" not in cleared_with_empty.prompt
