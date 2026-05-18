from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from slidenote.llm import LLMClient, resolve_provider_runtime
from slidenote.llm_cache import LLM_CACHE_SCHEMA_VERSION, LLMCache, make_cache_key, sha256_text, stable_json, utc_now_iso
from slidenote.models import Deck, SlidePage
from slidenote.table_understanding import table_preview

DECK_BRIEF_PROMPT_VERSION = "deck-brief-v1"

DECK_BRIEF_SYSTEM_PROMPT = (
    "You are a course-material cartographer. Build a global navigation map for lecture slides. "
    "Return strict JSON only. Do not write final notes, Markdown, apologies, or task explanations."
)


def build_deck_brief(
    deck: Deck,
    output_root: Path,
    section_plan: dict[str, Any] | None = None,
    provider: str = "openai",
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    cache_mode: str = "on",
    cache_dir: Path | None = None,
    max_output_tokens: int = 3000,
    temperature: float | None = 0.0,
) -> dict[str, Any]:
    runtime = resolve_provider_runtime(provider, model=model, base_url=base_url)
    resolved_cache_dir = (cache_dir or (output_root / ".cache" / "llm")).resolve()
    cache = LLMCache(resolved_cache_dir, mode=cache_mode)
    prompt = _deck_brief_prompt(deck, section_plan)
    cache_key_payload = {
        "schema_version": LLM_CACHE_SCHEMA_VERSION,
        "prompt_version": DECK_BRIEF_PROMPT_VERSION,
        "generation_stage": "deck_brief",
        "provider": runtime["provider"],
        "model": runtime["model"],
        "base_url": runtime["base_url"],
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "page_digest": _page_digest(deck),
        "section_plan_hash": sha256_text(stable_json(_section_digest_payload(section_plan))),
        "system_prompt_hash": sha256_text(DECK_BRIEF_SYSTEM_PROMPT),
        "user_prompt_hash": sha256_text(prompt),
        "user_prompt": prompt,
    }
    cache_key = make_cache_key(cache_key_payload)
    cache_path = cache.path_for(cache_key)
    prompt_hash = sha256_text(stable_json(cache_key_payload))
    cached = cache.read(cache_key)
    usage: dict[str, Any] = {}
    cache_status = "local_hit"
    llm_call = False
    result_text = ""
    warnings: list[str] = []

    try:
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
            result = client.generate_with_usage(prompt, system_prompt=DECK_BRIEF_SYSTEM_PROMPT)
            llm_call = True
            result_text = result.text
            usage = result.usage or {}
            cache_status = "disabled" if cache_mode == "off" else "refresh" if cache_mode == "refresh" else "miss"
            written_path = cache.write(
                cache_key,
                {
                    "provider": runtime["provider"],
                    "model": runtime["model"],
                    "base_url": runtime["base_url"],
                    "prompt_version": DECK_BRIEF_PROMPT_VERSION,
                    "generation_stage": "deck_brief",
                    "request": {
                        "temperature": temperature,
                        "max_output_tokens": max_output_tokens,
                    },
                    "prompt_hash": prompt_hash,
                    "output_text": result_text,
                    "response_usage": usage,
                },
            )
            if written_path is not None:
                cache_path = written_path
    except Exception as exc:
        warnings.append(f"deck_brief_failed:{type(exc).__name__}:{exc}")
        return _report(
            deck=deck,
            output_root=output_root,
            brief=_empty_brief(),
            provider=str(runtime["provider"]),
            model=str(runtime["model"]),
            base_url=runtime["base_url"],
            cache_mode=cache_mode,
            cache_status="error",
            cache_path=cache_path,
            llm_call=llm_call,
            usage=usage,
            prompt_hash=prompt_hash,
            warnings=warnings,
        )

    parsed = _parse_json_object(result_text)
    if parsed is None:
        warnings.append("deck_brief_invalid_json")
        brief = _empty_brief()
    else:
        brief = _normalize_brief(parsed, deck=deck, section_plan=section_plan)

    return _report(
        deck=deck,
        output_root=output_root,
        brief=brief,
        provider=str(runtime["provider"]),
        model=str(runtime["model"]),
        base_url=runtime["base_url"],
        cache_mode=cache_mode,
        cache_status=cache_status,
        cache_path=cache_path,
        llm_call=llm_call,
        usage=usage,
        prompt_hash=prompt_hash,
        warnings=warnings,
    )


