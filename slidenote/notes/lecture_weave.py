from __future__ import annotations

from pathlib import Path
from typing import Any

from slidenote.content_guard import record_repair
from slidenote.llm_cache import LLMCache
from slidenote.models import Deck

from .contexts import NoteContext, _resolved_context_mode, _select_note_contexts
from .postprocess import _postprocess_llm_markdown
from .reports import _build_page_notes_report, _build_teaching_enrichment_report, _build_weave_report, _render_page_notes_markdown
from .context_runner import _context_failure_warnings, _failed_context_record, _run_note_contexts
from .finalize import _finalize_notes_markdown
from .llm_calls import _generate_page_lecture_context, _generate_teaching_enrichment_context, _generate_weave_context
from .local import _render_local_context
from .options import needs_teaching_enrichment, should_run_teaching_enrichment
from .prompt_payload import _section_title_by_slide
from .repair import _repair_required_markdown_once
from .usage import _build_usage_report
from .versions import WEAVE_PROMPT_VERSION


def _generate_notes_with_lecture_weave(
    deck: Deck,
    output_root: Path,
    options: "NoteOptions",
    *,
    note_depth: str,
    asset_map: dict[str, str],
    cache: LLMCache,
    supports_image_input: bool,
) -> "NoteGenerationResult":  # string annotation to avoid circular import
    from . import NoteGenerationResult

    # ``direct`` resolves provider runtime defaults once and stores them back
    # into the immutable copy passed here, keeping calls, cache keys and reports
    # on the same canonical provider/model/base URL/cache directory.
    page_contexts = [
        NoteContext(id=f"p{page.slide_id}", kind="page_note", title=page.title or f"第 {page.slide_id} 页", pages=[page])
        for page in deck.pages
    ]
    page_markdown_by_slide, page_records, repair_context_records = _generate_page_notes(
        deck, page_contexts, output_root, options, note_depth=note_depth, asset_map=asset_map,
        cache=cache, supports_image_input=supports_image_input,
    )

    resolved_note_context = _resolved_context_mode(deck, options.note_context)
    weave_contexts = _select_note_contexts(deck, options.note_context, section_plan=options.section_plan)
    final_chunks, weave_records = _weave_contexts(
        weave_contexts, page_markdown_by_slide, output_root, options, cache=cache,
        note_context=resolved_note_context, note_depth=note_depth,
    )
    teaching_contexts = _teaching_contexts(weave_contexts, final_chunks, options)
    if options.progress_callback and options.teaching_enrichment == "auto":
        options.progress_callback({"event": "total", "total": len(page_contexts) + len(weave_contexts) + len(teaching_contexts)})
    teaching_records = _enrich_teaching_contexts(
        teaching_contexts, final_chunks, page_markdown_by_slide, output_root, options, cache=cache,
        note_context=resolved_note_context, note_depth=note_depth,
    )

    markdown, final_repair_records = _finalize_notes_markdown(
        deck,
        weave_contexts,
        final_chunks,
        output_root=output_root,
        cache=cache,
        options=options,
        asset_map=asset_map,
        stage="weave",
    )
    repair_context_records.extend(final_repair_records)
    warnings = _context_failure_warnings(page_records + weave_records + teaching_records)
    usage_report = _build_usage_report(
        deck=deck,
        output_root=output_root,
        options=options,
        contexts=page_records + weave_records + teaching_records + repair_context_records,
        note_strategy="lecture-weave",
        prompt_version=WEAVE_PROMPT_VERSION,
        page_contexts=page_records,
        weave_contexts=weave_records,
        teaching_enrichment_contexts=teaching_records,
        repair_contexts=repair_context_records,
        warnings=warnings,
    )

    page_notes = _build_page_notes_report(
        deck=deck,
        provider=options.provider,
        model=options.model,
        base_url=options.base_url,
        note_depth=note_depth,
        note_language=options.note_language,
        term_policy=options.term_policy,
        page_neighborhood=options.page_neighborhood,
        pages=page_contexts,
        page_markdown_by_slide=page_markdown_by_slide,
        page_records=page_records,
        deck_brief=options.deck_brief,
    )
    weave_report = _build_weave_report(
        deck=deck,
        note_context=resolved_note_context,
        note_depth=note_depth,
        note_language=options.note_language,
        term_policy=options.term_policy,
        weave_dedup=options.weave_dedup,
        contexts=weave_contexts,
        final_chunks=final_chunks,
        page_markdown_by_slide=page_markdown_by_slide,
        weave_records=weave_records,
        deck_brief=options.deck_brief,
    )
    teaching_report = None
    if teaching_records:
        teaching_report = _build_teaching_enrichment_report(
            deck=deck,
            note_context=resolved_note_context,
            note_profile=options.note_profile,
            note_depth=note_depth,
            note_language=options.note_language,
            term_policy=options.term_policy,
            contexts=teaching_contexts,
            final_chunks=final_chunks,
            page_markdown_by_slide=page_markdown_by_slide,
            teaching_records=teaching_records,
            deck_brief=options.deck_brief,
        )
    return NoteGenerationResult(
        markdown=markdown,
        llm_usage=usage_report,
        page_notes=page_notes,
        page_notes_markdown=_render_page_notes_markdown(deck, page_notes),
        weave_report=weave_report,
        teaching_report=teaching_report,
        generation_warnings=warnings,
    )


