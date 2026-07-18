from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite
import re
from threading import Lock
from time import monotonic
from typing import Literal, TypeAlias

from interview_assistant.audio.models import AudioSource

QuestionKind: TypeAlias = Literal[
    "theory",
    "coding",
    "system_design",
    "behavioral",
    "screen_analysis",
    "manual",
]
AutoQuestionKind: TypeAlias = Literal[
    "theory",
    "coding",
    "system_design",
    "behavioral",
    "screen_analysis",
]


@dataclass(frozen=True, slots=True)
class DetectedQuestion:
    request_id: int
    kind: Literal[
        "theory",
        "coding",
        "system_design",
        "behavioral",
        "screen_analysis",
        "manual",
    ]
    text: str
    detected_at: float
    trigger_source: AudioSource | None = None


@dataclass(frozen=True, slots=True)
class _RecentQuestion:
    detected_at: float
    canonical: str
    semantic_tokens: frozenset[str]


_WHITESPACE = re.compile(r"\s+")
_CANONICAL_PUNCTUATION = re.compile(r"[^\w]+", re.UNICODE)
_TOKEN = re.compile(r"\w+", re.UNICODE)
_LEXICAL_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)

_INTERROGATIVE = re.compile(
    r"^(?:(?:please|пожалуйста)[,\s]+)?(?:"
    r"who|what|when|where|why|how|which|whose|whom|"
    r"can\s+you|could\s+you|would\s+you|will\s+you|should\s+(?:i|we)|"
    r"do\s+you|does\s+|did\s+|is\s+|are\s+|was\s+|were\s+|"
    r"have\s+you|has\s+|"
    r"кто|что|когда|где|куда|откуда|почему|зачем|как|ка(?:к|кая|кие|кой|кую)|"
    r"сколько|можно\s+ли|можете\s+ли|могли\s+бы|является\s+ли"
    r")\b",
    re.IGNORECASE,
)
_THEORY_IMPERATIVE = re.compile(
    r"\b(?:explain|describe|compare|define|tell\s+me|"
    r"объясните|опишите|сравните|расскажите|дайте\s+определение)\b",
    re.IGNORECASE,
)
_EXPLICIT_REQUEST = re.compile(
    r"^(?:(?:please|пожалуйста)[,\s]+)?(?:"
    r"explain|describe|compare|define|tell\s+me|"
    r"analy[sz]e|inspect|review|look\s+at|find\s+(?:the\s+)?(?:bug|error)|"
    r"design|architect|scale|implement|write|code|debug|optimi[sz]e|solve|"
    r"объясните|опишите|сравните|расскажите|дайте\s+определение|"
    r"проанализируйте|посмотрите|разберите|найдите\s+(?:ошибку|баг)|"
    r"спроектируйте|спроектировать|разработайте\s+архитектуру|масштабируйте|"
    r"реализуйте|реализовать|напишите|закодируйте|отладьте|оптимизируйте|решите"
    r")\b",
    re.IGNORECASE,
)
_INDIRECT_REQUEST_PREFIX = re.compile(
    r"^(?:(?:please|пожалуйста)[,\s]+)?(?:"
    r"i(?:'d|\s+would)\s+like\s+to\s+hear\s+your\s+opinion\s+(?:on|about)\b|"
    r"what(?:'s|\s+is)\s+your\s+view\s+(?:on|about)\b|"
    r"could\s+you\s+elaborate\s+(?:on|about)\b|"
    r"walk\s+me\s+through\b|"
    r"хотелось\s+бы\s+услышать\s+(?:(?:ваше|вашу)\s+)?мнение\s+(?:о|про)\b|"
    r"как\s+вы\s+считаете\b|"
    r"что\s+вы\s+думаете\s+(?:о|про)\b|"
    r"раскройте\s+тему\b|"
    r"можете\s+подробнее\s+рассказать\s+(?:о|про)\b"
    r")",
    re.IGNORECASE,
)
_SCREEN_OBJECT = re.compile(
    r"\b(?:screenshot|screen|image|picture|diagram|code\s+shown|"
    r"скриншот\w*|экран\w*|изображени\w*|картин\w*|диаграмм\w*|"
    r"код\s+на\s+экране)\b",
    re.IGNORECASE,
)
_SCREEN_ACTION = re.compile(
    r"\b(?:analy[sz]e|inspect|review|look\s+at|find\s+(?:the\s+)?(?:bug|error)|"
    r"проанализируйте|посмотрите|разберите|найдите\s+(?:ошибку|баг))\b",
    re.IGNORECASE,
)
_BEHAVIORAL = re.compile(
    r"\b(?:tell\s+me\s+about\s+a\s+time|describe\s+(?:a\s+)?(?:time|situation)|"
    r"your\s+(?:experience|strengths?|weaknesses?)|resolved?\s+(?:a\s+)?conflict|"
    r"handled?\s+(?:a\s+)?(?:challenge|failure|disagreement)|"
    r"расскажите\s+о\s+(?:случае|ситуации|сво[её]м\s+опыте)|"
    r"опишите\s+(?:случай|ситуацию)|конфликт\w*\s+в\s+команд\w*|"
    r"ваш(?:и|ем)?\s+(?:опыт|сильн\w*|слаб\w*))\b",
    re.IGNORECASE,
)
_SYSTEM_ACTION = re.compile(
    r"\b(?:design|architect|scale|спроектируйте|спроектировать|"
    r"разработайте\s+архитектуру|масштабируйте)\b",
    re.IGNORECASE,
)
_SYSTEM_OBJECT = re.compile(
    r"\b(?:system|service|architecture|distributed|scalable|url\s+shortener|"
    r"load\s+balancer|microservice|message\s+queue|notification|chat|cache|"
    r"систем\w*|сервис\w*|архитектур\w*|распредел[её]нн\w*|масштабиру\w*|"
    r"коротк\w*\s+ссыл\w*|балансировщик\w*|микросервис\w*|очеред\w*|"
    r"уведомлен\w*|чат\w*|кэш\w*)\b",
    re.IGNORECASE,
)
_SYSTEM_PHRASE = re.compile(
    r"\b(?:system\s+design|high[- ]level\s+design|distributed\s+system|"
    r"системн\w*\s+дизайн\w*|распредел[её]нн\w*\s+систем\w*)\b",
    re.IGNORECASE,
)
_CODING_ACTION = re.compile(
    r"\b(?:implement|write|code|debug|optimi[sz]e|solve|"
    r"реализуйте|реализовать|напишите|закодируйте|отладьте|оптимизируйте|решите)\b",
    re.IGNORECASE,
)
_CODING_IMPERATIVE = re.compile(
    r"^(?:please\s+)?(?:implement|write|code|debug|optimi[sz]e|solve|"
    r"реализуйте|реализовать|напишите|закодируйте|отладьте|оптимизируйте|решите)\b",
    re.IGNORECASE,
)
_CODING_OBJECT = re.compile(
    r"\b(?:algorithm|data\s+structure|function|class|method|complexity|"
    r"binary\s+search|linked\s+list|tree|graph|array|leetcode|python|java|"
    r"алгоритм\w*|структур\w*\s+данн\w*|функци\w*|класс\w*|метод\w*|"
    r"сложност\w*|двоичн\w*\s+поиск\w*|список\w*|дерев\w*|граф\w*|"
    r"массив\w*|питон\w*)\b",
    re.IGNORECASE,
)

