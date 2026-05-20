from __future__ import annotations

import json
import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from slidenote.llm import LLMClient, resolve_provider_runtime
from slidenote.llm_cache import LLM_CACHE_SCHEMA_VERSION, LLMCache, make_cache_key, sha256_text
from slidenote.llm_cache import utc_now_iso
from slidenote.models import Deck, ImageAsset, SlidePage, TableBlock, TextBlock
from slidenote.table_understanding import table_text_for_prompt
from slidenote.vision import _cleanup_temp_image, _file_sha256, _prepare_image_for_api


FIGURE_GROUNDING_MODES = {"off", "auto", "vision"}
FIGURE_PLACEMENT_MODES = {"inline", "page-end"}
FIGURE_AUDIT_MODES = {"off", "local", "llm"}
FIGURE_GROUNDING_PROMPT_VERSION = "figure-grounding-vision-v1"
FIGURE_GROUNDING_MIN_CONFIDENCE = 0.55

FIGURE_GROUNDING_SYSTEM_PROMPT = (
    "You are a course slide figure grounding analyst. Return strict JSON only. "
    "Use only ids that appear in the provided candidates, layout elements, or semantic groups."
)


def enrich_deck_with_figure_grounding(
    deck: Deck,
    output_root: Path,
    mode: str = "auto",
    placement: str = "inline",
    audit: str = "local",
    provider: str = "openai",
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    cache_mode: str = "on",
    cache_dir: Path | None = None,
    max_output_tokens: int = 1200,
    temperature: float | None = 0.0,
    detail: str = "low",
    max_edge: int = 1400,
    concurrency: int = 1,
    refresh_slide_ids: set[int] | None = None,
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

    output_root = Path(output_root)
    runtime: dict[str, Any] | None = None
    cache: LLMCache | None = None
    resolved_cache_dir: Path | None = None
    if mode == "vision":
        runtime = resolve_provider_runtime(provider, model=model, base_url=base_url, for_vision=True)
        if not runtime["supports_image_input"]:
            raise RuntimeError(f"Figure grounding provider `{runtime['provider']}` does not support image input.")
        resolved_cache_dir = (cache_dir or (output_root / ".cache" / "vision")).resolve()
        cache = LLMCache(resolved_cache_dir, mode=cache_mode)

    page_entries: list[dict[str, Any]] = []
    candidate_count = 0
    anchored_count = 0
    explained_count = 0
    needs_review_count = 0
    auto_insertable_count = 0
    vision_records_by_slide: dict[int, dict[str, Any]] = {}
    local_context_by_slide: dict[int, dict[str, Any]] = {}

    for page in deck.pages:
        layout_elements = _layout_elements(deck, page)
        candidates = note_candidate_images(page)
        local_context_by_slide[page.slide_id] = {"layout_elements": layout_elements, "candidate_ids": [image.id for image in candidates]}
        for image in candidates:
            candidate_count += 1
            anchor = _anchor_image(deck, page, image, layout_elements)
            image.layout_order = anchor["layout_order"]
            image.anchor_element_ids = anchor["anchor_element_ids"]
            image.anchor_group_id = _local_anchor_group_id(page, image.anchor_element_ids)
            image.anchor_reason = anchor["anchor_reason"]
            image.grounding_confidence = anchor["grounding_confidence"]
            explanation, status = _figure_explanation(image)
            image.figure_explanation = explanation
            image.figure_explanation_status = status
            image.figure_audit_status = _local_audit_status(image) if audit != "off" else None

    if mode == "vision" and runtime is not None and cache is not None:
        pages_with_candidates = [page for page in deck.pages if local_context_by_slide.get(page.slide_id, {}).get("candidate_ids")]
        refresh_ids = refresh_slide_ids or set()
        workers = max(1, int(concurrency or 1))

        def process(index: int, page: SlidePage) -> tuple[int, int, dict[str, Any]]:
            record = _process_figure_grounding_vision_page(
                deck=deck,
                page=page,
                layout_elements=local_context_by_slide[page.slide_id]["layout_elements"],
                output_root=output_root,
                runtime=runtime,
                cache=cache,
                cache_mode=cache_mode,
                api_key=api_key,
                max_output_tokens=max_output_tokens,
                temperature=temperature,
                detail=detail,
                max_edge=max_edge,
                audit=audit,
                force_refresh=page.slide_id in refresh_ids,
            )
            return index, page.slide_id, record

        results: list[tuple[int, int, dict[str, Any]]] = []
        if workers == 1:
            for index, page in enumerate(pages_with_candidates):
                results.append(process(index, page))
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(process, index, page): index for index, page in enumerate(pages_with_candidates)}
                for future in as_completed(futures):
                    results.append(future.result())
        for _, slide_id, record in sorted(results, key=lambda item: item[0]):
            vision_records_by_slide[slide_id] = record

    for page in deck.pages:
        layout_elements = local_context_by_slide.get(page.slide_id, {}).get("layout_elements") or _layout_elements(deck, page)
        page_records: list[dict[str, Any]] = []
        for image in note_candidate_images(page):

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
                "vision_grounding": _page_vision_grounding_summary(vision_records_by_slide.get(page.slide_id), mode),
                "images": page_records,
            }
        )

    vision_records = list(vision_records_by_slide.values())
    return {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "mode": mode,
        "placement": placement,
        "audit": audit,
        "vision_grounding": {
            "provider": runtime.get("provider") if runtime else None,
            "model": runtime.get("model") if runtime else None,
            "base_url": runtime.get("base_url") if runtime else None,
            "cache": {"mode": cache_mode, "dir": _display_path(resolved_cache_dir, output_root) if resolved_cache_dir else None},
            "prompt_version": FIGURE_GROUNDING_PROMPT_VERSION if runtime else None,
        },
        "summary": {
            "pages_total": len(deck.pages),
            "candidate_images": candidate_count,
            "anchored_images": anchored_count,
            "explained_images": explained_count,
            "auto_insertable_images": auto_insertable_count,
            "needs_review": needs_review_count,
            "vision_candidate_pages": len(vision_records),
            "vision_calls": sum(1 for record in vision_records if record.get("llm_call")),
            "vision_cache_hits": sum(1 for record in vision_records if record.get("cache_status") == "local_hit"),
            "vision_applied_images": sum(int(record.get("applied_images") or 0) for record in vision_records),
            "vision_fallback_images": sum(int(record.get("fallback_images") or 0) for record in vision_records),
            "vision_fallback_pages": sum(1 for record in vision_records if record.get("status") != "applied"),
            "input_tokens": _sum_int(record.get("input_tokens") for record in vision_records),
            "output_tokens": _sum_int(record.get("output_tokens") for record in vision_records),
            "total_tokens": _sum_int(record.get("total_tokens") for record in vision_records),
            "provider_cached_input_tokens": _sum_int(record.get("provider_cached_input_tokens") for record in vision_records),
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
                "anchor_group_id": image.anchor_group_id,
                "anchor_reason": image.anchor_reason,
                "grounding_confidence": image.grounding_confidence,
                "figure_explanation": image.figure_explanation,
                "figure_explanation_status": image.figure_explanation_status,
                "source_element_ids": list(image.source_element_ids),
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
        preview = table_text_for_prompt(table, raw_rows=2)
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


def _process_figure_grounding_vision_page(
    deck: Deck,
    page: SlidePage,
    layout_elements: list[dict[str, Any]],
    output_root: Path,
    runtime: dict[str, Any],
    cache: LLMCache,
    cache_mode: str,
    api_key: str | None,
    max_output_tokens: int,
    temperature: float | None,
    detail: str,
    max_edge: int,
    audit: str,
    force_refresh: bool,
) -> dict[str, Any]:
    candidates = note_candidate_images(page)
    if not candidates:
        return {"status": "skipped", "reason": "no_candidate_images", "llm_call": False, "cache_status": "skipped"}
    if not page.page_screenshot:
        return {
            "status": "fallback",
            "reason": "missing_page_screenshot",
            "llm_call": False,
            "cache_status": "skipped",
            "fallback_images": len(candidates),
            "warnings": ["missing_page_screenshot"],
        }
    source_path = (output_root / page.page_screenshot).resolve()
    if not source_path.exists():
        return {
            "status": "fallback",
            "reason": "missing_page_screenshot_file",
            "llm_call": False,
            "cache_status": "skipped",
            "fallback_images": len(candidates),
            "warnings": ["missing_page_screenshot_file"],
        }
    prepared = _prepare_image_for_api(source_path, max_edge=max_edge)
    if prepared is None:
        return {
            "status": "fallback",
            "reason": "unsupported_or_unreadable_image",
            "llm_call": False,
            "cache_status": "skipped",
            "fallback_images": len(candidates),
            "warnings": ["unsupported_or_unreadable_image"],
        }

    prepared_path, image_meta = prepared
    try:
        prompt = _figure_grounding_prompt(deck, page, layout_elements, candidates)
        cache_key_payload = {
            "schema_version": LLM_CACHE_SCHEMA_VERSION,
            "prompt_version": FIGURE_GROUNDING_PROMPT_VERSION,
            "provider": runtime["provider"],
            "model": runtime["model"],
            "base_url": runtime["base_url"],
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
            "detail": detail,
            "max_edge": max_edge,
            "source_path": page.page_screenshot,
            "source_image_hash": _file_sha256(source_path),
            "prompt_hash": sha256_text(prompt),
        }
        cache_key = make_cache_key(cache_key_payload)
        cache_path = cache.path_for(cache_key)
        cached = None if force_refresh else cache.read(cache_key)
        record: dict[str, Any] = {
            "status": "fallback",
            "slide_id": page.slide_id,
            "cache_key": cache_key,
            "cache_file": _display_path(cache_path, output_root),
            "image_meta": image_meta,
            "warnings": [],
            "invalid_references": [],
            "applied_images": 0,
            "fallback_images": len(candidates),
        }
        if cached:
            result_json = cached["output_text"]
            response_usage = cached.get("response_usage") or {}
            record.update(
                {
                    "cache_status": "local_hit",
                    "llm_call": False,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "provider_cached_input_tokens": 0,
                    "cached_entry_usage": response_usage,
                    "cached_at": cached.get("created_at"),
                }
            )
        else:
            client = LLMClient(
                provider=str(runtime["provider"]),
                model=str(runtime["model"]),
                api_key=api_key,
                base_url=runtime["base_url"],
                max_output_tokens=max_output_tokens,
                temperature=temperature,
            )
            llm_result = client.generate_image_with_usage(
                prepared_path,
                prompt,
                system_prompt=FIGURE_GROUNDING_SYSTEM_PROMPT,
                image_detail=detail,
            )
            result_json = llm_result.text
            response_usage = llm_result.usage or {}
            cache_status = "disabled" if cache_mode == "off" else "refresh" if cache_mode == "refresh" or force_refresh else "miss"
            written_path = cache.write(
                cache_key,
                {
                    "provider": runtime["provider"],
                    "model": runtime["model"],
                    "base_url": runtime["base_url"],
                    "prompt_version": FIGURE_GROUNDING_PROMPT_VERSION,
                    "slide_id": page.slide_id,
                    "image_meta": image_meta,
                    "output_text": result_json,
                    "response_usage": response_usage,
                },
            )
            if written_path is not None:
                cache_path = written_path
            record.update(
                {
                    "cache_status": cache_status,
                    "cache_file": _display_path(cache_path, output_root),
                    "llm_call": True,
                    "api_retries": response_usage.get("retries", 0),
                    "input_tokens": response_usage.get("input_tokens"),
                    "output_tokens": response_usage.get("output_tokens"),
                    "total_tokens": response_usage.get("total_tokens"),
                    "provider_cached_input_tokens": response_usage.get("provider_cached_input_tokens"),
                    "provider_usage": response_usage,
                }
            )

        parsed = _parse_json_object(result_json)
        if parsed is None:
            record["reason"] = "model_output_not_json"
            record["warnings"].append("model_output_not_json")
            return record
        applied, fallback, validation = _apply_vision_grounding(page, candidates, layout_elements, parsed, audit=audit)
        record["warnings"].extend(validation["warnings"])
        record["invalid_references"] = validation["invalid_references"]
        record["result"] = parsed
        record["applied_images"] = applied
        record["fallback_images"] = fallback
        record["status"] = "applied" if applied else "fallback"
        if not applied:
            record["reason"] = validation["fallback_reason"] or "no_vision_grounding_applied"
        return record
    except Exception as exc:
        return {
            "status": "fallback",
            "reason": "vision_call_failed",
            "llm_call": False,
            "cache_status": "error",
            "applied_images": 0,
            "fallback_images": len(candidates),
            "warnings": [f"vision_call_failed:{type(exc).__name__}"],
        }
    finally:
        _cleanup_temp_image(prepared_path)


def _figure_grounding_prompt(
    deck: Deck,
    page: SlidePage,
    layout_elements: list[dict[str, Any]],
    candidates: list[ImageAsset],
) -> str:
    payload = {
        "task": "figure_grounding",
        "instructions": [
            "Return JSON only.",
            "For each candidate figure, choose nearby text/table anchors and optionally one semantic group.",
            "Use only ids listed in candidate_images, layout_elements, and semantic_groups.",
            "Prefer anchors that explain what the figure teaches, not just the nearest visual object.",
            "For code/output/blue-box/red-causal-annotation teaching scenes, anchor to the complete semantic group.",
        ],
        "output_schema": {
            "figures": [
                {
                    "image_id": "candidate image id",
                    "anchor_element_ids": ["text/table ids"],
                    "anchor_group_id": "semantic group id or null",
                    "figure_explanation": "short explanation grounded in visible evidence",
                    "grounding_confidence": 0.0,
                    "anchor_reason": "why these anchors explain the figure",
                    "audit_status": "ok/needs_review",
                }
            ],
            "warnings": [],
        },
        "slide": {
            "slide_id": page.slide_id,
            "title": page.title,
            "page_modality": page.page_modality,
            "source_type": deck.source_type,
            "page_size": {"width": page.page_width, "height": page.page_height},
            "page_ocr_text": _preview(page.page_ocr_text or "", 700),
            "page_visual_summary": _preview(page.page_visual_summary or "", 500),
        },
        "layout_elements": [
            {
                "id": element.get("id"),
                "kind": element.get("kind"),
                "type": element.get("type"),
                "bbox": element.get("bbox"),
                "layout_order": element.get("layout_order"),
                "preview": element.get("preview"),
            }
            for element in layout_elements
        ],
        "semantic_groups": [
            {
                "group_id": group.get("group_id"),
                "scene_type": group.get("scene_type"),
                "learning_goal": group.get("learning_goal"),
                "block_ids": group.get("block_ids"),
                "crop_policy": group.get("crop_policy"),
            }
            for group in page.semantic_groups
        ],
        "semantic_relations": page.semantic_relations[:12],
        "candidate_images": [
            {
                "image_id": image.id,
                "path": image.path,
                "role": image.role,
                "bbox": normalized_image_bbox(deck, page, image),
                "caption": image.caption,
                "ocr_text": _preview(image.ocr_text or "", 500),
                "visual_summary": _preview(image.visual_summary or "", 500),
                "local_anchor_element_ids": list(image.anchor_element_ids),
                "local_anchor_group_id": image.anchor_group_id,
                "local_anchor_reason": image.anchor_reason,
                "local_grounding_confidence": image.grounding_confidence,
                "source_element_ids": list(image.source_element_ids),
            }
            for image in candidates
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _apply_vision_grounding(
    page: SlidePage,
    candidates: list[ImageAsset],
    layout_elements: list[dict[str, Any]],
    parsed: dict[str, Any],
    audit: str,
) -> tuple[int, int, dict[str, Any]]:
    warnings = [str(item) for item in parsed.get("warnings", []) if isinstance(item, (str, int, float))]
    invalid_refs: list[dict[str, Any]] = []
    candidate_by_id = {image.id: image for image in candidates}
    valid_element_ids = {str(element.get("id")) for element in layout_elements if element.get("id")}
    valid_group_ids = {str(group.get("group_id")) for group in page.semantic_groups if group.get("group_id")}
    raw_items = parsed.get("figures") or parsed.get("images") or []
    if not isinstance(raw_items, list):
        return 0, len(candidates), {
            "warnings": warnings + ["missing_figures_array"],
            "invalid_references": invalid_refs,
            "fallback_reason": "missing_figures_array",
        }

    applied = 0
    seen: set[str] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        image_id = str(item.get("image_id") or item.get("id") or "")
        image = candidate_by_id.get(image_id)
        if image is None:
            invalid_refs.append({"kind": "image_id", "id": image_id})
            continue
        seen.add(image_id)
        confidence = round(max(0.0, min(1.0, _as_float(item.get("grounding_confidence") or item.get("confidence"), 0.0))), 3)
        if confidence < FIGURE_GROUNDING_MIN_CONFIDENCE:
            warnings.append(f"low_confidence_grounding:{image_id}")
            continue
        raw_anchor_ids = item.get("anchor_element_ids") or item.get("anchor_ids") or []
        if not isinstance(raw_anchor_ids, list):
            raw_anchor_ids = []
        anchor_ids: list[str] = []
        for raw_id in raw_anchor_ids:
            anchor_id = str(raw_id)
            if anchor_id in valid_element_ids and anchor_id not in anchor_ids:
                anchor_ids.append(anchor_id)
            else:
                invalid_refs.append({"kind": "anchor_element_id", "id": anchor_id, "image_id": image_id})
        group_id = item.get("anchor_group_id")
        group_id = str(group_id) if group_id else None
        if group_id and group_id not in valid_group_ids:
            invalid_refs.append({"kind": "anchor_group_id", "id": group_id, "image_id": image_id})
            group_id = None
        if not anchor_ids and group_id:
            anchor_ids = _group_anchor_element_ids(page, group_id, valid_element_ids)
        if not anchor_ids:
            warnings.append(f"discarded_grounding_without_valid_anchor:{image_id}")
            continue
        image.anchor_element_ids = anchor_ids
        image.anchor_group_id = group_id
        image.anchor_reason = str(item.get("anchor_reason") or "vision_grounding")
        image.grounding_confidence = confidence
        explanation = str(item.get("figure_explanation") or "").strip()
        if explanation:
            image.figure_explanation = _preview(explanation, limit=420)
            image.figure_explanation_status = "vision_grounding"
        elif not image.figure_explanation:
            explanation, status = _figure_explanation(image)
            image.figure_explanation = explanation
            image.figure_explanation_status = status
        if audit != "off":
            audit_status = str(item.get("audit_status") or "").strip()
            image.figure_audit_status = audit_status if audit_status in {"ok", "needs_review"} else _local_audit_status(image)
        applied += 1

    if invalid_refs:
        warnings.append("discarded_invalid_model_references")
    fallback = len(candidates) - applied
    fallback_reason = None if applied else "no_valid_vision_grounding"
    if not seen and raw_items:
        fallback_reason = "no_candidate_ids_matched"
    return applied, fallback, {"warnings": warnings, "invalid_references": invalid_refs, "fallback_reason": fallback_reason}


def _group_anchor_element_ids(page: SlidePage, group_id: str, valid_element_ids: set[str]) -> list[str]:
    group = next((item for item in page.semantic_groups if str(item.get("group_id")) == group_id), None)
    if not group:
        return []
    result: list[str] = []
    for block_id in group.get("block_ids") or []:
        block_id = str(block_id)
        if block_id in valid_element_ids and block_id not in result:
            result.append(block_id)
    return result


def _local_anchor_group_id(page: SlidePage, anchor_element_ids: list[str]) -> str | None:
    if not anchor_element_ids:
        return None
    anchor_set = set(anchor_element_ids)
    for group in page.semantic_groups:
        if anchor_set.intersection(str(block_id) for block_id in (group.get("block_ids") or [])):
            return str(group.get("group_id"))
    return None


def _page_vision_grounding_summary(record: dict[str, Any] | None, mode: str) -> dict[str, Any]:
    if record is None:
        return {"status": "disabled" if mode != "vision" else "not_selected", "llm_call": False}
    return {
        key: value
        for key, value in record.items()
        if key
        in {
            "status",
            "reason",
            "llm_call",
            "cache_status",
            "cache_file",
            "applied_images",
            "fallback_images",
            "warnings",
            "invalid_references",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "provider_cached_input_tokens",
            "api_retries",
        }
    }


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
        "anchor_group_id": image.anchor_group_id,
        "source_element_ids": list(image.source_element_ids),
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
        "crop_quality": image.crop_quality,
        "crop_warnings": list(image.crop_warnings),
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


def _parse_json_object(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _display_path(path: Path, output_root: Path) -> str:
    try:
        return path.resolve().relative_to(output_root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _sum_int(values: object) -> int:
    total = 0
    for value in values:
        if isinstance(value, int):
            total += value
    return total


def _preview(text: str, limit: int = 160) -> str:
    value = re.sub(r"\s+", " ", text).strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"
