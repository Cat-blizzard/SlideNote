from __future__ import annotations

import json
import base64
import mimetypes
import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from slidenote.llm import LLMClient, SYSTEM_PROMPT, resolve_provider_runtime
from slidenote.llm_cache import LLM_CACHE_SCHEMA_VERSION, LLMCache, make_cache_key, sha256_text, stable_json, utc_now_iso
from slidenote.models import Deck, ImageAsset, SlidePage, TableBlock, TextBlock

NOTE_PROMPT_VERSION = "note-context-v1"

ASSET_MODES = {"bundle", "absolute", "embed"}
SOURCE_DISPLAY_MODES = {"hidden", "footnote", "inline"}
NOTE_CONTEXT_MODES = {"auto", "document", "section", "page"}
NOTE_STYLES = {"article", "faithful"}

SOURCE_COMMENT_PREFIX = "slidenote-source:"


@dataclass(slots=True)
class NoteGenerationResult:
    markdown: str
    llm_usage: dict[str, Any] | None = None
    asset_warnings: list[str] | None = None


@dataclass(frozen=True, slots=True)
class NoteContext:
    id: str
    kind: str
    title: str
    pages: list[SlidePage]


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
    asset_mode: str = "bundle",
    source_display: str = "hidden",
    note_context: str = "auto",
    note_style: str = "article",
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
        asset_mode=asset_mode,
        source_display=source_display,
        note_context=note_context,
        note_style=note_style,
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
    asset_mode: str = "bundle",
    source_display: str = "hidden",
    note_context: str = "auto",
    note_style: str = "article",
) -> NoteGenerationResult:
    _validate_generation_options(asset_mode, source_display, note_context, note_style)
    asset_map, asset_warnings = _prepare_note_assets(deck, output_root, asset_mode)
    if use_llm:
        result = _generate_notes_with_llm(
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
            asset_map=asset_map,
            asset_mode=asset_mode,
            source_display=source_display,
            note_context=note_context,
            note_style=note_style,
        )
        result.asset_warnings = (result.asset_warnings or []) + asset_warnings + _validate_markdown_image_links(
            result.markdown, output_root
        )
        return result
    markdown = _generate_notes_locally(deck, asset_map=asset_map, source_display=source_display, note_style=note_style)
    asset_warnings = asset_warnings + _validate_markdown_image_links(markdown, output_root)
    return NoteGenerationResult(markdown=markdown, asset_warnings=asset_warnings)


def _validate_generation_options(asset_mode: str, source_display: str, note_context: str, note_style: str) -> None:
    if asset_mode not in ASSET_MODES:
        raise ValueError(f"asset_mode must be one of: {', '.join(sorted(ASSET_MODES))}")
    if source_display not in SOURCE_DISPLAY_MODES:
        raise ValueError(f"source_display must be one of: {', '.join(sorted(SOURCE_DISPLAY_MODES))}")
    if note_context not in NOTE_CONTEXT_MODES:
        raise ValueError(f"note_context must be one of: {', '.join(sorted(NOTE_CONTEXT_MODES))}")
    if note_style not in NOTE_STYLES:
        raise ValueError(f"note_style must be one of: {', '.join(sorted(NOTE_STYLES))}")


