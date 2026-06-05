from __future__ import annotations

import json
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from slidenote.image_assets import image_metadata
from slidenote.llm import LLMClient, resolve_provider_runtime
from slidenote.llm_cache import LLM_CACHE_SCHEMA_VERSION, LLMCache, make_cache_key, sha256_text, stable_json, utc_now_iso
from slidenote.modality import page_has_hint
from slidenote.models import Deck, ImageAsset, SlidePage, normalize_rel_path
from slidenote.semantic_layout import semantic_context_for_page, semantic_layout_for_prompt
from slidenote.table_understanding import table_preview

FIGURE_PROMPT_VERSION = "figure-crop-v1"

FIGURE_SYSTEM_PROMPT = (
    "你是课程幻灯片版面分析助手。请只返回 JSON。任务是找出页面中适合单独放入笔记的局部图，"
    "例如流程图、架构图、示意图、截图、公式图、图表和带标注的视觉区域。不要返回整页区域、纯标题、普通正文段落或页码。"
)

CODE_CROP_MIN_CONFIDENCE = 0.85


@dataclass(frozen=True, slots=True)
class FigureTarget:
    slide_id: int
    path: str
    reason: str


@dataclass(frozen=True, slots=True)
class NormalizedFigure:
    bbox: list[float]
    label: str
    content_type: str
    confidence: float


@dataclass(frozen=True, slots=True)
class CropRefinement:
    figure: NormalizedFigure
    original_bbox: list[float]
    quality: str
    warnings: list[str]
    blocking_reason: str | None = None


def enrich_deck_with_figures(
    deck: Deck,
    output_root: Path,
    mode: str = "auto",
    provider: str = "openai",
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    cache_mode: str = "on",
    cache_dir: Path | None = None,
    max_targets: int = 80,
    max_crops_per_page: int = 3,
    min_confidence: float = 0.45,
    min_area: int = 40_000,
    max_output_tokens: int = 1000,
    temperature: float | None = 0.0,
    detail: str = "low",
    max_edge: int = 1400,
    concurrency: int = 1,
    refresh_slide_ids: set[int] | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any] | None:
    if mode == "off":
        return None
    if mode not in {"auto", "vision"}:
        raise ValueError("figure crop mode must be one of: off, auto, vision")

    runtime = resolve_provider_runtime(provider, model=model, base_url=base_url, for_vision=True)
    if not runtime["supports_image_input"]:
        raise RuntimeError(f"Figure crop provider `{runtime['provider']}` does not support image input.")

    targets = select_figure_targets(deck, max_targets=max_targets)
    resolved_cache_dir = (cache_dir or (output_root / ".cache" / "figure")).resolve()
    cache = LLMCache(resolved_cache_dir, mode=cache_mode)
    figures_dir = output_root / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)
    records_by_index: dict[int, dict[str, Any]] = {}
    refresh_ids = refresh_slide_ids or set()
    workers = max(1, int(concurrency or 1))

    if progress_callback:
        progress_callback({"event": "start", "total": len(targets)})

    def process(index: int, target: FigureTarget) -> tuple[int, FigureTarget, dict[str, Any], list[ImageAsset]]:
        page = _page_by_id(deck, target.slide_id)
        record, crops = _process_figure_target(
            target=target,
            page=page,
            output_root=output_root,
            figures_dir=figures_dir,
            cache=cache,
            cache_mode=cache_mode,
            runtime=runtime,
            api_key=api_key,
            source_type=deck.source_type,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            detail=detail,
            max_edge=max_edge,
            max_crops_per_page=max_crops_per_page,
            min_confidence=min_confidence,
            min_area=min_area,
            force_refresh=target.slide_id in refresh_ids,
        )
        return index, target, record, crops

    results = []
    if workers == 1:
        for index, target in enumerate(targets):
            result = process(index, target)
            results.append(result)
            if progress_callback:
                _, completed_target, record, _ = result
                progress_callback({"event": "advance", "record": record, "slide_id": completed_target.slide_id})
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(process, index, target): (index, target) for index, target in enumerate(targets)}
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                if progress_callback:
                    _, completed_target, record, _ = result
                    progress_callback({"event": "advance", "record": record, "slide_id": completed_target.slide_id})

    for index, target, record, crops in sorted(results, key=lambda item: item[0]):
        page = _page_by_id(deck, target.slide_id)
        if page is not None:
            page.images.extend(crops)
        records_by_index[index] = record

    records = [records_by_index[index] for index in sorted(records_by_index)]
    return _build_report(deck, runtime, mode, cache_mode, resolved_cache_dir, output_root, records, max_targets, max_crops_per_page, min_confidence, min_area)


def select_figure_targets(deck: Deck, max_targets: int = 80) -> list[FigureTarget]:
    targets: list[FigureTarget] = []
    for page in deck.pages:
        if not page.page_screenshot:
            continue
        if _page_has_content_images(page):
            continue
        if page_has_hint(page, "crop_figures_from_screenshot"):
            targets.append(FigureTarget(page.slide_id, page.page_screenshot, reason=f"{page.page_modality}_modality"))
        elif _page_deserves_figure_crop(page):
            targets.append(FigureTarget(page.slide_id, page.page_screenshot, reason="needs_local_figure_crop"))
    if max_targets > 0:
        return targets[:max_targets]
    return targets


