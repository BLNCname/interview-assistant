"""OpenRouter Chat Completions adapter; independent of LM Studio processes.

Protocol references:
https://openrouter.ai/docs/api/reference/streaming
https://openrouter.ai/docs/guides/overview/models
"""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import AsyncIterator, Mapping
from typing import Literal

import httpx
from pydantic import ValidationError

from interview_assistant.lmstudio.client import is_loopback_host
from interview_assistant.lmstudio.models import (
    ChatError,
    ChatEvent,
    ModelCapabilities,
    ModelDetails,
    ModelInstance,
    ModelSummary,
)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DISCOVERY_TIMEOUT_SECONDS = 10.0


class OpenRouterError(RuntimeError):
    """An actionable failure that never includes raw payloads or credentials."""

    def __init__(self, error: ChatError) -> None:
        self.error = error
        super().__init__(error.message)


def _error(
    code: str,
    message: str,
    *,
    kind: Literal["internal_error", "invalid_request", "model_not_found"] = "internal_error",
) -> OpenRouterError:
    return OpenRouterError(ChatError(type=kind, code=code, message=message))


def _status_error(status: int) -> OpenRouterError:
    if status == 401:
        return _error(
            "authentication_error",
            "OpenRouter API key is missing or invalid.",
            kind="invalid_request",
        )
    if status == 402:
        return _error(
            "insufficient_credits",
            "OpenRouter account has insufficient credits.",
            kind="invalid_request",
        )
    if status == 403:
        return _error(
            "permission_denied",
            "OpenRouter denied this request or API key.",
            kind="invalid_request",
        )
    if status == 404:
        return _error(
            "model_not_found",
            "OpenRouter model is unavailable. Check the model ID.",
            kind="model_not_found",
        )
    if status == 408:
        return _error("timeout", "OpenRouter request timed out. Try again.")
    if status == 429:
        return _error("rate_limit", "OpenRouter rate limit reached. Wait before trying again.")
    if 400 <= status < 500:
        return _error(
            "invalid_request",
            "OpenRouter rejected the model or request parameters.",
            kind="invalid_request",
        )
    return _error("provider_unavailable", "OpenRouter provider is unavailable. Try again later.")


def _response_error(value: object) -> OpenRouterError:
    if isinstance(value, Mapping):
        metadata = value.get("metadata")
        error_type = metadata.get("error_type") if isinstance(metadata, Mapping) else None
        status = (
            {
                "authentication": 401,
                "payment_required": 402,
                "permission_denied": 403,
                "not_found": 404,
                "rate_limit_exceeded": 429,
                "provider_overloaded": 503,
                "invalid_request": 400,
                "invalid_prompt": 400,
                "context_length_exceeded": 400,
            }.get(error_type)
            if isinstance(error_type, str)
            else None
        )
        if status is not None:
            return _status_error(status)
        code = value.get("code")
        if isinstance(code, int) or (isinstance(code, str) and code.isdecimal()):
            return _status_error(int(code))
    return _status_error(502)


def _invalid_response() -> OpenRouterError:
    return _error("invalid_response", "OpenRouter returned an invalid response.")


def _chat_request(payload: Mapping[str, object], max_tokens: int, temperature: float) -> dict:
    if payload.get("integrations"):
        raise _error(
            "invalid_request",
            "LM Studio integrations cannot be sent to OpenRouter. "
            "Configure application MCP retrieval instead.",
            kind="invalid_request",
        )
    model = payload.get("model")
    if not isinstance(model, str) or not model.strip():
        raise _error("invalid_request", "Select an OpenRouter model ID.", kind="invalid_request")
    value = payload.get("input")
    content: str | list[dict]
    if isinstance(value, str) and value.strip():
        content = value
    elif isinstance(value, list) and value:
        content = []
        for part in value:
            if not isinstance(part, Mapping):
                raise _error("invalid_request", "Invalid message input.", kind="invalid_request")
            if part.get("type") in ("message", "text") and isinstance(part.get("content"), str):
                content.append({"type": "text", "text": part["content"]})
            elif part.get("type") == "image" and isinstance(part.get("data_url"), str):
                url = part["data_url"]
                if not url.startswith(
                    ("data:image/png;base64,", "data:image/jpeg;base64,", "data:image/webp;base64,")
                ):
                    raise _error(
                        "invalid_request",
                        "Images must be inline PNG, JPEG, or WebP data.",
                        kind="invalid_request",
                    )
                content.append({"type": "image_url", "image_url": {"url": url}})
            else:
                raise _error(
                    "invalid_request", "Unsupported message input.", kind="invalid_request"
                )
        if not any(part["type"] == "image_url" or part["text"].strip() for part in content):
            raise _error(
                "invalid_request", "Message input must not be empty.", kind="invalid_request"
            )
    else:
        raise _error("invalid_request", "Message input must not be empty.", kind="invalid_request")
    messages: list[dict[str, object]] = []
    system_prompt = payload.get("system_prompt")
    if system_prompt is not None:
        if not isinstance(system_prompt, str):
            raise _error("invalid_request", "Invalid system prompt.", kind="invalid_request")
        if system_prompt.strip():
            messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": content})
    return {
        "model": model.strip(),
        "messages": messages,
        "stream": True,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }


