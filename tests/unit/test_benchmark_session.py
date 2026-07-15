from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.benchmark_session import (
    BenchmarkInputError,
    build_report,
    main,
    parse_event_lines,
)


def _metadata() -> dict[str, object]:
    return {
        "event": "session_metadata",
        "observed_at_utc": "2026-07-15T12:00:00+00:00",
        "consent_confirmed": True,
        "app_version": "0.1.0",
        "windows_version": "Windows-11-24H2",
        "teams_version": "25200.1000.3000.0",
        "lm_studio_version": "0.4.19",
        "stt_model": "large-v3-turbo",
        "llm_model": "qwen3.5",
        "main_gpu": "RTX-5070-Ti",
        "inference_device": "Strix-Halo",
        "evidence_ref": "evidence/session-001",
    }


def _stt_events(language: str, *, partial_ms: float = 500.0) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for index in range(20):
        request_id = f"{language}-{index:02d}"
        base = index * 5_000.0
        common = {"request_id": request_id, "language": language, "source": "microphone"}
        events.extend(
            [
                {"event": "audio_start", "timestamp_ms": base, **common},
                {"event": "first_partial", "timestamp_ms": base + partial_ms, **common},
                {"event": "speech_end", "timestamp_ms": base + 1_500.0, **common},
                {
                    "event": "final_transcript",
                    "timestamp_ms": base + 2_000.0,
                    "reference_words": 10,
                    "word_errors": 1,
                    **common,
                },
            ]
        )
    return events


def _criterion(report: list[dict[str, object]], name: str) -> dict[str, object]:
    return next(
        record
        for record in report
        if record["record_type"] == "criterion" and record["criterion"] == name
    )


def _metric(
    report: list[dict[str, object]], name: str, language: str | None = None
) -> dict[str, object]:
    return next(
        record
        for record in report
        if record["record_type"] == "metric"
        and record["metric"] == name
        and record.get("language") == language
    )


def test_report_computes_stt_percentiles_and_requires_twenty_consent_samples() -> None:
    events = [_metadata(), *_stt_events("ru"), *_stt_events("en", partial_ms=1_100.0)]

    report = build_report(
        events,
        session_id="observed-001",
        evidence_kind="observed",
        generated_at_utc="2026-07-15T12:30:00+00:00",
    )

    assert _metric(report, "stt_first_partial_latency_ms", "ru") == {
        "record_type": "metric",
        "metric": "stt_first_partial_latency_ms",
        "language": "ru",
        "sample_count": 20,
        "p50": 500.0,
        "p95": 500.0,
        "unit": "ms",
    }
    assert _metric(report, "stt_finalization_after_pause_ms", "ru")["p95"] == 500.0
    assert _metric(report, "stt_word_error_rate", "ru")["value"] == 0.1
    assert _criterion(report, "stt_ru")["status"] == "PASS"
    assert _criterion(report, "stt_en")["status"] == "FAIL"


def test_partial_or_missing_evidence_is_not_run_instead_of_failure() -> None:
    incomplete = [_metadata(), *_stt_events("ru")[:4]]

    report = build_report(
        incomplete,
        session_id="observed-002",
        evidence_kind="observed",
        generated_at_utc="2026-07-15T12:30:00+00:00",
    )

    assert _criterion(report, "stt_ru")["status"] == "NOT_RUN"
    assert _criterion(report, "stt_en")["status"] == "NOT_RUN"
    assert _criterion(report, "teams_full_screen")["status"] == "NOT_RUN"
    assert _criterion(report, "overall_acceptance")["status"] == "NOT_RUN"


