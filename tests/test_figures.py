from pathlib import Path

from PIL import Image

from slidenote.composite_figures import enrich_deck_with_composite_figures
from slidenote.figures import _crop_figures, enrich_deck_with_figures, select_figure_targets
from slidenote.modality import enrich_deck_with_modalities
from slidenote.models import Deck, ImageAsset, SlidePage, TextBlock


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
