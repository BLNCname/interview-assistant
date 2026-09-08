"""Controlled Windows GUI fixture for real hotkey, MSS and vision-model checks.

The fixture uses an ordinary, minimizable window. Audio/STT are not started.
Use Ctrl+Shift+S and the fixture's buttons to exercise the runtime. Inspect the
first saved capture for unrelated desktop content before requesting model input.
"""
# ruff: noqa: E402

from __future__ import annotations

import asyncio
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget
from qasync import QEventLoop

from interview_assistant.app import InterviewApplication
from interview_assistant.audio.models import AudioSource
from interview_assistant.capture.worker import CaptureWorker
from interview_assistant.config import AppConfig, SecretStore
from interview_assistant.events import EventBus
from interview_assistant.lmstudio.client import LMStudioClient
from interview_assistant.lmstudio.registry import ModelRegistry
from interview_assistant.orchestration.coordinator import RequestCoordinator
from interview_assistant.retrieval.policy import SearchPolicy
from interview_assistant.runtime import ApplicationRuntime, RuntimeServices
from interview_assistant.state import StateMachine
from interview_assistant.stt.engine import TranscriptHypothesis
from interview_assistant.transcript.detector import QuestionDetector
from interview_assistant.transcript.store import TranscriptStore
from interview_assistant.utils.hotkeys import DEFAULT_HOTKEY_BINDINGS, HotkeyManager


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, default=ROOT / "build/stress-2026-09-08/capture-live.json")
OUTPUT = parser.parse_args().output.resolve()
if OUTPUT.exists():
    raise SystemExit("Capture report already exists; choose a fresh --output directory.")


class SilentLifecycle:
    def start(self):
        pass

    def stop(self, timeout=5):
        return True

    def pause(self):
        pass

    def resume(self):
        pass


class MeasuredVisionClient(LMStudioClient):
    async def stream_chat(self, payload):
        row = {"has_image": isinstance(payload.get("input"), list), "status": "incomplete"}
        report.setdefault("model_requests", []).append(row)
        try:
            async for event in super().stream_chat(payload):
                if event.type == "error":
                    row["status"] = "failed"
                    row["error_type"] = event.error.type if event.error else "unknown"
                elif event.type == "chat.end":
                    row["stats"] = (event.result or {}).get("stats", {})
                    if row["status"] != "failed":
                        row["status"] = "completed"
                yield event
        except Exception as error:
            row.update(status="failed", exception_type=type(error).__name__)
            raise
        finally:
            save()


class MeasuredCapture(CaptureWorker):
    async def capture_for_event(self, kind, *, manual=False):
        started = time.perf_counter()
        result = await super().capture_for_event(kind, manual=manual)
        if result.path:
            OUTPUT.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(result.path, OUTPUT.parent / f"capture-evidence-{len(captures) + 1}.jpg")
        captures.append({"kind": kind, "manual": manual, "status": result.status,
                         "seconds": round(time.perf_counter() - started, 4),
                         "file_bytes": result.path.stat().st_size if result.path else None,
                         "fixture": fixture_id[0]})
        save()
        return result


captures = []
requests = []
notifications = []
fixture_id = ["A"]
report = {"schema_version": 2,
          "scope": "real Windows hotkey/MSS/vision with synthetic fixture and text questions",
          "captures": captures, "requests": requests, "notifications": notifications,
          "audio_stt_substituted": True}
active = {}
runtime = None
tasks = set()


def save():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


qt = QApplication([])
qt.setQuitOnLastWindowClosed(False)
loop = QEventLoop(qt)
asyncio.set_event_loop(loop)
fixture = QWidget()
fixture.setWindowTitle("Interview Assistant - controlled screenshot test")
fixture.setStyleSheet("QWidget {background:#edf4fc;color:#152638;} QPushButton {background:#ffffff; padding:16px; font-size:18px;}")
layout = QVBoxLayout(fixture)
title = QLabel("CONTROLLED SCREENSHOT TEST — no personal content")
title.setStyleSheet("font-size:30px;font-weight:bold")
layout.addWidget(title)
content = QLabel()
content.setTextFormat(Qt.TextFormat.PlainText)
content.setAlignment(Qt.AlignmentFlag.AlignCenter)
content.setStyleSheet("font-family:Consolas; font-size:32px; background:#c5def7; padding:60px;")
layout.addWidget(content, 1)
status = QLabel("Starting local model connection…")
status.setStyleSheet("font-size:18px")
layout.addWidget(status)
buttons = QHBoxLayout()
layout.addLayout(buttons)


