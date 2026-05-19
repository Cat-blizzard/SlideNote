from __future__ import annotations

from pathlib import Path
from typing import Any

from slidenote.content_guard import CONTENT_REPAIR_PROMPT_VERSION, missing_required_items
from slidenote.coverage import analyze_coverage
from slidenote.llm_cache import LLMCache
from slidenote.models import Deck

from .assembly import NoteContext, _postprocess_llm_markdown
from .llm_calls import _generate_cached_llm_text
from .prompt_templates import _llm_repair_prompt


def _repair_required_markdown_once(
    deck: Deck,
    context: NoteContext,
    markdown: str,
    output_root: Path,
    cache: LLMCache,
    cache_mode: str,
    provider: str,
    model: str,
    api_key: str | None,
    base_url: str | None,
    max_output_tokens: int,
    temperature: float | None,
    source_display: str,
    note_language: str,
    term_policy: str,
    content_guard: dict[str, Any] | None,
    stage: str,
) -> tuple[str, dict[str, Any] | None]:
    if not content_guard:
        return markdown, None
    before_coverage = analyze_coverage(deck, markdown, content_guard=content_guard)
    missing_before = missing_required_items(content_guard, before_coverage)
    if not missing_before:
        return markdown, None

    prompt = _llm_repair_prompt(
        markdown=markdown,
        missing_items=missing_before,
        source_display=source_display,
        stage=stage,
        note_language=note_language,
        term_policy=term_policy,
    )
    repaired, llm_record = _generate_cached_llm_text(
        context=NoteContext(
            id=f"repair_{stage}_{context.id}",
            kind=f"repair_{context.kind}",
            title=context.title,
            pages=context.pages,
        ),
        output_root=output_root,
        cache=cache,
        cache_mode=cache_mode,
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
        user_prompt=prompt,
        prompt_version=CONTENT_REPAIR_PROMPT_VERSION,
        generation_stage=f"content_repair_{stage}",
        request_options={
            "source_display": source_display,
            "note_language": note_language,
            "term_policy": term_policy,
            "missing_item_ids": [str(item.get("element_id")) for item in missing_before],
        },
        force_refresh=False,
    )
    repaired = _postprocess_llm_markdown(repaired, source_display=source_display)
    after_coverage = analyze_coverage(deck, repaired, content_guard=content_guard)
    unresolved = missing_required_items(content_guard, after_coverage)
    before_ids = {str(item.get("element_id")) for item in missing_before}
    unresolved_ids = {str(item.get("element_id")) for item in unresolved}
    record = {
        "stage": stage,
        "context_id": context.id,
        "slide_ids": [page.slide_id for page in context.pages],
        "missing_before": missing_before,
        "resolved_items": sorted(before_ids - unresolved_ids),
        "unresolved_items": unresolved,
        "llm": llm_record,
    }
    return repaired, record
