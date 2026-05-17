from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from slidenote.llm import LLMClient, SYSTEM_PROMPT, resolve_provider_runtime
from slidenote.llm_cache import LLM_CACHE_SCHEMA_VERSION, LLMCache, make_cache_key, sha256_text, stable_json, utc_now_iso
from slidenote.models import Deck

from .assembly import (
    NoteContext,
    _compose_final_markdown,
    _display_path,
    _ensure_grounded_figures,
    _postprocess_llm_markdown,
    _select_note_contexts,
    _sum_int,
)
from .prompts import (
    _llm_context_prompt,
    _llm_page_lecture_prompt,
    _llm_weave_prompt,
    _prompt_brief_hash,
    _prompt_deck_brief,
    _section_title_by_slide,
)


NOTE_PROMPT_VERSION = "note-context-v3"
PAGE_LECTURE_PROMPT_VERSION = "page-lecture-v3"
WEAVE_PROMPT_VERSION = "weave-v3"


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
    note_strategy: str,
    note_depth: str,
    note_language: str,
    term_policy: str,
    weave_dedup: str,
    page_neighborhood: int,
    screenshot_policy: str,
    figure_placement: str,
    section_plan: dict[str, Any] | None,
    deck_brief: dict[str, Any] | None,
) -> "NoteGenerationResult":  # string annotation to avoid circular import
    from . import NoteGenerationResult

    runtime = resolve_provider_runtime(provider, model=model, base_url=base_url)
    resolved_provider = str(runtime["provider"])
    resolved_model = str(runtime["model"])
    resolved_base_url = runtime["base_url"]
    supports_image_input = bool(runtime["supports_image_input"])
    resolved_cache_dir = (cache_dir or (output_root / ".cache" / "llm")).resolve()
    cache = LLMCache(resolved_cache_dir, mode=cache_mode)
    if note_strategy == "lecture-weave":
        return _generate_notes_with_lecture_weave(
            deck=deck,
            output_root=output_root,
            provider=resolved_provider,
            model=resolved_model,
            api_key=api_key,
            base_url=resolved_base_url,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            cache_mode=cache_mode,
            cache=cache,
            cache_dir=resolved_cache_dir,
            concurrency=concurrency,
            refresh_slide_ids=refresh_slide_ids,
            progress_callback=progress_callback,
            asset_map=asset_map,
            asset_mode=asset_mode,
            source_display=source_display,
            note_context=note_context,
            note_style=note_style,
            note_depth=note_depth,
            note_language=note_language,
            term_policy=term_policy,
            weave_dedup=weave_dedup,
            page_neighborhood=page_neighborhood,
            screenshot_policy=screenshot_policy,
            figure_placement=figure_placement,
            supports_image_input=supports_image_input,
            section_plan=section_plan,
            deck_brief=deck_brief,
        )

    contexts = _select_note_contexts(deck, note_context, section_plan=section_plan)
    from .assembly import _resolved_context_mode
    resolved_note_context = _resolved_context_mode(deck, note_context)
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
            note_language=note_language,
            term_policy=term_policy,
            screenshot_policy=screenshot_policy,
            figure_placement=figure_placement,
            source_type=deck.source_type,
            deck_brief=deck_brief,
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
    final_chunks: dict[str, str] = {}
    for context in contexts:
        content, context_record = context_results[context.id]
        usage_contexts.append(context_record)
        final_chunks[context.id] = content

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
        note_strategy=note_strategy,
        note_depth=note_depth,
        note_language=note_language,
        term_policy=term_policy,
        weave_dedup=weave_dedup,
        page_neighborhood=page_neighborhood,
        asset_mode=asset_mode,
        screenshot_policy=screenshot_policy,
        figure_placement=figure_placement,
        deck_brief=deck_brief,
    )
    markdown = _compose_final_markdown(
        deck=deck,
        contexts=contexts,
        final_chunks=final_chunks,
        section_plan=section_plan,
        source_display=source_display,
    )
    markdown = _ensure_grounded_figures(markdown, deck, asset_map, source_display, figure_placement)
    return NoteGenerationResult(markdown=markdown, llm_usage=usage_report)


