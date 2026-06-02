from __future__ import annotations

from pathlib import Path
from typing import Any

from slidenote.llm import LLMClient as _DefaultLLMClient, SYSTEM_PROMPT
from slidenote.llm_cache import LLM_CACHE_SCHEMA_VERSION, LLMCache, make_cache_key, sha256_text, stable_json
from slidenote.models import Deck

from .assembly import NoteContext, _display_path
from .prompt_payload import _prompt_brief_hash, _prompt_deck_brief, _prompt_slide_scope
from .prompt_templates import _llm_context_prompt, _llm_page_lecture_prompt, _llm_teaching_enrichment_prompt, _llm_weave_prompt
from .versions import NOTE_PROMPT_VERSION, PAGE_LECTURE_PROMPT_VERSION, TEACHING_ENRICHMENT_PROMPT_VERSION, WEAVE_PROMPT_VERSION

LLMClient = _DefaultLLMClient


def _make_llm_client(**kwargs):
    from . import orchestrator

    legacy_client = getattr(orchestrator, "LLMClient", LLMClient)
    client_class = legacy_client if legacy_client is not _DefaultLLMClient else LLMClient
    return client_class(**kwargs)


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
    note_profile: str,
    note_depth: str,
    note_language: str,
    term_policy: str,
    screenshot_policy: str,
    figure_placement: str,
    source_type: str,
    deck_brief: dict[str, Any] | None = None,
    content_guard: dict[str, Any] | None = None,
    force_refresh: bool = False,
) -> tuple[str, dict[str, Any]]:
    user_prompt = _llm_context_prompt(
        context,
        supports_image_input=supports_image_input,
        asset_map=asset_map,
        source_display=source_display,
        note_context=note_context,
        note_style=note_style,
        note_profile=note_profile,
        note_depth=note_depth,
        note_language=note_language,
        term_policy=term_policy,
        screenshot_policy=screenshot_policy,
        figure_placement=figure_placement,
        source_type=source_type,
        deck_brief=deck_brief,
        content_guard=content_guard,
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
        "note_profile": note_profile,
        "note_depth": note_depth,
        "note_language": note_language,
        "term_policy": term_policy,
        "screenshot_policy": screenshot_policy,
        "figure_placement": figure_placement,
        "deck_brief_hash": _prompt_brief_hash(prompt_brief),
        "content_guard_used": bool(content_guard),
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
        client = _make_llm_client(
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
                    "note_profile": note_profile,
                    "note_depth": note_depth,
                    "note_language": note_language,
                    "term_policy": term_policy,
                    "deck_brief_used": bool(prompt_brief),
                    "content_guard_used": bool(content_guard),
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
                "api_retries": response_usage.get("retries", 0),
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
    note_profile: str,
    note_depth: str,
    note_language: str,
    term_policy: str,
    page_neighborhood: int,
    section_title: str | None,
    screenshot_policy: str,
    figure_placement: str,
    deck_brief: dict[str, Any] | None = None,
    content_guard: dict[str, Any] | None = None,
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
        note_profile=note_profile,
        note_depth=note_depth,
        note_language=note_language,
        term_policy=term_policy,
        page_neighborhood=page_neighborhood,
        section_title=section_title,
        screenshot_policy=screenshot_policy,
        figure_placement=figure_placement,
        source_type=deck.source_type,
        deck_brief=deck_brief,
        content_guard=content_guard,
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
            "note_profile": note_profile,
            "note_depth": note_depth,
            "note_language": note_language,
            "term_policy": term_policy,
            "page_neighborhood": page_neighborhood,
            "screenshot_policy": screenshot_policy,
            "figure_placement": figure_placement,
            "deck_brief_used": bool(prompt_brief),
            "deck_brief_hash": _prompt_brief_hash(prompt_brief),
            "content_guard_used": bool(content_guard),
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
    note_profile: str,
    note_depth: str,
    note_language: str,
    term_policy: str,
    weave_dedup: str,
    deck_brief: dict[str, Any] | None = None,
    content_guard: dict[str, Any] | None = None,
    force_refresh: bool = False,
) -> tuple[str, dict[str, Any]]:
    user_prompt = _llm_weave_prompt(
        context=context,
        page_markdown_by_slide=page_markdown_by_slide,
        source_display=source_display,
        note_context=note_context,
        note_style=note_style,
        note_profile=note_profile,
        note_depth=note_depth,
        note_language=note_language,
        term_policy=term_policy,
        weave_dedup=weave_dedup,
        deck_brief=deck_brief,
        content_guard=content_guard,
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
            "note_profile": note_profile,
            "note_depth": note_depth,
            "note_language": note_language,
            "term_policy": term_policy,
            "weave_dedup": weave_dedup,
            "deck_brief_used": bool(prompt_brief),
            "deck_brief_hash": _prompt_brief_hash(prompt_brief),
            "content_guard_used": bool(content_guard),
        },
    )

def _generate_teaching_enrichment_context(
    context: NoteContext,
    woven_markdown: str,
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
    note_profile: str,
    note_depth: str,
    note_language: str,
    term_policy: str,
    deck_brief: dict[str, Any] | None = None,
    content_guard: dict[str, Any] | None = None,
    force_refresh: bool = False,
) -> tuple[str, dict[str, Any]]:
    user_prompt = _llm_teaching_enrichment_prompt(
        context=context,
        woven_markdown=woven_markdown,
        page_markdown_by_slide=page_markdown_by_slide,
        source_display=source_display,
        note_context=note_context,
        note_profile=note_profile,
        note_depth=note_depth,
        note_language=note_language,
        term_policy=term_policy,
        deck_brief=deck_brief,
        content_guard=content_guard,
    )
    prompt_brief = _prompt_deck_brief(deck_brief, [page.slide_id for page in context.pages])
    return _generate_cached_llm_text(
        context=NoteContext(
            id=f"teaching_{context.id}",
            kind=f"teaching_{context.kind}",
            title=context.title,
            pages=context.pages,
        ),
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
        prompt_version=TEACHING_ENRICHMENT_PROMPT_VERSION,
        generation_stage="teaching_enrichment",
        force_refresh=force_refresh,
        request_options={
            "source_display": source_display,
            "note_context": note_context,
            "note_profile": note_profile,
            "note_depth": note_depth,
            "note_language": note_language,
            "term_policy": term_policy,
            "deck_brief_used": bool(prompt_brief),
            "deck_brief_hash": _prompt_brief_hash(prompt_brief),
            "content_guard_used": bool(content_guard),
            "woven_markdown_hash": sha256_text(woven_markdown),
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
        client = _make_llm_client(
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
                "api_retries": response_usage.get("retries", 0),
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
