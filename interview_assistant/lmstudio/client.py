import httpx

from .models import LoadResult, ModelDetails, ModelSummary, NativeModelList, OpenAIModelList


DISCOVERY_TIMEOUT_SECONDS = 5.0
REQUEST_TIMEOUT_SECONDS = 120.0


class LMStudioClient:
    def __init__(self, host: str, port: int, token: str | None) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._http = httpx.AsyncClient(
            base_url=f"http://{host}:{port}",
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )

    async def __aenter__(self) -> "LMStudioClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._http.aclose()

    async def list_models(self) -> list[ModelSummary]:
        response = await self._http.get("/v1/models", timeout=DISCOVERY_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = OpenAIModelList.model_validate(response.json())
        return payload.data

    async def list_model_details(self) -> list[ModelDetails]:
        response = await self._http.get("/api/v1/models", timeout=DISCOVERY_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = NativeModelList.model_validate(response.json())
        return payload.models

    async def load_model(
        self,
        key: str,
        *,
        context_length: int | None = None,
        flash_attention: bool | None = None,
        offload_kv_cache_to_gpu: bool | None = None,
    ) -> LoadResult:
        payload: dict[str, object] = {
            "model": key,
            "echo_load_config": True,
        }
        if context_length is not None:
            payload["context_length"] = context_length
        if flash_attention is not None:
            payload["flash_attention"] = flash_attention
        if offload_kv_cache_to_gpu is not None:
            payload["offload_kv_cache_to_gpu"] = offload_kv_cache_to_gpu

        response = await self._http.post(
            "/api/v1/models/load",
            json=payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return LoadResult.model_validate(response.json())
