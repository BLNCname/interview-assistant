import asyncio
from collections.abc import Mapping

import pytest

from interview_assistant.lmstudio.models import LoadResult, ModelDetails


def _loaded_instance(
    instance_id: str,
    *,
    instance_extra: Mapping[str, object] | None = None,
    config_extra: Mapping[str, object] | None = None,
) -> dict[str, object]:
    return {
        "id": instance_id,
        "config": {"context_length": 32_768, **dict(config_extra or {})},
        **dict(instance_extra or {}),
    }


def _model_details(
    key: str,
    *instances: dict[str, object],
) -> ModelDetails:
    return ModelDetails.model_validate(
        {
            "type": "llm",
            "publisher": "test",
            "key": key,
            "display_name": key,
            "size_bytes": 1,
            "loaded_instances": list(instances),
            "max_context_length": 131_072,
        }
    )


def _load_result(
    key: str,
    *,
    result_extra: Mapping[str, object] | None = None,
    config_extra: Mapping[str, object] | None = None,
) -> LoadResult:
    payload: dict[str, object] = {
        "type": "llm",
        "instance_id": f"{key}:loaded",
        "load_time_seconds": 1.0,
        "status": "loaded",
        **dict(result_extra or {}),
    }
    if config_extra is not None:
        payload["load_config"] = {
            "context_length": 32_768,
            **dict(config_extra),
        }
    return LoadResult.model_validate(payload)


class FakeLMStudioClient:
    def __init__(
        self,
        discoveries: list[list[ModelDetails]],
        *,
        load_results: Mapping[str, LoadResult] | None = None,
    ) -> None:
        self._discoveries = discoveries
        self._load_results = dict(load_results or {})
        self.list_calls = 0
        self.load_calls: list[str] = []

    async def list_model_details(self) -> list[ModelDetails]:
        index = min(self.list_calls, len(self._discoveries) - 1)
        self.list_calls += 1
        return self._discoveries[index]

    async def load_model(self, key: str) -> LoadResult:
        self.load_calls.append(key)
        await asyncio.sleep(0)
        return self._load_results.get(key, _load_result(key))


async def test_same_text_and_vision_key_load_once() -> None:
    from interview_assistant.lmstudio.registry import ModelRegistry

    client = FakeLMStudioClient([[], []])
    registry = ModelRegistry(client)

    text, vision = await asyncio.gather(
        registry.ensure_ready("qwen3.5"),
        registry.ensure_ready("qwen3.5"),
    )

    assert text is vision
    assert text.instance_id == "qwen3.5:loaded"
    assert client.load_calls == ["qwen3.5"]


async def test_reuses_exactly_one_discovered_instance_without_loading() -> None:
    from interview_assistant.lmstudio.registry import ModelRegistry

    client = FakeLMStudioClient(
        [[_model_details("qwen3.5", _loaded_instance("qwen3.5:existing"))]]
    )

    instance = await ModelRegistry(client).ensure_ready("qwen3.5")

    assert instance.instance_id == "qwen3.5:existing"
    assert instance.state == "ready"
    assert client.load_calls == []


async def test_rejects_duplicate_discovered_instances_without_unloading() -> None:
    from interview_assistant.lmstudio.registry import (
        DuplicateModelInstancesError,
        ModelRegistry,
    )

    client = FakeLMStudioClient(
        [
            [
                _model_details(
                    "qwen3.5",
                    _loaded_instance("qwen3.5:one"),
                    _loaded_instance("qwen3.5:two"),
                )
            ]
        ]
    )

    with pytest.raises(DuplicateModelInstancesError) as error:
        await ModelRegistry(client).ensure_ready("qwen3.5")

    assert error.value.key == "qwen3.5"
    assert error.value.instance_ids == ("qwen3.5:one", "qwen3.5:two")
    assert client.load_calls == []


@pytest.mark.parametrize(
    ("instance_extra", "config_extra"),
    [
        ({"device_name": "Strix Halo"}, None),
        ({"allocation": {"device": "Strix Halo"}}, None),
        (None, {"device_name": "Strix Halo"}),
        (None, {"allocation": {"device": "Strix Halo"}}),
    ],
)
async def test_accepts_preferred_device_from_each_discovery_source(
    instance_extra: Mapping[str, object] | None,
    config_extra: Mapping[str, object] | None,
) -> None:
    from interview_assistant.lmstudio.registry import ModelRegistry

    discovered = _loaded_instance(
        "qwen3.5:existing",
        instance_extra=instance_extra,
        config_extra=config_extra,
    )
    client = FakeLMStudioClient([[_model_details("qwen3.5", discovered)]])

    instance = await ModelRegistry(client).ensure_ready("qwen3.5")

    assert instance.device_name == "Strix Halo"


