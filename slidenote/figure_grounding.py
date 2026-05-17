from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from slidenote.llm_cache import utc_now_iso
from slidenote.models import Deck, ImageAsset, SlidePage, TableBlock, TextBlock


FIGURE_GROUNDING_MODES = {"off", "auto", "vision"}
FIGURE_PLACEMENT_MODES = {"inline", "page-end"}
FIGURE_AUDIT_MODES = {"off", "local", "llm"}


def enrich_deck_with_figure_grounding(
    deck: Deck,
    output_root: Path,
    mode: str = "auto",
    placement: str = "inline",
    audit: str = "local",
) -> dict[str, Any]:
    """Attach layout anchors and explanation metadata to study-value figures.

    The first implementation is intentionally deterministic: local layout and
    existing OCR/vision summaries do the grounding work. LLM audit can be added
    later without changing the JSON shape consumed by the GUI.
    """

    if mode not in FIGURE_GROUNDING_MODES:
        raise ValueError(f"figure grounding mode must be one of: {', '.join(sorted(FIGURE_GROUNDING_MODES))}")
    if placement not in FIGURE_PLACEMENT_MODES:
        raise ValueError(f"figure placement must be one of: {', '.join(sorted(FIGURE_PLACEMENT_MODES))}")
    if audit not in FIGURE_AUDIT_MODES:
        raise ValueError(f"figure audit must be one of: {', '.join(sorted(FIGURE_AUDIT_MODES))}")

    page_entries: list[dict[str, Any]] = []
    candidate_count = 0
    anchored_count = 0
    explained_count = 0
    needs_review_count = 0
    auto_insertable_count = 0

    for page in deck.pages:
        layout_elements = _layout_elements(deck, page)
        page_records: list[dict[str, Any]] = []
        for image in note_candidate_images(page):
            candidate_count += 1
            anchor = _anchor_image(deck, page, image, layout_elements)
            image.layout_order = anchor["layout_order"]
            image.anchor_element_ids = anchor["anchor_element_ids"]
            image.anchor_reason = anchor["anchor_reason"]
            image.grounding_confidence = anchor["grounding_confidence"]
            explanation, status = _figure_explanation(image)
            image.figure_explanation = explanation
            image.figure_explanation_status = status
            image.figure_audit_status = _local_audit_status(image) if audit != "off" else None

            if image.anchor_element_ids:
                anchored_count += 1
            if image.figure_explanation:
                explained_count += 1
            if image.figure_audit_status == "needs_review":
                needs_review_count += 1
            if image.anchor_element_ids or image.role == "figure_crop":
                auto_insertable_count += 1

            page_records.append(_image_record(page, image, output_root))

        page_entries.append(
            {
                "slide_id": page.slide_id,
                "title": page.title,
                "page_width": page.page_width,
                "page_height": page.page_height,
                "page_modality": page.page_modality,
                "layout_elements": [
                    {
                        "id": element["id"],
                        "kind": element["kind"],
                        "type": element.get("type"),
                        "layout_order": element["layout_order"],
                        "bbox": element.get("bbox"),
                        "preview": element.get("preview"),
                    }
                    for element in layout_elements
                ],
                "images": page_records,
            }
        )

    return {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "mode": mode,
        "placement": placement,
        "audit": audit,
        "summary": {
            "pages_total": len(deck.pages),
            "candidate_images": candidate_count,
            "anchored_images": anchored_count,
            "explained_images": explained_count,
            "auto_insertable_images": auto_insertable_count,
            "needs_review": needs_review_count,
        },
        "pages": page_entries,
    }


def note_candidate_images(page: SlidePage) -> list[ImageAsset]:
    content_images = [image for image in page.images if _is_content_image(image)]
    if content_images:
        return sorted(content_images, key=_image_sort_key)
    page_images = [image for image in page.images if not image.ignored and image.role == "page_image"]
    return sorted(page_images, key=_image_sort_key)


def ordered_page_elements(
    deck: Deck,
    page: SlidePage,
    asset_map: dict[str, str] | None = None,
    include_ignored: bool = False,
) -> list[dict[str, Any]]:
    asset_map = asset_map or {}
    elements = _layout_elements(deck, page)
    for image in page.images:
        if image.ignored and not include_ignored:
            continue
        if image.role == "page_image" and image not in note_candidate_images(page):
            continue
        order = image.layout_order
        if order is None:
            bbox = normalized_image_bbox(deck, page, image)
            order = _order_from_bbox(bbox) if bbox else 9000.0 + (image.importance_rank or 999)
        elements.append(
            {
                "id": image.id,
                "kind": "image",
                "type": image.role or "content",
                "layout_order": float(order),
                "bbox": normalized_image_bbox(deck, page, image),
                "path": asset_map.get(image.path, image.path),
                "caption": image.caption,
                "anchor_element_ids": list(image.anchor_element_ids),
                "anchor_reason": image.anchor_reason,
                "grounding_confidence": image.grounding_confidence,
                "figure_explanation": image.figure_explanation,
                "figure_explanation_status": image.figure_explanation_status,
                "importance_score": image.importance_score,
                "importance_rank": image.importance_rank,
                "preview": _preview(_image_text(image) or image.caption or image.path),
            }
        )
    return sorted(elements, key=lambda element: (float(element.get("layout_order") or 9999.0), str(element.get("id") or "")))


