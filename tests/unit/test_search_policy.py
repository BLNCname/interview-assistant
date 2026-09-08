from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from threading import Barrier

import pytest

from interview_assistant.retrieval import models as retrieval_models
from interview_assistant.retrieval.models import SearchIntegration
from interview_assistant.retrieval.policy import SearchPolicy


def test_force_next_is_one_shot_even_when_base_mode_is_auto() -> None:
    policy = SearchPolicy("auto")
    static_question = "Explain a binary search tree"

    assert policy.integrations_for(static_question) == []

    policy.force_next()

    forced = policy.integrations_for(static_question)
    assert [integration.id for integration in forced] == ["mcp/firecrawl"]
    assert policy.integrations_for(static_question) == []


@pytest.mark.parametrize("mode", ["auto", "forced"])
def test_web_query_is_limited_after_sanitizing_personal_metadata(mode: str) -> None:
    safe_question = "What is the latest Python release? " + "Release compatibility details. " * 30
    raw_question = "My name is Alice Smith. Email: alice@example.com. " + safe_question
    integration = SearchPolicy(mode).integrations_for(raw_question, "private full transcript")[0]

    assert integration.id == "mcp/firecrawl"
    assert integration.query == safe_question[:500].rstrip()
    assert len(integration.query) <= 500
    assert "Alice" not in integration.query
    assert "alice@example.com" not in integration.query
    assert "private full transcript" not in integration.query


def test_auto_privacy_classification_checks_text_beyond_web_query_limit() -> None:
    question = "What is the latest Python release? " + "details " * 100 + " internal system"
    assert SearchPolicy("auto").integrations_for(question) == []


def test_context7_documentation_query_is_not_cut_to_web_search_limit() -> None:
    question = "How do I configure FastAPI lifespan? " + "Implementation constraints. " * 30
    integration = SearchPolicy("auto").integrations_for(question)[0]
    assert integration.id == "mcp/context7"
    assert integration.query == question.strip()


def test_firecrawl_capability_rejects_query_beyond_server_limit() -> None:
    with pytest.raises(ValueError, match="query.*limit"):
        retrieval_models._policy_search_integration(
            "mcp/firecrawl", "x" * 501, ("firecrawl_search",),
        )


def test_force_next_is_retained_when_sanitization_removes_an_unusable_utterance() -> None:
    policy = SearchPolicy("off")
    policy.force_next()

    assert policy.integrations_for("My name is Alice Smith.") == []

    forced = policy.integrations_for("Explain a binary search tree")
    assert [integration.id for integration in forced] == ["mcp/firecrawl"]
    assert policy.integrations_for("Explain a binary search tree") == []


def test_library_question_uses_context7_only() -> None:
    question = "Как сейчас настраивается lifespan в FastAPI?"

    integrations = SearchPolicy("auto").integrations_for(
        question,
        full_transcript="private interview transcript",
    )

    assert len(integrations) == 1
    assert integrations[0].id == "mcp/context7"
    assert integrations[0].query == question
    assert integrations[0].allowed_tools == (
        "resolve-library-id",
        "query-docs",
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


@pytest.mark.parametrize("question", [
    "What is the current FastAPI version?",
    "What is the latest Python version?",
    "Какая последняя версия Python?",
    "Какая версия Python сейчас актуальна?",
    "Какая последняя версия библиотеки FastAPI?",
])
def test_current_software_version_lookup_uses_web_search(question: str) -> None:
    integrations = SearchPolicy("auto").integrations_for(question)

    assert [item.id for item in integrations] == ["mcp/firecrawl"]
    assert integrations[0].allowed_tools == ("firecrawl_search",)


@pytest.mark.parametrize("question", [
    "How do I configure lifespan in the latest FastAPI version?",
    "Как использовать функцию match в версии Python 3.10?",
    "Покажи документацию последней версии Python.",
])
def test_version_specific_api_and_documentation_still_use_context7(question: str) -> None:
    integrations = SearchPolicy("auto").integrations_for(question)

    assert [item.id for item in integrations] == ["mcp/context7"]


@pytest.mark.parametrize(
    "question",
    [
        "What is the current ISO 27001 standard?",
        "What is the latest Python release?",
        "Какие сегодня новости о выпуске Windows?",
    ],
)
def test_general_current_question_uses_firecrawl(question: str) -> None:
    integrations = SearchPolicy("auto").integrations_for(question)

    assert len(integrations) == 1
    assert integrations[0].id == "mcp/firecrawl"
    assert integrations[0].query == question
    assert integrations[0].allowed_tools == ("firecrawl_search",)


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
        "API request failed. https://example.test/status?",
        "Python use is slow. https://example.test/?",
    ],
)
def test_declarative_operation_words_do_not_trigger_context7(question: str) -> None:
    assert SearchPolicy("auto").integrations_for(question) == []


