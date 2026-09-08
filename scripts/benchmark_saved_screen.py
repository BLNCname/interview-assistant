"""Replay an inspected MSS JPEG through the real runtime twice, without showing a desktop UI.

The saved frame replaces only the capture backend. Capture validation, duplicate
handling, JPEG persistence, context building, request coordination and local vision
inference execute normally. Audio/STT/global keyboard hooks are not started.
"""
# ruff: noqa: E402

from __future__ import annotations

import argparse
import asyncio
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import numpy as np
from PIL import Image

from interview_assistant.app import InterviewApplication
from interview_assistant.audio.models import AudioSource
from interview_assistant.capture.worker import CaptureWorker
from interview_assistant.config import AppConfig, SecretStore
from interview_assistant.lmstudio.client import LMStudioClient, is_loopback_host
from interview_assistant.lmstudio.models import ChatEvent
from interview_assistant.lmstudio.registry import ModelRegistry
from interview_assistant.orchestration.coordinator import RequestCoordinator
from interview_assistant.retrieval.policy import SearchPolicy
from interview_assistant.runtime import ApplicationRuntime, RuntimeServices
from interview_assistant.stt.engine import TranscriptHypothesis
from interview_assistant.transcript.detector import QuestionDetector
from interview_assistant.transcript.store import TranscriptStore


class SilentLifecycle:
    def start(self) -> None:
        pass

    def stop(self, timeout: float = 5) -> bool:
        return True

    def pause(self) -> None:
        pass

    def resume(self) -> None:
        pass


class SavedFrameBackend:
    def __init__(self, path: Path) -> None:
        with Image.open(path) as image:
            self._frame = np.asarray(image.convert("RGB"), dtype=np.uint8).copy()
        self.closed = False

    def capture(self):
        if self.closed:
            raise RuntimeError("Saved frame backend is closed")
        return self._frame.copy()

    def close(self) -> None:
        self.closed = True


class MeasuredCapture(CaptureWorker):
    def __init__(self, image_path: Path) -> None:
        super().__init__(backend_factory=lambda: SavedFrameBackend(image_path))
        self.measurements: list[dict[str, object]] = []

    async def capture_for_event(self, kind, *, manual=False):
        started = time.perf_counter()
        result = await super().capture_for_event(kind, manual=manual)
        self.measurements.append({
            "kind": kind,
            "manual": manual,
            "status": result.status,
            "seconds": round(time.perf_counter() - started, 6),
            "path_present": result.path is not None,
            "path_exists": result.path.is_file() if result.path else False,
            "file_name": result.path.name if result.path else None,
            "file_sha256": hashlib.sha256(result.path.read_bytes()).hexdigest() if result.path else None,
            "retained_file_count": len(self.retained_paths),
        })
        return result


class MeasuredCoordinator(RequestCoordinator):
    def __init__(self, events, client) -> None:
        super().__init__(events, client)
        self.measurements: list[dict[str, object]] = []

    async def wait(self, request_id):
        outcome = await super().wait(request_id)
        self.measurements.append({
            "request_id": outcome.request_id,
            "status": outcome.status,
            "error_type": outcome.error_type,
            "text_characters": len(outcome.text),
        })
        return outcome


def sanitized_error(message: object, secret: str | None) -> str:
    text = str(message)
    if secret:
        text = text.replace(secret, "[secret]")
    text = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [secret]", text)
    text = re.sub(r"(?i)https?://[^\s\"'<>]+", "[url]", text)
    text = re.sub(r"(?i)data:[^\s\"'<>]+", "[inline data]", text)
    text = re.sub(r"(?i)(?:api[_-]?key|token|authorization)\s*[:=]\s*\S+", "[credential]", text)
    return text[:1200]


