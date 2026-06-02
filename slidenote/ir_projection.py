from __future__ import annotations

from typing import Any

from slidenote.models import Deck


def source_ref_from_element(deck: Deck, slide_id: int, element: dict[str, Any]) -> dict[str, Any]:
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
        "role": element.get("role"),
        "confidence": element.get("confidence"),
        "bbox_format": element.get("bbox_format"),
        "bbox_normalized": element.get("bbox_normalized"),
        "bbox_source": element.get("bbox_source"),
        "layout_order": element.get("layout_order"),
        "reading_order": element.get("reading_order"),
        "coverage_state": element.get("coverage_state"),
        "coverage": element.get("coverage"),
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
                "source_element_ids": [
                    sid for sid in element.get("source_ids") or [] if sid != element.get("element_id")
                ],
                "anchor_element_ids": evidence.get("anchor_element_ids") or [],
                "anchor_reason": evidence.get("anchor_reason"),
                "grounding_confidence": evidence.get("grounding_confidence"),
                "figure_explanation": evidence.get("figure_explanation"),
                "figure_explanation_status": roles.get("figure_explanation_status"),
                "figure_audit_status": roles.get("figure_audit_status"),
            }
        )
    return ref
