from interview_assistant.stt.engine import LanguageLatch


def test_low_confidence_does_not_flip_language() -> None:
    latch = LanguageLatch(threshold=0.80)
    assert latch.update("ru", 0.95) == "ru"
    assert latch.update("en", 0.55) == "ru"
    latch.reset_utterance()
    assert latch.update("en", 0.92) == "en"
