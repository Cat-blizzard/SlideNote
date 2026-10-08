"""Regressions for document-frame and structure checks found in PR review."""

import pytest

from slidenote.notes.document_frame import ensure_lecture_document_frame
from slidenote.notes.structure import (
    _heading_sections,
    assess_lecture_note_structure,
    structure_missing_labels,
)


BODY = "A source-backed explanation of the algorithm, its conditions, and its conclusion."


def _complete_note(objectives="本讲目标", summary="本讲总结", review="章节自测"):
    return (
        f"# Search Algorithms\n\n## {objectives}\n\n{BODY}\n\n"
        f"## Binary Search\n\n{BODY}\n\n"
        f"## {summary}\n\n{BODY}\n\n## {review}\n\n{BODY}\n"
    )


@pytest.mark.parametrize(
    ("note_language", "objectives", "summary", "review"),
    [
        ("zh", "一、本讲目标：二分搜索", "本讲总结（搜索算法）", "章节自测 - 搜索算法"),
        ("en", "1. Learning Objectives: Binary Search", "Lecture Summary / Search", "Review Questions (Search)"),
    ],
)
def test_frame_keeps_valid_suffixed_global_sections(note_language, objectives, summary, review):
    original = _complete_note(objectives, summary, review)
    assert assess_lecture_note_structure(original)["passed"] is True

    framed = ensure_lecture_document_frame(original, None, note_language)

    assert framed == original
    assert ensure_lecture_document_frame(framed, None, note_language) == framed
    assessment = assess_lecture_note_structure(framed)
    assert assessment["passed"] is True
    assert assessment["repeated_global_sections"] == {"orientation": 1, "summary": 1, "self_test": 1}


@pytest.mark.parametrize("fence", ["```", "~~~"])
def test_code_headings_do_not_fill_missing_document_sections(fence):
    code = (
        f"{fence}markdown\n# Example document\n\n"
        f"## 本讲目标\n\n{BODY}\n\n## 本讲总结\n\n{BODY}\n\n"
        f"## 章节自测\n\n{BODY}\n{fence}"
    )
    original = f"# Real Course\n\n## Binary Search\n\n{code}\n\n{BODY}\n"
    before = assess_lecture_note_structure(original)
    assert before["h1_count"] == 1
    assert before["missing_slots"] == ["orientation", "summary", "self_test"]

    framed = ensure_lecture_document_frame(original, None, "zh")

    assert code in framed
    assessment = assess_lecture_note_structure(framed)
    assert assessment["passed"] is True
    assert assessment["h1_count"] == 1
    assert assessment["repeated_global_sections"] == {"orientation": 1, "summary": 1, "self_test": 1}


@pytest.mark.parametrize("fence", ["```", "~~~"])
def test_code_page_and_template_headings_do_not_fail_valid_structure(fence):
    code = (
        f"{fence}markdown\n## 第 1 页：示例\n### 核心概念\n{BODY}\n"
        f"### 易错点\n{BODY}\n### 核心概念\n{BODY}\n### 易错点\n{BODY}\n{fence}"
    )
    original = _complete_note().replace(f"## Binary Search\n\n{BODY}", f"## Binary Search\n\n{code}\n\n{BODY}")

    assessment = assess_lecture_note_structure(original)

    assert assessment["passed"] is True
    assert assessment["page_headings"] == []
    assert assessment["repetitive_subheading_counts"] == {}
    assert ensure_lecture_document_frame(original, None, "zh") == original


@pytest.mark.parametrize("fence", ["```", "~~~"])
def test_frame_places_objectives_after_real_h1_and_preserves_code(fence):
    code = f"{fence}markdown\n# Example H1\n## Example H2\n{fence}"
    original = f"{code}\n\n# Real Course\n\n{BODY}\n"

    framed = ensure_lecture_document_frame(original, None, "en")

    assert framed.startswith(code + "\n\n# Real Course\n\n## Learning Objectives")
    assert f"## Core Content\n\n{BODY}" in framed
    assert assess_lecture_note_structure(framed)["passed"] is True


@pytest.mark.parametrize(
    "prefix",
    [
        "",
        "```markdown\n# Code-only title\n```\n\n",
        "~~~markdown\n# Code-only title\n~~~\n\n",
        "# First title\n\n# Second title\n\n",
    ],
)
def test_contract_requires_exactly_one_real_h1(prefix):
    original = prefix + _complete_note().split("\n\n", 1)[1]

    assessment = assess_lecture_note_structure(original)

    assert assessment["missing_slots"] == []
    assert assessment["h1_count"] == (2 if prefix.startswith("# First") else 0)
    assert assessment["title_pass"] is False
    assert assessment["passed"] is False
    assert "唯一的 H1 课程标题" in structure_missing_labels(assessment)


@pytest.mark.parametrize("fence", ["```", "~~~"])
def test_fence_closer_must_match_character_and_opening_length(fence):
    other_fence = "~~~" if fence[0] == "`" else "```"
    original = (
        f"# Real Course\r\n\r\n{fence}{fence[0]}markdown\r\n"
        f"# Hidden one\r\n{other_fence}\r\n# Hidden two\r\n"
        f"{fence}\r\n# Hidden three\r\n{fence}{fence[0]}\r\n"
        f"## Visible Topic\r\n\r\n{BODY}\r\n"
    )

    headings = _heading_sections(original)

    assert [(heading["level"], heading["title"]) for heading in headings] == [
        (1, "Real Course"), (2, "Visible Topic")
    ]
    assert original[headings[1]["start"] : headings[1]["end"]] == "## Visible Topic\r"
    assert "# Hidden three" in headings[0]["body"]


def test_commonmark_heading_spacing_and_closing_hashes_share_frame_matching():
    original = _complete_note("本讲目标：搜索 ###", "本讲总结 ##", "章节自测 #")
    original = original.replace("# Search Algorithms", "  # Search Algorithms ###").replace("## Binary Search", "   ## Binary Search")

    framed = ensure_lecture_document_frame(original, None, "zh")

    assert assess_lecture_note_structure(framed)["passed"] is True
    assert len(_heading_sections(framed)) == 5
    assert [heading["title"] for heading in _heading_sections(framed)][:2] == ["Search Algorithms", "本讲目标：搜索"]


def test_empty_atx_title_does_not_satisfy_document_title_contract():
    original = _complete_note().replace("# Search Algorithms", "# ###")

    assessment = assess_lecture_note_structure(original)

    assert assessment["h1_count"] == 1
    assert assessment["title_pass"] is False
    assert assessment["passed"] is False
