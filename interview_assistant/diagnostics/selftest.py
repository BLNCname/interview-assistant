"""Fast deterministic startup smoke checks, without hardware, network or pytest."""

from collections.abc import Callable
from pathlib import Path
from time import perf_counter
from typing import Any


def _search_policy_check() -> None:
    from interview_assistant.retrieval.policy import SearchPolicy

    policy = SearchPolicy("auto")
    request = policy.integrations_for(
        "My name is Alice Smith. What is the latest Python release?",
        full_transcript="private transcript",
    )
    if len(request) != 1 or request[0].query != "What is the latest Python release?":
        raise ValueError("Search privacy boundary failed")
    if request[0].id != "mcp/firecrawl" or request[0].allowed_tools != ("firecrawl_search",):
        raise ValueError("Search integration or tool allowlist failed")
    if SearchPolicy("off").integrations_for("What is the latest Python release?"):
        raise ValueError("Disabled search exposed a tool")


def _question_detection_check() -> None:
    from interview_assistant.audio.models import AudioSource
    from interview_assistant.transcript.detector import QuestionDetector

    detector = QuestionDetector(clock=lambda: 10.0)
    text = "Explain binary search complexity."
    if detector.detect(AudioSource.SYSTEM, text, is_final=False) is not None:
        raise ValueError("Partial speech generated a question")
    question = detector.detect(AudioSource.SYSTEM, text)
    if question is None or question.text != text:
        raise ValueError("Final interview question was lost")
    if detector.detect(AudioSource.SYSTEM, text) is not None:
        raise ValueError("Duplicate question was not suppressed")


def _payload_check() -> None:
    from interview_assistant.context.models import ContextItem, ContextSnapshot
    from interview_assistant.lmstudio.payload import build_context_payload

    context = ContextSnapshot(
        question="Explain binary search.", task_kind="theory",
        items=(ContextItem("system", "Instructions", "Give a concise spoken answer."),
               ContextItem("question", "Question", "Explain binary search.")),
        prompt="unused", estimated_tokens=20, max_tokens=1000,
    )
    payload = build_context_payload("offline-model", context)
    if payload.get("system_prompt") != "Give a concise spoken answer.":
        raise ValueError("Trusted instructions were lost")
    if payload.get("input") != "Question:\nExplain binary search." or payload.get("store") is not False:
        raise ValueError("Stateless request payload failed")


def run_fast_self_test(*, config_path: Path | None = None) -> dict[str, Any]:
    """Exercise pure core behavior and validate configuration, without live probes."""
    started = perf_counter()
    checks: list[dict[str, str]] = []
    routines: tuple[tuple[str, Callable[[], None]], ...] = (
        ("search_policy", _search_policy_check),
        ("question_detection", _question_detection_check),
        ("request_payload", _payload_check),
    )
    for name, routine in routines:
        try:
            routine()
        except Exception:
            checks.append({"id": name, "status": "failed"})
        else:
            checks.append({"id": name, "status": "passed"})

    try:
        from interview_assistant.config import AppConfig

        # AppConfig.load shares dotenv resolution with GUI bootstrap, including
        # the portable EXE/source-root fallback when AppData has no adjacent file.
        config = AppConfig.load(config_path) if config_path is not None else AppConfig()
        configuration: dict[str, Any] = {
            "provider": config.provider, "mcp_backend": config.mcp.backend,
            "text_model_configured": bool(config.text_model),
            "vision_model_configured": bool(config.vision_model),
            "stt_device": config.audio.device,
        }
    except Exception:
        configuration = {"status": "invalid"}
        checks.append({"id": "configuration", "status": "failed"})
    else:
        checks.append({"id": "configuration", "status": "passed"})
    return {
        "status": "ok" if all(row["status"] == "passed" for row in checks) else "error",
        "checks": checks,
        "configuration": configuration,
        "elapsed_ms": round((perf_counter() - started) * 1000, 2),
        "scope": "offline core checks; live device and provider checks run in GUI readiness",
    }
