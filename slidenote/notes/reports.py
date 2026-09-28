"""Build page-note, weave and teaching-enrichment reports."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from slidenote.models import Deck
from slidenote.utils import source_tokens, sum_int
from .versions import (
    PAGE_LECTURE_PROMPT_VERSION,
    TEACHING_ENRICHMENT_PROMPT_VERSION,
    WEAVE_PROMPT_VERSION,
)
from .contexts import NoteContext


def _build_page_notes_report(
    deck: Deck,
    provider: str,
    model: str,
    base_url: str | None,
    note_depth: str,
    note_language: str,
    term_policy: str,
    page_neighborhood: int,
    pages: list[NoteContext],
    page_markdown_by_slide: dict[int, str],
    page_records: list[dict[str, Any]],
    deck_brief: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from slidenote.llm_cache import utc_now_iso
    from .prompt_payload import _prompt_brief_hash, _prompt_deck_brief
    prompt_brief = _prompt_deck_brief(deck_brief)
    record_by_slide = {record.get("slide_id"): record for record in page_records}
    page_entries: list[dict[str, Any]] = []
    for context in pages:
        page = context.pages[0]
        record = record_by_slide.get(page.slide_id, {})
        markdown = page_markdown_by_slide.get(page.slide_id, "")
        page_entries.append(
            {
                "slide_id": page.slide_id,
                "title": page.title,
                "markdown": markdown,
                "source_ids": sorted(source_tokens(markdown)),
                "cache_status": record.get("cache_status"),
                "llm_call": record.get("llm_call"),
                "cache_file": record.get("cache_file"),
                "input_tokens": record.get("input_tokens"),
                "output_tokens": record.get("output_tokens"),
                "total_tokens": record.get("total_tokens"),
            }
        )
    return {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "prompt_version": PAGE_LECTURE_PROMPT_VERSION,
        "request": {
            "note_depth": note_depth,
            "note_language": note_language,
            "term_policy": term_policy,
            "page_neighborhood": page_neighborhood,
            "deck_brief_used": bool(prompt_brief),
            "deck_brief_hash": _prompt_brief_hash(prompt_brief),
        },
        "summary": {
            "pages_total": len(page_entries),
            "llm_calls": sum(1 for record in page_records if record.get("llm_call")),
            "local_cache_hits": sum(1 for record in page_records if record.get("cache_status") == "local_hit"),
            "input_tokens": sum_int(record.get("input_tokens") for record in page_records),
            "output_tokens": sum_int(record.get("output_tokens") for record in page_records),
            "total_tokens": sum_int(record.get("total_tokens") for record in page_records),
        },
        "pages": page_entries,
    }


def _render_page_notes_markdown(deck: Deck, page_notes: dict[str, Any]) -> str:
    lines = [f"# {Path(deck.source_path).stem} Page Notes", ""]
    for page in page_notes.get("pages", []):
        title = page.get("title") or f"\u7b2c {page.get('slide_id')} \u9875"
        lines.append(f"## \u7b2c {page.get('slide_id')} \u9875\uff1a{title}")
        lines.append("")
        markdown = str(page.get("markdown") or "").strip()
        if markdown:
            lines.append(markdown)
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _build_weave_report(
    deck: Deck,
    note_context: str,
    note_depth: str,
    note_language: str,
    term_policy: str,
    weave_dedup: str,
    contexts: list[NoteContext],
    final_chunks: dict[str, str],
    page_markdown_by_slide: dict[int, str],
    weave_records: list[dict[str, Any]],
    deck_brief: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from slidenote.llm_cache import utc_now_iso
    from .prompt_payload import _prompt_brief_hash, _prompt_deck_brief
    prompt_brief = _prompt_deck_brief(deck_brief)
    record_by_context = {record.get("context_id"): record for record in weave_records}
    context_entries: list[dict[str, Any]] = []
    for context in contexts:
        markdown = final_chunks.get(context.id, "")
        final_tokens = source_tokens(markdown)
        input_tokens: set[str] = set()
        pages: list[dict[str, Any]] = []
        for page in context.pages:
            page_tokens = source_tokens(page_markdown_by_slide.get(page.slide_id, ""))
            input_tokens.update(page_tokens)
            pages.append(
                {
                    "slide_id": page.slide_id,
                    "title": page.title,
                    "page_note_source_ids": sorted(page_tokens),
                    "retained_source_ids": sorted(page_tokens.intersection(final_tokens)),
                    "possibly_compressed_source_ids": sorted(page_tokens - final_tokens),
                }
            )
        record = record_by_context.get(f"weave_{context.id}", {})
        context_entries.append(
            {
                "context_id": context.id,
                "context_title": context.title,
                "slide_ids": [page.slide_id for page in context.pages],
                "input_source_ids": sorted(input_tokens),
                "final_source_ids": sorted(final_tokens),
                "possibly_compressed_source_ids": sorted(input_tokens - final_tokens),
                "cache_status": record.get("cache_status"),
                "llm_call": record.get("llm_call"),
                "cache_file": record.get("cache_file"),
                "pages": pages,
            }
        )
    return {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "prompt_version": WEAVE_PROMPT_VERSION,
        "request": {
            "note_context": note_context,
            "note_depth": note_depth,
            "note_language": note_language,
            "term_policy": term_policy,
            "weave_dedup": weave_dedup,
            "deck_brief_used": bool(prompt_brief),
            "deck_brief_hash": _prompt_brief_hash(prompt_brief),
        },
        "summary": {
            "contexts_total": len(context_entries),
            "llm_calls": sum(1 for record in weave_records if record.get("llm_call")),
            "local_cache_hits": sum(1 for record in weave_records if record.get("cache_status") == "local_hit"),
            "input_tokens": sum_int(record.get("input_tokens") for record in weave_records),
            "output_tokens": sum_int(record.get("output_tokens") for record in weave_records),
            "total_tokens": sum_int(record.get("total_tokens") for record in weave_records),
        },
        "contexts": context_entries,
    }


def _build_teaching_enrichment_report(
    deck: Deck,
    note_context: str,
    note_profile: str,
    note_depth: str,
    note_language: str,
    term_policy: str,
    contexts: list[NoteContext],
    final_chunks: dict[str, str],
    page_markdown_by_slide: dict[int, str],
    teaching_records: list[dict[str, Any]],
    deck_brief: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from slidenote.llm_cache import utc_now_iso
    from .prompt_payload import _prompt_brief_hash, _prompt_deck_brief

    prompt_brief = _prompt_deck_brief(deck_brief)
    record_by_context = {record.get("context_id"): record for record in teaching_records}
    context_entries: list[dict[str, Any]] = []
    for context in contexts:
        markdown = final_chunks.get(context.id, "")
        final_tokens = source_tokens(markdown)
        input_tokens: set[str] = set()
        for page in context.pages:
            input_tokens.update(source_tokens(page_markdown_by_slide.get(page.slide_id, "")))
        record = record_by_context.get(f"teaching_{context.id}", {})
        context_entries.append(
            {
                "context_id": context.id,
                "context_title": context.title,
                "slide_ids": [page.slide_id for page in context.pages],
                "input_source_ids": sorted(input_tokens),
                "final_source_ids": sorted(final_tokens),
                "possibly_added_source_ids": sorted(final_tokens - input_tokens),
                "possibly_dropped_source_ids": sorted(input_tokens - final_tokens),
                "cache_status": record.get("cache_status"),
                "llm_call": record.get("llm_call"),
                "cache_file": record.get("cache_file"),
                "input_tokens": record.get("input_tokens"),
                "output_tokens": record.get("output_tokens"),
                "total_tokens": record.get("total_tokens"),
            }
        )
    return {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "prompt_version": TEACHING_ENRICHMENT_PROMPT_VERSION,
        "request": {
            "note_context": note_context,
            "note_profile": note_profile,
            "note_depth": note_depth,
            "note_language": note_language,
            "term_policy": term_policy,
            "deck_brief_used": bool(prompt_brief),
            "deck_brief_hash": _prompt_brief_hash(prompt_brief),
        },
        "summary": {
            "contexts_total": len(context_entries),
            "llm_calls": sum(1 for record in teaching_records if record.get("llm_call")),
            "local_cache_hits": sum(1 for record in teaching_records if record.get("cache_status") == "local_hit"),
            "input_tokens": sum_int(record.get("input_tokens") for record in teaching_records),
            "output_tokens": sum_int(record.get("output_tokens") for record in teaching_records),
            "total_tokens": sum_int(record.get("total_tokens") for record in teaching_records),
        },
        "contexts": context_entries,
    }
