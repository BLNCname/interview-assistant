from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

ReadinessStatus = Literal["ready", "warning", "failed"]
ReadinessProbe = Callable[[], Awaitable["ProbeOutcome"]]

# Never display exception text here, even for typed provider exceptions. Only
# these application-authored descriptions can cross the diagnostic UI boundary.
_OPENROUTER_FAILURES = {
    "missing_api_key": (
        "OpenRouter API key is not configured. Select OpenRouter in Models, "
        "then paste the key and run checks again."
    ),
    "authentication_error": (
        "OpenRouter rejected the API key. Check that you pasted an active inference "
        "key without quotes or the Bearer prefix, then Save."
    ),
    "permission_denied": (
        "OpenRouter denied access. Check API key permissions, account access and "
        "whether this is an inference key rather than a management key."
    ),
    "insufficient_credits": "OpenRouter has insufficient credits or the key's spending limit is reached.",
    "rate_limit": "OpenRouter rate limit reached. Wait and run checks again.",
    "connection_error": (
        "OpenRouter connection failed. Check internet access, DNS and TLS; "
        "a browser connection alone may use different network settings."
    ),
    "timeout": "OpenRouter request timed out. Check the connection and run checks again.",
    "provider_unavailable": "OpenRouter or the selected provider is unavailable. Try again later.",
    "model_not_found": (
        "OpenRouter endpoint or model is unavailable. Check service access "
        "and the exact selected model ID."
    ),
    "invalid_request": "OpenRouter rejected the model or request parameters. Check the selected model.",
    "invalid_response": "OpenRouter returned an unexpected response. Check service and network access.",
}


@dataclass(frozen=True, slots=True)
class ProbeOutcome:
    status: ReadinessStatus
    message: str

    def __post_init__(self) -> None:
        if self.status not in {"ready", "warning", "failed"}:
            raise ValueError(f"Unsupported probe status: {self.status}")
        if not self.message.strip():
            raise ValueError("Probe outcome message must not be empty")


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    status: ReadinessStatus
    message: str
    remediation: str
    duration_ms: float
    required: bool
    timed_out: bool = False

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Readiness result name must not be empty")
        if self.status not in {"ready", "warning", "failed"}:
            raise ValueError(f"Unsupported readiness status: {self.status}")
        if not self.message.strip() or not self.remediation.strip():
            raise ValueError("Readiness result message and remediation must not be empty")
        if self.duration_ms < 0:
            raise ValueError("Readiness result duration must not be negative")


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    checks: tuple[CheckResult, ...]

    def __post_init__(self) -> None:
        if not self.checks:
            raise ValueError("Readiness report needs at least one check")
        duplicate = _first_duplicate(item.name for item in self.checks)
        if duplicate is not None:
            raise ValueError(f"Duplicate readiness check: {duplicate}")

    @property
    def status(self) -> ReadinessStatus:
        if any(item.required and item.status == "failed" for item in self.checks):
            return "failed"
        if any(item.status in {"warning", "failed"} for item in self.checks):
            return "warning"
        return "ready"

    @property
    def can_start(self) -> bool:
        return self.status != "failed"

    def by_name(self, name: str) -> CheckResult:
        for item in self.checks:
            if item.name == name:
                return item
        raise KeyError(name)


@dataclass(frozen=True, slots=True)
class ReadinessCheck:
    name: str
    required: bool
    timeout_s: float
    remediation: str
    probe: ReadinessProbe

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Readiness check name must not be empty")
        if self.timeout_s <= 0:
            raise ValueError("Readiness check timeout must be positive")
        if not self.remediation.strip():
            raise ValueError("Readiness check remediation must not be empty")


