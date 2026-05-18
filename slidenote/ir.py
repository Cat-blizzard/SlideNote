from __future__ import annotations

from typing import Any, Iterable

from slidenote.models import Deck, ImageAsset, SlidePage, TableBlock, TextBlock
from slidenote.table_understanding import table_preview


ElementIR = dict[str, Any]
PageIR = dict[str, Any]


def build_deck_ir(deck: Deck) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "pages": [build_page_ir(deck, page) for page in deck.pages],
    }


def build_page_ir(deck: Deck, page: SlidePage) -> PageIR:
    elements: list[ElementIR] = []
    semantic_by_id = {
        str(block.get("id")): block
        for block in page.semantic_blocks
        if isinstance(block, dict) and block.get("id")
    }
    for block in page.text_blocks:
        elements.append(_text_element(block, semantic_by_id.get(block.id)))
    for table in page.tables:
        elements.append(_table_element(table, semantic_by_id.get(table.id)))
    for image in page.images:
        elements.append(_image_element(image, semantic_by_id.get(image.id)))
    for group in page.semantic_groups:
        if isinstance(group, dict):
            elements.append(_semantic_group_element(page, group))
    for element in elements:
        element.setdefault("slide_id", page.slide_id)
    return {
        "slide_id": page.slide_id,
        "title": page.title,
        "page_width": page.page_width,
        "page_height": page.page_height,
        "page_modality": page.page_modality,
        "elements": elements,
    }


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
            index[element_id] = _source_ref(deck, slide_id, element)
    return index


def _text_element(block: TextBlock, semantic: dict[str, Any] | None) -> ElementIR:
    roles = {
        "text_type": block.type,
        "learning_role": _semantic_value(semantic, "learning_role"),
        "block_type": _semantic_value(semantic, "block_type"),
        "must_explain": _semantic_value(semantic, "must_explain"),
        "group_id": _semantic_value(semantic, "group_id"),
    }
    return {
        "element_id": block.id,
        "kind": "text",
        "bbox": _bbox(block.bbox),
        "roles": _compact(roles),
        "evidence": {
            "content": block.content,
            "preview": _preview(block.content),
        },
        "source_ids": [block.id],
        "expected_in_notes": True,
    }


def _table_element(table: TableBlock, semantic: dict[str, Any] | None) -> ElementIR:
    roles = {
        "learning_role": _semantic_value(semantic, "learning_role") or "table_conclusion",
        "block_type": _semantic_value(semantic, "block_type") or "table",
        "must_explain": _semantic_value(semantic, "must_explain", default=True),
        "group_id": _semantic_value(semantic, "group_id"),
    }
    return {
        "element_id": table.id,
        "kind": "table",
        "bbox": _bbox(table.bbox),
        "roles": _compact(roles),
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


def _image_element(image: ImageAsset, semantic: dict[str, Any] | None) -> ElementIR:
    roles = {
        "image_role": image.role,
        "learning_role": _semantic_value(semantic, "learning_role"),
        "block_type": _semantic_value(semantic, "block_type"),
        "must_explain": _semantic_value(semantic, "must_explain"),
        "ignored": image.ignored,
        "ignore_reason": image.ignore_reason,
        "crop_method": image.crop_method,
        "crop_quality": image.crop_quality,
        "figure_explanation_status": image.figure_explanation_status,
        "figure_audit_status": image.figure_audit_status,
    }
    source_ids = _unique_ids([image.id, *image.source_element_ids])
    return {
        "element_id": image.id,
        "kind": "image",
        "bbox": _bbox(image.crop_bbox or image.bbox),
        "roles": _compact(roles),
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
            "crop_bbox": _bbox(image.crop_bbox),
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


def _semantic_group_element(page: SlidePage, group: dict[str, Any]) -> ElementIR:
    group_id = str(group.get("group_id") or f"p{page.slide_id}_sg")
    source_ids = _unique_ids(group.get("source_element_ids") or group.get("block_ids") or [])
    return {
        "element_id": group_id,
        "kind": "semantic_group",
        "bbox": _bbox(group.get("bbox")),
        "roles": _compact(
            {
                "scene_type": group.get("scene_type"),
                "learning_goal": group.get("learning_goal"),
                "must_explain": group.get("must_explain"),
                "crop_policy": group.get("crop_policy"),
                "importance_score": group.get("importance_score"),
            }
        ),
        "evidence": {
            "block_ids": list(group.get("block_ids") or []),
            "preview": group.get("learning_goal"),
        },
        "source_ids": source_ids,
        "expected_in_notes": False,
    }


def _source_ref(deck: Deck, slide_id: int, element: ElementIR) -> dict[str, Any]:
    kind = str(element.get("kind") or "element")
    roles = element.get("roles") if isinstance(element.get("roles"), dict) else {}
    evidence = element.get("evidence") if isinstance(element.get("evidence"), dict) else {}
    ref: dict[str, Any] = {
        "type": kind,
        "source_path": deck.source_path,
        "slide_id": slide_id,
        "element_id": element.get("element_id"),
        "preview": evidence.get("preview"),
        "bbox": element.get("bbox"),
        "source_ids": list(element.get("source_ids") or []),
        "roles": roles,
    }
    if kind == "text":
        ref["element_type"] = roles.get("text_type")
    elif kind == "table":
        ref.update(
            {
                "table_summary": evidence.get("table_summary"),
                "table_conclusion": evidence.get("table_conclusion"),
                "key_rows": evidence.get("key_rows") or [],
            }
        )
    elif kind == "image":
        ref.update(
            {
                "path": evidence.get("path"),
                "role": roles.get("image_role"),
                "width": evidence.get("width"),
                "height": evidence.get("height"),
                "crop_source_path": evidence.get("crop_source_path"),
                "crop_bbox": evidence.get("crop_bbox"),
                "crop_method": roles.get("crop_method"),
                "crop_quality": roles.get("crop_quality"),
                "crop_warnings": evidence.get("crop_warnings") or [],
                "confidence": evidence.get("confidence"),
                "importance_score": evidence.get("importance_score"),
                "importance_rank": evidence.get("importance_rank"),
                "importance_reason": evidence.get("importance_reason"),
                "layout_order": evidence.get("layout_order"),
                "source_element_ids": [sid for sid in element.get("source_ids") or [] if sid != element.get("element_id")],
                "anchor_element_ids": evidence.get("anchor_element_ids") or [],
                "anchor_reason": evidence.get("anchor_reason"),
                "grounding_confidence": evidence.get("grounding_confidence"),
                "figure_explanation": evidence.get("figure_explanation"),
                "figure_explanation_status": roles.get("figure_explanation_status"),
                "figure_audit_status": roles.get("figure_audit_status"),
            }
        )
    return ref


def _semantic_value(semantic: dict[str, Any] | None, key: str, default: Any = None) -> Any:
    if not semantic:
        return default
    value = semantic.get(key)
    return default if value is None else value


def _bbox(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        return [float(part) for part in value]
    except (TypeError, ValueError):
        return None


def _compact(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


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
