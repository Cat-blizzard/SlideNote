from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from slidenote.content_guard import REQUIRED_CONFIDENCE_THRESHOLD
from slidenote.exporting import clean_markdown_for_export
from slidenote.llm import LLMClient, resolve_provider_runtime
from slidenote.llm_cache import LLM_CACHE_SCHEMA_VERSION, LLMCache, make_cache_key, sha256_text, stable_json, utc_now_iso
from slidenote.models import Deck, TableBlock, TextBlock
from slidenote.utils import as_float, display_path

from .common import (
    IMPORTANCE_LABELS,
    QUESTION_TYPE_LABELS,
    _as_int,
    _clean_inline,
    _dict_list,
    _string_list,
)
from .questions import (
    _add_unique,
    _default_points,
    _infer_importance,
    _local_logic_chains,
    _local_methods,
    _local_pitfall,
    _local_questions,
    _local_why,
    _normalize_answer,
    _normalize_image_refs,
    _normalize_importance,
    _normalize_question_type,
    _point_from_text,
    _trim_text,
    build_question_quality_report,
)
from .exam import (
    _attach_inline_figure_refs,
    _build_final_exam,
    _build_section_study_pack,
    _build_wrong_answer_review,
    render_exam_html,
    render_exam_markdown,
    render_final_exam_answers_markdown,
    render_final_exam_markdown,
    render_wrong_answer_review_prompt,
)
from .review import (
    _headings_from_notes,
    render_review_markdown,
)

STUDY_PACK_MODES = {"off", "auto", "local", "llm"}

STUDY_PACK_PROMPT_VERSION = "study-pack-v1"

STUDY_PACK_SYSTEM_PROMPT = (
    "You are an exam-oriented course review designer. Return strict JSON only. "
    "Do not include Markdown fences, apologies, or task explanations."
)

def _build_local_data(
    deck: Deck,
    notes_markdown: str,
    section_plan: dict[str, Any] | None,
    deck_brief: dict[str, Any] | None,
    content_guard: dict[str, Any] | None,
    question_count: int,
) -> dict[str, Any]:
    title = _deck_title(deck, deck_brief)
    items = _collect_study_items(deck, notes_markdown, content_guard, limit=max(question_count * 2, 16))
    review = {
        "title": title,
        "summary": _local_summary(deck, deck_brief),
        "logic_chains": _local_logic_chains(deck, section_plan, deck_brief),
        "checklist": items,
        "methods": _local_methods(items),
    }
    exam = {
        "title": title,
        "subtitle": f"共 {min(question_count, len(items))} 题；建议先独立作答，再核对解析。",
        "questions": _local_questions(items, question_count),
    }
    return {"review": review, "exam": exam}

