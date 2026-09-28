from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from slidenote.llm import resolve_provider_runtime
from slidenote.llm_cache import LLMCache
from slidenote.models import Deck

from .contexts import NoteContext, _resolved_context_mode, _select_note_contexts
from .postprocess import _postprocess_llm_markdown
from .context_runner import _context_failure_warnings, _failed_context_record, _run_note_contexts
from .finalize import _finalize_notes_markdown
from .lecture_weave import _generate_notes_with_lecture_weave
from .llm_calls import _generate_llm_context
from .local import _render_local_context
from .usage import _build_usage_report
from .versions import NOTE_PROMPT_VERSION


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
    supports_image_input = bool(runtime["supports_image_input"])
    resolved_cache_dir = (options.cache_dir or (output_root / ".cache" / "llm")).resolve()
    # Resolve provider runtime defaults once so calls, cache keys and reports
    # all use the same canonical provider/model/base URL/cache directory.
    options = replace(
        options,
        provider=str(runtime["provider"]),
        model=str(runtime["model"]),
        base_url=runtime["base_url"],
        cache_dir=resolved_cache_dir,
    )
    cache = LLMCache(resolved_cache_dir, mode=options.cache_mode)
    if options.note_strategy == "lecture-weave":
        return _generate_notes_with_lecture_weave(
            deck=deck,
            output_root=output_root,
            options=options,
            note_depth=note_depth,
            asset_map=asset_map,
            cache=cache,
            supports_image_input=supports_image_input,
        )

    contexts = _select_note_contexts(deck, options.note_context, section_plan=options.section_plan)
    resolved_note_context = _resolved_context_mode(deck, options.note_context)
    refresh_ids = options.refresh_slide_ids or set()

    def process(context: NoteContext) -> tuple[str, dict[str, Any]]:
        content, context_record = _generate_llm_context(
            context=context,
            output_root=output_root,
            cache=cache,
            options=options,
            provider=options.provider,
            model=options.model,
            base_url=options.base_url,
            supports_image_input=supports_image_input,
            force_refresh=bool(refresh_ids.intersection({page.slide_id for page in context.pages})),
            asset_map=asset_map,
            note_context=resolved_note_context,
            note_depth=note_depth,
            source_type=deck.source_type,
        )
        return _postprocess_llm_markdown(content, source_display=options.source_display), context_record

    def local_fallback(context: NoteContext, exc: Exception) -> tuple[str, dict[str, Any]]:
        content = _render_local_context(
            context,
            asset_map=asset_map,
            source_display=options.source_display,
            note_style=options.note_style,
            screenshot_policy=options.screenshot_policy,
            figure_placement=options.figure_placement,
        )
        return content, _failed_context_record(context, exc, generation_stage="note_context", fallback="local")

    context_results = _run_note_contexts(
        contexts,
        process,
        workers=max(1, int(options.concurrency or 1)),
        progress_callback=options.progress_callback,
        fallback=local_fallback,
    )
    usage_contexts = [context_results[context.id][1] for context in contexts]
    final_chunks = {context.id: context_results[context.id][0] for context in contexts}

    markdown, repair_context_records = _finalize_notes_markdown(
        deck,
        contexts,
        final_chunks,
        output_root=output_root,
        cache=cache,
        options=options,
        asset_map=asset_map,
        stage="final",
    )
    warnings = _context_failure_warnings(usage_contexts)
    usage_report = _build_usage_report(
        deck=deck,
        output_root=output_root,
        options=options,
        contexts=usage_contexts + repair_context_records,
        note_strategy=options.note_strategy,
        prompt_version=NOTE_PROMPT_VERSION,
        repair_contexts=repair_context_records,
        warnings=warnings,
    )
    return NoteGenerationResult(markdown=markdown, llm_usage=usage_report, generation_warnings=warnings)
