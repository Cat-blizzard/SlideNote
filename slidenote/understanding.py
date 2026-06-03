from __future__ import annotations

import re
from collections import Counter
from typing import Any

from slidenote.content_guard import learning_items_for_page
from slidenote.llm_cache import utc_now_iso
from slidenote.models import Deck, ImageAsset, SlidePage, TableBlock, TextBlock
from slidenote.table_understanding import table_preview


UNDERSTANDING_SCHEMA_VERSION = 1


def build_understanding_reports(
    deck: Deck,
    *,
    section_plan: dict[str, Any] | None = None,
    deck_brief_report: dict[str, Any] | None = None,
    modality_report: dict[str, Any] | None = None,
    table_understanding_report: dict[str, Any] | None = None,
    semantic_layout_report: dict[str, Any] | None = None,
    image_importance_report: dict[str, Any] | None = None,
    figure_grounding_report: dict[str, Any] | None = None,
    content_guard_report: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    del modality_report, table_understanding_report, semantic_layout_report, image_importance_report, figure_grounding_report

    brief = _brief(deck_brief_report)
    role_by_slide = _page_roles_from_brief(brief)
    section_by_slide = _section_lookup(section_plan, deck)
    pages = [
        _page_understanding(
            deck=deck,
            page=page,
            section=section_by_slide.get(page.slide_id),
            role_record=role_by_slide.get(page.slide_id),
            content_guard_report=content_guard_report,
        )
        for page in deck.pages
    ]
    deck_understanding = _deck_understanding(deck, pages, section_plan, deck_brief_report, brief)
    page_understanding = {
        "schema_version": UNDERSTANDING_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "summary": {
            "pages_total": len(pages),
            "pages_with_tables": sum(1 for page in pages if page["tables"]),
            "pages_with_figures": sum(1 for page in pages if page["figures"]),
            "pages_with_semantic_groups": sum(1 for page in pages if page["semantic"]["groups"]),
            "high_value_figures": sum(1 for page in pages for figure in page["figures"] if _as_float(figure.get("importance_score"), 0.0) >= 0.65),
            "required_items": sum(len(page["required_items"]) for page in pages),
            "role_sources": dict(Counter(page["role"]["source"] for page in pages)),
        },
        "pages": pages,
    }
    return deck_understanding, page_understanding


def _deck_understanding(
    deck: Deck,
    pages: list[dict[str, Any]],
    section_plan: dict[str, Any] | None,
    deck_brief_report: dict[str, Any] | None,
    brief: dict[str, Any],
) -> dict[str, Any]:
    figures = sorted(
        [figure for page in pages for figure in page["figures"]],
        key=lambda figure: (
            int(figure.get("importance_rank") or 9999),
            -_as_float(figure.get("importance_score"), 0.0),
            str(figure.get("id") or ""),
        ),
    )
    tables = [table for page in pages for table in page["tables"]]
    sections = _sections(section_plan, deck)
    role_counts = Counter(page["role"]["role"] for page in pages)
    warnings = list(deck.warnings)
    if deck_brief_report:
        warnings.extend(str(warning) for warning in deck_brief_report.get("warnings", []) if warning)
    return {
        "schema_version": UNDERSTANDING_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "summary": {
            "pages_total": len(deck.pages),
            "sections_total": len(sections),
            "page_roles": dict(sorted(role_counts.items())),
            "key_concepts_total": len(brief.get("key_concepts") or []),
            "cross_page_links_total": len(brief.get("cross_page_links") or []),
            "tables_total": len(tables),
            "figures_total": len(figures),
            "high_value_figures": sum(1 for figure in figures if _as_float(figure.get("importance_score"), 0.0) >= 0.65),
            "warnings_total": len(warnings),
        },
        "deck": {
            "course_title": brief.get("course_title") or _fallback_deck_title(deck),
            "one_sentence_summary": brief.get("one_sentence_summary"),
            "core_questions": _string_list(brief.get("core_questions"), limit=20),
            "writing_guidance": _string_list(brief.get("writing_guidance"), limit=20),
        },
        "sections": sections,
        "key_concepts": _dict_list(brief.get("key_concepts"), limit=160),
        "concept_dependencies": _dict_list(brief.get("concept_dependencies"), limit=160),
        "cross_page_links": _dict_list(brief.get("cross_page_links"), limit=200),
        "page_roles": [
            {
                "slide_id": page["slide_id"],
                "title": page["title"],
                "section_id": page["section"]["section_id"] if page.get("section") else None,
                **page["role"],
            }
            for page in pages
        ],
        "important_tables": tables[:80],
        "important_figures": figures[:80],
        "sources": {
            "deck_brief": bool(deck_brief_report),
            "sections": bool(section_plan),
            "page_modalities": any(page.get("modality", {}).get("type") for page in pages),
            "semantic_layout": any(page["semantic"]["blocks"] or page["semantic"]["groups"] for page in pages),
            "table_understanding": any(table.get("table_summary") or table.get("table_conclusion") for table in tables),
            "image_ranking": any(figure.get("importance_rank") is not None for figure in figures),
            "figure_grounding": any(figure.get("anchor_element_ids") or figure.get("figure_explanation") for figure in figures),
        },
        "warnings": warnings,
    }


def _page_understanding(
    deck: Deck,
    page: SlidePage,
    section: dict[str, Any] | None,
    role_record: dict[str, Any] | None,
    content_guard_report: dict[str, Any] | None,
) -> dict[str, Any]:
    role = _page_role(page, role_record)
    tables = [_table_record(table) for table in page.tables]
    figures = [_figure_record(page, image) for image in _page_figures(page)]
    semantic = {
        "blocks": _semantic_blocks(page.semantic_blocks),
        "groups": page.semantic_groups[:12],
        "relations": page.semantic_relations[:20],
    }
    required_items = learning_items_for_page(content_guard_report, page.slide_id)
    key_points = _key_points(page, tables, figures, semantic)
    return {
        "slide_id": page.slide_id,
        "title": page.title,
        "section": section,
        "role": role,
        "modality": {
            "type": page.page_modality,
            "confidence": page.modality_confidence,
            "reasons": list(page.modality_reasons),
            "processing_hints": list(page.processing_hints),
        },
        "key_points": key_points,
        "text": {
            "blocks_total": len(page.text_blocks),
            "chars_total": sum(len(block.content.strip()) for block in page.text_blocks),
            "blocks": [_text_block_record(block) for block in page.text_blocks[:12]],
            "page_ocr_text": _preview(page.page_ocr_text, 500) if page.page_ocr_text else None,
            "page_visual_summary": _preview(page.page_visual_summary, 500) if page.page_visual_summary else None,
        },
        "tables": tables,
        "figures": figures,
        "semantic": semantic,
        "required_items": required_items,
        "assets": {
            "page_screenshot": page.page_screenshot,
            "image_count": len(page.images),
            "content_image_count": sum(1 for image in page.images if not image.ignored and image.role != "page_image"),
        },
        "warnings": list(page.warnings),
    }


def _page_role(page: SlidePage, role_record: dict[str, Any] | None) -> dict[str, Any]:
    if role_record:
        return {
            "role": str(role_record.get("role") or "other"),
            "reason": str(role_record.get("reason") or role_record.get("text") or "deck_brief_page_role"),
            "source": "deck_brief",
            "confidence": 0.82,
        }
    title = (page.title or "").strip()
    normalized_title = re.sub(r"\s+", "", title).lower()
    if normalized_title in {"目录", "课程目录", "contents", "outline", "agenda"}:
        return {"role": "agenda", "reason": "title_matches_agenda", "source": "local_rules", "confidence": 0.72}
    if page.tables and not any(not image.ignored for image in page.images):
        return {"role": "table", "reason": "table_dominant_page", "source": "local_rules", "confidence": 0.7}
    if any(image.role in {"figure_crop", "composite_figure"} for image in page.images):
        return {"role": "diagram", "reason": "has_cropped_or_composite_figure", "source": "local_rules", "confidence": 0.7}
    if page.page_modality in {"image_only", "shape_diagram"}:
        return {"role": "diagram", "reason": f"page_modality:{page.page_modality}", "source": "local_rules", "confidence": 0.62}
    if page.page_modality == "decorative":
        return {"role": "transition", "reason": "decorative_or_low_content_page", "source": "local_rules", "confidence": 0.56}
    if _looks_like_example_page(page):
        return {"role": "example", "reason": "example_signal", "source": "local_rules", "confidence": 0.64}
    return {"role": "concept", "reason": "text_or_mixed_content_page", "source": "local_rules", "confidence": 0.58}


def _sections(section_plan: dict[str, Any] | None, deck: Deck) -> list[dict[str, Any]]:
    if section_plan and isinstance(section_plan.get("sections"), list):
        sections = []
        for index, section in enumerate(section_plan.get("sections") or [], start=1):
            if not isinstance(section, dict):
                continue
            sections.append(
                {
                    "section_id": str(section.get("section_id") or f"sec{index}"),
                    "title": section.get("title"),
                    "start_slide_id": section.get("start_slide_id"),
                    "end_slide_id": section.get("end_slide_id"),
                    "slide_ids": [slide_id for slide_id in section.get("slide_ids", []) if isinstance(slide_id, int)],
                    "reason": section.get("reason"),
                    "source": section_plan.get("method") or "sections",
                }
            )
        if sections:
            return sections
    if not deck.pages:
        return []
    return [
        {
            "section_id": "sec1",
            "title": _fallback_deck_title(deck),
            "start_slide_id": deck.pages[0].slide_id,
            "end_slide_id": deck.pages[-1].slide_id,
            "slide_ids": [page.slide_id for page in deck.pages],
            "reason": "single_section_fallback",
            "source": "local_fallback",
        }
    ]


def _section_lookup(section_plan: dict[str, Any] | None, deck: Deck) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for section in _sections(section_plan, deck):
        for slide_id in section.get("slide_ids") or []:
            result[int(slide_id)] = {
                "section_id": section.get("section_id"),
                "title": section.get("title"),
                "start_slide_id": section.get("start_slide_id"),
                "end_slide_id": section.get("end_slide_id"),
            }
    return result


def _brief(deck_brief_report: dict[str, Any] | None) -> dict[str, Any]:
    if not deck_brief_report:
        return {}
    brief = deck_brief_report.get("brief")
    return brief if isinstance(brief, dict) else {}


def _page_roles_from_brief(brief: dict[str, Any]) -> dict[int, dict[str, Any]]:
    roles: dict[int, dict[str, Any]] = {}
    for item in _dict_list(brief.get("page_roles"), limit=1000):
        slide_id = _int_or_none(item.get("slide_id"))
        if slide_id is not None:
            roles[slide_id] = item
    return roles


def _text_block_record(block: TextBlock) -> dict[str, Any]:
    return {
        "id": block.id,
        "type": block.type,
        "preview": _preview(block.content, 260),
        "bbox": block.bbox,
    }


def _table_record(table: TableBlock) -> dict[str, Any]:
    row_count = len(table.rows)
    column_count = max((len(row) for row in table.rows), default=0)
    return {
        "id": table.id,
        "row_count": row_count,
        "column_count": column_count,
        "bbox": table.bbox,
        "table_summary": table.table_summary,
        "table_conclusion": table.table_conclusion,
        "key_rows": table.key_rows[:8],
        "preview": table_preview(table, limit=300, raw_rows=3),
    }


def _page_figures(page: SlidePage) -> list[ImageAsset]:
    candidates = [
        image
        for image in page.images
        if not image.ignored
        and (
            image.role in {"content", "figure_crop", "composite_figure", "page_image"}
            or image.importance_rank is not None
            or image.visual_summary
            or image.ocr_text
        )
    ]
    return sorted(
        candidates,
        key=lambda image: (
            image.importance_rank if image.importance_rank is not None else 9999,
            -(image.importance_score or 0.0),
            image.id,
        ),
    )


def _figure_record(page: SlidePage, image: ImageAsset) -> dict[str, Any]:
    return {
        "id": image.id,
        "slide_id": page.slide_id,
        "path": image.path,
        "caption": image.caption,
        "role": image.role,
        "importance_score": image.importance_score,
        "importance_rank": image.importance_rank,
        "importance_reason": image.importance_reason,
        "visual_summary": _preview(image.visual_summary, 420) if image.visual_summary else None,
        "ocr_text": _preview(image.ocr_text, 420) if image.ocr_text else None,
        "figure_explanation": _preview(image.figure_explanation, 420) if image.figure_explanation else None,
        "figure_explanation_status": image.figure_explanation_status,
        "anchor_element_ids": list(image.anchor_element_ids),
        "anchor_group_id": image.anchor_group_id,
        "anchor_reason": image.anchor_reason,
        "grounding_confidence": image.grounding_confidence,
        "bbox": image.bbox,
        "crop_source_path": image.crop_source_path,
        "crop_bbox": image.crop_bbox,
        "crop_quality": image.crop_quality,
        "source_element_ids": list(image.source_element_ids),
    }


def _semantic_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for block in blocks[:40]:
        if not isinstance(block, dict):
            continue
        result.append(
            {
                "id": block.get("id"),
                "kind": block.get("kind"),
                "block_type": block.get("block_type"),
                "learning_role": block.get("learning_role"),
                "must_explain": block.get("must_explain"),
                "importance_score": block.get("importance_score"),
                "group_id": block.get("group_id"),
                "preview": block.get("preview"),
            }
        )
    return result


def _key_points(
    page: SlidePage,
    tables: list[dict[str, Any]],
    figures: list[dict[str, Any]],
    semantic: dict[str, Any],
) -> list[str]:
    points: list[str] = []
    if page.title:
        points.append(page.title)
    for group in semantic.get("groups") or []:
        goal = _str_or_none(group.get("learning_goal"))
        if goal:
            points.append(goal)
    for table in tables:
        points.extend(_str_or_none(table.get(key)) for key in ("table_conclusion", "table_summary"))
    for figure in figures:
        points.extend(_str_or_none(figure.get(key)) for key in ("figure_explanation", "visual_summary", "ocr_text"))
    for block in page.text_blocks:
        if block.type in {"title", "heading"}:
            continue
        points.append(block.content)
    if page.page_visual_summary:
        points.append(page.page_visual_summary)
    if page.page_ocr_text:
        points.append(page.page_ocr_text)
    return [_preview(point, 220) for point in _dedupe([point for point in points if point])[:8]]


def _looks_like_example_page(page: SlidePage) -> bool:
    text = " ".join([page.title or "", *(block.content for block in page.text_blocks)]).lower()
    return bool(re.search(r"例如|例子|案例|example|case study|demo", text))


def _fallback_deck_title(deck: Deck) -> str:
    for page in deck.pages:
        if page.title:
            return page.title
    return "Untitled Deck"


def _dict_list(value: Any, limit: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            result.append(dict(item))
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
        if len(result) >= limit:
            break
    return result


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = re.sub(r"\s+", " ", value).strip()
        if normalized and normalized not in seen:
            result.append(normalized)
            seen.add(normalized)
    return result


def _preview(text: str | None, limit: int = 180) -> str:
    value = re.sub(r"\s+", " ", text or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "..."


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