def _generate_notes_with_lecture_weave(
    deck: Deck,
    output_root: Path,
    provider: str,
    model: str,
    api_key: str | None,
    base_url: str | None,
    max_output_tokens: int,
    temperature: float | None,
    cache_mode: str,
    cache: LLMCache,
    cache_dir: Path,
    concurrency: int,
    refresh_slide_ids: set[int] | None,
    progress_callback: Callable[[dict[str, Any]], None] | None,
    asset_map: dict[str, str],
    asset_mode: str,
    source_display: str,
    note_context: str,
    note_style: str,
    note_depth: str,
    note_language: str,
    term_policy: str,
    weave_dedup: str,
    page_neighborhood: int,
    screenshot_policy: str,
    figure_placement: str,
    supports_image_input: bool,
    section_plan: dict[str, Any] | None,
    deck_brief: dict[str, Any] | None,
) -> "NoteGenerationResult":  # string annotation to avoid circular import
    from . import NoteGenerationResult

    refresh_ids = refresh_slide_ids or set()
    workers = max(1, int(concurrency or 1))
    page_contexts = [
        NoteContext(id=f"p{page.slide_id}", kind="page_note", title=page.title or f"\u7b2c {page.slide_id} \u9875", pages=[page])
        for page in deck.pages
    ]
    section_titles = _section_title_by_slide(deck, section_plan=section_plan)
    page_results: dict[str, tuple[str, dict[str, Any]]] = {}

    def process_page(context: NoteContext) -> tuple[str, str, dict[str, Any]]:
        page = context.pages[0]
        content, record = _generate_page_lecture_context(
            deck=deck,
            context=context,
            output_root=output_root,
            cache=cache,
            cache_mode=cache_mode,
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            supports_image_input=supports_image_input,
            force_refresh=page.slide_id in refresh_ids,
            asset_map=asset_map,
            asset_mode=asset_mode,
            source_display=source_display,
            note_style=note_style,
            note_depth=note_depth,
            note_language=note_language,
            term_policy=term_policy,
            page_neighborhood=page_neighborhood,
            section_title=section_titles.get(page.slide_id),
            screenshot_policy=screenshot_policy,
            figure_placement=figure_placement,
            deck_brief=deck_brief,
        )
        return context.id, _postprocess_llm_markdown(content, source_display=source_display), record

    if workers == 1:
        for context in page_contexts:
            context_id, content, record = process_page(context)
            page_results[context_id] = (content, record)
            if progress_callback:
                progress_callback(record)
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(process_page, context): context for context in page_contexts}
            for future in as_completed(futures):
                context_id, content, record = future.result()
                page_results[context_id] = (content, record)
                if progress_callback:
                    progress_callback(record)

    page_markdown_by_slide: dict[int, str] = {}
    page_records: list[dict[str, Any]] = []
    for context in page_contexts:
        content, record = page_results[context.id]
        page_markdown_by_slide[context.pages[0].slide_id] = content
        page_records.append(record)

    from .assembly import _resolved_context_mode
    resolved_note_context = _resolved_context_mode(deck, note_context)
    weave_contexts = _select_note_contexts(deck, note_context, section_plan=section_plan)
    weave_results: dict[str, tuple[str, dict[str, Any]]] = {}

    def process_weave(context: NoteContext) -> tuple[str, str, dict[str, Any]]:
        content, record = _generate_weave_context(
            context=context,
            page_markdown_by_slide=page_markdown_by_slide,
            output_root=output_root,
            cache=cache,
            cache_mode=cache_mode,
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            force_refresh=bool(refresh_ids.intersection({page.slide_id for page in context.pages})),
            source_display=source_display,
            note_context=resolved_note_context,
            note_style=note_style,
            note_depth=note_depth,
            note_language=note_language,
            term_policy=term_policy,
            weave_dedup=weave_dedup,
            deck_brief=deck_brief,
        )
        return context.id, _postprocess_llm_markdown(content, source_display=source_display), record

    if workers == 1:
        for context in weave_contexts:
            context_id, content, record = process_weave(context)
            weave_results[context_id] = (content, record)
            if progress_callback:
                progress_callback(record)
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(process_weave, context): context for context in weave_contexts}
            for future in as_completed(futures):
                context_id, content, record = future.result()
                weave_results[context_id] = (content, record)
                if progress_callback:
                    progress_callback(record)

    weave_records: list[dict[str, Any]] = []
    final_chunks: dict[str, str] = {}
    for context in weave_contexts:
        content, record = weave_results[context.id]
        final_chunks[context.id] = content
        weave_records.append(record)

    all_context_records = page_records + weave_records
    usage_report = _build_usage_report(
        deck=deck,
        provider=provider,
        model=model,
        base_url=base_url,
        cache_mode=cache_mode,
        cache_dir=cache_dir,
        output_root=output_root,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
        contexts=all_context_records,
        note_context=resolved_note_context,
        source_display=source_display,
        note_style=note_style,
        note_strategy="lecture-weave",
        note_depth=note_depth,
        note_language=note_language,
        term_policy=term_policy,
        weave_dedup=weave_dedup,
        page_neighborhood=page_neighborhood,
        asset_mode=asset_mode,
        screenshot_policy=screenshot_policy,
        figure_placement=figure_placement,
        page_contexts=page_records,
        weave_contexts=weave_records,
        deck_brief=deck_brief,
    )
    markdown = _compose_final_markdown(
        deck=deck,
        contexts=weave_contexts,
        final_chunks=final_chunks,
        section_plan=section_plan,
        source_display=source_display,
    )
    markdown = _ensure_grounded_figures(markdown, deck, asset_map, source_display, figure_placement)

    from .assembly import _build_page_notes_report, _build_weave_report, _render_page_notes_markdown

    page_notes = _build_page_notes_report(
        deck=deck,
        provider=provider,
        model=model,
        base_url=base_url,
        output_root=output_root,
        note_depth=note_depth,
        note_language=note_language,
        term_policy=term_policy,
        page_neighborhood=page_neighborhood,
        pages=page_contexts,
        page_markdown_by_slide=page_markdown_by_slide,
        page_records=page_records,
        deck_brief=deck_brief,
    )
    weave_report = _build_weave_report(
        deck=deck,
        output_root=output_root,
        note_context=resolved_note_context,
        note_depth=note_depth,
        note_language=note_language,
        term_policy=term_policy,
        weave_dedup=weave_dedup,
        contexts=weave_contexts,
        final_chunks=final_chunks,
        page_markdown_by_slide=page_markdown_by_slide,
        weave_records=weave_records,
        deck_brief=deck_brief,
    )
    return NoteGenerationResult(
        markdown=markdown,
        llm_usage=usage_report,
        page_notes=page_notes,
        page_notes_markdown=_render_page_notes_markdown(deck, page_notes),
        weave_report=weave_report,
    )