def test_lm_metrics_and_recovery_require_exactly_one_reload_and_latest_replay() -> None:
    events = [
        _metadata(),
        {"event": "request_submitted", "timestamp_ms": 1_000.0, "request_id": "q-1"},
        {"event": "prompt_processing_end", "timestamp_ms": 1_250.0, "request_id": "q-1"},
        {"event": "first_answer_token", "timestamp_ms": 1_400.0, "request_id": "q-1"},
        {
            "event": "chat_end",
            "timestamp_ms": 2_400.0,
            "request_id": "q-1",
            "output_tokens": 50,
        },
        {
            "event": "model_load_completed",
            "duration_ms": 12_000.0,
            "load_attempts": 1,
            "preferred_device_confirmed": True,
            "loaded_instances": 1,
            "same_model_key_selected": True,
            "shared_text_vision_instance": True,
        },
        {
            "event": "recovery_completed",
            "duration_ms": 95_000.0,
            "request_id": "q-2",
            "unload_during_request": True,
            "reload_count": 1,
            "reload_attempts": 1,
            "preferred_device_confirmed": True,
            "latest_only_replayed": True,
            "rtx_llm_fallback_used": False,
        },
    ]

    report = build_report(
        events,
        session_id="observed-003",
        evidence_kind="observed",
        generated_at_utc="2026-07-15T12:30:00+00:00",
    )

    assert _metric(report, "prompt_processing_ms")["p50"] == 250.0
    assert _metric(report, "request_to_first_answer_token_ms")["p50"] == 400.0
    assert _metric(report, "tokens_per_second")["p50"] == 49.0
    assert _metric(report, "model_recovery_duration_ms")["p50"] == 95_000.0
    assert _criterion(report, "lm_link_recovery")["status"] == "PASS"

    events[-1]["reload_count"] = 2
    failed = build_report(
        events,
        session_id="observed-004",
        evidence_kind="observed",
        generated_at_utc="2026-07-15T12:30:00+00:00",
    )
    assert _criterion(failed, "lm_link_recovery")["status"] == "FAIL"

    events[-1]["reload_count"] = 1
    events[-1]["reload_attempts"] = 0
    inconsistent = build_report(
        events,
        session_id="observed-004b",
        evidence_kind="observed",
        generated_at_utc="2026-07-15T12:30:00+00:00",
    )
    assert _criterion(inconsistent, "lm_link_recovery")["status"] == "FAIL"

    events[-1]["reload_attempts"] = 1
    events[-1]["rtx_llm_fallback_used"] = True
    fallback = build_report(
        events,
        session_id="observed-004c",
        evidence_kind="observed",
        generated_at_utc="2026-07-15T12:30:00+00:00",
    )
    assert _criterion(fallback, "lm_link_recovery")["status"] == "FAIL"


def test_initial_load_attempts_have_no_recovery_limit_and_distinct_models_are_valid() -> None:
    events = [
        _metadata(),
        {"event": "request_submitted", "timestamp_ms": 1_000.0, "request_id": "q-1"},
        {"event": "prompt_processing_end", "timestamp_ms": 1_250.0, "request_id": "q-1"},
        {"event": "first_answer_token", "timestamp_ms": 1_400.0, "request_id": "q-1"},
        {
            "event": "chat_end",
            "timestamp_ms": 2_400.0,
            "request_id": "q-1",
            "output_tokens": 50,
        },
        {
            "event": "model_load_completed",
            "duration_ms": 12_000.0,
            "load_attempts": 4,
            "preferred_device_confirmed": True,
            "loaded_instances": 2,
            "same_model_key_selected": False,
            "shared_text_vision_instance": False,
        },
        {
            "event": "recovery_completed",
            "duration_ms": 95_000.0,
            "request_id": "q-2",
            "unload_during_request": True,
            "reload_count": 1,
            "reload_attempts": 2,
            "preferred_device_confirmed": True,
            "latest_only_replayed": True,
            "rtx_llm_fallback_used": False,
        },
    ]

    report = build_report(
        events,
        session_id="observed-distinct-models",
        evidence_kind="observed",
        generated_at_utc="2026-07-15T12:30:00+00:00",
    )

    assert _criterion(report, "lm_link_recovery")["status"] == "PASS"


def test_one_token_chat_is_valid_but_cannot_unlock_lm_acceptance() -> None:
    events = [
        _metadata(),
        {"event": "request_submitted", "timestamp_ms": 1_000.0, "request_id": "q-1"},
        {"event": "prompt_processing_end", "timestamp_ms": 1_250.0, "request_id": "q-1"},
        {"event": "first_answer_token", "timestamp_ms": 1_400.0, "request_id": "q-1"},
        {
            "event": "chat_end",
            "timestamp_ms": 2_400.0,
            "request_id": "q-1",
            "output_tokens": 1,
        },
        {
            "event": "model_load_completed",
            "duration_ms": 12_000.0,
            "load_attempts": 1,
            "preferred_device_confirmed": True,
            "loaded_instances": 1,
            "same_model_key_selected": True,
            "shared_text_vision_instance": True,
        },
        {
            "event": "recovery_completed",
            "duration_ms": 95_000.0,
            "request_id": "q-2",
            "unload_during_request": True,
            "reload_count": 1,
            "reload_attempts": 1,
            "preferred_device_confirmed": True,
            "latest_only_replayed": True,
            "rtx_llm_fallback_used": False,
        },
    ]

    report = build_report(
        events,
        session_id="observed-one-token",
        evidence_kind="observed",
        generated_at_utc="2026-07-15T12:30:00+00:00",
    )

    assert not any(
        record.get("metric") == "tokens_per_second" for record in report
    )
    assert _criterion(report, "lm_link_recovery")["status"] == "NOT_RUN"