def _refresh_requested(options: "NoteOptions", context: NoteContext) -> bool:
    return bool((options.refresh_slide_ids or set()).intersection(page.slide_id for page in context.pages))


def _workers(options: "NoteOptions") -> int:
    return max(1, int(options.concurrency or 1))


def _generate_page_notes(
    deck: Deck,
    page_contexts: list[NoteContext],
    output_root: Path,
    options: "NoteOptions",
    *,
    note_depth: str,
    asset_map: dict[str, str],
    cache: LLMCache,
    supports_image_input: bool,
) -> tuple[dict[int, str], list[dict[str, Any]], list[dict[str, Any]]]:
    """Stage 1: one lecture-style note per page, each repaired for required items."""
    section_titles = _section_title_by_slide(deck, section_plan=options.section_plan)

    def process_page(context: NoteContext) -> tuple[str, dict[str, Any]]:
        page = context.pages[0]
        force_refresh = _refresh_requested(options, context)
        content, record = _generate_page_lecture_context(
            deck=deck,
            context=context,
            output_root=output_root,
            cache=cache,
            options=options,
            provider=options.provider,
            model=options.model,
            base_url=options.base_url,
            supports_image_input=supports_image_input,
            force_refresh=force_refresh,
            asset_map=asset_map,
            note_depth=note_depth,
            page_neighborhood=options.page_neighborhood,
            section_title=section_titles.get(page.slide_id),
        )
        content = _postprocess_llm_markdown(content, source_display=options.source_display)
        page_deck = Deck(source_path=deck.source_path, source_type=deck.source_type, pages=[page])
        content, repair_record = _repair_required_markdown_once(
            deck=page_deck,
            context=context,
            markdown=content,
            output_root=output_root,
            cache=cache,
            options=options,
            stage="page_note",
            force_refresh=force_refresh,
        )
        if repair_record is not None:
            record["content_guard_repair"] = repair_record
        return content, record

    def local_fallback(context: NoteContext, exc: Exception) -> tuple[str, dict[str, Any]]:
        content = _render_local_context(
            context,
            asset_map=asset_map,
            source_display=options.source_display,
            note_style=options.note_style,
            screenshot_policy=options.screenshot_policy,
            figure_placement=options.figure_placement,
        )
        return content, _failed_context_record(context, exc, generation_stage="page_note", fallback="local")

    page_results = _run_note_contexts(
        page_contexts, process_page, workers=_workers(options),
        progress_callback=options.progress_callback, fallback=local_fallback,
    )
    page_markdown_by_slide: dict[int, str] = {}
    page_records: list[dict[str, Any]] = []
    repair_context_records: list[dict[str, Any]] = []
    for context in page_contexts:
        content, record = page_results[context.id]
        page_markdown_by_slide[context.pages[0].slide_id] = content
        page_records.append(record)
        repair_record = record.get("content_guard_repair")
        if isinstance(repair_record, dict):
            record_repair(options.content_guard, repair_record)
            if isinstance(repair_record.get("llm"), dict):
                repair_context_records.append(repair_record["llm"])
    return page_markdown_by_slide, page_records, repair_context_records