def _process_figure_target(
    target: FigureTarget,
    page: SlidePage | None,
    output_root: Path,
    figures_dir: Path,
    cache: LLMCache,
    cache_mode: str,
    runtime: dict[str, Any],
    api_key: str | None,
    source_type: str,
    max_output_tokens: int,
    temperature: float | None,
    detail: str,
    max_edge: int,
    max_crops_per_page: int,
    min_confidence: float,
    min_area: int,
    force_refresh: bool,
) -> tuple[dict[str, Any], list[ImageAsset]]:
    source_path = (output_root / target.path).resolve()
    if not source_path.exists():
        return _skipped_record(target, "missing_file", output_root), []

    prepared = _prepare_image_for_api(source_path, max_edge=max_edge)
    if prepared is None:
        return _skipped_record(target, "unsupported_or_unreadable_image", output_root), []

    prepared_path, image_meta = prepared
    try:
        prompt = _figure_prompt(target, page)
        image_hash = _file_sha256(source_path)
        cache_key_payload = {
            "schema_version": LLM_CACHE_SCHEMA_VERSION,
            "prompt_version": FIGURE_PROMPT_VERSION,
            "provider": runtime["provider"],
            "model": runtime["model"],
            "base_url": runtime["base_url"],
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
            "detail": detail,
            "max_edge": max_edge,
            "source_path": target.path,
            "source_image_hash": image_hash,
            "prompt_hash": sha256_text(prompt),
            "max_crops_per_page": max_crops_per_page,
            "min_confidence": min_confidence,
            "min_area": min_area,
        }
        cache_key = make_cache_key(cache_key_payload)
        cache_path = cache.path_for(cache_key)
        cached = None if force_refresh else cache.read(cache_key)
        record = _base_record(target, cache_key, cache_path, output_root, image_meta)

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
            llm_result = client.generate_image_with_usage(prepared_path, prompt, system_prompt=FIGURE_SYSTEM_PROMPT, image_detail=detail)
            result_json = llm_result.text
            response_usage = llm_result.usage or {}
            cache_status = "disabled" if cache_mode == "off" else "refresh" if cache_mode == "refresh" or force_refresh else "miss"
            written_path = cache.write(
                cache_key,
                {
                    "provider": runtime["provider"],
                    "model": runtime["model"],
                    "base_url": runtime["base_url"],
                    "prompt_version": FIGURE_PROMPT_VERSION,
                    "slide_id": target.slide_id,
                    "target": {"slide_id": target.slide_id, "path": target.path, "reason": target.reason},
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
                    "llm_call": True,
                    "api_retries": response_usage.get("retries", 0),
                    "input_tokens": response_usage.get("input_tokens"),
                    "output_tokens": response_usage.get("output_tokens"),
                    "total_tokens": response_usage.get("total_tokens"),
                    "provider_cached_input_tokens": response_usage.get("provider_cached_input_tokens"),
                    "provider_usage": response_usage,
                }
            )

        parsed = _parse_figure_json(result_json)
        crops, crop_records, skipped_candidates = _crop_figures(
            source_path=source_path,
            output_root=output_root,
            figures_dir=figures_dir,
            target=target,
            figures=parsed.get("figures", []),
            max_crops_per_page=max_crops_per_page,
            min_confidence=min_confidence,
            min_area=min_area,
            start_index=_next_figure_index(page),
            page=page,
            source_type=source_type,
        )
        record["result"] = parsed
        record["crops"] = crop_records
        record["crops_created"] = len(crops)
        record["skipped_candidates"] = skipped_candidates
        record["cache_file"] = _display_path(cache_path, output_root)
        return record, crops
    finally:
        _cleanup_temp_image(prepared_path)


def _crop_figures(
    source_path: Path,
    output_root: Path,
    figures_dir: Path,
    target: FigureTarget,
    figures: list[dict[str, Any]],
    max_crops_per_page: int,
    min_confidence: float,
    min_area: int,
    start_index: int = 1,
    page: SlidePage | None = None,
    source_type: str | None = None,
) -> tuple[list[ImageAsset], list[dict[str, Any]], list[dict[str, Any]]]:
    crops: list[ImageAsset] = []
    crop_records: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    accepted: list[list[float]] = []
    try:
        figures_dir.mkdir(parents=True, exist_ok=True)
        with Image.open(source_path) as image:
            width, height = image.width, image.height
            image = image.convert("RGB")
            normalized_candidates: list[tuple[dict[str, Any], NormalizedFigure]] = []
            for candidate in figures:
                normalized = _normalize_candidate(candidate, width=width, height=height)
                if normalized is None:
                    skipped.append({"reason": "invalid_bbox", "candidate": candidate})
                    continue
                normalized_candidates.append((candidate, normalized))
            all_figures = [normalized for _, normalized in normalized_candidates]
            for candidate, normalized in normalized_candidates:
                refinement = _refine_crop_candidate(
                    image,
                    normalized,
                    all_figures,
                    width=width,
                    height=height,
                    page=page,
                    source_type=source_type,
                )
                if refinement.blocking_reason:
                    skipped.append(
                        {
                            "reason": refinement.blocking_reason,
                            "candidate": candidate,
                            "bbox": refinement.figure.bbox,
                            "original_bbox": refinement.original_bbox,
                            "crop_quality": refinement.quality,
                            "crop_warnings": refinement.warnings,
                        }
                    )
                    continue
                normalized = refinement.figure
                text_region_reason = _structured_text_region_skip_reason(page, normalized, source_type)
                if text_region_reason:
                    skipped.append(
                        {
                            "reason": text_region_reason,
                            "candidate": candidate,
                            "bbox": normalized.bbox,
                            "original_bbox": refinement.original_bbox,
                            "crop_quality": refinement.quality,
                            "crop_warnings": refinement.warnings,
                        }
                    )
                    continue
                reason = _skip_reason(normalized, accepted, width=width, height=height, min_confidence=min_confidence, min_area=min_area)
                if reason:
                    skipped.append(
                        {
                            "reason": reason,
                            "candidate": candidate,
                            "bbox": normalized.bbox,
                            "original_bbox": refinement.original_bbox,
                            "crop_quality": refinement.quality,
                            "crop_warnings": refinement.warnings,
                        }
                    )
                    continue
                crop_index = max(1, start_index) + len(crops)
                pixel_box = _pixel_box(normalized.bbox, width, height)
                crop = image.crop(pixel_box)
                crop_path = figures_dir / f"slide{target.slide_id}_fig{crop_index}.png"
                crop.save(crop_path, format="PNG")
                meta = image_metadata(crop_path)
                image_asset = ImageAsset(
                    id=f"s{target.slide_id}_fig{crop_index}",
                    path=normalize_rel_path(crop_path, output_root),
                    caption=normalized.label or f"第 {target.slide_id} 页局部图 {crop_index}",
                    bbox=[float(value) for value in pixel_box],
                    source_format="cropped_png",
                    width=meta["width"],
                    height=meta["height"],
                    file_size=meta["file_size"],
                    role="figure_crop",
                    ignored=False,
                    crop_source_path=target.path,
                    crop_bbox=normalized.bbox,
                    crop_method="vision_bbox_refined" if normalized.bbox != refinement.original_bbox else "vision_bbox",
                    crop_quality=refinement.quality,
                    crop_warnings=list(refinement.warnings),
                    confidence=normalized.confidence,
                )
                crops.append(image_asset)
                accepted.append(normalized.bbox)
                crop_records.append(
                    {
                        "id": image_asset.id,
                        "path": image_asset.path,
                        "bbox": normalized.bbox,
                        "original_bbox": refinement.original_bbox,
                        "pixel_bbox": [float(value) for value in pixel_box],
                        "label": normalized.label,
                        "content_type": normalized.content_type,
                        "confidence": normalized.confidence,
                        "crop_quality": refinement.quality,
                        "crop_warnings": refinement.warnings,
                    }
                )
                if len(crops) >= max_crops_per_page:
                    break
    except Exception as exc:
        skipped.append({"reason": "crop_failed", "error": str(exc)})
    return crops, crop_records, skipped


