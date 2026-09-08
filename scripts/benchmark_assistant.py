"""Exercise real retrieval and LM inference through the application's text runtime.

Audio/STT are replaced by synthetic final hypotheses; the Qt ribbon is offscreen.
This measures accepted-question to answer latency, not speech-recognition latency.
No credentials or screenshots are written to the report.
"""
# ruff: noqa: E402

from __future__ import annotations

import argparse
import asyncio
from collections.abc import AsyncIterator, Mapping
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from interview_assistant.app import InterviewApplication
from interview_assistant.audio.models import AudioSource
from interview_assistant.capture.worker import CaptureResult
from interview_assistant.config import AppConfig, SecretStore, environment_values
from interview_assistant.lmstudio.client import LMStudioClient
from interview_assistant.lmstudio.models import ChatEvent
from interview_assistant.lmstudio.registry import ModelRegistry
from interview_assistant.orchestration.coordinator import RequestCoordinator
from interview_assistant.retrieval.policy import SearchPolicy
from interview_assistant.runtime import ApplicationRuntime, RuntimeServices
from interview_assistant.stt.engine import TranscriptHypothesis
from interview_assistant.transcript.detector import QuestionDetector
from interview_assistant.transcript.store import TranscriptStore


HEAVY_QUESTIONS = (
    "Как на сентябрь 2026 года безопасно реализовать OAuth для SPA и мобильного клиента: "
    "сравни PKCE, refresh token rotation, DPoP и рекомендации RFC 9700; какие атаки остаются?",
    "Compare current passkey deployment recommendations for enterprise SSO: synced versus "
    "device-bound credentials, recovery risks, phishing resistance and WebAuthn requirements.",
    "Какие сейчас ограничения гибридного обмена ключами X25519MLKEM768 в TLS 1.3: "
    "совместимость браузеров и OpenSSL, размер handshake, откат и защита от downgrade?",
    "Compare current Kubernetes admission security options: Pod Security Admission, CEL "
    "ValidatingAdmissionPolicy and policy engines; explain fail-open risks and rollout checks.",
    "Как сейчас проектировать tenant isolation в PostgreSQL: RLS, владельцы таблиц, "
    "BYPASSRLS, connection pooling и тестирование утечки данных между арендаторами?",
    "Compare the latest software supply-chain guidance for SLSA provenance, Sigstore signing, "
    "SBOMs and dependency pinning. Explain which threats each control does not prevent.",
)


class SilentLifecycle:
    """Explicit test substitute for live microphone/STT/hotkey services."""

    def start(self) -> None:
        pass

    def stop(self, timeout: float = 5.0) -> bool:
        return True

    def pause(self) -> None:
        pass

    def resume(self) -> None:
        pass

    def update_bindings(self, bindings: object) -> None:
        pass


class NoCapture:
    async def capture_for_event(self, kind: str, *, manual: bool = False) -> CaptureResult:
        return CaptureResult("disallowed", None, None)

    async def shutdown(self) -> None:
        pass


class MeasuredLMClient(LMStudioClient):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.measurements: list[dict[str, object]] = []

    async def stream_chat(self, payload: Mapping[str, object]) -> AsyncIterator[ChatEvent]:
        started = time.perf_counter()
        text_input = payload.get("input", "")
        row: dict[str, object] = {
            "input_characters": len(text_input) if isinstance(text_input, str) else None,
            "system_characters": len(str(payload.get("system_prompt", ""))),
            "has_image": isinstance(text_input, list),
            "stored_server_conversation": payload.get("store"),
            "status": "incomplete",
        }
        self.measurements.append(row)
        try:
            async for event in super().stream_chat(payload):
                elapsed = round(time.perf_counter() - started, 4)
                if event.type == "message.delta" and event.content:
                    row.setdefault("first_token_seconds", elapsed)
                elif event.type == "prompt_processing.end":
                    row["prompt_processing_end_seconds"] = elapsed
                elif event.type == "error":
                    row["error_type"] = event.error.type if event.error else "unknown"
                    row["status"] = "failed"
                elif event.type == "chat.end":
                    row["stats"] = (event.result or {}).get("stats", {})
                    if row["status"] != "failed":
                        row["status"] = "completed"
                yield event
        finally:
            row["total_seconds"] = round(time.perf_counter() - started, 4)


def hypothesis(text: str, source: AudioSource = AudioSource.SYSTEM) -> TranscriptHypothesis:
    now = time.monotonic()
    return TranscriptHypothesis(source, text, "ru", True, now - 1, now)


