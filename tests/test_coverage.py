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
