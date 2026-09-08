import asyncio

from interview_assistant.app import InterviewApplication
from interview_assistant.config import AppConfig
from interview_assistant.diagnostics.readiness import build_readiness_checks
from tests.unit.test_production_probes import _probes
from tests.unit.test_runtime_lifecycle import _runtime, _hypothesis, _ParityCapture


async def test_openrouter_checks_do_not_call_lm_link_or_native_instances():
    def forbidden():
        raise AssertionError("LM Link must not run for OpenRouter")

    probes, _ = _probes(lmlink_status=forbidden)
    probes.config.provider = "openrouter"
    probes.config.openrouter.text_model = "qwen"
    probes.config.openrouter.vision_model = "qwen"
    for name in ("lmlink_status", "preferred_device", "duplicate_instances", "model_discovery"):
        assert (await probes.as_mapping()[name]()).status != "failed"
    await probes.aclose()


def test_lmlink_is_optional_and_shared_warmup_has_same_deadline():
    probes, _ = _probes()
    checks = {c.name: c for c in build_readiness_checks(probes.as_mapping())}
    assert not checks["lmlink_status"].required
    assert checks["streaming_ttft"].timeout_s >= checks["model_load_warmup"].timeout_s


async def test_native_retrieval_uses_only_policy_query_and_cloud_model(qtbot, tmp_path):
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    runtime, services, client = _runtime(app, capture=_ParityCapture(tmp_path / "unused.png"))
    runtime.config.provider = "openrouter"
    runtime.config.openrouter.text_model = "vendor/cloud-model"
    runtime.config.openrouter.vision_model = ""
    calls = []

    class Retrieval:
        async def retrieve(self, integration):
            calls.append(integration)
            return "External test reference: indexes trade writes for faster reads."

        async def aclose(self):
            pass

    services.retrieval = Retrieval()
    services.search_policy.force_next()
    try:
        await runtime.start()
        await runtime.submit_hypothesis(_hypothesis("How do database indexes work?"))
        assert calls and calls[0].query == "How do database indexes work?"
        assert len(client.payloads) == 1
        payload = client.payloads[0]
        assert payload["model"] == "instance:vendor/cloud-model"
        assert "integrations" not in payload
        assert "External test reference" in payload["input"]
        assert payload["system_prompt"]
    finally:
        await runtime.shutdown()


async def test_factory_uses_cloud_client_without_lm_host_or_credentials(qtbot):
    from interview_assistant.composition import build_production_components
    from interview_assistant.providers.openrouter import OpenRouterClient

    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    config = AppConfig(provider="openrouter")
    config.lmstudio.host = "unused.example"
    components = build_production_components(
        app, config, "cloud-key", loop=asyncio.get_running_loop()
    )
    try:
        assert isinstance(components.runtime.services.client, OpenRouterClient)
        assert components.runtime.services.retrieval is not None
    finally:
        await components.aclose()
        app.shutdown()


async def test_text_only_model_does_not_receive_manual_screenshot(qtbot, tmp_path):
    from PIL import Image

    screenshot = tmp_path / "screen.png"
    Image.new("RGB", (8, 8), "white").save(screenshot)
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    runtime, _, client = _runtime(app, capture=_ParityCapture(screenshot))
    runtime.config.lmstudio.vision_model = ""
    try:
        await runtime.start()
        await runtime.capture_manual_screenshot()
        await runtime.submit_hypothesis(_hypothesis("How do database indexes work?"))
        assert client.payloads and isinstance(client.payloads[-1]["input"], str)
    finally:
        await runtime.shutdown()


async def test_disabled_mcp_cannot_be_enabled_by_force_hotkey(qtbot, tmp_path):
    app = InterviewApplication.for_test()
    qtbot.addWidget(app.ribbon)
    runtime, services, client = _runtime(app, capture=_ParityCapture(tmp_path / "unused.png"))
    runtime.config.mcp.backend = "off"
    try:
        await runtime.start()
        services.search_policy.force_next()
        await runtime.submit_hypothesis(_hypothesis("What is the latest PostgreSQL version?"))
        assert len(client.payloads) == 1
        assert "integrations" not in client.payloads[0]
    finally:
        await runtime.shutdown()
