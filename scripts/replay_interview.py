"""Replay a consented 16-bit PCM WAV through real streaming STT and question detection.

This is an offline accuracy/throughput benchmark. It does not measure live WASAPI
dropouts or end-to-end display latency. Text is excluded from reports by default.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import wave
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from time import monotonic, perf_counter

import numpy as np

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from interview_assistant.audio.models import AudioFrame, AudioSource
from interview_assistant.audio.worker import _StreamingAudioConverter
from interview_assistant.config import AppConfig
from interview_assistant.stt.engine import (
    TranscriptHypothesis,
    TranscriptionEngine,
    TranscriptionResult,
    WhisperEngine,
)
from interview_assistant.stt.worker import StreamingSTTWorker
from interview_assistant.transcript.detector import QuestionDetector


class _TimedEngine:
    def __init__(self, engine: TranscriptionEngine) -> None:
        self.engine = engine
        self.durations: list[float] = []

    def transcribe(
        self, audio: np.ndarray, *, beam_size: int, condition_on_previous_text: bool,
    ) -> TranscriptionResult:
        started = perf_counter()
        try:
            return self.engine.transcribe(
                audio, beam_size=beam_size,
                condition_on_previous_text=condition_on_previous_text,
            )
        finally:
            self.durations.append(perf_counter() - started)


def _submit_and_wait(worker: StreamingSTTWorker, frame: AudioFrame, timeout_s: float) -> None:
    # Backpressure retains every recording sample, independently of decode speed.
    worker.submit(frame)
    queue = worker.system_queue if frame.source is AudioSource.SYSTEM else worker.microphone_queue
    deadline = monotonic() + timeout_s
    with queue.all_tasks_done:
        while queue.unfinished_tasks:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError("STT decode exceeded the recording replay deadline")
            queue.all_tasks_done.wait(timeout=remaining)
    if worker.last_error is not None:
        raise RuntimeError("STT decode failed during recording replay") from None


def word_error_rate(reference: str, actual: str) -> float:
    expected = re.findall(r"\w+", reference.casefold())
    recognized = re.findall(r"\w+", actual.casefold())
    if not expected:
        raise ValueError("Reference must contain at least one word")
    previous = list(range(len(recognized) + 1))
    for row, expected_word in enumerate(expected, start=1):
        current = [row]
        for column, actual_word in enumerate(recognized, start=1):
            current.append(min(
                current[-1] + 1, previous[column] + 1,
                previous[column - 1] + (expected_word != actual_word),
            ))
        previous = current
    return previous[-1] / len(expected)


def replay_wav(
    path: Path,
    *,
    engine: TranscriptionEngine,
    source: AudioSource = AudioSource.SYSTEM,
    include_text: bool = False,
    reference: str | None = None,
    max_seconds: float | None = None,
    timeout_s: float = 120.0,
) -> dict[str, object]:
    """Decode one recording without a microphone, playback device, or LLM server."""

    if timeout_s <= 0 or (max_seconds is not None and max_seconds <= 0):
        raise ValueError("Replay duration and timeout must be positive")
    if reference is not None and not re.search(r"\w", reference):
        raise ValueError("Reference must contain at least one word")
    finals: list[TranscriptHypothesis] = []
    questions: list[dict[str, object]] = []
    timeline = 0.0
    partial_count = 0
    detector = QuestionDetector(clock=lambda: timeline)

    def on_hypothesis(item: TranscriptHypothesis) -> None:
        nonlocal timeline, partial_count
        if not item.is_final:
            partial_count += 1
            return
        finals.append(item)
        timeline = item.ended_at
        question = detector.detect(item.source, item.text)
        if question is not None:
            event: dict[str, object] = {
                "kind": question.kind,
                "started_at_seconds": item.started_at,
                "ended_at_seconds": item.ended_at,
            }
            if include_text:
                event["text"] = question.text
            questions.append(event)

    timed = _TimedEngine(engine)
    worker = StreamingSTTWorker(engine=timed, on_hypothesis=on_hypothesis)
    converter = _StreamingAudioConverter()
    processed = 0
    started = perf_counter()
    with wave.open(str(path), "rb") as recording:
        if recording.getsampwidth() != 2 or recording.getcomptype() != "NONE":
            raise ValueError("Recording must use uncompressed 16-bit PCM WAV")
        rate, channels = recording.getframerate(), recording.getnchannels()
        if channels not in {1, 2}:
            raise ValueError("Recording must have one or two channels")
        available = recording.getnframes()
        frame_limit = available if max_seconds is None else min(available, round(max_seconds * rate))
        if frame_limit <= 0:
            raise ValueError("Recording is empty")
        chunk_frames = max(1, round(rate * 0.02))
        worker.start()
        try:
            while processed < frame_limit:
                raw = recording.readframes(min(chunk_frames, frame_limit - processed))
                if not raw:
                    break
                samples = converter.convert(raw, rate, channels)
                _submit_and_wait(worker, AudioFrame(source, processed / rate, samples), timeout_s)
                processed += len(raw) // (channels * 2)
            # Flush a recording that ends immediately after its last spoken word.
            _submit_and_wait(
                worker, AudioFrame(source, processed / rate, np.zeros(12_800, dtype=np.float32)),
                timeout_s,
            )
        finally:
            worker.stop(timeout=5.0)
    elapsed = perf_counter() - started
    duration = processed / rate
    transcript = " ".join(item.text for item in finals)
    report: dict[str, object] = {
        "schema_version": 1,
        "status": "ok",
        "mode": "offline_recording_replay",
        "source": source.value,
        "audio_seconds": duration,
        "elapsed_seconds": elapsed,
        "real_time_factor": elapsed / duration if duration else 0.0,
        "decode_count": len(timed.durations),
        "decode_seconds": sum(timed.durations),
        "decode_p95_ms": float(np.percentile(timed.durations, 95) * 1_000)
        if timed.durations else 0.0,
        "partial_count": partial_count,
        "final_count": len(finals),
        "question_count": len(questions),
        "languages": dict(Counter(item.language for item in finals)),
        "questions": questions,
        "includes_model_cold_start": True,
    }
    if include_text:
        report["transcript"] = transcript
    if reference is not None:
        report["word_error_rate"] = word_error_rate(reference, transcript)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording", type=Path)
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--model", help="Whisper model alias or local CTranslate2 model directory")
    parser.add_argument("--device", choices=("cuda", "cpu"))
    parser.add_argument("--compute-type", choices=("float16", "int8_float16", "int8", "float32"))
    parser.add_argument("--language", choices=("auto", "ru", "en"))
    parser.add_argument("--source", choices=("interviewer", "you"), default="interviewer")
    parser.add_argument("--max-seconds", type=float)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--include-text", action="store_true")
    parser.add_argument("--reference", type=Path, help="UTF-8 reference transcript for WER")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        config = AppConfig.load(args.config) if args.config.exists() else AppConfig()
        device = args.device or config.audio.device
        compute_type = args.compute_type or (
            "int8" if args.device == "cpu" else config.audio.compute_type
        )
        engine = WhisperEngine(
            args.model or config.audio.stt_model, device=device, compute_type=compute_type,
            language=args.language or config.audio.language,
        )
        report = replay_wav(
            args.recording, engine=engine, source=AudioSource(args.source),
            include_text=args.include_text,
            reference=args.reference.read_text(encoding="utf-8") if args.reference else None,
            max_seconds=args.max_seconds, timeout_s=args.timeout_seconds,
        )
        report["device"] = device
        report["compute_type"] = compute_type
        encoded = json.dumps(report, ensure_ascii=True, indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(encoded, encoding="utf-8")
        else:
            print(encoded, end="")
    except Exception as error:
        print(f"Recording replay failed ({type(error).__name__}).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
