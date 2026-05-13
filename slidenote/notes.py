from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from slidenote.llm import LLMClient, SYSTEM_PROMPT, resolve_provider_runtime
from slidenote.llm_cache import LLM_CACHE_SCHEMA_VERSION, LLMCache, make_cache_key, sha256_text, stable_json, utc_now_iso
from slidenote.models import Deck, ImageAsset, SlidePage, TableBlock, TextBlock

NOTE_PROMPT_VERSION = "note-page-v3"


@dataclass(slots=True)
class NoteGenerationResult:
    markdown: str
    llm_usage: dict[str, Any] | None = None


def generate_notes(
    deck: Deck,
    output_root: Path,
    use_llm: bool = False,
    provider: str = "openai",
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    max_output_tokens: int = 4096,
    temperature: float | None = None,
    cache_mode: str = "on",
    cache_dir: Path | None = None,
    concurrency: int = 1,
    refresh_slide_ids: set[int] | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> str:
    return generate_notes_result(
        deck=deck,
        output_root=output_root,
        use_llm=use_llm,
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
        cache_mode=cache_mode,
        cache_dir=cache_dir,
        concurrency=concurrency,
        refresh_slide_ids=refresh_slide_ids,
        progress_callback=progress_callback,
    ).markdown


def generate_notes_result(
    deck: Deck,
    output_root: Path,
    use_llm: bool = False,
    provider: str = "openai",
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    max_output_tokens: int = 4096,
    temperature: float | None = None,
    cache_mode: str = "on",
    cache_dir: Path | None = None,
    concurrency: int = 1,
    refresh_slide_ids: set[int] | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> NoteGenerationResult:
    if use_llm:
        return _generate_notes_with_llm(
            deck,
            output_root=output_root,
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            cache_mode=cache_mode,
            cache_dir=cache_dir,
            concurrency=concurrency,
            refresh_slide_ids=refresh_slide_ids,
            progress_callback=progress_callback,
        )
    return NoteGenerationResult(markdown=_generate_notes_locally(deck))


def _generate_notes_locally(deck: Deck) -> str:
    lines: list[str] = []
    title = Path(deck.source_path).stem
    lines.append(f"# {title} 课程笔记")
    lines.append("")
    lines.append("> 说明：这是 SlideNote MVP 的本地规则草稿。它用于调试解析和覆盖率链路；正式改写请使用 `--use-llm`。")
    lines.append("")

    for page in deck.pages:
        heading = page.title or f"第 {page.slide_id} 页"
        lines.append(f"## 第 {page.slide_id} 页：{heading}")
        lines.append("")

        if page.page_screenshot:
            lines.append(f"![第 {page.slide_id} 页截图]({page.page_screenshot})")
            lines.append("")

        page_visual_lines = _render_page_visual_context(page)
        if page_visual_lines:
            lines.extend(page_visual_lines)
            lines.append("")

        if page.text_blocks:
            lines.extend(_render_text_blocks(page))
            lines.append("")

        for table in page.tables:
            lines.extend(_render_table(page, table))
            lines.append("")

        for image in page.images:
            if image.ignored:
                continue
            lines.extend(_render_image(page, image))
            lines.append("")

        visible_images = [image for image in page.images if not image.ignored]
        if not page.text_blocks and not page.tables and not visible_images and not page_visual_lines:
            lines.append(f"PPT 第 {page.slide_id} 页没有解析到可写入的文本、表格或嵌入图片。")
            lines.append("")

        if page.notes:
            lines.append(f"讲者备注：{page.notes}")
            lines.append(f"【对应 PPT：第 {page.slide_id} 页，notes】")
            lines.append("")

        for warning in page.warnings:
            lines.append(f"> 提醒：{warning}")
            lines.append("")

    if deck.warnings:
        lines.append("## 解析提醒")
        lines.append("")
        for warning in deck.warnings:
            lines.append(f"- {warning}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _render_text_blocks(page: SlidePage) -> list[str]:
    lines: list[str] = []
    for block in page.text_blocks:
        if block.type == "title":
            content = _plain_block_text(block)
            lines.append(f"本页主题是“{content}”。")
        elif block.type == "bullet":
            content = _rewrite_block(block)
            lines.append(f"本页列出了以下要点：{content}")
        else:
            content = _rewrite_block(block)
            lines.append(content)
        lines.append(f"【对应 PPT：第 {page.slide_id} 页，文本块 {block.id}】")
        lines.append("")
    return lines


def _render_page_visual_context(page: SlidePage) -> list[str]:
    lines: list[str] = []
    if page.page_visual_summary:
        lines.append(f"页截图视觉解析：{_ensure_sentence(page.page_visual_summary)}")
        lines.append(f"【对应 PPT：第 {page.slide_id} 页，页截图】")
    if page.page_ocr_text:
        if lines:
            lines.append("")
        lines.append("页截图 OCR 文字：")
        lines.extend(_quote_multiline(page.page_ocr_text))
        lines.append(f"【对应 PPT：第 {page.slide_id} 页，页截图 OCR】")
    return lines


def _rewrite_block(block: TextBlock) -> str:
    text = _plain_block_text(block)
    if block.type == "bullet":
        lines = [line.strip(" \t-•*·") for line in block.content.splitlines() if line.strip()]
        if len(lines) > 1:
            text = "；".join(lines)
    if text and text[-1] not in "。.!！?？：:":
        text += "。"
    return text


def _plain_block_text(block: TextBlock) -> str:
    lines = [line.strip(" \t-•*·") for line in block.content.splitlines() if line.strip()]
    return " ".join(lines).strip()


def _render_table(page: SlidePage, table: TableBlock) -> list[str]:
    lines = [f"下表整理了第 {page.slide_id} 页中的表格内容。", f"【对应 PPT：第 {page.slide_id} 页，表格 {table.id}】", ""]
    if not table.rows:
        return lines
    width = max(len(row) for row in table.rows)
    padded = [row + [""] * (width - len(row)) for row in table.rows]
    header = padded[0]
    lines.append("| " + " | ".join(_escape_md(cell) or " " for cell in header) + " |")
    lines.append("| " + " | ".join("---" for _ in header) + " |")
    for row in padded[1:] or [[""] * width]:
        lines.append("| " + " | ".join(_escape_md(cell) or " " for cell in row) + " |")
    return lines


def _render_image(page: SlidePage, image: ImageAsset) -> list[str]:
    caption = image.caption or f"第 {page.slide_id} 页图片"
    lines = [
        f"{caption}。",
        f"【对应 PPT：第 {page.slide_id} 页，图片 {image.id}】",
        "",
    ]
    if image.visual_summary:
        lines.append(f"图片视觉解析：{_ensure_sentence(image.visual_summary)}")
    if image.ocr_text:
        if image.visual_summary:
            lines.append("")
        lines.append("图片 OCR 文字：")
        lines.extend(_quote_multiline(image.ocr_text))
    if image.visual_summary or image.ocr_text:
        lines.append("")
    lines.append(f"![{caption}]({image.path})")
    return lines


def _ensure_sentence(text: str) -> str:
    value = " ".join(text.split()).strip()
    if value and value[-1] not in "。.!！?？：:":
        value += "。"
    return value


def _quote_multiline(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return [f"> {line}" for line in lines]


def _escape_md(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


def _generate_notes_with_llm(
    deck: Deck,
    output_root: Path,
    provider: str,
    model: str | None,
    api_key: str | None,
    base_url: str | None,
    max_output_tokens: int,
    temperature: float | None,
    cache_mode: str,
    cache_dir: Path | None,
    concurrency: int,
    refresh_slide_ids: set[int] | None,
    progress_callback: Callable[[dict[str, Any]], None] | None,
) -> NoteGenerationResult:
    runtime = resolve_provider_runtime(provider, model=model, base_url=base_url)
    resolved_provider = str(runtime["provider"])
    resolved_model = str(runtime["model"])
    resolved_base_url = runtime["base_url"]
    supports_image_input = bool(runtime["supports_image_input"])
    resolved_cache_dir = (cache_dir or (output_root / ".cache" / "llm")).resolve()
    cache = LLMCache(resolved_cache_dir, mode=cache_mode)

    lines = [f"# {Path(deck.source_path).stem} 课程笔记", ""]
    refresh_ids = refresh_slide_ids or set()
    workers = max(1, int(concurrency or 1))
    page_results: dict[int, tuple[str, dict[str, Any]]] = {}

    def process(page: SlidePage) -> tuple[int, str, dict[str, Any]]:
        content, page_record = _generate_llm_page(
            page=page,
            output_root=output_root,
            cache=cache,
            cache_mode=cache_mode,
            provider=resolved_provider,
            model=resolved_model,
            api_key=api_key,
            base_url=resolved_base_url,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            supports_image_input=supports_image_input,
            force_refresh=page.slide_id in refresh_ids,
        )
        return page.slide_id, content, page_record

    if workers == 1:
        for page in deck.pages:
            slide_id, content, page_record = process(page)
            page_results[slide_id] = (content, page_record)
            if progress_callback:
                progress_callback(page_record)
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(process, page): page for page in deck.pages}
            for future in as_completed(futures):
                slide_id, content, page_record = future.result()
                page_results[slide_id] = (content, page_record)
                if progress_callback:
                    progress_callback(page_record)

    usage_pages: list[dict[str, Any]] = []
    for page in deck.pages:
        content, page_record = page_results[page.slide_id]
        usage_pages.append(page_record)
        lines.append(content)
        lines.append("")

    if deck.warnings:
        lines.append("## 解析提醒")
        lines.extend(f"- {warning}" for warning in deck.warnings)
        lines.append("")

    usage_report = _build_usage_report(
        deck=deck,
        provider=resolved_provider,
        model=resolved_model,
        base_url=resolved_base_url,
        cache_mode=cache_mode,
        cache_dir=resolved_cache_dir,
        output_root=output_root,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
        pages=usage_pages,
    )
    lines.extend(_render_generation_info(usage_report))
    return NoteGenerationResult(markdown="\n".join(lines).rstrip() + "\n", llm_usage=usage_report)


def _generate_llm_page(
    page: SlidePage,
    output_root: Path,
    cache: LLMCache,
    cache_mode: str,
    provider: str,
    model: str,
    api_key: str | None,
    base_url: str | None,
    max_output_tokens: int,
    temperature: float | None,
    supports_image_input: bool,
    force_refresh: bool = False,
) -> tuple[str, dict[str, Any]]:
    user_prompt = _llm_page_prompt(page, supports_image_input=supports_image_input)
    cache_key_payload = {
        "schema_version": LLM_CACHE_SCHEMA_VERSION,
        "prompt_version": NOTE_PROMPT_VERSION,
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
        "system_prompt_hash": sha256_text(SYSTEM_PROMPT),
        "user_prompt_hash": sha256_text(user_prompt),
        "user_prompt": user_prompt,
    }
    cache_key = make_cache_key(cache_key_payload)
    cache_path = cache.path_for(cache_key)
    prompt_hash = sha256_text(stable_json(cache_key_payload))
    cached = None if force_refresh else cache.read(cache_key)
    page_record = _base_usage_page_record(
        page=page,
        cache_key=cache_key,
        cache_path=cache_path,
        output_root=output_root,
        prompt_hash=prompt_hash,
    )

    if cached:
        content = cached["output_text"]
        cached_usage = cached.get("response_usage") or {}
        page_record.update(
            {
                "cache_status": "local_hit",
                "llm_call": False,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "provider_cached_input_tokens": 0,
                "cached_entry_usage": cached_usage,
                "cached_at": cached.get("created_at"),
            }
        )
    else:
        client = LLMClient(
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
        )
        llm_result = client.generate_with_usage(user_prompt)
        content = llm_result.text
        response_usage = llm_result.usage or {}
        cache_status = "disabled" if cache_mode == "off" else "refresh" if cache_mode == "refresh" or force_refresh else "miss"
        written_path = cache.write(
            cache_key,
            {
                "provider": provider,
                "model": model,
                "base_url": base_url,
                "prompt_version": NOTE_PROMPT_VERSION,
                "slide_id": page.slide_id,
                "request": {
                    "temperature": temperature,
                    "max_output_tokens": max_output_tokens,
                },
                "prompt_hash": prompt_hash,
                "output_text": content,
                "response_usage": response_usage,
            },
        )
        if written_path is not None:
            cache_path = written_path
        page_record.update(
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

    page_record["note_chars"] = len(content)
    page_record["cache_file"] = _display_path(cache_path, output_root)
    return content, page_record


def _llm_page_prompt(page: SlidePage, supports_image_input: bool = False) -> str:
    import json

    page_payload = asdict(page)
    if page_payload.get("images"):
        for image in page_payload["images"]:
            image["visual_status"] = (
                "image pixels are available to the model"
                if supports_image_input
                else "image pixels are NOT provided to this LLM call; preserve the image reference, but do not infer visual content"
            )

    image_rule = (
        "6. 本次调用没有提供图片像素时，只能把图片作为证据插入并说明“图片已保留，未进行视觉解析”；"
        "不要根据图片路径、文件名或通用 caption 猜测图片内容。若 JSON 中已有 ocr_text、visual_summary、page_ocr_text 或 page_visual_summary，必须把这些视觉解析结果纳入笔记。"
        "7. 不要把视觉解析结果孤立堆在图片下方；要把它和同页相关 text block、table 合并成有逻辑关系的知识段落，并同时标注文字元素 ID 和图片/页截图来源。"
    )
    return (
        "请把下面这一页课程材料 JSON 改写成结构清晰、内容完整的 Markdown 课程笔记。\n"
        "硬性要求：\n"
        "1. 覆盖每个 text block、table、image；不要把细节总结掉。\n"
        "2. 每段必须包含类似【对应 PPT：第 4 页，文本块 s4_t1】的证据标记。\n"
        "3. 图片要以 Markdown 图片语法插入。\n"
        "4. 如果某个元素被合并进段落，仍然写出它的元素 ID。\n"
        "5. PPT 没有提供的信息必须标记为“AI 补充说明”。\n"
        f"{image_rule}\n\n"
        f"{json.dumps(page_payload, ensure_ascii=False, indent=2)}"
    )


def _base_usage_page_record(page: SlidePage, cache_key: str, cache_path: Path, output_root: Path, prompt_hash: str) -> dict[str, Any]:
    return {
        "slide_id": page.slide_id,
        "cache_key": cache_key,
        "cache_file": _display_path(cache_path, output_root),
        "prompt_hash": prompt_hash,
        "element_counts": {
            "text_blocks": len(page.text_blocks),
            "tables": len(page.tables),
            "images": len(page.images),
        },
    }


def _build_usage_report(
    deck: Deck,
    provider: str,
    model: str,
    base_url: str | None,
    cache_mode: str,
    cache_dir: Path,
    output_root: Path,
    max_output_tokens: int,
    temperature: float | None,
    pages: list[dict[str, Any]],
) -> dict[str, Any]:
    summary = {
        "pages_total": len(pages),
        "local_cache_hits": sum(1 for page in pages if page.get("cache_status") == "local_hit"),
        "local_cache_misses": sum(1 for page in pages if page.get("cache_status") == "miss"),
        "local_cache_refreshes": sum(1 for page in pages if page.get("cache_status") == "refresh"),
        "cache_disabled_calls": sum(1 for page in pages if page.get("cache_status") == "disabled"),
        "llm_calls": sum(1 for page in pages if page.get("llm_call")),
        "input_tokens": _sum_int(page.get("input_tokens") for page in pages),
        "output_tokens": _sum_int(page.get("output_tokens") for page in pages),
        "total_tokens": _sum_int(page.get("total_tokens") for page in pages),
        "provider_cached_input_tokens": _sum_int(page.get("provider_cached_input_tokens") for page in pages),
    }
    return {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "prompt_version": NOTE_PROMPT_VERSION,
        "cache": {
            "mode": cache_mode,
            "dir": _display_path(cache_dir, output_root),
        },
        "request": {
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
        },
        "summary": summary,
        "pages": pages,
    }


def _render_generation_info(usage_report: dict[str, Any]) -> list[str]:
    summary = usage_report["summary"]
    cache_mode = usage_report["cache"]["mode"]
    return [
        "## 生成信息",
        "",
        f"- LLM provider：{usage_report['provider']}",
        f"- 模型：{usage_report['model']}",
        f"- 缓存模式：{cache_mode}",
        f"- 本地缓存命中：{summary['local_cache_hits']} / {summary['pages_total']} 页",
        f"- 实际 LLM 调用：{summary['llm_calls']} 页",
        "- 详细用量与缓存信息：`llm_usage.json`",
        "",
    ]


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