def _generate_notes_locally(
    deck: Deck,
    asset_map: dict[str, str] | None = None,
    source_display: str = "hidden",
    note_style: str = "article",
) -> str:
    asset_map = asset_map or {}
    lines: list[str] = []
    title = Path(deck.source_path).stem
    lines.append(f"# {title}")
    lines.append("")
    if note_style == "faithful":
        lines.append("> 本地规则草稿，用于调试解析和覆盖率链路；正式改写请使用 `--use-llm`。")
        lines.append("")

    for page in deck.pages:
        heading = page.title or f"第 {page.slide_id} 页"
        lines.append(f"## 第 {page.slide_id} 页：{heading}")
        lines.append(_source_marker(page.slide_id, _page_element_ids(page), source_display))
        lines.append("")

        if page.page_screenshot:
            screenshot_path = _asset_display_path(page.page_screenshot, asset_map)
            lines.append(f"![第 {page.slide_id} 页截图]({screenshot_path})")
            lines.append(_source_marker(page.slide_id, [f"p{page.slide_id}_screenshot"], source_display))
            lines.append("")

        page_visual_lines = _render_page_visual_context(page, source_display=source_display)
        if page_visual_lines:
            lines.extend(page_visual_lines)
            lines.append("")

        if page.text_blocks:
            lines.extend(_render_text_blocks(page, source_display=source_display, note_style=note_style))
            lines.append("")

        for table in page.tables:
            lines.extend(_render_table(page, table, source_display=source_display))
            lines.append("")

        for image in page.images:
            if image.ignored:
                continue
            lines.extend(_render_image(page, image, asset_map=asset_map, source_display=source_display))
            lines.append("")

        visible_images = [image for image in page.images if not image.ignored]
        if not page.text_blocks and not page.tables and not visible_images and not page_visual_lines:
            lines.append(f"PPT 第 {page.slide_id} 页没有解析到可写入的文本、表格或嵌入图片。")
            lines.append("")

        if page.notes:
            lines.append(f"讲者备注：{page.notes}")
            lines.append(_source_marker(page.slide_id, [f"p{page.slide_id}_notes"], source_display))
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


def _render_text_blocks(page: SlidePage, source_display: str, note_style: str) -> list[str]:
    lines: list[str] = []
    for block in page.text_blocks:
        if block.type == "title":
            content = _plain_block_text(block)
            lines.append(f"本页主题是“{content}”。")
        elif block.type == "bullet":
            content = _rewrite_block(block)
            if note_style == "article":
                lines.append(content)
            else:
                lines.append(f"本页列出了以下要点：{content}")
        else:
            content = _rewrite_block(block)
            lines.append(content)
        lines.append(_source_marker(page.slide_id, [block.id], source_display))
        lines.append("")
    return lines


def _render_page_visual_context(page: SlidePage, source_display: str) -> list[str]:
    lines: list[str] = []
    if page.page_visual_summary:
        lines.append(f"页截图视觉解析：{_ensure_sentence(page.page_visual_summary)}")
        lines.append(_source_marker(page.slide_id, [f"p{page.slide_id}_screenshot"], source_display))
    if page.page_ocr_text:
        if lines:
            lines.append("")
        lines.append("页截图 OCR 文字：")
        lines.extend(_quote_multiline(page.page_ocr_text))
        lines.append(_source_marker(page.slide_id, [f"p{page.slide_id}_ocr"], source_display))
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


def _render_table(page: SlidePage, table: TableBlock, source_display: str) -> list[str]:
    lines = [f"下表整理了第 {page.slide_id} 页中的表格内容。", _source_marker(page.slide_id, [table.id], source_display), ""]
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