def set_fixture(name):
    fixture_id[0] = name
    if name == "A":
        content.setText("CASE A — ORDER LOOKUP\n\ndef get_order(request, order_id):\n    order = db.get_order(order_id)\n    return order.to_json()\n\nExpected owner check is missing.\nMarker: BLUE-42")
        content.setStyleSheet("font-family:Consolas;font-size:32px;background:#c5def7;padding:60px;")
    else:
        content.setText("CASE B — DATABASE QUERY\n\ndef find_user(name):\n    sql = \"SELECT * FROM users WHERE name='\" + name + \"'\"\n    return db.execute(sql)\n\nUse a parameterized query.\nMarker: ORANGE-73")
        content.setStyleSheet("font-family:Consolas;font-size:32px;background:#ffd4a3;padding:60px;")


async def ask():
    global active
    if runtime is None or not runtime.is_started or active.get("pending"):
        return
    question = "Проанализируйте код на экране: назовите маркер, уязвимость и исправление."
    active = {"fixture": fixture_id[0], "manual_pending_before": runtime.manual_image_path is not None,
              "started": time.perf_counter(), "pending": True}
    now = time.monotonic()
    # Unique wording prevents intentional detector duplicate suppression.
    question += f" Проверка {len(requests) + 1}."
    model_requests_before = len(report.get("model_requests", []))
    try:
        await runtime.handle_hypothesis(TranscriptHypothesis(AudioSource.SYSTEM, question, "ru", True, now - 1, now))
        active["answer"] = runtime.application.ribbon.answer_text
        new_requests = report.get("model_requests", [])[model_requests_before:]
        active["status"] = (
            "completed" if new_requests and new_requests[-1].get("status") == "completed"
            and active["answer"].strip() else "failed"
        )
    except Exception as error:
        active.update(status="failed", error_type=type(error).__name__)
    active["total_seconds"] = round(time.perf_counter() - active.pop("started"), 4)
    active["pending"] = False
    active["manual_pending_after"] = runtime.manual_image_path is not None
    requests.append(dict(active))
    save()


def schedule(coro):
    task = asyncio.create_task(coro)
    tasks.add(task)
    task.add_done_callback(tasks.discard)


async def finish():
    if runtime:
        temp_dir = runtime.services.capture.temp_directory
        await runtime.shutdown()
        report["temporary_capture_directory_removed"] = not temp_dir.exists()
        runtime.application.shutdown()
    report["shutdown_completed"] = True
    save()
    fixture.close()
    loop.stop()


for label, callback in (
    ("Fixture A", lambda: set_fixture("A")),
    ("Fixture B", lambda: set_fixture("B")),
    ("Ask about screen", lambda: schedule(ask())),
    ("Finish test", lambda: schedule(finish())),
):
    button = QPushButton(label)
    button.clicked.connect(callback)
    buttons.addWidget(button)
set_fixture("A")
screen = qt.primaryScreen()
fixture.setGeometry(screen.virtualGeometry())
fixture.show()
report["fixture_geometry"] = [fixture.x(), fixture.y(), fixture.width(), fixture.height()]


async def setup():
    global runtime
    config = AppConfig.load(ROOT / "config.yaml")
    client = MeasuredVisionClient(config.lmstudio.host, config.lmstudio.port,
                            SecretStore(env_path=config.env_path).get_lm_token())
    models = [m for m in await client.list_model_details() if m.loaded_instances and m.capabilities and m.capabilities.vision]
    if len(models) != 1:
        status.setText("Requires exactly one loaded vision model")
        await client.aclose()
        return
    config.provider = "lmstudio"
    config.lmstudio.text_model = config.lmstudio.vision_model = models[0].key
    config.mcp.backend = "off"
    app = InterviewApplication(qt, EventBus(), StateMachine(), overlay_settings=None, allow_unchecked_start=True)
    capture = MeasuredCapture()
    runtime = ApplicationRuntime(app, config, RuntimeServices(
        audio=SilentLifecycle(), stt=SilentLifecycle(), hotkeys=HotkeyManager(app.events, DEFAULT_HOTKEY_BINDINGS),
        capture=capture, registry=ModelRegistry(client), client=client,
        coordinator=RequestCoordinator(app.events, client), transcript_store=TranscriptStore(),
        question_detector=QuestionDetector(cooldown_seconds=0), search_policy=SearchPolicy("off"),
    ))
    app.events.notification.connect(lambda text: (notifications.append(text), save()))
    app.events.answer_delta.connect(lambda _id, text: active.setdefault(
        "first_answer_seconds", round(time.perf_counter() - active.get("started", time.perf_counter()), 4)) if text and active.get("pending") else None)
    await runtime.start()
    report["affinity"] = asdict(app.ribbon.affinity_result)
    report["model"] = models[0].key
    save()


timer = QTimer()
timer.timeout.connect(lambda: status.setText(
    f"Ready: {bool(runtime and runtime.is_started)} | Pending screenshot: {bool(runtime and runtime.manual_image_path)} | Captures: {len(captures)} | Completed requests: {len(requests)}"
))
timer.start(200)
with loop:
    loop.create_task(setup())
    loop.run_forever()
