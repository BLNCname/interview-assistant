import json
from collections.abc import AsyncIterator, Mapping
from ipaddress import ip_address

import httpx
from pydantic import ValidationError

from .models import (
    ChatEvent,
    LoadResult,
    ModelDetails,
    ModelSummary,
    NativeModelList,
    OpenAIModelList,
)


DISCOVERY_TIMEOUT_SECONDS = 5.0
REQUEST_TIMEOUT_SECONDS = 120.0
_EXACT_STREAM_EVENTS = {"chat.start", "message.delta", "error", "chat.end"}
_STREAM_EVENT_PREFIXES = ("model_load.", "prompt_processing.", "tool_call.")


def _should_yield_event(event_name: str) -> bool:
    return event_name in _EXACT_STREAM_EVENTS or event_name.startswith(
        _STREAM_EVENT_PREFIXES
    )


class LMStudioProtocolError(RuntimeError):
    """Raised when LM Studio sends a malformed native chat SSE block."""


def _parse_event_payload(
    event_name: str | None,
    data_lines: list[str],
) -> tuple[str, dict[str, object]]:
    if not event_name or not data_lines:
        raise LMStudioProtocolError("Malformed LM Studio chat stream event")

    try:
        payload = json.loads("\n".join(data_lines))
    except (json.JSONDecodeError, TypeError):
        raise LMStudioProtocolError(
            "Malformed LM Studio chat stream event"
        ) from None
    if not isinstance(payload, dict):
        raise LMStudioProtocolError("Malformed LM Studio chat stream event")

    payload_type = payload.get("type")
    if not isinstance(payload_type, str) or payload_type != event_name:
        raise LMStudioProtocolError("Malformed LM Studio chat stream event")
    return event_name, payload


def _validate_chat_event(payload: dict[str, object]) -> ChatEvent:
    try:
        event = ChatEvent.model_validate(payload)
    except ValidationError:
        raise LMStudioProtocolError(
            "Malformed LM Studio chat stream event"
        ) from None
    return event


class LMStudioClient:
    def __init__(self, host: str, port: int, token: str | None) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        url_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
        self._http = httpx.AsyncClient(
            base_url=f"http://{url_host}:{port}",
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )

    async def __aenter__(self) -> "LMStudioClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Release the shared HTTP transport; safe to call during repeated shutdown."""

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

    async def stream_chat(
        self,
        payload: Mapping[str, object],
    ) -> AsyncIterator[ChatEvent]:
        request_payload = dict(payload)
        request_payload["stream"] = True
        async with self._http.stream(
            "POST",
            "/api/v1/chat",
            json=request_payload,
            headers={"Accept": "text/event-stream"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        ) as response:
            response.raise_for_status()
            event_name: str | None = None
            data_lines: list[str] = []
            has_fields = False
            async for line in response.aiter_lines():
                if line == "":
                    if has_fields:
                        parsed_name, payload = _parse_event_payload(
                            event_name, data_lines
                        )
                        if _should_yield_event(parsed_name):
                            yield _validate_chat_event(payload)
                    event_name = None
                    data_lines = []
                    has_fields = False
                    continue
                if line.startswith(":"):
                    continue

                has_fields = True
                field, separator, value = line.partition(":")
                if separator and value.startswith(" "):
                    value = value[1:]
                if field == "event":
                    event_name = value
                elif field == "data":
                    data_lines.append(value)
            if has_fields:
                parsed_name, payload = _parse_event_payload(event_name, data_lines)
                if _should_yield_event(parsed_name):
                    yield _validate_chat_event(payload)


def is_loopback_host(host: str) -> bool:
    normalized = host.strip().strip("[]").rstrip(".").casefold()
    if normalized == "localhost":
        return True
    try:
        return ip_address(normalized).is_loopback
    except ValueError:
        return False
