"""Small bilingual speech checks against the same decoder used by the session."""

import asyncio
import json
from pathlib import Path
import re
import sys
import wave

import numpy as np

from interview_assistant.stt.engine import TranscriptionEngine


class SpeechFixtureProbe:
    def __init__(
        self, engine: TranscriptionEngine, *, lock: asyncio.Lock | None = None,
        root: Path | None = None,
    ) -> None:
        self.engine = engine
        self.lock = lock if lock is not None else asyncio.Lock()
        app_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
        self.root = root if root is not None else app_root / "assets" / "diagnostics"

    async def __call__(self, language: str) -> bool:
        if language not in {"en", "ru"}:
            raise ValueError("Unsupported speech fixture language")
        async with self.lock:
            # Keep the lock until native inference exits, including cancellation.
            task = asyncio.create_task(asyncio.to_thread(self._run, language))
            cancellation: asyncio.CancelledError | None = None
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError as error:
                    if cancellation is None:
                        cancellation = error
                except Exception:
                    break
            # Observe decoder failures before propagating cancellation, matching
            # the capture worker's native-operation cleanup semantics.
            result = task.result()
            if cancellation is not None:
                raise cancellation
            return result

    def _run(self, language: str) -> bool:
        manifest = json.loads((self.root / "speech-fixtures.json").read_text(encoding="utf-8"))
        fixture = next(f for f in manifest["fixtures"] if f["language"] == language)
        with wave.open(str(self.root / f"stt-{language}.wav"), "rb") as audio:
            if audio.getnchannels() != 1 or audio.getsampwidth() != 2 or audio.getframerate() != 16000:
                raise ValueError("Speech fixture must be mono PCM16 at 16 kHz")
            samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2")
        result = self.engine.transcribe(
            samples.astype(np.float32) / 32768.0,
            beam_size=1, condition_on_previous_text=False,
            language=language, multilingual=False,
        )
        words = set(re.findall(r"\w+", result.text.casefold()))
        return bool(words) and all(word.casefold() in words for word in fixture["keywords"])
