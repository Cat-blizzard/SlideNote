from slidenote.ir import build_deck_ir, build_page_ir, element_index_from_ir, iter_expected_source_elements
from slidenote.models import Deck, ImageAsset, SlidePage, TableBlock, TextBlock


def test_page_ir_normalizes_source_elements_and_semantic_groups():
    table = TableBlock(
        id="s1_tbl1",
        rows=[["Protocol", "Feature"], ["TCP", "Reliable"]],
        bbox=[120, 220, 520, 300],
        table_summary="Protocol feature comparison",
        table_conclusion="TCP is reliable.",
        key_rows=[{"label": "TCP", "reason": "Reliable row"}],
    )
    page = SlidePage(
        slide_id=1,
        page_width=1000,
        page_height=1000,
        title="Transport",
        text_blocks=[TextBlock(id="s1_t1", type="title", content="Transport", bbox=[40, 50, 400, 90])],
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
    assert table_ir["role"] == "table_conclusion"
    assert table_ir["bbox_format"] == "source_xyxy"
    assert table_ir["bbox_normalized"] == [0.12, 0.22, 0.52, 0.3]
    assert table_ir["coverage_state"] == "required"
    assert table_ir["coverage"]["required"] is True
    assert table_ir["reading_order"] > 0
    assert 0.0 <= table_ir["confidence"] <= 1.0
    text_ir = next(element for element in page_ir["elements"] if element["element_id"] == "s1_t1")
    assert text_ir["role"] == "definition"
    assert text_ir["bbox_normalized"] == [0.04, 0.05, 0.4, 0.09]
    figure_ir = next(element for element in page_ir["elements"] if element["element_id"] == "s1_fig1")
    assert figure_ir["bbox_format"] == "normalized_xyxy"
    assert figure_ir["bbox_normalized"] == [0.1, 0.2, 0.8, 0.9]
    group_ir = next(element for element in page_ir["elements"] if element["kind"] == "semantic_group")
    assert group_ir["source_ids"] == ["s1_t1", "s1_fig1"]
    assert group_ir["coverage_state"] == "structural_group"
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
    assert index["s2_fig1"]["bbox_normalized"] == [0.2, 0.3, 0.7, 0.8]
    assert index["s2_fig1"]["coverage_state"] == "expected"


def test_ir_can_merge_content_guard_and_final_coverage_state():
    page = SlidePage(
        slide_id=1,
        page_width=1000,
        page_height=1000,
        text_blocks=[
            TextBlock(id="s1_t1", type="paragraph", content="Covered definition", bbox=[0, 0, 400, 80]),
            TextBlock(id="s1_t2", type="paragraph", content="Missing theorem", bbox=[0, 120, 400, 180]),
        ],
    )
    deck = Deck(source_path="lecture.pdf", source_type="pdf", pages=[page])
    content_guard = {
        "pages": [{"slide_id": 1, "page_role": "content"}],
        "items": [
            {
                "element_id": "s1_t2",
                "slide_id": 1,
                "learning_role": "theorem",
                "must_explain": True,
                "confidence": 0.93,
            }
        ],
    }
    coverage_report = {
        "items": [
            {
                "id": "s1_t1",
                "slide_id": 1,
                "kind": "text:paragraph",
                "trace_covered": True,
                "visible_covered": True,
                "marker_only": False,
                "structural": False,
                "required": False,
            },
            {
                "id": "s1_t2",
                "slide_id": 1,
                "kind": "text:paragraph",
                "trace_covered": False,
                "visible_covered": False,
                "marker_only": False,
                "structural": False,
                "required": True,
            },
        ]
    }

    page_ir = build_page_ir(deck, page, content_guard=content_guard, coverage_report=coverage_report)

    assert page_ir["page_role"] == "content"
    covered = next(element for element in page_ir["elements"] if element["element_id"] == "s1_t1")
    missing = next(element for element in page_ir["elements"] if element["element_id"] == "s1_t2")
    assert covered["coverage_state"] == "visible_covered"
    assert covered["coverage"]["trace_covered"] is True
    assert missing["role"] == "theorem"
    assert missing["confidence"] == 0.93
    assert missing["coverage_state"] == "missing_required"
    assert missing["coverage"]["required"] is True
