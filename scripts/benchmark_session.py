"""Privacy-preserving benchmark recorder for hardware acceptance sessions.

The recorder deliberately accepts only a small metadata/timing schema.  It never
accepts transcript text, prompts, answers, audio samples, screenshots, or image
payloads.  ``self-test`` exercises the report pipeline with deterministic synthetic
timings; synthetic data can never satisfy a hardware acceptance criterion.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from enum import StrEnum
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile


SCHEMA_VERSION = 1
MIN_STT_SAMPLES_PER_LANGUAGE = 20
FIRST_PARTIAL_P95_LIMIT_MS = 1_000.0
FINALIZATION_P95_LIMIT_MS = 800.0


class BenchmarkInputError(ValueError):
    """Raised when input could contain content or cannot be used as evidence."""


class Outcome(StrEnum):
    NOT_RUN = "NOT_RUN"
    FAIL = "FAIL"
    PASS = "PASS"


_FORBIDDEN_FIELDS = {
    "answer",
    "api_key",
    "api_token",
    "audio",
    "audio_samples",
    "base64",
    "content",
    "image",
    "image_data",
    "partial_text",
    "prompt",
    "samples",
    "screenshot",
    "secret",
    "text",
    "transcript",
    "transcript_text",
}

_EVENT_REQUIRED: dict[str, set[str]] = {
    "session_metadata": {
        "observed_at_utc",
        "consent_confirmed",
        "evidence_ref",
    },
    "audio_start": {"timestamp_ms", "request_id", "language", "source"},
    "speech_end": {"timestamp_ms", "request_id", "language", "source"},
    "first_partial": {"timestamp_ms", "request_id", "language", "source"},
    "final_transcript": {
        "timestamp_ms",
        "request_id",
        "language",
        "source",
        "reference_words",
        "word_errors",
    },
    "request_submitted": {"timestamp_ms", "request_id"},
    "prompt_processing_end": {"timestamp_ms", "request_id"},
    "first_answer_token": {"timestamp_ms", "request_id"},
    "chat_end": {"timestamp_ms", "request_id", "output_tokens"},
    "capture_completed": {"duration_ms", "capture_reason", "overlay_excluded"},
    "model_load_completed": {
        "duration_ms",
        "load_attempts",
        "preferred_device_confirmed",
        "loaded_instances",
        "same_model_key_selected",
        "shared_text_vision_instance",
    },
    "recovery_completed": {
        "duration_ms",
        "request_id",
        "unload_during_request",
        "reload_count",
        "reload_attempts",
        "preferred_device_confirmed",
        "latest_only_replayed",
        "rtx_llm_fallback_used",
    },
    "teams_acceptance": {
        "consent_confirmed",
        "second_participant",
        "entire_primary_display",
        "ribbon_interactions_observed",
        "stream_answer_observed",
        "event_capture_triggered",
        "live_overlay_absent",
        "desktop_visible_and_live",
        "recording_reviewed",
        "recording_overlay_absent",
        "affinity_diagnostics_captured",
        "evidence_ref",
    },
    "protected_content_acceptance": {
        "consent_confirmed",
        "legitimate_protected_source",
        "protected_status_reported",
        "image_sent_to_vlm",
        "audio_path_continued",
        "manual_text_path_continued",
        "notification_observation_recorded",
        "notification_observed",
        "notification_suppression_attempted",
        "protection_bypass_attempted",
        "evidence_ref",
    },
    "shutdown_completed": {"duration_ms", "resources_released", "evidence_ref"},
}

_EVENT_OPTIONAL: dict[str, set[str]] = {
    "session_metadata": {
        "app_version",
        "windows_version",
        "teams_version",
        "lm_studio_version",
        "stt_model",
        "llm_model",
        "main_gpu",
        "inference_device",
        "display_count",
        "display_scale_percent",
    },
    "capture_completed": {"timestamp_ms", "request_id"},
    "model_load_completed": {"timestamp_ms"},
    "recovery_completed": {"timestamp_ms"},
    "teams_acceptance": {"timestamp_ms"},
    "protected_content_acceptance": {"timestamp_ms"},
    "shutdown_completed": {"timestamp_ms"},
}

_BOOL_FIELDS = {
    "consent_confirmed",
    "overlay_excluded",
    "preferred_device_confirmed",
    "shared_text_vision_instance",
    "same_model_key_selected",
    "unload_during_request",
    "latest_only_replayed",
    "rtx_llm_fallback_used",
    "second_participant",
    "entire_primary_display",
    "ribbon_interactions_observed",
    "stream_answer_observed",
    "event_capture_triggered",
    "live_overlay_absent",
    "desktop_visible_and_live",
    "recording_reviewed",
    "recording_overlay_absent",
    "affinity_diagnostics_captured",
    "legitimate_protected_source",
    "protected_status_reported",
    "image_sent_to_vlm",
    "audio_path_continued",
    "manual_text_path_continued",
    "notification_observation_recorded",
    "notification_observed",
    "notification_suppression_attempted",
    "protection_bypass_attempted",
    "resources_released",
}
_INTEGER_FIELDS = {
    "reference_words",
    "word_errors",
    "output_tokens",
    "load_attempts",
    "reload_attempts",
    "reload_count",
    "loaded_instances",
    "display_count",
}
_NUMBER_FIELDS = {"timestamp_ms", "duration_ms", "display_scale_percent"}
_METADATA_LABEL_FIELDS = {
    "app_version",
    "windows_version",
    "teams_version",
    "lm_studio_version",
    "stt_model",
    "llm_model",
    "main_gpu",
    "inference_device",
}
_SAFE_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/\\@+() -]{0,199}\Z")
_SAFE_OPAQUE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}\Z")
_SAFE_EVIDENCE_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/\\-]{0,159}\Z")


class _DuplicateJSONKey(ValueError):
    pass


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJSONKey
        result[key] = value
    return result


def _utc_timestamp(value: str, *, field: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise BenchmarkInputError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise BenchmarkInputError(f"{field} must include a UTC offset")
    return parsed.astimezone(timezone.utc).isoformat()


def _validate_number(field: str, value: object) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BenchmarkInputError(f"{field} must be a non-negative finite number")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise BenchmarkInputError(f"{field} must be a non-negative finite number")
    return value


def _float_field(item: Mapping[str, object], field: str) -> float:
    value = item[field]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AssertionError(f"validated numeric field has invalid type: {field}")
    return float(value)


def _int_field(item: Mapping[str, object], field: str) -> int:
    value = item[field]
    if isinstance(value, bool) or not isinstance(value, int):
        raise AssertionError(f"validated integer field has invalid type: {field}")
    return value


def _validate_event(raw: Mapping[str, object]) -> dict[str, object]:
    for field in raw:
        if field.casefold() in _FORBIDDEN_FIELDS:
            raise BenchmarkInputError(f"forbidden field: {field}")

    event = raw.get("event")
    if not isinstance(event, str) or event not in _EVENT_REQUIRED:
        raise BenchmarkInputError("event must be a supported metadata/timing event")

    allowed = {"event", *_EVENT_REQUIRED[event], *_EVENT_OPTIONAL.get(event, set())}
    unknown = set(raw) - allowed
    if unknown:
        raise BenchmarkInputError("event contains an unknown field")
    missing = _EVENT_REQUIRED[event] - set(raw)
    if missing:
        raise BenchmarkInputError(f"missing field: {sorted(missing)[0]}")

    clean: dict[str, object] = {"event": event}
    for field in sorted(set(raw) - {"event"}):
        value = raw[field]
        if field in _BOOL_FIELDS:
            if not isinstance(value, bool):
                raise BenchmarkInputError(f"{field} must be boolean")
        elif field in _INTEGER_FIELDS:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise BenchmarkInputError(f"{field} must be a non-negative integer")
        elif field in _NUMBER_FIELDS:
            value = _validate_number(field, value)
        elif field == "observed_at_utc":
            if not isinstance(value, str):
                raise BenchmarkInputError("observed_at_utc must be an ISO-8601 timestamp")
            value = _utc_timestamp(value, field=field)
        elif field == "language":
            if value not in {"ru", "en"}:
                raise BenchmarkInputError("language must be ru or en")
        elif field == "source":
            if value not in {"microphone", "system", "interviewer", "you"}:
                raise BenchmarkInputError("source must identify one supported audio source")
        elif field == "capture_reason":
            if value not in {"automatic", "hotkey"}:
                raise BenchmarkInputError("capture_reason must be automatic or hotkey")
        elif field == "request_id":
            if not isinstance(value, str) or _SAFE_OPAQUE_ID.fullmatch(value) is None:
                raise BenchmarkInputError("request_id must be an opaque identifier")
        elif field == "evidence_ref":
            if not isinstance(value, str) or _SAFE_EVIDENCE_REF.fullmatch(value) is None:
                raise BenchmarkInputError("evidence_ref must be an opaque identifier")
        elif field in _METADATA_LABEL_FIELDS:
            if not isinstance(value, str) or _SAFE_LABEL.fullmatch(value) is None:
                raise BenchmarkInputError(f"{field} must be a short metadata label")
        else:  # pragma: no cover - every schema field belongs to a type group
            raise BenchmarkInputError(f"unsupported field schema: {field}")
        clean[field] = value

    if event == "final_transcript" and _int_field(clean, "reference_words") == 0:
        raise BenchmarkInputError("reference_words must be greater than zero")
    if event == "chat_end" and _int_field(clean, "output_tokens") == 0:
        raise BenchmarkInputError("output_tokens must be greater than zero")
    return clean


def parse_event_lines(lines: Iterable[str]) -> list[dict[str, object]]:
    """Parse JSONL without ever embedding source lines or values in errors."""

    events: list[dict[str, object]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line, object_pairs_hook=_reject_duplicate_json_keys)
        except _DuplicateJSONKey as exc:
            raise BenchmarkInputError(f"duplicate JSON key on line {line_number}") from exc
        except json.JSONDecodeError as exc:
            raise BenchmarkInputError(f"invalid JSON on line {line_number}") from exc
        if not isinstance(raw, dict):
            raise BenchmarkInputError(f"line {line_number} must be a JSON object")
        try:
            events.append(_validate_event(raw))
        except BenchmarkInputError as exc:
            raise BenchmarkInputError(f"line {line_number}: {exc}") from exc
    return events


def _percentile(values: Sequence[float], percent: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("cannot calculate a percentile without samples")
    position = (len(ordered) - 1) * percent / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + ((ordered[upper] - ordered[lower]) * fraction)


def _distribution_metric(
    name: str,
    values: Sequence[float],
    *,
    unit: str,
    language: str | None = None,
) -> dict[str, object] | None:
    if not values:
        return None
    result: dict[str, object] = {
        "record_type": "metric",
        "metric": name,
    }
    if language is not None:
        result["language"] = language
    result.update(
        {
            "sample_count": len(values),
            "p50": round(_percentile(values, 50), 3),
            "p95": round(_percentile(values, 95), 3),
            "unit": unit,
        }
    )
    return result


def _criterion(name: str, status: Outcome, reason: str) -> dict[str, object]:
    return {
        "record_type": "criterion",
        "criterion": name,
        "status": status.value,
        "reason": reason,
    }


def _metadata_has(metadata: Mapping[str, object] | None, fields: set[str]) -> bool:
    return metadata is not None and metadata.get("consent_confirmed") is True and fields <= metadata.keys()


def _correlate(events: Sequence[Mapping[str, object]], event: str) -> dict[str, Mapping[str, object]]:
    return {
        str(item["request_id"]): item
        for item in events
        if item["event"] == event and "request_id" in item
    }


def _reject_duplicate_request_events(events: Sequence[Mapping[str, object]]) -> None:
    seen: set[tuple[object, object]] = set()
    for item in events:
        if "request_id" not in item:
            continue
        identity = (item["event"], item["request_id"])
        if identity in seen:
            raise BenchmarkInputError(f"duplicate {item['event']} event for request_id")
        seen.add(identity)


def _duration(start: Mapping[str, object], end: Mapping[str, object]) -> float | None:
    duration = _float_field(end, "timestamp_ms") - _float_field(start, "timestamp_ms")
    return duration if duration >= 0 else None


def _stt_records(
    events: Sequence[Mapping[str, object]],
    metadata: Mapping[str, object] | None,
    *,
    synthetic: bool,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    starts = _correlate(events, "audio_start")
    partials = _correlate(events, "first_partial")
    ends = _correlate(events, "speech_end")
    finals = _correlate(events, "final_transcript")
    metrics: list[dict[str, object]] = []
    criteria: list[dict[str, object]] = []

    metadata_fields = {
        "observed_at_utc",
        "app_version",
        "windows_version",
        "stt_model",
        "main_gpu",
        "evidence_ref",
    }
    for language in ("ru", "en"):
        partial_latencies: list[float] = []
        final_latencies: list[float] = []
        total_reference_words = 0
        total_word_errors = 0
        complete_ids: set[str] = set()
        for request_id, final in finals.items():
            if final.get("language") != language:
                continue
            start = starts.get(request_id)
            partial = partials.get(request_id)
            speech_end = ends.get(request_id)
            if start is None or partial is None or speech_end is None:
                continue
            if any(
                item.get("language") != language or item.get("source") != final.get("source")
                for item in (start, partial, speech_end)
            ):
                continue
            start_timestamp = _float_field(start, "timestamp_ms")
            partial_timestamp = _float_field(partial, "timestamp_ms")
            speech_end_timestamp = _float_field(speech_end, "timestamp_ms")
            final_timestamp = _float_field(final, "timestamp_ms")
            if not (
                start_timestamp <= speech_end_timestamp <= final_timestamp
                and start_timestamp <= partial_timestamp <= final_timestamp
            ):
                continue
            partial_latency = _duration(start, partial)
            final_latency = _duration(speech_end, final)
            if partial_latency is None or final_latency is None:
                continue
            partial_latencies.append(partial_latency)
            final_latencies.append(final_latency)
            total_reference_words += _int_field(final, "reference_words")
            total_word_errors += _int_field(final, "word_errors")
            complete_ids.add(request_id)

        for item in (
            _distribution_metric(
                "stt_first_partial_latency_ms", partial_latencies, unit="ms", language=language
            ),
            _distribution_metric(
                "stt_finalization_after_pause_ms", final_latencies, unit="ms", language=language
            ),
        ):
            if item is not None:
                metrics.append(item)
        if total_reference_words:
            metrics.append(
                {
                    "record_type": "metric",
                    "metric": "stt_word_error_rate",
                    "language": language,
                    "sample_count": len(complete_ids),
                    "value": round(total_word_errors / total_reference_words, 6),
                    "unit": "ratio",
                }
            )

        name = f"stt_{language}"
        if synthetic:
            criteria.append(
                _criterion(name, Outcome.NOT_RUN, "synthetic timings are not hardware evidence")
            )
        elif not _metadata_has(metadata, metadata_fields) or len(complete_ids) < MIN_STT_SAMPLES_PER_LANGUAGE:
            criteria.append(
                _criterion(
                    name,
                    Outcome.NOT_RUN,
                    f"requires consent, dated hardware metadata, and {MIN_STT_SAMPLES_PER_LANGUAGE} complete utterances",
                )
            )
        else:
            partial_p95 = _percentile(partial_latencies, 95)
            final_p95 = _percentile(final_latencies, 95)
            passed = (
                partial_p95 <= FIRST_PARTIAL_P95_LIMIT_MS
                and final_p95 <= FINALIZATION_P95_LIMIT_MS
            )
            criteria.append(
                _criterion(
                    name,
                    Outcome.PASS if passed else Outcome.FAIL,
                    "latency thresholds satisfied" if passed else "one or more latency thresholds missed",
                )
            )
    return metrics, criteria


def _lm_records(
    events: Sequence[Mapping[str, object]],
    metadata: Mapping[str, object] | None,
    *,
    synthetic: bool,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    submitted = _correlate(events, "request_submitted")
    prompt_ends = _correlate(events, "prompt_processing_end")
    first_tokens = _correlate(events, "first_answer_token")
    chat_ends = _correlate(events, "chat_end")
    prompt_durations: list[float] = []
    ttft: list[float] = []
    decode_start: list[float] = []
    chat_durations: list[float] = []
    token_rates: list[float] = []

    for request_id, chat_end in chat_ends.items():
        request = submitted.get(request_id)
        prompt_end = prompt_ends.get(request_id)
        first_token = first_tokens.get(request_id)
        if request is None or prompt_end is None or first_token is None:
            continue
        prompt = _duration(request, prompt_end)
        first = _duration(request, first_token)
        decode = _duration(prompt_end, first_token)
        chat = _duration(request, chat_end)
        decode_duration = _duration(first_token, chat_end)
        if prompt is None or first is None or decode is None or chat is None or decode_duration is None:
            continue
        prompt_durations.append(prompt)
        ttft.append(first)
        decode_start.append(decode)
        chat_durations.append(chat)
        output_tokens = _int_field(chat_end, "output_tokens")
        if output_tokens >= 2 and decode_duration > 0:
            token_rates.append((output_tokens - 1) / (decode_duration / 1_000.0))

    model_loads = [
        _float_field(item, "duration_ms")
        for item in events
        if item["event"] == "model_load_completed"
    ]
    recoveries = [
        _float_field(item, "duration_ms")
        for item in events
        if item["event"] == "recovery_completed"
    ]
    metrics = [
        item
        for item in (
            _distribution_metric("prompt_processing_ms", prompt_durations, unit="ms"),
            _distribution_metric("request_to_first_answer_token_ms", ttft, unit="ms"),
            _distribution_metric("prompt_end_to_first_answer_token_ms", decode_start, unit="ms"),
            _distribution_metric("chat_duration_ms", chat_durations, unit="ms"),
            _distribution_metric("tokens_per_second", token_rates, unit="tokens/s"),
            _distribution_metric("model_load_duration_ms", model_loads, unit="ms"),
            _distribution_metric("model_recovery_duration_ms", recoveries, unit="ms"),
        )
        if item is not None
    ]

    if synthetic:
        return metrics, _criterion(
            "lm_link_recovery", Outcome.NOT_RUN, "synthetic timings are not LM Link evidence"
        )
    metadata_fields = {
        "observed_at_utc",
        "app_version",
        "lm_studio_version",
        "llm_model",
        "inference_device",
        "evidence_ref",
    }
    loads = [item for item in events if item["event"] == "model_load_completed"]
    recovery_events = [item for item in events if item["event"] == "recovery_completed"]
    if not _metadata_has(metadata, metadata_fields) or not token_rates or not loads or not recovery_events:
        return metrics, _criterion(
            "lm_link_recovery",
            Outcome.NOT_RUN,
            "requires a measured chat, model load, controlled unload, and recovery observation",
        )

    load_observations_pass = all(
        _int_field(item, "load_attempts") >= 1
        and item["preferred_device_confirmed"] is True
        and (
            (
                item["same_model_key_selected"] is True
                and item["loaded_instances"] == 1
                and item["shared_text_vision_instance"] is True
            )
            or (
                item["same_model_key_selected"] is False
                and item["loaded_instances"] == 2
                and item["shared_text_vision_instance"] is False
            )
        )
        for item in loads
    )
    recovery_observations_pass = all(
        item["unload_during_request"] is True
        and item["reload_count"] == 1
        and 1 <= _int_field(item, "reload_attempts") <= 3
        and item["preferred_device_confirmed"] is True
        and item["latest_only_replayed"] is True
        and item["rtx_llm_fallback_used"] is False
        for item in recovery_events
    )
    passed = load_observations_pass and recovery_observations_pass
    return metrics, _criterion(
        "lm_link_recovery",
        Outcome.PASS if passed else Outcome.FAIL,
        "single reload and latest-only replay verified"
        if passed
        else "reload, device, instance, or replay observation failed",
    )


def _capture_metrics(events: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    durations = [
        _float_field(item, "duration_ms")
        for item in events
        if item["event"] == "capture_completed"
    ]
    metric = _distribution_metric("capture_duration_ms", durations, unit="ms")
    return [] if metric is None else [metric]


def _capture_criterion(
    events: Sequence[Mapping[str, object]],
    metadata: Mapping[str, object] | None,
    *,
    synthetic: bool,
) -> dict[str, object]:
    if synthetic:
        return _criterion(
            "capture_pipeline", Outcome.NOT_RUN, "synthetic capture is not Windows evidence"
        )
    observations = [item for item in events if item["event"] == "capture_completed"]
    fields = {"observed_at_utc", "app_version", "windows_version", "evidence_ref"}
    if not _metadata_has(metadata, fields) or not observations:
        return _criterion(
            "capture_pipeline",
            Outcome.NOT_RUN,
            "requires consent, dated Windows metadata, and an observed event capture",
        )
    passed = all(item["overlay_excluded"] is True for item in observations)
    return _criterion(
        "capture_pipeline",
        Outcome.PASS if passed else Outcome.FAIL,
        "Ribbon excluded from every observed event capture"
        if passed
        else "Ribbon appeared in one or more observed event captures",
    )


def _teams_criterion(
    events: Sequence[Mapping[str, object]],
    metadata: Mapping[str, object] | None,
    *,
    synthetic: bool,
) -> dict[str, object]:
    if synthetic:
        return _criterion(
            "teams_full_screen", Outcome.NOT_RUN, "self-test does not run Microsoft Teams"
        )
    observations = [item for item in events if item["event"] == "teams_acceptance"]
    fields = {"observed_at_utc", "windows_version", "teams_version", "evidence_ref"}
    if not _metadata_has(metadata, fields) or not observations:
        return _criterion(
            "teams_full_screen",
            Outcome.NOT_RUN,
            "requires consent, versions, second-participant live view, and recording evidence",
        )
    expected_true = _EVENT_REQUIRED["teams_acceptance"] - {"evidence_ref"}
    passed = all(all(item[field] is True for field in expected_true) for item in observations)
    return _criterion(
        "teams_full_screen",
        Outcome.PASS if passed else Outcome.FAIL,
        "overlay absent in live full-screen share and recording"
        if passed
        else "one or more required Teams observations failed",
    )


def _protected_criterion(
    events: Sequence[Mapping[str, object]],
    metadata: Mapping[str, object] | None,
    *,
    synthetic: bool,
) -> dict[str, object]:
    if synthetic:
        return _criterion(
            "protected_content",
            Outcome.NOT_RUN,
            "self-test does not open protected third-party content",
        )
    observations = [item for item in events if item["event"] == "protected_content_acceptance"]
    fields = {"observed_at_utc", "windows_version", "evidence_ref"}
    if not _metadata_has(metadata, fields) or not observations:
        return _criterion(
            "protected_content",
            Outcome.NOT_RUN,
            "requires a consented protected-source and notification observation",
        )
    passed = all(
        item["consent_confirmed"] is True
        and item["legitimate_protected_source"] is True
        and item["protected_status_reported"] is True
        and item["image_sent_to_vlm"] is False
        and item["audio_path_continued"] is True
        and item["manual_text_path_continued"] is True
        and item["notification_observation_recorded"] is True
        and item["notification_suppression_attempted"] is False
        and item["protection_bypass_attempted"] is False
        for item in observations
    )
    return _criterion(
        "protected_content",
        Outcome.PASS if passed else Outcome.FAIL,
        "protected frame stopped before VLM and notification behavior was only observed"
        if passed
        else "protected-content fallback or no-suppression policy failed",
    )


def _shutdown_criterion(
    events: Sequence[Mapping[str, object]], *, synthetic: bool
) -> dict[str, object]:
    if synthetic:
        return _criterion(
            "shutdown_resources", Outcome.NOT_RUN, "synthetic events are not resource evidence"
        )
    observations = [item for item in events if item["event"] == "shutdown_completed"]
    if not observations:
        return _criterion(
            "shutdown_resources", Outcome.NOT_RUN, "no observed shutdown evidence supplied"
        )
    passed = all(item["resources_released"] is True for item in observations)
    return _criterion(
        "shutdown_resources",
        Outcome.PASS if passed else Outcome.FAIL,
        "resource release observed" if passed else "resource release observation failed",
    )


def build_report(
    raw_events: Sequence[Mapping[str, object]],
    *,
    session_id: str,
    evidence_kind: str,
    generated_at_utc: str | None = None,
) -> list[dict[str, object]]:
    """Validate events and build JSONL-ready event, metric, and criterion records."""

    if _SAFE_OPAQUE_ID.fullmatch(session_id) is None:
        raise BenchmarkInputError("session_id must be an opaque identifier")
    if evidence_kind not in {"observed", "synthetic"}:
        raise BenchmarkInputError("evidence_kind must be observed or synthetic")
    generated = _utc_timestamp(
        generated_at_utc or datetime.now(timezone.utc).isoformat(), field="generated_at_utc"
    )
    events = [_validate_event(event) for event in raw_events]
    _reject_duplicate_request_events(events)
    metadata_events = [event for event in events if event["event"] == "session_metadata"]
    if len(metadata_events) > 1:
        raise BenchmarkInputError("only one session_metadata event is allowed")
    metadata = metadata_events[0] if metadata_events else None
    synthetic = evidence_kind == "synthetic"

    report: list[dict[str, object]] = [
        {
            "record_type": "session",
            "schema_version": SCHEMA_VERSION,
            "session_id": session_id,
            "evidence_kind": evidence_kind,
            "generated_at_utc": generated,
            "event_count": len(events),
        }
    ]
    report.extend({"record_type": "event", **event} for event in events)

    stt_metrics, stt_criteria = _stt_records(events, metadata, synthetic=synthetic)
    lm_metrics, lm_criterion = _lm_records(events, metadata, synthetic=synthetic)
    report.extend(stt_metrics)
    report.extend(lm_metrics)
    report.extend(_capture_metrics(events))
    criteria = [
        *stt_criteria,
        lm_criterion,
        _capture_criterion(events, metadata, synthetic=synthetic),
        _teams_criterion(events, metadata, synthetic=synthetic),
        _protected_criterion(events, metadata, synthetic=synthetic),
        _shutdown_criterion(events, synthetic=synthetic),
    ]
    if synthetic:
        criteria.append(
            _criterion(
                "harness_self_test",
                Outcome.PASS,
                "schema, aggregation, privacy filtering, and report writing exercised",
            )
        )

    required = [item["status"] for item in criteria if item["criterion"] != "harness_self_test"]
    if Outcome.FAIL.value in required:
        overall = Outcome.FAIL
        overall_reason = "at least one observed acceptance criterion failed"
    elif required and all(status == Outcome.PASS.value for status in required):
        overall = Outcome.PASS
        overall_reason = "all required observed acceptance criteria passed"
    else:
        overall = Outcome.NOT_RUN
        overall_reason = "one or more required criteria lack observed evidence"
    criteria.append(_criterion("overall_acceptance", overall, overall_reason))
    report.extend(criteria)
    return report


def _synthetic_events() -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for language_index, language in enumerate(("ru", "en")):
        for index in range(MIN_STT_SAMPLES_PER_LANGUAGE):
            request_id = f"demo-{language}-{index:02d}"
            base = float((language_index * 100_000) + (index * 4_000))
            common = {
                "request_id": request_id,
                "language": language,
                "source": "microphone",
            }
            events.extend(
                [
                    {"event": "audio_start", "timestamp_ms": base, **common},
                    {"event": "first_partial", "timestamp_ms": base + 480 + index, **common},
                    {"event": "speech_end", "timestamp_ms": base + 1_500, **common},
                    {
                        "event": "final_transcript",
                        "timestamp_ms": base + 1_980 + index,
                        "reference_words": 10,
                        "word_errors": index % 2,
                        **common,
                    },
                ]
            )
    events.extend(
        [
            {"event": "request_submitted", "timestamp_ms": 250_000.0, "request_id": "demo-q"},
            {
                "event": "prompt_processing_end",
                "timestamp_ms": 250_280.0,
                "request_id": "demo-q",
            },
            {
                "event": "first_answer_token",
                "timestamp_ms": 250_420.0,
                "request_id": "demo-q",
            },
            {
                "event": "chat_end",
                "timestamp_ms": 251_420.0,
                "request_id": "demo-q",
                "output_tokens": 60,
            },
            {
                "event": "capture_completed",
                "duration_ms": 42.0,
                "capture_reason": "hotkey",
                "overlay_excluded": True,
            },
            {
                "event": "model_load_completed",
                "duration_ms": 10_000.0,
                "load_attempts": 1,
                "preferred_device_confirmed": True,
                "loaded_instances": 1,
                "same_model_key_selected": True,
                "shared_text_vision_instance": True,
            },
            {
                "event": "recovery_completed",
                "duration_ms": 90_000.0,
                "request_id": "demo-recovery",
                "unload_during_request": True,
                "reload_count": 1,
                "reload_attempts": 1,
                "preferred_device_confirmed": True,
                "latest_only_replayed": True,
                "rtx_llm_fallback_used": False,
            },
        ]
    )
    return events


def _write_jsonl(path: Path, records: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as handle:
            temporary_name = handle.name
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("self-test", "event-input"), default="self-test")
    parser.add_argument("--events-input", help="observed JSONL path, or - for stdin")
    parser.add_argument("--report", type=Path, required=True, help="destination JSONL report")
    parser.add_argument("--session-id", default="benchmark-self-test")
    parser.add_argument("--generated-at-utc", help="override report time for reproducible tests")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.mode == "self-test":
        if args.events_input is not None:
            raise BenchmarkInputError("--events-input is valid only in event-input mode")
        events = _synthetic_events()
        evidence_kind = "synthetic"
    else:
        if args.events_input is None:
            raise BenchmarkInputError("event-input mode requires --events-input")
        if args.events_input == "-":
            events = parse_event_lines(sys.stdin)
        else:
            with Path(args.events_input).open(encoding="utf-8") as handle:
                events = parse_event_lines(handle)
        evidence_kind = "observed"

    report = build_report(
        events,
        session_id=args.session_id,
        evidence_kind=evidence_kind,
        generated_at_utc=args.generated_at_utc,
    )
    _write_jsonl(args.report, report)
    print(args.report)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BenchmarkInputError as exc:
        print(f"benchmark input error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
