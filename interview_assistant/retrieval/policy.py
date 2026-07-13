import re
import unicodedata
from typing import Literal
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from .models import (
    CONTEXT7_ID,
    CONTEXT7_TOOLS,
    DUCKDUCKGO_ID,
    DUCKDUCKGO_TOOLS,
    SearchIntegration,
)


SearchMode = Literal["off", "auto", "forced"]

_EMAIL = re.compile(
    r"(?i)(?<![\w.+-])[\w.+-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+"
)
_LABELED_EMAIL = re.compile(
    r"(?i)\b(?:e-?mail|почта|электронная\s+почта)\s*:\s*"
    + _EMAIL.pattern.removeprefix("(?i)")
    + r"\s*[.,;!?]?"
)
_SOURCE_LINE = re.compile(
    r"(?im)^\s*(?:source|источник)\s*:\s*[^\r\n]*(?:\r?\n|$)"
)
_TIMESTAMP_PREFIX = re.compile(
    r"(?m)^\s*\[(?:\d{1,2}:){1,2}\d{1,2}(?:[.,]\d+)?\]\s*"
)
_CANDIDATE_NAME_LINE = re.compile(
    r"(?imu)^\s*(?:candidate|кандидат)\s*:\s*"
    r"[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё'’-]+"
    r"(?:\s+[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё'’-]+){0,3}\s*(?:\r?\n|$)"
)
_SPEAKER_PREFIX = re.compile(
    r"(?im)^\s*"
    r"(?:\[(?:\d{1,2}:){1,2}\d{1,2}(?:[.,]\d+)?\]\s*)?"
    r"(?:interviewer|candidate|speaker(?:\s+\d+)?|"
    r"интервьюер|кандидат|спикер(?:\s+\d+)?)"
    r"(?:\s*\[(?:\d{1,2}:){1,2}\d{1,2}(?:[.,]\d+)?\])?\s*:\s*"
)
_NAME_INTRODUCTION = re.compile(
    r"(?iu)\b(?:my\s+name\s+is|меня\s+зовут)\s+"
    r"[^,\r\n.!?]{1,100}(?:[,.;:!?]\s*|(?=\r?\n|$))"
)
_FIRST_PERSON_NAME = re.compile(
    r"(?u)\b(?:I\s+(?:am|['’]m)|Я)\s+"
    r"[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё'’-]+"
    r"(?:\s+[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё'’-]+){0,3}[.!?]?"
)
_NAME_METADATA = re.compile(
    r"(?iu)(?:^|(?<=[.!?])\s+)"
    r"(?:full\s+name|name|имя|фио)\s*:\s*[^\r\n.!?]{1,100}[.!?]?"
)
_LEADING_VOCATIVE = re.compile(
    r"^(?P<name>"
    r"(?:[A-ZА-ЯЁ][a-zа-яё'’-]+|[A-ZА-ЯЁ]{2,})"
    r"(?:\s+(?:[A-ZА-ЯЁ][a-zа-яё'’-]+|[A-ZА-ЯЁ]{2,})){0,2}"
    r"),\s*",
)
_URL = re.compile(r"(?i)https?://[^\s<>\"']+")