# ---------------------------------------------------------------------------
# LLM context generation + caching
# ---------------------------------------------------------------------------

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
    note_language: str,
    term_policy: str,
    screenshot_policy: str,
    figure_placement: str,
    source_type: str,
    deck_brief: dict[str, Any] | None = None,
    force_refresh: bool = False,
) -> tuple[str, dict[str, Any]]:
    user_prompt = _llm_context_prompt(
        context,
        supports_image_input=supports_image_input,
        asset_map=asset_map,
        source_display=source_display,
        note_context=note_context,
        note_style=note_style,
        note_language=note_language,
        term_policy=term_policy,
        screenshot_policy=screenshot_policy,
        figure_placement=figure_placement,
        source_type=source_type,
        deck_brief=deck_brief,
    )
    prompt_brief = _prompt_deck_brief(deck_brief, [page.slide_id for page in context.pages])
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
        "note_language": note_language,
        "term_policy": term_policy,
        "screenshot_policy": screenshot_policy,
        "figure_placement": figure_placement,
        "deck_brief_hash": _prompt_brief_hash(prompt_brief),
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
                    "note_language": note_language,
                    "term_policy": term_policy,
                    "deck_brief_used": bool(prompt_brief),
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


def _generate_page_lecture_context(
    deck: Deck,
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
    note_style: str,
    note_depth: str,
    note_language: str,
    term_policy: str,
    page_neighborhood: int,
    section_title: str | None,
    screenshot_policy: str,
    figure_placement: str,
    deck_brief: dict[str, Any] | None = None,
    force_refresh: bool = False,
) -> tuple[str, dict[str, Any]]:
    from .prompts import _prompt_slide_scope
    user_prompt = _llm_page_lecture_prompt(
        deck=deck,
        context=context,
        supports_image_input=supports_image_input,
        asset_map=asset_map,
        source_display=source_display,
        note_style=note_style,
        note_depth=note_depth,
        note_language=note_language,
        term_policy=term_policy,
        page_neighborhood=page_neighborhood,
        section_title=section_title,
        screenshot_policy=screenshot_policy,
        figure_placement=figure_placement,
        source_type=deck.source_type,
        deck_brief=deck_brief,
    )
    prompt_brief = _prompt_deck_brief(deck_brief, _prompt_slide_scope(deck, context.pages[0].slide_id, page_neighborhood))
    return _generate_cached_llm_text(
        context=context,
        output_root=output_root,
        cache=cache,
        cache_mode=cache_mode,
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
        user_prompt=user_prompt,
        prompt_version=PAGE_LECTURE_PROMPT_VERSION,
        generation_stage="page_note",
        force_refresh=force_refresh,
        request_options={
            "asset_mode": asset_mode,
            "source_display": source_display,
            "note_style": note_style,
            "note_depth": note_depth,
            "note_language": note_language,
            "term_policy": term_policy,
            "page_neighborhood": page_neighborhood,
            "screenshot_policy": screenshot_policy,
            "figure_placement": figure_placement,
            "deck_brief_used": bool(prompt_brief),
            "deck_brief_hash": _prompt_brief_hash(prompt_brief),
        },
    )


