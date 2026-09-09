"""Provider failures must stay actionable without displaying external error text."""

import httpx
import pytest
import respx

from interview_assistant.diagnostics.readiness import (
    ProbeOutcome,
    ReadinessCheck,
    ReadinessRunner,
)
from interview_assistant.lmstudio.models import ChatError
from interview_assistant.providers.openrouter import OpenRouterClient, OpenRouterError


@respx.mock
async def test_missing_key_is_reported_as_unconfigured_before_any_request():
    async with OpenRouterClient(None) as client:
        async def probe():
            await client.validate_credentials()
            return ProbeOutcome("ready", "Validated")

        report = await ReadinessRunner((
            ReadinessCheck("lmstudio_auth", True, 1, "Check provider", probe),
        )).run()
    assert "not configured" in report.by_name("lmstudio_auth").message
    assert not report.can_start
    assert len(respx.calls) == 0


@pytest.mark.parametrize(
    ("status", "meaning"),
    [(401, "API key"), (402, "credits"), (403, "denied"), (404, "endpoint"),
     (429, "rate limit"), (503, "unavailable")],
)
@respx.mock
async def test_http_failure_reaches_readiness_with_an_actionable_safe_reason(status, meaning):
    respx.get("https://openrouter.ai/api/v1/key").respond(
        status, json={"error": {"message": "Bearer private-fixture-secret"}},
    )
    async with OpenRouterClient("private-fixture-secret") as client:
        async def probe():
            await client.validate_credentials()
            return ProbeOutcome("ready", "Validated")

        report = await ReadinessRunner((
            ReadinessCheck("lmstudio_auth", True, 1, "Check provider", probe),
        )).run()
    failure = report.by_name("lmstudio_auth")
    assert not report.can_start
    assert meaning.casefold() in failure.message.casefold()
    assert "OpenRouter" in failure.message
    assert "private-fixture-secret" not in repr(report)


@respx.mock
async def test_connection_failure_is_identifiable_without_exposing_transport_details():
    respx.get("https://openrouter.ai/api/v1/key").mock(
        side_effect=httpx.ConnectError("private-fixture-secret at private-host"),
    )
    async with OpenRouterClient("private-fixture-secret") as client:
        async def probe():
            await client.validate_credentials()
            return ProbeOutcome("ready", "Validated")

        report = await ReadinessRunner((
            ReadinessCheck("lmstudio_auth", True, 1, "Check provider", probe),
        )).run()
    assert "connection" in report.by_name("lmstudio_auth").message.casefold()
    assert "private-fixture-secret" not in repr(report)
    assert "private-host" not in repr(report)


@pytest.mark.parametrize("code", ["authentication_error", "private-fixture-secret", None])
async def test_typed_provider_exception_does_not_make_its_arbitrary_text_safe(code):
    async def probe():
        raise OpenRouterError(ChatError(
            type="invalid_request", code=code, message="Bearer private-fixture-secret",
        ))

    report = await ReadinessRunner((
        ReadinessCheck("lmstudio_auth", True, 1, "Check provider", probe),
    )).run()
    assert not report.can_start
    assert "private-fixture-secret" not in repr(report)
    if code == "authentication_error":
        assert "API key" in report.by_name("lmstudio_auth").message


@respx.mock
async def test_paid_model_rejection_survives_warmup_and_readiness_without_raw_provider_text():
    from interview_assistant.composition import _warm_model
    from interview_assistant.lmstudio.models import ModelInstance

    respx.post("https://openrouter.ai/api/v1/chat/completions").respond(
        402, json={"error": {"message": "private-fixture-secret"}},
    )
    async with OpenRouterClient("private-fixture-secret") as client:
        async def probe():
            await _warm_model(client, ModelInstance(
                key="vendor/model", instance_id="vendor/model", state="ready",
            ))
            return ProbeOutcome("ready", "Warm")

        report = await ReadinessRunner((
            ReadinessCheck("model_load_warmup", True, 1, "Check model", probe),
        )).run()
    assert "credits" in report.by_name("model_load_warmup").message
    assert not report.can_start
    assert "private-fixture-secret" not in repr(report)