_CONTEXT_TERMS = re.compile(
    r"(?i)\b(?:api|sdk|docs?|documentation|library|framework|package|module|"
    r"апи|документац\w*|библиотек\w*|фреймворк\w*|пакет\w*|модул\w*)\b"
)
_DOCUMENTATION_TERMS = re.compile(
    r"(?i)\b(?:docs?|documentation|документац\w*)\b"
)
_TECHNICAL_OPERATION_TERMS = re.compile(
    r"(?i)\b(?:syntax|configure|configuration|setup|install|import|call|"
    r"calling|use|using|endpoint|request|method|function|parameter|argument|"
    r"decorator|dependency|lifespan|hook|"
    r"синтаксис\w*|настро\w*|установ\w*|импорт\w*|метод\w*|функц\w*|"
    r"параметр\w*|аргумент\w*|декоратор\w*|зависимост\w*|вызов\w*|"
    r"использ\w*|запрос\w*|эндпоинт\w*)\b"
)
_SOFTWARE_TERMS = re.compile(
    r"(?i)\b(?:fastapi|pydantic|django|flask|react|angular|vue|svelte|"
    r"next(?:js|\.js)?|numpy|pandas|sqlalchemy|pytest|requests|python|"
    r"typescript|javascript|node(?:js|\.js)?|spring|\.net|dotnet|"
    r"pytorch|tensorflow|rust|tokio|kotlin|golang)\b"
)
_VERSION_TERMS = re.compile(r"(?i)\b(?:version|верси\w*)\b")
_CURRENT_TERMS = re.compile(
    r"(?i)\b(?:current|currently|latest|today|now|newest|up-to-date|"
    r"сейчас|сегодня|текущ\w*|актуальн\w*|последн\w*|новейш\w*)\b"
)
_GENERAL_WEB_TERMS = re.compile(
    r"(?i)\b(?:news|release|released|releases|standard|standards|"
    r"новост\w*|выпуск\w*|релиз\w*|стандарт\w*)\b"
)
_INTERNAL_TERMS = re.compile(
    r"(?i)\b(?:internal|private|confidential|proprietary|"
    r"внутрен\w*|приватн\w*|конфиденциал\w*|служебн\w*)\b"
)
_GENERIC_CONTEXT_DEFINITION = re.compile(
    r"(?i)^\s*(?:what\s+(?:is|are)|define|explain|что\s+такое|"
    r"что\s+означает)\s+(?:an?\s+|the\s+)?"
    r"(?:api|sdk|documentation|library|framework|package|module|"
    r"апи|документац\w*|библиотек\w*|фреймворк\w*|пакет\w*|модул\w*)"
    r"\s*[?.!]*$"
)
_QUESTION_OR_HOW_TO_INTENT = re.compile(
    r"(?i)(?:\?(?=\s|$)|^\s*(?:how|what|which|when|where|why|who|can|could|"
    r"should|would|do|does|did|is|are|tell\s+me|explain|show\s+me|find|"
    r"как|какой|какая|какие|что|где|когда|почему|кто|можно|расскажи|"
    r"объясни|покажи|найди)\b)"
)

_SENSITIVE_QUERY_KEYS = {
    "accesskey",
    "accesskeyid",
    "apikey",
    "auth",
    "authcode",
    "authorization",
    "authorizationcode",
    "bearer",
    "clientsecret",
    "code",
    "credential",
    "jwt",
    "key",
    "nonce",
    "password",
    "passwd",
    "privatekey",
    "secret",
    "session",
    "sessionid",
    "sessionstate",
    "sig",
    "signature",
    "sid",
    "state",
    "ticket",
    "token",
    "xamzcredential",
}
_SENSITIVE_KEY_SUFFIXES = (
    "accesskey",
    "accesstoken",
    "apikey",
    "authcode",
    "clientsecret",
    "credential",
    "password",
    "privatekey",
    "sessionid",
    "signature",
    "token",
)

_NON_PERSON_VOCATIVES = {
    "angular",
    "django",
    "fastapi",
    "flask",
    "go",
    "golang",
    "javascript",
    "kotlin",
    "nextjs",
    "node",
    "nodejs",
    "numpy",
    "pandas",
    "pydantic",
    "python",
    "pytorch",
    "react",
    "rust",
    "spring",
    "sqlalchemy",
    "svelte",
    "tensorflow",
    "typescript",
    "vue",
}


class SearchPolicy:
    """Select at most one retrieval integration for a sanitized question."""

    def __init__(self, mode: SearchMode | str) -> None:
        if mode not in {"off", "auto", "forced"}:
            raise ValueError(f"Unsupported search mode: {mode!r}")
        self._mode: SearchMode = mode  # type: ignore[assignment]

    def integrations_for(
        self,
        question: str,
        full_transcript: str | None = None,
    ) -> list[SearchIntegration]:
        # Accepted for compatibility and deliberately never inspected or copied.
        del full_transcript

        if self._mode == "off":
            return []

        query = _sanitize_question(question)
        if not query:
            return []

        if self._mode == "forced":
            return [_duckduckgo(query)]
        if _INTERNAL_TERMS.search(query):
            return []
        if _is_context7_question(query):
            return [_context7(query)]
        if _requires_current_information(query):
            return [_duckduckgo(query)]
        return []


def _context7(query: str) -> SearchIntegration:
    return SearchIntegration(CONTEXT7_ID, query, CONTEXT7_TOOLS)


def _duckduckgo(query: str) -> SearchIntegration:
    return SearchIntegration(DUCKDUCKGO_ID, query, DUCKDUCKGO_TOOLS)