def _generate_weave_context(
    context: NoteContext,
    page_markdown_by_slide: dict[int, str],
    output_root: Path,
    cache: LLMCache,
    cache_mode: str,
    provider: str,
    model: str,
    api_key: str | None,
    base_url: str | None,
    max_output_tokens: int,
    temperature: float | None,
    source_display: str,
    note_context: str,
    note_style: str,
    note_depth: str,
    note_language: str,
    term_policy: str,
    weave_dedup: str,
    deck_brief: dict[str, Any] | None = None,
    force_refresh: bool = False,
) -> tuple[str, dict[str, Any]]:
    user_prompt = _llm_weave_prompt(
        context=context,
        page_markdown_by_slide=page_markdown_by_slide,
        source_display=source_display,
        note_context=note_context,
        note_style=note_style,
        note_depth=note_depth,
        note_language=note_language,
        term_policy=term_policy,
        weave_dedup=weave_dedup,
        deck_brief=deck_brief,
    )
    prompt_brief = _prompt_deck_brief(deck_brief, [page.slide_id for page in context.pages])
    return _generate_cached_llm_text(
        context=NoteContext(id=f"weave_{context.id}", kind=f"weave_{context.kind}", title=context.title, pages=context.pages),
        output_root=output_root,
        cache=cache,
        cache_mode=cache_mode,
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
        user_prompt=user_prompt,
        prompt_version=WEAVE_PROMPT_VERSION,
        generation_stage="weave",
        force_refresh=force_refresh,
        request_options={
            "source_display": source_display,
            "note_context": note_context,
            "note_style": note_style,
            "note_depth": note_depth,
            "note_language": note_language,
            "term_policy": term_policy,
            "weave_dedup": weave_dedup,
            "deck_brief_used": bool(prompt_brief),
            "deck_brief_hash": _prompt_brief_hash(prompt_brief),
        },
    )


