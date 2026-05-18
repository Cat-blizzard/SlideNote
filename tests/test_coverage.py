from slidenote.coverage import analyze_coverage
from slidenote.models import Deck, ImageAsset, SlidePage, TextBlock


def test_coverage_detects_missing_ids():
    deck = Deck(
        source_path="demo.pptx",
        source_type="pptx",
        pages=[
            SlidePage(
                slide_id=1,
                text_blocks=[TextBlock(id="s1_t1", type="paragraph", content="hello")],
                images=[ImageAsset(id="s1_img1", path="images/a.png")],
            )
        ],
    )

    report = analyze_coverage(deck, "hello\n【对应 PPT：第 1 页，文本块 s1_t1】")

    assert report["total"] == 2
    assert report["covered"] == 1
    assert report["missing"] == 1
    missing = [item for item in report["items"] if not item["covered"]]
    assert missing[0]["id"] == "s1_img1"


def test_coverage_reads_hidden_source_markers():
    deck = Deck(
        source_path="demo.pptx",
        source_type="pptx",
        pages=[
            SlidePage(
                slide_id=2,
                text_blocks=[TextBlock(id="s2_t1", type="paragraph", content="hello")],
                images=[ImageAsset(id="s2_img1", path="images/a.png")],
            )
        ],
    )

    report = analyze_coverage(deck, "hello\n<!-- slidenote-source: p2:s2_t1,s2_img1 -->")

    assert report["missing"] == 0


def test_coverage_counts_attached_source_marker_as_visible():
    deck = Deck(
        source_path="demo.pptx",
        source_type="pptx",
        pages=[
            SlidePage(
                slide_id=1,
                text_blocks=[TextBlock(id="s1_t1", type="paragraph", content="replication")],
            )
        ],
    )

    report = analyze_coverage(deck, "Replication improves availability.\n<!-- slidenote-source: p1:s1_t1 -->")

    assert report["missing"] == 0
    assert report["trace_coverage"]["covered"] == 1
    assert report["visible_coverage"]["covered"] == 1
    assert report["visible_coverage"]["missing"] == 0
    assert report["marker_only"] == 0


def test_coverage_treats_comment_only_marker_as_marker_only():
    deck = Deck(
        source_path="demo.pptx",
        source_type="pptx",
        pages=[
            SlidePage(
                slide_id=1,
                text_blocks=[TextBlock(id="s1_t1", type="paragraph", content="replication")],
            )
        ],
    )

    report = analyze_coverage(deck, "<!-- slidenote-source: p1:s1_t1 -->")

    assert report["missing"] == 0
    assert report["trace_coverage"]["covered"] == 1
    assert report["visible_coverage"]["covered"] == 0
    assert report["visible_coverage"]["missing"] == 1
    assert report["marker_only"] == 1
    assert report["marker_only_items"][0]["id"] == "s1_t1"


def test_required_coverage_distinguishes_marker_only_content():
    deck = Deck(
        source_path="demo.pptx",
        source_type="pptx",
        pages=[
            SlidePage(
                slide_id=1,
                text_blocks=[TextBlock(id="s1_t1", type="paragraph", content="replication improves availability")],
            )
        ],
    )
    content_guard = {
        "pages": [{"slide_id": 1, "page_role": "content", "items": []}],
        "items": [{"element_id": "s1_t1", "slide_id": 1, "must_explain": True, "confidence": 0.91}],
    }

    report = analyze_coverage(deck, "<!-- slidenote-source: p1:s1_t1 -->", content_guard=content_guard)

    assert report["trace_coverage"]["covered"] == 1
    assert report["required_visible_coverage"]["covered"] == 0
    assert report["required_visible_coverage"]["missing"] == 1
    assert report["required_visible_coverage"]["missing_items"][0]["id"] == "s1_t1"


def test_structural_pages_are_exempt_from_visible_coverage():
    deck = Deck(
        source_path="demo.pptx",
        source_type="pptx",
        pages=[
            SlidePage(
                slide_id=1,
                title="目录",
                text_blocks=[
                    TextBlock(id="s1_t1", type="paragraph", content="1. 复制与一致性基础"),
                    TextBlock(id="s1_t2", type="paragraph", content="2. 数据为中心的一致性模型"),
                    TextBlock(id="s1_t3", type="paragraph", content="3. 客户端为中心的一致性模型"),
                ],
            )
        ],
    )

    report = analyze_coverage(deck, "<!-- slidenote-source: p1:s1_t1,s1_t2,s1_t3 -->")

    assert report["missing"] == 0
    assert report["visible_coverage"]["total"] == 0
    assert report["structural_marker_only"] == 3


def test_coverage_tracks_figure_crop_ids():
    deck = Deck(
        source_path="demo.pptx",
        source_type="pptx",
        pages=[
            SlidePage(
                slide_id=3,
                images=[ImageAsset(id="s3_fig1", path="figures/slide3_fig1.png", role="figure_crop")],
            )
        ],
    )

    report = analyze_coverage(deck, "<!-- slidenote-source: p3:s3_fig1 -->")

    assert report["covered"] == 1
    assert report["missing"] == 0


def test_coverage_reports_figure_grounding_status():
    deck = Deck(
        source_path="demo.pdf",
        source_type="pdf",
        pages=[
            SlidePage(
                slide_id=4,
                images=[
                    ImageAsset(
                        id="s4_img1",
                        path="images/diagram.png",
                        anchor_element_ids=["s4_t1"],
                        figure_explanation_status="visual_summary",
                        figure_audit_status="ok",
                    )
                ],
            )
        ],
    )

    report = analyze_coverage(deck, "![图](images/diagram.png)<!-- slidenote-source: p4:s4_img1 -->")

    figures = report["figure_coverage"]["figures"]
    assert report["figure_coverage"]["covered_figures"] == 1
    assert report["figure_coverage"]["anchored_figures"] == 1
    assert report["figure_coverage"]["explained_figures"] == 1
    assert figures[0]["anchor_element_ids"] == ["s4_t1"]


def test_coverage_reports_pages_with_no_referenced_elements():
    deck = Deck(
        source_path="demo.pdf",
        source_type="pdf",
        pages=[
            SlidePage(slide_id=1, text_blocks=[TextBlock(id="s1_t1", type="paragraph", content="covered")]),
            SlidePage(slide_id=2, text_blocks=[TextBlock(id="s2_t1", type="paragraph", content="missing")]),
        ],
    )

    report = analyze_coverage(deck, "<!-- slidenote-source: p1:s1_t1 -->")

    assert report["page_coverage"]["pages_with_expected_content"] == 2
    assert report["page_coverage"]["covered_pages"] == 1
    assert report["page_coverage"]["missing_slide_ids"] == [2]
