from __future__ import annotations

from pathlib import Path
from typing import Any

from slidenote.llm_cache import utc_now_iso
from slidenote.models import Deck
from slidenote.utils import display_path, sum_int

from .prompt_payload import _prompt_brief_hash, _prompt_deck_brief


def _build_usage_report(
    deck: Deck,
    *,
    output_root: Path,
    options: "NoteOptions",
    contexts: list[dict[str, Any]],
    note_strategy: str,
    prompt_version: str,
    page_contexts: list[dict[str, Any]] | None = None,
    weave_contexts: list[dict[str, Any]] | None = None,
    teaching_enrichment_contexts: list[dict[str, Any]] | None = None,
    repair_contexts: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    prompt_brief = _prompt_deck_brief(options.deck_brief)
    summary = {
        "pages_total": len(deck.pages),
        "contexts_total": len(contexts),
        "page_note_contexts": len(page_contexts or []),
        "weave_contexts": len(weave_contexts or []),
        "teaching_enrichment_contexts": len(teaching_enrichment_contexts or []),
        "repair_contexts": len(repair_contexts or []),
        "page_note_calls": sum(1 for context in (page_contexts or []) if context.get("llm_call")),
        "weave_calls": sum(1 for context in (weave_contexts or []) if context.get("llm_call")),
        "teaching_enrichment_calls": sum(1 for context in (teaching_enrichment_contexts or []) if context.get("llm_call")),
        "repair_calls": sum(1 for context in (repair_contexts or []) if context.get("llm_call")),
        "local_cache_hits": sum(1 for context in contexts if context.get("cache_status") == "local_hit"),
        "local_cache_misses": sum(1 for context in contexts if context.get("cache_status") == "miss"),
        "local_cache_refreshes": sum(1 for context in contexts if context.get("cache_status") == "refresh"),
        "cache_disabled_calls": sum(1 for context in contexts if context.get("cache_status") == "disabled"),
        "failed_contexts": sum(1 for context in contexts if context.get("cache_status") == "failed"),
        "llm_calls": sum(1 for context in contexts if context.get("llm_call")),
        "api_retries": sum(int(context.get("api_retries") or 0) for context in contexts),
        "input_tokens": sum_int(context.get("input_tokens") for context in contexts),
        "output_tokens": sum_int(context.get("output_tokens") for context in contexts),
        "total_tokens": sum_int(context.get("total_tokens") for context in contexts),
        "provider_cached_input_tokens": sum_int(context.get("provider_cached_input_tokens") for context in contexts),
    }
    return {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "provider": options.provider,
        "model": options.model,
        "base_url": options.base_url,
        "prompt_version": prompt_version,
        "cache": {
            "mode": options.cache_mode,
            "dir": display_path(options.cache_dir, output_root),
        },
        "request": {
            "temperature": options.temperature,
            "max_output_tokens": options.max_output_tokens,
            "note_context": options.note_context,
            "note_strategy": note_strategy,
            "note_depth": options.note_depth,
            "note_profile": options.note_profile,
            "note_language": options.note_language,
            "term_policy": options.term_policy,
            "teaching_enrichment": options.teaching_enrichment,
            "weave_dedup": options.weave_dedup,
            "page_neighborhood": options.page_neighborhood,
            "source_display": options.source_display,
            "note_style": options.note_style,
            "asset_mode": options.asset_mode,
            "screenshot_policy": options.screenshot_policy,
            "figure_placement": options.figure_placement,
            "deck_brief_used": bool(prompt_brief),
            "deck_brief_hash": _prompt_brief_hash(prompt_brief),
            "content_guard_used": bool(options.content_guard),
        },
        "summary": summary,
        "warnings": list(warnings or []),
        "contexts": contexts,
        "page_contexts": page_contexts or [],
        "weave_contexts": weave_contexts or [],
        "teaching_enrichment_contexts": teaching_enrichment_contexts or [],
        "repair_contexts": repair_contexts or [],
    }