def _structured_text_region_skip_reason(page: SlidePage | None, figure: NormalizedFigure, source_type: str | None) -> str | None:
    if page is None:
        return None
    crop_area = _bbox_area(figure.bbox)
    if crop_area <= 0:
        return None
    text_boxes, visual_boxes = _page_layout_boxes(page, source_type)
    if not text_boxes:
        return None
    text_coverage = sum(_intersection_area(figure.bbox, box) for box in text_boxes) / crop_area
    visual_coverage = sum(_intersection_area(figure.bbox, box) for box in visual_boxes) / crop_area
    text_like_candidate = _is_text_crop_content(figure.content_type, figure.label)
    if text_like_candidate and text_coverage >= 0.18 and visual_coverage < 0.08:
        return "structured_text_region"
    if text_coverage >= 0.55 and visual_coverage < 0.12:
        return "structured_text_region"
    return None


def _page_layout_boxes(page: SlidePage, source_type: str | None) -> tuple[list[list[float]], list[list[float]]]:
    text_boxes: list[list[float]] = []
    visual_boxes: list[list[float]] = []
    if page.semantic_blocks:
        for block in page.semantic_blocks:
            bbox = block.get("bbox")
            if not _looks_normalized_bbox(bbox):
                continue
            kind = str(block.get("kind") or "")
            block_type = str(block.get("block_type") or "")
            if kind in {"text", "table"} or block_type in {"title", "concept_label", "explanation", "cause_explanation", "fix", "code", "output"}:
                text_boxes.append(_round_bbox([float(value) for value in bbox]))
            elif kind == "image" or block_type in {"figure", "image"}:
                visual_boxes.append(_round_bbox([float(value) for value in bbox]))
        if text_boxes or visual_boxes:
            return text_boxes, visual_boxes

    for block in page.text_blocks:
        bbox = _normalize_page_bbox(block.bbox, page, source_type)
        if bbox:
            text_boxes.append(bbox)
    for table in page.tables:
        bbox = _normalize_page_bbox(table.bbox, page, source_type)
        if bbox:
            text_boxes.append(bbox)
    for image in page.images:
        if image.ignored or image.role == "page_image":
            continue
        bbox = _normalize_page_bbox(image.crop_bbox or image.bbox, page, source_type)
        if bbox:
            visual_boxes.append(bbox)
    return text_boxes, visual_boxes


def _normalize_page_bbox(bbox: list[float] | None, page: SlidePage, source_type: str | None) -> list[float] | None:
    if not bbox or len(bbox) != 4:
        return None
    try:
        values = [float(value) for value in bbox]
    except (TypeError, ValueError):
        return None
    if all(-0.001 <= value <= 1.001 for value in values):
        return _round_bbox(values)
    width = page.page_width or 0.0
    height = page.page_height or 0.0
    if width <= 0 or height <= 0:
        return None
    x1, y1, third, fourth = values
    if source_type == "pptx":
        x2, y2 = x1 + third, y1 + fourth
    else:
        x2, y2 = third, fourth
    return _round_bbox([x1 / width, y1 / height, x2 / width, y2 / height])


def _looks_normalized_bbox(value: object) -> bool:
    return isinstance(value, list) and len(value) == 4 and all(isinstance(item, (int, float)) and -0.001 <= float(item) <= 1.001 for item in value)


def _is_text_crop_content(content_type: str, label: str) -> bool:
    text = f"{content_type} {label}".strip().lower()
    return any(token in text for token in ("text", "title", "heading", "paragraph", "bullet", "caption", "ocr", "word", "文字", "标题", "正文"))


