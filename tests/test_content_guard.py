import json

from slidenote.content_guard import build_content_guard, missing_required_items
from slidenote.coverage import analyze_coverage
from slidenote.models import Deck, SlidePage, TextBlock


def test_local_content_guard_marks_structural_page_with_definition_as_mixed(tmp_path):
    deck = Deck(
        source_path="lecture.pptx",
        source_type="pptx",
        pages=[
            SlidePage(
                slide_id=1,
                title="目录",
                text_blocks=[
                    TextBlock(id="s1_t1", type="paragraph", content="1. 复制与一致性基础"),
                    TextBlock(id="s1_t2", type="paragraph", content="定义：Quorum 是读集合和写集合必须相交。"),
                ],
            )
        ],
    )

    report = build_content_guard(deck, tmp_path, use_llm=False)

    assert report["classifier"] == "local"
    assert report["pages"][0]["page_role"] == "mixed"
    required_ids = {item["element_id"] for item in report["items"] if item["must_explain"]}
    assert "s1_t2" in required_ids


def test_content_guard_falls_back_to_local_when_llm_json_is_invalid(tmp_path, monkeypatch):
    deck = Deck(
        source_path="lecture.pptx",
        source_type="pptx",
        pages=[
            SlidePage(
                slide_id=1,
                text_blocks=[TextBlock(id="s1_t1", type="paragraph", content="定义：一致性是指读写可见性规则。")],
            )
        ],
    )

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def generate_with_usage(self, prompt):
            class Result:
                text = "not json"
                usage = {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}

            return Result()

    monkeypatch.setattr("slidenote.content_guard.LLMClient", FakeClient)

    report = build_content_guard(deck, tmp_path, use_llm=True, api_key="test", cache_mode="off")

    assert report["classifier"] == "local_fallback"
    assert "content_guard_llm_output_not_json" in report["warnings"]
    assert report["llm"]["llm_call"] is True


def test_missing_required_items_merges_guard_metadata():
    deck = Deck(
        source_path="lecture.pptx",
        source_type="pptx",
        pages=[
            SlidePage(
                slide_id=1,
                text_blocks=[TextBlock(id="s1_t1", type="paragraph", content="定义：一致性是读写可见性规则。")],
            )
        ],
    )
    guard = {
        "items": [
            {
                "element_id": "s1_t1",
                "slide_id": 1,
                "learning_role": "definition",
                "must_explain": True,
                "confidence": 0.9,
                "reason": "definition_signal",
            }
        ],
        "pages": [{"slide_id": 1, "page_role": "content", "items": []}],
    }
    coverage = analyze_coverage(deck, "<!-- slidenote-source: p1:s1_t1 -->", content_guard=guard)

    missing = missing_required_items(guard, coverage)

    assert missing[0]["element_id"] == "s1_t1"
    assert missing[0]["learning_role"] == "definition"
    assert missing[0]["coverage"]["marker_only"] is True
