from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from slidenote.llm_cache import utc_now_iso
from slidenote.models import Deck, ImageAsset, SlidePage, TableBlock, TextBlock


ELEMENT_PATTERN = re.compile(r"\bs\d+_(?:t|tbl|img)\d+\b")


def build_source_map(deck: Deck, notes_markdown: str, output_root: Path) -> dict[str, Any]:
    blocks = _note_blocks(notes_markdown)
    element_index = _element_index(deck)
    note_blocks: list[dict[str, Any]] = []
    for block in blocks:
        element_ids = sorted(set(ELEMENT_PATTERN.findall(block["text"])))
        refs = [element_index[element_id] for element_id in element_ids if element_id in element_index]
        note_blocks.append(
            {
                "note_block_id": block["id"],
                "kind": block["kind"],
                "heading": block["heading"],
                "line_start": block["line_start"],
                "line_end": block["line_end"],
                "element_ids": element_ids,
                "source_refs": refs,
                "ai_supplement": "AI 补充说明" in block["text"],
            }
        )

    return {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "display_modes": ["strict", "compact", "hidden"],
        "default_display_mode": "compact",
        "pages": [_page_sources(page) for page in deck.pages],
        "note_blocks": note_blocks,
        "artifacts": {
            "notes": "notes.md",
            "content": "content.json",
        },
    }


def _note_blocks(markdown: str) -> list[dict[str, Any]]:
    lines = markdown.splitlines()
    blocks: list[dict[str, Any]] = []
    current: list[str] = []
    start_line = 1
    heading = ""
    block_index = 1

    def flush(end_line: int) -> None:
        nonlocal current, start_line, block_index, heading
        text = "\n".join(current).strip()
        if not text:
            current = []
            start_line = end_line + 1
            return
        kind = "heading" if text.startswith("#") else "paragraph"
        if text.startswith("#"):
            heading = text.lstrip("#").strip()
        blocks.append(
            {
                "id": f"note_block_{block_index}",
                "kind": kind,
                "heading": heading,
                "line_start": start_line,
                "line_end": end_line,
                "text": text,
            }
        )
        block_index += 1
        current = []
        start_line = end_line + 1

    for index, line in enumerate(lines, start=1):
        if not line.strip():
            flush(index - 1)
            continue
        if not current:
            start_line = index
        current.append(line)
    flush(len(lines))
    return blocks


def _element_index(deck: Deck) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for page in deck.pages:
        for block in page.text_blocks:
            index[block.id] = _text_ref(deck, page, block)
        for table in page.tables:
            index[table.id] = _table_ref(deck, page, table)
        for image in page.images:
            if not image.ignored:
                index[image.id] = _image_ref(deck, page, image)
    return index


def _page_sources(page: SlidePage) -> dict[str, Any]:
    return {
        "slide_id": page.slide_id,
        "title": page.title,
        "page_screenshot": page.page_screenshot,
        "page_ocr_status": page.page_ocr_status,
        "page_visual_status": page.page_visual_status,
        "text_blocks": [block.id for block in page.text_blocks],
        "tables": [table.id for table in page.tables],
        "images": [
            {
                "id": image.id,
                "path": image.path,
                "role": image.role,
                "ignored": image.ignored,
                "ignore_reason": image.ignore_reason,
            }
            for image in page.images
        ],
    }


def _text_ref(deck: Deck, page: SlidePage, block: TextBlock) -> dict[str, Any]:
    return {
        "type": "text",
        "source_path": deck.source_path,
        "slide_id": page.slide_id,
        "element_id": block.id,
        "element_type": block.type,
        "preview": _preview(block.content),
    }


def _table_ref(deck: Deck, page: SlidePage, table: TableBlock) -> dict[str, Any]:
    preview = " / ".join(" | ".join(row) for row in table.rows[:2])
    return {
        "type": "table",
        "source_path": deck.source_path,
        "slide_id": page.slide_id,
        "element_id": table.id,
        "preview": _preview(preview),
    }


def _image_ref(deck: Deck, page: SlidePage, image: ImageAsset) -> dict[str, Any]:
    return {
        "type": "image",
        "source_path": deck.source_path,
        "slide_id": page.slide_id,
        "element_id": image.id,
        "path": image.path,
        "role": image.role,
        "width": image.width,
        "height": image.height,
        "preview": image.caption or image.path,
    }


def _preview(text: str, limit: int = 160) -> str:
    value = " ".join(text.split())
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"
