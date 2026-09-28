from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from slidenote.content_guard import CONTENT_REPAIR_PROMPT_VERSION, missing_required_items
from slidenote.coverage import analyze_coverage
from slidenote.llm_cache import LLMCache
from slidenote.models import Deck

from .contexts import NoteContext
from .postprocess import _postprocess_llm_markdown
from .llm_calls import _generate_cached_llm_text
from .prompt_templates import _llm_repair_prompt


_IMAGE_LINK = re.compile(r"!\[[^\]]*]\(([^)]+)\)")
# Repair adds missing explanations; substantial compression is unsafe here.
MIN_BODY_RETENTION = 0.8
# A repair must re-emit the whole input, so skip inputs whose estimated output
# would not fit the output-token budget (CJK text is roughly 0.5-1 token/char).
REPAIR_ESTIMATED_TOKENS_PER_CHAR = 0.7
REPAIR_OUTPUT_BUDGET_HEADROOM = 0.85
_INCOMPLETE_FINISH_REASONS = {
    "length", "max_tokens", "content_filter", "safety", "recitation",
    "blocklist", "prohibited_content", "spii", "malformed_function_call",
    "tool_calls", "function_call", "tool_use", "pause_turn", "refusal",
}


def _covered_ids(coverage: dict[str, Any], field: str) -> set[str]:
    return {str(item["id"]) for item in coverage["items"] if item[field]}


def _image_targets(markdown: str) -> set[str]:
    targets: set[str] = set()
    for destination in _IMAGE_LINK.findall(markdown):
        match = re.match(r"\s*(?:<([^>]+)>|(\S+))", destination)
        if match:
            targets.add(match.group(1) or match.group(2))
    return targets


def _body_chars(markdown: str) -> int:
    """Count prose without source comments, image URLs or heading boilerplate."""
    text = re.sub(r"<!--.*?-->", "", markdown, flags=re.DOTALL)
    text = _IMAGE_LINK.sub("", text)
    text = re.sub(r"(?m)^[ \t]{0,3}#{1,6}[ \t]+.*$", "", text)
    text = re.sub(r"【[^】]*?PPT[^】]*?】", "", text)
    return sum(char.isalnum() for char in text)


def _repair_required_markdown_once(
    deck: Deck,
    context: NoteContext,
    markdown: str,
    output_root: Path,
    cache: LLMCache,
    options: "NoteOptions",
    *,
    stage: str,
    force_refresh: bool = False,
) -> tuple[str, dict[str, Any] | None]:
    source_display = options.source_display
    note_language = options.note_language
    term_policy = options.term_policy
    content_guard = options.content_guard
    if not content_guard:
        return markdown, None
    before_coverage = analyze_coverage(deck, markdown, content_guard=content_guard)
    missing_before = missing_required_items(content_guard, before_coverage)
    if not missing_before:
        return markdown, None

    record = {
        "stage": stage,
        "context_id": context.id,
        "slide_ids": [page.slide_id for page in context.pages],
        "missing_before": missing_before,
        "accepted": False,
        "rejection_reasons": [],
        "resolved_items": [],
        "unresolved_items": missing_before,
        "llm": None,
    }
    if not _fits_output_budget(markdown, options.max_output_tokens):
        # Sending an over-long draft would truncate the rewrite; keep the draft.
        record["rejection_reasons"] = ["input_too_long_for_output_budget"]
        record["input_chars"] = len(markdown)
        return markdown, record
    prompt = _llm_repair_prompt(
        markdown=markdown,
        missing_items=missing_before,
        source_display=source_display,
        stage=stage,
        note_language=note_language,
        term_policy=term_policy,
    )
    try:
        repaired, llm_record = _generate_cached_llm_text(
            context=NoteContext(
                id=f"repair_{stage}_{context.id}",
                kind=f"repair_{context.kind}",
                title=context.title,
                pages=context.pages,
            ),
            output_root=output_root,
            cache=cache,
            cache_mode=options.cache_mode,
            provider=options.provider,
            model=options.model,
            api_key=options.api_key,
            base_url=options.base_url,
            max_output_tokens=options.max_output_tokens,
            temperature=options.temperature,
            user_prompt=prompt,
            prompt_version=CONTENT_REPAIR_PROMPT_VERSION,
            generation_stage=f"content_repair_{stage}",
            request_options={
                "source_display": source_display,
                "note_language": note_language,
                "term_policy": term_policy,
                "missing_item_ids": [str(item.get("element_id")) for item in missing_before],
            },
            force_refresh=force_refresh,
        )
    except Exception as exc:
        # This optional repair must not discard an already generated draft.
        # Do not persist provider error text, which can contain credentials.
        record["rejection_reasons"] = ["generation_error"]
        record["error_type"] = type(exc).__name__
        return markdown, record

    repaired = _postprocess_llm_markdown(repaired, source_display=source_display)
    after_coverage = analyze_coverage(deck, repaired, content_guard=content_guard)
    unresolved = missing_required_items(content_guard, after_coverage)
    before_ids = {str(item.get("element_id")) for item in missing_before}
    unresolved_ids = {str(item.get("element_id")) for item in unresolved}
    lost_trace = _covered_ids(before_coverage, "trace_covered") - _covered_ids(after_coverage, "trace_covered")
    lost_visible = _covered_ids(before_coverage, "visible_covered") - _covered_ids(after_coverage, "visible_covered")
    lost_images = _image_targets(markdown) - _image_targets(repaired)
    original_chars = _body_chars(markdown)
    candidate_chars = _body_chars(repaired)
    reasons: list[str] = []
    if not candidate_chars:
        reasons.append("empty_repair")
    if lost_trace or lost_visible:
        reasons.append("coverage_regression")
    if lost_images:
        reasons.append("missing_images")
    if candidate_chars < original_chars * MIN_BODY_RETENTION:
        reasons.append("body_truncated")
    if not before_ids - unresolved_ids:
        reasons.append("no_coverage_improvement")
    response_usage = llm_record.get("provider_usage") or llm_record.get("cached_entry_usage") or {}
    finish_reason = str(response_usage.get("finish_reason") or "").lower()
    if finish_reason in _INCOMPLETE_FINISH_REASONS:
        reasons.append("incomplete_generation")

    record["llm"] = llm_record
    record["candidate_unresolved_items"] = unresolved
    record["rejection_reasons"] = reasons
    record["validation"] = {
        "lost_trace_items": sorted(lost_trace),
        "lost_visible_items": sorted(lost_visible),
        "lost_image_targets": sorted(lost_images),
        "original_body_chars": original_chars,
        "candidate_body_chars": candidate_chars,
        "finish_reason": finish_reason or None,
    }
    if reasons:
        return markdown, record

    record["accepted"] = True
    record["resolved_items"] = sorted(before_ids - unresolved_ids)
    record["unresolved_items"] = unresolved
    return repaired, record


def _fits_output_budget(markdown: str, max_output_tokens: int | None) -> bool:
    if not max_output_tokens:
        return True
    estimated_tokens = len(markdown) * REPAIR_ESTIMATED_TOKENS_PER_CHAR
    return estimated_tokens <= max_output_tokens * REPAIR_OUTPUT_BUDGET_HEADROOM