def _weave_contexts(
    weave_contexts: list[NoteContext],
    page_markdown_by_slide: dict[int, str],
    output_root: Path,
    options: "NoteOptions",
    *,
    cache: LLMCache,
    note_context: str,
    note_depth: str,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Stage 2: weave page notes of each context into one coherent chunk."""

    def process_weave(context: NoteContext) -> tuple[str, dict[str, Any]]:
        content, record = _generate_weave_context(
            context=context,
            page_markdown_by_slide=page_markdown_by_slide,
            output_root=output_root,
            cache=cache,
            options=options,
            provider=options.provider,
            model=options.model,
            base_url=options.base_url,
            note_context=note_context,
            note_depth=note_depth,
            force_refresh=_refresh_requested(options, context),
        )
        return _postprocess_llm_markdown(content, source_display=options.source_display), record

    def page_notes_fallback(context: NoteContext, exc: Exception) -> tuple[str, dict[str, Any]]:
        content = "\n\n".join(page_markdown_by_slide.get(page.slide_id, "") for page in context.pages).strip()
        weave_context = NoteContext(id=f"weave_{context.id}", kind=f"weave_{context.kind}", title=context.title, pages=context.pages)
        return content, _failed_context_record(weave_context, exc, generation_stage="weave", fallback="page_notes")

    weave_results = _run_note_contexts(
        weave_contexts, process_weave, workers=_workers(options),
        progress_callback=options.progress_callback, fallback=page_notes_fallback,
    )
    final_chunks = {context.id: weave_results[context.id][0] for context in weave_contexts}
    weave_records = [weave_results[context.id][1] for context in weave_contexts]
    return final_chunks, weave_records


def _teaching_contexts(
    weave_contexts: list[NoteContext],
    final_chunks: dict[str, str],
    options: "NoteOptions",
) -> list[NoteContext]:
    if not should_run_teaching_enrichment(options.note_profile, options.teaching_enrichment, "lecture-weave"):
        return []
    return [
        context for context in weave_contexts
        if options.teaching_enrichment == "force"
        or needs_teaching_enrichment(final_chunks.get(context.id, ""), len(context.pages))
    ]


def _enrich_teaching_contexts(
    teaching_contexts: list[NoteContext],
    final_chunks: dict[str, str],
    page_markdown_by_slide: dict[int, str],
    output_root: Path,
    options: "NoteOptions",
    *,
    cache: LLMCache,
    note_context: str,
    note_depth: str,
) -> list[dict[str, Any]]:
    """Stage 3 (optional): add examples, pitfalls and self-checks; updates ``final_chunks``."""
    if not teaching_contexts:
        return []

    def process_teaching(context: NoteContext) -> tuple[str, dict[str, Any]]:
        content, record = _generate_teaching_enrichment_context(
            context=context,
            woven_markdown=final_chunks.get(context.id, ""),
            page_markdown_by_slide=page_markdown_by_slide,
            output_root=output_root,
            cache=cache,
            options=options,
            provider=options.provider,
            model=options.model,
            base_url=options.base_url,
            note_context=note_context,
            note_depth=note_depth,
            force_refresh=_refresh_requested(options, context),
        )
        return _postprocess_llm_markdown(content, source_display=options.source_display), record

    def keep_woven_fallback(context: NoteContext, exc: Exception) -> tuple[str, dict[str, Any]]:
        teaching_context = NoteContext(id=f"teaching_{context.id}", kind=f"teaching_{context.kind}", title=context.title, pages=context.pages)
        record = _failed_context_record(teaching_context, exc, generation_stage="teaching_enrichment", fallback="woven_notes")
        return final_chunks.get(context.id, ""), record

    teaching_results = _run_note_contexts(
        teaching_contexts, process_teaching, workers=_workers(options),
        progress_callback=options.progress_callback, fallback=keep_woven_fallback,
    )
    teaching_records: list[dict[str, Any]] = []
    for context in teaching_contexts:
        content, record = teaching_results[context.id]
        final_chunks[context.id] = content
        teaching_records.append(record)
    return teaching_records
