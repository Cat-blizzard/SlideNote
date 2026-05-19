from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from slidenote.content_guard import record_repair
from slidenote.llm_cache import LLMCache
from slidenote.models import Deck

from .assembly import (
    NoteContext,
    _build_page_notes_report,
    _build_weave_report,
    _compose_final_markdown,
    _ensure_grounded_figures,
    _postprocess_llm_markdown,
    _render_page_notes_markdown,
    _resolved_context_mode,
    _select_note_contexts,
)
from .llm_calls import _generate_page_lecture_context, _generate_weave_context
from .prompt_payload import _section_title_by_slide
from .repair import _repair_required_markdown_once
from .usage import _build_usage_report


def _generate_notes_with_lecture_weave(
    deck: Deck,
    output_root: Path,
    provider: str,
    model: str,
    api_key: str | None,
    base_url: str | None,
    max_output_tokens: int,
    temperature: float | None,
    cache_mode: str,
    cache: LLMCache,
    cache_dir: Path,
    concurrency: int,
    refresh_slide_ids: set[int] | None,
    progress_callback: Callable[[dict[str, Any]], None] | None,
    asset_map: dict[str, str],
    asset_mode: str,
    source_display: str,
    note_context: str,
    note_style: str,
    note_depth: str,
    note_language: str,
    term_policy: str,
    weave_dedup: str,
    page_neighborhood: int,
    screenshot_policy: str,
    figure_placement: str,
    supports_image_input: bool,
    section_plan: dict[str, Any] | None,
    deck_brief: dict[str, Any] | None,
    content_guard: dict[str, Any] | None = None,
) -> "NoteGenerationResult":  # string annotation to avoid circular import
    from . import NoteGenerationResult

    refresh_ids = refresh_slide_ids or set()
    workers = max(1, int(concurrency or 1))
    page_contexts = [
        NoteContext(id=f"p{page.slide_id}", kind="page_note", title=page.title or f"\u7b2c {page.slide_id} \u9875", pages=[page])
        for page in deck.pages
    ]
    section_titles = _section_title_by_slide(deck, section_plan=section_plan)
    page_results: dict[str, tuple[str, dict[str, Any]]] = {}

    def process_page(context: NoteContext) -> tuple[str, str, dict[str, Any]]:
        page = context.pages[0]
        content, record = _generate_page_lecture_context(
            deck=deck,
            context=context,
            output_root=output_root,
            cache=cache,
            cache_mode=cache_mode,
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            supports_image_input=supports_image_input,
            force_refresh=page.slide_id in refresh_ids,
            asset_map=asset_map,
            asset_mode=asset_mode,
            source_display=source_display,
            note_style=note_style,
            note_depth=note_depth,
            note_language=note_language,
            term_policy=term_policy,
            page_neighborhood=page_neighborhood,
            section_title=section_titles.get(page.slide_id),
            screenshot_policy=screenshot_policy,
            figure_placement=figure_placement,
            deck_brief=deck_brief,
            content_guard=content_guard,
        )
        content = _postprocess_llm_markdown(content, source_display=source_display)
        page_deck = Deck(source_path=deck.source_path, source_type=deck.source_type, pages=[page])
        content, repair_record = _repair_required_markdown_once(
            deck=page_deck,
            context=context,
            markdown=content,
            output_root=output_root,
            cache=cache,
            cache_mode=cache_mode,
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            source_display=source_display,
            note_language=note_language,
            term_policy=term_policy,
            content_guard=content_guard,
            stage="page_note",
        )
        if repair_record is not None:
            record["content_guard_repair"] = repair_record
        return context.id, content, record

    if workers == 1:
        for context in page_contexts:
            context_id, content, record = process_page(context)
            page_results[context_id] = (content, record)
            if progress_callback:
                progress_callback(record)
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(process_page, context): context for context in page_contexts}
            for future in as_completed(futures):
                context_id, content, record = future.result()
                page_results[context_id] = (content, record)
                if progress_callback:
                    progress_callback(record)

    page_markdown_by_slide: dict[int, str] = {}
    page_records: list[dict[str, Any]] = []
    repair_context_records: list[dict[str, Any]] = []
    for context in page_contexts:
        content, record = page_results[context.id]
        page_markdown_by_slide[context.pages[0].slide_id] = content
        page_records.append(record)
        repair_record = record.get("content_guard_repair")
        if isinstance(repair_record, dict):
            record_repair(content_guard, repair_record)
            if isinstance(repair_record.get("llm"), dict):
                repair_context_records.append(repair_record["llm"])

    resolved_note_context = _resolved_context_mode(deck, note_context)
    weave_contexts = _select_note_contexts(deck, note_context, section_plan=section_plan)
    weave_results: dict[str, tuple[str, dict[str, Any]]] = {}

    def process_weave(context: NoteContext) -> tuple[str, str, dict[str, Any]]:
        content, record = _generate_weave_context(
            context=context,
            page_markdown_by_slide=page_markdown_by_slide,
            output_root=output_root,
            cache=cache,
            cache_mode=cache_mode,
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            force_refresh=bool(refresh_ids.intersection({page.slide_id for page in context.pages})),
            source_display=source_display,
            note_context=resolved_note_context,
            note_style=note_style,
            note_depth=note_depth,
            note_language=note_language,
            term_policy=term_policy,
            weave_dedup=weave_dedup,
            deck_brief=deck_brief,
            content_guard=content_guard,
        )
        return context.id, _postprocess_llm_markdown(content, source_display=source_display), record

    if workers == 1:
        for context in weave_contexts:
            context_id, content, record = process_weave(context)
            weave_results[context_id] = (content, record)
            if progress_callback:
                progress_callback(record)
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(process_weave, context): context for context in weave_contexts}
            for future in as_completed(futures):
                context_id, content, record = future.result()
                weave_results[context_id] = (content, record)
                if progress_callback:
                    progress_callback(record)

    weave_records: list[dict[str, Any]] = []
    final_chunks: dict[str, str] = {}
    for context in weave_contexts:
        content, record = weave_results[context.id]
        final_chunks[context.id] = content
        weave_records.append(record)

    markdown = _compose_final_markdown(
        deck=deck,
        contexts=weave_contexts,
        final_chunks=final_chunks,
        section_plan=section_plan,
        source_display=source_display,
    )
    markdown = _ensure_grounded_figures(markdown, deck, asset_map, source_display, figure_placement)
    markdown, final_repair_record = _repair_required_markdown_once(
        deck=deck,
        context=NoteContext(id="final", kind="final", title="final", pages=deck.pages),
        markdown=markdown,
        output_root=output_root,
        cache=cache,
        cache_mode=cache_mode,
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
        source_display=source_display,
        note_language=note_language,
        term_policy=term_policy,
        content_guard=content_guard,
        stage="weave",
    )
    if final_repair_record is not None:
        record_repair(content_guard, final_repair_record)
        if isinstance(final_repair_record.get("llm"), dict):
            repair_context_records.append(final_repair_record["llm"])
    markdown = _ensure_grounded_figures(markdown, deck, asset_map, source_display, figure_placement)
    all_context_records = page_records + weave_records + repair_context_records
    usage_report = _build_usage_report(
        deck=deck,
        provider=provider,
        model=model,
        base_url=base_url,
        cache_mode=cache_mode,
        cache_dir=cache_dir,
        output_root=output_root,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
        contexts=all_context_records,
        note_context=resolved_note_context,
        source_display=source_display,
        note_style=note_style,
        note_strategy="lecture-weave",
        note_depth=note_depth,
        note_language=note_language,
        term_policy=term_policy,
        weave_dedup=weave_dedup,
        page_neighborhood=page_neighborhood,
        asset_mode=asset_mode,
        screenshot_policy=screenshot_policy,
        figure_placement=figure_placement,
        page_contexts=page_records,
        weave_contexts=weave_records,
        repair_contexts=repair_context_records,
        deck_brief=deck_brief,
        content_guard=content_guard,
    )

    page_notes = _build_page_notes_report(
        deck=deck,
        provider=provider,
        model=model,
        base_url=base_url,
        output_root=output_root,
        note_depth=note_depth,
        note_language=note_language,
        term_policy=term_policy,
        page_neighborhood=page_neighborhood,
        pages=page_contexts,
        page_markdown_by_slide=page_markdown_by_slide,
        page_records=page_records,
        deck_brief=deck_brief,
    )
    weave_report = _build_weave_report(
        deck=deck,
        output_root=output_root,
        note_context=resolved_note_context,
        note_depth=note_depth,
        note_language=note_language,
        term_policy=term_policy,
        weave_dedup=weave_dedup,
        contexts=weave_contexts,
        final_chunks=final_chunks,
        page_markdown_by_slide=page_markdown_by_slide,
        weave_records=weave_records,
        deck_brief=deck_brief,
    )
    return NoteGenerationResult(
        markdown=markdown,
        llm_usage=usage_report,
        page_notes=page_notes,
        page_notes_markdown=_render_page_notes_markdown(deck, page_notes),
        weave_report=weave_report,
    )
