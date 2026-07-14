from __future__ import annotations

import asyncio

import pytest

from interview_assistant.diagnostics.readiness import (
    READINESS_CHECK_NAMES,
    CheckResult,
    ProbeOutcome,
    ReadinessCheck,
    ReadinessReport,
    ReadinessRunner,
    build_readiness_checks,
    streaming_ttft_outcome,
)


async def _outcome(
    status: str = "ready",
    message: str = "available",
) -> ProbeOutcome:
    return ProbeOutcome(status=status, message=message)  # type: ignore[arg-type]


def _check(
    name: str,
    *,
    required: bool = True,
    timeout_s: float = 0.1,
    status: str = "ready",
    message: str = "available",
) -> ReadinessCheck:
    async def probe() -> ProbeOutcome:
        return await _outcome(status, message)

    return ReadinessCheck(
        name=name,
        required=required,
        timeout_s=timeout_s,
        remediation=f"Fix {name}",
        probe=probe,
    )


@pytest.mark.asyncio
async def test_required_failure_blocks_session() -> None:
    report = await ReadinessRunner(
        (_check("display_affinity", status="failed", message="unsupported"),)
    ).run()

    assert report.status == "failed"
    assert not report.can_start
    assert report.by_name("display_affinity").message == "unsupported"
    assert report.by_name("display_affinity").remediation == "Fix display_affinity"


@pytest.mark.asyncio
async def test_optional_failure_is_visible_warning_and_does_not_block() -> None:
    report = await ReadinessRunner(
        (
            _check("display_affinity"),
            _check("context7", required=False, status="failed", message="offline"),
        )
    ).run()

    assert report.status == "warning"
    assert report.can_start
    assert report.by_name("context7").status == "warning"
    assert not report.by_name("context7").required


def test_report_rejects_duplicate_names_and_is_frozen() -> None:
    result = CheckResult(
        name="hotkeys",
        status="ready",
        message="registered",
        remediation="Configure hotkeys",
        duration_ms=1.0,
        required=True,
        timed_out=False,
    )

    with pytest.raises(ValueError, match="Duplicate readiness check"):
        ReadinessReport((result, result))
    with pytest.raises(AttributeError):
        result.message = "changed"  # type: ignore[misc]


def test_direct_report_treats_optional_failed_result_as_nonblocking_warning() -> None:
    report = ReadinessReport(
        (
            CheckResult(
                name="context7",
                status="failed",
                message="offline",
                remediation="Start Context7",
                duration_ms=1.0,
                required=False,
            ),
        )
    )

    assert report.status == "warning"
    assert report.can_start


def test_empty_report_is_rejected_instead_of_claiming_readiness() -> None:
    with pytest.raises(ValueError, match="at least one"):
        ReadinessReport(())


@pytest.mark.asyncio
async def test_checks_run_concurrently_but_report_keeps_declared_order() -> None:
    release = asyncio.Event()
    both_started = asyncio.Event()
    starts: list[str] = []

    def delayed(name: str) -> ReadinessCheck:
        async def probe() -> ProbeOutcome:
            starts.append(name)
            if len(starts) == 2:
                both_started.set()
            await release.wait()
            return ProbeOutcome("ready", name)

        return ReadinessCheck(name, True, 1.0, "retry", probe)

    task = asyncio.create_task(ReadinessRunner((delayed("first"), delayed("second"))).run())
    await asyncio.wait_for(both_started.wait(), 0.5)
    release.set()
    report = await task

    assert [item.name for item in report.checks] == ["first", "second"]
    assert all(item.duration_ms >= 0 for item in report.checks)


@pytest.mark.asyncio
async def test_each_check_has_an_independent_timeout() -> None:
    cancelled = asyncio.Event()

    async def slow_probe() -> ProbeOutcome:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    slow = ReadinessCheck("cuda_stt", True, 0.01, "Install CUDA STT", slow_probe)
    report = await ReadinessRunner((slow, _check("hotkeys"))).run()

    timeout = report.by_name("cuda_stt")
    assert timeout.status == "failed"
    assert timeout.timed_out
    assert "timed out" in timeout.message.casefold()
    assert report.by_name("hotkeys").status == "ready"
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_probe_exception_is_sanitized_without_leaking_message() -> None:
    async def broken_probe() -> ProbeOutcome:
        raise RuntimeError("Bearer secret-value\nprivate host")

    check = ReadinessCheck("lmstudio_auth", True, 0.1, "Check LM Studio token", broken_probe)
    report = await ReadinessRunner((check,)).run()
    result = report.by_name("lmstudio_auth")

    assert result.status == "failed"
    assert "secret-value" not in result.message
    assert "RuntimeError" in result.message


@pytest.mark.asyncio
async def test_cancelling_run_cleans_up_all_child_probes() -> None:
    both_started = asyncio.Event()
    starts: set[str] = set()
    cancelled: set[str] = set()

    def never(name: str) -> ReadinessCheck:
        async def probe() -> ProbeOutcome:
            starts.add(name)
            if starts == {"one", "two"}:
                both_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.add(name)

        return ReadinessCheck(name, True, 5.0, "retry", probe)

    run = asyncio.create_task(ReadinessRunner((never("one"), never("two"))).run())
    await asyncio.wait_for(both_started.wait(), 0.5)
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run

    assert cancelled == {"one", "two"}
    assert not any(
        task.get_name().startswith("readiness:") and not task.done()
        for task in asyncio.all_tasks()
    )


@pytest.mark.asyncio
async def test_base_exception_in_one_probe_cancels_and_drains_sibling() -> None:
    class FatalProbeError(BaseException):
        pass

    sibling_started = asyncio.Event()
    sibling_cancelled = asyncio.Event()

    async def fatal_probe() -> ProbeOutcome:
        await sibling_started.wait()
        raise FatalProbeError

    async def sibling_probe() -> ProbeOutcome:
        sibling_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            sibling_cancelled.set()

    checks = (
        ReadinessCheck("fatal", True, 1.0, "retry", fatal_probe),
        ReadinessCheck("sibling", True, 1.0, "retry", sibling_probe),
    )
    with pytest.raises(FatalProbeError):
        await ReadinessRunner(checks).run()
    assert sibling_cancelled.is_set()


def test_catalog_is_complete_and_requires_injected_probes() -> None:
    calls: list[str] = []

    def make_probe(name: str):
        async def probe() -> ProbeOutcome:
            calls.append(name)
            return ProbeOutcome("ready", "ok")

        return probe

    probes = {name: make_probe(name) for name in READINESS_CHECK_NAMES}
    checks = build_readiness_checks(probes)

    assert tuple(check.name for check in checks) == READINESS_CHECK_NAMES
    assert calls == []
    assert not next(check for check in checks if check.name == "context7").required
    assert not next(check for check in checks if check.name == "duckduckgo_mcp").required
    assert not next(check for check in checks if check.name == "event_capture").required
    with pytest.raises(ValueError, match="Missing readiness probes"):
        build_readiness_checks({})
    with pytest.raises(ValueError, match="Unexpected readiness probes"):
        build_readiness_checks({**probes, "model_disocvery": make_probe("typo")})


def test_ttft_outcome_warns_when_slow_and_fails_without_first_delta() -> None:
    assert streaming_ttft_outcome(250, warning_threshold_ms=1_000).status == "ready"
    assert streaming_ttft_outcome(1_250, warning_threshold_ms=1_000).status == "warning"
    missing = streaming_ttft_outcome(None, warning_threshold_ms=1_000)
    assert missing.status == "failed"
    assert "first delta" in missing.message.casefold()