class MeasuredVisionClient(LMStudioClient):
    def __init__(self, host: str, port: int, token: str | None) -> None:
        super().__init__(host, port, token)
        self._redacted_token = token
        self.measurements: list[dict[str, object]] = []

    async def stream_chat(self, payload: Mapping[str, object]) -> AsyncIterator[ChatEvent]:
        started = time.perf_counter()
        parts = payload.get("input")
        row: dict[str, object] = {
            "has_image": isinstance(parts, list) and any(
                isinstance(part, dict) and part.get("type") == "image" for part in parts
            ),
            "status": "incomplete",
            "errors": [],
            "stored_server_conversation": payload.get("store"),
        }
        self.measurements.append(row)
        saw_end = False
        saw_content = False
        errors = row["errors"]
        assert isinstance(errors, list)
        try:
            async for event in super().stream_chat(payload):
                if event.type == "message.delta" and event.content:
                    row.setdefault("ttft_seconds", round(time.perf_counter() - started, 6))
                    saw_content = True
                elif event.type == "error":
                    errors.append({
                        "type": event.error.type if event.error else "unknown",
                        "message": sanitized_error(
                            event.error.message if event.error else "", self._redacted_token,
                        ),
                    })
                elif event.type == "chat.end":
                    saw_end = True
                    stats = (event.result or {}).get("stats", {})
                    if isinstance(stats, dict):
                        row["stats"] = {
                            key: value for key, value in stats.items()
                            if isinstance(value, (int, float, bool))
                        }
                yield event
        except Exception as error:
            errors.append({
                "exception_type": type(error).__name__,
                "message": sanitized_error(error, self._redacted_token),
            })
            raise
        finally:
            row["saw_chat_end"] = saw_end
            row["status"] = "completed" if saw_end and saw_content and not errors else "failed"
            row["total_seconds"] = round(time.perf_counter() - started, 6)


