from interview_assistant.stt.stable_prefix import StablePrefix


def test_only_common_words_become_stable() -> None:
    prefix = StablePrefix()
    assert prefix.update("design a short") == ""
    assert prefix.update("design a short link") == "design a short"
    assert prefix.update("design a short link service") == "design a short link"
