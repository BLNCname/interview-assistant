from dataclasses import FrozenInstanceError
from typing import Literal, get_type_hints

import pytest

from interview_assistant.audio.models import AudioSource
from interview_assistant.transcript.detector import DetectedQuestion, QuestionDetector


class ManualClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.mark.parametrize("source", [AudioSource.SYSTEM, AudioSource.MICROPHONE])
def test_final_question_from_either_source_preserves_trigger_source(
    source: AudioSource,
) -> None:
    result = QuestionDetector(cooldown_seconds=0).detect(
        source,
        "Как работает B-tree?",
    )

    assert result is not None
    assert result.trigger_source is source


def test_partial_system_hypothesis_never_auto_triggers() -> None:
    detector = QuestionDetector()

    assert detector.detect(AudioSource.SYSTEM, "How does a B-tree work?", is_final=False) is None


def test_system_design_question_is_classified() -> None:
    detector = QuestionDetector()

    result = detector.detect(
        AudioSource.SYSTEM,
        "Спроектируйте сервис коротких ссылок",
    )

    assert result is not None
    assert result.kind == "system_design"


@pytest.mark.parametrize(
    ("text", "expected_kind"),
    [
        ("What is eventual consistency?", "theory"),
        ("Как работает B-tree?", "theory"),
        ("Implement binary search in Python", "coding"),
        ("Реализуйте алгоритм LRU-кэша", "coding"),
        ("Design a scalable URL shortener", "system_design"),
        ("Спроектируйте распределённый чат", "system_design"),
        ("Tell me about a time you resolved a conflict", "behavioral"),
        ("Расскажите о случае конфликта в команде", "behavioral"),
        ("What is wrong with the code on this screenshot?", "screen_analysis"),
        ("Проанализируйте код на экране", "screen_analysis"),
    ],
)
def test_ru_and_en_question_intents_are_classified(
    text: str,
    expected_kind: str,
) -> None:
    detector = QuestionDetector()

    result = detector.detect(AudioSource.SYSTEM, text)

    assert result is not None
    assert result.kind == expected_kind


def test_non_question_declarative_text_is_ignored() -> None:
    detector = QuestionDetector()

    assert detector.detect(AudioSource.SYSTEM, "The cache is warm and ready") is None


@pytest.mark.parametrize(
    "text",
    [
        "Our system design uses a scalable cache",
        "Наша распределённая система использует кэш",
        "Your experience includes resolving a conflict",
        "Ваш опыт включает конфликт в команде",
        "We implement binary search in Python",
        "Мы планируем реализовать алгоритм двоичного поиска",
        "We review the code shown on this screenshot",
        "В документации сказано: посмотрите код на экране",
        "We explain the cache behavior in onboarding",
        "В документации просят: объясните работу кэша",
    ],
)
def test_intent_keywords_in_declarative_text_do_not_auto_trigger(text: str) -> None:
    detector = QuestionDetector()

    assert detector.detect(AudioSource.SYSTEM, text) is None


@pytest.mark.parametrize(
    "text",
    [
        "Хотелось бы услышать ваше мнение о CAP theorem",
        "Как вы считаете, когда нужна eventual consistency",
        "Что вы думаете о микросервисах",
        "Раскройте тему индексов в PostgreSQL",
        "Можете подробнее рассказать про optimistic locking",
        "I'd like to hear your opinion on eventual consistency",
        "What's your view on microservices",
        "Could you elaborate on optimistic locking",
        "Walk me through a B-tree lookup",
    ],
)
def test_indirect_ru_en_interview_requests_trigger(text: str) -> None:
    assert QuestionDetector(cooldown_seconds=0).detect(AudioSource.SYSTEM, text)


@pytest.mark.parametrize(
    "text",
    [
        "У него интересное мнение о микросервисах",
        "В документации встречается фраза как вы считаете",
        "My opinion on caching changed last year",
        "The guide contains the phrase walk me through",
        "Я использовал Redis в прошлом проекте",
    ],
)
def test_indirect_keywords_inside_narration_do_not_trigger(text: str) -> None:
    assert QuestionDetector().detect(AudioSource.MICROPHONE, text) is None


def test_polite_anchored_question_is_classified() -> None:
    detector = QuestionDetector()

    result = detector.detect(
        AudioSource.SYSTEM,
        "Could you compare Redis and Memcached?",
    )

    assert result is not None
    assert result.kind == "theory"


