from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from slidenote.content_guard import content_guard_warnings, missing_required_items
from slidenote.coverage import analyze_coverage
from slidenote.llm_cache import LLMCache
from slidenote.models import Deck, SlidePage, TextBlock
from slidenote.notes import NoteOptions, generate_notes_result
from slidenote.notes.assembly import NoteContext
from slidenote.notes.repair import _repair_required_markdown_once


def _paragraph(element_id: str, text: str) -> str:
    return f"{text} <!-- slidenote-source: p1:{element_id} -->"


EXPLANATIONS = {
    "s1_t1": "Read and write quorums must intersect so that a read observes the relevant completed write.",
    "s1_t2": "The sum of read and write quorum sizes exceeds the replica count, guaranteeing an overlap.",
    "s1_t3": "A majority write quorum prevents two conflicting writes from being accepted by disjoint sets.",
    "s1_t4": "For five replicas, reading three replicas and writing three replicas provides an intersecting example.",
}
PARAGRAPHS = {key: _paragraph(key, value) for key, value in EXPLANATIONS.items()}
ORIGINAL = f"{PARAGRAPHS['s1_t1']}\n\n{PARAGRAPHS['s1_t4']}\n"
LLM_RECORD = {"llm_call": True, "input_tokens": 11, "output_tokens": 7, "total_tokens": 18}


@pytest.fixture
def repair_case(tmp_path):
    deck = Deck(
        source_path="lecture.pdf",
        source_type="pdf",
        pages=[SlidePage(
            slide_id=1,
            title="Quorum guarantees",
            text_blocks=[TextBlock(id=key, type="paragraph", content=value)
                         for key, value in EXPLANATIONS.items()],
        )],
    )
    guard = {
        "required_confidence_threshold": 0.7,
        "summary": {"repair_attempts": 0, "required_missing": 0, "residual_risks": 0},
        "pages": [{"slide_id": 1, "page_role": "content", "items": []}],
        "items": [
            {"element_id": key, "slide_id": 1, "learning_role": "definition",
             "must_explain": key != "s1_t4", "confidence": 0.95, "reason": "learning content"}
            for key in EXPLANATIONS
        ],
        "repairs": [],
    }
    return {
        "deck": deck,
        "context": NoteContext(id="final", kind="final", title="Quorums", pages=deck.pages),
        "output_root": tmp_path,
        "cache": LLMCache(tmp_path / "cache", mode="off"),
        "options": NoteOptions(content_guard=guard, cache_mode="off"),
        "stage": "final",
    }


def _run_repair(monkeypatch, repair_case, candidate, original=ORIGINAL):
    generate = Mock(return_value=(candidate, dict(LLM_RECORD)))
    monkeypatch.setattr("slidenote.notes.repair._generate_cached_llm_text", generate)
    markdown, record = _repair_required_markdown_once(markdown=original, **repair_case)
    generate.assert_called_once()
    assert record is not None
    coverage = analyze_coverage(
        repair_case["deck"], markdown, content_guard=repair_case["options"].content_guard,
    )
    # The public report must describe the saved draft, including on rejection.
    assert record["unresolved_items"] == missing_required_items(
        repair_case["options"].content_guard, coverage,
    )
    assert record["llm"] == LLM_RECORD
    return markdown, record


@pytest.mark.parametrize("repaired_ids", [("s1_t2",), ("s1_t2", "s1_t3")], ids=["partial", "complete"])
def test_repair_accepts_improvement_without_losing_existing_content(monkeypatch, repair_case, repaired_ids):
    candidate = ORIGINAL + "\n\n".join(PARAGRAPHS[key] for key in repaired_ids)
    markdown, record = _run_repair(monkeypatch, repair_case, candidate)

    assert markdown == candidate.strip()
    assert record["accepted"] is True
    assert record["rejection_reasons"] == []
    assert record["resolved_items"] == list(repaired_ids)
    assert record["candidate_unresolved_items"] == record["unresolved_items"]
    assert {item["element_id"] for item in record["unresolved_items"]} == {"s1_t2", "s1_t3"} - set(repaired_ids)


