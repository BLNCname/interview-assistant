from __future__ import annotations

import importlib
import wave
from pathlib import Path

import numpy as np
import pytest

from interview_assistant.stt.engine import TranscriptionResult


def _wav(path: Path, *, channels: int = 1, rate: int = 16_000, silent: bool = False) -> None:
    signal = np.zeros(rate, dtype="<i2") if silent else np.full(rate, 8_000, dtype="<i2")
    if channels > 1:
        signal = np.repeat(signal[:, None], channels, axis=1)
    with wave.open(str(path), "wb") as stream:
        stream.setparams((channels, 2, rate, 0, "NONE", "not compressed"))
        stream.writeframes(signal.tobytes())


class _Engine:
    def __init__(self) -> None:
        self.calls = 0

    def transcribe(self, audio: np.ndarray, **_options: object) -> TranscriptionResult:
        self.calls += 1
        assert audio.dtype == np.float32
        return TranscriptionResult("What is a Python decorator?", "en", 0.99)


@pytest.mark.parametrize("channels,rate", [(1, 16_000), (2, 48_000)])
def test_recording_replay_flushes_last_utterance_and_detects_question(
    tmp_path: Path, channels: int, rate: int,
) -> None:
    module = importlib.import_module("scripts.replay_interview")
    recording = tmp_path / "recording.wav"
    _wav(recording, channels=channels, rate=rate)
    engine = _Engine()

    report = module.replay_wav(recording, engine=engine)

    assert report["status"] == "ok"
    assert report["audio_seconds"] == pytest.approx(1.0)
    assert report["final_count"] == 1
    assert report["question_count"] == 1
    assert report["questions"][0]["kind"] == "theory"
    assert report["decode_count"] == engine.calls > 0
    assert report["real_time_factor"] >= 0
    assert "text" not in report["questions"][0]
    assert "transcript" not in report
    assert str(recording) not in str(report)


def test_silent_recording_does_not_call_decoder(tmp_path: Path) -> None:
    module = importlib.import_module("scripts.replay_interview")
    recording = tmp_path / "silence.wav"
    _wav(recording, silent=True)
    engine = _Engine()
    report = module.replay_wav(recording, engine=engine)
    assert report["final_count"] == report["question_count"] == engine.calls == 0


def test_replay_text_is_opt_in_and_reference_measures_word_error_rate(tmp_path: Path) -> None:
    module = importlib.import_module("scripts.replay_interview")
    recording = tmp_path / "recording.wav"
    _wav(recording)
    report = module.replay_wav(
        recording, engine=_Engine(), include_text=True,
        reference="What is a Python generator?",
    )
    assert report["transcript"] == "What is a Python decorator?"
    assert report["questions"][0]["text"] == "What is a Python decorator?"
    assert report["word_error_rate"] == pytest.approx(0.2)


def test_replay_raises_decoder_failure_instead_of_reporting_success(tmp_path: Path) -> None:
    module = importlib.import_module("scripts.replay_interview")
    recording = tmp_path / "recording.wav"
    _wav(recording)

    class FailedEngine:
        def transcribe(self, *_args: object, **_options: object) -> TranscriptionResult:
            raise RuntimeError("private provider path")

    with pytest.raises(RuntimeError, match="STT decode failed") as error:
        module.replay_wav(recording, engine=FailedEngine())
    assert "private provider path" not in str(error.value)


def test_recording_format_validation_precedes_decoder(tmp_path: Path) -> None:
    module = importlib.import_module("scripts.replay_interview")
    recording = tmp_path / "8bit.wav"
    with wave.open(str(recording), "wb") as stream:
        stream.setparams((1, 1, 16_000, 0, "NONE", "not compressed"))
        stream.writeframes(b"\x80" * 16_000)
    with pytest.raises(ValueError, match="16-bit PCM"):
        module.replay_wav(recording, engine=_Engine())