async def _sse_data(response: httpx.Response) -> AsyncIterator[str]:
    data: list[str] = []
    async for line in response.aiter_lines():
        if not line:
            if data:
                yield "\n".join(data)
                data.clear()
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if field == "data" and separator:
            data.append(value[1:] if value.startswith(" ") else value)
    if data:
        yield "\n".join(data)


class OpenRouterClient:
    def __init__(
        self,
        token: str | None,
        *,
        base_url: str = OPENROUTER_BASE_URL,
        timeout_seconds: float = 120.0,
        max_tokens: int = 1200,
        temperature: float = 0.55,
    ) -> None:
        url = httpx.URL(base_url)
        if (
            url.scheme not in ("http", "https")
            or not url.host
            or url.userinfo
            or url.query
            or url.fragment
            or (url.scheme == "http" and not is_loopback_host(url.host))
        ):
            raise ValueError("OpenRouter requires HTTPS (HTTP is allowed for loopback tests only).")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("OpenRouter timeout must be positive and finite.")
        if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens <= 0:
            raise ValueError("OpenRouter max_tokens must be a positive integer.")
        if not math.isfinite(temperature) or not 0 <= temperature <= 2:
            raise ValueError("OpenRouter temperature must be between 0 and 2.")
        token = token.strip() if token else ""
        self._has_token = bool(token)
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {token}"} if token else {},
            timeout=timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        )

    async def __aenter__(self) -> OpenRouterClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    def _require_token(self) -> None:
        if not self._has_token:
            raise _status_error(401)

    async def _get_json(self, path: str) -> dict:
        self._require_token()
        try:
            response = await self._http.get(path, timeout=DISCOVERY_TIMEOUT_SECONDS)
            if not response.is_success:
                raise _status_error(response.status_code)
            value = response.json()
            if not isinstance(value, dict):
                raise _invalid_response()
            if "error" in value:
                raise _response_error(value["error"])
            return value
        except httpx.TimeoutException:
            raise _status_error(408) from None
        except httpx.RequestError:
            raise _error(
                "connection_error", "Cannot connect to OpenRouter. Check the network."
            ) from None
        except (ValueError, ValidationError):
            raise _invalid_response() from None

    async def list_models(self) -> list[ModelSummary]:
        payload = await self._get_json("models")
        entries = payload.get("data")
        if not isinstance(entries, list):
            raise _invalid_response()
        try:
            models = [ModelSummary.model_validate(entry) for entry in entries]
        except ValidationError:
            raise _invalid_response() from None
        if any(not model.key.strip() for model in models):
            raise _invalid_response()
        return models

    async def validate_credentials(self) -> None:
        """Verify the key through the authenticated endpoint without paid generation."""
        payload = await self._get_json("key")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise _invalid_response()
        if data.get("is_management_key") is True or data.get("is_provisioning_key") is True:
            raise _error(
                "permission_denied",
                "Use an OpenRouter inference API key, not a management key.",
                kind="invalid_request",
            )

    async def list_model_details(self) -> list[ModelDetails]:
        result = []
        for model in await self.list_models():
            extra = model.model_extra or {}
            architecture = extra.get("architecture")
            architecture = architecture if isinstance(architecture, dict) else {}
            modalities = architecture.get("input_modalities", [])
            parameters = extra.get("supported_parameters", [])
            context_length = extra.get("context_length", 0)
            if not isinstance(context_length, int) or isinstance(context_length, bool):
                raise _invalid_response()
            display_name = extra.get("name", model.key)
            description = extra.get("description")
            if not isinstance(display_name, str) or (
                description is not None and not isinstance(description, str)
            ):
                raise _invalid_response()
            result.append(
                ModelDetails(
                    type="llm",
                    publisher=model.key.partition("/")[0],
                    key=model.key,
                    display_name=display_name,
                    max_context_length=context_length,
                    size_bytes=0,
                    loaded_instances=[],
                    description=description,
                    capabilities=ModelCapabilities(
                        vision=isinstance(modalities, list) and "image" in modalities,
                        trained_for_tool_use=isinstance(parameters, list) and "tools" in parameters,
                    ),
                )
            )
        return result

    async def stream_chat(self, payload: Mapping[str, object]) -> AsyncIterator[ChatEvent]:
        try:
            self._require_token()
            request = _chat_request(payload, self._max_tokens, self._temperature)
            async with self._http.stream(
                "POST",
                "chat/completions",
                json=request,
                headers={"Accept": "text/event-stream"},
            ) as response:
                if not response.is_success:
                    raise _status_error(response.status_code)
                if "application/json" in response.headers.get("Content-Type", ""):
                    await response.aread()
                    try:
                        value = response.json()
                    except ValueError:
                        raise _invalid_response() from None
                    if isinstance(value, dict) and "error" in value:
                        raise _response_error(value["error"])
                    raise _invalid_response()
                yield ChatEvent(type="chat.start", model_instance_id=request["model"])
                has_text = False
                finish_reason: str | None = None
                usage = None
                async for raw in _sse_data(response):
                    if raw.strip() == "[DONE]":
                        if not has_text:
                            raise _error(
                                "empty_response",
                                "OpenRouter returned no answer. Try another model.",
                            )
                        if finish_reason != "stop":
                            raise _error(
                                "truncated_response", "OpenRouter response ended before completion."
                            )
                        result: dict[str, object] = {"finish_reason": finish_reason}
                        if usage is not None:
                            result["usage"] = usage
                        yield ChatEvent(type="chat.end", result=result)
                        return
                    try:
                        value = json.loads(raw)
                    except (ValueError, TypeError):
                        raise _invalid_response() from None
                    if not isinstance(value, dict):
                        raise _invalid_response()
                    if "error" in value:
                        raise _response_error(value["error"])
                    if isinstance(value.get("usage"), dict):
                        usage = value["usage"]
                    choices = value.get("choices")
                    if not isinstance(choices, list):
                        raise _invalid_response()
                    if not choices:  # OpenAI-style usage-only accounting chunk.
                        continue
                    choice = choices[0]
                    if not isinstance(choice, dict) or not isinstance(choice.get("delta"), dict):
                        raise _invalid_response()
                    content = choice["delta"].get("content")
                    if content is not None and not isinstance(content, str):
                        raise _invalid_response()
                    if content:
                        has_text = has_text or bool(content.strip())
                        yield ChatEvent(type="message.delta", content=content)
                    reason = choice.get("finish_reason")
                    if reason is not None:
                        finish_reason = reason
                        if reason == "length":
                            raise _error(
                                "truncated_response",
                                "OpenRouter reached the answer token limit. "
                                "Increase max_tokens or select another model.",
                            )
                        if reason == "content_filter":
                            raise _error(
                                "content_filtered", "The model provider filtered this response."
                            )
                        if reason != "stop":
                            raise _status_error(502)
                raise _error("truncated_response", "OpenRouter connection ended before completion.")
        except OpenRouterError as error:
            yield ChatEvent(type="error", error=error.error)
        except httpx.TimeoutException:
            yield ChatEvent(type="error", error=_status_error(408).error)
        except httpx.RequestError:
            yield ChatEvent(
                type="error",
                error=_error(
                    "connection_error", "Cannot connect to OpenRouter. Check the network."
                ).error,
            )


class OpenRouterRegistry:
    """Resolve remotely served model IDs without starting or loading local models."""

    def __init__(self, client: OpenRouterClient) -> None:
        self._client = client
        self._instances: dict[str, ModelInstance] = {}
        self._lock = asyncio.Lock()

    async def ensure_ready(self, key: str, refresh: bool = False) -> ModelInstance:
        async with self._lock:
            if refresh:
                self.invalidate(key)
            if key in self._instances:
                return self._instances[key]
            if not any(model.key == key for model in await self._client.list_models()):
                raise _status_error(404)
            instance = ModelInstance(key=key, instance_id=key, state="ready")
            self._instances[key] = instance
            return instance

    def invalidate(self, key: str) -> None:
        self._instances.pop(key, None)

    async def refresh(self, key: str) -> ModelInstance:
        return await self.ensure_ready(key, refresh=True)
