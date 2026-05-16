from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from slidenote.llm import LLMClient, resolve_provider_runtime
from slidenote.llm_cache import LLM_CACHE_SCHEMA_VERSION, LLMCache, make_cache_key, sha256_text, stable_json, utc_now_iso
from slidenote.models import Deck, SlidePage

SECTION_PROMPT_VERSION = "section-detection-v1"

SECTION_SYSTEM_PROMPT = (
    "你是课程幻灯片章节识别助手。你只负责判断哪些页应该作为章节或小节起点。"
    "输出必须是严格 JSON，不要输出 Markdown、解释性段落或寒暄。"
)


def build_section_plan(
    deck: Deck,
    output_root: Path,
    mode: str = "auto",
    use_llm: bool = False,
    provider: str = "openai",
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    cache_mode: str = "on",
    cache_dir: Path | None = None,
    max_output_tokens: int = 1800,
    temperature: float | None = 0.0,
) -> dict[str, Any]:
    if mode not in {"local", "llm", "auto"}:
        raise ValueError("section detection mode must be one of: local, llm, auto")

    local_plan = build_local_section_plan(deck)
    should_use_llm = mode == "llm" or (mode == "auto" and use_llm and len(deck.pages) >= 4)
    if not should_use_llm:
        local_plan["requested_mode"] = mode
        return local_plan

    runtime = resolve_provider_runtime(provider, model=model, base_url=base_url)
    resolved_cache_dir = (cache_dir or (output_root / ".cache" / "sections")).resolve()
    cache = LLMCache(resolved_cache_dir, mode=cache_mode)
    prompt = _section_prompt(deck, local_plan)
    cache_key_payload = {
        "schema_version": LLM_CACHE_SCHEMA_VERSION,
        "prompt_version": SECTION_PROMPT_VERSION,
        "provider": runtime["provider"],
        "model": runtime["model"],
        "base_url": runtime["base_url"],
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "page_digest": _page_digest(deck),
        "local_plan_hash": sha256_text(stable_json(local_plan.get("sections", []))),
        "prompt_hash": sha256_text(prompt),
    }
    cache_key = make_cache_key(cache_key_payload)
    cache_path = cache.path_for(cache_key)
    cached = cache.read(cache_key)
    usage: dict[str, Any] = {}
    cache_status = "local_hit"
    llm_call = False
    if cached:
        result_text = cached["output_text"]
        usage = cached.get("response_usage") or {}
    else:
        client = LLMClient(
            provider=str(runtime["provider"]),
            model=str(runtime["model"]),
            api_key=api_key,
            base_url=runtime["base_url"],
            max_output_tokens=max_output_tokens,
            temperature=temperature,
        )
        result = client.generate_with_usage(prompt, system_prompt=SECTION_SYSTEM_PROMPT)
        result_text = result.text
        usage = result.usage or {}
        cache_status = "disabled" if cache_mode == "off" else "refresh" if cache_mode == "refresh" else "miss"
        llm_call = True
        written_path = cache.write(
            cache_key,
            {
                "provider": runtime["provider"],
                "model": runtime["model"],
                "base_url": runtime["base_url"],
                "prompt_version": SECTION_PROMPT_VERSION,
                "output_text": result_text,
                "response_usage": usage,
            },
        )
        if written_path is not None:
            cache_path = written_path

    parsed = _parse_section_json(result_text)
    plan = _normalize_model_plan(deck, parsed, local_plan)
    if plan is None:
        plan = build_local_section_plan(deck)
        plan["method"] = "local_fallback"
        plan["warnings"].append("llm_section_output_invalid")
    else:
        plan["method"] = "llm"
    plan["requested_mode"] = mode
    plan["prompt_version"] = SECTION_PROMPT_VERSION
    plan["llm"] = {
        "provider": runtime["provider"],
        "model": runtime["model"],
        "base_url": runtime["base_url"],
        "cache": {"mode": cache_mode, "status": cache_status, "file": _display_path(cache_path, output_root)},
        "llm_call": llm_call,
        "input_tokens": usage.get("input_tokens") if llm_call else 0,
        "output_tokens": usage.get("output_tokens") if llm_call else 0,
        "total_tokens": usage.get("total_tokens") if llm_call else 0,
        "provider_cached_input_tokens": usage.get("provider_cached_input_tokens") if llm_call else 0,
        "cached_entry_usage": usage if not llm_call else None,
    }
    plan["summary"]["llm_call"] = llm_call
    plan["summary"]["local_cache_hits"] = 1 if cache_status == "local_hit" else 0
    plan["summary"]["input_tokens"] = usage.get("input_tokens") if llm_call else 0
    plan["summary"]["output_tokens"] = usage.get("output_tokens") if llm_call else 0
    plan["summary"]["total_tokens"] = usage.get("total_tokens") if llm_call else 0
    plan["local_baseline"] = {
        "sections_total": len(local_plan.get("sections", [])),
        "section_starts": [section.get("start_slide_id") for section in local_plan.get("sections", [])],
    }
    return plan


