from pathlib import Path

from slidenote.models import Deck, ImageAsset, SlidePage, TextBlock
from slidenote.source_map import build_source_map


def test_source_map_links_note_blocks_to_elements(tmp_path):
    deck = Deck(
        source_path="lecture.pdf",
        source_type="pdf",
        pages=[
            SlidePage(
                slide_id=2,
                text_blocks=[TextBlock(id="s2_t1", type="paragraph", content="TCP")],
                images=[
                    ImageAsset(id="s2_img1", path="images/diagram.png", role="content"),
                    ImageAsset(id="s2_img2", path="images/icon.png", ignored=True, role="decorative"),
                ],
            )
        ],
    )
    notes = "TCP 是传输层协议。\n【对应 PPT：第 2 页，文本块 s2_t1，图片 s2_img1】\n"

    source_map = build_source_map(deck, notes, tmp_path)

    refs = source_map["note_blocks"][0]["source_refs"]
    assert {ref["element_id"] for ref in refs} == {"s2_t1", "s2_img1"}
    assert source_map["pages"][0]["images"][1]["ignored"] is True
    assert source_map["default_display_mode"] == "hidden"


def test_source_map_links_hidden_source_comments(tmp_path):
    deck = Deck(
        source_path="lecture.pdf",
        source_type="pdf",
        pages=[SlidePage(slide_id=3, text_blocks=[TextBlock(id="s3_t1", type="paragraph", content="一致性")])],
    )
    notes = "一致性模型描述读写可见性。\n<!-- slidenote-source: p3:s3_t1 -->\n"

    source_map = build_source_map(deck, notes, tmp_path)

    refs = source_map["note_blocks"][0]["source_refs"]
    assert [ref["element_id"] for ref in refs] == ["s3_t1"]


def test_source_map_includes_figure_crop_metadata(tmp_path):
    deck = Deck(
        source_path="lecture.pdf",
        source_type="pdf",
        pages=[
            SlidePage(
                slide_id=4,
                images=[
                    ImageAsset(
                        id="s4_fig1",
                        path="figures/slide4_fig1.png",
                        role="figure_crop",
                        crop_source_path="screenshots/slide4.png",
                        crop_bbox=[0.1, 0.2, 0.6, 0.8],
                        crop_method="vision_bbox",
                        confidence=0.88,
                    )
                ],
            )
        ],
    )

    source_map = build_source_map(deck, "<!-- slidenote-source: p4:s4_fig1 -->", tmp_path)

    ref = source_map["note_blocks"][0]["source_refs"][0]
    assert ref["element_id"] == "s4_fig1"
    assert ref["crop_bbox"] == [0.1, 0.2, 0.6, 0.8]
    assert source_map["pages"][0]["images"][0]["crop_method"] == "vision_bbox"
