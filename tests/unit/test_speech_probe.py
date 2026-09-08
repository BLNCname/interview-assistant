import asyncio
from threading import Event

import numpy as np
import pytest

from interview_assistant.diagnostics.speech import SpeechFixtureProbe
from interview_assistant.stt.engine import TranscriptionResult


async def test_real_fixture_probe_checks_words_and_forces_fixture_language():
    calls = []

    class Engine:
        def transcribe(self, samples, **options):
            calls.append(options)
            assert samples.dtype == np.float32 and len(samples) > 16000
            text = (
                "Explain binary search in a sorted array"
                if options["language"] == "en"
                else "Объясните двоичный поиск в отсортированном массиве"
            )
            return TranscriptionResult(text, options["language"], 1.0)

    probe = SpeechFixtureProbe(Engine())
    assert await probe("ru")
    assert await probe("en")
    assert [c["language"] for c in calls] == ["ru", "en"]


async def test_empty_inference_is_not_passing_speech_evidence():
    class Engine:
        def transcribe(self, samples, **options):
            return TranscriptionResult("", "ru", 1.0)

    assert not await SpeechFixtureProbe(Engine())("en")


@pytest.mark.parametrize("decode_fails", [False, True])
async def test_repeated_cancellation_drains_native_inference_before_unlocking(decode_fails):
    first_entered = Event()
    release_first = Event()
    second_entered = Event()

    class BlockingEngine:
        def transcribe(self, samples, **options):
            if options["language"] == "en":
                first_entered.set()
                if not release_first.wait(timeout=2.0):
                    raise AssertionError("test did not release the native decoder")
                if decode_fails:
                    raise RuntimeError("decoder failed")
                text = "Explain binary search in a sorted array"
            else:
                second_entered.set()
                text = "Объясните двоичный поиск в отсортированном массиве"
            return TranscriptionResult(text, options["language"], 1.0)

    lock = asyncio.Lock()
    probe = SpeechFixtureProbe(BlockingEngine(), lock=lock)
    first = asyncio.create_task(probe("en"))
    second = None
    try:
        assert await asyncio.to_thread(first_entered.wait, 1.0)
        first.cancel()
        await asyncio.sleep(0)
        first.cancel()
        await asyncio.sleep(0)
        second = asyncio.create_task(probe("ru"))
        # The second probe may run only after the actual blocking call exits.
        assert not await asyncio.to_thread(second_entered.wait, 0.05)
        assert lock.locked()
        assert not first.done()
    finally:
        release_first.set()
        results = await asyncio.gather(
            first, *([second] if second is not None else []), return_exceptions=True,
        )

    expected_error = RuntimeError if decode_fails else asyncio.CancelledError
    assert isinstance(results[0], expected_error)
    assert results[1] is True
    assert not lock.locked()