def test_zero_output_tokens_are_invalid_evidence() -> None:
    with pytest.raises(BenchmarkInputError, match="greater than zero"):
        build_report(
            [
                {
                    "event": "chat_end",
                    "timestamp_ms": 2_400.0,
                    "request_id": "q-1",
                    "output_tokens": 0,
                }
            ],
            session_id="observed-zero-token",
            evidence_kind="observed",
            generated_at_utc="2026-07-15T12:30:00+00:00",
        )


def test_teams_and_protected_content_observations_are_explicit() -> None:
    events = [
        _metadata(),
        {
            "event": "teams_acceptance",
            "consent_confirmed": True,
            "second_participant": True,
            "entire_primary_display": True,
            "ribbon_interactions_observed": True,
            "stream_answer_observed": True,
            "event_capture_triggered": True,
            "live_overlay_absent": True,
            "desktop_visible_and_live": True,
            "recording_reviewed": True,
            "recording_overlay_absent": True,
            "affinity_diagnostics_captured": True,
            "evidence_ref": "evidence/teams-001",
        },
        {
            "event": "protected_content_acceptance",
            "consent_confirmed": True,
            "legitimate_protected_source": True,
            "protected_status_reported": True,
            "image_sent_to_vlm": False,
            "audio_path_continued": True,
            "manual_text_path_continued": True,
            "notification_observation_recorded": True,
            "notification_observed": False,
            "notification_suppression_attempted": False,
            "protection_bypass_attempted": False,
            "evidence_ref": "evidence/protected-001",
        },
    ]

    report = build_report(
        events,
        session_id="observed-005",
        evidence_kind="observed",
        generated_at_utc="2026-07-15T12:30:00+00:00",
    )

    assert _criterion(report, "teams_full_screen")["status"] == "PASS"
    assert _criterion(report, "protected_content")["status"] == "PASS"

    events[-1]["protection_bypass_attempted"] = True
    bypassed = build_report(
        events,
        session_id="observed-005b",
        evidence_kind="observed",
        generated_at_utc="2026-07-15T12:30:00+00:00",
    )
    assert _criterion(bypassed, "protected_content")["status"] == "FAIL"


def test_teams_black_or_frozen_share_cannot_pass_as_overlay_absent() -> None:
    event = {
        "event": "teams_acceptance",
        "consent_confirmed": True,
        "second_participant": True,
        "entire_primary_display": True,
        "ribbon_interactions_observed": True,
        "stream_answer_observed": True,
        "event_capture_triggered": True,
        "live_overlay_absent": True,
        "desktop_visible_and_live": False,
        "recording_reviewed": True,
        "recording_overlay_absent": True,
        "affinity_diagnostics_captured": True,
        "evidence_ref": "evidence/teams-black-001",
    }

    report = build_report(
        [_metadata(), event],
        session_id="observed-black-share",
        evidence_kind="observed",
        generated_at_utc="2026-07-15T12:30:00+00:00",
    )

    assert _criterion(report, "teams_full_screen")["status"] == "FAIL"


def test_capture_and_shutdown_are_measured_without_assuming_success() -> None:
    events = [
        _metadata(),
        {
            "event": "capture_completed",
            "duration_ms": 40.0,
            "capture_reason": "automatic",
            "overlay_excluded": True,
        },
        {
            "event": "capture_completed",
            "duration_ms": 60.0,
            "capture_reason": "hotkey",
            "overlay_excluded": False,
        },
        {
            "event": "shutdown_completed",
            "duration_ms": 250.0,
            "resources_released": False,
            "evidence_ref": "evidence/shutdown-001",
        },
    ]

    report = build_report(
        events,
        session_id="observed-007",
        evidence_kind="observed",
        generated_at_utc="2026-07-15T12:30:00+00:00",
    )

    assert _metric(report, "capture_duration_ms")["p50"] == 50.0
    assert _criterion(report, "capture_pipeline")["status"] == "FAIL"
    assert _criterion(report, "shutdown_resources")["status"] == "FAIL"
    assert _criterion(report, "overall_acceptance")["status"] == "FAIL"


