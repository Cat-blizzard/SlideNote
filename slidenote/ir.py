from __future__ import annotations

from typing import Any, Iterable

from slidenote.ir_context import IRBuildContext
from slidenote.ir_projection import source_ref_from_element
from slidenote.ir_standard import (
    assign_reading_order,
    coerce_bbox,
    compact,
    guard_or_semantic_value,
    primary_role,
    semantic_value,
    standard_fields,
)
from slidenote.models import Deck, ImageAsset, SlidePage, TableBlock, TextBlock
from slidenote.table_understanding import table_preview


ElementIR = dict[str, Any]
PageIR = dict[str, Any]


def build_deck_ir(
    deck: Deck,
    *,
    content_guard: dict[str, Any] | None = None,
    coverage_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = IRBuildContext(deck=deck, content_guard=content_guard, coverage_report=coverage_report)
    return {
        "schema_version": 1,
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "schema_features": [
            "normalized_bbox",
            "primary_role",
            "confidence",
            "reading_order",
            "coverage_state",
        ],
        "pages": [_build_page_ir(context, page) for page in deck.pages],
    }


def build_page_ir(
    deck: Deck,
    page: SlidePage,
    *,
    content_guard: dict[str, Any] | None = None,
    coverage_report: dict[str, Any] | None = None,
) -> PageIR:
    context = IRBuildContext(deck=deck, content_guard=content_guard, coverage_report=coverage_report)
    return _build_page_ir(context, page)


def iter_expected_source_elements(deck: Deck) -> Iterable[ElementIR]:
    for page in build_deck_ir(deck)["pages"]:
        for element in page["elements"]:
            if element.get("kind") == "semantic_group":
                continue
            if element.get("expected_in_notes", True):
                yield element


def element_index_from_ir(deck: Deck) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for page in build_deck_ir(deck)["pages"]:
        slide_id = int(page["slide_id"])
        for element in page["elements"]:
            if element.get("kind") == "semantic_group":
                continue
            element_id = str(element.get("element_id") or "")
            if not element_id:
                continue
            index[element_id] = source_ref_from_element(deck, slide_id, element)
    return index


def _build_page_ir(context: IRBuildContext, page: SlidePage) -> PageIR:
    elements: list[ElementIR] = []
    semantic_by_id = context.semantic_by_id(page)
    for block in page.text_blocks:
        elements.append(_text_element(context, page, block, semantic_by_id.get(block.id)))
    for table in page.tables:
        elements.append(_table_element(context, page, table, semantic_by_id.get(table.id)))
    for image in page.images:
        elements.append(_image_element(context, page, image, semantic_by_id.get(image.id)))
    for group in page.semantic_groups:
        if isinstance(group, dict):
            elements.append(_semantic_group_element(context, page, group))
    for element in elements:
        element.setdefault("slide_id", page.slide_id)
    assign_reading_order(elements)
    return {
        "slide_id": page.slide_id,
        "title": page.title,
        "page_width": page.page_width,
        "page_height": page.page_height,
        "page_modality": page.page_modality,
        "page_role": context.page_role(page.slide_id),
        "elements": elements,
    }


def _text_element(
    context: IRBuildContext,
    page: SlidePage,
    block: TextBlock,
    semantic: dict[str, Any] | None,
) -> ElementIR:
    roles = {
        "text_type": block.type,
        "learning_role": guard_or_semantic_value(context, block.id, semantic, "learning_role"),
        "block_type": semantic_value(semantic, "block_type"),
        "must_explain": guard_or_semantic_value(context, block.id, semantic, "must_explain"),
        "group_id": semantic_value(semantic, "group_id"),
    }
    role = primary_role("text", roles)
    element = {
        "element_id": block.id,
        "kind": "text",
        "bbox": coerce_bbox(block.bbox),
        "roles": compact(roles),
        "evidence": {
            "content": block.content,
            "preview": _preview(block.content),
        },
        "source_ids": [block.id],
        "expected_in_notes": True,
    }
    element.update(
        standard_fields(
            context=context,
            page=page,
            element_id=block.id,
            kind="text",
            role=role,
            raw_bbox=block.bbox,
            bbox_source="source_bbox",
            semantic=semantic,
            expected_in_notes=True,
            required_hint=bool(compact(roles).get("must_explain")),
            fallback_confidence=0.95,
        )
    )
    return element


def _table_element(
    context: IRBuildContext,
    page: SlidePage,
    table: TableBlock,
    semantic: dict[str, Any] | None,
) -> ElementIR:
    roles = {
        "learning_role": guard_or_semantic_value(context, table.id, semantic, "learning_role") or "table_conclusion",
        "block_type": semantic_value(semantic, "block_type") or "table",
        "must_explain": guard_or_semantic_value(context, table.id, semantic, "must_explain", default=True),
        "group_id": semantic_value(semantic, "group_id"),
    }
    role = primary_role("table", roles)
    element = {
        "element_id": table.id,
        "kind": "table",
        "bbox": coerce_bbox(table.bbox),
        "roles": compact(roles),
        "evidence": {
            "rows": table.rows,
            "preview": table_preview(table),
            "table_summary": table.table_summary,
            "table_conclusion": table.table_conclusion,
            "key_rows": table.key_rows,
        },
        "source_ids": [table.id],
        "expected_in_notes": True,
    }
    element.update(
        standard_fields(
            context=context,
            page=page,
            element_id=table.id,
            kind="table",
            role=role,
            raw_bbox=table.bbox,
            bbox_source="source_bbox",
            semantic=semantic,
            expected_in_notes=True,
            required_hint=bool(compact(roles).get("must_explain")),
            fallback_confidence=0.9,
        )
    )
    return element


def _image_element(
    context: IRBuildContext,
    page: SlidePage,
    image: ImageAsset,
    semantic: dict[str, Any] | None,
) -> ElementIR:
    roles = {
        "image_role": image.role,
        "learning_role": guard_or_semantic_value(context, image.id, semantic, "learning_role"),
        "block_type": semantic_value(semantic, "block_type"),
        "must_explain": guard_or_semantic_value(context, image.id, semantic, "must_explain"),
        "ignored": image.ignored,
        "ignore_reason": image.ignore_reason,
        "crop_method": image.crop_method,
        "crop_quality": image.crop_quality,
        "figure_explanation_status": image.figure_explanation_status,
        "figure_audit_status": image.figure_audit_status,
    }
    source_ids = _unique_ids([image.id, *image.source_element_ids])
    raw_bbox = image.crop_bbox or image.bbox
    role = primary_role("image", roles)
    element = {
        "element_id": image.id,
        "kind": "image",
        "bbox": coerce_bbox(raw_bbox),
        "roles": compact(roles),
        "evidence": {
            "path": image.path,
            "caption": image.caption,
            "ocr_text": image.ocr_text,
            "visual_summary": image.visual_summary,
            "figure_explanation": image.figure_explanation,
            "preview": _preview(" ".join(_truthy([image.caption, image.figure_explanation, image.visual_summary, image.ocr_text])) or image.path),
            "width": image.width,
            "height": image.height,
            "crop_source_path": image.crop_source_path,
            "crop_bbox": coerce_bbox(image.crop_bbox),
            "crop_warnings": list(image.crop_warnings),
            "confidence": image.confidence,
            "importance_score": image.importance_score,
            "importance_rank": image.importance_rank,
            "importance_reason": image.importance_reason,
            "layout_order": image.layout_order,
            "anchor_element_ids": list(image.anchor_element_ids),
            "anchor_reason": image.anchor_reason,
            "grounding_confidence": image.grounding_confidence,
        },
        "source_ids": source_ids,
        "expected_in_notes": not image.ignored,
    }
    fallback_confidence = 0.4 if image.ignored else 0.9
    element.update(
        standard_fields(
            context=context,
            page=page,
            element_id=image.id,
            kind="image",
            role=role,
            raw_bbox=raw_bbox,
            bbox_source="crop_bbox" if image.crop_bbox else "source_bbox",
            semantic=semantic,
            expected_in_notes=not image.ignored,
            required_hint=bool(compact(roles).get("must_explain")),
            fallback_confidence=fallback_confidence,
            confidence_candidates=[image.confidence, image.grounding_confidence],
            layout_order=image.layout_order,
        )
    )
    return element


def _semantic_group_element(context: IRBuildContext, page: SlidePage, group: dict[str, Any]) -> ElementIR:
    group_id = str(group.get("group_id") or f"p{page.slide_id}_sg")
    source_ids = _unique_ids(group.get("source_element_ids") or group.get("block_ids") or [])
    roles = compact(
        {
            "scene_type": group.get("scene_type"),
            "learning_goal": group.get("learning_goal"),
            "must_explain": group.get("must_explain"),
            "crop_policy": group.get("crop_policy"),
            "importance_score": group.get("importance_score"),
        }
    )
    role = primary_role("semantic_group", roles)
    element = {
        "element_id": group_id,
        "kind": "semantic_group",
        "bbox": coerce_bbox(group.get("bbox")),
        "roles": roles,
        "evidence": {
            "block_ids": list(group.get("block_ids") or []),
            "preview": group.get("learning_goal"),
        },
        "source_ids": source_ids,
        "expected_in_notes": False,
    }
    element.update(
        standard_fields(
            context=context,
            page=page,
            element_id=group_id,
            kind="semantic_group",
            role=role,
            raw_bbox=group.get("bbox"),
            bbox_source="semantic_group_bbox",
            semantic=group,
            expected_in_notes=False,
            required_hint=bool(roles.get("must_explain")),
            fallback_confidence=0.75 if source_ids else 0.5,
            confidence_candidates=[group.get("confidence")],
            layout_order=group.get("layout_order"),
        )
    )
    return element


def _truthy(values: Iterable[str | None]) -> list[str]:
    return [value for value in values if value]


def _unique_ids(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _preview(text: str, limit: int = 160) -> str:
    value = " ".join(str(text).split())
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "..."