def _bbox_area(bbox: list[float]) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _intersection_area(left: list[float], right: list[float]) -> float:
    x1 = max(left[0], right[0])
    y1 = max(left[1], right[1])
    x2 = min(left[2], right[2])
    y2 = min(left[3], right[3])
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def _normalize_candidate(candidate: dict[str, Any], width: int, height: int) -> NormalizedFigure | None:
    raw_bbox = candidate.get("bbox")
    if not isinstance(raw_bbox, list) or len(raw_bbox) != 4:
        return None
    try:
        values = [float(value) for value in raw_bbox]
    except (TypeError, ValueError):
        return None

    max_value = max(values)
    if max_value > 100:
        values = [values[0] / width, values[1] / height, values[2] / width, values[3] / height]
    elif max_value > 1.5:
        values = [value / 100 for value in values]

    x1, y1, x2, y2 = values
    x1, x2 = sorted((max(0.0, min(1.0, x1)), max(0.0, min(1.0, x2))))
    y1, y2 = sorted((max(0.0, min(1.0, y1)), max(0.0, min(1.0, y2))))
    if x2 <= x1 or y2 <= y1:
        return None
    confidence = _as_float(candidate.get("confidence"), default=0.0)
    label = str(candidate.get("label") or candidate.get("caption") or "").strip()
    content_type = str(candidate.get("content_type") or candidate.get("type") or "unknown").strip() or "unknown"
    return NormalizedFigure(bbox=[x1, y1, x2, y2], label=label, content_type=content_type, confidence=confidence)


def _refine_crop_candidate(
    image: Image.Image,
    figure: NormalizedFigure,
    all_figures: list[NormalizedFigure],
    width: int,
    height: int,
    page: SlidePage | None = None,
    source_type: str | None = None,
) -> CropRefinement:
    original_bbox = _round_bbox(figure.bbox)
    warnings: list[str] = []
    quality = "ok"
    x1, y1, x2, y2 = figure.bbox
    if (x2 - x1) * (y2 - y1) > 0.85:
        return CropRefinement(figure=figure, original_bbox=original_bbox, quality=quality, warnings=warnings)

    if _is_code_content(figure.content_type) and figure.confidence < CODE_CROP_MIN_CONFIDENCE:
        return CropRefinement(
            figure=figure,
            original_bbox=original_bbox,
            quality="code_deferred_to_ocr",
            warnings=["code_crop_requires_high_confidence"],
            blocking_reason="code_crop_deferred_to_ocr",
        )

    bbox = _limit_bbox_before_next_candidate(figure, all_figures, height=height)
    if bbox != figure.bbox:
        warnings.append("trimmed_before_next_candidate")
        quality = "trimmed_neighbor_overlap"

    crop = image.crop(_pixel_box(bbox, width, height))
    touched_edges = _foreground_touching_edges(crop)
    if touched_edges:
        expanded = _expand_bbox_for_edges(bbox, touched_edges, width=width, height=height)
        if expanded != bbox:
            bbox = expanded
            warnings.extend(f"expanded_{edge}_edge_touch" for edge in touched_edges)
            quality = _prefer_quality(quality, "expanded_edge_touch")
            crop = image.crop(_pixel_box(bbox, width, height))

    semantic_bbox = _semantic_group_bbox_for_candidate(page, figure, source_type)
    if semantic_bbox and _bbox_area(semantic_bbox) <= 0.72 and _bbox_area(semantic_bbox) > _bbox_area(bbox):
        bbox = _round_bbox(_union_bbox([bbox, semantic_bbox]))
        warnings.append("merged_semantic_group")
        quality = _prefer_quality(quality, "merged_semantic_group")
        crop = image.crop(_pixel_box(bbox, width, height))

    merged_context = _merge_nearby_context_boxes(bbox, page, source_type, figure)
    if merged_context[0] != bbox:
        bbox = merged_context[0]
        warnings.extend(merged_context[1])
        quality = _prefer_quality(quality, "merged_caption")
        crop = image.crop(_pixel_box(bbox, width, height))

    bands = _foreground_row_bands(crop)
    trim_bottom = _bottom_trim_from_bands(bands, crop.height)
    if trim_bottom is not None:
        pixel_box = _pixel_box(bbox, width, height)
        new_bottom = max(pixel_box[1] + 1, min(pixel_box[3], pixel_box[1] + trim_bottom))
        bbox = _round_bbox([bbox[0], bbox[1], bbox[2], new_bottom / height])
        warnings.append("trimmed_bottom_contamination")
        quality = "trimmed_bottom_contamination"
        crop = image.crop(_pixel_box(bbox, width, height))
        bands = _foreground_row_bands(crop)
    elif _looks_bottom_contaminated(bands, crop.height):
        return CropRefinement(
            figure=NormalizedFigure(bbox=_round_bbox(bbox), label=figure.label, content_type=figure.content_type, confidence=figure.confidence),
            original_bbox=original_bbox,
            quality="contaminated_unresolved",
            warnings=warnings + ["multiple_separated_foreground_bands"],
            blocking_reason="contaminated_crop",
        )

    if _is_code_content(figure.content_type) and _foreground_touches_vertical_edge(bands, crop.height):
        return CropRefinement(
            figure=NormalizedFigure(bbox=_round_bbox(bbox), label=figure.label, content_type=figure.content_type, confidence=figure.confidence),
            original_bbox=original_bbox,
            quality="code_edge_clipped",
            warnings=warnings + ["code_foreground_touches_crop_edge"],
            blocking_reason="code_crop_unstable",
        )

    refined = NormalizedFigure(bbox=_round_bbox(bbox), label=figure.label, content_type=figure.content_type, confidence=figure.confidence)
    return CropRefinement(figure=refined, original_bbox=original_bbox, quality=quality, warnings=warnings)


def _prefer_quality(current: str, candidate: str) -> str:
    if current == "ok":
        return candidate
    return current


