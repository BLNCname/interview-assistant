"""Offline characterization of context loss; no model, audio, GPU or HTTP calls.

These tests distinguish bounded recent context from semantic summarization.
The synthetic facts make retained and discarded information independently visible.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from interview_assistant.audio.models import AudioSource
from interview_assistant.context.builder import ContextBuilder
from interview_assistant.context.models import ContextBudgetError
from interview_assistant.lmstudio.payload import build_context_payload
from interview_assistant.stt.engine import TranscriptHypothesis
from interview_assistant.transcript.detector import DetectedQuestion
from interview_assistant.transcript.store import TranscriptStore


@dataclass
class ManualClock:
    now: float = 0.0

    def __call__(self) -> float:
        return self.now


def add_turn(store: TranscriptStore, index: int, text: str, ended_at: float) -> None:
    store.add(TranscriptHypothesis(
        source=AudioSource.SYSTEM if index % 2 == 0 else AudioSource.MICROPHONE,
        text=text,
        language="en",
        is_final=True,
        started_at=max(0.0, ended_at - 0.01),
        ended_at=ended_at,
    ))


def question(text: str, now: float = 0.0) -> DetectedQuestion:
    return DetectedQuestion(1, "system_design", text, now, AudioSource.SYSTEM)


def hour_dialogue() -> tuple[dict[str, object], dict[str, object]]:
    clock = ManualClock()
    store = TranscriptStore(clock=clock)
    facts = {
        0: "The project codename is HAWTHORN-731.",
        120: "The earlier migration batch size was 1907.",
        220: "The retention period is 43 days.",
        238: "We settled on receipt_id as the idempotency key.",
        239: "We settled on exactly 3 replicas across failure domains.",
    }
    peak_tokens = 0
    peak_store_count = 0
    for index in range(240):
        clock.now = index * 15.0 + 1.0
        text = facts.get(index, f"Decision {index}: retries use bounded exponential backoff.")
        add_turn(store, index, text, clock.now)
        snapshot = ContextBuilder(
            store,
            latest_question=question(f"Explain decision {index} and its trade-offs.", clock.now),
        ).normal()
        assert snapshot.estimated_tokens <= snapshot.max_tokens
        assert text in snapshot.prompt
        peak_tokens = max(peak_tokens, snapshot.estimated_tokens)
        peak_store_count = max(peak_store_count, len(store.snapshot()))

    clock.now = 3600.0
    current_text = "What idempotency key and replica count did we settle on?"
    # Runtime records the triggering final utterance before building its request.
    add_turn(store, 240, current_text, clock.now)
    snapshot = ContextBuilder(
        store,
        latest_question=question(current_text, clock.now),
    ).normal()
    prompt = snapshot.prompt
    stored = "\n".join(entry.text for entry in store.snapshot())
    assert "receipt_id" in prompt and "exactly 3 replicas" in prompt
    assert "HAWTHORN-731" not in prompt and "1907" not in prompt
    assert "43 days" in stored and "43 days" not in prompt
    assert len([item for item in snapshot.items if item.kind == "transcript"]) == 7
    assert prompt.count(current_text) == 1
    assert len(store.snapshot()) == 21
    payload = build_context_payload("MODEL_ID_REPLACE_BEFORE_LIVE_TEST", snapshot)
    assert payload["store"] is False
    assert "previous_response_id" not in payload
    return {
        "historical_utterances": 240,
        "total_utterances_including_current_request": 241,
        "simulated_seconds": 3600,
        "maximum_store_entries": peak_store_count,
        "final_store_entries": len(store.snapshot()),
        "final_prompt_transcript_entries": 7,
        "current_request_counts_towards_last_eight_then_is_deduplicated": True,
        "peak_estimated_tokens": peak_tokens,
        "final_estimated_tokens": snapshot.estimated_tokens,
        "recent_idempotency_anchor_retained": True,
        "recent_replica_anchor_retained": True,
        "early_and_middle_anchors_expired": True,
        "five_minute_anchor_in_store_but_outside_last_eight": True,
        "semantic_summary_created": False,
    }, payload


def test_hour_dialogue_retains_recent_facts_and_exposes_earlier_fact_loss() -> None:
    hour_dialogue()


def dense_dialogue() -> dict[str, object]:
    clock = ManualClock()
    store = TranscriptStore(clock=clock)
    for index in range(3000):
        clock.now = index * 0.04
        add_turn(store, index, f"TURN-{index:04}: payment ledger partitions by merchant.", clock.now)
    snapshot = ContextBuilder(
        store, latest_question=question("What is the ledger partition key?", clock.now),
    ).normal()
    transcript = [item.text for item in snapshot.items if item.kind == "transcript"]
    assert len(store.snapshot()) == 3000
    assert len(transcript) == 8
    assert transcript[0].startswith("TURN-2992:")
    assert transcript[-1].startswith("TURN-2999:")
    assert "TURN-2991:" not in snapshot.prompt
    assert snapshot.estimated_tokens <= 4000
    return {
        "utterances": 3000,
        "simulated_seconds": clock.now,
        "store_entries": len(store.snapshot()),
        "prompt_transcript_entries": len(transcript),
        "estimated_tokens": snapshot.estimated_tokens,
        "store_count_is_not_capped": True,
    }


def test_dense_dialogue_bounds_prompt_but_store_has_only_an_age_limit() -> None:
    dense_dialogue()


def budget_pressure() -> tuple[dict[str, object], dict[str, object]]:
    store = TranscriptStore(clock=lambda: 100.0)
    for index in range(8):
        add_turn(store, index, f"Decision {index}: audit partition key is tenant_id.", 90.0 + index)
    current = question("Explain the CURRENT-REQUEST-ANCHOR and identify available facts.", 100.0)
    previous = "PREVIOUS-ANSWER-ANCHOR " + "Design rationale. " * 332
    search = "SEARCH-EVIDENCE-ANCHOR " + "External documentation. " * 249
    previous = previous[:6000].strip()
    search = search[:6000].strip()
    builder = ContextBuilder(
        store, latest_question=current, previous_answer=previous, search_results=(search,),
    )
    snapshot = builder.normal()
    assert "CURRENT-REQUEST-ANCHOR" in snapshot.prompt
    assert "tenant_id" not in snapshot.prompt
    assert "PREVIOUS-ANSWER-ANCHOR" not in snapshot.prompt
    assert search in snapshot.prompt
    assert snapshot.estimated_tokens <= 4000
    assert snapshot == builder.normal()
    payload = build_context_payload("MODEL_ID_REPLACE_BEFORE_LIVE_TEST", snapshot)
    return {
        "previous_answer_characters": len(previous),
        "search_characters": len(search),
        "estimated_tokens": snapshot.estimated_tokens,
        "current_request_retained": True,
        "recent_dialogue_facts_discarded": True,
        "previous_answer_discarded": True,
        "search_retained_verbatim": True,
        "deterministic_on_repeated_build": True,
    }, payload


def test_budget_pressure_removes_whole_history_and_answer_without_summarizing() -> None:
    budget_pressure()


def test_default_store_expiry_is_exact_at_five_minutes() -> None:
    clock = ManualClock(1.0)
    store = TranscriptStore(clock=clock)
    add_turn(store, 0, "Expiry anchor: audit records use lease_epoch.", 1.0)
    clock.now = 301.0
    assert "lease_epoch" in ContextBuilder(
        store, latest_question=question("Which field identifies the lease?", clock.now),
    ).normal().prompt
    clock.now = 301.001
    assert store.snapshot() == ()
    assert "lease_epoch" not in ContextBuilder(
        store, latest_question=question("Which field identifies the lease?", clock.now),
    ).normal().prompt


def test_recovery_has_no_summary_and_drops_normal_history_and_previous_answer() -> None:
    store = TranscriptStore(clock=lambda: 100.0)
    add_turn(store, 0, "Earlier decision: use ARCHIVE-ANCHOR-59.", 90.0)
    add_turn(store, 1, "Clarification: retries must preserve RECEIPT-ANCHOR-23.", 91.0)
    builder = ContextBuilder(
        store,
        latest_question=question("Explain the current request.", 100.0),
        previous_answer="Previous proposal used ANSWER-ANCHOR-82.",
        search_results="Documentation evidence SEARCH-ANCHOR-17.",
    )
    normal = builder.normal()
    recovery = builder.recovery(max_tokens=8000)
    assert all(anchor in normal.prompt for anchor in (
        "ARCHIVE-ANCHOR-59", "RECEIPT-ANCHOR-23", "ANSWER-ANCHOR-82", "SEARCH-ANCHOR-17",
    ))
    assert "RECEIPT-ANCHOR-23" in recovery.prompt
    assert all(anchor not in recovery.prompt for anchor in (
        "ARCHIVE-ANCHOR-59", "ANSWER-ANCHOR-82", "SEARCH-ANCHOR-17",
    ))
    assert recovery.max_tokens == 2000
    assert recovery.estimated_tokens <= 2000


def test_mandatory_oversize_request_raises_instead_of_fake_compaction() -> None:
    current = question("Explain REQUIRED-ANCHOR. " + "Specification detail. " * 700)
    builder = ContextBuilder(TranscriptStore(), latest_question=current)
    with pytest.raises(ContextBudgetError, match="mandatory context requires"):
        builder.normal()
    with pytest.raises(ContextBudgetError, match="mandatory context requires"):
        builder.recovery()


def test_microphone_follow_up_preserves_required_interviewer_after_history_limit() -> None:
    store = TranscriptStore(clock=lambda: 100.0)
    add_turn(store, 0, "Interviewer requirement: LATENCY-ANCHOR-37 milliseconds.", 10.0)
    for index in range(1, 20, 2):
        add_turn(store, index, f"Candidate explanation {index}: bounded retries.", 70.0 + index)
    trigger = DetectedQuestion(
        2, "system_design", "Does that include queueing delay?", 100.0, AudioSource.MICROPHONE,
    )
    snapshot = ContextBuilder(store, latest_question=trigger).normal()
    assert "LATENCY-ANCHOR-37" in snapshot.prompt
    assert snapshot.prompt.count(trigger.text) == 1
    assert "Latest interviewer request:\nInterviewer requirement:" in snapshot.prompt
