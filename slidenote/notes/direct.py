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
    options: "NoteOptions",
    *,
    note_depth: str,
    asset_map: dict[str, str],
) -> "NoteGenerationResult":  # string annotation to avoid circular import
    from . import NoteGenerationResult

    runtime = resolve_provider_runtime(options.provider, model=options.model, base_url=options.base_url)
    resolved_provider = str(runtime["provider"])
    resolved_model = str(runtime["model"])
    resolved_base_url = runtime["base_url"]
    supports_image_input = bool(runtime["supports_image_input"])
    resolved_cache_dir = (options.cache_dir or (output_root / ".cache" / "llm")).resolve()
    cache = LLMCache(resolved_cache_dir, mode=options.cache_mode)
    if options.note_strategy == "lecture-weave":
        return _generate_notes_with_lecture_weave(
            deck=deck,
            output_root=output_root,
            options=options,
            note_depth=note_depth,
            asset_map=asset_map,
            resolved_provider=resolved_provider,
            resolved_model=resolved_model,
            resolved_base_url=resolved_base_url,
            resolved_cache_dir=resolved_cache_dir,
            cache=cache,
            supports_image_input=supports_image_input,
        )

    contexts = _select_note_contexts(deck, options.note_context, section_plan=options.section_plan)
    resolved_note_context = _resolved_context_mode(deck, options.note_context)
    refresh_ids = options.refresh_slide_ids or set()
    workers = max(1, int(options.concurrency or 1))
    context_results: dict[str, tuple[str, dict[str, Any]]] = {}

    def process(context: NoteContext) -> tuple[str, str, dict[str, Any]]:
        content, context_record = _generate_llm_context(
            context=context,
            output_root=output_root,
            cache=cache,
            options=options,
            provider=resolved_provider,
            model=resolved_model,
            base_url=resolved_base_url,
            supports_image_input=supports_image_input,
            force_refresh=bool(refresh_ids.intersection({page.slide_id for page in context.pages})),
            asset_map=asset_map,
            note_context=resolved_note_context,
            note_depth=note_depth,
            source_type=deck.source_type,
        )
        content = _postprocess_llm_markdown(content, source_display=options.source_display)
        return context.id, content, context_record

    if workers == 1:
        for context in contexts:
            context_id, content, context_record = process(context)
            context_results[context_id] = (content, context_record)
            if options.progress_callback:
                options.progress_callback(context_record)
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(process, context): context for context in contexts}
            for future in as_completed(futures):
                context_id, content, context_record = future.result()
                context_results[context_id] = (content, context_record)
                if options.progress_callback:
                    options.progress_callback(context_record)

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
        section_plan=options.section_plan,
        source_display=options.source_display,
    )
    markdown = _repair_markdown_image_links(markdown, output_root, asset_map)
    markdown = _ensure_grounded_figures(markdown, deck, asset_map, options.source_display, options.figure_placement)
    repair_context_records: list[dict[str, Any]] = []
    markdown, repair_record = _repair_required_markdown_once(
        deck=deck,
        context=NoteContext(id="final", kind="final", title="final", pages=deck.pages),
        markdown=markdown,
        output_root=output_root,
        cache=cache,
        options=options,
        stage="final",
    )
    if repair_record is not None:
        record_repair(options.content_guard, repair_record)
        if isinstance(repair_record.get("llm"), dict):
            repair_context_records.append(repair_record["llm"])
    markdown = _repair_markdown_image_links(markdown, output_root, asset_map)
    markdown = _ensure_grounded_figures(markdown, deck, asset_map, options.source_display, options.figure_placement)
    markdown = _repair_markdown_image_links(markdown, output_root, asset_map)
    usage_report = _build_usage_report(
        deck=deck,
        output_root=output_root,
        options=options,
        contexts=usage_contexts + repair_context_records,
        note_strategy=options.note_strategy,
        repair_contexts=repair_context_records,
    )
    return NoteGenerationResult(markdown=markdown, llm_usage=usage_report)
