from pathlib import Path

from PIL import Image, ImageDraw

from slidenote.composite_figures import enrich_deck_with_composite_figures
from slidenote.figures import _crop_figures, _figure_prompt, enrich_deck_with_figures, select_figure_targets
from slidenote.modality import enrich_deck_with_modalities
from slidenote.models import Deck, ImageAsset, SlidePage, TextBlock
from slidenote.semantic_layout import enrich_deck_with_semantic_layout


def test_figure_crop_creates_image_asset_from_normalized_bbox(tmp_path):
    screenshot = tmp_path / "screenshots" / "slide1.png"
    screenshot.parent.mkdir()
    Image.new("RGB", (1000, 600), "white").save(screenshot)
    target = select_figure_targets(
        Deck(
            source_path="lecture.pdf",
            source_type="pdf",
            pages=[SlidePage(slide_id=1, page_screenshot="screenshots/slide1.png", text_blocks=[TextBlock(id="s1_t1", type="paragraph", content="short")])],
        )
    )[0]

    crops, records, skipped = _crop_figures(
        source_path=screenshot,
        output_root=tmp_path,
        figures_dir=tmp_path / "figures",
        target=target,
        figures=[{"bbox": [0.1, 0.2, 0.6, 0.7], "label": "架构图", "content_type": "diagram", "confidence": 0.9}],
        max_crops_per_page=3,
        min_confidence=0.45,
        min_area=40_000,
    )

    assert skipped == []
    assert len(crops) == 1
    assert crops[0].id == "s1_fig1"
    assert crops[0].role == "figure_crop"
    assert crops[0].crop_source_path == "screenshots/slide1.png"
    assert crops[0].crop_bbox == [0.1, 0.2, 0.6, 0.7]
    assert (tmp_path / crops[0].path).exists()
    assert records[0]["confidence"] == 0.9


def test_figure_crop_filters_low_quality_candidates(tmp_path):
    screenshot = tmp_path / "screenshots" / "slide2.png"
    screenshot.parent.mkdir()
    Image.new("RGB", (1000, 600), "white").save(screenshot)
    target = select_figure_targets(
        Deck(source_path="lecture.pdf", source_type="pdf", pages=[SlidePage(slide_id=2, page_screenshot="screenshots/slide2.png")])
    )[0]

    crops, _, skipped = _crop_figures(
        source_path=screenshot,
        output_root=tmp_path,
        figures_dir=tmp_path / "figures",
        target=target,
        figures=[
            {"bbox": [0.1, 0.1, 0.9, 0.9], "confidence": 0.1},
            {"bbox": [0.02, 0.02, 0.98, 0.98], "confidence": 0.9},
            {"bbox": [0.1, 0.1, 0.12, 0.12], "confidence": 0.9},
            {"bbox": [0.2, 0.2, 0.5, 0.5], "confidence": 0.9},
            {"bbox": [0.21, 0.21, 0.51, 0.51], "confidence": 0.95},
        ],
        max_crops_per_page=3,
        min_confidence=0.45,
        min_area=40_000,
    )

    assert len(crops) == 1
    assert {item["reason"] for item in skipped} >= {"low_confidence", "full_page_like", "small_area", "overlap_duplicate"}


