from __future__ import annotations

from pathlib import Path
from typing import Any

from slidenote.figure_grounding import note_candidate_images
from slidenote.image_ranking import sorted_images_by_importance
from slidenote.models import Deck, ImageAsset, SlidePage, TableBlock, TextBlock

from .assembly import (
    NoteContext,
    _asset_display_path,
    _context_heading,
    _context_heading_title,
    _document_title,
    _ensure_sentence,
    _is_frontmatter_heading,
    _page_element_ids,
    _quote_multiline,
    _render_image,
    _section_contexts,
    _should_add_context_headings,
    _should_render_screenshot,
    _source_marker,
    _styled_block_text,
)


def _generate_notes_locally(
    deck: Deck,
    asset_map: dict[str, str] | None = None,
    source_display: str = "hidden",
    note_style: str = "article",
    screenshot_policy: str = "fallback",
    figure_placement: str = "inline",
    section_plan: dict[str, Any] | None = None,
) -> str:
    asset_map = asset_map or {}
    lines: list[str] = []
    lines.append(f"# {_document_title(deck)}")
    lines.append("")
    if note_style == "faithful":
        lines.append("> \u672c\u5730\u89c4\u5219\u8349\u7a3f\uff0c\u7528\u4e8e\u8c03\u8bd5\u89e3\u6790\u548c\u8986\u76d6\u7387\u94fe\u8def\uff1b\u6b63\u5f0f\u6539\u5199\u8bf7\u4f7f\u7528 `--use-llm`\u3002")
        lines.append("")

    contexts = _section_contexts(deck, section_plan=section_plan)
    if not contexts:
        contexts = [NoteContext(id="doc", kind="document", title=Path(deck.source_path).stem, pages=list(deck.pages))]
    add_context_headings = _should_add_context_headings(contexts)
    section_number = 1
    for context in contexts:
        if add_context_headings:
            heading_title = _context_heading_title(context, section_plan)
            if not _is_frontmatter_heading(heading_title, context):
                lines.append(_context_heading(context, heading_title, section_number))
                lines.append("")
                section_number += 1
        page_heading_level = "###" if add_context_headings else "##"
        for page in context.pages:
            lines.extend(
                _render_local_page(
                    page,
                    asset_map=asset_map,
                    source_display=source_display,
                    note_style=note_style,
                    screenshot_policy=screenshot_policy,
                    figure_placement=figure_placement,
                    heading_level=page_heading_level,
                )
            )

    if deck.warnings:
        lines.append("## \u89e3\u6790\u63d0\u9192")
        lines.append("")
        for warning in deck.warnings:
            lines.append(f"- {warning}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _render_local_page(
    page: SlidePage,
    asset_map: dict[str, str],
    source_display: str,
    note_style: str,
    screenshot_policy: str,
    figure_placement: str,
    heading_level: str,
) -> list[str]:
    lines: list[str] = []
    rendered_image_ids: set[str] = set()
    heading = page.title or f"\u7b2c {page.slide_id} \u9875"
    lines.append(f"{heading_level} \u7b2c {page.slide_id} \u9875\uff1a{heading}")
    lines.append(_source_marker(page.slide_id, _page_element_ids(page), source_display))
    lines.append("")

    if _should_render_screenshot(page, screenshot_policy):
        screenshot_path = _asset_display_path(page.page_screenshot, asset_map)
        lines.append(f"![\u7b2c {page.slide_id} \u9875\u622a\u56fe]({screenshot_path})")
        lines.append(_source_marker(page.slide_id, [f"p{page.slide_id}_screenshot"], source_display))
        lines.append("")

    page_visual_lines = _render_page_visual_context(page, source_display=source_display)
    if page_visual_lines:
        lines.extend(page_visual_lines)
        lines.append("")

    if page.text_blocks and figure_placement == "page-end":
        lines.extend(_render_text_blocks(page, source_display=source_display, note_style=note_style))
        lines.append("")
    elif page.text_blocks:
        for block in page.text_blocks:
            lines.extend(_render_text_block(page, block, source_display=source_display, note_style=note_style))
            lines.extend(
                _render_images_for_anchor(
                    page,
                    block.id,
                    rendered_image_ids=rendered_image_ids,
                    asset_map=asset_map,
                    source_display=source_display,
                )
            )
            lines.append("")

    for table in page.tables:
        lines.extend(_render_table(page, table, source_display=source_display))
        if figure_placement == "inline":
            lines.extend(
                _render_images_for_anchor(
                    page,
                    table.id,
                    rendered_image_ids=rendered_image_ids,
                    asset_map=asset_map,
                    source_display=source_display,
                )
            )
        lines.append("")

    images_to_render = sorted_images_by_importance(page.images) if figure_placement == "page-end" else note_candidate_images(page)
    for image in images_to_render:
        if image.ignored or image.id in rendered_image_ids:
            continue
        if figure_placement == "inline" and image.anchor_element_ids:
            continue
        lines.extend(_render_image(page, image, asset_map=asset_map, source_display=source_display))
        rendered_image_ids.add(image.id)
        lines.append("")

    visible_images = [image for image in page.images if not image.ignored]
    if not page.text_blocks and not page.tables and not visible_images and not page_visual_lines:
        lines.append(f"PPT \u7b2c {page.slide_id} \u9875\u6ca1\u6709\u89e3\u6790\u5230\u53ef\u5199\u5165\u7684\u6587\u672c\u3001\u8868\u683c\u6216\u5d4c\u5165\u56fe\u7247\u3002")
        lines.append("")

    if page.notes:
        lines.append(f"\u8bb2\u8005\u5907\u6ce8\uff1a{page.notes}")
        lines.append(_source_marker(page.slide_id, [f"p{page.slide_id}_notes"], source_display))
        lines.append("")

    for warning in page.warnings:
        lines.append(f"> \u63d0\u9192\uff1a{warning}")
        lines.append("")
    return lines


