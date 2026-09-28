from __future__ import annotations

from typing import Any

from slidenote.geometry import bbox_format, coerce_bbox, normalize_page_bbox
from slidenote.ir_context import IRBuildContext
from slidenote.models import SlidePage
from slidenote.utils import as_float


def standard_fields(
    *,
    context: IRBuildContext,
    page: SlidePage,
    element_id: str,
    kind: str,
    role: str,
    raw_bbox: Any,
    bbox_source: str,
    semantic: dict[str, Any] | None,
    expected_in_notes: bool,
    required_hint: bool,
    fallback_confidence: float,
    confidence_candidates: list[Any] | None = None,
    layout_order: Any = None,
) -> dict[str, Any]:
    raw = coerce_bbox(raw_bbox)
    normalized = normalize_page_bbox(context.deck.source_type, raw, page, precision=6)
    resolved_confidence, confidence_source = _resolve_confidence(
        semantic=semantic,
        guard_item=context.guard_item(element_id),
        fallback=fallback_confidence,
        candidates=confidence_candidates,
    )
    resolved_layout_order = as_float(layout_order, None)
    if resolved_layout_order is None:
        resolved_layout_order = as_float(semantic_value(semantic, "layout_order"), None)
    if resolved_layout_order is None:
        resolved_layout_order = _order_from_bbox(normalized)
    coverage_state, coverage = _coverage_state(
        kind=kind,
        expected_in_notes=expected_in_notes,
        required_hint=required_hint,
        guard_item=context.guard_item(element_id),
        coverage_item=context.coverage_item(element_id),
    )
    return {
        "role": role,
        "confidence": resolved_confidence,
        "confidence_source": confidence_source,
        "bbox_format": bbox_format(context.deck.source_type, raw),
        "bbox_normalized": normalized,
        "bbox_source": bbox_source if raw else None,
        "layout_order": resolved_layout_order,
        "coverage_state": coverage_state,
        "coverage": coverage,
    }


def assign_reading_order(elements: list[dict[str, Any]]) -> None:
    for index, element in enumerate(elements):
        element["_ir_insertion_order"] = index
    ordered = sorted(
        elements,
        key=lambda element: (
            element.get("layout_order") is None,
            float(element.get("layout_order") if element.get("layout_order") is not None else 1_000_000.0),
            int(element.get("_ir_insertion_order") or 0),
        ),
    )
    for reading_order, element in enumerate(ordered, start=1):
        element["reading_order"] = reading_order
    for element in elements:
        element.pop("_ir_insertion_order", None)


def primary_role(kind: str, roles: dict[str, Any]) -> str:
    if kind == "text":
        return str(roles.get("learning_role") or roles.get("text_type") or "text")
    if kind == "table":
        return str(roles.get("learning_role") or roles.get("block_type") or "table")
    if kind == "image":
        return str(roles.get("learning_role") or roles.get("image_role") or roles.get("block_type") or "image")
    if kind == "semantic_group":
        return str(roles.get("scene_type") or roles.get("learning_goal") or "semantic_group")
    return kind


def guard_or_semantic_value(
    context: IRBuildContext,
    element_id: str,
    semantic: dict[str, Any] | None,
    key: str,
    default: Any = None,
) -> Any:
    guard_item = context.guard_item(element_id)
    if guard_item and guard_item.get(key) is not None:
        return guard_item.get(key)
    return semantic_value(semantic, key, default=default)


def semantic_value(semantic: dict[str, Any] | None, key: str, default: Any = None) -> Any:
    if not semantic:
        return default
    value = semantic.get(key)
    return default if value is None else value


def compact(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def _resolve_confidence(
    *,
    semantic: dict[str, Any] | None,
    guard_item: dict[str, Any] | None,
    fallback: float,
    candidates: list[Any] | None = None,
) -> tuple[float, str]:
    for source, value in [
        ("content_guard", guard_item.get("confidence") if guard_item else None),
        ("semantic_layout", semantic_value(semantic, "confidence")),
        *[(f"candidate_{index}", value) for index, value in enumerate(candidates or [], start=1)],
        ("local_default", fallback),
    ]:
        score = as_float(value, None)
        if score is not None:
            return round(max(0.0, min(1.0, score)), 3), source
    return 0.0, "unknown"


def _coverage_state(
    *,
    kind: str,
    expected_in_notes: bool,
    required_hint: bool,
    guard_item: dict[str, Any] | None,
    coverage_item: dict[str, Any] | None,
) -> tuple[str, dict[str, Any]]:
    required = bool((guard_item or {}).get("must_explain")) or required_hint
    if kind == "semantic_group":
        return "structural_group", {"expected": False, "required": False, "structural": True}
    if not expected_in_notes:
        return "ignored", {"expected": False, "required": False, "structural": False}
    if coverage_item:
        trace_covered = bool(coverage_item.get("trace_covered"))
        visible_covered = bool(coverage_item.get("visible_covered"))
        marker_only = bool(coverage_item.get("marker_only"))
        structural = bool(coverage_item.get("structural"))
        required = bool(coverage_item.get("required")) or required
        if visible_covered:
            state = "visible_covered"
        elif marker_only:
            state = "marker_only"
        elif trace_covered:
            state = "covered"
        elif required:
            state = "missing_required"
        else:
            state = "missing"
        return state, {
            "expected": True,
            "required": required,
            "structural": structural,
            "trace_covered": trace_covered,
            "visible_covered": visible_covered,
            "marker_only": marker_only,
        }
    return ("required" if required else "expected"), {
        "expected": True,
        "required": required,
        "structural": False,
    }


def _order_from_bbox(bbox: list[float] | None) -> float | None:
    if not bbox:
        return None
    return round(float(bbox[1]) * 1000.0 + float(bbox[0]), 4)