@pytest.mark.parametrize(
    "candidate,reason",
    [
        # Equal coverage counts still hide replacement of previously covered knowledge.
        (f"{PARAGRAPHS['s1_t2']}\n\n{PARAGRAPHS['s1_t4']}", "coverage_regression"),
        # Keep the old marker but move it into an otherwise empty block.
        ("<!-- slidenote-source: p1:s1_t1 -->\n\n"
         + f"{PARAGRAPHS['s1_t2']}\n\n{PARAGRAPHS['s1_t3']}\n\n{PARAGRAPHS['s1_t4']}", "coverage_regression"),
        # Non-required material is also part of the existing draft and must survive.
        (f"{PARAGRAPHS['s1_t1']}\n\n{PARAGRAPHS['s1_t2']}\n\n{PARAGRAPHS['s1_t3']}", "coverage_regression"),
        # Retaining every marker is not sufficient when most explanations disappear.
        ("Brief. <!-- slidenote-source: p1:s1_t1,s1_t2,s1_t3,s1_t4 -->", "body_truncated"),
        (ORIGINAL + "\nA further discussion without any missing source reference.", "no_coverage_improvement"),
        ("", "empty_repair"),
        (" \n\t ", "empty_repair"),
        ("<!-- slidenote-source: p1:s1_t1,s1_t2,s1_t3,s1_t4 -->", "empty_repair"),
    ],
    ids=["exchanged-coverage", "visible-to-marker-only", "optional-content-lost", "truncated",
         "no-progress", "empty", "whitespace", "markers-only"],
)
def test_repair_rejects_unsafe_candidate_and_returns_original(monkeypatch, repair_case, candidate, reason):
    markdown, record = _run_repair(monkeypatch, repair_case, candidate)

    assert markdown == ORIGINAL
    assert record["accepted"] is False
    assert reason in record["rejection_reasons"]
    assert record["resolved_items"] == []
    assert {item["element_id"] for item in record["unresolved_items"]} == {"s1_t2", "s1_t3"}
    assert "candidate_unresolved_items" in record


def test_repair_preserves_trace_only_optional_elements(monkeypatch, repair_case):
    original = PARAGRAPHS["s1_t1"] + "\n\n<!-- slidenote-source: p1:s1_t4 -->"
    candidate = f"{PARAGRAPHS['s1_t1']}\n\n{PARAGRAPHS['s1_t2']}"
    markdown, record = _run_repair(monkeypatch, repair_case, candidate, original=original)

    assert markdown == original
    assert record["accepted"] is False
    assert "coverage_regression" in record["rejection_reasons"]
    assert record["validation"]["lost_trace_items"] == ["s1_t4"]
    assert record["validation"]["lost_visible_items"] == []


@pytest.mark.parametrize("image", ["![Quorum](assets/quorum.png)", '![Quorum](<assets/quorum diagram.png> "Diagram")'])
def test_repair_rejects_removed_image_even_when_all_text_coverage_improves(monkeypatch, repair_case, image):
    original = ORIGINAL + "\n" + image
    candidate = ORIGINAL + "\n" + PARAGRAPHS["s1_t2"] + "\n\n" + PARAGRAPHS["s1_t3"]
    markdown, record = _run_repair(monkeypatch, repair_case, candidate, original=original)

    assert markdown == original
    assert record["accepted"] is False
    assert record["rejection_reasons"] == ["missing_images"]
    assert record["candidate_unresolved_items"] == []
    assert record["resolved_items"] == []


@pytest.mark.parametrize("retained_chars,accepted", [(79, False), (80, True), (81, True)])
def test_body_retention_boundary_ignores_headings_comments_and_images(monkeypatch, repair_case, retained_chars, accepted):
    image = "![Diagram](assets/quorum.png)"
    original = _paragraph("s1_t1", "理" * 100) + "\n\n" + image
    candidate = (
        "# " + "Heading " * 100 + "\n\n"
        + _paragraph("s1_t1,s1_t2", "理" * retained_chars)
        + "\n\n<!-- " + "Comment " * 100 + "-->\n\n" + image
    )
    markdown, record = _run_repair(monkeypatch, repair_case, candidate, original=original)

    assert record["accepted"] is accepted
    assert ("body_truncated" in record["rejection_reasons"]) is (not accepted)
    if not accepted:
        assert markdown == original


def test_repair_falls_back_after_generation_error_without_persisting_error_message(monkeypatch, repair_case):
    generate = Mock(side_effect=RuntimeError("provider failed with secret-token-value"))
    monkeypatch.setattr("slidenote.notes.repair._generate_cached_llm_text", generate)

    markdown, record = _repair_required_markdown_once(markdown=ORIGINAL, **repair_case)

    assert markdown == ORIGINAL
    assert record["accepted"] is False
    assert record["rejection_reasons"] == ["generation_error"]
    assert record["error_type"] == "RuntimeError"
    assert "secret-token-value" not in str(record)
    assert record["resolved_items"] == []
    assert {item["element_id"] for item in record["unresolved_items"]} == {"s1_t2", "s1_t3"}