@pytest.mark.parametrize(
    "question",
    [
        "Today Alice Smith joined Acme Corp.",
        "Python documentation contains Alice Smith's resume details.",
        "API version is v2.",
        "Python documentation is difficult to read.",
        "The latest release broke our build.",
    ],
)
def test_auto_does_not_route_declarative_current_or_documentation_text(
    question: str,
) -> None:
    assert SearchPolicy("auto").integrations_for(question) == []


@pytest.mark.parametrize(
    ("question", "expected_id"),
    [
        ("Tell me the latest Python release.", "mcp/firecrawl"),
        ("Find the current FastAPI documentation.", "mcp/context7"),
        ("Расскажи последние новости Python.", "mcp/firecrawl"),
        ("Покажи актуальную документацию FastAPI.", "mcp/context7"),
    ],
)
def test_auto_routes_english_and_russian_imperative_questions(
    question: str,
    expected_id: str,
) -> None:
    integrations = SearchPolicy("auto").integrations_for(question)

    assert [item.id for item in integrations] == [expected_id]


def test_off_mode_exposes_no_tools() -> None:
    assert SearchPolicy("off").integrations_for(
        "latest Python release",
        "secret transcript",
    ) == []


def test_forced_mode_is_a_per_request_firecrawl_override() -> None:
    policy = SearchPolicy("forced")

    integrations = policy.integrations_for(
        "Explain FastAPI lifespan."
    )
    second = policy.integrations_for("What is the latest Python release?")

    assert [item.id for item in integrations] == ["mcp/firecrawl"]
    assert integrations[0].allowed_tools == ("firecrawl_search",)
    assert second == []


def test_forced_override_is_consumed_by_only_one_concurrent_request() -> None:
    workers = 8
    barrier = Barrier(workers)
    policy = SearchPolicy("forced")

    def route(index: int) -> list[SearchIntegration]:
        barrier.wait(timeout=2)
        return policy.integrations_for(f"request {index}")

    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(route, range(workers)))

    assert sum(bool(result) for result in results) == 1
    assert {
        integration.id
        for result in results
        for integration in result
    } == {"mcp/firecrawl"}


def test_auto_and_off_modes_remain_stable_across_repeated_calls() -> None:
    auto = SearchPolicy("auto")
    off = SearchPolicy("off")

    first_auto = auto.integrations_for("What is the latest Python release?")
    second_auto = auto.integrations_for("What is the latest Python release?")

    assert [item.id for item in first_auto] == ["mcp/firecrawl"]
    assert second_auto == first_auto
    assert off.integrations_for("What is the latest Python release?") == []
    assert off.integrations_for("What is the latest Python release?") == []


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


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        (
            "Candidate: Alice Smith. What is the latest Python release?",
            "What is the latest Python release?",
        ),
        (
            "Кандидат: Иван Петров. Какая сейчас версия библиотеки Pydantic?",
            "Какая сейчас версия библиотеки Pydantic?",
        ),
    ],
)
def test_inline_candidate_name_metadata_is_removed(
    question: str,
    expected: str,
) -> None:
    integrations = SearchPolicy("auto").integrations_for(question)

    assert integrations[0].query == expected


