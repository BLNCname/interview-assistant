import json
from pathlib import Path

import httpx
import pytest
import respx
from pydantic import ValidationError

from interview_assistant.lmstudio.client import LMStudioClient
from interview_assistant.lmstudio.models import (
    LoadResult,
    ModelCapabilities,
    ModelLoadConfig,
    NativeModelList,
)


FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "lmstudio_models.json"


@respx.mock
async def test_list_models_uses_openai_compatible_endpoint() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    route = respx.get("http://127.0.0.1:1234/v1/models").mock(
        return_value=httpx.Response(200, json=payload)
    )

    async with LMStudioClient("127.0.0.1", 1234, "") as client:
        models = await client.list_models()

    request = route.calls.last.request
    assert [model.key for model in models] == ["qwen3.5"]
    assert models[0].object == "model"
    assert models[0].owned_by == "organization-owner"
    assert "Authorization" not in request.headers
    assert request.extensions["timeout"]["read"] == 5.0


@respx.mock
async def test_list_model_details_preserves_loaded_instances_and_additive_metadata() -> None:
    route = respx.get("http://lmstudio.local:1234/api/v1/models").mock(
        return_value=httpx.Response(
            200,
            json={
                "models": [
                    {
                        "type": "llm",
                        "publisher": "lmstudio-community",
                        "key": "qwen3.5",
                        "display_name": "Qwen 3.5",
                        "architecture": "qwen3",
                        "quantization": {"name": "Q4_K_M", "bits_per_weight": 4},
                        "size_bytes": 12_345_678,
                        "params_string": "9B",
                        "loaded_instances": [
                            {
                                "id": "qwen3.5:1",
                                "config": {
                                    "context_length": 32_768,
                                    "eval_batch_size": 512,
                                    "parallel": 2,
                                    "flash_attention": True,
                                    "offload_kv_cache_to_gpu": True,
                                    "tensor_allocation": [
                                        {"device": "Strix Halo", "bytes": 10_000_000}
                                    ],
                                },
                                "device": {"name": "Strix Halo", "kind": "lmlink"},
                            }
                        ],
                        "max_context_length": 131_072,
                        "format": "gguf",
                        "capabilities": {
                            "vision": True,
                            "trained_for_tool_use": True,
                            "reasoning": {
                                "allowed_options": ["off", "on"],
                                "default": "on",
                            },
                        },
                        "description": None,
                        "variants": ["qwen3.5@q4_k_m"],
                        "selected_variant": "qwen3.5@q4_k_m",
                        "allocation": {"device": "Strix Halo", "remote": True},
                    },
                    {
                        "type": "embedding",
                        "publisher": "nomic-ai",
                        "key": "nomic-embed-text",
                        "display_name": "Nomic Embed Text",
                        "quantization": None,
                        "size_bytes": 274_290_560,
                        "params_string": None,
                        "loaded_instances": [],
                        "max_context_length": 2_048,
                        "format": "gguf",
                    },
                ],
                "future_response_field": "accepted",
            },
        )
    )

    async with LMStudioClient("lmstudio.local", 1234, "api-token") as client:
        models = await client.list_model_details()

    request = route.calls.last.request
    loaded = models[0].loaded_instances[0]
    assert request.headers["Authorization"] == "Bearer api-token"
    assert request.extensions["timeout"]["read"] == 5.0
    assert models[0].key == "qwen3.5"
    assert models[0].capabilities is not None
    assert models[0].capabilities.vision is True
    assert loaded.instance_id == "qwen3.5:1"
    assert loaded.config.context_length == 32_768
    assert loaded.config.flash_attention is True
    assert models[0].model_extra == {
        "allocation": {"device": "Strix Halo", "remote": True}
    }
    assert loaded.model_extra == {"device": {"name": "Strix Halo", "kind": "lmlink"}}
    assert loaded.config.model_extra == {
        "tensor_allocation": [{"device": "Strix Halo", "bytes": 10_000_000}]
    }
    assert models[1].architecture is None
    assert models[1].capabilities is None


def test_native_model_list_preserves_additive_envelope_metadata() -> None:
    payload = NativeModelList.model_validate(
        {
            "models": [],
            "request_metadata": {"server": "lmstudio", "node": "main-pc"},
        }
    )

    assert payload.model_extra == {
        "request_metadata": {"server": "lmstudio", "node": "main-pc"}
    }