def render_deck_brief_markdown(report: dict[str, Any]) -> str:
    brief = report.get("brief") if isinstance(report.get("brief"), dict) else {}
    title = _str_or_none(brief.get("course_title")) or Path(str(report.get("source_path") or "Deck")).stem
    lines = [f"# Deck Brief: {title}", ""]
    summary = _str_or_none(brief.get("one_sentence_summary"))
    if summary:
        lines.extend([summary, ""])
    _render_list_section(lines, "Core Questions", brief.get("core_questions"))
    _render_dict_section(lines, "Chapter Outline", brief.get("chapter_outline"), ["title", "summary", "slide_ids"])
    _render_dict_section(lines, "Key Concepts", brief.get("key_concepts"), ["term", "definition", "first_slide_id"])
    _render_dict_section(lines, "Concept Dependencies", brief.get("concept_dependencies"), ["source", "target", "reason"])
    _render_dict_section(lines, "Page Roles", brief.get("page_roles"), ["slide_id", "role", "reason"])
    _render_dict_section(lines, "Cross-page Links", brief.get("cross_page_links"), ["from_slide_id", "to_slide_id", "reason"])
    _render_list_section(lines, "Writing Guidance", brief.get("writing_guidance"))
    warnings = report.get("warnings") or []
    if warnings:
        lines.extend(["## Warnings", ""])
        lines.extend(f"- {warning}" for warning in warnings)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def deck_brief_for_prompt(report: dict[str, Any] | None, slide_ids: list[int] | set[int] | None = None) -> dict[str, Any] | None:
    if not report or not isinstance(report, dict):
        return None
    brief = report.get("brief")
    if not isinstance(brief, dict) or not _brief_has_content(brief):
        return None
    wanted = {int(slide_id) for slide_id in slide_ids or [] if isinstance(slide_id, int)}
    prompt_brief = {
        "course_title": _str_or_none(brief.get("course_title")),
        "one_sentence_summary": _str_or_none(brief.get("one_sentence_summary")),
        "core_questions": _string_list(brief.get("core_questions"), limit=8),
        "chapter_outline": _filter_by_slide_ids(_dict_list(brief.get("chapter_outline"), limit=18), wanted),
        "key_concepts": _dict_list(brief.get("key_concepts"), limit=40),
        "concept_dependencies": _dict_list(brief.get("concept_dependencies"), limit=40),
        "page_roles": _filter_by_slide_ids(_dict_list(brief.get("page_roles"), limit=400), wanted),
        "cross_page_links": _filter_by_slide_ids(_dict_list(brief.get("cross_page_links"), limit=60), wanted),
        "writing_guidance": _string_list(brief.get("writing_guidance"), limit=8),
    }
    return {key: value for key, value in prompt_brief.items() if value not in (None, [], {})}


def _report(
    deck: Deck,
    output_root: Path,
    brief: dict[str, Any],
    provider: str,
    model: str,
    base_url: str | None,
    cache_mode: str,
    cache_status: str,
    cache_path: Path,
    llm_call: bool,
    usage: dict[str, Any],
    prompt_hash: str,
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "prompt_version": DECK_BRIEF_PROMPT_VERSION,
        "brief": brief,
        "summary": {
            "pages_total": len(deck.pages),
            "sections_total": len(brief.get("chapter_outline") or []),
            "key_concepts_total": len(brief.get("key_concepts") or []),
            "page_roles_total": len(brief.get("page_roles") or []),
            "cross_page_links_total": len(brief.get("cross_page_links") or []),
            "llm_call": llm_call,
            "local_cache_hits": 1 if cache_status == "local_hit" else 0,
            "input_tokens": usage.get("input_tokens") if llm_call else 0,
            "output_tokens": usage.get("output_tokens") if llm_call else 0,
            "total_tokens": usage.get("total_tokens") if llm_call else 0,
            "provider_cached_input_tokens": usage.get("provider_cached_input_tokens") if llm_call else 0,
        },
        "llm": {
            "provider": provider,
            "model": model,
            "base_url": base_url,
            "cache": {
                "mode": cache_mode,
                "status": cache_status,
                "file": _display_path(cache_path, output_root),
            },
            "llm_call": llm_call,
            "prompt_hash": prompt_hash,
            "input_tokens": usage.get("input_tokens") if llm_call else 0,
            "output_tokens": usage.get("output_tokens") if llm_call else 0,
            "total_tokens": usage.get("total_tokens") if llm_call else 0,
            "provider_cached_input_tokens": usage.get("provider_cached_input_tokens") if llm_call else 0,
            "cached_entry_usage": usage if not llm_call else None,
        },
        "warnings": warnings,
    }