@pytest.mark.parametrize(
    ("result_extra", "config_extra"),
    [
        ({"device_name": "Strix Halo"}, None),
        ({"allocation": {"device": "Strix Halo"}}, None),
        (None, {"device_name": "Strix Halo"}),
        (None, {"allocation": {"device": "Strix Halo"}}),
    ],
)
async def test_accepts_preferred_device_from_each_load_source(
    result_extra: Mapping[str, object] | None,
    config_extra: Mapping[str, object] | None,
) -> None:
    from interview_assistant.lmstudio.registry import ModelRegistry

    client = FakeLMStudioClient(
        [[], []],
        load_results={
            "qwen3.5": _load_result(
                "qwen3.5",
                result_extra=result_extra,
                config_extra=config_extra,
            )
        },
    )

    instance = await ModelRegistry(client).ensure_ready("qwen3.5")

    assert instance.device_name == "Strix Halo"


@pytest.mark.parametrize("origin", ["discovery", "load"])
async def test_absent_device_metadata_is_allowed(origin: str) -> None:
    from interview_assistant.lmstudio.registry import ModelRegistry

    if origin == "discovery":
        discoveries = [[_model_details("qwen3.5", _loaded_instance("qwen3.5:existing"))]]
    else:
        discoveries = [[], []]

    instance = await ModelRegistry(FakeLMStudioClient(discoveries)).ensure_ready("qwen3.5")

    assert instance.device_name is None


@pytest.mark.parametrize("origin", ["discovery", "load"])
@pytest.mark.parametrize("preference", ["omitted", "config-default", "whitespace"])
async def test_default_registry_accepts_another_gpu_and_preserves_reported_device(
    origin: str, preference: str,
) -> None:
    from interview_assistant.config import AppConfig
    from interview_assistant.lmstudio.registry import ModelRegistry

    if origin == "discovery":
        client = FakeLMStudioClient([[_model_details("qwen", _loaded_instance(
            "qwen:existing", instance_extra={"device_name": "RTX 5070 Ti"},
        ))]])
    else:
        client = FakeLMStudioClient([[], []], load_results={
            "qwen": _load_result("qwen", result_extra={"device_name": "RTX 5070 Ti"}),
        })
    if preference == "omitted":
        registry = ModelRegistry(client)
    else:
        selected = (
            AppConfig().lmstudio.preferred_device_name if preference == "config-default" else "  "
        )
        registry = ModelRegistry(client, preferred_device_name=selected)

    instance = await registry.ensure_ready("qwen")

    assert instance.state == "ready"
    assert instance.device_name == "RTX 5070 Ti"


@pytest.mark.parametrize("origin", ["discovery", "load"])
async def test_rejects_unexpected_device_from_discovery_or_load(origin: str) -> None:
    from interview_assistant.lmstudio.registry import (
        ModelRegistry,
        UnexpectedModelDeviceError,
    )

    if origin == "discovery":
        client = FakeLMStudioClient(
            [
                [
                    _model_details(
                        "qwen3.5",
                        _loaded_instance(
                            "qwen3.5:existing",
                            instance_extra={"device_name": "RTX 5070 Ti"},
                        ),
                    )
                ]
            ]
        )
    else:
        client = FakeLMStudioClient(
            [[], []],
            load_results={
                "qwen3.5": _load_result(
                    "qwen3.5",
                    result_extra={"allocation": {"device": "RTX 5070 Ti"}},
                )
            },
        )

    with pytest.raises(UnexpectedModelDeviceError) as error:
        await ModelRegistry(client, preferred_device_name="Strix Halo").ensure_ready("qwen3.5")

    assert error.value.key == "qwen3.5"
    assert error.value.expected_device_name == "Strix Halo"
    assert error.value.actual_device_name == "RTX 5070 Ti"


@pytest.mark.parametrize("origin", ["discovery", "load"])
async def test_validates_all_present_device_sources(origin: str) -> None:
    from interview_assistant.lmstudio.registry import (
        ModelRegistry,
        UnexpectedModelDeviceError,
    )

    if origin == "discovery":
        client = FakeLMStudioClient(
            [
                [
                    _model_details(
                        "qwen3.5",
                        _loaded_instance(
                            "qwen3.5:existing",
                            instance_extra={"device_name": "Strix Halo"},
                            config_extra={"allocation": {"device": "RTX 5070 Ti"}},
                        ),
                    )
                ]
            ]
        )
    else:
        client = FakeLMStudioClient(
            [[], []],
            load_results={
                "qwen3.5": _load_result(
                    "qwen3.5",
                    result_extra={"device_name": "Strix Halo"},
                    config_extra={"allocation": {"device": "RTX 5070 Ti"}},
                )
            },
        )

    with pytest.raises(UnexpectedModelDeviceError):
        await ModelRegistry(client, preferred_device_name="Strix Halo").ensure_ready("qwen3.5")