@pytest.mark.parametrize(
    "question",
    [
        (
            "Resume: private-resume-marker. Current question: "
            "What is the latest Python release?"
        ),
        (
            "System prompt: private-system-marker. Question: "
            "What is the latest Python release?"
        ),
        (
            "Previous answer: private-answer-marker. Question: "
            "What is the latest Python release?"
        ),
        (
            "Transcript: private-transcript-marker. Question: "
            "What is the latest Python release?"
        ),
        (
            "Job metadata: private-job-marker. Question: "
            "What is the latest Python release?"
        ),
        (
            "Резюме: приватные-данные. Текущий вопрос: "
            "Какая сейчас версия библиотеки Pydantic?"
        ),
    ],
)
def test_labeled_context_contamination_keeps_only_current_question(
    question: str,
) -> None:
    integrations = SearchPolicy("auto").integrations_for(question)

    expected = (
        "Какая сейчас версия библиотеки Pydantic?"
        if "Pydantic" in question
        else "What is the latest Python release?"
    )
    assert integrations[0].query == expected


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        (
            "Previous answer: Alice Smith works at Acme. "
            "What is the latest Python release?",
            "What is the latest Python release?",
        ),
        (
            "System prompt: candidate Alice Smith. Latest OAuth standard?",
            "Latest OAuth standard?",
        ),
        (
            "Speaker 1 [00:01]: Previous answer: Alice Smith works at Acme. "
            "What is the latest Python release?",
            "What is the latest Python release?",
        ),
        (
            "[00:01] System prompt: secret. Latest OAuth standard?",
            "Latest OAuth standard?",
        ),
        (
            "Speaker 1: [00:01] Previous answer: Alice Smith works at Acme. "
            "What is the latest Python release?",
            "What is the latest Python release?",
        ),
        (
            "Speaker 1: Speaker 2: [00:01] Previous answer: "
            "Alice Smith works at Acme. What is the latest Python release?",
            "What is the latest Python release?",
        ),
        (
            "Previous answer: private. Speaker 1: [00:01] "
            "What is the latest Python release?",
            "What is the latest Python release?",
        ),
        (
            "Speaker 1: [00:01] Source: Teams recording\n"
            "Interviewer: Previous answer: Alice Smith works at Acme. "
            "What is the latest Python release?",
            "What is the latest Python release?",
        ),
        (
            "Спикер 1: [00:01] Предыдущий ответ: "
            "Иван Петров работает в Acme. Какая сейчас версия Python?",
            "Какая сейчас версия Python?",
        ),
    ],
)
def test_reviewer_labeled_metadata_without_question_label_is_removed(
    question: str,
    expected: str,
) -> None:
    integrations = SearchPolicy("auto").integrations_for(question)

    assert integrations[0].query == expected


def test_excessively_nested_question_prefixes_fail_closed() -> None:
    prefixes = "Speaker 1: " * 32

    integrations = SearchPolicy("auto").integrations_for(
        f"{prefixes}What is the latest Python release?"
    )

    assert integrations == []


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        (
            "What is the latest Python release for Alice Smith?",
            "What is the latest Python release?",
        ),
        (
            "What is the latest Python release for Alice?",
            "What is the latest Python release?",
        ),
        (
            "What is the latest Python release requested by Alice Smith?",
            "What is the latest Python release?",
        ),
        (
            "What is the latest Python release requested by Alice Mary Smith?",
            "What is the latest Python release?",
        ),
        (
            "What is the latest Python release requested by Alice Smith "
            "in the interview?",
            "What is the latest Python release in the interview?",
        ),
        (
            "What is the latest Python release by Alice Smith?",
            "What is the latest Python release?",
        ),
        (
            "What is the latest Python release from Alice Smith?",
            "What is the latest Python release?",
        ),
        (
            "Какая сейчас версия Python для Ивана Петрова?",
            "Какая сейчас версия Python?",
        ),
        (
            "Какая сейчас версия Python от Ивана Петрова?",
            "Какая сейчас версия Python?",
        ),
        (
            "Какая сейчас версия Python для иван@пример.рф?",
            "Какая сейчас версия Python?",
        ),
    ],
)
def test_reviewer_russian_inline_personal_data_is_removed(
    question: str,
    expected: str,
) -> None:
    integrations = SearchPolicy("auto").integrations_for(question)

    assert integrations[0].query == expected