def test_figure_crop_trims_bottom_contamination(tmp_path):
    screenshot = tmp_path / "screenshots" / "slide3.png"
    screenshot.parent.mkdir()
    image = Image.new("RGB", (1000, 600), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((120, 110, 850, 210), fill="navy")
    draw.rectangle((120, 350, 850, 385), fill="red")
    image.save(screenshot)
    target = select_figure_targets(
        Deck(source_path="lecture.pdf", source_type="pdf", pages=[SlidePage(slide_id=3, page_screenshot="screenshots/slide3.png")])
    )[0]

    crops, records, skipped = _crop_figures(
        source_path=screenshot,
        output_root=tmp_path,
        figures_dir=tmp_path / "figures",
        target=target,
        figures=[{"bbox": [0.1, 0.1, 0.9, 0.72], "label": "数组图", "content_type": "diagram", "confidence": 0.9}],
        max_crops_per_page=3,
        min_confidence=0.45,
        min_area=40_000,
    )

    assert skipped == []
    assert len(crops) == 1
    assert crops[0].crop_quality == "trimmed_bottom_contamination"
    assert "trimmed_bottom_contamination" in crops[0].crop_warnings
    assert crops[0].crop_bbox[3] < 0.45
    assert records[0]["original_bbox"] == [0.1, 0.1, 0.9, 0.72]


def test_figure_crop_skips_unstable_code_candidates(tmp_path):
    screenshot = tmp_path / "screenshots" / "slide4.png"
    screenshot.parent.mkdir()
    Image.new("RGB", (1000, 600), "white").save(screenshot)
    target = select_figure_targets(
        Deck(source_path="lecture.pdf", source_type="pdf", pages=[SlidePage(slide_id=4, page_screenshot="screenshots/slide4.png")])
    )[0]

    crops, _, skipped = _crop_figures(
        source_path=screenshot,
        output_root=tmp_path,
        figures_dir=tmp_path / "figures",
        target=target,
        figures=[{"bbox": [0.1, 0.1, 0.8, 0.6], "label": "代码", "content_type": "code", "confidence": 0.7}],
        max_crops_per_page=3,
        min_confidence=0.45,
        min_area=40_000,
    )

    assert crops == []
    assert skipped[0]["reason"] == "code_crop_deferred_to_ocr"
    assert skipped[0]["crop_quality"] == "code_deferred_to_ocr"


def test_figure_crop_skips_structured_text_regions(tmp_path):
    screenshot = tmp_path / "screenshots" / "slide5.png"
    screenshot.parent.mkdir()
    Image.new("RGB", (1000, 600), "white").save(screenshot)
    target = select_figure_targets(
        Deck(
            source_path="lecture.pdf",
            source_type="pdf",
            pages=[SlidePage(slide_id=5, page_screenshot="screenshots/slide5.png")],
        )
    )[0]
    page = SlidePage(
        slide_id=5,
        page_width=1000,
        page_height=600,
        text_blocks=[TextBlock(id="s5_t1", type="heading", content="High performance", bbox=[50, 50, 500, 170])],
    )

    crops, _, skipped = _crop_figures(
        source_path=screenshot,
        output_root=tmp_path,
        figures_dir=tmp_path / "figures",
        target=target,
        figures=[{"bbox": [0.04, 0.06, 0.56, 0.33], "label": "red title text", "content_type": "text", "confidence": 0.92}],
        max_crops_per_page=3,
        min_confidence=0.45,
        min_area=40_000,
        page=page,
        source_type="pdf",
    )

    assert crops == []
    assert skipped[0]["reason"] == "structured_text_region"


def test_figure_crop_skips_decorative_logo_candidates(tmp_path):
    screenshot = tmp_path / "screenshots" / "slide6.png"
    screenshot.parent.mkdir()
    Image.new("RGB", (1000, 600), "white").save(screenshot)
    target = select_figure_targets(
        Deck(source_path="lecture.pdf", source_type="pdf", pages=[SlidePage(slide_id=6, page_screenshot="screenshots/slide6.png")])
    )[0]

    crops, _, skipped = _crop_figures(
        source_path=screenshot,
        output_root=tmp_path,
        figures_dir=tmp_path / "figures",
        target=target,
        figures=[{"bbox": [0.02, 0.02, 0.18, 0.16], "label": "school logo", "content_type": "logo", "confidence": 0.98}],
        max_crops_per_page=3,
        min_confidence=0.45,
        min_area=20_000,
    )

    assert crops == []
    assert skipped[0]["reason"] == "decorative_candidate"


def test_figure_crop_skips_edge_template_candidates(tmp_path):
    screenshot = tmp_path / "screenshots" / "slide7.png"
    screenshot.parent.mkdir()
    Image.new("RGB", (1000, 600), "white").save(screenshot)
    target = select_figure_targets(
        Deck(source_path="lecture.pdf", source_type="pdf", pages=[SlidePage(slide_id=7, page_screenshot="screenshots/slide7.png")])
    )[0]

    crops, _, skipped = _crop_figures(
        source_path=screenshot,
        output_root=tmp_path,
        figures_dir=tmp_path / "figures",
        target=target,
        figures=[{"bbox": [0.01, 0.03, 0.10, 0.13], "label": "corner mark", "content_type": "unknown", "confidence": 0.96}],
        max_crops_per_page=3,
        min_confidence=0.45,
        min_area=2_000,
    )

    assert crops == []
    assert skipped[0]["reason"] == "edge_template_candidate"


def test_figure_crop_keeps_edge_knowledge_candidate(tmp_path):
    screenshot = tmp_path / "screenshots" / "slide10.png"
    screenshot.parent.mkdir()
    Image.new("RGB", (1000, 600), "white").save(screenshot)
    target = select_figure_targets(
        Deck(source_path="lecture.pdf", source_type="pdf", pages=[SlidePage(slide_id=10, page_screenshot="screenshots/slide10.png")])
    )[0]

    crops, _, skipped = _crop_figures(
        source_path=screenshot,
        output_root=tmp_path,
        figures_dir=tmp_path / "figures",
        target=target,
        figures=[{"bbox": [0.01, 0.03, 0.15, 0.15], "label": "small formula screenshot", "content_type": "screenshot", "confidence": 0.96}],
        max_crops_per_page=3,
        min_confidence=0.45,
        min_area=2_000,
    )

    assert skipped == []
    assert len(crops) == 1


def test_figure_crop_expands_when_foreground_touches_edges(tmp_path):
    screenshot = tmp_path / "screenshots" / "slide8.png"
    screenshot.parent.mkdir()
    image = Image.new("RGB", (1000, 600), "white")
    draw = ImageDraw.Draw(image)
    draw.line((200, 120, 200, 330), fill="black", width=6)
    draw.line((498, 120, 498, 330), fill="black", width=6)
    draw.line((220, 300, 470, 150), fill="black", width=5)
    image.save(screenshot)
    target = select_figure_targets(
        Deck(source_path="lecture.pdf", source_type="pdf", pages=[SlidePage(slide_id=8, page_screenshot="screenshots/slide8.png")])
    )[0]

    crops, records, skipped = _crop_figures(
        source_path=screenshot,
        output_root=tmp_path,
        figures_dir=tmp_path / "figures",
        target=target,
        figures=[{"bbox": [0.2, 0.2, 0.5, 0.55], "label": "ROC curve", "content_type": "chart", "confidence": 0.92}],
        max_crops_per_page=3,
        min_confidence=0.45,
        min_area=20_000,
    )

    assert skipped == []
    assert len(crops) == 1
    assert crops[0].crop_quality == "expanded_edge_touch"
    assert crops[0].crop_bbox[0] < 0.2
    assert crops[0].crop_bbox[2] > 0.5
    assert "expanded_left_edge_touch" in records[0]["crop_warnings"]


def test_figure_crop_merges_nearby_chart_title(tmp_path):
    screenshot = tmp_path / "screenshots" / "slide9.png"
    screenshot.parent.mkdir()
    Image.new("RGB", (1000, 600), "white").save(screenshot)
    target = select_figure_targets(
        Deck(source_path="lecture.pdf", source_type="pdf", pages=[SlidePage(slide_id=9, page_screenshot="screenshots/slide9.png")])
    )[0]
    page = SlidePage(
        slide_id=9,
        page_width=1000,
        page_height=600,
        text_blocks=[TextBlock(id="s9_t1", type="caption", content="Figure 2 ROC curve", bbox=[0.2, 0.14, 0.62, 0.18])],
    )

    crops, records, skipped = _crop_figures(
        source_path=screenshot,
        output_root=tmp_path,
        figures_dir=tmp_path / "figures",
        target=target,
        figures=[{"bbox": [0.2, 0.2, 0.62, 0.65], "label": "ROC curve", "content_type": "chart", "confidence": 0.9}],
        max_crops_per_page=3,
        min_confidence=0.45,
        min_area=20_000,
        page=page,
        source_type="pdf",
    )

    assert skipped == []
    assert len(crops) == 1
    assert crops[0].crop_quality == "merged_caption"
    assert crops[0].crop_bbox[1] == 0.14
    assert "merged_nearby_title" in records[0]["crop_warnings"]


def test_figure_prompt_includes_semantic_layout_context(tmp_path):
    page = SlidePage(
        slide_id=5,
        page_screenshot="screenshots/slide5.png",
        page_width=1000,
        page_height=600,
        text_blocks=[
            TextBlock(id="s5_t1", type="paragraph", content="cin >> student_number;", bbox=[50, 100, 300, 160]),
            TextBlock(id="s5_t2", type="paragraph", content="getline 会读取残留换行符，因此需要 cin.ignore();", bbox=[320, 160, 900, 230]),
        ],
    )
    deck = Deck(source_path="lecture.pdf", source_type="pdf", pages=[page])
    enrich_deck_with_semantic_layout(deck)
    target = select_figure_targets(deck)[0]

    prompt = _figure_prompt(target, page)

    assert "semantic_layout" in prompt
    assert "code_causal_explanation" in prompt
    assert "prefer_structured_text_then_group_image" in prompt
    assert "semantic_layout_json" in prompt


def test_figure_enrichment_uses_vision_model_and_cache(tmp_path, monkeypatch):
    screenshot = tmp_path / "screenshots" / "slide1.png"
    screenshot.parent.mkdir()
    Image.new("RGB", (1000, 600), "white").save(screenshot)

    class FakeFigureClient:
        def __init__(self, **kwargs):
            pass

        def generate_image_with_usage(self, image_path: Path, prompt: str, system_prompt: str, image_detail: str):
            class Result:
                text = '{"figures":[{"bbox":[0.1,0.2,0.6,0.7],"label":"流程图","content_type":"diagram","confidence":0.9}]}'
                usage = {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}

            return Result()

    monkeypatch.setattr("slidenote.figures.LLMClient", FakeFigureClient)
    deck = Deck(source_path="lecture.pdf", source_type="pdf", pages=[SlidePage(slide_id=1, page_screenshot="screenshots/slide1.png")])
    report = enrich_deck_with_figures(deck, tmp_path, mode="vision", provider="openai", api_key="test", cache_dir=tmp_path / "cache")

    assert report["summary"]["llm_calls"] == 1
    assert report["summary"]["crops_created"] == 1
    assert deck.pages[0].images[0].id == "s1_fig1"

    class FailingFigureClient:
        def __init__(self, **kwargs):
            raise AssertionError("cache hit should not instantiate a figure client")

    monkeypatch.setattr("slidenote.figures.LLMClient", FailingFigureClient)
    second_deck = Deck(source_path="lecture.pdf", source_type="pdf", pages=[SlidePage(slide_id=1, page_screenshot="screenshots/slide1.png")])
    second = enrich_deck_with_figures(second_deck, tmp_path, mode="vision", provider="openai", cache_dir=tmp_path / "cache")

    assert second["summary"]["local_cache_hits"] == 1
    assert second["summary"]["llm_calls"] == 0
    assert second_deck.pages[0].images[0].id == "s1_fig1"


def test_select_figure_targets_skips_pages_with_content_images():
    deck = Deck(
        source_path="lecture.pdf",
        source_type="pdf",
        pages=[
            SlidePage(slide_id=1, page_screenshot="screenshots/slide1.png", images=[ImageAsset(id="s1_img1", path="images/a.png")]),
            SlidePage(slide_id=2, page_screenshot="screenshots/slide2.png", images=[ImageAsset(id="s2_img1", path="images/page.png", role="page_image", ignored=True)]),
        ],
    )

    targets = select_figure_targets(deck)

    assert [target.slide_id for target in targets] == [2]


def test_select_figure_targets_uses_modality_hints_for_shape_diagram():
    deck = Deck(
        source_path="lecture.pdf",
        source_type="pdf",
        pages=[
            SlidePage(
                slide_id=1,
                page_screenshot="screenshots/slide1.png",
                text_blocks=[TextBlock(id="s1_t1", type="paragraph", content="short diagram labels")],
            )
        ],
    )
    enrich_deck_with_modalities(deck)

    targets = select_figure_targets(deck)

    assert len(targets) == 1
    assert targets[0].reason == "shape_diagram_modality"


def test_composite_figures_crop_cluster_and_absorb_children(tmp_path):
    screenshot = tmp_path / "screenshots" / "slide3.png"
    screenshot.parent.mkdir()
    Image.new("RGB", (1000, 600), "white").save(screenshot)
    page = SlidePage(
        slide_id=3,
        page_screenshot="screenshots/slide3.png",
        text_blocks=[TextBlock(id="s3_t1", type="paragraph", content="request flow", bbox=[0.18, 0.25, 0.55, 0.31])],
        images=[
            ImageAsset(id="s3_img1", path="images/client.png", bbox=[0.18, 0.36, 0.24, 0.46], width=60, height=60),
            ImageAsset(id="s3_img2", path="images/arrow.png", bbox=[0.27, 0.39, 0.38, 0.42], width=110, height=20),
            ImageAsset(id="s3_img3", path="images/cache.png", bbox=[0.41, 0.36, 0.47, 0.46], width=60, height=60),
            ImageAsset(id="s3_img4", path="images/db.png", bbox=[0.52, 0.36, 0.58, 0.46], width=60, height=60),
        ],
    )
    deck = Deck(source_path="lecture.pdf", source_type="pdf", pages=[page])
    enrich_deck_with_modalities(deck)

    report = enrich_deck_with_composite_figures(deck, tmp_path)

    assert report["summary"]["composites_created"] == 1
    composite = next(image for image in page.images if image.role == "composite_figure")
    assert composite.id == "s3_fig1"
    assert composite.crop_method == "composite_layout"
    assert (tmp_path / composite.path).exists()
    assert composite.source_element_ids[:4] == ["s3_img1", "s3_img2", "s3_img3", "s3_img4"]
    assert "s3_t1" in composite.source_element_ids
    children = [image for image in page.images if image.role == "composite_child"]
    assert len(children) == 4
    assert all(image.ignored for image in children)