async def test_ignores_lookalike_undocumented_device_metadata() -> None:
    from interview_assistant.lmstudio.registry import ModelRegistry

    client = FakeLMStudioClient(
        [
            [
                _model_details(
                    "qwen3.5",
                    _loaded_instance(
                        "qwen3.5:existing",
                        instance_extra={"device": {"name": "Strix Halo"}},
                        config_extra={
                            "tensor_allocation": [{"device": "Strix Halo", "bytes": 1}]
                        },
                    ),
                )
            ]
        ]
    )

    instance = await ModelRegistry(client).ensure_ready("qwen3.5")

    assert instance.device_name is None


async def test_invalidate_removes_cached_instance() -> None:
    from interview_assistant.lmstudio.registry import ModelRegistry

    client = FakeLMStudioClient([[], [], [], []])
    registry = ModelRegistry(client)

    first = await registry.ensure_ready("qwen3.5")
    registry.invalidate("qwen3.5")
    second = await registry.ensure_ready("qwen3.5")

    assert first.instance_id == second.instance_id
    assert client.load_calls == ["qwen3.5", "qwen3.5"]


async def test_refresh_reloads_after_external_unload_without_deadlock() -> None:
    from interview_assistant.lmstudio.registry import ModelRegistry

    client = FakeLMStudioClient(
        [
            [_model_details("qwen3.5", _loaded_instance("qwen3.5:old"))],
            [],
            [],
        ]
    )
    registry = ModelRegistry(client)
    cached = await registry.ensure_ready("qwen3.5")

    refreshed = await asyncio.wait_for(registry.refresh("qwen3.5"), timeout=0.5)

    assert cached.instance_id == "qwen3.5:old"
    assert refreshed.instance_id == "qwen3.5:loaded"
    assert client.load_calls == ["qwen3.5"]


async def test_ensure_ready_accepts_positional_refresh_flag() -> None:
    from interview_assistant.lmstudio.registry import ModelRegistry

    client = FakeLMStudioClient([[], [], [], []])
    registry = ModelRegistry(client)
    await registry.ensure_ready("qwen3.5")

    await registry.ensure_ready("qwen3.5", True)

    assert client.load_calls == ["qwen3.5", "qwen3.5"]


async def test_post_load_rediscovery_enriches_current_instance_metadata() -> None:
    from interview_assistant.lmstudio.registry import ModelRegistry

    client = FakeLMStudioClient(
        [
            [],
            [
                _model_details(
                    "qwen3.5",
                    _loaded_instance(
                        "qwen3.5:loaded",
                        config_extra={"allocation": {"device": "Strix Halo"}},
                    ),
                )
            ],
        ]
    )

    instance = await ModelRegistry(client).ensure_ready("qwen3.5")

    assert instance.instance_id == "qwen3.5:loaded"
    assert instance.device_name == "Strix Halo"
    assert client.list_calls == 2


async def test_post_load_rediscovery_rejects_different_instance_identity() -> None:
    import interview_assistant.lmstudio.registry as registry_module

    client = FakeLMStudioClient(
        [
            [],
            [
                _model_details(
                    "qwen3.5",
                    _loaded_instance("qwen3.5:rediscovered"),
                )
            ],
        ]
    )

    with pytest.raises(RuntimeError) as captured:
        await registry_module.ModelRegistry(client).ensure_ready("qwen3.5")

    assert type(captured.value).__name__ == "ModelInstanceIdentityError"
    assert captured.value.key == "qwen3.5"
    assert captured.value.loaded_instance_id == "qwen3.5:loaded"
    assert captured.value.discovered_instance_id == "qwen3.5:rediscovered"


async def test_different_model_keys_do_not_block_each_other() -> None:
    from interview_assistant.lmstudio.registry import ModelRegistry

    first_discovery_started = asyncio.Event()
    release_first_discovery = asyncio.Event()

    class IndependentlyBlockingClient:
        def __init__(self) -> None:
            self.list_calls = 0
            self.load_calls: list[str] = []

        async def list_model_details(self) -> list[ModelDetails]:
            self.list_calls += 1
            if self.list_calls == 1:
                first_discovery_started.set()
                await release_first_discovery.wait()
            return []

        async def load_model(self, key: str) -> LoadResult:
            self.load_calls.append(key)
            return _load_result(key)

    client = IndependentlyBlockingClient()
    registry = ModelRegistry(client)
    first = asyncio.create_task(registry.ensure_ready("model-a"))
    await first_discovery_started.wait()

    second = asyncio.create_task(registry.ensure_ready("model-b"))
    second_result = await asyncio.wait_for(second, timeout=0.5)

    assert second_result.instance_id == "model-b:loaded"
    assert "model-b" in client.load_calls
    assert not first.done()

    release_first_discovery.set()
    await first


