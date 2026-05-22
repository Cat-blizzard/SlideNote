from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from slidenote.content_guard import record_repair
from slidenote.llm import resolve_provider_runtime
from slidenote.llm_cache import LLMCache
from slidenote.models import Deck

from .assembly import (
    NoteContext,
    _compose_final_markdown,
    _ensure_grounded_figures,
    _postprocess_llm_markdown,
    _repair_markdown_image_links,
    _resolved_context_mode,
    _select_note_contexts,
)
from .lecture_weave import _generate_notes_with_lecture_weave
from .llm_calls import _generate_llm_context
from .repair import _repair_required_markdown_once
from .usage import _build_usage_report


def _generate_notes_with_llm(
    deck: Deck,
    output_root: Path,
    provider: str,
    model: str | None,
    api_key: str | None,
    base_url: str | None,
    max_output_tokens: int,
    temperature: float | None,
    cache_mode: str,
    cache_dir: Path | None,
    concurrency: int,
    refresh_slide_ids: set[int] | None,
    progress_callback: Callable[[dict[str, Any]], None] | None,
    asset_map: dict[str, str],
    asset_mode: str,
    source_display: str,
    note_context: str,
    note_style: str,
    note_strategy: str,
    note_depth: str,
    note_language: str,
    term_policy: str,
    weave_dedup: str,
    page_neighborhood: int,
    screenshot_policy: str,
    figure_placement: str,
    section_plan: dict[str, Any] | None,
    deck_brief: dict[str, Any] | None,
    content_guard: dict[str, Any] | None = None,
) -> "NoteGenerationResult":  # string annotation to avoid circular import
    from . import NoteGenerationResult

    runtime = resolve_provider_runtime(provider, model=model, base_url=base_url)
    resolved_provider = str(runtime["provider"])
    resolved_model = str(runtime["model"])
    resolved_base_url = runtime["base_url"]
    supports_image_input = bool(runtime["supports_image_input"])
    resolved_cache_dir = (cache_dir or (output_root / ".cache" / "llm")).resolve()
    cache = LLMCache(resolved_cache_dir, mode=cache_mode)
    if note_strategy == "lecture-weave":
        return _generate_notes_with_lecture_weave(
            deck=deck,
            output_root=output_root,
            provider=resolved_provider,
            model=resolved_model,
            api_key=api_key,
            base_url=resolved_base_url,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            cache_mode=cache_mode,
            cache=cache,
            cache_dir=resolved_cache_dir,
            concurrency=concurrency,
            refresh_slide_ids=refresh_slide_ids,
            progress_callback=progress_callback,
            asset_map=asset_map,
            asset_mode=asset_mode,
            source_display=source_display,
            note_context=note_context,
            note_style=note_style,
            note_depth=note_depth,
            note_language=note_language,
            term_policy=term_policy,
            weave_dedup=weave_dedup,
            page_neighborhood=page_neighborhood,
            screenshot_policy=screenshot_policy,
            figure_placement=figure_placement,
            supports_image_input=supports_image_input,
            section_plan=section_plan,
            deck_brief=deck_brief,
            content_guard=content_guard,
        )

    contexts = _select_note_contexts(deck, note_context, section_plan=section_plan)
    resolved_note_context = _resolved_context_mode(deck, note_context)
    refresh_ids = refresh_slide_ids or set()
    workers = max(1, int(concurrency or 1))
    context_results: dict[str, tuple[str, dict[str, Any]]] = {}

    def process(context: NoteContext) -> tuple[str, str, dict[str, Any]]:
        content, context_record = _generate_llm_context(
            context=context,
            output_root=output_root,
            cache=cache,
            cache_mode=cache_mode,
            provider=resolved_provider,
            model=resolved_model,
            api_key=api_key,
            base_url=resolved_base_url,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            supports_image_input=supports_image_input,
            force_refresh=bool(refresh_ids.intersection({page.slide_id for page in context.pages})),
            asset_map=asset_map,
            asset_mode=asset_mode,
            source_display=source_display,
            note_context=resolved_note_context,
            note_style=note_style,
            note_depth=note_depth,
            note_language=note_language,
            term_policy=term_policy,
            screenshot_policy=screenshot_policy,
            figure_placement=figure_placement,
            source_type=deck.source_type,
            deck_brief=deck_brief,
            content_guard=content_guard,
        )
        content = _postprocess_llm_markdown(content, source_display=source_display)
        return context.id, content, context_record

    if workers == 1:
        for context in contexts:
            context_id, content, context_record = process(context)
            context_results[context_id] = (content, context_record)
            if progress_callback:
                progress_callback(context_record)
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(process, context): context for context in contexts}
            for future in as_completed(futures):
                context_id, content, context_record = future.result()
                context_results[context_id] = (content, context_record)
                if progress_callback:
                    progress_callback(context_record)

    usage_contexts: list[dict[str, Any]] = []
    final_chunks: dict[str, str] = {}
    for context in contexts:
        content, context_record = context_results[context.id]
        usage_contexts.append(context_record)
        final_chunks[context.id] = content

    markdown = _compose_final_markdown(
        deck=deck,
        contexts=contexts,
        final_chunks=final_chunks,
        section_plan=section_plan,
        source_display=source_display,
    )
    markdown = _repair_markdown_image_links(markdown, output_root, asset_map)
    markdown = _ensure_grounded_figures(markdown, deck, asset_map, source_display, figure_placement)
    repair_context_records: list[dict[str, Any]] = []
    markdown, repair_record = _repair_required_markdown_once(
        deck=deck,
        context=NoteContext(id="final", kind="final", title="final", pages=deck.pages),
        markdown=markdown,
        output_root=output_root,
        cache=cache,
        cache_mode=cache_mode,
        provider=resolved_provider,
        model=resolved_model,
        api_key=api_key,
        base_url=resolved_base_url,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
        source_display=source_display,
        note_language=note_language,
        term_policy=term_policy,
        content_guard=content_guard,
        stage="final",
    )
    if repair_record is not None:
        record_repair(content_guard, repair_record)
        if isinstance(repair_record.get("llm"), dict):
            repair_context_records.append(repair_record["llm"])
    markdown = _repair_markdown_image_links(markdown, output_root, asset_map)
    markdown = _ensure_grounded_figures(markdown, deck, asset_map, source_display, figure_placement)
    markdown = _repair_markdown_image_links(markdown, output_root, asset_map)
    usage_report = _build_usage_report(
        deck=deck,
        provider=resolved_provider,
        model=resolved_model,
        base_url=resolved_base_url,
        cache_mode=cache_mode,
        cache_dir=resolved_cache_dir,
        output_root=output_root,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
        contexts=usage_contexts + repair_context_records,
        note_context=resolved_note_context,
        source_display=source_display,
        note_style=note_style,
        note_strategy=note_strategy,
        note_depth=note_depth,
        note_language=note_language,
        term_policy=term_policy,
        weave_dedup=weave_dedup,
        page_neighborhood=page_neighborhood,
        asset_mode=asset_mode,
        screenshot_policy=screenshot_policy,
        figure_placement=figure_placement,
        repair_contexts=repair_context_records,
        deck_brief=deck_brief,
        content_guard=content_guard,
    )
    return NoteGenerationResult(markdown=markdown, llm_usage=usage_report)