def normalized_image_bbox(deck: Deck, page: SlidePage, image: ImageAsset) -> list[float] | None:
    if image.crop_bbox and _looks_normalized(image.crop_bbox):
        return _clamp_bbox(image.crop_bbox)
    return _normalize_bbox(deck.source_type, image.bbox, page)


def normalized_element_bbox(deck: Deck, page: SlidePage, element: TextBlock | TableBlock) -> list[float] | None:
    return _normalize_bbox(deck.source_type, element.bbox, page)


def _layout_elements(deck: Deck, page: SlidePage) -> list[dict[str, Any]]:
    elements: list[dict[str, Any]] = []
    fallback_index = 0
    for block in page.text_blocks:
        bbox = normalized_element_bbox(deck, page, block)
        order = _order_from_bbox(bbox) if bbox else float(fallback_index)
        elements.append(
            {
                "id": block.id,
                "kind": "text",
                "type": block.type,
                "bbox": bbox,
                "layout_order": order,
                "preview": _preview(block.content),
                "content": block.content,
            }
        )
        fallback_index += 1
    for table in page.tables:
        bbox = normalized_element_bbox(deck, page, table)
        order = _order_from_bbox(bbox) if bbox else float(fallback_index)
        preview = " / ".join(" | ".join(row) for row in table.rows[:2])
        elements.append(
            {
                "id": table.id,
                "kind": "table",
                "type": "table",
                "bbox": bbox,
                "layout_order": order,
                "preview": _preview(preview),
                "content": preview,
            }
        )
        fallback_index += 1
    return sorted(elements, key=lambda element: (float(element["layout_order"]), str(element["id"])))


def _anchor_image(deck: Deck, page: SlidePage, image: ImageAsset, layout_elements: list[dict[str, Any]]) -> dict[str, Any]:
    bbox = normalized_image_bbox(deck, page, image)
    layout_order = _order_from_bbox(bbox) if bbox else 9000.0 + (image.importance_rank or 999)
    if bbox and layout_elements:
        spatial = _spatial_anchor(bbox, layout_elements)
        if spatial:
            return {
                "layout_order": layout_order,
                "anchor_element_ids": [spatial["id"]],
                "anchor_reason": spatial["reason"],
                "grounding_confidence": spatial["confidence"],
            }

    semantic = _semantic_anchor(image, layout_elements)
    if semantic:
        return {
            "layout_order": layout_order,
            "anchor_element_ids": [semantic["id"]],
            "anchor_reason": "semantic_overlap",
            "grounding_confidence": semantic["confidence"],
        }

    title = next((element for element in layout_elements if element.get("type") in {"title", "heading"}), None)
    if title:
        return {
            "layout_order": layout_order,
            "anchor_element_ids": [title["id"]],
            "anchor_reason": "page_title_fallback",
            "grounding_confidence": 0.45,
        }
    if layout_elements:
        return {
            "layout_order": layout_order,
            "anchor_element_ids": [layout_elements[0]["id"]],
            "anchor_reason": "first_element_fallback",
            "grounding_confidence": 0.4,
        }
    return {
        "layout_order": layout_order,
        "anchor_element_ids": [],
        "anchor_reason": "page_end_fallback",
        "grounding_confidence": 0.25,
    }


def _spatial_anchor(image_bbox: list[float], layout_elements: list[dict[str, Any]]) -> dict[str, Any] | None:
    x1, y1, x2, y2 = image_bbox
    image_cx = (x1 + x2) / 2
    image_cy = (y1 + y2) / 2
    candidates: list[tuple[float, dict[str, Any], str, float]] = []
    for element in layout_elements:
        bbox = element.get("bbox")
        if not bbox:
            continue
        ex1, ey1, ex2, ey2 = bbox
        ecx = (ex1 + ex2) / 2
        ecy = (ey1 + ey2) / 2
        overlap = _horizontal_overlap_ratio(image_bbox, bbox)
        vertical_gap = max(0.0, y1 - ey2)
        distance = math.hypot(image_cx - ecx, image_cy - ecy)
        if ey2 <= y1 + 0.03 and overlap >= 0.08:
            score = vertical_gap * 1.8 + abs(image_cx - ecx) * 0.45 - overlap * 0.25
            candidates.append((score, element, "bbox_nearest_preceding_element", min(0.92, 0.72 + overlap * 0.2)))
        else:
            score = distance + (0.12 if overlap < 0.08 else 0.0)
            candidates.append((score + 0.35, element, "bbox_nearest_element", min(0.7, 0.46 + overlap * 0.2)))
    if not candidates:
        return None
    _, element, reason, confidence = min(candidates, key=lambda item: item[0])
    return {"id": element["id"], "reason": reason, "confidence": round(confidence, 3)}


