from __future__ import annotations

from pathlib import Path
from typing import Any

from slidenote.content_guard import missing_required_items, record_repair
from slidenote.coverage import analyze_coverage
from slidenote.llm_cache import LLMCache
from slidenote.models import Deck

from .assets import _repair_markdown_image_links
from .compose import _compose_final_markdown
from .contexts import NoteContext
from .document_frame import ensure_lecture_document_frame
from .figure_blocks import _ensure_grounded_figures
from .repair import _repair_note_structure_once, _repair_required_markdown_once


def _finalize_notes_markdown(
    deck: Deck,
    contexts: list[NoteContext],
    final_chunks: dict[str, str],
    *,
    output_root: Path,
    cache: LLMCache,
    options: "NoteOptions",
    asset_map: dict[str, str],
    stage: str,
) -> tuple[str, list[dict[str, Any]]]:
    """Compose the final document and repair contexts that miss required items.

    Repairs run per context (never on the whole deck) so the rewrite fits the
    output-token budget; accepted repairs replace the chunk in ``final_chunks``.
    Returns the final markdown and the LLM usage records of repair calls.

    For the lecture-weave strategy the deterministic document frame (objectives,
    summary, self-test) is appended after composition, and a whole-document
    structure repair runs only when the frame could not make the lecture
    structure contract pass.
    """

    def compose() -> str:
        markdown = _compose_final_markdown(
            deck=deck,
            contexts=contexts,
            final_chunks=final_chunks,
            section_plan=options.section_plan,
            source_display=options.source_display,
        )
        markdown = _repair_markdown_image_links(markdown, output_root, asset_map)
        return _ensure_grounded_figures(markdown, deck, asset_map, options.source_display, options.figure_placement)

    markdown = compose()
    repair_usage: list[dict[str, Any]] = []
    content_guard = options.content_guard
    if content_guard:
        coverage = analyze_coverage(deck, markdown, content_guard=content_guard)
        missing_slide_ids = {item.get("slide_id") for item in missing_required_items(content_guard, coverage)}
        if missing_slide_ids:
            refresh_ids = options.refresh_slide_ids or set()
            changed = False
            for context in contexts:
                slide_ids = {page.slide_id for page in context.pages}
                if not slide_ids & missing_slide_ids or not final_chunks.get(context.id, "").strip():
                    continue
                repaired, record = _repair_required_markdown_once(
                    deck=Deck(source_path=deck.source_path, source_type=deck.source_type, pages=list(context.pages)),
                    context=context,
                    markdown=final_chunks[context.id],
                    output_root=output_root,
                    cache=cache,
                    options=options,
                    stage=stage,
                    force_refresh=bool(refresh_ids & slide_ids),
                )
                if record is None:
                    continue
                record_repair(content_guard, record)
                if isinstance(record.get("llm"), dict):
                    repair_usage.append(record["llm"])
                if record.get("accepted"):
                    final_chunks[context.id] = repaired
                    changed = True
            if changed:
                markdown = compose()

    if options.note_strategy != "lecture-weave":
        return markdown, repair_usage

    markdown = ensure_lecture_document_frame(markdown, options.deck_brief, options.note_language)
    if not options.use_llm:
        return markdown, repair_usage
    repaired, record = _repair_note_structure_once(
        deck=deck,
        context=NoteContext(id="final", kind="final", title="final", pages=deck.pages),
        markdown=markdown,
        output_root=output_root,
        cache=cache,
        options=options,
    )
    if record is not None:
        if isinstance(record.get("llm"), dict):
            repair_usage.append(record["llm"])
        if record.get("accepted"):
            markdown = _repair_markdown_image_links(repaired, output_root, asset_map)
            markdown = _ensure_grounded_figures(
                markdown,
                deck,
                asset_map,
                options.source_display,
                options.figure_placement,
            )
    return markdown, repair_usage