def build_local_section_plan(deck: Deck) -> dict[str, Any]:
    boundaries, reasons = _section_boundaries(deck)
    if len(boundaries) <= 1 and deck.pages:
        boundaries = [deck.pages[index].slide_id for index in range(0, len(deck.pages), 8)]
        reasons = {slide_id: "fixed_size_fallback" for slide_id in boundaries}
        reasons[deck.pages[0].slide_id] = "first_page"
    sections = _sections_from_boundaries(deck, boundaries, reasons)
    return {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "method": "local",
        "requested_mode": "local",
        "summary": {
            "pages_total": len(deck.pages),
            "sections_total": len(sections),
            "llm_call": False,
            "local_cache_hits": 0,
        },
        "sections": sections,
        "warnings": [],
    }


def _section_prompt(deck: Deck, local_plan: dict[str, Any]) -> str:
    payload = {
        "task": "detect_course_sections",
        "source_file": Path(deck.source_path).stem,
        "pages": [_page_outline_payload(page) for page in deck.pages],
        "local_baseline": [
            {
                "title": section.get("title"),
                "start_slide_id": section.get("start_slide_id"),
                "slide_ids": section.get("slide_ids"),
                "reason": section.get("reason"),
            }
            for section in local_plan.get("sections", [])
        ],
    }
    return (
        "请根据页面标题、页内文字摘要、页面类型和本地基线，判断课程材料的章节/小节边界。\n"
        "输出严格 JSON，格式：\n"
        '{"sections":[{"title":"章节标题","start_slide_id":1,"reason":"为什么这里是章节起点"}]}\n'
        "要求：\n"
        "1. start_slide_id 必须来自输入页面。\n"
        "2. 第一页必须是第一个 section 的 start_slide_id。\n"
        "3. 不要把每一页都切成章节；优先找课程语义上的大段落。\n"
        "4. 如果目录页列出章节，优先参考目录；如果标题页很明显，也可作为章节起点。\n"
        "5. 标题要短，不能写“课程笔记”。\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _page_outline_payload(page: SlidePage) -> dict[str, Any]:
    return {
        "slide_id": page.slide_id,
        "title": page.title,
        "page_modality": page.page_modality,
        "brief": _page_brief(page),
        "text_block_count": len(page.text_blocks),
        "table_count": len(page.tables),
        "image_count": len([image for image in page.images if not image.ignored]),
    }


def _page_brief(page: SlidePage, limit: int = 520) -> str:
    parts: list[str] = []
    if page.title:
        parts.append(page.title)
    parts.extend(block.content for block in page.text_blocks[:5] if block.content.strip())
    if page.page_ocr_text:
        parts.append(page.page_ocr_text[:260])
    if page.page_visual_summary:
        parts.append(page.page_visual_summary[:260])
    text = re.sub(r"\s+", " ", " ".join(parts)).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _parse_section_json(text: str) -> dict[str, Any] | None:
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


def _normalize_model_plan(deck: Deck, parsed: dict[str, Any] | None, local_plan: dict[str, Any]) -> dict[str, Any] | None:
    if parsed is None:
        return None
    raw_sections = parsed.get("sections")
    if not isinstance(raw_sections, list):
        return None
    slide_ids = [page.slide_id for page in deck.pages]
    if not slide_ids:
        return build_local_section_plan(deck)
    valid_ids = set(slide_ids)
    titles_by_start: dict[int, str] = {}
    reasons: dict[int, str] = {}
    starts: list[int] = []
    for raw in raw_sections:
        if not isinstance(raw, dict):
            continue
        start = raw.get("start_slide_id")
        if not isinstance(start, int) or start not in valid_ids:
            continue
        if start in starts:
            continue
        starts.append(start)
        title = str(raw.get("title") or "").strip()
        reason = str(raw.get("reason") or "llm_boundary").strip()
        if title:
            titles_by_start[start] = title
        reasons[start] = reason or "llm_boundary"
    if not starts:
        return None
    if starts[0] != slide_ids[0]:
        starts.insert(0, slide_ids[0])
        reasons[slide_ids[0]] = "first_page_inserted"
    starts = sorted(set(starts), key=slide_ids.index)
    sections = _sections_from_boundaries(deck, starts, reasons, titles_by_start=titles_by_start)
    if not _covers_all_pages(deck, sections):
        return None
    return {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "method": "llm",
        "requested_mode": "llm",
        "summary": {
            "pages_total": len(deck.pages),
            "sections_total": len(sections),
            "llm_call": True,
            "local_cache_hits": 0,
        },
        "sections": sections,
        "warnings": [],
        "model_raw_section_count": len(raw_sections),
        "local_baseline_sections_total": len(local_plan.get("sections", [])),
    }


def _sections_from_boundaries(
    deck: Deck,
    boundaries: list[int],
    reasons: dict[int, str],
    titles_by_start: dict[int, str] | None = None,
) -> list[dict[str, Any]]:
    if not deck.pages:
        return []
    titles_by_start = titles_by_start or {}
    slide_to_index = {page.slide_id: index for index, page in enumerate(deck.pages)}
    boundary_indexes = sorted({slide_to_index[slide_id] for slide_id in boundaries if slide_id in slide_to_index})
    if not boundary_indexes or boundary_indexes[0] != 0:
        boundary_indexes.insert(0, 0)
    sections: list[dict[str, Any]] = []
    for position, start_index in enumerate(boundary_indexes):
        end_index = boundary_indexes[position + 1] if position + 1 < len(boundary_indexes) else len(deck.pages)
        pages = deck.pages[start_index:end_index]
        if not pages:
            continue
        start_id = pages[0].slide_id
        sections.append(
            {
                "section_id": f"sec{len(sections) + 1}",
                "title": titles_by_start.get(start_id) or _context_title(pages, len(sections) + 1),
                "start_slide_id": start_id,
                "end_slide_id": pages[-1].slide_id,
                "slide_ids": [page.slide_id for page in pages],
                "reason": reasons.get(start_id, "local_boundary"),
            }
        )
    return sections


def _section_boundaries(deck: Deck) -> tuple[list[int], dict[int, str]]:
    if not deck.pages:
        return [], {}
    outline_titles = _outline_titles(deck)
    boundaries = [deck.pages[0].slide_id]
    reasons = {deck.pages[0].slide_id: "first_page"}
    for page in deck.pages[1:]:
        title = _normalize_heading_text(page.title or "")
        if not title or "目录" in title or title.lower() == "contents":
            continue
        if any(title == outline or title in outline or outline in title for outline in outline_titles):
            boundaries.append(page.slide_id)
            reasons[page.slide_id] = "matched_outline_title"
        elif _looks_like_section_title_page(page):
            boundaries.append(page.slide_id)
            reasons[page.slide_id] = "section_title_page"
    return sorted(set(boundaries)), reasons


def _outline_titles(deck: Deck) -> set[str]:
    titles: set[str] = set()
    for page in deck.pages:
        page_text = "\n".join(block.content for block in page.text_blocks)
        if "目录" not in page_text and "Contents" not in page_text:
            continue
        for line in page_text.splitlines():
            normalized = _normalize_heading_text(line)
            if not normalized or normalized in {"目录", "contents"}:
                continue
            if len(normalized) >= 4:
                titles.add(normalized)
    return titles


def _normalize_heading_text(value: str) -> str:
    value = re.sub(r"^\s*[\d一二三四五六七八九十]+[.、\s-]*", "", value.strip())
    return re.sub(r"\s+", "", value).strip("：:")


def _looks_like_section_title_page(page: SlidePage) -> bool:
    content_blocks = [
        block
        for block in page.text_blocks
        if block.content.strip() and not re.fullmatch(r"\d+", block.content.strip())
    ]
    if not page.title or len(content_blocks) > 3:
        return False
    text_len = sum(len(block.content.strip()) for block in content_blocks)
    return text_len <= 120


def _context_title(pages: list[SlidePage], index: int) -> str:
    for page in pages:
        if page.title:
            return page.title
    return f"第 {index} 节"


def _covers_all_pages(deck: Deck, sections: list[dict[str, Any]]) -> bool:
    expected = [page.slide_id for page in deck.pages]
    actual: list[int] = []
    for section in sections:
        slide_ids = section.get("slide_ids")
        if isinstance(slide_ids, list):
            actual.extend(slide_id for slide_id in slide_ids if isinstance(slide_id, int))
    return actual == expected


def _page_digest(deck: Deck) -> str:
    payload = [_page_outline_payload(page) for page in deck.pages]
    return sha256_text(stable_json(payload))


def _display_path(path: Path, output_root: Path) -> str:
    try:
        return path.resolve().relative_to(output_root.resolve()).as_posix()
    except ValueError:
        return str(path)
