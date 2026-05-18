from slidenote.ir import build_deck_ir, build_page_ir, element_index_from_ir, iter_expected_source_elements
from slidenote.models import Deck, ImageAsset, SlidePage, TableBlock, TextBlock


def test_page_ir_normalizes_source_elements_and_semantic_groups():
    table = TableBlock(
        id="s1_tbl1",
        rows=[["Protocol", "Feature"], ["TCP", "Reliable"]],
        table_summary="Protocol feature comparison",
        table_conclusion="TCP is reliable.",
        key_rows=[{"label": "TCP", "reason": "Reliable row"}],
    )
    page = SlidePage(
        slide_id=1,
        title="Transport",
        text_blocks=[TextBlock(id="s1_t1", type="title", content="Transport")],
        tables=[table],
        images=[
            ImageAsset(
                id="s1_fig1",
                path="figures/slide1_fig1.png",
                role="figure_crop",
                crop_bbox=[0.1, 0.2, 0.8, 0.9],
                crop_quality="clean",
                source_element_ids=["s1_img1"],
            ),
            ImageAsset(id="s1_img1", path="images/raw.png", ignored=True, role="composite_child"),
        ],
        semantic_groups=[
            {
                "group_id": "p1_sg1",
                "scene_type": "code_causal_explanation",
                "learning_goal": "Explain cause and fix",
                "block_ids": ["s1_t1", "s1_fig1"],
                "source_element_ids": ["s1_t1", "s1_fig1"],
                "must_explain": True,
                "crop_policy": "prefer_structured_text_then_group_image",
            }
        ],
    )
    page.semantic_blocks = [
        {"id": "s1_t1", "learning_role": "definition", "must_explain": True, "group_id": "p1_sg1"},
        {"id": "s1_tbl1", "learning_role": "table_conclusion", "must_explain": True},
    ]
    deck = Deck(source_path="lecture.pdf", source_type="pdf", pages=[page])

    page_ir = build_page_ir(deck, page)

    assert [element["kind"] for element in page_ir["elements"]] == [
        "text",
        "table",
        "image",
        "image",
        "semantic_group",
    ]
    table_ir = next(element for element in page_ir["elements"] if element["element_id"] == "s1_tbl1")
    assert table_ir["evidence"]["table_conclusion"] == "TCP is reliable."
    assert table_ir["roles"]["learning_role"] == "table_conclusion"
    group_ir = next(element for element in page_ir["elements"] if element["kind"] == "semantic_group")
    assert group_ir["source_ids"] == ["s1_t1", "s1_fig1"]
    expected_ids = [element["element_id"] for element in iter_expected_source_elements(deck)]
    assert expected_ids == ["s1_t1", "s1_tbl1", "s1_fig1"]


def test_deck_ir_and_element_index_preserve_source_map_metadata():
    image = ImageAsset(
        id="s2_fig1",
        path="figures/slide2_fig1.png",
        role="figure_crop",
        crop_source_path="screenshots/slide2.png",
        crop_bbox=[0.2, 0.3, 0.7, 0.8],
        crop_method="vision_bbox",
        crop_quality="needs_review",
        crop_warnings=["neighbor_content"],
        anchor_element_ids=["s2_t1"],
        grounding_confidence=0.8,
        figure_explanation_status="visual_summary",
    )
    table = TableBlock(
        id="s2_tbl1",
        rows=[["Name", "Value"], ["A", "1"]],
        table_conclusion="A has value 1.",
        key_rows=[{"label": "A"}],
    )
    deck = Deck(
        source_path="lecture.pdf",
        source_type="pdf",
        pages=[
            SlidePage(
                slide_id=2,
                text_blocks=[TextBlock(id="s2_t1", type="paragraph", content="Figure anchor")],
                tables=[table],
                images=[image],
            )
        ],
    )

    deck_ir = build_deck_ir(deck)
    index = element_index_from_ir(deck)

    assert deck_ir["pages"][0]["elements"][0]["slide_id"] == 2
    assert index["s2_tbl1"]["table_conclusion"] == "A has value 1."
    assert index["s2_tbl1"]["key_rows"] == [{"label": "A"}]
    assert index["s2_fig1"]["crop_quality"] == "needs_review"
    assert index["s2_fig1"]["crop_warnings"] == ["neighbor_content"]
    assert index["s2_fig1"]["anchor_element_ids"] == ["s2_t1"]
