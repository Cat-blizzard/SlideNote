from __future__ import annotations

from slidenote.llm import LLMClient

from .direct import _generate_notes_with_llm
from .lecture_weave import _generate_notes_with_lecture_weave
from .llm_calls import (
    _base_usage_context_record,
    _generate_cached_llm_text,
    _generate_llm_context,
    _generate_page_lecture_context,
    _generate_weave_context,
)
from .repair import _repair_required_markdown_once
from .usage import _build_usage_report, _render_generation_info
from .versions import NOTE_PROMPT_VERSION, PAGE_LECTURE_PROMPT_VERSION, WEAVE_PROMPT_VERSION

__all__ = [
    "LLMClient",
    "NOTE_PROMPT_VERSION",
    "PAGE_LECTURE_PROMPT_VERSION",
    "WEAVE_PROMPT_VERSION",
    "_base_usage_context_record",
    "_build_usage_report",
    "_generate_cached_llm_text",
    "_generate_llm_context",
    "_generate_notes_with_lecture_weave",
    "_generate_notes_with_llm",
    "_generate_page_lecture_context",
    "_generate_weave_context",
    "_render_generation_info",
    "_repair_required_markdown_once",
]