async def test_failed_load_is_not_cached_and_next_attempt_retries() -> None:
    from interview_assistant.lmstudio.registry import ModelRegistry

    class FailsOnceClient:
        def __init__(self) -> None:
            self.load_calls: list[str] = []

        async def list_model_details(self) -> list[ModelDetails]:
            return []

        async def load_model(self, key: str) -> LoadResult:
            self.load_calls.append(key)
            if len(self.load_calls) == 1:
                raise RuntimeError("temporary load failure")
            return _load_result(key)

    client = FailsOnceClient()
    registry = ModelRegistry(client)

    with pytest.raises(RuntimeError, match="temporary load failure"):
        await registry.ensure_ready("qwen3.5")

    instance = await registry.ensure_ready("qwen3.5")

    assert instance.instance_id == "qwen3.5:loaded"
    assert client.load_calls == ["qwen3.5", "qwen3.5"]


@pytest.mark.parametrize(
    ("failure", "expected_error_name"),
    [
        ("load", "RuntimeError"),
        ("duplicate", "DuplicateModelInstancesError"),
        ("device", "UnexpectedModelDeviceError"),
        ("identity", "ModelInstanceIdentityError"),
    ],
)
async def test_failed_refresh_discards_stale_ready_cache_before_plain_retry(
    failure: str,
    expected_error_name: str,
) -> None:
    import interview_assistant.lmstudio.registry as registry_module

    old = [_model_details("qwen3.5", _loaded_instance("qwen3.5:old"))]
    fresh = [_model_details("qwen3.5", _loaded_instance("qwen3.5:fresh"))]
    load_outcomes: list[LoadResult | BaseException] = []
    if failure == "load":
        discoveries = [old, [], fresh]
        load_outcomes.append(RuntimeError("load failed"))
    elif failure == "duplicate":
        discoveries = [
            old,
            [
                _model_details(
                    "qwen3.5",
                    _loaded_instance("qwen3.5:one"),
                    _loaded_instance("qwen3.5:two"),
                )
            ],
            fresh,
        ]
    elif failure == "device":
        discoveries = [
            old,
            [
                _model_details(
                    "qwen3.5",
                    _loaded_instance(
                        "qwen3.5:wrong-device",
                        instance_extra={"device_name": "unexpected"},
                    ),
                )
            ],
            fresh,
        ]
    else:
        discoveries = [
            old,
            [],
            [_model_details("qwen3.5", _loaded_instance("qwen3.5:other"))],
            fresh,
        ]
        load_outcomes.append(_load_result("qwen3.5"))

    class RefreshFailureClient:
        def __init__(self) -> None:
            self.list_calls = 0
            self.load_calls = 0

        async def list_model_details(self) -> list[ModelDetails]:
            result = discoveries[self.list_calls]
            self.list_calls += 1
            return result

        async def load_model(self, _key: str) -> LoadResult:
            self.load_calls += 1
            outcome = load_outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

    client = RefreshFailureClient()
    registry = registry_module.ModelRegistry(client, preferred_device_name="Strix Halo")
    cached = await registry.ensure_ready("qwen3.5")
    assert cached.instance_id == "qwen3.5:old"

    with pytest.raises(RuntimeError) as captured:
        await registry.refresh("qwen3.5")

    assert type(captured.value).__name__ == expected_error_name

    retried = await registry.ensure_ready("qwen3.5")

    assert retried.instance_id == "qwen3.5:fresh"
    assert client.list_calls == len(discoveries)


async def test_invalidate_during_ensure_prevents_cache_resurrection() -> None:
    import interview_assistant.lmstudio.registry as registry_module

    discovery_started = asyncio.Event()
    release_discovery = asyncio.Event()

    class InvalidatedClient:
        def __init__(self) -> None:
            self.list_calls = 0

        async def list_model_details(self) -> list[ModelDetails]:
            self.list_calls += 1
            if self.list_calls == 1:
                discovery_started.set()
                await release_discovery.wait()
                return []
            if self.list_calls == 2:
                return []
            return [
                _model_details("qwen3.5", _loaded_instance("qwen3.5:fresh"))
            ]

        async def load_model(self, key: str) -> LoadResult:
            return _load_result(key)

    client = InvalidatedClient()
    registry = registry_module.ModelRegistry(client)
    ensuring = asyncio.create_task(registry.ensure_ready("qwen3.5"))
    await discovery_started.wait()

    registry.invalidate("qwen3.5")
    release_discovery.set()

    with pytest.raises(RuntimeError) as captured:
        await ensuring

    assert type(captured.value).__name__ == "ModelRegistryInvalidatedError"
    assert captured.value.key == "qwen3.5"

    retried = await registry.ensure_ready("qwen3.5")

    assert retried.instance_id == "qwen3.5:fresh"
    assert client.list_calls == 3