def _render_text_blocks(page: SlidePage, source_display: str, note_style: str) -> list[str]:
    lines: list[str] = []
    for block in page.text_blocks:
        lines.extend(_render_text_block(page, block, source_display=source_display, note_style=note_style))
    return lines


def _render_text_block(page: SlidePage, block: TextBlock, source_display: str, note_style: str) -> list[str]:
    lines: list[str] = []
    if block.type == "title":
        content = _styled_block_text(block) or _plain_block_text(block)
        lines.append(f"\u672c\u9875\u4e3b\u9898\u662f\u201c{content}\u201d\u3002")
    elif block.type == "bullet":
        content = _styled_block_text(block) or _rewrite_block(block)
        if note_style == "article":
            lines.append(content)
        else:
            lines.append(f"\u672c\u9875\u5217\u51fa\u4e86\u4ee5\u4e0b\u8981\u70b9\uff1a{content}")
    else:
        content = _styled_block_text(block) or _rewrite_block(block)
        lines.append(content)
    lines.append(_source_marker(page.slide_id, [block.id], source_display))
    lines.append("")
    return lines


def _render_images_for_anchor(
    page: SlidePage,
    anchor_id: str,
    rendered_image_ids: set[str],
    asset_map: dict[str, str],
    source_display: str,
) -> list[str]:
    lines: list[str] = []
    anchored = [
        image
        for image in note_candidate_images(page)
        if image.id not in rendered_image_ids and anchor_id in (image.anchor_element_ids or [])
    ]
    for image in sorted_images_by_importance(anchored):
        lines.extend(_render_image(page, image, asset_map=asset_map, source_display=source_display))
        lines.append("")
        rendered_image_ids.add(image.id)
    return lines


def _render_page_visual_context(page: SlidePage, source_display: str) -> list[str]:
    lines: list[str] = []
    if page.page_visual_summary:
        lines.append(f"\u9875\u622a\u56fe\u89c6\u89c9\u89e3\u6790\uff1a{_ensure_sentence(page.page_visual_summary)}")
        lines.append(_source_marker(page.slide_id, [f"p{page.slide_id}_screenshot"], source_display))
    if page.page_ocr_text:
        if lines:
            lines.append("")
        lines.append("\u9875\u622a\u56fe OCR \u6587\u5b57\uff1a")
        lines.extend(_quote_multiline(page.page_ocr_text))
        lines.append(_source_marker(page.slide_id, [f"p{page.slide_id}_ocr"], source_display))
    return lines


def _rewrite_block(block: TextBlock) -> str:
    text = _plain_block_text(block)
    if block.type == "bullet":
        lines = [line.strip(" \t-\u2022*\u00b7") for line in block.content.splitlines() if line.strip()]
        if len(lines) > 1:
            text = "\uff1b".join(lines)
    if text and text[-1] not in "\u3002.!!\uff1f?\uff1a:":
        text += "\u3002"
    return text


def _plain_block_text(block: TextBlock) -> str:
    lines = [line.strip(" \t-\u2022*\u00b7") for line in block.content.splitlines() if line.strip()]
    return " ".join(lines).strip()


def _render_table(page: SlidePage, table: TableBlock, source_display: str) -> list[str]:
    table_lead = table.table_conclusion or table.table_summary or f"\u4e0b\u8868\u6574\u7406\u4e86\u7b2c {page.slide_id} \u9875\u4e2d\u7684\u8868\u683c\u5185\u5bb9\u3002"
    prefix = "\u8868\u683c\u7ed3\u8bba" if table.table_conclusion else "\u8868\u683c\u8bf4\u660e"
    lines = [f"{prefix}\uff1a{_ensure_sentence(table_lead)}", _source_marker(page.slide_id, [table.id], source_display), ""]
    if table.key_rows:
        lines.append("\u5173\u952e\u884c\uff1a")
        for key_row in table.key_rows[:3]:
            lines.append(f"- {_format_key_row(key_row)}")
        lines.append("")
    if not table.rows:
        return lines
    width = max(len(row) for row in table.rows)
    padded = [row + [""] * (width - len(row)) for row in table.rows]
    header = padded[0]
    lines.append("| " + " | ".join(_escape_md(cell) or " " for cell in header) + " |")
    lines.append("| " + " | ".join("---" for _ in header) + " |")
    for row in padded[1:] or [[""] * width]:
        lines.append("| " + " | ".join(_escape_md(cell) or " " for cell in row) + " |")
    return lines


def _format_key_row(row: dict[str, Any]) -> str:
    label = str(row.get("label") or f"\u7b2c {row.get('row_index', '?')} \u884c")
    values = row.get("values")
    if not isinstance(values, list):
        return label
    parts: list[str] = []
    for value in values[:4]:
        if not isinstance(value, dict):
            continue
        column = str(value.get("column") or "").strip()
        cell = str(value.get("value") or "").strip()
        if not cell or cell == label:
            continue
        parts.append(f"{column}={cell}" if column else cell)
    if not parts:
        return label
    separator = "\uff1b"
    return f"{label}\uff1a{separator.join(parts)}"


def _escape_md(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")