def _deck_brief_prompt(deck: Deck, section_plan: dict[str, Any] | None) -> str:
    payload = {
        "task": "build_deck_brief",
        "source_file": Path(deck.source_path).stem,
        "source_type": deck.source_type,
        "sections": _section_digest_payload(section_plan),
        "pages": [_page_payload(page) for page in deck.pages],
    }
    return (
        "Build a global course map from this structured slide deck.\n"
        "This map is only background navigation for later page-by-page explanation. It is not the final note.\n"
        "Return strict JSON with this shape:\n"
        "{\n"
        '  "course_title": "short title or null",\n'
        '  "one_sentence_summary": "overall topic",\n'
        '  "core_questions": ["question the lecture answers"],\n'
        '  "chapter_outline": [{"title":"section title","summary":"what it covers","slide_ids":[1,2]}],\n'
        '  "key_concepts": [{"term":"concept","definition":"brief meaning","first_slide_id":1}],\n'
        '  "concept_dependencies": [{"source":"earlier concept","target":"later concept","reason":"why target depends on source"}],\n'
        '  "page_roles": [{"slide_id":1,"role":"definition|example|diagram|exercise|transition|summary|other","reason":"why"}],\n'
        '  "cross_page_links": [{"from_slide_id":1,"to_slide_id":5,"reason":"relationship to revisit"}],\n'
        '  "writing_guidance": ["brief advice for later note weaving"]\n'
        "}\n"
        "Guidelines:\n"
        "- Prefer concrete course concepts over generic descriptions.\n"
        "- Page roles must describe the role of each page in the lecture flow.\n"
        "- Cross-page links should identify where definitions, examples, diagrams, and applications connect.\n"
        "- Do not summarize away page-level content; later stages will still write each page in detail.\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _page_payload(page: SlidePage) -> dict[str, Any]:
    return {
        "slide_id": page.slide_id,
        "title": page.title,
        "page_modality": page.page_modality,
        "summary": _page_summary(page),
        "text_block_count": len(page.text_blocks),
        "table_count": len(page.tables),
        "image_count": len([image for image in page.images if not image.ignored]),
        "study_images": [
            {
                "id": image.id,
                "role": image.role,
                "importance_score": image.importance_score,
                "importance_reason": image.importance_reason,
                "visual_summary": _truncate(image.visual_summary, 180),
                "ocr_text": _truncate(image.ocr_text, 180),
            }
            for image in page.images
            if not image.ignored and (image.importance_score or 0.0) >= 0.4
        ][:5],
        "has_page_ocr": bool(page.page_ocr_text),
        "has_page_visual_summary": bool(page.page_visual_summary),
    }


def _page_summary(page: SlidePage, limit: int = 760) -> str:
    parts: list[str] = []
    if page.title:
        parts.append(page.title)
    parts.extend(block.content for block in page.text_blocks[:7] if block.content.strip())
    for table in page.tables[:2]:
        preview = table_preview(table, limit=320, raw_rows=4)
        if preview:
            parts.append(preview)
    if page.page_ocr_text:
        parts.append(page.page_ocr_text[:320])
    if page.page_visual_summary:
        parts.append(page.page_visual_summary[:320])
    for image in page.images[:4]:
        if image.ocr_text:
            parts.append(image.ocr_text[:180])
        if image.visual_summary:
            parts.append(image.visual_summary[:180])
    text = re.sub(r"\s+", " ", " ".join(parts)).strip()
    return _truncate(text, limit)


def _section_digest_payload(section_plan: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not section_plan or not isinstance(section_plan.get("sections"), list):
        return []
    result: list[dict[str, Any]] = []
    for section in section_plan.get("sections") or []:
        if not isinstance(section, dict):
            continue
        result.append(
            {
                "section_id": section.get("section_id"),
                "title": section.get("title"),
                "start_slide_id": section.get("start_slide_id"),
                "end_slide_id": section.get("end_slide_id"),
                "slide_ids": [slide_id for slide_id in section.get("slide_ids", []) if isinstance(slide_id, int)],
                "reason": section.get("reason"),
            }
        )
    return result


def _page_digest(deck: Deck) -> str:
    return sha256_text(stable_json([_page_payload(page) for page in deck.pages]))


def _parse_json_object(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    for candidate in (cleaned, _json_object_slice(cleaned)):
        if not candidate:
            continue
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _json_object_slice(text: str) -> str | None:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start : end + 1]


def _normalize_brief(parsed: dict[str, Any], deck: Deck, section_plan: dict[str, Any] | None) -> dict[str, Any]:
    raw = parsed.get("brief") if isinstance(parsed.get("brief"), dict) else parsed
    chapter_outline = _dict_list(raw.get("chapter_outline") or raw.get("chapters"), limit=60)
    if not chapter_outline:
        chapter_outline = [
            {
                "title": section.get("title"),
                "summary": section.get("reason"),
                "slide_ids": section.get("slide_ids", []),
            }
            for section in _section_digest_payload(section_plan)
        ]
    page_roles = _normalize_page_roles(raw.get("page_roles"), deck)
    return {
        "course_title": _str_or_none(raw.get("course_title") or raw.get("title") or raw.get("topic")),
        "one_sentence_summary": _str_or_none(raw.get("one_sentence_summary") or raw.get("summary")),
        "core_questions": _string_list(raw.get("core_questions"), limit=20),
        "chapter_outline": chapter_outline,
        "key_concepts": _dict_list(raw.get("key_concepts") or raw.get("glossary"), limit=120),
        "concept_dependencies": _dict_list(raw.get("concept_dependencies") or raw.get("dependencies"), limit=120),
        "page_roles": page_roles,
        "cross_page_links": _dict_list(raw.get("cross_page_links") or raw.get("cross_references"), limit=160),
        "writing_guidance": _string_list(raw.get("writing_guidance") or raw.get("guidance"), limit=20),
    }


def _normalize_page_roles(raw_roles: Any, deck: Deck) -> list[dict[str, Any]]:
    roles = _dict_list(raw_roles, limit=max(400, len(deck.pages) + 20))
    valid_ids = {page.slide_id for page in deck.pages}
    normalized: list[dict[str, Any]] = []
    seen: set[int] = set()
    for role in roles:
        slide_id = _int_or_none(role.get("slide_id") or role.get("page") or role.get("page_id"))
        if slide_id is None or slide_id not in valid_ids or slide_id in seen:
            continue
        role["slide_id"] = slide_id
        role.pop("page", None)
        role.pop("page_id", None)
        normalized.append(role)
        seen.add(slide_id)
    return normalized


def _empty_brief() -> dict[str, Any]:
    return {
        "course_title": None,
        "one_sentence_summary": None,
        "core_questions": [],
        "chapter_outline": [],
        "key_concepts": [],
        "concept_dependencies": [],
        "page_roles": [],
        "cross_page_links": [],
        "writing_guidance": [],
    }


def _brief_has_content(brief: dict[str, Any]) -> bool:
    return any(
        brief.get(key)
        for key in (
            "course_title",
            "one_sentence_summary",
            "core_questions",
            "chapter_outline",
            "key_concepts",
            "concept_dependencies",
            "page_roles",
            "cross_page_links",
            "writing_guidance",
        )
    )


def _filter_by_slide_ids(items: list[dict[str, Any]], wanted: set[int]) -> list[dict[str, Any]]:
    if not wanted:
        return items
    result: list[dict[str, Any]] = []
    for item in items:
        item_ids = _slide_ids_from_item(item)
        if not item_ids or item_ids.intersection(wanted):
            result.append(item)
    return result


def _slide_ids_from_item(item: dict[str, Any]) -> set[int]:
    ids: set[int] = set()
    for key in ("slide_id", "first_slide_id", "from_slide_id", "to_slide_id", "start_slide_id", "end_slide_id"):
        value = _int_or_none(item.get(key))
        if value is not None:
            ids.add(value)
    raw_ids = item.get("slide_ids") or item.get("page_ids") or item.get("pages")
    if isinstance(raw_ids, list):
        for raw in raw_ids:
            value = _int_or_none(raw)
            if value is not None:
                ids.add(value)
    return ids


def _dict_list(value: Any, limit: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            cleaned = {str(key): _clean_value(raw_value) for key, raw_value in item.items() if raw_value not in (None, "", [], {})}
            if cleaned:
                result.append(cleaned)
        elif isinstance(item, str) and item.strip():
            result.append({"text": item.strip()})
        if len(result) >= limit:
            break
    return result


def _string_list(value: Any, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            result.append(item.strip())
        elif isinstance(item, dict):
            text = _str_or_none(item.get("text") or item.get("title") or item.get("question") or item.get("guidance"))
            if text:
                result.append(text)
        if len(result) >= limit:
            break
    return result


def _clean_value(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return [_clean_value(item) for item in value if item not in (None, "", [], {})]
    if isinstance(value, dict):
        return {str(key): _clean_value(raw_value) for key, raw_value in value.items() if raw_value not in (None, "", [], {})}
    return value


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _truncate(text: str | None, limit: int) -> str:
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "..."


def _render_list_section(lines: list[str], title: str, values: Any) -> None:
    items = _string_list(values, limit=80)
    if not items:
        return
    lines.extend([f"## {title}", ""])
    lines.extend(f"- {item}" for item in items)
    lines.append("")


def _render_dict_section(lines: list[str], title: str, values: Any, keys: list[str]) -> None:
    items = _dict_list(values, limit=400)
    if not items:
        return
    lines.extend([f"## {title}", ""])
    for item in items:
        parts = []
        for key in keys:
            value = item.get(key)
            if value in (None, "", [], {}):
                continue
            parts.append(f"{key}: {value}")
        if parts:
            lines.append(f"- {'; '.join(parts)}")
    lines.append("")


def _display_path(path: Path, output_root: Path) -> str:
    try:
        return path.resolve().relative_to(output_root.resolve()).as_posix()
    except ValueError:
        return str(path)