def _foreground_touching_edges(crop: Image.Image) -> list[str]:
    width, height = crop.width, crop.height
    if width <= 4 or height <= 4:
        return []
    background = _estimate_background(crop)
    pixels = crop.load()
    margin_x = max(3, int(round(width * 0.018)))
    margin_y = max(3, int(round(height * 0.018)))
    center_area = max(1, (width - 2 * margin_x) * (height - 2 * margin_y))

    def density(x_start: int, x_end: int, y_start: int, y_end: int) -> float:
        total = max(1, (x_end - x_start) * (y_end - y_start))
        foreground = 0
        for y in range(y_start, y_end):
            for x in range(x_start, x_end):
                if _is_foreground_pixel(pixels[x, y], background):
                    foreground += 1
        return foreground / total

    center_density = density(margin_x, max(margin_x + 1, width - margin_x), margin_y, max(margin_y + 1, height - margin_y)) if center_area else 0.0
    threshold = max(0.025, min(0.12, center_density * 0.42))
    edges = []
    if density(0, margin_x, 0, height) >= threshold:
        edges.append("left")
    if density(width - margin_x, width, 0, height) >= threshold:
        edges.append("right")
    if density(0, width, 0, margin_y) >= threshold:
        edges.append("top")
    if density(0, width, height - margin_y, height) >= threshold:
        edges.append("bottom")
    return edges


def _expand_bbox_for_edges(bbox: list[float], edges: list[str], width: int, height: int) -> list[float]:
    x1, y1, x2, y2 = bbox
    x_margin = max(0.012, 16 / max(1, width))
    y_margin = max(0.012, 16 / max(1, height))
    if "left" in edges:
        x1 -= x_margin
    if "right" in edges:
        x2 += x_margin
    if "top" in edges:
        y1 -= y_margin
    if "bottom" in edges:
        y2 += y_margin
    return _round_bbox([x1, y1, x2, y2])


def _semantic_group_bbox_for_candidate(page: SlidePage | None, figure: NormalizedFigure, source_type: str | None) -> list[float] | None:
    if page is None or not page.semantic_groups or not page.semantic_blocks:
        return None
    block_boxes = {
        str(block.get("id")): _round_bbox([float(value) for value in block["bbox"]])
        for block in page.semantic_blocks
        if isinstance(block.get("id"), str) and _looks_normalized_bbox(block.get("bbox"))
    }
    if not block_boxes:
        return None
    figure_area = max(_bbox_area(figure.bbox), 0.0001)
    best: list[float] | None = None
    best_score = 0.0
    for group in page.semantic_groups:
        block_ids = [str(item) for item in group.get("block_ids") or []]
        boxes = [block_boxes[item] for item in block_ids if item in block_boxes]
        if not boxes:
            continue
        group_bbox = _round_bbox(_union_bbox(boxes))
        group_area = _bbox_area(group_bbox)
        if group_area <= figure_area or group_area > 0.72:
            continue
        overlap = _intersection_area(figure.bbox, group_bbox) / figure_area
        if overlap < 0.45:
            continue
        policy = str(group.get("crop_policy") or "")
        scene = str(group.get("scene_type") or "")
        if not any(token in f"{policy} {scene}" for token in ("group", "visual", "figure", "code", "table", "concept")):
            continue
        score = overlap / max(group_area, 0.0001)
        if score > best_score:
            best = group_bbox
            best_score = score
    return best


def _merge_nearby_context_boxes(
    bbox: list[float],
    page: SlidePage | None,
    source_type: str | None,
    figure: NormalizedFigure,
) -> tuple[list[float], list[str]]:
    if page is None:
        return bbox, []
    context_boxes = _nearby_context_text_boxes(page, source_type, figure)
    if not context_boxes:
        return bbox, []
    merged = list(bbox)
    warnings: list[str] = []
    for box, role in context_boxes:
        if _should_merge_context_box(merged, box, role, figure):
            merged = _round_bbox(_union_bbox([merged, box]))
            warning = "merged_nearby_title" if role == "title" else "merged_nearby_legend"
            if warning not in warnings:
                warnings.append(warning)
    return merged, warnings


def _nearby_context_text_boxes(page: SlidePage, source_type: str | None, figure: NormalizedFigure) -> list[tuple[list[float], str]]:
    boxes: list[tuple[list[float], str]] = []
    for block in page.text_blocks:
        bbox = _normalize_page_bbox(block.bbox, page, source_type)
        if not bbox:
            continue
        role = _context_text_role(block.type, block.content, figure)
        if role:
            boxes.append((bbox, role))
    for block in page.semantic_blocks:
        if not _looks_normalized_bbox(block.get("bbox")):
            continue
        role = _context_text_role(str(block.get("block_type") or ""), str(block.get("preview") or ""), figure)
        if role:
            boxes.append((_round_bbox([float(value) for value in block["bbox"]]), role))
    return boxes


def _context_text_role(block_type: str, content: str, figure: NormalizedFigure) -> str | None:
    text = " ".join(str(content or "").split())
    if not text:
        return None
    lowered = text.lower()
    type_text = str(block_type or "").lower()
    if len(text) > 120 and "title" not in type_text and "caption" not in type_text:
        return None
    title_tokens = ("title", "heading", "caption", "subtitle", "图", "表", "figure", "fig.", "chart", "diagram")
    legend_tokens = ("legend", "图例", "axis", "坐标", "曲线", "roc", "loss", "accuracy", "precision", "recall", "x轴", "y轴")
    if any(token in type_text for token in ("title", "heading", "caption")) or any(token in lowered for token in title_tokens):
        return "title"
    if _is_chart_like(figure.content_type) and any(token in lowered for token in legend_tokens):
        return "legend"
    if _is_chart_like(figure.content_type) and len(text) <= 48:
        return "legend"
    return None


def _should_merge_context_box(figure_bbox: list[float], context_bbox: list[float], role: str, figure: NormalizedFigure) -> bool:
    horizontal_overlap = _horizontal_overlap_ratio(figure_bbox, context_bbox)
    vertical_overlap = _vertical_overlap_ratio(figure_bbox, context_bbox)
    x_gap = max(0.0, max(context_bbox[0] - figure_bbox[2], figure_bbox[0] - context_bbox[2]))
    y_gap = max(0.0, max(context_bbox[1] - figure_bbox[3], figure_bbox[1] - context_bbox[3]))
    if role == "title":
        if y_gap <= 0.06 and horizontal_overlap >= 0.28:
            return True
        return _is_chart_like(figure.content_type) and y_gap <= 0.075 and horizontal_overlap >= 0.18
    if role == "legend":
        if y_gap <= 0.055 and horizontal_overlap >= 0.18:
            return True
        return x_gap <= 0.045 and vertical_overlap >= 0.22
    return False


