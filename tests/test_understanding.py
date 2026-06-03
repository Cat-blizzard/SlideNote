from slidenote.models import Deck, ImageAsset, SlidePage, TableBlock, TextBlock
from slidenote.understanding import build_understanding_reports


def test_understanding_reports_converge_sections_roles_tables_and_figures():
    table = TableBlock(
        id="s1_tbl1",
        rows=[["Metric", "Value"], ["Accuracy", "95%"]],
        table_summary="表格围绕「Metric、Value」组织。",
        table_conclusion="Accuracy 是关键对比项。",
    )
    figure = ImageAsset(
        id="s1_fig1",
        path="figures/diagram.png",
        role="figure_crop",
        importance_score=0.86,
        importance_rank=1,
        importance_reason="figure_crop; large_visual_area",
        figure_explanation="Diagram explains the quorum flow.",
        anchor_element_ids=["s1_t1"],
        grounding_confidence=0.77,
    )
    deck = Deck(
        source_path="lecture.pdf",
        source_type="pdf",
        pages=[
            SlidePage(
                slide_id=1,
                title="Quorum",
                text_blocks=[TextBlock(id="s1_t1", type="heading", content="Quorum reads and writes")],
                tables=[table],
                images=[figure],
                page_modality="mixed",
                modality_confidence=0.82,
                semantic_groups=[
                    {
                        "group_id": "p1_sg1",
                        "scene_type": "table_explanation",
                        "learning_goal": "Explain quorum with the table and diagram.",
                        "block_ids": ["s1_t1", "s1_tbl1", "s1_fig1"],
                    }
                ],
            )
        ],
    )
    sections = {
        "method": "local",
        "sections": [
            {
                "section_id": "sec1",
                "title": "Replication",
                "start_slide_id": 1,
                "end_slide_id": 1,
                "slide_ids": [1],
                "reason": "first_page",
            }
        ],
    }
    deck_brief = {
        "brief": {
            "course_title": "Replication",
            "one_sentence_summary": "Replication needs quorum.",
            "core_questions": ["Why do quorum sets overlap?"],
            "key_concepts": [{"term": "Quorum", "definition": "Intersecting read/write sets", "first_slide_id": 1}],
            "page_roles": [{"slide_id": 1, "role": "definition", "reason": "Defines quorum"}],
            "cross_page_links": [],
        },
        "warnings": [],
    }
    content_guard = {
        "pages": [
            {
                "slide_id": 1,
                "items": [
                    {
                        "element_id": "s1_t1",
                        "learning_role": "definition",
                        "must_explain": True,
                        "confidence": 0.8,
                        "reason": "definition_signal",
                    }
                ],
            }
        ]
    }

    deck_understanding, page_understanding = build_understanding_reports(
        deck,
        section_plan=sections,
        deck_brief_report=deck_brief,
        content_guard_report=content_guard,
    )

    assert deck_understanding["deck"]["course_title"] == "Replication"
    assert deck_understanding["summary"]["sections_total"] == 1
    assert deck_understanding["summary"]["tables_total"] == 1
    assert deck_understanding["summary"]["high_value_figures"] == 1
    assert deck_understanding["page_roles"][0]["role"] == "definition"
    assert deck_understanding["sources"]["image_ranking"] is True
    assert page_understanding["summary"]["required_items"] == 1
    page = page_understanding["pages"][0]
    assert page["section"]["section_id"] == "sec1"
    assert page["role"]["source"] == "deck_brief"
    assert page["tables"][0]["table_conclusion"] == "Accuracy 是关键对比项。"
    assert page["figures"][0]["anchor_element_ids"] == ["s1_t1"]
    assert "Explain quorum" in page["key_points"][0] or "Quorum" in page["key_points"][0]
