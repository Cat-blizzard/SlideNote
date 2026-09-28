"""Note context selection: whole document, sections, or single pages."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from slidenote.models import Deck, SlidePage
from slidenote.sections import _normalize_heading_text
from slidenote.utils import context_title, looks_like_section_title_page


DOCUMENT_CONTEXT_MAX_PAGES = 12
DOCUMENT_CONTEXT_MAX_CHARS = 16_000
# Section size used when no outline or section plan gives boundaries.
FALLBACK_SECTION_PAGES = 8


@dataclass(frozen=True, slots=True)
class NoteContext:
    id: str
    kind: str
    title: str
    pages: list[SlidePage]


def _select_note_contexts(deck: Deck, requested: str, section_plan: dict[str, Any] | None = None) -> list[NoteContext]:
    resolved = _resolved_context_mode(deck, requested)
    if resolved == "document":
        return [NoteContext(id="doc", kind="document", title=Path(deck.source_path).stem, pages=list(deck.pages))]
    if resolved == "page":
        return [
            NoteContext(id=f"p{page.slide_id}", kind="page", title=page.title or f"\u7b2c {page.slide_id} \u9875", pages=[page])
            for page in deck.pages
        ]
    return _section_contexts(deck, section_plan=section_plan)


def _resolved_context_mode(deck: Deck, requested: str) -> str:
    if requested != "auto":
        return requested
    if len(deck.pages) <= DOCUMENT_CONTEXT_MAX_PAGES and _structured_char_count(deck) <= DOCUMENT_CONTEXT_MAX_CHARS:
        return "document"
    return "section"


def _structured_char_count(deck: Deck) -> int:
    total = 0
    for page in deck.pages:
        total += sum(len(block.content) for block in page.text_blocks)
        total += sum(len(cell) for table in page.tables for row in table.rows for cell in row)
        total += len(page.page_ocr_text or "") + len(page.page_visual_summary or "")
        total += sum(len(image.ocr_text or "") + len(image.visual_summary or "") for image in page.images)
    return total


def _section_contexts(deck: Deck, section_plan: dict[str, Any] | None = None) -> list[NoteContext]:
    if not deck.pages:
        return []
    if section_plan:
        planned_contexts = _section_contexts_from_plan(deck, section_plan)
        if planned_contexts:
            return planned_contexts
    boundaries = _section_boundaries(deck)
    if len(boundaries) <= 1:
        boundaries = [deck.pages[index].slide_id for index in range(0, len(deck.pages), FALLBACK_SECTION_PAGES)]
    contexts: list[NoteContext] = []
    slide_to_index = {page.slide_id: index for index, page in enumerate(deck.pages)}
    boundary_indexes = sorted({slide_to_index[slide_id] for slide_id in boundaries if slide_id in slide_to_index})
    if not boundary_indexes or boundary_indexes[0] != 0:
        boundary_indexes.insert(0, 0)
    for position, start_index in enumerate(boundary_indexes):
        end_index = boundary_indexes[position + 1] if position + 1 < len(boundary_indexes) else len(deck.pages)
        pages = deck.pages[start_index:end_index]
        if not pages:
            continue
        title = context_title(pages, position + 1)
        contexts.append(NoteContext(id=f"sec{position + 1}", kind="section", title=title, pages=pages))
    return contexts


def _section_contexts_from_plan(deck: Deck, section_plan: dict[str, Any]) -> list[NoteContext]:
    pages_by_id = {page.slide_id: page for page in deck.pages}
    contexts: list[NoteContext] = []
    sections = section_plan.get("sections")
    if not isinstance(sections, list):
        return []
    for index, section in enumerate(sections, start=1):
        if not isinstance(section, dict):
            continue
        raw_ids = section.get("slide_ids")
        if not isinstance(raw_ids, list):
            continue
        pages = [pages_by_id[slide_id] for slide_id in raw_ids if isinstance(slide_id, int) and slide_id in pages_by_id]
        if not pages:
            continue
        context_id = str(section.get("section_id") or f"sec{index}")
        title = str(section.get("title") or context_title(pages, index)).strip() or context_title(pages, index)
        contexts.append(NoteContext(id=context_id, kind="section", title=title, pages=pages))
    return contexts


def _section_boundaries(deck: Deck) -> list[int]:
    outline_titles = _outline_titles(deck)
    boundaries = [deck.pages[0].slide_id]
    for page in deck.pages[1:]:
        title = _normalize_heading_text(page.title or "")
        if not title or "\u76ee\u5f55" in title or title.lower() == "contents":
            continue
        if any(title == outline or title in outline or outline in title for outline in outline_titles):
            boundaries.append(page.slide_id)
        elif not outline_titles and looks_like_section_title_page(page):
            boundaries.append(page.slide_id)
    return sorted(set(boundaries))


def _outline_titles(deck: Deck) -> set[str]:
    titles: set[str] = set()
    for page in deck.pages:
        page_text = "\n".join(block.content for block in page.text_blocks)
        if "\u76ee\u5f55" not in page_text and "contents" not in page_text.lower():
            continue
        for line in page_text.splitlines():
            normalized = _normalize_heading_text(line)
            if not normalized or normalized.lower() in {"\u76ee\u5f55", "contents"}:
                continue
            if len(normalized) >= 4:
                titles.add(normalized)
    return titles