class ReadinessRunner:
    """Runs injected readiness probes concurrently without owning any hardware."""

    def __init__(
        self,
        checks: tuple[ReadinessCheck, ...],
        *,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        duplicate = _first_duplicate(check.name for check in checks)
        if duplicate is not None:
            raise ValueError(f"Duplicate readiness check: {duplicate}")
        self._checks = checks
        self._clock = clock

    async def run(self) -> ReadinessReport:
        tasks = [
            asyncio.create_task(self._run_check(check), name=f"readiness:{check.name}")
            for check in self._checks
        ]
        try:
            results = await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        return ReadinessReport(tuple(results))

    async def _run_check(self, check: ReadinessCheck) -> CheckResult:
        # Keep network dependencies out of the cheap offline --self-test path.
        from interview_assistant.providers.openrouter import OpenRouterError

        started = self._clock()
        timed_out = False
        try:
            outcome = await asyncio.wait_for(check.probe(), timeout=check.timeout_s)
            if not isinstance(outcome, ProbeOutcome):
                raise TypeError("Probe must return ProbeOutcome")
        except TimeoutError:
            timed_out = True
            outcome = ProbeOutcome(
                "failed",
                f"Check timed out after {check.timeout_s:g} seconds",
            )
        except asyncio.CancelledError:
            raise
        except OpenRouterError as error:
            outcome = ProbeOutcome("failed", _OPENROUTER_FAILURES.get(
                error.error.code or "",
                "OpenRouter request failed. Check the provider settings and run checks again.",
            ))
        except Exception as error:
            # Probe exception text may contain a token, URL, device name, or transcript.
            outcome = ProbeOutcome("failed", f"{type(error).__name__}: readiness probe failed")

        status = outcome.status
        if not check.required and status == "failed":
            status = "warning"
        duration_ms = max(0.0, (self._clock() - started) * 1_000)
        return CheckResult(
            name=check.name,
            status=status,
            message=outcome.message,
            remediation=check.remediation,
            duration_ms=duration_ms,
            required=check.required,
            timed_out=timed_out,
        )


def _first_duplicate(names: Iterable[str]) -> str | None:
    seen: set[str] = set()
    for name in names:
        if name in seen:
            return name
        seen.add(name)
    return None


READINESS_CHECK_NAMES = (
    "windows_dwm",
    "system_audio",
    "microphone",
    "cuda_stt",
    "stt_ru_fixture",
    "stt_en_fixture",
    "lmstudio_auth",
    "lmlink_status",
    "preferred_device",
    "model_discovery",
    "duplicate_instances",
    "model_load_warmup",
    "context7",
    "firecrawl_mcp",
    "hotkeys",
    "display_affinity",
    "event_capture",
    "streaming_ttft",
)

_OPTIONAL_CHECKS = frozenset({
    "context7", "firecrawl_mcp", "event_capture", "lmlink_status", "preferred_device",
})
_CHECK_CONFIGURATION: Mapping[str, tuple[float, str]] = {
    "windows_dwm": (2.0, "Enable Windows Desktop Window Manager composition."),
    "system_audio": (3.0, "Select an available system-audio loopback device."),
    "microphone": (3.0, "Select an available microphone and grant access."),
    "cuda_stt": (120.0, "Verify the STT device, compute type and local model files."),
    "stt_ru_fixture": (120.0, "Run the bundled Russian STT readiness fixture."),
    "stt_en_fixture": (120.0, "Run the bundled English STT readiness fixture."),
    "lmstudio_auth": (5.0, "Check the active provider and its API key/token."),
    "lmlink_status": (5.0, "LM Link is optional; configure it only for remote LM Studio inference."),
    "preferred_device": (5.0, "Select the configured LM Link preferred device."),
    "model_discovery": (8.0, "Refresh models exposed by the selected provider."),
    "duplicate_instances": (5.0, "Reuse one loaded instance for identical model keys."),
    "model_load_warmup": (120.0, "Load and warm the selected unique model keys."),
    "context7": (5.0, "Start or reconfigure the Context7 MCP server."),
    "firecrawl_mcp": (
        5.0, "Check FIRECRAWL_API_KEY, internet access and the Firecrawl MCP endpoint configuration.",
    ),
    "hotkeys": (3.0, "Resolve conflicting or unavailable global hotkeys."),
    "display_affinity": (3.0, "Enable supported Windows capture exclusion for the overlay."),
    "event_capture": (5.0, "Verify event-driven capture; the hotkey remains available."),
    "streaming_ttft": (120.0, "Check provider availability and streaming latency."),
}


def build_readiness_checks(probes: Mapping[str, ReadinessProbe]) -> tuple[ReadinessCheck, ...]:
    """Build the complete deterministic catalog from side-effect-free injected probes."""

    missing = [name for name in READINESS_CHECK_NAMES if name not in probes]
    if missing:
        raise ValueError(f"Missing readiness probes: {', '.join(missing)}")
    unexpected = [name for name in probes if name not in READINESS_CHECK_NAMES]
    if unexpected:
        raise ValueError(f"Unexpected readiness probes: {', '.join(unexpected)}")
    return tuple(
        ReadinessCheck(
            name=name,
            required=name not in _OPTIONAL_CHECKS,
            timeout_s=_CHECK_CONFIGURATION[name][0],
            remediation=_CHECK_CONFIGURATION[name][1],
            probe=probes[name],
        )
        for name in READINESS_CHECK_NAMES
    )


def streaming_ttft_outcome(
    first_delta_ms: float | None,
    *,
    warning_threshold_ms: float,
) -> ProbeOutcome:
    if first_delta_ms is None:
        return ProbeOutcome("failed", "No first delta arrived from the streaming response")
    if first_delta_ms < 0:
        raise ValueError("First-delta duration must not be negative")
    if warning_threshold_ms <= 0:
        raise ValueError("TTFT warning threshold must be positive")
    if first_delta_ms > warning_threshold_ms:
        return ProbeOutcome(
            "warning",
            f"First delta took {first_delta_ms:.0f} ms",
        )
    return ProbeOutcome("ready", f"First delta arrived in {first_delta_ms:.0f} ms")