def _render_image(page: SlidePage, image: ImageAsset, asset_map: dict[str, str], source_display: str) -> list[str]:
    caption = image.caption or f"第 {page.slide_id} 页图片"
    lines = [
        f"{caption}。",
        _source_marker(page.slide_id, [image.id], source_display),
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
    lines.append(f"![{caption}]({_asset_display_path(image.path, asset_map)})")
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
    asset_map: dict[str, str],
    asset_mode: str,
    source_display: str,
    note_context: str,
    note_style: str,
) -> NoteGenerationResult:
    runtime = resolve_provider_runtime(provider, model=model, base_url=base_url)
    resolved_provider = str(runtime["provider"])
    resolved_model = str(runtime["model"])
    resolved_base_url = runtime["base_url"]
    supports_image_input = bool(runtime["supports_image_input"])
    resolved_cache_dir = (cache_dir or (output_root / ".cache" / "llm")).resolve()
    cache = LLMCache(resolved_cache_dir, mode=cache_mode)

    contexts = _select_note_contexts(deck, note_context)
    resolved_note_context = _resolved_context_mode(deck, note_context)
    lines = [f"# {Path(deck.source_path).stem}", ""]
    refresh_ids = refresh_slide_ids or set()
    workers = max(1, int(concurrency or 1))
    context_results: dict[str, tuple[str, dict[str, Any]]] = {}

    def process(context: NoteContext) -> tuple[str, str, dict[str, Any]]:
        content, context_record = _generate_llm_context(
            context=context,
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
            force_refresh=bool(refresh_ids.intersection({page.slide_id for page in context.pages})),
            asset_map=asset_map,
            asset_mode=asset_mode,
            source_display=source_display,
            note_context=resolved_note_context,
            note_style=note_style,
        )
        content = _postprocess_llm_markdown(content, source_display=source_display)
        return context.id, content, context_record

    if workers == 1:
        for context in contexts:
            context_id, content, context_record = process(context)
            context_results[context_id] = (content, context_record)
            if progress_callback:
                progress_callback(context_record)
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(process, context): context for context in contexts}
            for future in as_completed(futures):
                context_id, content, context_record = future.result()
                context_results[context_id] = (content, context_record)
                if progress_callback:
                    progress_callback(context_record)

    usage_contexts: list[dict[str, Any]] = []
    for context in contexts:
        content, context_record = context_results[context.id]
        usage_contexts.append(context_record)
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
        contexts=usage_contexts,
        note_context=resolved_note_context,
        source_display=source_display,
        note_style=note_style,
        asset_mode=asset_mode,
    )
    lines.extend(_render_generation_info(usage_report))
    return NoteGenerationResult(markdown="\n".join(lines).rstrip() + "\n", llm_usage=usage_report)


