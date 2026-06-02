from __future__ import annotations

import re
from typing import Any

from slidenote.llm_cache import utc_now_iso
from slidenote.models import Deck
from slidenote.study_pack import build_question_quality_report


def build_note_quality_report(
    deck: Deck,
    notes_markdown: str,
    coverage_report: dict[str, Any] | None,
    note_profile: str,
    note_context: str,
    note_strategy: str,
    note_depth: str,
    study_pack_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    paragraphs = _paragraphs(notes_markdown)
    heading_count = len(re.findall(r"(?m)^#{2,4}\s+", notes_markdown))
    source_markers = len(re.findall(r"<!--\s*slidenote-source:", notes_markdown))
    image_refs = len(re.findall(r"!\[[^\]]*\]\([^)]+\)", notes_markdown))
    image_total = sum(1 for page in deck.pages for image in page.images if not image.ignored)

    coherence_score = _coherence_score(paragraphs, heading_count)
    explanation_depth_score = _keyword_score(
        notes_markdown,
        ["为什么", "原因", "重要", "如何", "机制", "关系", "影响", "直觉", "步骤", "because", "why", "how"],
        base=_avg_paragraph_len(paragraphs) / 220,
    )
    example_score = _keyword_score(notes_markdown, ["例如", "例子", "比如", "类比", "case", "example"], base=0.0)
    figure_integration_score = _figure_integration_score(notes_markdown, image_total, image_refs)
    mechanical_page_listing_score = _mechanical_page_listing_score(notes_markdown)
    self_test_score = _keyword_score(notes_markdown, ["自测", "检查自己", "思考题", "quiz", "self-test"], base=0.0)
    pitfall_score = _keyword_score(notes_markdown, ["易错", "误解", "注意", "陷阱", "常见错误", "pitfall", "misconception"], base=0.0)
    hallucination_risk = _hallucination_risk(
        paragraphs=paragraphs,
        source_markers=source_markers,
        coverage_report=coverage_report,
        note_profile=note_profile,
        note_depth=note_depth,
    )
    suggested_repairs = _suggest_repairs(
        explanation_depth_score=explanation_depth_score,
        example_score=example_score,
        figure_integration_score=figure_integration_score,
        mechanical_page_listing_score=mechanical_page_listing_score,
        self_test_score=self_test_score,
        pitfall_score=pitfall_score,
        image_total=image_total,
    )
    question_quality = build_question_quality_report(study_pack_report) if study_pack_report else None
    return {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "request": {
            "note_profile": note_profile,
            "note_context": note_context,
            "note_strategy": note_strategy,
            "note_depth": note_depth,
        },
        "coherence_score": coherence_score,
        "explanation_depth_score": explanation_depth_score,
        "example_score": example_score,
        "figure_integration_score": figure_integration_score,
        "mechanical_page_listing_score": mechanical_page_listing_score,
        "self_test_score": self_test_score,
        "pitfall_score": pitfall_score,
        "question_quality": question_quality,
        "question_quality_score": question_quality.get("overall_score") if question_quality else None,
        "hallucination_risk": hallucination_risk,
        "suggested_repairs": suggested_repairs,
        "summary": {
            "paragraphs": len(paragraphs),
            "headings": heading_count,
            "source_markers": source_markers,
            "image_refs": image_refs,
            "source_images": image_total,
            "coverage_missing": coverage_report.get("missing") if coverage_report else None,
            "required_visible_missing": (coverage_report.get("required_visible_coverage") or {}).get("missing") if coverage_report else None,
            "question_quality_score": question_quality.get("overall_score") if question_quality else None,
            "suggested_repairs": len(suggested_repairs),
        },
    }


def _paragraphs(markdown: str) -> list[str]:
    chunks = [chunk.strip() for chunk in re.split(r"\n\s*\n", markdown) if chunk.strip()]
    return [
        chunk
        for chunk in chunks
        if not chunk.startswith("#")
        and not chunk.startswith("<!--")
        and not chunk.startswith("![")
        and not chunk.startswith("|")
    ]


def _avg_paragraph_len(paragraphs: list[str]) -> float:
    if not paragraphs:
        return 0.0
    return sum(len(paragraph) for paragraph in paragraphs) / len(paragraphs)


def _coherence_score(paragraphs: list[str], heading_count: int) -> float:
    if not paragraphs:
        return 0.0
    avg_len = _avg_paragraph_len(paragraphs)
    paragraph_factor = min(1.0, len(paragraphs) / 8)
    heading_factor = min(1.0, heading_count / 5) if heading_count else 0.25
    length_factor = min(1.0, avg_len / 180)
    return _round_score(0.42 * paragraph_factor + 0.28 * heading_factor + 0.30 * length_factor)


def _keyword_score(markdown: str, keywords: list[str], base: float) -> float:
    lowered = markdown.lower()
    hits = sum(lowered.count(keyword.lower()) for keyword in keywords)
    return _round_score(min(1.0, base + hits / 8))


def _figure_integration_score(markdown: str, image_total: int, image_refs: int) -> float:
    if image_total == 0:
        return 1.0
    figure_terms = sum(markdown.count(term) for term in ["图", "表", "公式", "流程", "截图", "figure", "formula"])
    reference_factor = min(1.0, image_refs / max(1, image_total))
    explanation_factor = min(1.0, figure_terms / max(2, image_refs * 2))
    return _round_score(0.55 * reference_factor + 0.45 * explanation_factor)


def _mechanical_page_listing_score(markdown: str) -> float:
    patterns = [
        r"第\s*\d+\s*页",
        r"第\s*\d+\s*张",
        r"幻灯片\s*\d+",
        r"slide\s*\d+",
        r"this page",
        r"on this slide",
    ]
    hits = sum(len(re.findall(pattern, markdown, flags=re.IGNORECASE)) for pattern in patterns)
    page_heading_hits = len(re.findall(r"(?m)^#{2,4}\s*(第\s*\d+\s*页|Slide\s*\d+)", markdown, flags=re.IGNORECASE))
    density = (hits + page_heading_hits * 2) / max(1, len(markdown) / 1000)
    return _round_score(min(1.0, density / 3))


def _hallucination_risk(
    paragraphs: list[str],
    source_markers: int,
    coverage_report: dict[str, Any] | None,
    note_profile: str,
    note_depth: str,
) -> str:
    missing_required = 0
    missing_total = 0
    if coverage_report:
        missing_total = int(coverage_report.get("missing") or 0)
        missing_required = int((coverage_report.get("required_visible_coverage") or {}).get("missing") or 0)
    marker_density = source_markers / max(1, len(paragraphs))
    expanded = note_profile in {"lecture-notes", "study-guide"} or note_depth == "very-detailed"
    if missing_required or (expanded and marker_density < 0.25):
        return "high"
    if missing_total or marker_density < 0.45:
        return "medium"
    return "low"


def _suggest_repairs(
    explanation_depth_score: float,
    example_score: float,
    figure_integration_score: float,
    mechanical_page_listing_score: float,
    self_test_score: float,
    pitfall_score: float,
    image_total: int,
) -> list[str]:
    suggestions: list[str] = []
    if explanation_depth_score < 0.55:
        suggestions.append("补充核心概念的为什么、如何运作、前后关系解释。")
    if example_score < 0.35:
        suggestions.append("增加帮助理解的例子或类比，但避免新增课件没有依据的具体事实。")
    if image_total and figure_integration_score < 0.55:
        suggestions.append("加强图、表、公式与正文概念之间的解释关系。")
    if mechanical_page_listing_score > 0.35:
        suggestions.append("减少按页复述，把内容按章节问题和概念链重新组织。")
    if self_test_score < 0.25:
        suggestions.append("增加本节自测问题，帮助学生检查是否真正理解。")
    if pitfall_score < 0.25:
        suggestions.append("补充易错点或常见误解。")
    return suggestions


def _round_score(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 3)