def _is_context7_question(query: str) -> bool:
    has_context = _CONTEXT_TERMS.search(query) is not None
    has_docs = _DOCUMENTATION_TERMS.search(query) is not None
    has_operation = _TECHNICAL_OPERATION_TERMS.search(query) is not None
    has_software = _SOFTWARE_TERMS.search(query) is not None
    has_current = _CURRENT_TERMS.search(query) is not None
    has_version = _VERSION_TERMS.search(query) is not None
    has_question_intent = _QUESTION_OR_HOW_TO_INTENT.search(query) is not None

    if _GENERIC_CONTEXT_DEFINITION.fullmatch(query):
        return False
    if has_docs:
        return True
    if has_context and (has_operation or has_current or has_version):
        return has_current or has_version or has_question_intent
    if has_software and has_version:
        return True
    return has_software and has_operation and (
        has_current or has_question_intent
    )


def _requires_current_information(query: str) -> bool:
    return any(
        pattern.search(query) is not None
        for pattern in (_CURRENT_TERMS, _VERSION_TERMS, _GENERAL_WEB_TERMS)
    )


def _sanitize_question(question: str) -> str:
    text = unicodedata.normalize("NFKC", question)
    text = _SOURCE_LINE.sub(" ", text)
    text = _TIMESTAMP_PREFIX.sub("", text)
    text = _CANDIDATE_NAME_LINE.sub(" ", text)
    text = _SPEAKER_PREFIX.sub("", text)
    text = _NAME_INTRODUCTION.sub(" ", text)
    text = _FIRST_PERSON_NAME.sub(" ", text)
    text = _NAME_METADATA.sub(" ", text)
    text = _LEADING_VOCATIVE.sub(_redact_vocative, text)
    text = _URL.sub(_sanitize_url_match, text)
    text = _LABELED_EMAIL.sub(" ", text)
    text = _EMAIL.sub(" ", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,.;!?])", r"\1", text)
    text = re.sub(r"^[\s,.;:!?—-]+", "", text)
    return text.strip()


def _sanitize_url_match(match: re.Match[str]) -> str:
    raw_url = match.group(0)
    suffix = ""
    while raw_url and raw_url[-1] in ".,;!)]}":
        suffix = raw_url[-1] + suffix
        raw_url = raw_url[:-1]

    try:
        split = urlsplit(raw_url)
        query_pairs = parse_qsl(split.query, keep_blank_values=True)
    except ValueError:
        return suffix

    decoded_path = _fully_unquote(split.path)
    if _EMAIL.search(decoded_path) or _path_contains_sensitive_data(
        decoded_path
    ):
        return suffix

    has_user_info = "@" in split.netloc
    safe_pairs = [
        (key, value)
        for key, value in query_pairs
        if _query_pair_is_safe(key, value)
    ]
    removed_sensitive_data = (
        has_user_info
        or bool(split.fragment)
        or len(safe_pairs) != len(query_pairs)
    )
    if not removed_sensitive_data:
        return raw_url + suffix

    netloc = split.netloc.rsplit("@", 1)[-1]
    sanitized = urlunsplit(
        (
            split.scheme,
            netloc,
            split.path,
            urlencode(safe_pairs, doseq=True),
            "",
        )
    )
    return sanitized + suffix


def _contains_sensitive_key(value: str) -> bool:
    return any(key in value.casefold() for key in _SENSITIVE_QUERY_KEYS)


def _is_sensitive_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
    return normalized in _SENSITIVE_QUERY_KEYS or normalized.endswith(
        _SENSITIVE_KEY_SUFFIXES
    )


def _redact_vocative(match: re.Match[str]) -> str:
    candidate = re.sub(r"[^a-zа-яё0-9]", "", match.group("name").casefold())
    if candidate in _NON_PERSON_VOCATIVES:
        return match.group(0)
    return ""


def _fully_unquote(value: str) -> str:
    decoded = value
    for _ in range(2):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    return decoded


def _path_contains_sensitive_data(path: str) -> bool:
    for segment in path.split("/"):
        if not segment:
            continue
        if _is_sensitive_key(segment) or _looks_like_secret(segment):
            return True
    return False


def _query_pair_is_safe(key: str, value: str) -> bool:
    decoded_value = _fully_unquote(value)
    return not (
        _is_sensitive_key(key)
        or _EMAIL.search(decoded_value)
        or _contains_sensitive_key(decoded_value)
        or _looks_like_secret(decoded_value)
    )


def _looks_like_secret(value: str) -> bool:
    candidate = value.strip()
    if re.fullmatch(r"[a-f0-9]{10,}", candidate, flags=re.IGNORECASE):
        return True
    if re.fullmatch(r"eyJ[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+){1,2}", candidate):
        return True
    return (
        len(candidate) >= 16
        and not re.search(r"\s", candidate)
        and re.search(r"[A-Za-z]", candidate) is not None
        and re.search(r"\d", candidate) is not None
    )
