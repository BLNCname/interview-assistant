from __future__ import annotations

import os
import sys
from pathlib import Path


_PROMPT_RELATIVE_PATH = Path("prompts") / "interview_system.md"


def candidate_system_prompt() -> str:
    """Load the tracked prompt from frozen data first, then from source."""

    for root in _prompt_roots():
        try:
            prompt = (root / _PROMPT_RELATIVE_PATH).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if prompt.strip():
            return prompt
    raise RuntimeError("Interview system prompt is unavailable")


def _prompt_roots() -> tuple[Path, ...]:
    roots: list[Path] = []
    frozen_value = getattr(sys, "_MEIPASS", None)
    if isinstance(frozen_value, (str, os.PathLike)):
        frozen_root = Path(frozen_value)
        if frozen_root.is_absolute():
            roots.append(frozen_root)
    source_root = Path(__file__).resolve().parents[2]
    if source_root not in roots:
        roots.append(source_root)
    return tuple(roots)
