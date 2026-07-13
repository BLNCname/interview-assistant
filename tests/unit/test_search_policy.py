from dataclasses import FrozenInstanceError

import pytest

from interview_assistant.retrieval.models import SearchIntegration
from interview_assistant.retrieval.policy import SearchPolicy


def test_library_question_uses_context7_only() -> None:
    question = "Как сейчас настраивается lifespan в FastAPI?"

    integrations = SearchPolicy("auto").integrations_for(
        question,
        full_transcript="private interview transcript",
    )

    assert len(integrations) == 1
    assert integrations[0] == SearchIntegration(
        id="mcp/context7",
        query=question,
        allowed_tools=("resolve-library-id", "query-docs"),
    )


def test_context7_takes_precedence_for_current_api_documentation() -> None:
    integrations = SearchPolicy("auto").integrations_for(
        "What is the latest FastAPI API syntax?"
    )

    assert [item.id for item in integrations] == ["mcp/context7"]


def test_specific_api_question_uses_context7_without_a_temporal_marker() -> None:
    integrations = SearchPolicy("auto").integrations_for(
        "How do I call the Stripe API?"
    )

    assert [item.id for item in integrations] == ["mcp/context7"]


def test_current_library_version_question_uses_context7() -> None:
    integrations = SearchPolicy("auto").integrations_for(
        "What is the current FastAPI version?"
    )

    assert [item.id for item in integrations] == ["mcp/context7"]


@pytest.mark.parametrize(
    "question",
    [
        "What is the current ISO 27001 standard?",
        "What is the latest Python release?",
        "Какие сегодня новости о выпуске Windows?",
    ],
)
def test_general_current_question_uses_duckduckgo(question: str) -> None:
    integrations = SearchPolicy("auto").integrations_for(question)

    assert integrations == [
        SearchIntegration(
            id="mcp/duckduckgo",
            query=question,
            allowed_tools=("search",),
        )
    ]


def test_static_question_exposes_no_tools() -> None:
    assert SearchPolicy("auto").integrations_for(
        "Explain binary search complexity."
    ) == []


def test_generic_static_api_definition_exposes_no_tools() -> None:
    assert SearchPolicy("auto").integrations_for("What is an API?") == []


@pytest.mark.parametrize(
    "question",
    [
        "Our internal API is slow.",
        "Наша внутренняя библиотека работает медленно.",
    ],
)
def test_internal_static_technical_text_stays_local(question: str) -> None:
    assert SearchPolicy("auto").integrations_for(question) == []


@pytest.mark.parametrize(
    "question",
    [
        "Python use is slow.",
        "API request failed.",
        "API request failed. https://example.test/status?lang=en",
    ],
)
def test_declarative_operation_words_do_not_trigger_context7(question: str) -> None:
    assert SearchPolicy("auto").integrations_for(question) == []


def test_off_mode_exposes_no_tools() -> None:
    assert SearchPolicy("off").integrations_for(
        "latest Python release",
        "secret transcript",
    ) == []


def test_forced_mode_is_a_per_request_duckduckgo_override() -> None:
    integrations = SearchPolicy("forced").integrations_for(
        "Explain FastAPI lifespan."
    )

    assert [item.id for item in integrations] == ["mcp/duckduckgo"]
    assert integrations[0].allowed_tools == ("search",)


def test_english_privacy_data_and_transcript_metadata_are_removed() -> None:
    question = (
        "Speaker 1 [00:01:02]: My name is Alice Smith. "
        "Email: alice.smith@example.com. What is the latest Python release?"
    )

    first = SearchPolicy("auto").integrations_for(
        question,
        full_transcript="Interviewer: internal-secret@example.test",
    )
    second = SearchPolicy("auto").integrations_for(
        question,
        full_transcript="A different private transcript",
    )

    assert first[0].query == "What is the latest Python release?"
    assert second[0].query == first[0].query
    assert "Alice" not in first[0].query
    assert "alice.smith" not in first[0].query
    assert "internal-secret" not in first[0].query


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        (
            "My name is Alice Smith, what is the latest Python release?",
            "what is the latest Python release?",
        ),
        (
            "Меня зовут Иван Петров, какая сейчас версия библиотеки Pydantic?",
            "какая сейчас версия библиотеки Pydantic?",
        ),
    ],
)
def test_name_introduction_redaction_retains_comma_separated_question(
    question: str,
    expected: str,
) -> None:
    integrations = SearchPolicy("auto").integrations_for(question)

    assert integrations[0].query == expected


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        (
            "Alice Smith, what is the latest Python release?",
            "what is the latest Python release?",
        ),
        (
            "Alice Smith, What is the latest Python release?",
            "What is the latest Python release?",
        ),
        (
            "Иван Петров, какая сейчас версия библиотеки Pydantic?",
            "какая сейчас версия библиотеки Pydantic?",
        ),
        (
            "Alice, tell me the latest Python release.",
            "tell me the latest Python release.",
        ),
        (
            "Alice Smith, explain the current FastAPI API.",
            "explain the current FastAPI API.",
        ),
        (
            "Иван, расскажи последнюю версию Pydantic.",
            "расскажи последнюю версию Pydantic.",
        ),
    ],
)
def test_leading_vocative_name_is_removed_without_losing_the_question(
    question: str,
    expected: str,
) -> None:
    integrations = SearchPolicy("auto").integrations_for(question)

    assert integrations[0].query == expected