def test_capture_pipeline_requires_observed_exclusion_evidence() -> None:
    no_capture = build_report(
        [_metadata()],
        session_id="observed-no-capture",
        evidence_kind="observed",
        generated_at_utc="2026-07-15T12:30:00+00:00",
    )
    captured = build_report(
        [
            _metadata(),
            {
                "event": "capture_completed",
                "duration_ms": 40.0,
                "capture_reason": "hotkey",
                "overlay_excluded": True,
            },
        ],
        session_id="observed-capture",
        evidence_kind="observed",
        generated_at_utc="2026-07-15T12:30:00+00:00",
    )

    assert _criterion(no_capture, "capture_pipeline")["status"] == "NOT_RUN"
    assert _criterion(captured, "capture_pipeline")["status"] == "PASS"


def test_word_error_rate_can_exceed_one_due_to_insertions() -> None:
    events = [_metadata(), *_stt_events("ru")]
    for event in events:
        if event["event"] == "final_transcript":
            event["reference_words"] = 5
            event["word_errors"] = 7

    report = build_report(
        events,
        session_id="observed-high-wer",
        evidence_kind="observed",
        generated_at_utc="2026-07-15T12:30:00+00:00",
    )

    assert _metric(report, "stt_word_error_rate", "ru")["value"] == 1.4


def test_duplicate_event_milestone_for_request_is_rejected() -> None:
    duplicate = {
        "event": "first_partial",
        "timestamp_ms": 500.0,
        "request_id": "ru-00",
        "language": "ru",
        "source": "microphone",
    }

    with pytest.raises(BenchmarkInputError, match="duplicate first_partial"):
        build_report(
            [_metadata(), duplicate, {**duplicate, "timestamp_ms": 600.0}],
            session_id="observed-duplicate",
            evidence_kind="observed",
            generated_at_utc="2026-07-15T12:30:00+00:00",
        )


def test_sufficient_stt_timings_without_consent_remain_not_run() -> None:
    metadata = _metadata()
    metadata["consent_confirmed"] = False

    report = build_report(
        [metadata, *_stt_events("ru")],
        session_id="observed-008",
        evidence_kind="observed",
        generated_at_utc="2026-07-15T12:30:00+00:00",
    )

    assert _criterion(report, "stt_ru")["status"] == "NOT_RUN"


@pytest.mark.parametrize(
    ("event_name", "request_id", "timestamp_ms"),
    [
        pytest.param("first_partial", "ru-00", 2_500.0, id="partial-after-final"),
        pytest.param("speech_end", "ru-01", 4_999.0, id="speech-end-before-start"),
    ],
)
def test_incoherent_stt_chronology_cannot_contribute_to_pass(
    event_name: str, request_id: str, timestamp_ms: float
) -> None:
    events = [_metadata(), *_stt_events("ru")]
    target = next(
        event
        for event in events
        if event["event"] == event_name and event.get("request_id") == request_id
    )
    target["timestamp_ms"] = timestamp_ms

    report = build_report(
        events,
        session_id="observed-bad-order",
        evidence_kind="observed",
        generated_at_utc="2026-07-15T12:30:00+00:00",
    )

    assert _criterion(report, "stt_ru")["status"] == "NOT_RUN"
    assert _metric(report, "stt_first_partial_latency_ms", "ru")["sample_count"] == 19


@pytest.mark.parametrize(
    "unsafe",
    [
        {"event": "first_partial", "timestamp_ms": 1.0, "request_id": "x", "text": "secret"},
        {"event": "capture_completed", "duration_ms": 1.0, "image_data": "base64"},
        {"event": "chat_end", "timestamp_ms": 1.0, "request_id": "x", "answer": "secret"},
        {"event": "session_metadata", "api_token": "secret"},
    ],
)
def test_input_rejects_transcript_image_answer_and_secret_fields(
    unsafe: dict[str, object],
) -> None:
    with pytest.raises(BenchmarkInputError, match="forbidden field") as captured:
        build_report(
            [unsafe],
            session_id="safe-id",
            evidence_kind="observed",
            generated_at_utc="2026-07-15T12:30:00+00:00",
        )

    assert "secret" not in str(captured.value)


def test_parser_rejects_invalid_json_without_echoing_input() -> None:
    with pytest.raises(BenchmarkInputError, match="line 1") as captured:
        parse_event_lines(['{"transcript":"do not echo"'])

    assert "do not echo" not in str(captured.value)