@respx.mock
async def test_load_model_posts_explicit_configuration_and_preserves_instance_id() -> None:
    route = respx.post("http://127.0.0.1:1234/api/v1/models/load").mock(
        return_value=httpx.Response(
            200,
            json={
                "type": "llm",
                "instance_id": "qwen3.5:loaded",
                "load_time_seconds": 9.099,
                "status": "loaded",
                "load_config": {
                    "context_length": 16_384,
                    "eval_batch_size": 512,
                    "flash_attention": False,
                    "offload_kv_cache_to_gpu": False,
                    "num_experts": 4,
                    "tensor_allocation": [{"device": "Strix Halo", "bytes": 9_000_000}],
                },
                "allocation": {"device": "Strix Halo", "remote": True},
            },
        )
    )

    async with LMStudioClient("127.0.0.1", 1234, None) as client:
        result = await client.load_model(
            "qwen3.5",
            context_length=16_384,
            flash_attention=False,
            offload_kv_cache_to_gpu=False,
        )

    request = route.calls.last.request
    assert json.loads(request.content) == {
        "model": "qwen3.5",
        "context_length": 16_384,
        "flash_attention": False,
        "offload_kv_cache_to_gpu": False,
        "echo_load_config": True,
    }
    assert request.extensions["timeout"]["read"] == 120.0
    assert result.instance_id == "qwen3.5:loaded"
    assert result.status == "loaded"
    assert result.load_config is not None
    assert result.load_config.num_experts == 4
    assert result.model_extra == {
        "allocation": {"device": "Strix Halo", "remote": True}
    }
    assert result.load_config.model_extra == {
        "tensor_allocation": [{"device": "Strix Halo", "bytes": 9_000_000}]
    }


@respx.mock
async def test_load_model_accepts_a_key_without_explicit_configuration() -> None:
    route = respx.post("http://127.0.0.1:1234/api/v1/models/load").mock(
        return_value=httpx.Response(
            200,
            json={
                "type": "llm",
                "instance_id": "qwen3.5",
                "load_time_seconds": 1,
                "status": "loaded",
            },
        )
    )

    async with LMStudioClient("127.0.0.1", 1234, None) as client:
        result = await client.load_model("qwen3.5")

    assert json.loads(route.calls.last.request.content) == {
        "model": "qwen3.5",
        "echo_load_config": True,
    }
    assert result.instance_id == "qwen3.5"
    assert result.load_config is None


@pytest.mark.parametrize(
    "payload",
    [
        {"vision": True},
        {"trained_for_tool_use": True},
        {"vision": "true", "trained_for_tool_use": True},
        {"vision": True, "trained_for_tool_use": 1},
    ],
)
def test_capabilities_require_both_strict_booleans(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ModelCapabilities.model_validate(payload)


@pytest.mark.parametrize(
    ("contract", "payload"),
    [
        (ModelLoadConfig, {"context_length": "32768"}),
        (
            LoadResult,
            {
                "type": "llm",
                "instance_id": "qwen3.5",
                "load_time_seconds": "9.099",
                "status": "loaded",
            },
        ),
    ],
)
def test_known_numeric_fields_reject_string_coercion(
    contract: type[ModelLoadConfig] | type[LoadResult],
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        contract.model_validate(payload)


@pytest.mark.parametrize(
    ("operation", "method", "path"),
    [
        ("list_models", "GET", "/v1/models"),
        ("list_model_details", "GET", "/api/v1/models"),
        ("load_model", "POST", "/api/v1/models/load"),
    ],
)
@respx.mock
async def test_every_response_raises_http_errors_without_exposing_token(
    operation: str,
    method: str,
    path: str,
) -> None:
    token = "do-not-leak-this-token"
    respx.request(method, f"http://127.0.0.1:1234{path}").mock(
        return_value=httpx.Response(503, json={"error": "unavailable"})
    )

    with pytest.raises(httpx.HTTPStatusError) as error:
        async with LMStudioClient("127.0.0.1", 1234, token) as client:
            if operation == "list_models":
                await client.list_models()
            elif operation == "list_model_details":
                await client.list_model_details()
            else:
                await client.load_model("qwen3.5")

    assert token not in str(error.value)


@respx.mock
async def test_context_manager_closes_the_async_client() -> None:
    client = LMStudioClient("127.0.0.1", 1234, None)

    async with client:
        pass

    with pytest.raises(RuntimeError, match="client has been closed"):
        await client.list_models()
