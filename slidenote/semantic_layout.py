from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from slidenote.llm import LLMClient, resolve_provider_runtime
from slidenote.llm_cache import LLM_CACHE_SCHEMA_VERSION, LLMCache, make_cache_key, sha256_text, utc_now_iso
from slidenote.modality import page_has_hint
from slidenote.models import Deck, ImageAsset, SlidePage, TableBlock, TextBlock
from slidenote.table_understanding import table_preview
from slidenote.vision import _cleanup_temp_image, _file_sha256, _prepare_image_for_api


SEMANTIC_LAYOUT_MODES = {"auto", "local", "vision"}
SEMANTIC_LAYOUT_PROMPT_VERSION = "semantic-layout-vision-v1"

SEMANTIC_LAYOUT_SYSTEM_PROMPT = (
    "You are a course slide multimodal layout analyst. Return strict JSON only. "
    "Use only element ids that appear in the provided input. Do not invent text or image ids."
)


def enrich_deck_with_semantic_layout(
    deck: Deck,
    output_root: Path | str | None = None,
    mode: str = "auto",
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
    if mode not in SEMANTIC_LAYOUT_MODES:
        raise ValueError(f"semantic layout mode must be one of: {', '.join(sorted(SEMANTIC_LAYOUT_MODES))}")

    resolved_output_root = Path(output_root).resolve() if output_root is not None else None
    runtime: dict[str, Any] | None = None
    cache: LLMCache | None = None
    resolved_cache_dir: Path | None = None
    if mode != "local" and resolved_output_root is not None:
        runtime = resolve_provider_runtime(provider, model=model, base_url=base_url, for_vision=True)
        if not runtime["supports_image_input"]:
            raise RuntimeError(f"Semantic layout provider `{runtime['provider']}` does not support image input.")
        resolved_cache_dir = (cache_dir or (resolved_output_root / ".cache" / "vision")).resolve()
        cache = LLMCache(resolved_cache_dir, mode=cache_mode)

    local_results: dict[int, dict[str, Any]] = {}
    candidate_reasons: dict[int, str] = {}
    for page in deck.pages:
        result = analyze_page_semantic_layout(deck, page)
        local_results[page.slide_id] = result
        if mode == "vision":
            candidate_reasons[page.slide_id] = "forced_vision"
        elif mode == "auto" and runtime is not None and _page_needs_vision_enhancement(page, result):
            candidate_reasons[page.slide_id] = _vision_candidate_reason(page, result)

    vision_results: dict[int, dict[str, Any]] = {}
    targets = [page for page in deck.pages if page.slide_id in candidate_reasons]
    if targets and runtime is not None and cache is not None and resolved_output_root is not None:
        refresh_ids = refresh_slide_ids or set()
        workers = max(1, int(concurrency or 1))

        def process(index: int, page: SlidePage) -> tuple[int, int, dict[str, Any]]:
            record = _process_semantic_layout_vision_page(
                deck=deck,
                page=page,
                local_result=local_results[page.slide_id],
                reason=candidate_reasons[page.slide_id],
                output_root=resolved_output_root,
                runtime=runtime,
                cache=cache,
                cache_mode=cache_mode,
                api_key=api_key,
                max_output_tokens=max_output_tokens,
                temperature=temperature,
                detail=detail,
                max_edge=max_edge,
                force_refresh=page.slide_id in refresh_ids,
            )
            return index, page.slide_id, record

        results: list[tuple[int, int, dict[str, Any]]] = []
        if workers == 1:
            for index, page in enumerate(targets):
                results.append(process(index, page))
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(process, index, page): index for index, page in enumerate(targets)}
                for future in as_completed(futures):
                    results.append(future.result())
        for _, slide_id, record in sorted(results, key=lambda item: item[0]):
            vision_results[slide_id] = record

    pages: list[dict[str, Any]] = []
    blocks_total = 0
    groups_total = 0
    relations_total = 0
    must_explain_total = 0
    vision_pages = 0
    fallback_pages = 0
    warning_count = 0

    for page in deck.pages:
        result = local_results[page.slide_id]
        vision_record = vision_results.get(page.slide_id)
        page_method = "local_rules_v1"
        page_reason = candidate_reasons.get(page.slide_id, "local_rules")
        page_warnings: list[str] = []
        confidence = _layout_confidence(page, result)
        vision_enhancement = _local_vision_status(page, mode, page_reason)
        if vision_record:
            page_warnings.extend(str(item) for item in vision_record.get("warnings", []) if item)
            warning_count += len(page_warnings)
            if vision_record.get("status") == "applied":
                result = vision_record["result"]
                page_method = "vision_enhanced_v1"
                confidence = float(vision_record.get("confidence") or confidence)
                vision_pages += 1
            else:
                fallback_pages += 1
            vision_enhancement = {
                key: value
                for key, value in vision_record.items()
                if key
                in {
                    "status",
                    "reason",
                    "llm_call",
                    "cache_status",
                    "cache_file",
                    "input_tokens",
                    "output_tokens",
                    "total_tokens",
                    "provider_cached_input_tokens",
                    "api_retries",
                    "invalid_references",
                    "warnings",
                    "fallback_reason",
                }
            }
        page.semantic_blocks = result["blocks"]
        page.semantic_groups = result["groups"]
        page.semantic_relations = result["relations"]
        blocks_total += len(page.semantic_blocks)
        groups_total += len(page.semantic_groups)
        relations_total += len(page.semantic_relations)
        must_explain_total += sum(1 for block in page.semantic_blocks if block.get("must_explain"))
        pages.append(
            {
                "slide_id": page.slide_id,
                "title": page.title,
                "page_modality": page.page_modality,
                "method": page_method,
                "vision_enhancement": vision_enhancement,
                "confidence": round(confidence, 3),
                "reason": page_reason,
                "warnings": page_warnings,
                "blocks": page.semantic_blocks,
                "groups": page.semantic_groups,
                "relations": page.semantic_relations,
            }
        )

    vision_records = list(vision_results.values())
    return {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "mode": mode,
        "method": "hybrid_local_vision_v1" if vision_pages else "local_rules_v1",
        "vision_enhancement": {
            "provider": runtime.get("provider") if runtime else None,
            "model": runtime.get("model") if runtime else None,
            "base_url": runtime.get("base_url") if runtime else None,
            "cache": {"mode": cache_mode, "dir": _display_path(resolved_cache_dir, resolved_output_root) if resolved_cache_dir and resolved_output_root else None},
            "prompt_version": SEMANTIC_LAYOUT_PROMPT_VERSION if runtime else None,
        },
        "summary": {
            "pages_total": len(deck.pages),
            "blocks_total": blocks_total,
            "groups_total": groups_total,
            "relations_total": relations_total,
            "must_explain_blocks": must_explain_total,
            "vision_candidate_pages": len(targets),
            "vision_enhanced_pages": vision_pages,
            "vision_calls": sum(1 for record in vision_records if record.get("llm_call")),
            "vision_cache_hits": sum(1 for record in vision_records if record.get("cache_status") == "local_hit"),
            "vision_fallback_pages": fallback_pages,
            "vision_skipped_pages": sum(1 for record in vision_records if record.get("status") == "skipped"),
            "vision_warnings": warning_count,
            "input_tokens": _sum_int(record.get("input_tokens") for record in vision_records),
            "output_tokens": _sum_int(record.get("output_tokens") for record in vision_records),
            "total_tokens": _sum_int(record.get("total_tokens") for record in vision_records),
            "provider_cached_input_tokens": _sum_int(record.get("provider_cached_input_tokens") for record in vision_records),
        },
        "pages": pages,
    }


