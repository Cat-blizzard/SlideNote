from PIL import Image

from slidenote.models import Deck, SlidePage, TextBlock
from slidenote.semantic_layout import enrich_deck_with_semantic_layout, semantic_context_for_page, semantic_layout_for_prompt


def test_semantic_layout_groups_code_output_cause_and_fix():
    page = SlidePage(
        slide_id=1,
        page_width=1000,
        page_height=600,
        title="string 字符串的输入",
        text_blocks=[
            TextBlock(id="s1_t1", type="title", content="6.5.1 string字符串的输入", bbox=[300, 20, 700, 60]),
            TextBlock(
                id="s1_t2",
                type="paragraph",
                content='#include <string>\nint main() {\ncin >> student_number;\ngetline(cin, student_name);\n}',
                bbox=[50, 90, 400, 500],
            ),
            TextBlock(
                id="s1_t3",
                type="paragraph",
                content="Enter student number: 12345\nEnter student name:\nStudent Number: 12345",
                bbox=[600, 80, 950, 190],
            ),
            TextBlock(
                id="s1_t4",
                type="paragraph",
                content="cin 的 >> 流提取运算符输入数据后，再使用 getline 会发生异常，因为换行符仍留在输入流中。",
                bbox=[390, 210, 980, 340],
            ),
            TextBlock(
                id="s1_t5",
                type="paragraph",
                content="cin.ignore(); 需要在 getline 之前清空换行符。",
                bbox=[70, 360, 560, 395],
            ),
        ],
    )
    deck = Deck(source_path="lecture.pdf", source_type="pdf", pages=[page])

    report = enrich_deck_with_semantic_layout(deck)

    assert report["summary"]["groups_total"] == 1
    group = page.semantic_groups[0]
    assert group["scene_type"] == "code_causal_explanation"
    assert group["crop_policy"] == "prefer_structured_text_then_group_image"
    assert set(group["block_ids"]) >= {"s1_t2", "s1_t3", "s1_t4", "s1_t5"}
    roles = {block["id"]: block["learning_role"] for block in page.semantic_blocks}
    assert roles["s1_t2"] == "code_example"
    assert roles["s1_t3"] == "runtime_output"
    assert roles["s1_t4"] == "cause"
    assert roles["s1_t5"] == "fix"
    assert any(relation["relation"] == "fixes" for relation in page.semantic_relations)
    assert "换行符" in group["learning_goal"]
    assert semantic_layout_for_prompt(page)["groups"][0]["scene_type"] == "code_causal_explanation"
    assert "code_causal_explanation" in semantic_context_for_page(page)


def test_semantic_layout_local_mode_does_not_call_llm(monkeypatch, tmp_path):
    class FailingClient:
        def __init__(self, **kwargs):
            raise AssertionError("local mode should not construct a vision client")

    monkeypatch.setattr("slidenote.semantic_layout.LLMClient", FailingClient)
    deck = Deck(
        source_path="lecture.pdf",
        source_type="pdf",
        pages=[SlidePage(slide_id=1, text_blocks=[TextBlock(id="s1_t1", type="paragraph", content="hello")])],
    )

    report = enrich_deck_with_semantic_layout(deck, output_root=tmp_path, mode="local")

    assert report["mode"] == "local"
    assert report["summary"]["vision_calls"] == 0
    assert report["pages"][0]["method"] == "local_rules_v1"
    assert report["pages"][0]["vision_enhancement"]["status"] == "disabled"


def test_semantic_layout_vision_mode_applies_model_result_and_caches(monkeypatch, tmp_path):
    screenshot = tmp_path / "screenshots" / "slide1.png"
    screenshot.parent.mkdir()
    Image.new("RGB", (20, 20), "white").save(screenshot)

    class FakeVisionClient:
        def __init__(self, **kwargs):
            pass

        def generate_image_with_usage(self, image_path, prompt, system_prompt=None, image_detail="low"):
            class Result:
                text = """
                {
                  "confidence": 0.91,
                  "reason": "code and explanation belong together",
                  "warnings": [],
                  "groups": [
                    {
                      "group_id": "p1_vsg1",
                      "scene_type": "concept_explanation",
                      "learning_goal": "Explain the page",
                      "block_ids": ["s1_t1"]
                    }
                  ],
                  "relations": [
                    {
                      "from": "s1_t999",
                      "to": "s1_t1",
                      "relation": "annotates",
                      "reason": "invalid"
                    }
                  ]
                }
                """
                usage = {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}

            return Result()

    monkeypatch.setattr("slidenote.semantic_layout.LLMClient", FakeVisionClient)
    page = SlidePage(
        slide_id=1,
        page_screenshot="screenshots/slide1.png",
        page_modality="mixed",
        text_blocks=[TextBlock(id="s1_t1", type="paragraph", content="visual explanation", bbox=[0.1, 0.1, 0.6, 0.2])],
    )
    deck = Deck(source_path="lecture.pdf", source_type="pdf", pages=[page])

    report = enrich_deck_with_semantic_layout(deck, output_root=tmp_path, mode="vision", provider="openai", api_key="test", cache_dir=tmp_path / "cache")

    assert report["summary"]["vision_calls"] == 1
    assert report["summary"]["vision_enhanced_pages"] == 1
    assert report["pages"][0]["method"] == "vision_enhanced_v1"
    assert report["pages"][0]["warnings"] == ["discarded_invalid_model_references"]
    assert page.semantic_groups[0]["group_id"] == "p1_vsg1"

    class FailingVisionClient:
        def __init__(self, **kwargs):
            raise AssertionError("cache hit should skip client construction")

    monkeypatch.setattr("slidenote.semantic_layout.LLMClient", FailingVisionClient)
    second_page = SlidePage(
        slide_id=1,
        page_screenshot="screenshots/slide1.png",
        page_modality="mixed",
        text_blocks=[TextBlock(id="s1_t1", type="paragraph", content="visual explanation", bbox=[0.1, 0.1, 0.6, 0.2])],
    )
    second = enrich_deck_with_semantic_layout(
        Deck(source_path="lecture.pdf", source_type="pdf", pages=[second_page]),
        output_root=tmp_path,
        mode="vision",
        provider="openai",
        cache_dir=tmp_path / "cache",
    )

    assert second["summary"]["vision_cache_hits"] == 1
    assert second["summary"]["vision_calls"] == 0


def test_code_and_role_heuristics_do_not_fire_on_ordinary_prose():
    from slidenote.semantic_layout import _contains_cause_signal, _contains_fix_signal, _looks_like_code, _looks_like_output

    for prose in ["The data point shows a vaccine trial.", "A scout counts the print runs.", "所以这一节需要先回顾之前的定义。"]:
        assert not _looks_like_code(prose)
        assert not _looks_like_output(prose)
    assert not _contains_cause_signal("所以这一节需要先回顾之前的定义。")
    assert not _contains_fix_signal("必须掌握之后的内容，before class 预习。")
    assert _looks_like_code("int count = 0;")
    assert _looks_like_code("cout << value;")
    assert _looks_like_output("Enter your name: Alice")
    assert _contains_cause_signal("因为缓冲区里残留了换行符")
    assert _contains_fix_signal("调用 ignore 清空缓冲区即可解决")