def _build_local_report_item(
    section: str,
    point: str,
    explanation: str,
    slide_id: int | None,
    importance: str = "key",
    role: str | None = None,
    image_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    clean_point = _clean_inline(point)
    clean_explanation = _clean_inline(explanation) or clean_point
    return {
        "section": section or "核心知识点",
        "importance": _infer_importance(clean_point + " " + clean_explanation, role),
        "point": clean_point or "知识点",
        "explanation": clean_explanation or "需要结合原始笔记复习。",
        "why": _local_why(_infer_importance(clean_point + " " + clean_explanation, role)),
        "pitfall": _local_pitfall(clean_point, role),
        "source_refs": [f"P{slide_id}"] if slide_id else [],
        "image_refs": image_refs or [],
    }

def _collect_study_items(
    deck: Deck,
    notes_markdown: str,
    content_guard: dict[str, Any] | None,
    limit: int,
) -> list[dict[str, Any]]:
    lookup = _element_lookup(deck)
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for guard_item in _guard_items(content_guard):
        element_id = str(guard_item.get("element_id") or "")
        element = lookup.get(element_id, {})
        preview = str(element.get("text") or guard_item.get("preview") or guard_item.get("reason") or "").strip()
        if not preview:
            continue
        section = str(element.get("section") or f"第 {guard_item.get('slide_id') or element.get('slide_id')} 页")
        item = _build_local_report_item(
            section=section,
            point=_point_from_text(preview),
            explanation=preview,
            slide_id=_as_int(guard_item.get("slide_id") or element.get("slide_id"), 0) or None,
            role=str(guard_item.get("learning_role") or element.get("kind") or ""),
        )
        if _add_unique(items, item, seen):
            continue
        if len(items) >= limit:
            return items

    for page in deck.pages:
        section = page.title or f"第 {page.slide_id} 页"
        for table in page.tables:
            text = table.table_conclusion or table.table_summary or _table_text(table)
            if text:
                item = _build_local_report_item(section, _point_from_text(text), text, page.slide_id, role="table_conclusion")
                _add_unique(items, item, seen)
        for image in page.images:
            if image.ignored:
                continue
            text = image.figure_explanation or image.visual_summary or image.ocr_text or image.caption
            if text:
                item = _build_local_report_item(
                    section,
                    _point_from_text(text),
                    text,
                    page.slide_id,
                    role="figure_explanation",
                    image_refs=[{"id": image.id, "title": image.caption or f"P{page.slide_id} 图示", "path": image.path, "source_ref": f"P{page.slide_id}"}],
                )
                _add_unique(items, item, seen)
        for block in page.text_blocks:
            text = _text_block_text(block)
            if _skip_text(text):
                continue
            item = _build_local_report_item(section, _point_from_text(text), text, page.slide_id, role=block.type)
            _add_unique(items, item, seen)
            if len(items) >= limit:
                return items

    for heading in _headings_from_notes(notes_markdown):
        item = _build_local_report_item("笔记结构", heading, heading, None, role="heading")
        _add_unique(items, item, seen)
        if len(items) >= limit:
            break
    if not items:
        items.append(
            {
                "section": "核心知识点",
                "importance": "key",
                "point": "课程核心内容",
                "explanation": "请回到 notes.md 中按章节复习主要定义、公式、图表和例子。",
                "why": "这是本次材料的主体学习目标。",
                "pitfall": "不要只背标题，要能解释概念之间的关系。",
                "source_refs": [],
            }
        )
    return items[:limit]

def _content_guard_for_prompt(content_guard: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not content_guard:
        return []
    result = []
    for item in _dict_list(content_guard.get("items"), limit=300):
        confidence = as_float(item.get("confidence"), 0.0)
        if item.get("must_explain") or confidence >= REQUIRED_CONFIDENCE_THRESHOLD:
            result.append(
                {
                    "slide_id": item.get("slide_id"),
                    "learning_role": item.get("learning_role"),
                    "confidence": confidence,
                    "reason": item.get("reason"),
                }
            )
    return result

def _coverage_for_prompt(coverage_report: dict[str, Any] | None) -> dict[str, Any]:
    if not coverage_report:
        return {}
    return {
        "total": coverage_report.get("total"),
        "covered": coverage_report.get("covered"),
        "missing": coverage_report.get("missing"),
        "coverage_ratio": coverage_report.get("coverage_ratio"),
        "required_visible_coverage": coverage_report.get("required_visible_coverage"),
    }

def _deck_digest(deck: Deck) -> str:
    payload = [
        {
            "slide_id": page.slide_id,
            "title": page.title,
            "texts": [_text_block_text(block) for block in page.text_blocks[:20]],
            "tables": [_table_text(table) for table in page.tables[:5]],
            "images": [image.visual_summary or image.ocr_text or image.caption for image in page.images[:8]],
        }
        for page in deck.pages
    ]
    return sha256_text(stable_json(payload))

def _deck_outline_for_prompt(deck: Deck, section_plan: dict[str, Any] | None, deck_brief: dict[str, Any] | None) -> dict[str, Any]:
    brief = deck_brief.get("brief") if isinstance(deck_brief, dict) and isinstance(deck_brief.get("brief"), dict) else {}
    return {
        "title": _deck_title(deck, deck_brief),
        "pages_total": len(deck.pages),
        "sections": _dict_list((section_plan or {}).get("sections"), limit=80),
        "core_questions": _string_list(brief.get("core_questions"), limit=12),
        "key_concepts": _dict_list(brief.get("key_concepts"), limit=80),
        "page_titles": [{"slide_id": page.slide_id, "title": page.title} for page in deck.pages[:300]],
    }

def _deck_title(deck: Deck, deck_brief: dict[str, Any] | None) -> str:
    brief = deck_brief.get("brief") if isinstance(deck_brief, dict) and isinstance(deck_brief.get("brief"), dict) else {}
    title = _clean_inline(brief.get("course_title"))
    if title:
        return title
    for page in deck.pages:
        title = _clean_inline(page.title)
        if title:
            return title
    return Path(deck.source_path).stem or "课程材料"

def _effective_mode(mode: str, use_llm: bool) -> str:
    if mode == "auto":
        return "llm" if use_llm else "local"
    return mode

def _element_lookup(deck: Deck) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for page in deck.pages:
        section = page.title or f"第 {page.slide_id} 页"
        for block in page.text_blocks:
            lookup[block.id] = {"slide_id": page.slide_id, "section": section, "kind": block.type, "text": _text_block_text(block)}
        for table in page.tables:
            lookup[table.id] = {"slide_id": page.slide_id, "section": section, "kind": "table", "text": table.table_conclusion or table.table_summary or _table_text(table)}
        for image in page.images:
            lookup[image.id] = {"slide_id": page.slide_id, "section": section, "kind": image.role or "image", "text": image.figure_explanation or image.visual_summary or image.ocr_text or image.caption or ""}
    return lookup

def _figure_table_notes(deck: Deck) -> list[dict[str, Any]]:
    notes: list[dict[str, Any]] = []
    for page in deck.pages:
        section = page.title or f"第 {page.slide_id} 页"
        for table in page.tables:
            explanation = _clean_inline(table.table_conclusion or table.table_summary or _table_text(table))
            if not explanation:
                continue
            notes.append(
                {
                    "id": table.id,
                    "kind": "table",
                    "section": section,
                    "title": f"P{page.slide_id} 表格",
                    "explanation": explanation,
                    "source_ref": f"P{page.slide_id}",
                    "source_ids": [table.id],
                }
            )
        for image in page.images:
            if image.ignored:
                continue
            explanation = _clean_inline(image.figure_explanation or image.visual_summary or image.ocr_text or image.caption)
            if not explanation:
                continue
            notes.append(
                {
                    "id": image.id,
                    "kind": "image",
                    "section": section,
                    "title": _clean_inline(image.caption) or f"P{page.slide_id} 图示",
                    "explanation": explanation,
                    "path": image.path,
                    "source_ref": f"P{page.slide_id}",
                    "source_ids": [image.id, *image.source_element_ids],
                    "importance_score": image.importance_score,
                }
            )
    return notes[:200]

def _generate_llm_data(
    deck: Deck,
    notes_markdown: str,
    output_root: Path,
    review_requested: bool,
    exam_requested: bool,
    question_count: int,
    provider: str,
    model: str | None,
    api_key: str | None,
    base_url: str | None,
    cache_mode: str,
    cache_dir: Path | None,
    max_output_tokens: int,
    temperature: float | None,
    note_language: str,
    section_plan: dict[str, Any] | None,
    deck_brief: dict[str, Any] | None,
    content_guard: dict[str, Any] | None,
    coverage_report: dict[str, Any] | None,
    source_map: dict[str, Any] | None,
    fallback: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[str]]:
    runtime = resolve_provider_runtime(provider, model=model, base_url=base_url)
    resolved_cache_dir = (cache_dir or (output_root / ".cache" / "llm")).resolve()
    cache = LLMCache(resolved_cache_dir, mode=cache_mode)
    prompt = _study_pack_prompt(
        deck=deck,
        notes_markdown=notes_markdown,
        review_requested=review_requested,
        exam_requested=exam_requested,
        question_count=question_count,
        note_language=note_language,
        section_plan=section_plan,
        deck_brief=deck_brief,
        content_guard=content_guard,
        coverage_report=coverage_report,
        source_map=source_map,
    )
    cache_key_payload = {
        "schema_version": LLM_CACHE_SCHEMA_VERSION,
        "prompt_version": STUDY_PACK_PROMPT_VERSION,
        "generation_stage": "study_pack",
        "provider": runtime["provider"],
        "model": runtime["model"],
        "base_url": runtime["base_url"],
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "review_requested": review_requested,
        "exam_requested": exam_requested,
        "question_count": question_count,
        "note_language": note_language,
        "deck_digest": _deck_digest(deck),
        "notes_hash": sha256_text(clean_markdown_for_export(notes_markdown)),
        "section_plan_hash": sha256_text(stable_json(section_plan or {})),
        "deck_brief_hash": sha256_text(stable_json(deck_brief or {})),
        "content_guard_hash": sha256_text(stable_json(content_guard or {})),
        "system_prompt_hash": sha256_text(STUDY_PACK_SYSTEM_PROMPT),
        "user_prompt_hash": sha256_text(prompt),
        "user_prompt": prompt,
    }
    cache_key = make_cache_key(cache_key_payload)
    cache_path = cache.path_for(cache_key)
    prompt_hash = sha256_text(stable_json(cache_key_payload))
    cached = cache.read(cache_key)
    cache_status = "local_hit"
    llm_call = False
    usage: dict[str, Any] = {}
    raw_text = ""
    warnings: list[str] = []

    try:
        if cached:
            raw_text = str(cached.get("output_text") or "")
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
            result = client.generate_with_usage(prompt, system_prompt=STUDY_PACK_SYSTEM_PROMPT)
            raw_text = result.text
            usage = result.usage or {}
            llm_call = True
            cache_status = "disabled" if cache_mode == "off" else "refresh" if cache_mode == "refresh" else "miss"
            written_path = cache.write(
                cache_key,
                {
                    "provider": runtime["provider"],
                    "model": runtime["model"],
                    "base_url": runtime["base_url"],
                    "prompt_version": STUDY_PACK_PROMPT_VERSION,
                    "generation_stage": "study_pack",
                    "request": {
                        "temperature": temperature,
                        "max_output_tokens": max_output_tokens,
                        "review_requested": review_requested,
                        "exam_requested": exam_requested,
                        "question_count": question_count,
                    },
                    "prompt_hash": prompt_hash,
                    "output_text": raw_text,
                    "response_usage": usage,
                },
            )
            if written_path is not None:
                cache_path = written_path
    except Exception as exc:
        warnings.append(f"study_pack_llm_failed:{type(exc).__name__}:{exc}")
        return None, _llm_record(runtime, cache_key, cache_path, output_root, prompt_hash, "error", llm_call, usage), warnings

    parsed = _parse_json_object(raw_text)
    if parsed is None:
        warnings.append("study_pack_invalid_json")
        return None, _llm_record(runtime, cache_key, cache_path, output_root, prompt_hash, cache_status, llm_call, usage), warnings

    normalized = _normalize_study_data(parsed, fallback=fallback, question_count=question_count)
    return normalized, _llm_record(runtime, cache_key, cache_path, output_root, prompt_hash, cache_status, llm_call, usage), warnings

def _guard_items(content_guard: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not content_guard:
        return []
    items = _dict_list(content_guard.get("items"), limit=1000)
    result = []
    for item in items:
        if item.get("must_explain") or as_float(item.get("confidence"), 0.0) >= REQUIRED_CONFIDENCE_THRESHOLD:
            result.append(item)
    return result

def _llm_record(
    runtime: dict[str, Any],
    cache_key: str,
    cache_path: Path,
    output_root: Path,
    prompt_hash: str,
    cache_status: str,
    llm_call: bool,
    usage: dict[str, Any],
) -> dict[str, Any]:
    return {
        "provider": runtime["provider"],
        "model": runtime["model"],
        "base_url": runtime["base_url"],
        "cache_key": cache_key,
        "cache_file": display_path(cache_path, output_root),
        "prompt_hash": prompt_hash,
        "cache_status": cache_status,
        "llm_call": llm_call,
        "input_tokens": usage.get("input_tokens") if llm_call else 0,
        "output_tokens": usage.get("output_tokens") if llm_call else 0,
        "total_tokens": usage.get("total_tokens") if llm_call else 0,
        "provider_cached_input_tokens": usage.get("provider_cached_input_tokens") if llm_call else 0,
        "provider_usage": usage if llm_call else {},
    }

def _local_summary(deck: Deck, deck_brief: dict[str, Any] | None) -> str:
    brief = deck_brief.get("brief") if isinstance(deck_brief, dict) and isinstance(deck_brief.get("brief"), dict) else {}
    summary = _clean_inline(brief.get("one_sentence_summary"))
    if summary:
        return summary
    return f"本复习清单基于 {len(deck.pages)} 页课程材料生成，优先覆盖定义、公式、表格结论、图示解释和容易混淆的概念。"

def _normalize_exam(raw: dict[str, Any] | None, fallback: dict[str, Any], question_count: int) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    questions = []
    for index, question in enumerate(_dict_list(data.get("questions"), limit=question_count), start=1):
        qtype = _normalize_question_type(question.get("type"))
        normalized = {
            "id": _clean_inline(question.get("id")) or f"q{index}",
            "type": qtype,
            "points": _as_int(question.get("points"), _default_points(qtype)),
            "question": _clean_inline(question.get("question")) or "请解释本节核心知识点。",
            "answer": _normalize_answer(question.get("answer"), qtype),
            "explanation": _clean_inline(question.get("explanation")),
            "pitfall": _clean_inline(question.get("pitfall")),
            "source_refs": _string_list(question.get("source_refs"), limit=12),
            "image_refs": _normalize_image_refs(question.get("image_refs")),
        }
        if qtype == "choice":
            options = _string_list(question.get("options"), limit=8)
            if len(options) < 2:
                normalized["type"] = "short"
                normalized["answer"] = _clean_inline(question.get("answer")) or normalized["explanation"]
            else:
                normalized["options"] = options
                answer_index = _as_int(normalized["answer"], 0)
                normalized["answer"] = min(max(answer_index, 0), len(options) - 1)
        questions.append(normalized)
    if not questions:
        questions = list((fallback.get("exam") if "exam" in fallback else fallback).get("questions") or [])
    return {
        "title": _clean_inline(data.get("title")) or fallback.get("title") or "课程自测",
        "subtitle": _clean_inline(data.get("subtitle")) or fallback.get("subtitle") or "",
        "questions": questions[:question_count],
    }

def _normalize_review(raw: dict[str, Any] | None, fallback: dict[str, Any]) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    checklist = []
    for item in _dict_list(data.get("checklist"), limit=400):
        checklist.append(
            {
                "section": _clean_inline(item.get("section")) or "核心知识点",
                "importance": _normalize_importance(item.get("importance")),
                "point": _clean_inline(item.get("point")) or "知识点",
                "explanation": _clean_inline(item.get("explanation")) or "需要结合原始笔记复习。",
                "why": _clean_inline(item.get("why")),
                "pitfall": _clean_inline(item.get("pitfall")),
                "source_refs": _string_list(item.get("source_refs"), limit=12),
            }
        )
    if not checklist:
        checklist = list(fallback.get("checklist") or [])
    return {
        "title": _clean_inline(data.get("title")) or fallback.get("title") or "课程复习",
        "summary": _clean_inline(data.get("summary")) or fallback.get("summary") or "",
        "logic_chains": _dict_list(data.get("logic_chains"), limit=40) or fallback.get("logic_chains") or [],
        "checklist": checklist,
        "methods": _dict_list(data.get("methods"), limit=80) or fallback.get("methods") or [],
    }

def _normalize_study_data(raw: dict[str, Any], fallback: dict[str, Any], question_count: int) -> dict[str, Any]:
    review = raw.get("review") if isinstance(raw.get("review"), dict) else fallback.get("review")
    exam = raw.get("exam") if isinstance(raw.get("exam"), dict) else fallback.get("exam")
    normalized_review = _normalize_review(review, fallback.get("review") or {})
    normalized_exam = _normalize_exam(exam, fallback.get("exam") or {}, question_count)
    return {"review": normalized_review, "exam": normalized_exam}

def _parse_json_object(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None

def _skip_text(text: str) -> bool:
    clean = _clean_inline(text)
    if len(clean) < 3:
        return True
    if re.fullmatch(r"[\d\s/._:-]+", clean):
        return True
    if len(clean) <= 12 and any(token in clean.lower() for token in ("email", "@", "http", "www")):
        return True
    return False

def _source_map_for_prompt(source_map: dict[str, Any] | None) -> dict[str, Any]:
    if not source_map:
        return {}
    return {
        "note_blocks": len(source_map.get("note_blocks") or []),
        "default_display_mode": source_map.get("default_display_mode"),
    }

def _study_pack_prompt(
    deck: Deck,
    notes_markdown: str,
    review_requested: bool,
    exam_requested: bool,
    question_count: int,
    note_language: str,
    section_plan: dict[str, Any] | None,
    deck_brief: dict[str, Any] | None,
    content_guard: dict[str, Any] | None,
    coverage_report: dict[str, Any] | None,
    source_map: dict[str, Any] | None,
) -> str:
    payload = {
        "task": "build_exam_review_pack",
        "source_file": Path(deck.source_path).name,
        "source_type": deck.source_type,
        "requested_outputs": {
            "review": review_requested,
            "exam": exam_requested,
            "question_count": question_count,
            "language": note_language,
        },
        "deck_outline": _deck_outline_for_prompt(deck, section_plan, deck_brief),
        "high_value_items": _content_guard_for_prompt(content_guard),
        "figure_table_notes": _figure_table_notes(deck),
        "coverage_summary": _coverage_for_prompt(coverage_report),
        "source_map_summary": _source_map_for_prompt(source_map),
        "notes_markdown": _trim_text(clean_markdown_for_export(notes_markdown), 42000),
    }
    return (
        "请基于 SlideNote 已生成的保真课程笔记，生成考试复习包。"
        "复习包要服务于短期备考，但不能牺牲来源忠实性；只使用输入材料中的信息，不要编造教材外事实。\n"
        "review 的目标：把知识点重组成可扫读的考点清单，讲清「是什么、为什么、怎么用/易错点」，并给出逻辑链。\n"
        "exam 的目标：生成能检验理解的自测题，包含选择题、判断题、简答题和必要的综合题；题目要有答案、解析和易错提醒。\n"
        "重要规则：\n"
        "1. 输出严格 JSON，不要 Markdown 代码围栏。\n"
        "2. importance 只能是 must/key/frequent/background。\n"
        "3. question.type 只能是 choice/true_false/short/essay/comprehensive。\n"
        "4. choice.answer 使用 0 起始选项索引；true_false.answer 使用 true/false；主观题 answer 使用字符串。\n"
        "5. 选择题干扰项必须来自同一概念簇、常见错因、相邻概念或错误推理链；不要使用一眼排除的选项，例如“页面装饰”“完全无关”“只需背名字”。\n"
        "6. 涉及图、表、公式的题目要把 image_refs 指向输入 figure_table_notes 中的 id/path，让图文就地出现在题目旁边，而不是集中放在附录。\n"
        "7. source_refs 使用简洁页码，如 P1、P3-P5；不要输出内部 element id，除非原文必须调试。\n"
        "8. 如果 requested_outputs.review=false，review 可为 null；如果 requested_outputs.exam=false，exam 可为 null。\n"
        "JSON schema:\n"
        "{\n"
        '  "review": {"title": "...", "summary": "...", "logic_chains": [{"title": "...", "steps": ["..."]}], "checklist": [{"section": "...", "importance": "must", "point": "...", "explanation": "...", "why": "...", "pitfall": "...", "source_refs": ["P1"]}], "methods": [{"name": "...", "detail": "...", "example": "..."}]},\n'
        '  "exam": {"title": "...", "subtitle": "...", "questions": [{"id": "q1", "type": "choice", "points": 2, "question": "...", "options": ["..."], "answer": 0, "explanation": "...", "pitfall": "...", "source_refs": ["P1"], "image_refs": [{"id": "s1_img1", "title": "...", "path": "images/fig.png", "source_ref": "P1"}]}]}\n'
        "}\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )

def _table_text(table: TableBlock) -> str:
    rows = []
    for row in table.rows[:6]:
        rows.append(" / ".join(cell.strip() for cell in row[:6] if cell.strip()))
    return "；".join(row for row in rows if row)

def _text_block_text(block: TextBlock) -> str:
    return " ".join((block.content or "").split())

def build_study_pack(
    deck: Deck,
    notes_markdown: str,
    output_root: Path,
    review_mode: str = "off",
    exam_mode: str = "off",
    question_count: int = 12,
    use_llm: bool = False,
    provider: str = "openai",
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    cache_mode: str = "on",
    cache_dir: Path | None = None,
    max_output_tokens: int = 4096,
    temperature: float | None = 0.0,
    note_language: str = "zh",
    section_plan: dict[str, Any] | None = None,
    deck_brief: dict[str, Any] | None = None,
    content_guard: dict[str, Any] | None = None,
    coverage_report: dict[str, Any] | None = None,
    source_map: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if review_mode not in STUDY_PACK_MODES:
        raise ValueError(f"review_mode must be one of: {', '.join(sorted(STUDY_PACK_MODES))}")
    if exam_mode not in STUDY_PACK_MODES:
        raise ValueError(f"exam_mode must be one of: {', '.join(sorted(STUDY_PACK_MODES))}")

    review_requested = review_mode != "off"
    exam_requested = exam_mode != "off"
    if not review_requested and not exam_requested:
        return None

    effective_review_mode = _effective_mode(review_mode, use_llm) if review_requested else "off"
    effective_exam_mode = _effective_mode(exam_mode, use_llm) if exam_requested else "off"
    requested_question_count = max(1, min(int(question_count or 12), 60))

    local_data = _build_local_data(
        deck=deck,
        notes_markdown=notes_markdown,
        section_plan=section_plan,
        deck_brief=deck_brief,
        content_guard=content_guard,
        question_count=requested_question_count,
    )
    final_data = {
        "review": local_data.get("review") if review_requested else None,
        "exam": local_data.get("exam") if exam_requested else None,
    }

    warnings: list[str] = []
    llm_record: dict[str, Any] | None = None
    generator = "local"
    if effective_review_mode == "llm" or effective_exam_mode == "llm":
        llm_data, llm_record, llm_warnings = _generate_llm_data(
            deck=deck,
            notes_markdown=notes_markdown,
            output_root=output_root,
            review_requested=review_requested,
            exam_requested=exam_requested,
            question_count=requested_question_count,
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            cache_mode=cache_mode,
            cache_dir=cache_dir,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            note_language=note_language,
            section_plan=section_plan,
            deck_brief=deck_brief,
            content_guard=content_guard,
            coverage_report=coverage_report,
            source_map=source_map,
            fallback=local_data,
        )
        warnings.extend(llm_warnings)
        if llm_data is not None:
            if effective_review_mode == "llm" and review_requested:
                final_data["review"] = llm_data.get("review") or final_data["review"]
            if effective_exam_mode == "llm" and exam_requested:
                final_data["exam"] = llm_data.get("exam") or final_data["exam"]
            generator = "llm" if effective_review_mode == effective_exam_mode else "mixed"
        else:
            generator = "local_fallback"

    review = final_data.get("review") if isinstance(final_data.get("review"), dict) else None
    exam = final_data.get("exam") if isinstance(final_data.get("exam"), dict) else None
    figure_table_notes = _figure_table_notes(deck)
    if review is not None:
        review["figure_table_notes"] = figure_table_notes
    if exam is not None:
        _attach_inline_figure_refs(exam, figure_table_notes)
    question_quality = build_question_quality_report({"exam": exam, "figure_table_notes": figure_table_notes})
    section_study_pack = _build_section_study_pack(review, exam, figure_table_notes) if review or exam else None
    final_exam = _build_final_exam(exam) if exam else None
    wrong_answer_review = _build_wrong_answer_review(exam, question_quality) if exam else None
    exam_review_pack = (
        {
            "schema_version": 1,
            "review": review,
            "exam": exam,
            "final_exam": final_exam,
            "question_quality": question_quality,
            "wrong_answer_review": wrong_answer_review,
        }
        if review or exam
        else None
    )
    return {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "prompt_version": STUDY_PACK_PROMPT_VERSION,
        "generator": generator,
        "modes": {
            "review": review_mode,
            "exam": exam_mode,
            "effective_review": effective_review_mode,
            "effective_exam": effective_exam_mode,
        },
        "request": {
            "question_count": requested_question_count,
            "note_language": note_language,
        },
        "review": review,
        "exam": exam,
        "figure_table_notes": figure_table_notes,
        "section_study_pack": section_study_pack,
        "exam_review_pack": exam_review_pack,
        "final_exam": final_exam,
        "wrong_answer_review": wrong_answer_review,
        "question_quality": question_quality,
        "llm": llm_record,
        "summary": {
            "review_enabled": review is not None,
            "exam_enabled": exam is not None,
            "review_items_total": len(review.get("checklist") or []) if review else 0,
            "logic_chains_total": len(review.get("logic_chains") or []) if review else 0,
            "questions_total": len(exam.get("questions") or []) if exam else 0,
            "question_quality_score": question_quality.get("overall_score"),
            "choice_distractor_score": question_quality.get("choice_distractor_score"),
            "figure_question_score": question_quality.get("figure_question_score"),
            "section_study_pack_sections": len(section_study_pack.get("sections") or []) if section_study_pack else 0,
            "final_exam_questions_total": len(final_exam.get("questions") or []) if final_exam else 0,
            "llm_call": bool(llm_record and llm_record.get("llm_call")),
            "local_cache_hits": 1 if llm_record and llm_record.get("cache_status") == "local_hit" else 0,
            "input_tokens": (llm_record or {}).get("input_tokens") or 0,
            "output_tokens": (llm_record or {}).get("output_tokens") or 0,
            "total_tokens": (llm_record or {}).get("total_tokens") or 0,
        },
        "warnings": warnings,
        "artifacts": {
            "study_pack": "study_pack.json",
            "review_markdown": "review.md" if review else None,
            "exam_markdown": "exam.md" if exam else None,
            "exam_json": "exam.json" if exam else None,
            "exam_html": "exam.html" if exam else None,
            "section_study_pack": "section_study_pack.json" if section_study_pack else None,
            "exam_review_pack": "exam_review_pack.json" if exam_review_pack else None,
            "final_exam_markdown": "final_exam.md" if final_exam else None,
            "final_exam_answers": "final_exam.answers.md" if final_exam else None,
            "wrong_answer_review_prompt": "wrong_answer_review_prompt.md" if wrong_answer_review else None,
        },
    }

__all__ = [
    "STUDY_PACK_MODES",
    "STUDY_PACK_PROMPT_VERSION",
    "STUDY_PACK_SYSTEM_PROMPT",
    "IMPORTANCE_LABELS",
    "QUESTION_TYPE_LABELS",
    "build_study_pack",
    "render_review_markdown",
    "render_exam_markdown",
    "render_final_exam_markdown",
    "render_final_exam_answers_markdown",
    "render_wrong_answer_review_prompt",
    "render_exam_html",
    "build_question_quality_report",
]
