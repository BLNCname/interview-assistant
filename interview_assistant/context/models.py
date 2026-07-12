from dataclasses import dataclass
from typing import Literal, TypeAlias

from interview_assistant.audio.models import AudioSource
from interview_assistant.transcript.detector import QuestionKind

ContextItemKind: TypeAlias = Literal[
    "system",
    "task",
    "question",
    "transcript",
    "resume",
    "job",
    "stack",
    "screenshot",
    "search",
    "previous_answer",
    "clarification",
]


class ContextBudgetError(ValueError):
    """Raised when required context cannot fit without exceeding its budget."""


@dataclass(frozen=True, slots=True)
class ContextItem:
    kind: ContextItemKind
    label: str
    text: str
    source: AudioSource | None = None
    timestamp: float | None = None


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    question: str
    task_kind: QuestionKind
    items: tuple[ContextItem, ...]
    prompt: str
    estimated_tokens: int
    max_tokens: int

    @property
    def token_budget(self) -> int:
        return self.max_tokens


def estimate_tokens(text: str) -> int:
    """Conservatively estimate text tokens using the product's fixed heuristic."""

    return -(-len(text) // 3)