async def run(args: argparse.Namespace) -> int:
    from scripts.benchmark_firecrawl import MeasuredMCPClient, read_credit_balance

    config = AppConfig.load(ROOT / "config.yaml")
    env = environment_values(config.env_path)
    client = MeasuredLMClient(config.lmstudio.host, config.lmstudio.port,
                              SecretStore(env_path=config.env_path).get_lm_token())
    models = await client.list_model_details()
    loaded = [model for model in models if model.type == "llm" and model.loaded_instances]
    if len(loaded) != 1:
        await client.aclose()
        raise RuntimeError("Benchmark requires exactly one already-loaded local model")
    config.provider = "lmstudio"
    config.lmstudio.text_model = loaded[0].key
    config.lmstudio.vision_model = ""
    config.mcp.backend = "native"
    config.search.timeout_seconds = args.search_timeout
    report: dict[str, object] = {
        "measurement_boundary": "synthetic final hypothesis to streaming ribbon",
        "real_components": ["SearchPolicy", "NativeMCPClient", "ContextBuilder",
                            "LMStudioClient", "RequestCoordinator", "ApplicationRuntime"],
        "substitutes": ["no live audio or STT", "offscreen Qt ribbon", "no screen capture"],
        "model": loaded[0].key, "context_length": loaded[0].loaded_instances[0].config.context_length,
        "search_timeout_seconds": args.search_timeout, "cases": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def save() -> None:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")

    search = MeasuredMCPClient(timeout_seconds=args.search_timeout, environ=env)
    app = InterviewApplication.for_test()
    policy = SearchPolicy("off")
    store = TranscriptStore()
    runtime = ApplicationRuntime(app, config, RuntimeServices(
        audio=SilentLifecycle(), stt=SilentLifecycle(), hotkeys=SilentLifecycle(),
        capture=NoCapture(), registry=ModelRegistry(client), client=client,
        coordinator=RequestCoordinator(app.events, client), transcript_store=store,
        question_detector=QuestionDetector(cooldown_seconds=0), search_policy=policy,
        retrieval=search,
    ))
    current: dict[str, object] = {}
    case_started = 0.0

    def delta(_request: int, content: str) -> None:
        if content:
            current.setdefault("first_answer_seconds", round(time.perf_counter() - case_started, 4))

    def notification(message: str) -> None:
        current.setdefault("notifications", []).append(message)  # type: ignore[union-attr]

    app.events.answer_delta.connect(delta)
    app.events.notification.connect(notification)
    await runtime.start()
    try:
        report["credits_before"] = await read_credit_balance(env)
        save()
        balance = report["credits_before"]
        if (not isinstance(balance, dict) or balance.get("status") != "ok"
                or not isinstance(balance.get("remaining_credits"), (int, float))
                or balance["remaining_credits"] < 100):
            report["stopped_reason"] = "credit balance unavailable or below 100-credit reserve"
            return 1
        for index, question in enumerate(HEAVY_QUESTIONS[:args.cases]):
            current = {"case": index + 1, "question": question, "kind": "heavy_web"}
            # Entire artificial dialogue is kept out of web queries by SearchPolicy.
            for turn in range(args.history_turns):
                store.add(hypothesis(
                    f"Обсуждение {turn}: в учебном проекте выбираем проверку прав на сервере, "
                    "аудит действий и изоляцию арендаторов. Сравниваем производительность, "
                    "безопасность и удобство сопровождения, учитывая отказы сети. " * 3,
                    AudioSource.MICROPHONE if turn % 2 else AudioSource.SYSTEM,
                ))
            current["history_entries_before"] = len(store)
            policy.force_next()
            case_started = time.perf_counter()
            before_calls = len(client.measurements)
            try:
                await asyncio.wait_for(runtime.handle_hypothesis(hypothesis(question)), timeout=120)
                current["status"] = "completed" if len(client.measurements) > before_calls else "no_answer"
                current["answer"] = app.ribbon.answer_text
            except Exception as error:
                current.update(status="failed", error_type=type(error).__name__)
            current["total_seconds"] = round(time.perf_counter() - case_started, 4)
            current["llm"] = client.measurements[before_calls:]
            current["retrieval"] = search.last_measurement
            current["history_entries_after"] = len(store)
            current["sources"] = list(app.ribbon.source_texts)
            report["cases"].append(current)  # type: ignore[union-attr]
            save()
            print(json.dumps({key: current[key] for key in (
                "case", "status", "first_answer_seconds", "total_seconds", "sources"
            ) if key in current}), flush=True)
            if search.last_measurement.get("error_code") in {"credits", "rate_limit", "credentials"}:
                report["stopped_reason"] = "provider limit or credential error"
                break
        report["credits_after"] = await read_credit_balance(env)
    finally:
        await runtime.shutdown()
        app.shutdown()
        report["shutdown_completed"] = True
        save()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cases", type=int, choices=range(1, 7), default=6)
    parser.add_argument("--history-turns", type=int, default=80)
    parser.add_argument("--search-timeout", type=float, default=10)
    raise SystemExit(asyncio.run(run(parser.parse_args())))
