from __future__ import annotations

import json
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from slidenote.llm import LLMClient, resolve_provider_runtime
from slidenote.llm_cache import LLM_CACHE_SCHEMA_VERSION, LLMCache, make_cache_key, sha256_text, stable_json, utc_now_iso
from slidenote.models import Deck, ImageAsset, SlidePage

VISION_PROMPT_VERSION = "vision-extract-v1"

VISION_SYSTEM_PROMPT = (
    "你是课程材料视觉解析助手。请只描述图片中能直接观察到的信息，不要猜测不可见内容。"
    "重点提取图片里的文字、公式、代码、流程图节点、图表趋势、关键标注，以及它和本页课程主题的关系。"
)


@dataclass(slots=True)
class VisionTarget:
    slide_id: int
    kind: str
    path: str
    image_id: str | None = None
    reason: str = ""


def enrich_deck_with_vision(
    deck: Deck,
    output_root: Path,
    mode: str = "off",
    provider: str = "openai",
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    cache_mode: str = "on",
    cache_dir: Path | None = None,
    max_output_tokens: int = 1200,
    temperature: float | None = 0.0,
    detail: str = "low",
    max_targets: int = 80,
    min_area: int = 120_000,
    max_edge: int = 1400,
    concurrency: int = 1,
    refresh_slide_ids: set[int] | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any] | None:
    if mode == "off":
        return None
    if mode not in {"auto", "all"}:
        raise ValueError("vision mode must be one of: off, auto, all")

    runtime = resolve_provider_runtime(provider, model=model, base_url=base_url, for_vision=True)
    if not runtime["supports_image_input"]:
        raise RuntimeError(f"Vision provider `{runtime['provider']}` does not support image input.")

    targets = select_vision_targets(deck, output_root, mode=mode, min_area=min_area, max_targets=max_targets)
    resolved_cache_dir = (cache_dir or (output_root / ".cache" / "vision")).resolve()
    cache = LLMCache(resolved_cache_dir, mode=cache_mode)
    records_by_index: dict[int, dict[str, Any]] = {}
    refresh_ids = refresh_slide_ids or set()
    workers = max(1, int(concurrency or 1))

    if progress_callback:
        progress_callback({"event": "start", "total": len(targets)})

    def process(index: int, target: VisionTarget) -> tuple[int, VisionTarget, dict[str, Any], dict[str, Any]]:
        page = _page_by_id(deck, target.slide_id)
        record, parsed = _process_visual_target(
            target=target,
            page=page,
            output_root=output_root,
            cache=cache,
            cache_mode=cache_mode,
            runtime=runtime,
            api_key=api_key,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            detail=detail,
            max_edge=max_edge,
            force_refresh=target.slide_id in refresh_ids,
        )
        return index, target, record, parsed

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

    for index, target, record, parsed in sorted(results, key=lambda item: item[0]):
        _apply_visual_result(
            deck,
            target,
            ocr_text=parsed.get("ocr_text"),
            visual_summary=parsed.get("visual_summary"),
            visual_status=record.get("visual_status", "parsed"),
        )
        records_by_index[index] = record

    records = [records_by_index[index] for index in sorted(records_by_index)]
    return _build_report(deck, runtime, mode, cache_mode, resolved_cache_dir, output_root, max_targets, min_area, max_edge, records)


