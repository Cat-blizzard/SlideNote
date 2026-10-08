"""Regression tests for complete-document structure repairs."""

from unittest.mock import Mock

import pytest

from slidenote.models import Deck, SlidePage, TextBlock
from slidenote.notes import NoteOptions
from slidenote.notes.contexts import NoteContext
from slidenote.notes.repair import _repair_note_structure_once
from slidenote.notes.structure import assess_lecture_note_structure


BODY = "Binary search requires sorted input and narrows the candidate range after each comparison."
SOURCE = "<!-- slidenote-source: p1:s1_t1 -->"
IMAGE = "![Search](assets/search.png)"
ORIGINAL = (
    f"# 课程笔记：Search\n\n## 本讲目标\n\n{BODY}\n\n"
    f"## 第 1 页：搜索\n\n{BODY} {SOURCE}\n\n{IMAGE}\n\n"
    f"### 运行示例\n\n{BODY}\n\n### 易错点\n\n{BODY}\n\n"
    f"## 本讲总结\n\n{BODY}\n\n## 章节自测\n\n{BODY}"
)
CANDIDATE = ORIGINAL.replace("## 第 1 页：搜索", "## Binary Search")


@pytest.fixture
def repair_case(tmp_path):
    deck = Deck(
        source_path="lecture.pdf",
        source_type="pdf",
        pages=[SlidePage(
            slide_id=1,
            title="Search",
            text_blocks=[TextBlock(id="s1_t1", type="paragraph", content=BODY)],
        )],
    )
    return {
        "deck": deck,
        "context": NoteContext(id="final", kind="final", title="Search", pages=deck.pages),
        "output_root": tmp_path,
        "cache": None,
        "options": NoteOptions(note_profile="lecture-notes", cache_mode="off"),
    }


def _run_repair(monkeypatch, repair_case, candidate, original=ORIGINAL):
    generate = Mock(return_value=(candidate, {"provider_usage": {"finish_reason": "stop"}}))
    monkeypatch.setattr("slidenote.notes.repair._generate_cached_llm_text", generate)
    markdown, record = _repair_note_structure_once(markdown=original, **repair_case)
    generate.assert_called_once()
    assert record is not None
    return markdown, record


def test_successful_structure_repair_preserves_original_document_title(monkeypatch, repair_case):
    assert assess_lecture_note_structure(ORIGINAL)["passed"] is False
    assert assess_lecture_note_structure(CANDIDATE)["passed"] is True

    markdown, record = _run_repair(monkeypatch, repair_case, CANDIDATE)

    assert record["accepted"] is True
    assert record["rejection_reasons"] == []
    assert markdown == CANDIDATE
    assert markdown.startswith("# 课程笔记：Search\n")
    assert record["validation"]["document_title_preserved"] is True
    assert record["validation"]["candidate_document_titles"] == ["课程笔记：Search"]
    assert record["validation"]["lost_trace_items"] == []
    assert record["validation"]["lost_visible_items"] == []
    assert record["validation"]["lost_source_markers"] == []
    assert record["validation"]["lost_image_targets"] == []


@pytest.mark.parametrize(
    "candidate",
    [
        CANDIDATE.replace("# 课程笔记：Search\n\n", ""),
        CANDIDATE + "\n\n# Another title",
        CANDIDATE.replace("# 课程笔记：Search", "# Renamed course"),
        CANDIDATE.replace("# 课程笔记：Search", "```markdown\n# 课程笔记：Search\n```"),
    ],
    ids=["missing", "duplicate", "changed", "code-only"],
)
def test_structure_repair_rejects_candidate_that_does_not_preserve_single_title(
    monkeypatch, repair_case, candidate,
):
    markdown, record = _run_repair(monkeypatch, repair_case, candidate)

    assert record["accepted"] is False
    assert "document_title_not_preserved" in record["rejection_reasons"]
    assert record["validation"]["document_title_preserved"] is False
    assert markdown == ORIGINAL


def test_structure_repair_preserves_code_heading_without_counting_it_as_document_title(
    monkeypatch, repair_case,
):
    code = "```markdown\n# Example heading\n```"
    original = ORIGINAL.replace(IMAGE, IMAGE + "\n\n" + code)
    candidate = CANDIDATE.replace(IMAGE, IMAGE + "\n\n" + code)

    markdown, record = _run_repair(monkeypatch, repair_case, candidate, original=original)

    assert record["accepted"] is True
    assert code in markdown
    assert record["validation"]["original_document_titles"] == ["课程笔记：Search"]
    assert record["validation"]["candidate_document_titles"] == ["课程笔记：Search"]


@pytest.mark.parametrize(
    "original",
    [
        ORIGINAL.replace("# 课程笔记：Search\n\n", ""),
        ORIGINAL + "\n\n# Another title",
    ],
    ids=["missing-original-title", "multiple-original-titles"],
)
def test_structure_repair_requires_one_original_title(monkeypatch, repair_case, original):
    markdown, record = _run_repair(monkeypatch, repair_case, CANDIDATE, original=original)

    assert record["accepted"] is False
    assert "document_title_not_preserved" in record["rejection_reasons"]
    assert markdown == original
