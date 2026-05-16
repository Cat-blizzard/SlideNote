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

FIGURE_PROMPT_VERSION = "figure-crop-v1"

FIGURE_SYSTEM_PROMPT = (
    "你是课程幻灯片版面分析助手。请只返回 JSON。任务是找出页面中适合单独放入笔记的局部图，"
    "例如流程图、架构图、示意图、截图、公式图、图表和带标注的视觉区域。不要返回整页区域、纯标题、普通正文段落或页码。"
)


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
            for candidate in figures:
                normalized = _normalize_candidate(candidate, width=width, height=height)
                if normalized is None:
                    skipped.append({"reason": "invalid_bbox", "candidate": candidate})
                    continue
                reason = _skip_reason(normalized, accepted, width=width, height=height, min_confidence=min_confidence, min_area=min_area)
                if reason:
                    skipped.append({"reason": reason, "candidate": candidate, "bbox": normalized.bbox})
                    continue
                crop_index = len(crops) + 1
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
                    crop_method="vision_bbox",
                    confidence=normalized.confidence,
                )
                crops.append(image_asset)
                accepted.append(normalized.bbox)
                crop_records.append(
                    {
                        "id": image_asset.id,
                        "path": image_asset.path,
                        "bbox": normalized.bbox,
                        "pixel_bbox": [float(value) for value in pixel_box],
                        "label": normalized.label,
                        "content_type": normalized.content_type,
                        "confidence": normalized.confidence,
                    }
                )
                if len(crops) >= max_crops_per_page:
                    break
    except Exception as exc:
        skipped.append({"reason": "crop_failed", "error": str(exc)})
    return crops, crop_records, skipped


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


def _skip_reason(
    figure: NormalizedFigure,
    accepted: list[list[float]],
    width: int,
    height: int,
    min_confidence: float,
    min_area: int,
) -> str | None:
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
        preview = " / ".join(" | ".join(row) for row in table.rows[:3])
        pieces.append(f"{table.id}(table)：{preview}")
    text = "\n".join(piece for piece in pieces if piece.strip())
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


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