_SEMANTIC_STOPWORDS = frozenset(
    {
        "a",
        "about",
        "an",
        "can",
        "could",
        "describe",
        "did",
        "do",
        "does",
        "explain",
        "how",
        "is",
        "me",
        "please",
        "tell",
        "the",
        "to",
        "what",
        "would",
        "you",
        "бы",
        "как",
        "ли",
        "могли",
        "можете",
        "объясните",
        "опишите",
        "пожалуйста",
        "расскажите",
        "что",
    }
)


class QuestionDetector:
    """Classify final utterances from both audio sources and suppress repetitions."""

    def __init__(
        self,
        *,
        cooldown_seconds: float = 15.0,
        similarity_threshold: float = 0.88,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if not isfinite(cooldown_seconds) or cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be finite and non-negative")
        if not 0.0 <= similarity_threshold <= 1.0:
            raise ValueError("similarity_threshold must be between 0 and 1")
        self.cooldown_seconds = cooldown_seconds
        self.similarity_threshold = similarity_threshold
        self._clock = clock
        self._next_request_id = 1
        self._recent: deque[_RecentQuestion] = deque()
        self._lock = Lock()

    def detect(
        self,
        source: AudioSource,
        text: str,
        *,
        is_final: bool = True,
    ) -> DetectedQuestion | None:
        if not is_final:
            return None

        normalized = _normalize(text)
        if not normalized:
            return None
        kind = _classify(normalized)
        if kind is None:
            return None

        with self._lock:
            now = self._clock()
            self._prune_recent(now)
            canonical = _canonicalize(normalized)
            semantic_tokens = _semantic_tokens(normalized)
            if any(
                self._is_near_duplicate(canonical, semantic_tokens, recent)
                for recent in self._recent
            ):
                return None
            question = DetectedQuestion(
                self._allocate_id(),
                kind,
                normalized,
                now,
                source,
            )
            self._recent.append(_RecentQuestion(now, canonical, semantic_tokens))
            return question

    def force(self, text: str) -> DetectedQuestion:
        """Create a manual request regardless of audio role, intent, or cooldown."""

        normalized = _normalize(text)
        if not normalized:
            raise ValueError("manual question text must not be empty")
        with self._lock:
            return DetectedQuestion(
                self._allocate_id(),
                "manual",
                normalized,
                self._clock(),
            )

    def force_request(self, text: str) -> DetectedQuestion:
        """Explicitly named alias used by manual-request UI actions."""

        return self.force(text)

    def _allocate_id(self) -> int:
        request_id = self._next_request_id
        self._next_request_id += 1
        return request_id

    def _prune_recent(self, now: float) -> None:
        while self._recent and now - self._recent[0].detected_at >= self.cooldown_seconds:
            self._recent.popleft()

    def _is_near_duplicate(
        self,
        canonical: str,
        semantic_tokens: frozenset[str],
        recent: _RecentQuestion,
    ) -> bool:
        if canonical == recent.canonical:
            return True
        if min(len(semantic_tokens), len(recent.semantic_tokens)) < 2:
            return False
        overlap = len(semantic_tokens & recent.semantic_tokens)
        symmetric_coverage = overlap / max(
            len(semantic_tokens),
            len(recent.semantic_tokens),
        )
        return symmetric_coverage >= self.similarity_threshold


def _normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def _canonicalize(text: str) -> str:
    return _WHITESPACE.sub(
        " ", _CANONICAL_PUNCTUATION.sub(" ", text.casefold().replace("ё", "е"))
    ).strip()


def _semantic_tokens(text: str) -> frozenset[str]:
    tokens = (
        _stem_english(token)
        for token in _TOKEN.findall(text.casefold().replace("ё", "е"))
        if token not in _SEMANTIC_STOPWORDS
    )
    return frozenset(token for token in tokens if token)


def _stem_english(token: str) -> str:
    if not token.isascii() or len(token) <= 3:
        return token
    if token.endswith("ies") and len(token) > 4:
        return f"{token[:-3]}y"
    for suffix in ("ing", "ed"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            return token[: -len(suffix)]
    if token.endswith(("ches", "shes", "xes", "zes", "ses", "oes")):
        return token[:-2]
    if token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def _classify(text: str) -> AutoQuestionKind | None:
    question_like = "?" in text or bool(_INTERROGATIVE.search(text))
    indirect_prefix = _INDIRECT_REQUEST_PREFIX.search(text)
    indirect_request = bool(indirect_prefix and _LEXICAL_TOKEN.search(text, indirect_prefix.end()))
    if indirect_prefix is not None and not indirect_request:
        return None
    if not question_like and not _EXPLICIT_REQUEST.search(text) and not indirect_request:
        return None
    if _SCREEN_OBJECT.search(text) and (question_like or _SCREEN_ACTION.search(text)):
        return "screen_analysis"
    if _BEHAVIORAL.search(text):
        return "behavioral"
    if _SYSTEM_PHRASE.search(text) or (_SYSTEM_ACTION.search(text) and _SYSTEM_OBJECT.search(text)):
        return "system_design"
    if _CODING_ACTION.search(text) and (
        _CODING_OBJECT.search(text) or question_like or _CODING_IMPERATIVE.search(text)
    ):
        return "coding"
    if question_like or indirect_request or _THEORY_IMPERATIVE.search(text):
        return "theory"
    return None
