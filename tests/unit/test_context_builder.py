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


def test_recovery_rejects_supplied_context_that_cannot_fit_hard_cap() -> None:
    builder = ContextBuilder(
        TranscriptStore(clock=lambda: 100.0),
        latest_question=DetectedQuestion(1, "screen_analysis", "latest question", 99.0),
        screenshot="x" * 6_000,
    )

    with pytest.raises(ContextBudgetError, match="recovery"):
        builder.recovery(max_tokens=10_000)