def _generate_cached_llm_text(
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
    user_prompt: str,
    prompt_version: str,
    generation_stage: str,
    request_options: dict[str, Any],
    force_refresh: bool = False,
) -> tuple[str, dict[str, Any]]:
    cache_key_payload = {
        "schema_version": LLM_CACHE_SCHEMA_VERSION,
        "prompt_version": prompt_version,
        "generation_stage": generation_stage,
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
        "system_prompt_hash": sha256_text(SYSTEM_PROMPT),
        "user_prompt_hash": sha256_text(user_prompt),
        "user_prompt": user_prompt,
        **request_options,
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
    context_record["generation_stage"] = generation_stage

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
                "prompt_version": prompt_version,
                "generation_stage": generation_stage,
                "context_id": context.id,
                "context_kind": context.kind,
                "slide_ids": [page.slide_id for page in context.pages],
                "request": {
                    "temperature": temperature,
                    "max_output_tokens": max_output_tokens,
                    **request_options,
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


# ---------------------------------------------------------------------------
# Usage reporting
# ---------------------------------------------------------------------------

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
    note_strategy: str,
    note_depth: str,
    note_language: str,
    term_policy: str,
    weave_dedup: str,
    page_neighborhood: int,
    asset_mode: str,
    screenshot_policy: str,
    figure_placement: str,
    page_contexts: list[dict[str, Any]] | None = None,
    weave_contexts: list[dict[str, Any]] | None = None,
    deck_brief: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prompt_brief = _prompt_deck_brief(deck_brief)
    summary = {
        "pages_total": len(deck.pages),
        "contexts_total": len(contexts),
        "page_note_contexts": len(page_contexts or []),
        "weave_contexts": len(weave_contexts or []),
        "page_note_calls": sum(1 for context in (page_contexts or []) if context.get("llm_call")),
        "weave_calls": sum(1 for context in (weave_contexts or []) if context.get("llm_call")),
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
            "note_strategy": note_strategy,
            "note_depth": note_depth,
            "note_language": note_language,
            "term_policy": term_policy,
            "weave_dedup": weave_dedup,
            "page_neighborhood": page_neighborhood,
            "source_display": source_display,
            "note_style": note_style,
            "asset_mode": asset_mode,
            "screenshot_policy": screenshot_policy,
            "figure_placement": figure_placement,
            "deck_brief_used": bool(prompt_brief),
            "deck_brief_hash": _prompt_brief_hash(prompt_brief),
        },
        "summary": summary,
        "pages": contexts,
        "contexts": contexts,
        "page_contexts": page_contexts or [],
        "weave_contexts": weave_contexts or [],
    }


def _render_generation_info(usage_report: dict[str, Any]) -> list[str]:
    summary = usage_report["summary"]
    cache_mode = usage_report["cache"]["mode"]
    lines = [
        "## \u751f\u6210\u4fe1\u606f",
        "",
        f"- LLM provider\uff1a{usage_report['provider']}",
        f"- \u6a21\u578b\uff1a{usage_report['model']}",
        f"- \u7f13\u5b58\u6a21\u5f0f\uff1a{cache_mode}",
        f"- \u7b14\u8bb0\u7b56\u7565\uff1a{usage_report['request'].get('note_strategy', 'direct')}",
        f"- \u8f93\u51fa\u8bed\u8a00\uff1a{usage_report['request'].get('note_language', 'zh')}",
        f"- \u672f\u8bed\u7b56\u7565\uff1a{usage_report['request'].get('term_policy', 'bilingual')}",
        f"- \u751f\u6210\u4e0a\u4e0b\u6587\uff1a{summary.get('contexts_total', summary['pages_total'])} \u4e2a",
        f"- \u672c\u5730\u7f13\u5b58\u547d\u4e2d\uff1a{summary['local_cache_hits']} / {summary.get('contexts_total', summary['pages_total'])} \u4e2a\u4e0a\u4e0b\u6587",
        f"- \u5b9e\u9645 LLM \u8c03\u7528\uff1a{summary['llm_calls']} \u4e2a\u4e0a\u4e0b\u6587",
        "- \u8be6\u7ec6\u7528\u91cf\u4e0e\u7f13\u5b58\u4fe1\u606f\uff1a`llm_usage.json`",
        "",
    ]
    if usage_report["request"].get("note_strategy") == "lecture-weave":
        lines.insert(8, f"- \u9010\u9875\u6df1\u8bb2\u8c03\u7528\uff1a{summary.get('page_note_calls', 0)} \u4e2a")
        lines.insert(9, f"- \u7ae0\u8282\u7f16\u7ec7\u8c03\u7528\uff1a{summary.get('weave_calls', 0)} \u4e2a")
    return lines