def _vertical_overlap_ratio(left: list[float], right: list[float]) -> float:
    overlap = max(0.0, min(left[3], right[3]) - max(left[1], right[1]))
    denom = max(0.0001, min(left[3] - left[1], right[3] - right[1]))
    return overlap / denom


def _union_bbox(boxes: list[list[float]]) -> list[float]:
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def _is_chart_like(content_type: str) -> bool:
    lowered = content_type.strip().lower()
    return any(token in lowered for token in ("chart", "plot", "graph", "axis", "curve", "diagram", "table", "formula", "mixed"))


def _limit_bbox_before_next_candidate(figure: NormalizedFigure, all_figures: list[NormalizedFigure], height: int) -> list[float]:
    x1, y1, x2, y2 = figure.bbox
    margin = max(0.006, 8 / max(1, height))
    next_tops = [
        other.bbox[1]
        for other in all_figures
        if other is not figure
        and other.bbox[1] > y1 + margin
        and _horizontal_overlap_ratio(figure.bbox, other.bbox) >= 0.25
    ]
    if not next_tops:
        return list(figure.bbox)
    limited_y2 = min(y2, min(next_tops) - margin)
    if limited_y2 <= y1 + 0.04:
        return list(figure.bbox)
    return _round_bbox([x1, y1, x2, limited_y2])