async def run(args: argparse.Namespace) -> int:
    if args.output.exists():
        raise RuntimeError("Report exists; choose a fresh output path")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp_root = args.output.parent / "saved-screen-temp"
    temp_root.mkdir(exist_ok=True)
    tempfile.tempdir = str(temp_root.resolve())
    config = AppConfig.load(ROOT / "config.yaml")
    if not is_loopback_host(config.lmstudio.host):
        raise RuntimeError("This benchmark requires the configured local LM Studio server")
    token = SecretStore(env_path=config.env_path).get_lm_token()
    client = MeasuredVisionClient(config.lmstudio.host, config.lmstudio.port, token)
    models = [
        model for model in await client.list_model_details()
        if model.key == "qwen3.6-35b-a3b-mtp" and model.loaded_instances
        and model.capabilities and model.capabilities.vision
    ]
    if len(models) != 1:
        await client.aclose()
        raise RuntimeError("Expected exactly one already-loaded requested vision model")
    config.provider = "lmstudio"
    config.lmstudio.text_model = config.lmstudio.vision_model = models[0].key
    config.mcp.backend = "off"
    config.search.mode = "off"
    app = InterviewApplication.for_test()
    capture = MeasuredCapture(args.image)
    coordinator = MeasuredCoordinator(app.events, client)
    runtime = ApplicationRuntime(app, config, RuntimeServices(
        audio=SilentLifecycle(), stt=SilentLifecycle(), hotkeys=SilentLifecycle(),
        capture=capture, registry=ModelRegistry(client), client=client,
        coordinator=coordinator, transcript_store=TranscriptStore(),
        question_detector=QuestionDetector(cooldown_seconds=0), search_policy=SearchPolicy("off"),
    ))
    report: dict[str, object] = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "scope": "original controlled real MSS JPEG replayed through a fixture capture backend",
        "real_components": [
            "ApplicationRuntime", "QuestionDetector", "CaptureWorker", "FrameValidator",
            "ContextBuilder", "LMStudioClient", "ModelRegistry", "RequestCoordinator",
        ],
        "substitutes": [
            "SavedFrameBackend instead of a new MSS capture", "Noop audio/STT/hotkeys",
            "offscreen QApplication and for_test affinity stub",
        ],
        "new_os_capture_or_hotkey_test": False,
        "search_enabled": False,
        "image_file": args.image.name,
        "image_sha256": hashlib.sha256(args.image.read_bytes()).hexdigest(),
        "model_key": models[0].key,
        "loaded_context_length": models[0].loaded_instances[0].config.context_length,
        "cases": [],
    }
    cases = report["cases"]
    assert isinstance(cases, list)
    active: dict[str, object] = {}
    case_started = 0.0

    def save() -> None:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def answer_delta(_id: int, text: str) -> None:
        if text:
            active.setdefault("runtime_ttft_seconds", round(time.perf_counter() - case_started, 6))

    def notification(message: str) -> None:
        active.setdefault("notifications", []).append(sanitized_error(message, token))

    app.events.answer_delta.connect(answer_delta)
    app.events.notification.connect(notification)
    try:
        await runtime.start()
        for index in (1, 2):
            current_question = (
                "Проанализируйте код на экране: назовите маркер, уязвимость и исправление. "
                f"Проверка сохранённого кадра {index}."
            )
            active = {"case": index, "question": current_question, "notifications": []}
            before_captures = len(capture.measurements)
            before_model = len(client.measurements)
            before_outcomes = len(coordinator.measurements)
            case_started = time.perf_counter()
            now = time.monotonic()
            try:
                await asyncio.wait_for(runtime.handle_hypothesis(TranscriptHypothesis(
                    AudioSource.SYSTEM, current_question, "ru", True, now - 1, now,
                )), timeout=150.0)
            except Exception as error:
                active["exception"] = {
                    "type": type(error).__name__, "message": sanitized_error(error, token),
                }
            active["total_seconds"] = round(time.perf_counter() - case_started, 6)
            active["captures"] = capture.measurements[before_captures:]
            active["model_requests"] = client.measurements[before_model:]
            outcomes = coordinator.measurements[before_outcomes:]
            active["coordinator_outcomes"] = outcomes
            active["answer"] = app.ribbon.answer_text
            active["status"] = "completed" if (
                "exception" not in active and len(outcomes) == 1
                and outcomes[0]["status"] == "completed" and outcomes[0]["text_characters"]
            ) else "failed"
            answer = app.ribbon.answer_text.casefold()
            active["answer_checks"] = {
                "marker_orange_73": "orange-73" in answer,
                "sql_injection": "sql-инъекц" in answer or "sql injection" in answer,
                "parameterized_fix": "параметриз" in answer or "parameteriz" in answer,
            }
            cases.append(active)
            save()
            print(json.dumps({key: active[key] for key in (
                "case", "status", "total_seconds", "answer_checks",
            )}, ensure_ascii=False), flush=True)
    finally:
        temp_directory = capture.temp_directory
        await runtime.shutdown()
        app.shutdown()
        report["shutdown_completed"] = True
        report["capture_temp_directory_removed"] = not temp_directory.exists()
        save()
    report["passed"] = len(cases) == 2 and all(
        case["status"] == "completed" and all(case["answer_checks"].values())
        and len(case["model_requests"]) == 1 and case["model_requests"][0]["has_image"]
        and len(case["captures"]) == 1 and case["captures"][0]["path_present"]
        for case in cases
    )
    report["duplicate_reused_same_image_file"] = (
        len(capture.measurements) == 2
        and capture.measurements[1]["status"] == "duplicate"
        and capture.measurements[0]["file_name"] == capture.measurements[1]["file_name"]
        and capture.measurements[0]["file_sha256"] == capture.measurements[1]["file_sha256"]
    )
    report["passed"] = report["passed"] and report["duplicate_reused_same_image_file"]
    save()
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, default=ROOT / "build/stress-2026-09-08/capture-evidence-3.jpg")
    parser.add_argument("--output", type=Path, default=ROOT / "build/stress-2026-09-08/capture-replay-after-fix.json")
    raise SystemExit(asyncio.run(run(parser.parse_args())))
