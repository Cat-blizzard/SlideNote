from __future__ import annotations

from pathlib import Path
from typing import Any

from slidenote.llm_cache import utc_now_iso
from slidenote.models import Deck

from .assembly import _display_path, _sum_int
from .prompt_payload import _prompt_brief_hash, _prompt_deck_brief
from .versions import NOTE_PROMPT_VERSION


def _build_usage_report(
    deck: Deck,
    provider: str,
    model: str,
    base_url: str | None,
    cache_mode: str,
    cache_dir: Path,
    output_root: Path,
    max_output_tokens: int,
    temperature: float | None,
    contexts: list[dict[str, Any]],
    note_context: str,
    source_display: str,
    note_style: str,
    note_strategy: str,
    note_depth: str,
    note_language: str,
    term_policy: str,
    weave_dedup: str,
    page_neighborhood: int,
    asset_mode: str,
    screenshot_policy: str,
    figure_placement: str,
    page_contexts: list[dict[str, Any]] | None = None,
    weave_contexts: list[dict[str, Any]] | None = None,
    repair_contexts: list[dict[str, Any]] | None = None,
    deck_brief: dict[str, Any] | None = None,
    content_guard: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prompt_brief = _prompt_deck_brief(deck_brief)
    summary = {
        "pages_total": len(deck.pages),
        "contexts_total": len(contexts),
        "page_note_contexts": len(page_contexts or []),
        "weave_contexts": len(weave_contexts or []),
        "repair_contexts": len(repair_contexts or []),
        "page_note_calls": sum(1 for context in (page_contexts or []) if context.get("llm_call")),
        "weave_calls": sum(1 for context in (weave_contexts or []) if context.get("llm_call")),
        "local_cache_hits": sum(1 for context in contexts if context.get("cache_status") == "local_hit"),
        "local_cache_misses": sum(1 for context in contexts if context.get("cache_status") == "miss"),
        "local_cache_refreshes": sum(1 for context in contexts if context.get("cache_status") == "refresh"),
        "cache_disabled_calls": sum(1 for context in contexts if context.get("cache_status") == "disabled"),
        "llm_calls": sum(1 for context in contexts if context.get("llm_call")),
        "api_retries": sum(int(context.get("api_retries") or 0) for context in contexts),
        "input_tokens": _sum_int(context.get("input_tokens") for context in contexts),
        "output_tokens": _sum_int(context.get("output_tokens") for context in contexts),
        "total_tokens": _sum_int(context.get("total_tokens") for context in contexts),
        "provider_cached_input_tokens": _sum_int(context.get("provider_cached_input_tokens") for context in contexts),
    }
    return {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "prompt_version": NOTE_PROMPT_VERSION,
        "cache": {
            "mode": cache_mode,
            "dir": _display_path(cache_dir, output_root),
        },
        "request": {
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
            "note_context": note_context,
            "note_strategy": note_strategy,
            "note_depth": note_depth,
            "note_language": note_language,
            "term_policy": term_policy,
            "weave_dedup": weave_dedup,
            "page_neighborhood": page_neighborhood,
            "source_display": source_display,
            "note_style": note_style,
            "asset_mode": asset_mode,
            "screenshot_policy": screenshot_policy,
            "figure_placement": figure_placement,
            "deck_brief_used": bool(prompt_brief),
            "deck_brief_hash": _prompt_brief_hash(prompt_brief),
            "content_guard_used": bool(content_guard),
        },
        "summary": summary,
        "pages": contexts,
        "contexts": contexts,
        "page_contexts": page_contexts or [],
        "weave_contexts": weave_contexts or [],
        "repair_contexts": repair_contexts or [],
    }


def _render_generation_info(usage_report: dict[str, Any]) -> list[str]:
    summary = usage_report["summary"]
    cache_mode = usage_report["cache"]["mode"]
    lines = [
        "## \u751f\u6210\u4fe1\u606f",
        "",
        f"- LLM provider\uff1a{usage_report['provider']}",
        f"- \u6a21\u578b\uff1a{usage_report['model']}",
        f"- \u7f13\u5b58\u6a21\u5f0f\uff1a{cache_mode}",
        f"- \u7b14\u8bb0\u7b56\u7565\uff1a{usage_report['request'].get('note_strategy', 'direct')}",
        f"- \u8f93\u51fa\u8bed\u8a00\uff1a{usage_report['request'].get('note_language', 'zh')}",
        f"- \u672f\u8bed\u7b56\u7565\uff1a{usage_report['request'].get('term_policy', 'bilingual')}",
        f"- \u751f\u6210\u4e0a\u4e0b\u6587\uff1a{summary.get('contexts_total', summary['pages_total'])} \u4e2a",
        f"- \u672c\u5730\u7f13\u5b58\u547d\u4e2d\uff1a{summary['local_cache_hits']} / {summary.get('contexts_total', summary['pages_total'])} \u4e2a\u4e0a\u4e0b\u6587",
        f"- \u5b9e\u9645 LLM \u8c03\u7528\uff1a{summary['llm_calls']} \u4e2a\u4e0a\u4e0b\u6587",
        "- \u8be6\u7ec6\u7528\u91cf\u4e0e\u7f13\u5b58\u4fe1\u606f\uff1a`llm_usage.json`",
        "",
    ]
    if usage_report["request"].get("note_strategy") == "lecture-weave":
        lines.insert(8, f"- \u9010\u9875\u6df1\u8bb2\u8c03\u7528\uff1a{summary.get('page_note_calls', 0)} \u4e2a")
        lines.insert(9, f"- \u7ae0\u8282\u7f16\u7ec7\u8c03\u7528\uff1a{summary.get('weave_calls', 0)} \u4e2a")
    return lines