def test_repair_does_not_swallow_user_interrupt(monkeypatch, repair_case):
    monkeypatch.setattr("slidenote.notes.repair._generate_cached_llm_text", Mock(side_effect=KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        _repair_required_markdown_once(markdown=ORIGINAL, **repair_case)


@pytest.mark.parametrize("guard_enabled", [True, False], ids=["already-complete", "guard-disabled"])
def test_repair_skips_model_when_no_repair_is_needed(monkeypatch, repair_case, guard_enabled):
    generate = Mock(side_effect=AssertionError("Unnecessary repair call"))
    monkeypatch.setattr("slidenote.notes.repair._generate_cached_llm_text", generate)
    original = ORIGINAL + "\n" + PARAGRAPHS["s1_t2"] + "\n\n" + PARAGRAPHS["s1_t3"]
    if not guard_enabled:
        repair_case["options"].content_guard = None
        original = ORIGINAL

    markdown, record = _repair_required_markdown_once(markdown=original, **repair_case)

    assert markdown == original
    assert record is None
    generate.assert_not_called()


def test_direct_generation_keeps_original_report_and_usage_after_rejected_repair(monkeypatch, repair_case, tmp_path):
    calls = []
    candidate = f"{PARAGRAPHS['s1_t2']}\n\n{PARAGRAPHS['s1_t3']}"

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def generate_with_usage(self, prompt):
            is_repair = '"task": "repair_required_learning_coverage"' in prompt
            calls.append(is_repair)
            return SimpleNamespace(
                text=candidate if is_repair else ORIGINAL,
                usage={"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
            )

    monkeypatch.setattr("slidenote.notes.llm_calls.LLMClient", FakeClient)
    guard = repair_case["options"].content_guard
    result = generate_notes_result(
        repair_case["deck"], tmp_path, use_llm=True, provider="openai", api_key="test",
        note_strategy="direct", note_context="page", content_guard=guard, cache_mode="off",
    )

    assert calls == [False, True]
    assert PARAGRAPHS["s1_t1"] in result.markdown
    assert PARAGRAPHS["s1_t4"] in result.markdown
    assert EXPLANATIONS["s1_t2"] not in result.markdown
    assert len(guard["repairs"]) == 1
    record = guard["repairs"][0]
    assert record["accepted"] is False
    assert "coverage_regression" in record["rejection_reasons"]
    assert record["resolved_items"] == []
    assert {item["element_id"] for item in record["unresolved_items"]} == {"s1_t2", "s1_t3"}
    assert {item["element_id"] for item in record["candidate_unresolved_items"]} == {"s1_t1"}
    assert guard["summary"]["repair_attempts"] == 1
    assert guard["summary"]["repair_rejections"] == 1
    assert "content_guard_repair_rejected:1" in content_guard_warnings(guard)
    assert result.llm_usage["summary"]["repair_contexts"] == 1
    assert result.llm_usage["summary"]["llm_calls"] == 2
    assert result.llm_usage["summary"]["total_tokens"] == 36
    assert result.llm_usage["repair_contexts"][0]["total_tokens"] == 18


@pytest.mark.parametrize("usage_key", ["provider_usage", "cached_entry_usage"], ids=["live", "cached"])
@pytest.mark.parametrize(
    "finish_reason,accepted",
    [("length", False), ("max_tokens", False), ("MAX_TOKENS", False),
     ("content_filter", False), ("stop", True), ("end_turn", True)],
)
def test_repair_checks_provider_completion_status_even_when_candidate_has_full_coverage(
    monkeypatch, repair_case, usage_key, finish_reason, accepted,
):
    candidate = ORIGINAL + "\n" + PARAGRAPHS["s1_t2"] + "\n\n" + PARAGRAPHS["s1_t3"]
    llm_record = {**LLM_RECORD, usage_key: {"finish_reason": finish_reason}}
    monkeypatch.setattr(
        "slidenote.notes.repair._generate_cached_llm_text",
        Mock(return_value=(candidate, llm_record)),
    )

    markdown, record = _repair_required_markdown_once(markdown=ORIGINAL, **repair_case)

    assert record["accepted"] is accepted
    assert record["candidate_unresolved_items"] == []
    assert record["llm"] == llm_record
    assert record["validation"]["finish_reason"] == finish_reason.lower()
    if accepted:
        assert markdown == candidate.strip()
        assert record["rejection_reasons"] == []
        assert record["unresolved_items"] == []
    else:
        assert markdown == ORIGINAL
        assert record["rejection_reasons"] == ["incomplete_generation"]
        assert record["resolved_items"] == []
        assert {item["element_id"] for item in record["unresolved_items"]} == {"s1_t2", "s1_t3"}