def analyze_page_semantic_layout(deck: Deck, page: SlidePage) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = []
    for block in page.text_blocks:
        blocks.append(_text_block_record(deck, page, block))
    for table in page.tables:
        blocks.append(_table_block_record(deck, page, table))
    for image in page.images:
        if image.ignored:
            continue
        blocks.append(_image_block_record(deck, page, image))
    blocks = sorted(blocks, key=lambda block: (_layout_order(block), str(block["id"])))
    groups = _semantic_groups(page, blocks)
    relations = _semantic_relations(blocks, groups)
    return {"blocks": blocks, "groups": groups, "relations": relations}


def _process_semantic_layout_vision_page(
    deck: Deck,
    page: SlidePage,
    local_result: dict[str, Any],
    reason: str,
    output_root: Path,
    runtime: dict[str, Any],
    cache: LLMCache,
    cache_mode: str,
    api_key: str | None,
    max_output_tokens: int,
    temperature: float | None,
    detail: str,
    max_edge: int,
    force_refresh: bool,
) -> dict[str, Any]:
    if not page.page_screenshot:
        return {
            "status": "skipped",
            "reason": reason,
            "fallback_reason": "missing_page_screenshot",
            "llm_call": False,
            "cache_status": "skipped",
            "warnings": ["missing_page_screenshot"],
        }
    source_path = (output_root / page.page_screenshot).resolve()
    if not source_path.exists():
        return {
            "status": "skipped",
            "reason": reason,
            "fallback_reason": "missing_file",
            "llm_call": False,
            "cache_status": "skipped",
            "warnings": ["missing_page_screenshot_file"],
        }
    prepared = _prepare_image_for_api(source_path, max_edge=max_edge)
    if prepared is None:
        return {
            "status": "skipped",
            "reason": reason,
            "fallback_reason": "unsupported_or_unreadable_image",
            "llm_call": False,
            "cache_status": "skipped",
            "warnings": ["unsupported_or_unreadable_image"],
        }

    prepared_path, image_meta = prepared
    try:
        prompt = _semantic_layout_vision_prompt(deck, page, local_result, reason)
        cache_key_payload = {
            "schema_version": LLM_CACHE_SCHEMA_VERSION,
            "prompt_version": SEMANTIC_LAYOUT_PROMPT_VERSION,
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
            "reason": reason,
            "cache_key": cache_key,
            "cache_file": _display_path(cache_path, output_root),
            "image_meta": image_meta,
            "warnings": [],
            "invalid_references": [],
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
                system_prompt=SEMANTIC_LAYOUT_SYSTEM_PROMPT,
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
                    "prompt_version": SEMANTIC_LAYOUT_PROMPT_VERSION,
                    "slide_id": page.slide_id,
                    "reason": reason,
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
            record["fallback_reason"] = "model_output_not_json"
            record["warnings"].append("model_output_not_json")
            return record
        enhanced, validation = _validated_vision_layout(page, local_result, parsed)
        record["warnings"].extend(validation["warnings"])
        record["invalid_references"] = validation["invalid_references"]
        if enhanced is None:
            record["fallback_reason"] = validation["fallback_reason"]
            return record
        record.update(
            {
                "status": "applied",
                "result": enhanced,
                "confidence": _as_float(parsed.get("confidence"), _layout_confidence(page, enhanced)),
                "model_reason": str(parsed.get("reason") or "").strip(),
            }
        )
        return record
    except Exception as exc:
        return {
            "status": "fallback",
            "reason": reason,
            "fallback_reason": "vision_call_failed",
            "llm_call": False,
            "cache_status": "error",
            "warnings": [f"vision_call_failed:{type(exc).__name__}"],
        }
    finally:
        _cleanup_temp_image(prepared_path)


def _semantic_layout_vision_prompt(deck: Deck, page: SlidePage, local_result: dict[str, Any], reason: str) -> str:
    payload = {
        "task": "semantic_layout_vision_enhancement",
        "instructions": [
            "Return JSON only.",
            "Keep the existing blocks/groups/relations shape.",
            "Use only ids from local.blocks[].id when creating groups and relations.",
            "Group complete teaching scenes: code plus output, blue callout boxes, red causal annotations, arrows, tables, and nearby explanations.",
            "Prefer full semantic groups over isolated tiny visual fragments.",
        ],
        "output_schema": {
            "confidence": 0.0,
            "reason": "short reason",
            "warnings": [],
            "blocks": [
                {
                    "id": "existing block id",
                    "block_type": "title/code/output/cause_explanation/fix/annotation/table/figure/image/explanation",
                    "learning_role": "structural/code_example/runtime_output/cause/fix/visual_annotation/visual_evidence/concept",
                    "must_explain": True,
                    "importance_score": 0.0,
                    "crop_policy": "text_or_group_scene/use_existing_image/text_only/skip_crop",
                }
            ],
            "groups": [
                {
                    "group_id": "optional existing-or-new id",
                    "scene_type": "code_causal_explanation/code_example_with_output/table_explanation/visual_explanation/concept_explanation",
                    "learning_goal": "what this group teaches",
                    "block_ids": ["existing block ids"],
                    "crop_policy": "prefer_structured_text_then_group_image/group_image_near_explanation/no_individual_crop",
                    "must_explain": True,
                    "importance_score": 0.0,
                }
            ],
            "relations": [
                {
                    "from": "existing block id",
                    "to": "existing block id",
                    "relation": "demonstrates/explained_by/fixes/leads_to_fix/annotates/anchors",
                    "reason": "visible or semantic cue",
                    "confidence": 0.0,
                }
            ],
        },
        "slide": {
            "slide_id": page.slide_id,
            "title": page.title,
            "page_modality": page.page_modality,
            "candidate_reason": reason,
            "page_size": {"width": page.page_width, "height": page.page_height},
            "page_ocr_text": _preview(page.page_ocr_text or "", 700),
            "page_visual_summary": _preview(page.page_visual_summary or "", 500),
        },
        "local": local_result,
        "element_ids": [str(block.get("id")) for block in local_result.get("blocks", [])],
        "source_type": deck.source_type,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _validated_vision_layout(
    page: SlidePage,
    local_result: dict[str, Any],
    parsed: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    warnings = [str(item) for item in parsed.get("warnings", []) if isinstance(item, (str, int, float))]
    invalid_refs: list[dict[str, Any]] = []
    local_blocks = [dict(block) for block in local_result.get("blocks", []) if isinstance(block, dict)]
    block_by_id = {str(block.get("id")): block for block in local_blocks if block.get("id") is not None}
    valid_ids = set(block_by_id)
    if not valid_ids:
        return None, {"warnings": warnings + ["no_local_blocks"], "invalid_references": invalid_refs, "fallback_reason": "no_local_blocks"}

    for update in parsed.get("blocks") or []:
        if not isinstance(update, dict):
            continue
        block_id = str(update.get("id") or "")
        if block_id not in block_by_id:
            invalid_refs.append({"kind": "block", "id": block_id})
            continue
        block = block_by_id[block_id]
        for key in ("block_type", "learning_role", "crop_policy", "preview"):
            if isinstance(update.get(key), str) and update.get(key):
                block[key] = update[key]
        if "must_explain" in update:
            block["must_explain"] = bool(update.get("must_explain"))
        if "importance_score" in update:
            block["importance_score"] = round(max(0.0, min(1.0, _as_float(update.get("importance_score"), block.get("importance_score") or 0.0))), 3)

    groups: list[dict[str, Any]] = []
    for index, group in enumerate(parsed.get("groups") or [], start=1):
        if not isinstance(group, dict):
            continue
        raw_ids = group.get("block_ids") or group.get("element_ids") or []
        if not isinstance(raw_ids, list):
            raw_ids = []
        block_ids: list[str] = []
        for raw_id in raw_ids:
            block_id = str(raw_id)
            if block_id in valid_ids and block_id not in block_ids:
                block_ids.append(block_id)
            else:
                invalid_refs.append({"kind": "group_block", "id": block_id, "group_id": group.get("group_id")})
        if not block_ids:
            warnings.append("discarded_group_without_valid_block_ids")
            continue
        group_id = str(group.get("group_id") or f"p{page.slide_id}_vsg{index}")
        record = {
            "group_id": group_id,
            "slide_id": page.slide_id,
            "scene_type": str(group.get("scene_type") or "visual_explanation"),
            "learning_goal": str(group.get("learning_goal") or _preview(" ".join(str(block_by_id[item].get("preview") or "") for item in block_ids), 180)),
            "block_ids": block_ids,
            "source_element_ids": _unique_ids(source_id for item in block_ids for source_id in block_by_id[item].get("source_element_ids", [])),
            "must_explain": bool(group.get("must_explain", True)),
            "crop_policy": str(group.get("crop_policy") or "group_image_near_explanation"),
            "importance_score": round(max(0.0, min(1.0, _as_float(group.get("importance_score"), 0.7))), 3),
            "method": "vision_enhanced_v1",
        }
        groups.append(record)

    if not groups and parsed.get("groups"):
        return None, {
            "warnings": warnings + ["no_valid_vision_groups"],
            "invalid_references": invalid_refs,
            "fallback_reason": "no_valid_vision_groups",
        }
    if not groups:
        groups = [dict(group) for group in local_result.get("groups", []) if isinstance(group, dict)]

    for block in local_blocks:
        block["group_id"] = None
    for group in groups:
        for block_id in group.get("block_ids") or []:
            if block_id in block_by_id:
                block_by_id[block_id]["group_id"] = group.get("group_id")

    relations: list[dict[str, Any]] = []
    for relation in parsed.get("relations") or []:
        if not isinstance(relation, dict):
            continue
        source_id = str(relation.get("from") or relation.get("source") or "")
        target_id = str(relation.get("to") or relation.get("target") or "")
        invalid = False
        if source_id not in valid_ids:
            invalid_refs.append({"kind": "relation_from", "id": source_id})
            invalid = True
        if target_id not in valid_ids:
            invalid_refs.append({"kind": "relation_to", "id": target_id})
            invalid = True
        if invalid:
            continue
        relations.append(
            {
                "from": source_id,
                "to": target_id,
                "relation": str(relation.get("relation") or "related_to"),
                "reason": str(relation.get("reason") or "vision_semantic_layout"),
                "confidence": round(max(0.0, min(1.0, _as_float(relation.get("confidence"), 0.65))), 3),
                "method": "vision_enhanced_v1",
            }
        )
    if not relations:
        relations = [dict(relation) for relation in local_result.get("relations", []) if isinstance(relation, dict)]

    if invalid_refs:
        warnings.append("discarded_invalid_model_references")
    return {"blocks": local_blocks, "groups": groups, "relations": relations}, {
        "warnings": warnings,
        "invalid_references": invalid_refs,
        "fallback_reason": None,
    }


def _page_needs_vision_enhancement(page: SlidePage, local_result: dict[str, Any]) -> bool:
    if not page.page_screenshot:
        return False
    if page_has_hint(page, "vision_page_screenshot") or page_has_hint(page, "crop_figures_from_screenshot"):
        return True
    if page.page_modality in {"mixed", "image_only", "shape_diagram"}:
        return True
    has_images = any(not image.ignored for image in page.images)
    has_text_or_tables = bool(page.text_blocks or page.tables)
    if has_images and has_text_or_tables:
        return True
    block_types = {str(block.get("block_type")) for block in local_result.get("blocks", [])}
    roles = {str(block.get("learning_role")) for block in local_result.get("blocks", [])}
    if {"code", "output"} & block_types or {"cause", "fix", "causal_annotation", "visual_annotation"} & roles:
        return True
    return _layout_confidence(page, local_result) < 0.68


def _vision_candidate_reason(page: SlidePage, local_result: dict[str, Any]) -> str:
    if page_has_hint(page, "vision_page_screenshot"):
        return "page_modality_hint"
    if page.page_modality in {"mixed", "image_only", "shape_diagram"}:
        return f"{page.page_modality}_page"
    if any(not image.ignored for image in page.images) and (page.text_blocks or page.tables):
        return "image_text_mixed_page"
    block_types = {str(block.get("block_type")) for block in local_result.get("blocks", [])}
    roles = {str(block.get("learning_role")) for block in local_result.get("blocks", [])}
    if {"code", "output"} & block_types:
        return "code_or_output_page"
    if {"cause", "fix", "causal_annotation", "visual_annotation"} & roles:
        return "causal_annotation_page"
    return "low_confidence_layout"


def _layout_confidence(page: SlidePage, result: dict[str, Any]) -> float:
    blocks = result.get("blocks") or []
    groups = result.get("groups") or []
    must_blocks = [block for block in blocks if block.get("must_explain")]
    if not blocks:
        return 0.35 if page.page_screenshot else 0.2
    if must_blocks and not groups:
        return 0.52
    if groups:
        covered = {str(block_id) for group in groups for block_id in (group.get("block_ids") or [])}
        must_ids = {str(block.get("id")) for block in must_blocks}
        coverage = len(covered & must_ids) / max(1, len(must_ids))
        return round(0.62 + min(0.28, coverage * 0.28), 3)
    return 0.76


def _local_vision_status(page: SlidePage, mode: str, reason: str) -> dict[str, Any]:
    if mode == "local":
        status = "disabled"
    elif not page.page_screenshot:
        status = "local_only"
    else:
        status = "not_selected"
    return {"status": status, "reason": reason, "llm_call": False, "cache_status": None, "warnings": []}


def semantic_layout_for_prompt(page: SlidePage, limit: int = 6) -> dict[str, Any] | None:
    if not page.semantic_groups and not page.semantic_blocks:
        return None
    return {
        "blocks": [
            {
                "id": block.get("id"),
                "block_type": block.get("block_type"),
                "learning_role": block.get("learning_role"),
                "must_explain": block.get("must_explain"),
                "group_id": block.get("group_id"),
                "crop_policy": block.get("crop_policy"),
                "preview": block.get("preview"),
            }
            for block in page.semantic_blocks[: max(0, limit * 3)]
        ],
        "groups": [
            {
                "group_id": group.get("group_id"),
                "scene_type": group.get("scene_type"),
                "learning_goal": group.get("learning_goal"),
                "block_ids": group.get("block_ids"),
                "must_explain": group.get("must_explain"),
                "crop_policy": group.get("crop_policy"),
            }
            for group in page.semantic_groups[:limit]
        ],
        "relations": page.semantic_relations[: limit * 2],
    }


def semantic_context_for_page(page: SlidePage, limit: int = 900) -> str:
    if not page.semantic_groups:
        return ""
    pieces: list[str] = []
    for group in page.semantic_groups[:4]:
        pieces.append(
            f"{group.get('group_id')}({group.get('scene_type')}, {group.get('crop_policy')}): "
            f"{group.get('learning_goal')} -> {', '.join(group.get('block_ids') or [])}"
        )
    for relation in page.semantic_relations[:6]:
        pieces.append(
            f"relation {relation.get('from')} {relation.get('relation')} {relation.get('to')}: {relation.get('reason')}"
        )
    text = "\n".join(piece for piece in pieces if piece.strip())
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _text_block_record(deck: Deck, page: SlidePage, block: TextBlock) -> dict[str, Any]:
    block_type = _classify_text_block(block)
    learning_role = _learning_role_for_block(block_type, block.content)
    must_explain = learning_role not in {"structural", "decorative"}
    bbox = _normalize_bbox(deck.source_type, block.bbox, (page.page_width, page.page_height))
    return {
        "id": block.id,
        "kind": "text",
        "source_element_ids": [block.id],
        "block_type": block_type,
        "learning_role": learning_role,
        "must_explain": must_explain,
        "importance_score": _importance_score(block_type, block.content),
        "bbox": bbox,
        "layout_order": _order_from_bbox(bbox),
        "crop_policy": _crop_policy_for_block(block_type),
        "preview": _preview(block.content),
        "group_id": None,
    }


def _table_block_record(deck: Deck, page: SlidePage, table: TableBlock) -> dict[str, Any]:
    bbox = _normalize_bbox(deck.source_type, table.bbox, (page.page_width, page.page_height))
    return {
        "id": table.id,
        "kind": "table",
        "source_element_ids": [table.id],
        "block_type": "table",
        "learning_role": "table_conclusion",
        "must_explain": True,
        "importance_score": 0.82,
        "bbox": bbox,
        "layout_order": _order_from_bbox(bbox),
        "crop_policy": "text_summary",
        "preview": table_preview(table, limit=220),
        "group_id": None,
    }


def _image_block_record(deck: Deck, page: SlidePage, image: ImageAsset) -> dict[str, Any]:
    bbox = _normalize_bbox(deck.source_type, image.crop_bbox or image.bbox, (page.page_width, page.page_height))
    block_type = "figure" if image.role in {"figure_crop", "composite_figure"} else image.role or "image"
    return {
        "id": image.id,
        "kind": "image",
        "source_element_ids": [image.id, *image.source_element_ids],
        "block_type": block_type,
        "learning_role": "visual_evidence",
        "must_explain": image.role in {"figure_crop", "composite_figure"} or (image.importance_score or 0.0) >= 0.55,
        "importance_score": image.importance_score if image.importance_score is not None else 0.55,
        "bbox": bbox,
        "layout_order": image.layout_order if image.layout_order is not None else _order_from_bbox(bbox),
        "crop_policy": "use_existing_image",
        "preview": _preview(" ".join(part for part in [image.caption, image.figure_explanation, image.visual_summary, image.ocr_text] if part) or image.path),
        "group_id": None,
    }


def _semantic_groups(page: SlidePage, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    key_blocks = [block for block in blocks if block.get("must_explain")]
    if not key_blocks:
        return []
    clusters = _cluster_key_blocks(key_blocks)
    groups: list[dict[str, Any]] = []
    for index, cluster in enumerate(clusters, start=1):
        group_id = f"p{page.slide_id}_sg{index}"
        scene_type = _scene_type(cluster)
        crop_policy = _group_crop_policy(cluster, scene_type)
        learning_goal = _learning_goal(cluster, scene_type)
        for block in cluster:
            block["group_id"] = group_id
        groups.append(
            {
                "group_id": group_id,
                "slide_id": page.slide_id,
                "scene_type": scene_type,
                "learning_goal": learning_goal,
                "block_ids": [str(block["id"]) for block in cluster],
                "source_element_ids": _unique_ids(source_id for block in cluster for source_id in block.get("source_element_ids", [])),
                "must_explain": any(block.get("must_explain") for block in cluster),
                "crop_policy": crop_policy,
                "importance_score": round(max(float(block.get("importance_score") or 0.0) for block in cluster), 3),
            }
        )
    return groups


def _cluster_key_blocks(blocks: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    if _has_code_causal_scene(blocks):
        return [blocks]
    sorted_blocks = sorted(blocks, key=lambda block: (_layout_order(block), str(block["id"])))
    clusters: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    previous_bottom: float | None = None
    for block in sorted_blocks:
        bbox = block.get("bbox")
        top = float(bbox[1]) if isinstance(bbox, list) and len(bbox) == 4 else None
        bottom = float(bbox[3]) if isinstance(bbox, list) and len(bbox) == 4 else None
        if current and top is not None and previous_bottom is not None and top - previous_bottom > 0.16:
            clusters.append(current)
            current = []
        current.append(block)
        if bottom is not None:
            previous_bottom = max(previous_bottom or 0.0, bottom)
    if current:
        clusters.append(current)
    return clusters


def _semantic_relations(blocks: list[dict[str, Any]], groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    del groups
    relations: list[dict[str, Any]] = []
    code_blocks = [block for block in blocks if block.get("block_type") == "code"]
    output_blocks = [block for block in blocks if block.get("block_type") == "output"]
    cause_blocks = [block for block in blocks if block.get("learning_role") == "cause"]
    fix_blocks = [block for block in blocks if block.get("learning_role") == "fix"]
    for code in code_blocks[:2]:
        for output in output_blocks[:2]:
            relations.append(_relation(code, output, "demonstrates", "代码块对应运行输入输出现象"))
        for cause in cause_blocks[:2]:
            relations.append(_relation(code, cause, "explained_by", "解释块说明代码行为背后的原因"))
        for fix in fix_blocks[:2]:
            relations.append(_relation(fix, code, "fixes", "修复提示作用于代码示例"))
    for cause in cause_blocks[:2]:
        for fix in fix_blocks[:2]:
            relations.append(_relation(cause, fix, "leads_to_fix", "原因解释导向修复方法"))
    return relations[:10]


def _relation(source: dict[str, Any], target: dict[str, Any], relation: str, reason: str) -> dict[str, Any]:
    return {
        "from": source.get("id"),
        "to": target.get("id"),
        "relation": relation,
        "reason": reason,
        "confidence": 0.72,
    }


def _classify_text_block(block: TextBlock) -> str:
    text = block.content.strip()
    lowered = text.lower()
    if block.type == "title":
        return "title"
    if _has_explanatory_language(text) and _contains_fix_signal(text):
        return "fix"
    if _has_explanatory_language(text) and _contains_cause_signal(text):
        return "cause_explanation"
    if _looks_like_code(text):
        return "code"
    if _looks_like_output(text):
        return "output"
    if _contains_fix_signal(text):
        return "fix"
    if _contains_cause_signal(text):
        return "cause_explanation"
    if _contains_visual_annotation(text):
        return "annotation"
    if "include" in lowered and len(text) < 80:
        return "code"
    if block.type in {"heading", "bullet"} and len(text) <= 80:
        return "concept_label"
    return "explanation"


def _looks_like_code(text: str) -> bool:
    signals = [
        "#include",
        "using namespace",
        "main()",
        "cout",
        "cin",
        "getline",
        "std::",
        "return ",
        "char ",
        "string ",
        "int ",
    ]
    lowered = text.lower()
    if any(signal in lowered for signal in signals):
        return True
    code_chars = sum(text.count(char) for char in "{};<>=")
    return code_chars >= 4 and bool(re.search(r"\b(if|for|while|void|int|char|string|cout|cin)\b", lowered))


def _looks_like_output(text: str) -> bool:
    lowered = text.lower()
    if re.search(r"\benter\s+(your|student)", lowered):
        return True
    return any(signal in text for signal in ["Data Entered", "Student Number", "Student Name", "Hello John", "输入", "输出"]) and ":" in text


def _contains_cause_signal(text: str) -> bool:
    return bool(re.search(r"因为|由于|导致|所以|因此|异常|留在|依然|被接下来|流提取|换行符|空白字符|缓冲|before|after", text, re.IGNORECASE))


def _contains_fix_signal(text: str) -> bool:
    return bool(re.search(r"cin\.ignore|ignore\(|需要|必须|清空|清除|丢弃|解决|修复|避免|之前|之后", text, re.IGNORECASE))


def _contains_visual_annotation(text: str) -> bool:
    return bool(re.search(r"×|√|->|=>|注意|提示|关键|红色|蓝框", text, re.IGNORECASE))


def _has_explanatory_language(text: str) -> bool:
    cjk_count = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    return cjk_count >= 4 or bool(re.search(r"\b(because|therefore|need|before|after|fix|causes?)\b", text, re.IGNORECASE))


def _learning_role_for_block(block_type: str, text: str) -> str:
    if block_type == "title":
        return "structural"
    if block_type == "code":
        return "code_example"
    if block_type == "output":
        return "runtime_output"
    if block_type == "fix":
        return "fix"
    if block_type == "cause_explanation":
        return "cause"
    if block_type == "annotation":
        return "causal_annotation" if _contains_cause_signal(text) or _contains_fix_signal(text) else "visual_annotation"
    return "concept"


def _importance_score(block_type: str, text: str) -> float:
    base = {
        "title": 0.2,
        "code": 0.86,
        "output": 0.78,
        "fix": 0.88,
        "cause_explanation": 0.86,
        "annotation": 0.76,
        "concept_label": 0.58,
        "explanation": 0.62,
    }.get(block_type, 0.5)
    if len(text) >= 160:
        base += 0.04
    return round(min(1.0, base), 3)


def _crop_policy_for_block(block_type: str) -> str:
    if block_type in {"code", "output", "fix", "cause_explanation", "annotation"}:
        return "text_or_group_scene"
    if block_type == "title":
        return "skip_crop"
    return "text_only"


def _has_code_causal_scene(blocks: list[dict[str, Any]]) -> bool:
    types = {str(block.get("block_type")) for block in blocks}
    roles = {str(block.get("learning_role")) for block in blocks}
    return "code" in types and bool({"runtime_output", "cause", "fix", "causal_annotation"} & roles)


def _scene_type(blocks: list[dict[str, Any]]) -> str:
    types = {str(block.get("block_type")) for block in blocks}
    roles = {str(block.get("learning_role")) for block in blocks}
    if "code" in types and "fix" in roles:
        return "code_causal_explanation"
    if "code" in types and "runtime_output" in roles:
        return "code_example_with_output"
    if "table" in types:
        return "table_explanation"
    if "figure" in types or "image" in types:
        return "visual_explanation"
    return "concept_explanation"


def _group_crop_policy(blocks: list[dict[str, Any]], scene_type: str) -> str:
    if scene_type.startswith("code_"):
        return "prefer_structured_text_then_group_image"
    if any(block.get("kind") == "image" for block in blocks):
        return "group_image_near_explanation"
    return "no_individual_crop"


def _learning_goal(blocks: list[dict[str, Any]], scene_type: str) -> str:
    previews = [str(block.get("preview") or "") for block in blocks]
    text = " ".join(previews)
    if scene_type == "code_causal_explanation":
        if re.search(r"getline|cin|换行符|空白字符|ignore", text, re.IGNORECASE):
            return "讲清 cin 提取运算符与 getline 混用时的换行符残留问题、现象和修复方法。"
        return "讲清代码示例的运行现象、原因和修复方法。"
    if scene_type == "code_example_with_output":
        return "把代码与运行输出对应起来，说明示例验证了什么行为。"
    if scene_type == "table_explanation":
        return "提炼表格结论，而不是逐格复述表格内容。"
    if scene_type == "visual_explanation":
        return "将图示与邻近概念合并讲解，避免把图单独堆放。"
    return _preview(text, 140) or "讲解本组核心概念。"


def _normalize_bbox(source_type: str, bbox: list[float] | None, page_size: tuple[float | None, float | None] | None) -> list[float] | None:
    if not bbox or len(bbox) != 4:
        return None
    if all(-0.001 <= float(value) <= 1.001 for value in bbox):
        return _clamp_bbox(bbox)
    width, height = page_size or (None, None)
    if not width or not height:
        return None
    x1, y1, third, fourth = [float(value) for value in bbox]
    if source_type == "pptx":
        x2, y2 = x1 + third, y1 + fourth
    else:
        x2, y2 = third, fourth
    return _clamp_bbox([x1 / width, y1 / height, x2 / width, y2 / height])


def _page_size_for_bbox(deck: Deck, page: SlidePage | None) -> tuple[float | None, float | None] | None:
    if page is not None:
        return page.page_width, page.page_height
    return None


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


def _layout_order(block: dict[str, Any]) -> float:
    try:
        return float(block.get("layout_order"))
    except (TypeError, ValueError):
        return 9999.0


def _unique_ids(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value)
        if text and text not in seen:
            result.append(text)
            seen.add(text)
    return result


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


def _display_path(path: Path | None, output_root: Path | None) -> str | None:
    if path is None:
        return None
    if output_root is None:
        return str(path)
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


def _preview(text: str, limit: int = 180) -> str:
    value = re.sub(r"\s+", " ", text).strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"