@pytest.mark.parametrize(
    "text",
    [
        "Please, could you compare Redis and Memcached",
        "Пожалуйста, могли бы вы сравнить Redis и Memcached",
    ],
)
def test_polite_prefixed_question_without_question_mark_is_classified(text: str) -> None:
    detector = QuestionDetector()

    result = detector.detect(AudioSource.SYSTEM, text, is_final=True)

    assert result is not None
    assert result.kind == "theory"


def test_detection_normalizes_whitespace() -> None:
    detector = QuestionDetector()

    result = detector.detect(AudioSource.SYSTEM, "  How\n does\tRedis work?  ")

    assert result is not None
    assert result.text == "How does Redis work?"


def test_exact_duplicate_is_suppressed_until_cooldown_expires() -> None:
    clock = ManualClock(10.0)
    detector = QuestionDetector(cooldown_seconds=5.0, clock=clock)

    first = detector.detect(AudioSource.SYSTEM, "How does a B-tree work?")
    clock.advance(4.9)
    duplicate = detector.detect(AudioSource.SYSTEM, "  how does a B-tree work? ")
    clock.advance(0.1)
    after_cooldown = detector.detect(AudioSource.SYSTEM, "How does a B-tree work?")

    assert first == DetectedQuestion(
        1,
        "theory",
        "How does a B-tree work?",
        10.0,
        AudioSource.SYSTEM,
    )
    assert duplicate is None
    assert after_cooldown == DetectedQuestion(
        2,
        "theory",
        "How does a B-tree work?",
        15.0,
        AudioSource.SYSTEM,
    )


def test_same_question_from_both_sources_is_suppressed() -> None:
    detector = QuestionDetector(cooldown_seconds=15)

    assert detector.detect(AudioSource.SYSTEM, "What is a B-tree?") is not None
    assert detector.detect(AudioSource.MICROPHONE, "What is a B-tree?") is None


def test_semantic_near_duplicate_is_suppressed_during_cooldown() -> None:
    clock = ManualClock(20.0)
    detector = QuestionDetector(cooldown_seconds=10.0, clock=clock)

    first = detector.detect(AudioSource.SYSTEM, "How does a B-tree work?")
    clock.advance(1.0)
    near_duplicate = detector.detect(
        AudioSource.SYSTEM,
        "Could you explain how B trees work?",
    )

    assert first is not None
    assert near_duplicate is None


def test_similar_question_shape_with_different_subject_is_not_suppressed() -> None:
    clock = ManualClock(25.0)
    detector = QuestionDetector(cooldown_seconds=10.0, clock=clock)

    cache = detector.detect(AudioSource.SYSTEM, "How would you design a cache?")
    clock.advance(1.0)
    chat = detector.detect(AudioSource.SYSTEM, "How would you design a chat?")

    assert cache is not None
    assert chat is not None
    assert (cache.request_id, chat.request_id) == (1, 2)


def test_question_with_new_qualifier_is_not_suppressed() -> None:
    clock = ManualClock(27.0)
    detector = QuestionDetector(cooldown_seconds=10.0, clock=clock)

    redis = detector.detect(AudioSource.SYSTEM, "How does Redis work?")
    clock.advance(1.0)
    redis_cluster = detector.detect(
        AudioSource.SYSTEM,
        "How does Redis cluster work?",
    )

    assert redis is not None
    assert redis_cluster is not None
    assert (redis.request_id, redis_cluster.request_id) == (1, 2)


def test_strong_coding_imperative_without_question_mark_is_classified() -> None:
    detector = QuestionDetector()

    result = detector.detect(AudioSource.SYSTEM, "Implement an LRU cache")

    assert result is not None
    assert result.kind == "coding"


def test_manual_force_bypasses_intent_and_deduplication() -> None:
    clock = ManualClock(30.0)
    detector = QuestionDetector(clock=clock)

    first = detector.force("  Explain\n the code  ")
    second = detector.force("Explain the code")

    assert first == DetectedQuestion(1, "manual", "Explain the code", 30.0)
    assert second == DetectedQuestion(2, "manual", "Explain the code", 30.0)
    assert first.trigger_source is None
    assert second.trigger_source is None


def test_detected_question_has_exact_frozen_typed_contract() -> None:
    question = DetectedQuestion(1, "coding", "Implement a queue", 1.0)

    assert (
        get_type_hints(DetectedQuestion)["kind"]
        == Literal[
            "theory",
            "coding",
            "system_design",
            "behavioral",
            "screen_analysis",
            "manual",
        ]
    )
    with pytest.raises(FrozenInstanceError):
        question.text = "changed"  # type: ignore[misc]
