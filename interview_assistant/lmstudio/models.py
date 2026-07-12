from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LMStudioContract(BaseModel):
    """Strict contract that preserves additive LM Studio response metadata."""

    model_config = ConfigDict(extra="allow", populate_by_name=True, strict=True)


class ModelSummary(LMStudioContract):
    key: str = Field(alias="id")
    object: str | None = None
    owned_by: str | None = None


class OpenAIModelList(LMStudioContract):
    object: Literal["list"]
    data: list[ModelSummary]


class QuantizationInfo(LMStudioContract):
    name: str | None = None
    bits_per_weight: float | None = None


class ReasoningCapabilities(LMStudioContract):
    allowed_options: list[str]
    default: str


class ModelCapabilities(LMStudioContract):
    vision: bool
    trained_for_tool_use: bool
    reasoning: ReasoningCapabilities | None = None


class ModelLoadConfig(LMStudioContract):
    context_length: int
    eval_batch_size: int | None = None
    parallel: int | None = None
    flash_attention: bool | None = None
    num_experts: int | None = None
    offload_kv_cache_to_gpu: bool | None = None


class LoadedModelInstance(LMStudioContract):
    instance_id: str = Field(alias="id")
    config: ModelLoadConfig


class ModelDetails(LMStudioContract):
    type: Literal["llm", "embedding"]
    publisher: str
    key: str
    display_name: str
    architecture: str | None = None
    quantization: QuantizationInfo | None = None
    size_bytes: int
    params_string: str | None = None
    loaded_instances: list[LoadedModelInstance]
    max_context_length: int
    format: Literal["gguf", "mlx"] | None = None
    capabilities: ModelCapabilities | None = None
    description: str | None = None
    variants: list[str] | None = None
    selected_variant: str | None = None


class NativeModelList(LMStudioContract):
    models: list[ModelDetails]


class LoadResult(LMStudioContract):
    type: Literal["llm", "embedding"]
    instance_id: str
    load_time_seconds: float
    status: Literal["loaded"]
    load_config: ModelLoadConfig | None = None


class ModelInstance(LMStudioContract):
    """Application lifecycle view of a loaded model, populated by the registry."""

    key: str
    instance_id: str
    state: str
    device_name: str | None = None
