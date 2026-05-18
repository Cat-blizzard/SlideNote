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
    assert "cin 提取运算符" in group["learning_goal"]
    assert semantic_layout_for_prompt(page)["groups"][0]["scene_type"] == "code_causal_explanation"
    assert "code_causal_explanation" in semantic_context_for_page(page)
