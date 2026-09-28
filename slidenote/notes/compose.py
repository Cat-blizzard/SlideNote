"""Compose per-context Markdown into the final notes document."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from slidenote.models import Deck
from .contexts import NoteContext
from .frontmatter import (
    _clean_heading_text,
    _frontmatter_source_markers,
    _is_frontmatter_heading,
    _is_generic_heading_text,
    _leading_frontmatter_slide_ids,
    _looks_like_frontmatter_text,
    _normalize_title_key,
)
from .sources import _collapse_blank_lines, _source_slide_ids


def _compose_final_markdown(
    deck: Deck,
    contexts: list[NoteContext],
    final_chunks: dict[str, str],
    section_plan: dict[str, Any] | None,
    source_display: str,
) -> str:
    del source_display
    lines = [f"# {_document_title(deck)}", ""]
    add_context_headings = _should_add_context_headings(contexts)
    leading_frontmatter_slide_ids = _leading_frontmatter_slide_ids(deck.pages)
    section_number = 1
    for context in contexts:
        content = final_chunks.get(context.id, "").strip()
        if not content:
            continue
        if add_context_headings:
            heading_title = _context_heading_title(context, section_plan)
            if _is_frontmatter_heading(heading_title, context) and len(contexts) > 1:
                content = _frontmatter_source_markers(context.pages)
            else:
                lines.append(_context_heading(context, heading_title, section_number))
                lines.append("")
                section_number += 1
                content = _prepare_context_chunk(content, heading_title, add_outer_heading=True)
                content = _strip_leading_frontmatter_content(content, context, leading_frontmatter_slide_ids)
                content = _number_subsection_headings(content)
        else:
            content = _prepare_context_chunk(content, context.title, add_outer_heading=False)
        if content:
            lines.append(content)
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _document_title(deck: Deck) -> str:
    stem = Path(deck.source_path).stem
    for page in deck.pages[:3]:
        title = (page.title or "").strip()
        if title and not _is_generic_heading_text(title):
            return f"{stem}\uff1a{title}" if title != stem else stem
    return stem


def _should_add_context_headings(contexts: list[NoteContext]) -> bool:
    if not contexts:
        return False
    if len(contexts) > 1:
        return True
    return contexts[0].kind == "section"


def _context_heading(context: NoteContext, title: str, section_number: int) -> str:
    if context.kind == "page":
        slide_id = context.pages[0].slide_id if context.pages else section_number
        return f"## \u7b2c {slide_id} \u9875\uff1a{title}"
    return f"## {_chinese_ordinal(section_number)}\u3001{title}"


def _context_heading_title(context: NoteContext, section_plan: dict[str, Any] | None) -> str:
    planned_title = _planned_context_title(context, section_plan)
    title = planned_title or context.title or ""
    title = _clean_heading_text(title)
    if title:
        return title
    if context.kind == "page" and context.pages:
        return context.pages[0].title or f"\u7b2c {context.pages[0].slide_id} \u9875"
    return "\u672c\u8282\u5185\u5bb9"


def _planned_context_title(context: NoteContext, section_plan: dict[str, Any] | None) -> str | None:
    if not section_plan:
        return None
    sections = section_plan.get("sections")
    if not isinstance(sections, list):
        return None
    slide_ids = [page.slide_id for page in context.pages]
    for section in sections:
        if not isinstance(section, dict):
            continue
        if section.get("section_id") == context.id or section.get("slide_ids") == slide_ids:
            title = str(section.get("title") or "").strip()
            return title or None
    return None


def _prepare_context_chunk(markdown: str, section_title: str, add_outer_heading: bool) -> str:
    text = _remove_generation_info_sections(markdown)
    text = _drop_redundant_leading_headings(text, section_title) if add_outer_heading else text
    text = _demote_chunk_headings(text, minimum_level=3 if add_outer_heading else 2)
    text = _collapse_blank_lines(text.splitlines())
    return text.strip()


def _drop_redundant_leading_headings(markdown: str, section_title: str) -> str:
    lines = markdown.splitlines()
    while True:
        first_index = next((index for index, line in enumerate(lines) if line.strip()), None)
        if first_index is None:
            return ""
        match = re.match(r"^(#{1,6})\s+(.*)$", lines[first_index].strip())
        if not match:
            return "\n".join(lines).strip()
        heading_text = _clean_heading_text(match.group(2))
        if not _is_redundant_context_heading(heading_text, section_title):
            return "\n".join(lines).strip()
        del lines[first_index]
        while first_index < len(lines) and not lines[first_index].strip():
            del lines[first_index]


def _is_redundant_context_heading(heading_text: str, section_title: str) -> bool:
    heading_norm = _normalize_title_key(heading_text)
    context_norm = _normalize_title_key(section_title)
    if not heading_norm:
        return True
    if _is_generic_heading_text(heading_text):
        return True
    return bool(context_norm and (heading_norm == context_norm or heading_norm in context_norm or context_norm in heading_norm))


def _demote_chunk_headings(markdown: str, minimum_level: int) -> str:
    lines: list[str] = []
    for line in markdown.splitlines():
        match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if not match:
            lines.append(line)
            continue
        text = _clean_heading_text(match.group(2))
        if not text or _is_generic_heading_text(text):
            continue
        level = max(minimum_level, len(match.group(1)))
        lines.append("#" * min(level, 6) + " " + text)
    return "\n".join(lines)


def _strip_leading_frontmatter_content(markdown: str, context: NoteContext, leading_frontmatter_slide_ids: set[int]) -> str:
    frontmatter_slide_ids = {page.slide_id for page in context.pages if page.slide_id in leading_frontmatter_slide_ids}
    if not frontmatter_slide_ids:
        return markdown
    blocks = re.split(r"\n\s*\n", markdown.strip())
    kept: list[str] = []
    dropping = True
    for block in blocks:
        stripped = block.strip()
        if not stripped:
            continue
        if dropping and _is_horizontal_rule(stripped):
            continue
        if dropping and _is_droppable_frontmatter_block(stripped, frontmatter_slide_ids):
            continue
        dropping = False
        kept.append(stripped)
    marker = _frontmatter_source_markers([page for page in context.pages if page.slide_id in frontmatter_slide_ids])
    if not marker:
        return "\n\n".join(kept).strip()
    body = "\n\n".join(kept).strip()
    return f"{marker}\n\n{body}".strip() if body else marker


def _is_droppable_frontmatter_block(block: str, frontmatter_slide_ids: set[int]) -> bool:
    match = re.match(r"^(#{1,6})\s+(.*)$", block)
    if match:
        return _is_generic_heading_text(match.group(2)) or _looks_like_frontmatter_text(match.group(2))
    slide_ids = _source_slide_ids(block)
    if slide_ids and slide_ids.issubset(frontmatter_slide_ids):
        return True
    return _looks_like_frontmatter_text(block)


def _is_horizontal_rule(block: str) -> bool:
    return bool(re.fullmatch(r"[-*_]{3,}", block.strip()))


def _number_subsection_headings(markdown: str) -> str:
    counters: list[int] = []
    base_level: int | None = None
    lines: list[str] = []
    for line in markdown.splitlines():
        match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if not match:
            lines.append(line)
            continue
        level = len(match.group(1))
        title = _strip_heading_number(match.group(2).strip())
        if level < 3:
            counters = []
            base_level = None
            lines.append(line)
            continue
        if base_level is None or level < base_level:
            base_level = level
            counters = []
        depth = max(0, level - base_level)
        while len(counters) <= depth:
            counters.append(0)
        counters = counters[: depth + 1]
        counters[depth] += 1
        prefix = ".".join(str(value) for value in counters)
        separator = ". " if len(counters) == 1 else " "
        lines.append(f"{match.group(1)} {prefix}{separator}{title}")
    return "\n".join(lines)


def _strip_heading_number(title: str) -> str:
    return re.sub(
        r"^\s*(?:\d+(?:\.\d+)*\.?|[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341]+[\u3001.])\s*",
        "",
        title,
    ).strip()


def _remove_generation_info_sections(markdown: str) -> str:
    lines = markdown.splitlines()
    kept: list[str] = []
    skipping = False
    skip_level = 0
    for line in lines:
        match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if match:
            level = len(match.group(1))
            heading = _normalize_title_key(match.group(2))
            if heading in {"\u751f\u6210\u4fe1\u606f", "generationinfo", "generationmetadata"}:
                skipping = True
                skip_level = level
                continue
            if skipping and level <= skip_level:
                skipping = False
        if not skipping:
            kept.append(line)
    return "\n".join(kept)


def _chinese_ordinal(index: int) -> str:
    numerals = ["\u4e00", "\u4e8c", "\u4e09", "\u56db", "\u4e94", "\u516d", "\u4e03", "\u516b", "\u4e5d", "\u5341"]
    if 1 <= index <= 10:
        return numerals[index - 1]
    if 11 <= index <= 19:
        return "\u5341" + numerals[index - 11]
    if index == 20:
        return "\u4e8c\u5341"
    return str(index)