def _generate_llm_context(
    context: NoteContext,
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
    asset_map: dict[str, str],
    asset_mode: str,
    source_display: str,
    note_context: str,
    note_style: str,
    force_refresh: bool = False,
) -> tuple[str, dict[str, Any]]:
    user_prompt = _llm_context_prompt(
        context,
        supports_image_input=supports_image_input,
        asset_map=asset_map,
        source_display=source_display,
        note_context=note_context,
        note_style=note_style,
    )
    cache_key_payload = {
        "schema_version": LLM_CACHE_SCHEMA_VERSION,
        "prompt_version": NOTE_PROMPT_VERSION,
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
        "asset_mode": asset_mode,
        "source_display": source_display,
        "note_context": note_context,
        "note_style": note_style,
        "system_prompt_hash": sha256_text(SYSTEM_PROMPT),
        "user_prompt_hash": sha256_text(user_prompt),
        "user_prompt": user_prompt,
    }
    cache_key = make_cache_key(cache_key_payload)
    cache_path = cache.path_for(cache_key)
    prompt_hash = sha256_text(stable_json(cache_key_payload))
    cached = None if force_refresh else cache.read(cache_key)
    context_record = _base_usage_context_record(
        context=context,
        cache_key=cache_key,
        cache_path=cache_path,
        output_root=output_root,
        prompt_hash=prompt_hash,
    )

    if cached:
        content = cached["output_text"]
        cached_usage = cached.get("response_usage") or {}
        context_record.update(
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
                "context_id": context.id,
                "context_kind": context.kind,
                "slide_ids": [page.slide_id for page in context.pages],
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
        context_record.update(
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

    context_record["note_chars"] = len(content)
    context_record["cache_file"] = _display_path(cache_path, output_root)
    return content, context_record


def _llm_page_prompt(page: SlidePage, supports_image_input: bool = False) -> str:
    context = NoteContext(id=f"p{page.slide_id}", kind="page", title=page.title or f"第 {page.slide_id} 页", pages=[page])
    return _llm_context_prompt(
        context,
        supports_image_input=supports_image_input,
        asset_map={},
        source_display="hidden",
        note_context="page",
        note_style="article",
    )


def _llm_context_prompt(
    context: NoteContext,
    supports_image_input: bool,
    asset_map: dict[str, str],
    source_display: str,
    note_context: str,
    note_style: str,
) -> str:
    payload = {
        "context_id": context.id,
        "context_kind": context.kind,
        "context_title": context.title,
        "pages": [_page_payload_for_prompt(page, asset_map, supports_image_input) for page in context.pages],
    }
    source_rule = {
        "hidden": (
            "在每个主要段落或图片后写 HTML 隐藏来源注释，格式严格为 "
            "`<!-- slidenote-source: p4:s4_t1,s4_t2 -->`；不要在可见正文里写元素 ID。"
        ),
        "footnote": (
            "每个主要段落末尾只显示简洁页码，例如 `（PPT 第 4 页）`，并继续附加 "
            "`<!-- slidenote-source: p4:s4_t1,s4_t2 -->` 隐藏来源注释。"
        ),
        "inline": "可以在正文中显示详细来源页码和元素 ID，同时也要保留隐藏来源注释。",
    }[source_display]
    context_rule = {
        "document": "这是整份材料的上下文，请写成一篇连续笔记。",
        "section": "这是同一章节或小节的一组页面，请写成一个连贯小节，不要逐页机械翻译。",
        "page": "这是单页调试上下文，请尽量完整覆盖该页元素，但仍要保持自然行文。",
        "auto": "这是自动选择的上下文，请根据内容写成连贯笔记。",
    }[note_context]
    style_rule = (
        "采用文章式课程笔记风格：把 bullet 改写成讲解型段落，合并重复表格与页码文本，避免清单堆砌。"
        if note_style == "article"
        else "采用保真风格：尽量保持原始条目顺序，但不要输出元叙述。"
    )
    image_rule = (
        "图片必须用真正的 Markdown 图片语法插入，不能放进反引号。"
        "如果 JSON 里有 ocr_text、visual_summary、page_ocr_text 或 page_visual_summary，要把它们和相关概念合并讲解。"
        "如果没有视觉摘要，不要写“未提供图片像素”“无法视觉解析”“建议查看原 PPT”等说明；只插入图片，不猜测图片内容。"
    )
    banned_rule = (
        "严禁输出与课程内容无关的元叙述，例如“好的，这是……”“以下是根据 JSON……”“笔记已严格遵循……”"
        "“覆盖了所有文本块……”等。不要出现“课程笔记”作为章节标题；全文 H1 已由系统生成，你只能使用 ## 或更低级标题。"
    )
    return (
        "请把下面的课程材料 JSON 改写成可以直接阅读的 Markdown 笔记。\n"
        f"{context_rule}\n"
        f"{style_rule}\n"
        "硬性要求：\n"
        "1. 覆盖重要 text block、table、image；不要遗漏定义、条件、例子、公式、图中 OCR/视觉摘要。\n"
        "2. 允许删去纯页码、重复表格壳、装饰性元素和无意义说明。\n"
        f"3. {source_rule}\n"
        f"4. {image_rule}\n"
        f"5. {banned_rule}\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _page_payload_for_prompt(page: SlidePage, asset_map: dict[str, str], supports_image_input: bool) -> dict[str, Any]:
    page_payload = asdict(page)
    if page_payload.get("page_screenshot"):
        page_payload["page_screenshot"] = _asset_display_path(page_payload["page_screenshot"], asset_map)
    if page_payload.get("images"):
        for image in page_payload["images"]:
            image["path"] = _asset_display_path(image["path"], asset_map)
            image["visual_status"] = (
                "image pixels are available to the model"
                if supports_image_input
                else "image pixels are not attached to this note-writing call; use only supplied OCR/visual_summary"
            )
    return page_payload


def _base_usage_context_record(
    context: NoteContext,
    cache_key: str,
    cache_path: Path,
    output_root: Path,
    prompt_hash: str,
) -> dict[str, Any]:
    return {
        "context_id": context.id,
        "context_kind": context.kind,
        "context_title": context.title,
        "slide_id": context.pages[0].slide_id if context.pages else None,
        "slide_ids": [page.slide_id for page in context.pages],
        "cache_key": cache_key,
        "cache_file": _display_path(cache_path, output_root),
        "prompt_hash": prompt_hash,
        "element_counts": {
            "text_blocks": sum(len(page.text_blocks) for page in context.pages),
            "tables": sum(len(page.tables) for page in context.pages),
            "images": sum(len(page.images) for page in context.pages),
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
    contexts: list[dict[str, Any]],
    note_context: str,
    source_display: str,
    note_style: str,
    asset_mode: str,
) -> dict[str, Any]:
    summary = {
        "pages_total": len(deck.pages),
        "contexts_total": len(contexts),
        "local_cache_hits": sum(1 for context in contexts if context.get("cache_status") == "local_hit"),
        "local_cache_misses": sum(1 for context in contexts if context.get("cache_status") == "miss"),
        "local_cache_refreshes": sum(1 for context in contexts if context.get("cache_status") == "refresh"),
        "cache_disabled_calls": sum(1 for context in contexts if context.get("cache_status") == "disabled"),
        "llm_calls": sum(1 for context in contexts if context.get("llm_call")),
        "input_tokens": _sum_int(context.get("input_tokens") for context in contexts),
        "output_tokens": _sum_int(context.get("output_tokens") for context in contexts),
        "total_tokens": _sum_int(context.get("total_tokens") for context in contexts),
        "provider_cached_input_tokens": _sum_int(context.get("provider_cached_input_tokens") for context in contexts),
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
            "note_context": note_context,
            "source_display": source_display,
            "note_style": note_style,
            "asset_mode": asset_mode,
        },
        "summary": summary,
        "pages": contexts,
        "contexts": contexts,
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
        f"- 生成上下文：{summary.get('contexts_total', summary['pages_total'])} 个",
        f"- 本地缓存命中：{summary['local_cache_hits']} / {summary.get('contexts_total', summary['pages_total'])} 个上下文",
        f"- 实际 LLM 调用：{summary['llm_calls']} 个上下文",
        "- 详细用量与缓存信息：`llm_usage.json`",
        "",
    ]


def _prepare_note_assets(deck: Deck, output_root: Path, asset_mode: str) -> tuple[dict[str, str], list[str]]:
    asset_map: dict[str, str] = {}
    warnings: list[str] = []
    seen_destinations: set[Path] = set()
    for rel_path, kind in _iter_note_asset_paths(deck):
        if rel_path in asset_map:
            continue
        source_path = _resolve_output_asset(output_root, rel_path)
        if not source_path.exists():
            warnings.append(f"Missing note asset: {rel_path}")
            continue
        if asset_mode == "absolute":
            asset_map[rel_path] = source_path.as_posix()
        elif asset_mode == "embed":
            embedded = _embed_asset(source_path)
            if embedded:
                asset_map[rel_path] = embedded
            else:
                warnings.append(f"Could not embed note asset: {rel_path}")
        else:
            destination = _bundled_asset_destination(output_root, rel_path, kind, seen_destinations)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination)
            asset_map[rel_path] = destination.relative_to(output_root).as_posix()
            seen_destinations.add(destination)
    return asset_map, warnings


def _iter_note_asset_paths(deck: Deck) -> list[tuple[str, str]]:
    paths: list[tuple[str, str]] = []
    for page in deck.pages:
        if page.page_screenshot:
            paths.append((page.page_screenshot, "screenshots"))
        for image in page.images:
            if not image.ignored:
                paths.append((image.path, "images"))
    return paths


def _resolve_output_asset(output_root: Path, path: str) -> Path:
    asset_path = Path(path)
    if asset_path.is_absolute():
        return asset_path
    return (output_root / asset_path).resolve()


def _bundled_asset_destination(output_root: Path, rel_path: str, kind: str, seen_destinations: set[Path]) -> Path:
    source = Path(rel_path)
    subdir = "screenshots" if kind == "screenshots" else "images"
    stem = source.stem or "asset"
    suffix = source.suffix or ".png"
    destination = output_root / "notes.assets" / subdir / f"{stem}{suffix}"
    counter = 2
    while destination in seen_destinations:
        destination = output_root / "notes.assets" / subdir / f"{stem}-{counter}{suffix}"
        counter += 1
    return destination


def _embed_asset(source_path: Path) -> str | None:
    try:
        data = source_path.read_bytes()
    except OSError:
        return None
    mime_type, _ = mimetypes.guess_type(source_path.name)
    if not mime_type:
        mime_type = "application/octet-stream"
    return f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}"


def _asset_display_path(path: str, asset_map: dict[str, str]) -> str:
    return asset_map.get(path, path)


def _validate_markdown_image_links(markdown: str, output_root: Path) -> list[str]:
    warnings: list[str] = []
    for target in re.findall(r"!\[[^\]]*]\(([^)]+)\)", markdown):
        cleaned = target.strip().strip("<>")
        if not cleaned or cleaned.startswith(("data:", "http://", "https://")):
            continue
        path = Path(cleaned)
        if path.is_absolute():
            candidate = path
        else:
            candidate = output_root / path
        if not candidate.exists():
            warnings.append(f"Markdown image link target is missing: {cleaned}")
    for target in re.findall(r"`(!\[[^\]]*]\([^)]+\))`", markdown):
        warnings.append(f"Markdown image is wrapped as code and will not render: {target}")
    return warnings


def _source_marker(slide_id: int, element_ids: list[str], source_display: str) -> str:
    ids = [element_id for element_id in element_ids if element_id]
    comment = f"<!-- {SOURCE_COMMENT_PREFIX} p{slide_id}:{','.join(ids)} -->" if ids else ""
    if source_display == "hidden":
        return comment
    if source_display == "footnote":
        return f"（PPT 第 {slide_id} 页） {comment}".rstrip()
    detail = "、".join(ids)
    return f"【对应 PPT：第 {slide_id} 页，元素 {detail}】 {comment}".rstrip()


def _page_element_ids(page: SlidePage) -> list[str]:
    ids = [block.id for block in page.text_blocks]
    ids.extend(table.id for table in page.tables)
    ids.extend(image.id for image in page.images if not image.ignored)
    return ids


def _select_note_contexts(deck: Deck, requested: str) -> list[NoteContext]:
    resolved = _resolved_context_mode(deck, requested)
    if resolved == "document":
        return [NoteContext(id="doc", kind="document", title=Path(deck.source_path).stem, pages=list(deck.pages))]
    if resolved == "page":
        return [
            NoteContext(id=f"p{page.slide_id}", kind="page", title=page.title or f"第 {page.slide_id} 页", pages=[page])
            for page in deck.pages
        ]
    return _section_contexts(deck)


def _resolved_context_mode(deck: Deck, requested: str) -> str:
    if requested != "auto":
        return requested
    if len(deck.pages) <= 12 and _structured_char_count(deck) <= 16_000:
        return "document"
    return "section"


def _structured_char_count(deck: Deck) -> int:
    total = 0
    for page in deck.pages:
        total += sum(len(block.content) for block in page.text_blocks)
        total += sum(len(cell) for table in page.tables for row in table.rows for cell in row)
        total += len(page.page_ocr_text or "") + len(page.page_visual_summary or "")
        total += sum(len(image.ocr_text or "") + len(image.visual_summary or "") for image in page.images)
    return total


def _section_contexts(deck: Deck) -> list[NoteContext]:
    if not deck.pages:
        return []
    boundaries = _section_boundaries(deck)
    if len(boundaries) <= 1:
        boundaries = [deck.pages[index].slide_id for index in range(0, len(deck.pages), 8)]
    contexts: list[NoteContext] = []
    slide_to_index = {page.slide_id: index for index, page in enumerate(deck.pages)}
    boundary_indexes = sorted({slide_to_index[slide_id] for slide_id in boundaries if slide_id in slide_to_index})
    if not boundary_indexes or boundary_indexes[0] != 0:
        boundary_indexes.insert(0, 0)
    for position, start_index in enumerate(boundary_indexes):
        end_index = boundary_indexes[position + 1] if position + 1 < len(boundary_indexes) else len(deck.pages)
        pages = deck.pages[start_index:end_index]
        if not pages:
            continue
        title = _context_title(pages, position + 1)
        contexts.append(NoteContext(id=f"sec{position + 1}", kind="section", title=title, pages=pages))
    return contexts


def _section_boundaries(deck: Deck) -> list[int]:
    outline_titles = _outline_titles(deck)
    boundaries = [deck.pages[0].slide_id]
    for page in deck.pages[1:]:
        title = _normalize_heading_text(page.title or "")
        if not title or "目录" in title or title.lower() == "contents":
            continue
        if any(title == outline or title in outline or outline in title for outline in outline_titles):
            boundaries.append(page.slide_id)
        elif _looks_like_section_title_page(page):
            boundaries.append(page.slide_id)
    return sorted(set(boundaries))


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


def _postprocess_llm_markdown(markdown: str, source_display: str) -> str:
    text = _unwrap_code_images(markdown)
    text = _remove_meta_paragraphs(text)
    text = _normalize_chunk_headings(text)
    text = _convert_visible_sources(text, source_display)
    return text.strip()


def _unwrap_code_images(markdown: str) -> str:
    return re.sub(r"`(!\[[^\]]*]\([^)]+\))`", r"\1", markdown)


def _remove_meta_paragraphs(markdown: str) -> str:
    paragraphs = re.split(r"\n\s*\n", markdown)
    kept = [paragraph.strip() for paragraph in paragraphs if paragraph.strip() and not _is_meta_paragraph(paragraph)]
    return "\n\n".join(kept)


def _is_meta_paragraph(paragraph: str) -> bool:
    normalized = " ".join(line.strip() for line in paragraph.splitlines() if line.strip())
    banned_patterns = [
        "好的，这是",
        "好的，我将",
        "以下是根据",
        "下面是依据",
        "根据您提供的 JSON",
        "根据你提供的 JSON",
        "课程材料 JSON",
        "笔记已严格遵循",
        "严格遵循全部硬性要求",
        "覆盖了所有文本块",
        "覆盖每一个文本块",
        "每段均标注",
        "每一段都标注",
        "未提供图片像素",
        "未提供图像像素",
        "未提供图片的 OCR",
        "未提供该截图的 OCR",
        "未进行视觉解析",
        "无法进行具体描述",
        "无法进一步说明",
        "无法对截图内容",
        "建议在原始幻灯片",
        "若需了解图片具体内容",
        "图片留作原始证据",
        "仅作为证据保留",
    ]
    return any(pattern in normalized for pattern in banned_patterns)


def _normalize_chunk_headings(markdown: str) -> str:
    lines: list[str] = []
    for line in markdown.splitlines():
        match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if not match:
            lines.append(line)
            continue
        text = re.sub(r"^课程笔记[：:\s-]*", "", match.group(2).strip())
        if not text:
            continue
        level = max(2, len(match.group(1)))
        lines.append("#" * level + " " + text)
    return "\n".join(lines)


def _convert_visible_sources(markdown: str, source_display: str) -> str:
    if source_display == "inline":
        return _ensure_source_comments_for_inline(markdown)

    def replace(match: re.Match[str]) -> str:
        citation = match.group(0)
        element_ids = re.findall(r"\bs\d+_(?:t|tbl|img)\d+\b", citation)
        slide_match = re.search(r"第\s*(\d+)\s*页", citation)
        if not slide_match:
            return ""
        slide_id = int(slide_match.group(1))
        if source_display == "footnote":
            return _source_marker(slide_id, element_ids, "footnote")
        return _source_marker(slide_id, element_ids, "hidden")

    return re.sub(r"【[^】]*?PPT[^】]*?】", replace, markdown)


def _ensure_source_comments_for_inline(markdown: str) -> str:
    def replace(match: re.Match[str]) -> str:
        citation = match.group(0)
        if SOURCE_COMMENT_PREFIX in citation:
            return citation
        element_ids = re.findall(r"\bs\d+_(?:t|tbl|img)\d+\b", citation)
        slide_match = re.search(r"第\s*(\d+)\s*页", citation)
        if not slide_match or not element_ids:
            return citation
        return f"{citation} {_source_marker(int(slide_match.group(1)), element_ids, 'hidden')}"

    return re.sub(r"【[^】]*?PPT[^】]*?】", replace, markdown)


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