def _foreground_row_bands(crop: Image.Image) -> list[tuple[int, int, float]]:
    width, height = crop.width, crop.height
    if width <= 1 or height <= 1:
        return []
    background = _estimate_background(crop)
    x_step = max(1, width // 650)
    samples_per_row = max(1, (width + x_step - 1) // x_step)
    densities: list[float] = []
    pixels = crop.load()
    for y in range(height):
        foreground = 0
        for x in range(0, width, x_step):
            if _is_foreground_pixel(pixels[x, y], background):
                foreground += 1
        densities.append(foreground / samples_per_row)
    max_density = max(densities) if densities else 0.0
    if max_density < 0.006:
        return []
    threshold = max(0.01, min(0.08, max_density * 0.16))
    active = [density >= threshold for density in densities]
    raw_bands: list[tuple[int, int, float]] = []
    start: int | None = None
    mass = 0.0
    for index, is_active in enumerate(active):
        if is_active:
            if start is None:
                start = index
                mass = 0.0
            mass += densities[index]
        elif start is not None:
            raw_bands.append((start, index, mass))
            start = None
            mass = 0.0
    if start is not None:
        raw_bands.append((start, height, mass))
    return _merge_row_bands(raw_bands, height)


def _merge_row_bands(bands: list[tuple[int, int, float]], height: int) -> list[tuple[int, int, float]]:
    if not bands:
        return []
    max_gap = max(4, int(round(height * 0.015)))
    merged: list[tuple[int, int, float]] = []
    current_start, current_end, current_mass = bands[0]
    for start, end, mass in bands[1:]:
        if start - current_end <= max_gap:
            current_end = end
            current_mass += mass
            continue
        merged.append((current_start, current_end, current_mass))
        current_start, current_end, current_mass = start, end, mass
    merged.append((current_start, current_end, current_mass))
    min_mass = max(0.08, height * 0.001)
    return [(start, end, mass) for start, end, mass in merged if end - start >= 2 and mass >= min_mass]


def _bottom_trim_from_bands(bands: list[tuple[int, int, float]], height: int) -> int | None:
    if len(bands) < 2:
        return None
    total_mass = sum(mass for _, _, mass in bands)
    if total_mass <= 0:
        return None
    min_gap = max(12, int(round(height * 0.045)))
    pad = max(4, int(round(height * 0.018)))
    preceding_mass = 0.0
    for index, band in enumerate(bands[:-1]):
        preceding_mass += band[2]
        next_band = bands[index + 1]
        gap = next_band[0] - band[1]
        trailing_mass = total_mass - preceding_mass
        if gap >= min_gap and next_band[0] >= height * 0.45 and preceding_mass >= total_mass * 0.35 and trailing_mass <= total_mass * 0.7:
            return min(height, band[1] + pad)
    return None


def _looks_bottom_contaminated(bands: list[tuple[int, int, float]], height: int) -> bool:
    if len(bands) < 2:
        return False
    min_gap = max(18, int(round(height * 0.08)))
    return any((bands[index + 1][0] - bands[index][1]) >= min_gap and bands[index + 1][0] >= height * 0.5 for index in range(len(bands) - 1))


def _foreground_touches_vertical_edge(bands: list[tuple[int, int, float]], height: int) -> bool:
    if not bands:
        return False
    margin = max(3, int(round(height * 0.018)))
    return bands[0][0] <= margin or bands[-1][1] >= height - margin


def _estimate_background(crop: Image.Image) -> tuple[int, int, int]:
    width, height = crop.width, crop.height
    coords = [
        (0, 0),
        (width - 1, 0),
        (0, height - 1),
        (width - 1, height - 1),
        (width // 2, 0),
        (width // 2, height - 1),
        (0, height // 2),
        (width - 1, height // 2),
    ]
    pixels = crop.load()
    samples = [pixels[x, y][:3] for x, y in coords]
    return tuple(sorted(channel)[len(channel) // 2] for channel in zip(*samples))


def _is_foreground_pixel(pixel: tuple[int, ...], background: tuple[int, int, int]) -> bool:
    red, green, blue = pixel[:3]
    distance = abs(red - background[0]) + abs(green - background[1]) + abs(blue - background[2])
    brightness = (red + green + blue) / 3
    background_brightness = sum(background) / 3
    if distance >= 55:
        return True
    return background_brightness >= 235 and brightness <= 235 and distance >= 24


def _is_code_content(content_type: str) -> bool:
    return content_type.strip().lower() in {"code", "source_code"}


def _horizontal_overlap_ratio(left: list[float], right: list[float]) -> float:
    overlap = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
    denom = max(0.0001, min(left[2] - left[0], right[2] - right[0]))
    return overlap / denom


def _round_bbox(bbox: list[float]) -> list[float]:
    return [round(max(0.0, min(1.0, float(value))), 4) for value in bbox]


def _skip_reason(
    figure: NormalizedFigure,
    accepted: list[list[float]],
    width: int,
    height: int,
    min_confidence: float,
    min_area: int,
) -> str | None:
    decorative_reason = _decorative_candidate_reason(figure)
    if decorative_reason:
        return decorative_reason
    if figure.confidence < min_confidence:
        return "low_confidence"
    x1, y1, x2, y2 = figure.bbox
    area = (x2 - x1) * width * (y2 - y1) * height
    if area < min_area:
        return "small_area"
    if (x2 - x1) < 0.04 or (y2 - y1) < 0.04:
        return "thin_or_tiny_region"
    if (x2 - x1) * (y2 - y1) > 0.85:
        return "full_page_like"
    if any(_iou(figure.bbox, other) >= 0.75 for other in accepted):
        return "overlap_duplicate"
    return None


def _decorative_candidate_reason(figure: NormalizedFigure) -> str | None:
    text = f"{figure.content_type} {figure.label}".lower()
    if any(token in text for token in ("logo", "watermark", "decorative", "decoration", "background", "icon", "页码", "图标", "装饰", "背景", "水印")):
        return "decorative_candidate"
    if _edge_template_like(figure.bbox) and not _is_chart_like(figure.content_type):
        return "edge_template_candidate"
    return None


def _edge_template_like(bbox: list[float]) -> bool:
    area = _bbox_area(bbox)
    if area > 0.028:
        return False
    near_edge = bbox[0] <= 0.08 or bbox[1] <= 0.08 or bbox[2] >= 0.92 or bbox[3] >= 0.92
    near_corner = (bbox[0] <= 0.08 or bbox[2] >= 0.92) and (bbox[1] <= 0.08 or bbox[3] >= 0.92)
    return near_corner or (near_edge and area <= 0.016)


def _pixel_box(bbox: list[float], width: int, height: int) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = bbox
    return (
        max(0, min(width - 1, int(round(x1 * width)))),
        max(0, min(height - 1, int(round(y1 * height)))),
        max(1, min(width, int(round(x2 * width)))),
        max(1, min(height, int(round(y2 * height)))),
    )


def _iou(left: list[float], right: list[float]) -> float:
    lx1, ly1, lx2, ly2 = left
    rx1, ry1, rx2, ry2 = right
    ix1, iy1 = max(lx1, rx1), max(ly1, ry1)
    ix2, iy2 = min(lx2, rx2), min(ly2, ry2)
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    intersection = (ix2 - ix1) * (iy2 - iy1)
    left_area = (lx2 - lx1) * (ly2 - ly1)
    right_area = (rx2 - rx1) * (ry2 - ry1)
    union = left_area + right_area - intersection
    return intersection / union if union > 0 else 0.0


def _page_has_content_images(page: SlidePage) -> bool:
    return any(not image.ignored and image.role != "page_image" for image in page.images)


def _page_deserves_figure_crop(page: SlidePage) -> bool:
    if any(image.role == "page_image" for image in page.images):
        return True
    text_len = sum(len(block.content.strip()) for block in page.text_blocks)
    return bool(page.tables or text_len < 800 or page.warnings)


def _figure_prompt(target: FigureTarget, page: SlidePage | None) -> str:
    return (
        "请分析这张完整幻灯片截图，找出适合单独裁剪放进课程笔记的局部图。输出严格 JSON，不要输出 Markdown。\n"
        "JSON 格式：{\"figures\":[{\"bbox\":[x1,y1,x2,y2],\"label\":\"...\",\"content_type\":\"diagram/chart/table/formula/code/screenshot/photo/mixed/unknown\",\"confidence\":0.0}]}\n"
        "bbox 必须使用 0 到 1 的归一化坐标，表示左上角和右下角。不要返回整页、纯标题、普通正文段落、页码、logo 或背景装饰。\n"
        "纯代码区域通常不要作为图片返回；代码应优先由 OCR/文本提取进入笔记，除非它是必须保留版式的完整代码截图。\n"
        "bbox 不能包含目标图下方或旁边的相邻图形；如果两个区域之间有明显空白，请拆成独立候选或只返回核心目标。\n"
        "优先返回最能承载课程知识的 1-3 个区域。若没有适合裁剪的局部图，返回空数组。\n"
        f"本页文字上下文：{_page_context(page)}\n"
        f"元数据：slide_id={target.slide_id}, path={target.path}, reason={target.reason}。"
    )


def _page_context(page: SlidePage | None, limit: int = 1000) -> str:
    if page is None:
        return ""
    pieces: list[str] = []
    if page.title:
        pieces.append(f"标题：{page.title}")
    for block in page.text_blocks[:8]:
        pieces.append(f"{block.id}({block.type})：{block.content}")
    for table in page.tables[:2]:
        preview = table_preview(table, limit=260, raw_rows=3)
        pieces.append(f"{table.id}(table)：{preview}")
    semantic_context = semantic_context_for_page(page, limit=420)
    if semantic_context:
        pieces.append(f"semantic_layout：{semantic_context}")
    text = "\n".join(piece for piece in pieces if piece.strip())
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _semantic_layout_json_for_prompt(page: SlidePage | None) -> str:
    if page is None:
        return "{}"
    data = semantic_layout_for_prompt(page, limit=8)
    return json.dumps(data or {}, ensure_ascii=False, separators=(",", ":"))


def _figure_prompt(target: FigureTarget, page: SlidePage | None) -> str:
    return (
        "Analyze this full slide screenshot and return strict JSON only.\n"
        'JSON schema: {"figures":[{"bbox":[x1,y1,x2,y2],"label":"...","content_type":"diagram/chart/table/formula/code/screenshot/photo/mixed/unknown","confidence":0.0}]}\n'
        "bbox must use normalized 0..1 coordinates for top-left and bottom-right corners. Do not return the whole page, pure titles, body paragraphs, page numbers, logos, or decorative backgrounds.\n"
        "Pure code regions should usually stay as OCR/text unless the complete visual layout must be preserved.\n"
        "When semantic_layout describes a complete teaching scene, crop the whole semantic group rather than an isolated small part. Prioritize code plus runtime output, blue explanation boxes, red causal annotations, arrows, labels, and nearby explanations as one coherent crop when they explain each other.\n"
        "Keep crop boxes tight and avoid neighboring figures below or beside the intended target. Return the 1-3 regions that carry the most course knowledge, or an empty array.\n"
        f"page_context: {_page_context(page)}\n"
        f"semantic_layout_json: {_semantic_layout_json_for_prompt(page)}\n"
        f"metadata: slide_id={target.slide_id}, path={target.path}, reason={target.reason}."
    )


def _parse_figure_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return {"figures": [], "warnings": ["model_output_not_json"], "raw_text": cleaned}
    figures = parsed.get("figures")
    if not isinstance(figures, list):
        parsed["figures"] = []
        parsed.setdefault("warnings", []).append("missing_figures_array")
    parsed.setdefault("warnings", [])
    return parsed


def _prepare_image_for_api(path: Path, max_edge: int) -> tuple[Path, dict[str, Any]] | None:
    try:
        with Image.open(path) as image:
            original = {"width": image.width, "height": image.height, "mode": image.mode, "format": image.format}
            image = image.convert("RGB")
            scale = min(1.0, max_edge / max(image.width, image.height))
            if scale < 1.0:
                image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))))
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
            tmp_path = Path(tmp.name)
            tmp.close()
            image.save(tmp_path, format="JPEG", quality=85, optimize=True)
            meta = {
                "original": original,
                "prepared": {"width": image.width, "height": image.height, "mime_type": "image/jpeg", "bytes": tmp_path.stat().st_size},
            }
            return tmp_path, meta
    except Exception:
        return None


def _cleanup_temp_image(path: Path) -> None:
    if path.parent != Path(tempfile.gettempdir()):
        return
    try:
        path.unlink()
    except OSError:
        pass


def _build_report(
    deck: Deck,
    runtime: dict[str, Any],
    mode: str,
    cache_mode: str,
    cache_dir: Path,
    output_root: Path,
    records: list[dict[str, Any]],
    max_targets: int,
    max_crops_per_page: int,
    min_confidence: float,
    min_area: int,
) -> dict[str, Any]:
    summary = {
        "targets_total": len(records),
        "crops_created": sum(int(record.get("crops_created", 0)) for record in records),
        "local_cache_hits": sum(1 for record in records if record.get("cache_status") == "local_hit"),
        "llm_calls": sum(1 for record in records if record.get("llm_call")),
        "api_retries": sum(int(record.get("api_retries") or 0) for record in records),
        "skipped": sum(1 for record in records if record.get("cache_status") == "skipped"),
        "skipped_candidates": sum(len(record.get("skipped_candidates", [])) for record in records),
        "input_tokens": _sum_int(record.get("input_tokens") for record in records),
        "output_tokens": _sum_int(record.get("output_tokens") for record in records),
        "total_tokens": _sum_int(record.get("total_tokens") for record in records),
        "provider_cached_input_tokens": _sum_int(record.get("provider_cached_input_tokens") for record in records),
    }
    return {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "provider": runtime["provider"],
        "model": runtime["model"],
        "base_url": runtime["base_url"],
        "prompt_version": FIGURE_PROMPT_VERSION,
        "mode": mode,
        "cache": {"mode": cache_mode, "dir": _display_path(cache_dir, output_root)},
        "selection": {
            "max_targets": max_targets,
            "max_crops_per_page": max_crops_per_page,
            "min_confidence": min_confidence,
            "min_area": min_area,
        },
        "summary": summary,
        "targets": records,
    }


def _base_record(target: FigureTarget, cache_key: str, cache_path: Path, output_root: Path, image_meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "slide_id": target.slide_id,
        "kind": "page_screenshot",
        "path": target.path,
        "reason": target.reason,
        "cache_key": cache_key,
        "cache_file": _display_path(cache_path, output_root),
        "image_meta": image_meta,
    }


def _skipped_record(target: FigureTarget, status: str, output_root: Path) -> dict[str, Any]:
    return {
        "slide_id": target.slide_id,
        "kind": "page_screenshot",
        "path": target.path,
        "reason": target.reason,
        "cache_status": "skipped",
        "skip_reason": status,
        "llm_call": False,
        "crops_created": 0,
        "skipped_candidates": [],
    }


def _page_by_id(deck: Deck, slide_id: int) -> SlidePage | None:
    return next((page for page in deck.pages if page.slide_id == slide_id), None)


def _next_figure_index(page: SlidePage | None) -> int:
    if page is None:
        return 1
    next_index = 1
    prefix = f"s{page.slide_id}_fig"
    for image in page.images:
        if not image.id.startswith(prefix):
            continue
        suffix = image.id[len(prefix) :]
        if suffix.isdigit():
            next_index = max(next_index, int(suffix) + 1)
    return next_index


def _file_sha256(path: Path) -> str:
    return sha256_text(path.read_bytes().hex())


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