@pytest.mark.parametrize(
    "question",
    [
        "What does the system prompt parameter do in the current FastAPI API?",
        "How do I parse transcript metadata in the current Python API?",
    ],
)
def test_legitimate_technical_metadata_questions_are_preserved(
    question: str,
) -> None:
    integrations = SearchPolicy("auto").integrations_for(question)

    assert integrations[0].query == question
    assert integrations[0].id == "mcp/context7"


@pytest.mark.parametrize(
    "question",
    [
        "What is the latest release for Python?",
        "What is the latest release for Visual Studio Code?",
    ],
)
def test_known_product_name_is_not_redacted_as_a_person(question: str) -> None:

    integrations = SearchPolicy("auto").integrations_for(question)

    assert integrations[0].query == question


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        (
            "What is the latest Python release for Alice B. Smith?",
            "What is the latest Python release?",
        ),
        (
            "What is the latest Python release requested by A. B. Smith?",
            "What is the latest Python release?",
        ),
        (
            "Какая сейчас версия Python для Ивана П. Петрова?",
            "Какая сейчас версия Python?",
        ),
        (
            "Какая сейчас версия Python от И. П. Петрова?",
            "Какая сейчас версия Python?",
        ),
    ],
)
def test_dotted_initials_in_person_attribution_are_removed(
    question: str,
    expected: str,
) -> None:
    integrations = SearchPolicy("auto").integrations_for(question)

    assert integrations[0].query == expected


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
            "https://example.test/callback?token_value=opaquevalue",
            "Latest OAuth standard? https://example.test/callback",
            "opaquevalue",
        ),
        (
            "https://example.test/callback?token-value=opaquevalue",
            "Latest OAuth standard? https://example.test/callback",
            "opaquevalue",
        ),
        (
            "https://example.test/callback?token.value=opaquevalue",
            "Latest OAuth standard? https://example.test/callback",
            "opaquevalue",
        ),
        (
            "https://example.test/callback?tokenValue=opaquevalue",
            "Latest OAuth standard? https://example.test/callback",
            "opaquevalue",
        ),
        (
            "https://example.test/users/alice%40example.com",
            "Latest OAuth standard?",
            "alice%40example.com",
        ),
        (
            "https://example.test/reset/token_abcd1234",
            "Latest OAuth standard?",
            "token_abcd1234",
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


@pytest.mark.parametrize(
    "key",
    [
        "%74oken",
        "%2574oken",
        "%74oken%5Fvalue",
        "%2574oken%255Fvalue",
        "%74oken%2Dvalue",
        "%2574oken%252Dvalue",
        "%74oken%2Evalue",
        "%2574oken%252Evalue",
        "%74okenValue",
        "%2574okenValue",
    ],
)
def test_encoded_sensitive_query_keys_are_fully_decoded_before_filtering(
    key: str,
) -> None:
    url = f"https://example.test/callback?{key}=opaquevalue&lang=en"

    integrations = SearchPolicy("auto").integrations_for(
        f"Latest OAuth standard? {url}"
    )

    assert integrations[0].query == (
        "Latest OAuth standard? https://example.test/callback?lang=en"
    )
    assert "opaquevalue" not in integrations[0].query


@pytest.mark.parametrize(
    "key",
    [
        "candidate",
        "full_name",
        "name",
        "person",
        "user",
        "%2563andidate",
        "full%255Fname",
    ],
)
def test_identity_query_keys_are_removed_after_full_decoding(key: str) -> None:
    url = f"https://example.test/search?{key}=Alice%20Smith&lang=en"

    integrations = SearchPolicy("auto").integrations_for(
        f"Latest OAuth standard? {url}"
    )

    assert integrations[0].query == (
        "Latest OAuth standard? https://example.test/search?lang=en"
    )
    assert "Alice" not in integrations[0].query


@pytest.mark.parametrize(
    "key",
    [
        "%256CibraryName",
        "%2566ilename",
        "%2574okenizer",
    ],
)
def test_benign_encoded_query_keys_are_preserved(key: str) -> None:
    url = f"https://example.test/search?{key}=FastAPI"

    integrations = SearchPolicy("auto").integrations_for(
        f"Latest OAuth standard? {url}"
    )

    assert integrations[0].query == f"Latest OAuth standard? {url}"


@pytest.mark.parametrize("key", ["libraryName", "filename"])
def test_benign_name_suffix_query_keys_are_preserved(key: str) -> None:
    value = "FastAPI" if key == "libraryName" else "guide.pdf"
    url = f"https://example.test/search?{key}={value}"

    integrations = SearchPolicy("auto").integrations_for(
        f"Latest OAuth standard? {url}"
    )

    assert integrations[0].query == f"Latest OAuth standard? {url}"


@pytest.mark.parametrize("key", ["monkey", "tokenizer"])
def test_benign_query_keys_that_contain_key_words_are_preserved(key: str) -> None:
    url = f"https://example.test/search?{key}=opaquevalue"

    integrations = SearchPolicy("auto").integrations_for(
        f"Latest OAuth standard? {url}"
    )

    assert integrations[0].query == f"Latest OAuth standard? {url}"


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
    integration = SearchPolicy("forced").integrations_for(
        "latest release secret-free"
    )[0]

    assert not hasattr(integration, "__dict__")
    with pytest.raises(FrozenInstanceError):
        integration.id = "mcp/other"
    assert integration.to_lmstudio() == {
        "type": "plugin",
        "id": "mcp/firecrawl",
        "allowed_tools": ["firecrawl_search"],
    }
    assert "query" not in integration.to_lmstudio()


def test_raw_search_integration_construction_is_rejected() -> None:
    with pytest.raises(TypeError, match="sanitized"):
        SearchIntegration(
            id="mcp/firecrawl",
            query="raw private transcript",
            allowed_tools=("firecrawl_search",),
        )


def test_unsealed_sanitized_question_is_rejected() -> None:
    unsealed = object.__new__(retrieval_models._SanitizedQuestion)
    object.__setattr__(unsealed, "_text", "raw private transcript")
    object.__setattr__(unsealed, "_proof", object())

    with pytest.raises(TypeError, match="sanitized"):
        SearchIntegration(
            id="mcp/firecrawl",
            query=unsealed,
            allowed_tools=("firecrawl_search",),
        )


def test_reused_proof_and_mac_cannot_seal_different_text() -> None:
    valid = SearchPolicy("forced").integrations_for("safe question")[0]
    original = valid._sanitized_question
    forged = retrieval_models._SanitizedQuestion._create(
        "raw private transcript",
        original._proof,
        original._mac,
    )

    with pytest.raises(TypeError, match="sanitized"):
        SearchIntegration(
            id="mcp/firecrawl",
            query=forged,
            allowed_tools=("firecrawl_search",),
        )


def test_sanitized_question_subclass_lookalike_is_rejected() -> None:
    valid = SearchPolicy("forced").integrations_for("safe question")[0]

    class SanitizedQuestionLookalike(retrieval_models._SanitizedQuestion):
        pass

    lookalike = object.__new__(SanitizedQuestionLookalike)
    object.__setattr__(lookalike, "_text", "raw private transcript")
    object.__setattr__(
        lookalike,
        "_proof",
        valid._sanitized_question._proof,
    )

    with pytest.raises(TypeError, match="sanitized"):
        SearchIntegration(
            id="mcp/firecrawl",
            query=lookalike,
            allowed_tools=("firecrawl_search",),
        )


def test_invalid_mode_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported search mode"):
        SearchPolicy("always")
