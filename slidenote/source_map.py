from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from slidenote.ir import build_page_ir, element_index_from_ir
from slidenote.llm_cache import utc_now_iso
from slidenote.models import Deck, ImageAsset, SlidePage, TableBlock, TextBlock
from slidenote.table_understanding import table_preview


ELEMENT_PATTERN = re.compile(r"\bs\d+_(?:t|tbl|img|fig)\d+\b")


def build_source_map(deck: Deck, notes_markdown: str, output_root: Path) -> dict[str, Any]:
    blocks = _note_blocks(notes_markdown)
    element_index = _element_index(deck)
    image_path_index = _image_path_index(deck)
    note_blocks: list[dict[str, Any]] = []
    used_block_ids: dict[str, int] = {}
    for ordinal, block in enumerate(blocks, start=1):
        image_targets = _image_targets(block["text"])
        element_ids = set(ELEMENT_PATTERN.findall(block["text"]))
        for target in image_targets:
            element_ids.update(_image_ids_for_target(target, image_path_index))
        element_ids = sorted(element_ids)
        refs = [element_index[element_id] for element_id in element_ids if element_id in element_index]
        note_block_id = _stable_note_block_id(block, element_ids, used_block_ids)
        note_blocks.append(
            {
                "note_block_id": note_block_id,
                "legacy_note_block_id": f"note_block_{ordinal}",
                "ordinal": ordinal,
                "kind": block["kind"],
                "heading": block["heading"],
                "heading_level": block["heading_level"],
                "heading_path": block["heading_path"],
                "line_start": block["line_start"],
                "line_end": block["line_end"],
                "element_ids": element_ids,
                "primary_element_id": element_ids[0] if element_ids else None,
                "source_refs": refs,
                "image_targets": image_targets,
                "text_preview": _preview(_visible_text(block["text"])),
                "ai_supplement": "AI 补充说明" in block["text"],
            }
        )

    return {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "display_modes": ["hidden", "footnote", "inline"],
        "default_display_mode": "hidden",
        "pages": [_page_sources(page) for page in deck.pages],
        "note_blocks": note_blocks,
        "artifacts": {
            "notes": "notes.md",
            "content": "content.json",
            "note_assets": "notes.assets",
        },
    }


def _note_blocks(markdown: str) -> list[dict[str, Any]]:
    lines = markdown.splitlines()
    blocks: list[dict[str, Any]] = []
    current: list[str] = []
    start_line = 1
    heading_stack: list[tuple[int, str]] = []

    def flush(end_line: int) -> None:
        nonlocal current, start_line, heading_stack
        text = "\n".join(current).strip()
        if not text:
            current = []
            start_line = end_line + 1
            return
        kind = _block_kind(text)
        heading_level = None
        if kind == "heading":
            heading_level, heading = _parse_heading(text)
            heading_stack = [(level, title) for level, title in heading_stack if level < heading_level]
            heading_stack.append((heading_level, heading))
        else:
            heading = heading_stack[-1][1] if heading_stack else ""
        blocks.append(
            {
                "kind": kind,
                "heading": heading,
                "heading_level": heading_level,
                "heading_path": [title for _, title in heading_stack],
                "line_start": start_line,
                "line_end": end_line,
                "text": text,
            }
        )
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


def _block_kind(text: str) -> str:
    stripped = text.strip()
    if _parse_heading(stripped)[0]:
        return "heading"
    if _is_marker_only(stripped):
        return "source_marker"
    if _image_targets(stripped):
        visible = _strip_html_comments(stripped).strip()
        if re.fullmatch(r"!\[[^\]]*]\([^)]+\)", visible):
            return "image"
    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    if len(lines) >= 2 and lines[0].startswith("|") and re.fullmatch(r"\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?", lines[1]):
        return "table"
    if all(line.startswith(("- ", "* ", "+ ")) or re.match(r"\d+[.)]\s+", line) for line in lines):
        return "list"
    return "paragraph"


def _parse_heading(text: str) -> tuple[int | None, str]:
    first_line = text.strip().splitlines()[0] if text.strip() else ""
    match = re.match(r"^(#{1,6})\s+(.*)$", _strip_html_comments(first_line).strip())
    if not match:
        return None, ""
    heading = re.sub(r"\s+", " ", match.group(2).strip(" #")).strip()
    return len(match.group(1)), heading


def _is_marker_only(text: str) -> bool:
    return bool(re.fullmatch(r"(?:<!--.*?-->\s*)+", text.strip(), flags=re.DOTALL))