def test_parser_rejects_duplicate_json_keys_before_last_value_can_win() -> None:
    line = (
        '{"event":"capture_completed","duration_ms":1,"capture_reason":"hotkey",'
        '"overlay_excluded":false,"overlay_excluded":true}'
    )

    with pytest.raises(BenchmarkInputError, match="duplicate JSON key") as captured:
        parse_event_lines([line])

    message = str(captured.value)
    assert "overlay_excluded" not in message
    assert "false" not in message
    assert "true" not in message


def test_unknown_field_error_never_echoes_attacker_controlled_name_or_value() -> None:
    field = "candidate_private_words_xyz"
    value = "do-not-echo-this-value"

    with pytest.raises(BenchmarkInputError, match="unknown field") as captured:
        build_report(
            [{"event": "session_metadata", field: value}],
            session_id="safe-session",
            evidence_kind="observed",
            generated_at_utc="2026-07-15T12:30:00+00:00",
        )

    message = str(captured.value)
    assert field not in message
    assert value not in message


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("request_id", "candidate answer words", id="request-id"),
        pytest.param("evidence_ref", "evidence/private spoken words", id="evidence-ref"),
    ],
)
def test_identifiers_must_be_opaque_ascii_without_spaces(field: str, value: str) -> None:
    event = (
        {
            "event": "request_submitted",
            "timestamp_ms": 1.0,
            "request_id": value,
        }
        if field == "request_id"
        else {**_metadata(), "evidence_ref": value}
    )

    with pytest.raises(BenchmarkInputError, match="opaque identifier") as captured:
        build_report(
            [event],
            session_id="safe-session",
            evidence_kind="observed",
            generated_at_utc="2026-07-15T12:30:00+00:00",
        )

    assert value not in str(captured.value)


def test_metadata_labels_can_still_describe_hardware_with_spaces() -> None:
    metadata = _metadata()
    metadata["main_gpu"] = "NVIDIA GeForce RTX 5070 Ti"

    report = build_report(
        [metadata],
        session_id="safe-session",
        evidence_kind="observed",
        generated_at_utc="2026-07-15T12:30:00+00:00",
    )

    assert report[1]["main_gpu"] == "NVIDIA GeForce RTX 5070 Ti"


def test_session_id_must_be_an_opaque_token_without_echoing_value() -> None:
    value = "candidate private answer"

    with pytest.raises(BenchmarkInputError, match="opaque identifier") as captured:
        build_report(
            [],
            session_id=value,
            evidence_kind="observed",
            generated_at_utc="2026-07-15T12:30:00+00:00",
        )

    assert value not in str(captured.value)


def test_self_test_cli_writes_safe_metrics_but_no_hardware_passes(tmp_path: Path) -> None:
    report_path = tmp_path / "self-test.jsonl"

    exit_code = main(
        [
            "--mode",
            "self-test",
            "--report",
            str(report_path),
            "--session-id",
            "self-test-001",
            "--generated-at-utc",
            "2026-07-15T12:30:00+00:00",
        ]
    )

    records = [json.loads(line) for line in report_path.read_text(encoding="utf-8").splitlines()]
    assert exit_code == 0
    assert _criterion(records, "harness_self_test")["status"] == "PASS"
    assert _criterion(records, "stt_ru")["status"] == "NOT_RUN"
    assert _criterion(records, "lm_link_recovery")["status"] == "NOT_RUN"
    assert _criterion(records, "capture_pipeline")["status"] == "NOT_RUN"
    assert _criterion(records, "teams_full_screen")["status"] == "NOT_RUN"
    assert _criterion(records, "overall_acceptance")["status"] == "NOT_RUN"
    raw = report_path.read_text(encoding="utf-8").casefold()
    assert "transcript_text" not in raw
    assert "image_data" not in raw


def test_event_input_cli_sanitizes_and_writes_observed_report(tmp_path: Path) -> None:
    input_path = tmp_path / "events.jsonl"
    report_path = tmp_path / "observed.jsonl"
    input_path.write_text(
        "\n".join(json.dumps(event) for event in [_metadata(), *_stt_events("ru")]) + "\n",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "--mode",
            "event-input",
            "--events-input",
            str(input_path),
            "--report",
            str(report_path),
            "--session-id",
            "observed-006",
            "--generated-at-utc",
            "2026-07-15T12:30:00+00:00",
        ]
    )

    records = [json.loads(line) for line in report_path.read_text(encoding="utf-8").splitlines()]
    assert exit_code == 0
    assert _criterion(records, "stt_ru")["status"] == "PASS"
    assert _criterion(records, "stt_en")["status"] == "NOT_RUN"
    assert records[0]["evidence_kind"] == "observed"