def test_leading_software_name_is_not_mistaken_for_a_person() -> None:
    question = "FastAPI, what is the current lifespan syntax?"

    integrations = SearchPolicy("auto").integrations_for(question)

    assert integrations[0].query == question
    assert integrations[0].id == "mcp/context7"


def test_russian_privacy_data_and_transcript_metadata_are_removed() -> None:
    question = (
        "[00:03:04] Кандидат: Меня зовут Иван Петров. "
        "Почта: ivan.petrov@example.ru. "
        "Какая сейчас версия библиотеки Pydantic?"
    )

    integrations = SearchPolicy("auto").integrations_for(
        question,
        full_transcript="Источник: закрытая стенограмма",
    )

    assert integrations[0].query == "Какая сейчас версия библиотеки Pydantic?"
    assert "Иван" not in integrations[0].query
    assert "закрытая" not in integrations[0].query


def test_standalone_timestamp_and_candidate_name_metadata_are_removed() -> None:
    question = (
        "[00:04:05]\n"
        "Candidate: Alice Smith\n"
        "Interviewer: What is the latest Python release?"
    )

    integrations = SearchPolicy("auto").integrations_for(question)

    assert integrations[0].query == "What is the latest Python release?"
    assert "00:04:05" not in integrations[0].query
    assert "Alice Smith" not in integrations[0].query


def test_source_metadata_and_sensitive_url_parameters_are_removed() -> None:
    question = (
        "Source: Teams recording\n"
        "Interviewer [00:03]: Latest OAuth standard? "
        "https://example.test/spec?token=top-secret&lang=ru&X-Amz-Signature=signed"
    )

    integrations = SearchPolicy("auto").integrations_for(question)

    assert integrations[0].query == (
        "Latest OAuth standard? https://example.test/spec?lang=ru"
    )
    assert "Teams" not in integrations[0].query
    assert "top-secret" not in integrations[0].query
    assert "signed" not in integrations[0].query


def test_url_fragment_credentials_are_removed_even_without_sensitive_query() -> None:
    question = (
        "Latest OAuth standard? "
        "https://example.test/spec?lang=en#access_token=fragment-secret"
    )

    integrations = SearchPolicy("auto").integrations_for(question)

    assert integrations[0].query == (
        "Latest OAuth standard? https://example.test/spec?lang=en"
    )
    assert "fragment-secret" not in integrations[0].query


def test_unparseable_url_is_dropped_instead_of_returned_with_encoded_secret() -> None:
    question = (
        "Latest OAuth standard? "
        "https://[broken.example/spec?%74oken=private-value"
    )

    integrations = SearchPolicy("auto").integrations_for(question)

    assert integrations[0].query == "Latest OAuth standard?"
    assert "private-value" not in integrations[0].query


def test_url_userinfo_credentials_are_removed() -> None:
    question = (
        "Latest OAuth standard? "
        "https://user:password@example.test/spec?lang=en"
    )

    integrations = SearchPolicy("auto").integrations_for(question)

    assert integrations[0].query == (
        "Latest OAuth standard? https://example.test/spec?lang=en"
    )
    assert "password" not in integrations[0].query


@pytest.mark.parametrize(
    ("url", "expected", "forbidden"),
    [
        (
            "https://example.test/reset/token/super-secret",
            "Latest OAuth standard?",
            "super-secret",
        ),
        (
            "https://example.test/callback?code=a8f3d90b71",
            "Latest OAuth standard? https://example.test/callback",
            "a8f3d90b71",
        ),
        (
            "https://example.test/callback?sessionid=a8f3d90b71",
            "Latest OAuth standard? https://example.test/callback",
            "a8f3d90b71",
        ),
        (
            "https://example.test/users/alice%40example.com",
            "Latest OAuth standard?",
            "alice%40example.com",
        ),
    ],
)
def test_url_credential_paths_codes_sessions_and_encoded_email_are_redacted(
    url: str,
    expected: str,
    forbidden: str,
) -> None:
    integrations = SearchPolicy("auto").integrations_for(
        f"Latest OAuth standard? {url}"
    )

    assert integrations[0].query == expected
    assert forbidden not in integrations[0].query


@pytest.mark.parametrize("mode", ["auto", "forced"])
@pytest.mark.parametrize("key", ["state", "nonce", "session_state"])
def test_oauth_correlation_values_are_removed_in_auto_and_forced_modes(
    mode: str,
    key: str,
) -> None:
    secret = "opaquevalue"
    question = (
        "Latest OAuth standard? "
        f"https://example.test/callback?{key}={secret}"
    )

    integrations = SearchPolicy(mode).integrations_for(question)

    assert integrations[0].query == (
        "Latest OAuth standard? https://example.test/callback"
    )
    assert secret not in integrations[0].query


def test_empty_sanitized_query_never_enables_an_all_tools_fallback() -> None:
    assert SearchPolicy("forced").integrations_for(
        "My name is Alice Smith. Email: alice@example.com."
    ) == []


def test_integration_is_frozen_slotted_and_serializes_only_native_fields() -> None:
    integration = SearchIntegration(
        id="mcp/duckduckgo",
        query="latest release secret-free",
        allowed_tools=("search",),
    )

    assert not hasattr(integration, "__dict__")
    with pytest.raises(FrozenInstanceError):
        integration.id = "mcp/other"
    assert integration.to_lmstudio() == {
        "type": "plugin",
        "id": "mcp/duckduckgo",
        "allowed_tools": ["search"],
    }
    assert "query" not in integration.to_lmstudio()


def test_invalid_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported search mode"):
        SearchPolicy("always")