def _image_targets(text: str) -> list[str]:
    targets: list[str] = []
    for target in re.findall(r"!\[[^\]]*]\(([^)]+)\)", text):
        cleaned = target.strip().strip("<>")
        if cleaned:
            targets.append(cleaned)
    return targets


def _image_path_index(deck: Deck) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    for page in deck.pages:
        for image in page.images:
            variants = {
                image.path,
                Path(image.path).as_posix(),
                Path(image.path).name,
            }
            for variant in variants:
                if not variant:
                    continue
                index.setdefault(variant.replace("\\", "/"), []).append(image.id)
    return index


def _image_ids_for_target(target: str, image_path_index: dict[str, list[str]]) -> set[str]:
    normalized = target.replace("\\", "/")
    target_name = Path(normalized).name
    ids: set[str] = set()
    for path, image_ids in image_path_index.items():
        if normalized == path or normalized.endswith("/" + path) or path.endswith("/" + normalized) or target_name == Path(path).name:
            ids.update(image_ids)
    return ids


def _stable_note_block_id(block: dict[str, Any], element_ids: list[str], used_block_ids: dict[str, int]) -> str:
    kind = str(block.get("kind") or "block")
    basis = "\n".join(
        [
            kind,
            " / ".join(str(part) for part in block.get("heading_path") or []),
            ",".join(element_ids),
            _visible_text(str(block.get("text") or "")),
        ]
    )
    digest = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]
    prefix = {"heading": "h", "paragraph": "p", "list": "list", "table": "tbl", "image": "img", "source_marker": "src"}.get(kind, "blk")
    base = f"nb_{prefix}_{digest}"
    count = used_block_ids.get(base, 0) + 1
    used_block_ids[base] = count
    return base if count == 1 else f"{base}_{count}"


def _visible_text(text: str) -> str:
    value = _strip_html_comments(text)
    value = re.sub(r"!\[([^\]]*)]\([^)]+\)", r"\1", value)
    return re.sub(r"\s+", " ", value).strip()


def _strip_html_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)


def _element_index(deck: Deck) -> dict[str, dict[str, Any]]:
    return element_index_from_ir(deck)


def _page_sources(page: SlidePage) -> dict[str, Any]:
    return {
        "slide_id": page.slide_id,
        "title": page.title,
        "page_screenshot": page.page_screenshot,
        "page_width": page.page_width,
        "page_height": page.page_height,
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
                "crop_source_path": image.crop_source_path,
                "crop_bbox": image.crop_bbox,
                "crop_method": image.crop_method,
                "confidence": image.confidence,
                "importance_score": image.importance_score,
                "importance_rank": image.importance_rank,
                "importance_reason": image.importance_reason,
                "layout_order": image.layout_order,
                "source_element_ids": list(image.source_element_ids),
                "anchor_element_ids": image.anchor_element_ids,
                "anchor_reason": image.anchor_reason,
                "grounding_confidence": image.grounding_confidence,
                "figure_explanation": image.figure_explanation,
                "figure_explanation_status": image.figure_explanation_status,
                "figure_audit_status": image.figure_audit_status,
            }
            for image in page.images
        ],
        "semantic_groups": page.semantic_groups,
        "semantic_relations": page.semantic_relations,
        "element_ir": build_page_ir(Deck(source_path="", source_type="", pages=[page]), page),
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
    return {
        "type": "table",
        "source_path": deck.source_path,
        "slide_id": page.slide_id,
        "element_id": table.id,
        "preview": table_preview(table),
        "table_summary": table.table_summary,
        "table_conclusion": table.table_conclusion,
        "key_rows": table.key_rows,
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
        "crop_source_path": image.crop_source_path,
        "crop_bbox": image.crop_bbox,
        "crop_method": image.crop_method,
        "crop_quality": image.crop_quality,
        "crop_warnings": list(image.crop_warnings),
        "confidence": image.confidence,
        "importance_score": image.importance_score,
        "importance_rank": image.importance_rank,
        "importance_reason": image.importance_reason,
        "layout_order": image.layout_order,
        "source_element_ids": list(image.source_element_ids),
        "anchor_element_ids": image.anchor_element_ids,
        "anchor_reason": image.anchor_reason,
        "grounding_confidence": image.grounding_confidence,
        "figure_explanation": image.figure_explanation,
        "figure_explanation_status": image.figure_explanation_status,
        "figure_audit_status": image.figure_audit_status,
        "preview": image.caption or image.path,
    }


def _preview(text: str, limit: int = 160) -> str:
    value = " ".join(text.split())
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"