def _process_visual_target(
    target: VisionTarget,
    page: SlidePage | None,
    output_root: Path,
    cache: LLMCache,
    cache_mode: str,
    runtime: dict[str, Any],
    api_key: str | None,
    max_output_tokens: int,
    temperature: float | None,
    detail: str,
    max_edge: int,
    force_refresh: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    source_path = (output_root / target.path).resolve()
    if not source_path.exists():
        record = _skipped_record(target, "missing_file", output_root)
        record["visual_status"] = "missing_file"
        return record, {}
    prepared = _prepare_image_for_api(source_path, max_edge=max_edge)
    if prepared is None:
        record = _skipped_record(target, "unsupported_or_unreadable_image", output_root)
        record["visual_status"] = "unsupported_or_unreadable_image"
        return record, {}

    prepared_path, image_meta = prepared
    try:
        prompt = _vision_prompt(target, page)
        image_hash = _file_sha256(source_path)
        cache_key_payload = {
            "schema_version": LLM_CACHE_SCHEMA_VERSION,
            "prompt_version": VISION_PROMPT_VERSION,
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
            llm_result = client.generate_image_with_usage(prepared_path, prompt, system_prompt=VISION_SYSTEM_PROMPT, image_detail=detail)
            result_json = llm_result.text
            response_usage = llm_result.usage or {}
            cache_status = "disabled" if cache_mode == "off" else "refresh" if cache_mode == "refresh" or force_refresh else "miss"
            written_path = cache.write(
                cache_key,
                {
                    "provider": runtime["provider"],
                    "model": runtime["model"],
                    "base_url": runtime["base_url"],
                    "prompt_version": VISION_PROMPT_VERSION,
                    "slide_id": target.slide_id,
                    "target": asdict(target),
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

        parsed = _parse_visual_json(result_json)
        record["result"] = parsed
        record["visual_status"] = "parsed"
        return record, parsed
    finally:
        _cleanup_temp_image(prepared_path)


def select_vision_targets(
    deck: Deck,
    output_root: Path,
    mode: str,
    min_area: int = 120_000,
    max_targets: int = 80,
) -> list[VisionTarget]:
    targets: list[VisionTarget] = []
    for page in deck.pages:
        if mode == "auto":
            if page.page_screenshot and _page_deserves_screenshot(page):
                targets.append(VisionTarget(page.slide_id, "page_screenshot", page.page_screenshot, reason="auto_page_visual"))
            elif not page.page_screenshot:
                targets.extend(_large_image_targets(page, output_root, min_area=min_area, first_only=True))
        else:
            if page.page_screenshot:
                targets.append(VisionTarget(page.slide_id, "page_screenshot", page.page_screenshot, reason="all_page_screenshot"))
            else:
                targets.extend(_large_image_targets(page, output_root, min_area=0, first_only=False))
    if max_targets > 0:
        return targets[:max_targets]
    return targets


def _page_deserves_screenshot(page: SlidePage) -> bool:
    text_len = sum(len(block.content.strip()) for block in page.text_blocks)
    has_content_images = any(not image.ignored for image in page.images)
    return bool(has_content_images or page.tables or text_len < 500 or page.warnings)


def _large_image_targets(page: SlidePage, output_root: Path, min_area: int, first_only: bool) -> list[VisionTarget]:
    targets: list[VisionTarget] = []
    for image in page.images:
        if image.ignored:
            continue
        path = output_root / image.path
        area = _image_area(path)
        if area is not None and area < min_area:
            continue
        targets.append(VisionTarget(page.slide_id, "image", image.path, image_id=image.id, reason="large_embedded_image"))
        if first_only:
            break
    return targets


def _image_area(path: Path) -> int | None:
    try:
        with Image.open(path) as image:
            return image.width * image.height
    except Exception:
        return None


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


def _vision_prompt(target: VisionTarget, page: SlidePage | None = None) -> str:
    page_context = _page_context(page)
    return (
        "请解析这张课程幻灯片图片，输出严格 JSON，不要输出 Markdown。\n"
        "字段要求：\n"
        "- ocr_text: 图片中可读文字，保留公式、代码、图表坐标轴、流程节点；没有则为空字符串。\n"
        "- visual_summary: 2-5 句话解释图片传达的课程知识，只基于图片可见信息，并尽量结合本页文字上下文说明它在讲什么。\n"
        "- content_type: 从 diagram/table/chart/formula/code/screenshot/photo/mixed/unknown 中选一个。\n"
        "- confidence: 0 到 1。\n"
        "- warnings: 字符串数组，标注低清晰度、文字太小、无法判断等问题。\n"
        "注意：如果图片和本页文字互相解释，请在 visual_summary 里说明这种关联；如果看不出关联，请明确说无法判断。\n"
        f"本页文字上下文：{page_context}\n"
        f"元数据：slide_id={target.slide_id}, kind={target.kind}, image_id={target.image_id or ''}, path={target.path}。"
    )


def _page_by_id(deck: Deck, slide_id: int) -> SlidePage | None:
    return next((page for page in deck.pages if page.slide_id == slide_id), None)


def _page_context(page: SlidePage | None, limit: int = 1200) -> str:
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
    if page.page_ocr_text and page.page_ocr_status:
        pieces.append(f"page_ocr_text：{page.page_ocr_text[:600]}")
    text = "\n".join(piece for piece in pieces if piece.strip())
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _parse_visual_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        parsed = {"ocr_text": "", "visual_summary": cleaned, "content_type": "unknown", "confidence": None, "warnings": ["model_output_not_json"]}
    parsed.setdefault("ocr_text", "")
    parsed.setdefault("visual_summary", "")
    parsed.setdefault("warnings", [])
    return parsed


def _apply_visual_result(
    deck: Deck,
    target: VisionTarget,
    ocr_text: str | None = None,
    visual_summary: str | None = None,
    visual_status: str | None = None,
) -> None:
    page = next((item for item in deck.pages if item.slide_id == target.slide_id), None)
    if page is None:
        return
    if target.kind == "page_screenshot":
        page.page_ocr_text = page.page_ocr_text or ocr_text
        page.page_visual_summary = visual_summary or page.page_visual_summary
        page.page_visual_status = visual_status or page.page_visual_status
        return
    if target.image_id:
        image = next((item for item in page.images if item.id == target.image_id), None)
        if image:
            image.ocr_text = image.ocr_text or ocr_text
            image.visual_summary = visual_summary or image.visual_summary
            image.visual_status = visual_status or image.visual_status


def _build_report(
    deck: Deck,
    runtime: dict[str, Any],
    mode: str,
    cache_mode: str,
    cache_dir: Path,
    output_root: Path,
    max_targets: int,
    min_area: int,
    max_edge: int,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    summary = {
        "targets_total": len(records),
        "local_cache_hits": sum(1 for record in records if record.get("cache_status") == "local_hit"),
        "llm_calls": sum(1 for record in records if record.get("llm_call")),
        "skipped": sum(1 for record in records if record.get("cache_status") == "skipped"),
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
        "prompt_version": VISION_PROMPT_VERSION,
        "mode": mode,
        "cache": {"mode": cache_mode, "dir": _display_path(cache_dir, output_root)},
        "selection": {"max_targets": max_targets, "min_area": min_area, "max_edge": max_edge},
        "summary": summary,
        "targets": records,
    }


def _base_record(target: VisionTarget, cache_key: str, cache_path: Path, output_root: Path, image_meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "slide_id": target.slide_id,
        "kind": target.kind,
        "image_id": target.image_id,
        "path": target.path,
        "reason": target.reason,
        "cache_key": cache_key,
        "cache_file": _display_path(cache_path, output_root),
        "image_meta": image_meta,
    }


def _skipped_record(target: VisionTarget, status: str, output_root: Path) -> dict[str, Any]:
    return {
        "slide_id": target.slide_id,
        "kind": target.kind,
        "image_id": target.image_id,
        "path": target.path,
        "reason": target.reason,
        "cache_status": "skipped",
        "skip_reason": status,
        "llm_call": False,
    }


def _file_sha256(path: Path) -> str:
    return sha256_text(path.read_bytes().hex())


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