def _semantic_anchor(image: ImageAsset, layout_elements: list[dict[str, Any]]) -> dict[str, Any] | None:
    image_tokens = _tokens(_image_text(image))
    if not image_tokens:
        return None
    best: tuple[int, dict[str, Any]] | None = None
    for element in layout_elements:
        score = len(image_tokens.intersection(_tokens(str(element.get("content") or element.get("preview") or ""))))
        if score and (best is None or score > best[0]):
            best = (score, element)
    if best is None:
        return None
    score, element = best
    confidence = min(0.72, 0.42 + score * 0.06)
    return {"id": element["id"], "confidence": round(confidence, 3)}


def _figure_explanation(image: ImageAsset) -> tuple[str | None, str]:
    if image.figure_explanation:
        return _preview(image.figure_explanation, limit=420), image.figure_explanation_status or "existing"
    if image.visual_summary:
        return _preview(image.visual_summary, limit=420), "visual_summary"
    if image.ocr_text:
        return _preview(image.ocr_text, limit=420), "ocr_text"
    if image.caption and not _is_generic_caption(image.caption):
        return _preview(image.caption, limit=220), "caption"
    return None, "missing"


def _local_audit_status(image: ImageAsset) -> str:
    if not image.anchor_element_ids:
        return "needs_review"
    if (image.grounding_confidence or 0.0) < 0.45:
        return "needs_review"
    if image.figure_explanation_status == "missing" and (image.importance_score or 0.0) >= 0.6:
        return "needs_review"
    return "ok"


def _image_record(page: SlidePage, image: ImageAsset, output_root: Path) -> dict[str, Any]:
    del output_root
    return {
        "id": image.id,
        "path": image.path,
        "caption": image.caption,
        "role": image.role,
        "ignored": image.ignored,
        "ignore_reason": image.ignore_reason,
        "layout_order": image.layout_order,
        "anchor_element_ids": list(image.anchor_element_ids),
        "anchor_reason": image.anchor_reason,
        "grounding_confidence": image.grounding_confidence,
        "figure_explanation": image.figure_explanation,
        "figure_explanation_status": image.figure_explanation_status,
        "figure_audit_status": image.figure_audit_status,
        "importance_score": image.importance_score,
        "importance_rank": image.importance_rank,
        "importance_reason": image.importance_reason,
        "crop_source_path": image.crop_source_path,
        "crop_bbox": image.crop_bbox,
        "crop_method": image.crop_method,
        "confidence": image.confidence,
        "bbox": image.bbox,
        "slide_id": page.slide_id,
    }


def _normalize_bbox(source_type: str, bbox: list[float] | None, page: SlidePage) -> list[float] | None:
    if not bbox or len(bbox) != 4:
        return None
    if _looks_normalized(bbox):
        return _clamp_bbox(bbox)
    width = page.page_width or 0.0
    height = page.page_height or 0.0
    if width <= 0 or height <= 0:
        return None
    x1, y1, third, fourth = [float(value) for value in bbox]
    if source_type == "pptx":
        x2 = x1 + third
        y2 = y1 + fourth
    else:
        x2 = third
        y2 = fourth
    return _clamp_bbox([x1 / width, y1 / height, x2 / width, y2 / height])


def _looks_normalized(bbox: list[float]) -> bool:
    return len(bbox) == 4 and all(-0.001 <= float(value) <= 1.001 for value in bbox)


def _clamp_bbox(bbox: list[float]) -> list[float]:
    x1, y1, x2, y2 = [max(0.0, min(1.0, float(value))) for value in bbox]
    if x2 < x1:
        x1, x2 = x2, x1
    if y2 < y1:
        y1, y2 = y2, y1
    return [round(x1, 4), round(y1, 4), round(x2, 4), round(y2, 4)]


def _order_from_bbox(bbox: list[float] | None) -> float:
    if not bbox:
        return 9999.0
    return round(float(bbox[1]) * 1000.0 + float(bbox[0]), 4)


def _horizontal_overlap_ratio(a: list[float], b: list[float]) -> float:
    left = max(a[0], b[0])
    right = min(a[2], b[2])
    overlap = max(0.0, right - left)
    denom = max(0.0001, min(a[2] - a[0], b[2] - b[0]))
    return overlap / denom


def _is_content_image(image: ImageAsset) -> bool:
    return not image.ignored and image.role not in {"decorative", "page_image"}


def _image_sort_key(image: ImageAsset) -> tuple[int, float, str]:
    rank = image.importance_rank if image.importance_rank is not None else 9999
    score = image.importance_score or 0.0
    return (rank, -score, image.id)


def _image_text(image: ImageAsset) -> str:
    return " ".join(part for part in [image.caption, image.visual_summary, image.ocr_text] if part)


def _is_generic_caption(caption: str) -> bool:
    return bool(re.fullmatch(r"第\s*\d+\s*页(?:嵌入)?图片\s*\d*|第\s*\d+\s*页图片|图示", caption.strip()))


def _tokens(text: str) -> set[str]:
    words = {word.lower() for word in re.findall(r"[A-Za-z0-9_]{2,}", text)}
    cjk = {char for char in text if "\u4e00" <= char <= "\u9fff"}
    return words.union(cjk)


def _preview(text: str, limit: int = 160) -> str:
    value = re.sub(r"\s+", " ", text).strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"
