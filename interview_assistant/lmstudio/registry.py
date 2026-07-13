import asyncio
from collections.abc import Mapping, Sequence
from typing import Protocol

from pydantic import BaseModel

from .models import (
    LoadedModelInstance,
    LoadResult,
    ModelDetails,
    ModelInstance,
)


class ModelRegistryClient(Protocol):
    async def list_model_details(self) -> list[ModelDetails]: ...

    async def load_model(self, key: str) -> LoadResult: ...


class DuplicateModelInstancesError(RuntimeError):
    def __init__(self, key: str, instance_ids: Sequence[str]) -> None:
        self.key = key
        self.instance_ids = tuple(instance_ids)
        super().__init__(
            f"model {key!r} has multiple loaded instances: "
            f"{', '.join(self.instance_ids)}"
        )


class UnexpectedModelDeviceError(RuntimeError):
    def __init__(
        self,
        key: str,
        expected_device_name: str,
        actual_device_name: object,
    ) -> None:
        self.key = key
        self.expected_device_name = expected_device_name
        self.actual_device_name = actual_device_name
        super().__init__(
            f"model {key!r} is allocated to unexpected device "
            f"{actual_device_name!r}; expected {expected_device_name!r}"
        )


class ModelRegistry:
    def __init__(
        self,
        client: ModelRegistryClient,
        preferred_device_name: str = "Strix Halo",
    ) -> None:
        self._client = client
        self._preferred_device_name = preferred_device_name
        self._instances: dict[str, ModelInstance] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def ensure_ready(self, key: str, refresh: bool = False) -> ModelInstance:
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._instances.get(key)
            if not refresh and cached is not None and cached.state == "ready":
                return cached

            instance = await self._discover_or_load(key)
            self._instances[key] = instance
            return instance

    def invalidate(self, key: str) -> None:
        self._instances.pop(key, None)

    async def refresh(self, key: str) -> ModelInstance:
        return await self.ensure_ready(key, refresh=True)

    async def _discover_or_load(self, key: str) -> ModelInstance:
        discovered = self._instances_for_key(
            key,
            await self._client.list_model_details(),
        )
        self._reject_duplicates(key, discovered)
        if discovered:
            return self._from_discovered(key, discovered[0])

        loaded = await self._client.load_model(key)

        refreshed = self._instances_for_key(
            key,
            await self._client.list_model_details(),
        )
        self._reject_duplicates(key, refreshed)
        if refreshed:
            current = refreshed[0]
            device_name = self._validated_device_name(
                key,
                loaded,
                loaded.load_config,
                current,
                current.config,
            )
            instance_id = current.instance_id
        else:
            device_name = self._validated_device_name(
                key,
                loaded,
                loaded.load_config,
            )
            instance_id = loaded.instance_id

        return ModelInstance(
            key=key,
            instance_id=instance_id,
            state="ready",
            device_name=device_name,
        )

    @staticmethod
    def _instances_for_key(
        key: str,
        details: Sequence[ModelDetails],
    ) -> list[LoadedModelInstance]:
        return [
            instance
            for model in details
            if model.key == key
            for instance in model.loaded_instances
        ]

    @staticmethod
    def _reject_duplicates(key: str, instances: Sequence[LoadedModelInstance]) -> None:
        if len(instances) > 1:
            raise DuplicateModelInstancesError(
                key,
                [instance.instance_id for instance in instances],
            )

    def _from_discovered(
        self,
        key: str,
        loaded: LoadedModelInstance,
    ) -> ModelInstance:
        return ModelInstance(
            key=key,
            instance_id=loaded.instance_id,
            state="ready",
            device_name=self._validated_device_name(key, loaded, loaded.config),
        )

    def _validated_device_name(
        self,
        key: str,
        *sources: BaseModel | None,
    ) -> str | None:
        found = False
        for source in sources:
            if source is None:
                continue
            for actual in self._device_names(source.model_extra or {}):
                found = True
                if actual != self._preferred_device_name:
                    raise UnexpectedModelDeviceError(
                        key,
                        self._preferred_device_name,
                        actual,
                    )
        return self._preferred_device_name if found else None

    @staticmethod
    def _device_names(extra: Mapping[str, object]) -> list[object]:
        devices: list[object] = []
        if "device_name" in extra and extra["device_name"] is not None:
            devices.append(extra["device_name"])

        allocation = extra.get("allocation")
        if isinstance(allocation, Mapping):
            device = allocation.get("device")
            if device is not None:
                devices.append(device)
        return devices
