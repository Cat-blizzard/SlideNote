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
    _build_teaching_enrichment_report,
    _build_weave_report,
    _compose_final_markdown,
    _ensure_grounded_figures,
    _postprocess_llm_markdown,
    _repair_markdown_image_links,
    _render_page_notes_markdown,
    _resolved_context_mode,
    _select_note_contexts,
)
from .llm_calls import _generate_page_lecture_context, _generate_teaching_enrichment_context, _generate_weave_context
from .options import should_run_teaching_enrichment
from .prompt_payload import _section_title_by_slide
from .repair import _repair_required_markdown_once
from .usage import _build_usage_report


def _generate_notes_with_lecture_weave(
    deck: Deck,
    output_root: Path,
    options: "NoteOptions",
    *,
    note_depth: str,
    asset_map: dict[str, str],
    resolved_provider: str,
    resolved_model: str,
    resolved_base_url: str,
    resolved_cache_dir: Path,
    cache: LLMCache,
    supports_image_input: bool,
) -> "NoteGenerationResult":  # string annotation to avoid circular import
    from . import NoteGenerationResult

    # Unpack the option object into the locals the rest of this function uses.
    provider = resolved_provider
    model = resolved_model
    api_key = options.api_key
    base_url = resolved_base_url
    max_output_tokens = options.max_output_tokens
    temperature = options.temperature
    cache_mode = options.cache_mode
    cache_dir = resolved_cache_dir
    concurrency = options.concurrency
    refresh_slide_ids = options.refresh_slide_ids
    progress_callback = options.progress_callback
    asset_mode = options.asset_mode
    source_display = options.source_display
    note_context = options.note_context
    note_style = options.note_style
    note_profile = options.note_profile
    note_language = options.note_language
    term_policy = options.term_policy
    teaching_enrichment = options.teaching_enrichment
    weave_dedup = options.weave_dedup
    page_neighborhood = options.page_neighborhood
    screenshot_policy = options.screenshot_policy
    figure_placement = options.figure_placement
    section_plan = options.section_plan
    deck_brief = options.deck_brief
    content_guard = options.content_guard

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
            options=options,
            provider=provider,
            model=model,
            base_url=base_url,
            supports_image_input=supports_image_input,
            force_refresh=page.slide_id in refresh_ids,
            asset_map=asset_map,
            note_depth=note_depth,
            page_neighborhood=page_neighborhood,
            section_title=section_titles.get(page.slide_id),
        )
        content = _postprocess_llm_markdown(content, source_display=source_display)
        page_deck = Deck(source_path=deck.source_path, source_type=deck.source_type, pages=[page])
        content, repair_record = _repair_required_markdown_once(
            deck=page_deck,
            context=context,
            markdown=content,
            output_root=output_root,
            cache=cache,
            options=options,
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
            options=options,
            provider=provider,
            model=model,
            base_url=base_url,
            note_context=resolved_note_context,
            note_depth=note_depth,
            force_refresh=bool(refresh_ids.intersection({page.slide_id for page in context.pages})),
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

    teaching_records: list[dict[str, Any]] = []
    teaching_report: dict[str, Any] | None = None
    if should_run_teaching_enrichment(note_profile, teaching_enrichment, "lecture-weave"):
        teaching_results: dict[str, tuple[str, dict[str, Any]]] = {}

        def process_teaching(context: NoteContext) -> tuple[str, str, dict[str, Any]]:
            content, record = _generate_teaching_enrichment_context(
                context=context,
                woven_markdown=final_chunks.get(context.id, ""),
                page_markdown_by_slide=page_markdown_by_slide,
                output_root=output_root,
                cache=cache,
                options=options,
                provider=provider,
                model=model,
                base_url=base_url,
                note_context=resolved_note_context,
                note_depth=note_depth,
                force_refresh=bool(refresh_ids.intersection({page.slide_id for page in context.pages})),
            )
            return context.id, _postprocess_llm_markdown(content, source_display=source_display), record

        if workers == 1:
            for context in weave_contexts:
                context_id, content, record = process_teaching(context)
                teaching_results[context_id] = (content, record)
                if progress_callback:
                    progress_callback(record)
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(process_teaching, context): context for context in weave_contexts}
                for future in as_completed(futures):
                    context_id, content, record = future.result()
                    teaching_results[context_id] = (content, record)
                    if progress_callback:
                        progress_callback(record)

        for context in weave_contexts:
            content, record = teaching_results[context.id]
            final_chunks[context.id] = content
            teaching_records.append(record)

    markdown = _compose_final_markdown(
        deck=deck,
        contexts=weave_contexts,
        final_chunks=final_chunks,
        section_plan=section_plan,
        source_display=source_display,
    )
    markdown = _repair_markdown_image_links(markdown, output_root, asset_map)
    markdown = _ensure_grounded_figures(markdown, deck, asset_map, source_display, figure_placement)
    markdown, final_repair_record = _repair_required_markdown_once(
        deck=deck,
        context=NoteContext(id="final", kind="final", title="final", pages=deck.pages),
        markdown=markdown,
        output_root=output_root,
        cache=cache,
        options=options,
        stage="weave",
    )
    if final_repair_record is not None:
        record_repair(content_guard, final_repair_record)
        if isinstance(final_repair_record.get("llm"), dict):
            repair_context_records.append(final_repair_record["llm"])
    markdown = _repair_markdown_image_links(markdown, output_root, asset_map)
    markdown = _ensure_grounded_figures(markdown, deck, asset_map, source_display, figure_placement)
    markdown = _repair_markdown_image_links(markdown, output_root, asset_map)
    all_context_records = page_records + weave_records + teaching_records + repair_context_records
    usage_report = _build_usage_report(
        deck=deck,
        output_root=output_root,
        options=options,
        contexts=all_context_records,
        note_strategy="lecture-weave",
        page_contexts=page_records,
        weave_contexts=weave_records,
        teaching_enrichment_contexts=teaching_records,
        repair_contexts=repair_context_records,
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
    if teaching_records:
        teaching_report = _build_teaching_enrichment_report(
            deck=deck,
            output_root=output_root,
            note_context=resolved_note_context,
            note_profile=note_profile,
            note_depth=note_depth,
            note_language=note_language,
            term_policy=term_policy,
            contexts=weave_contexts,
            final_chunks=final_chunks,
            page_markdown_by_slide=page_markdown_by_slide,
            teaching_records=teaching_records,
            deck_brief=deck_brief,
        )
    return NoteGenerationResult(
        markdown=markdown,
        llm_usage=usage_report,
        page_notes=page_notes,
        page_notes_markdown=_render_page_notes_markdown(deck, page_notes),
        weave_report=weave_report,
        teaching_report=teaching_report,
    )
