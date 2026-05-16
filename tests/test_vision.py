from pathlib import Path

import pytest
from PIL import Image

from slidenote.modality import enrich_deck_with_modalities
from slidenote.models import Deck, ImageAsset, SlidePage, TextBlock
from slidenote.vision import enrich_deck_with_vision, select_vision_targets


def test_auto_vision_prefers_extracted_image_before_page_screenshot(tmp_path):
    screenshot = tmp_path / "screenshots" / "slide1.png"
    screenshot.parent.mkdir()
    Image.new("RGB", (800, 450), "white").save(screenshot)
    deck = Deck(
        source_path="lecture.pptx",
        source_type="pptx",
        pages=[
            SlidePage(
                slide_id=1,
                text_blocks=[TextBlock(id="s1_t1", type="paragraph", content="short")],
                images=[ImageAsset(id="s1_img1", path="images/a.png")],
                page_screenshot="screenshots/slide1.png",
            )
        ],
    )

    targets = select_vision_targets(deck, tmp_path, mode="auto")

    assert len(targets) == 1
    assert targets[0].kind == "image"
    assert targets[0].image_id == "s1_img1"


def test_auto_vision_prefers_figure_crop_before_embedded_image(tmp_path):
    figure = tmp_path / "figures" / "slide1_fig1.png"
    figure.parent.mkdir()
    Image.new("RGB", (500, 300), "white").save(figure)
    embedded = tmp_path / "images" / "slide1_img1.png"
    embedded.parent.mkdir()
    Image.new("RGB", (500, 300), "white").save(embedded)
    deck = Deck(
        source_path="lecture.pptx",
        source_type="pptx",
        pages=[
            SlidePage(
                slide_id=1,
                images=[
                    ImageAsset(id="s1_img1", path="images/slide1_img1.png"),
                    ImageAsset(id="s1_fig1", path="figures/slide1_fig1.png", role="figure_crop"),
                ],
            )
        ],
    )

    targets = select_vision_targets(deck, tmp_path, mode="auto")

    assert len(targets) == 1
    assert targets[0].image_id == "s1_fig1"


def test_auto_vision_uses_modality_hint_for_image_only_page(tmp_path):
    screenshot = tmp_path / "screenshots" / "slide1.png"
    screenshot.parent.mkdir()
    Image.new("RGB", (800, 450), "white").save(screenshot)
    deck = Deck(source_path="lecture.pdf", source_type="pdf", pages=[SlidePage(slide_id=1, page_screenshot="screenshots/slide1.png")])
    enrich_deck_with_modalities(deck)

    targets = select_vision_targets(deck, tmp_path, mode="auto")

    assert len(targets) == 1
    assert targets[0].kind == "page_screenshot"
    assert targets[0].reason == "auto_page_visual"


def test_vision_enrichment_writes_summary_and_uses_cache(tmp_path, monkeypatch):
    screenshot = tmp_path / "screenshots" / "slide1.png"
    screenshot.parent.mkdir()
    Image.new("RGB", (800, 450), "white").save(screenshot)
    deck = Deck(
        source_path="lecture.pptx",
        source_type="pptx",
        pages=[SlidePage(slide_id=1, page_screenshot="screenshots/slide1.png")],
    )

    class FakeVisionClient:
        def __init__(self, **kwargs):
            pass

        def generate_image_with_usage(self, image_path: Path, prompt: str, system_prompt: str, image_detail: str):
            class Result:
                text = '{"ocr_text":"TCP","visual_summary":"图中展示 TCP 流程。","content_type":"diagram","confidence":0.9,"warnings":[]}'
                usage = {"input_tokens": 20, "output_tokens": 12, "total_tokens": 32}

            return Result()

    monkeypatch.setattr("slidenote.vision.LLMClient", FakeVisionClient)
    report = enrich_deck_with_vision(deck, tmp_path, mode="auto", provider="openai", api_key="test", cache_dir=tmp_path / "cache")

    assert report["summary"]["llm_calls"] == 1
    assert deck.pages[0].page_ocr_text == "TCP"
    assert "TCP 流程" in deck.pages[0].page_visual_summary

    class FailingVisionClient:
        def __init__(self, **kwargs):
            raise AssertionError("cache hit should not instantiate a vision client")

    monkeypatch.setattr("slidenote.vision.LLMClient", FailingVisionClient)
    second = enrich_deck_with_vision(deck, tmp_path, mode="auto", provider="openai", cache_dir=tmp_path / "cache")

    assert second["summary"]["local_cache_hits"] == 1
    assert second["summary"]["llm_calls"] == 0


def test_vision_enrichment_cleans_temp_image_on_model_error(tmp_path, monkeypatch):
    screenshot = tmp_path / "screenshots" / "slide1.png"
    screenshot.parent.mkdir()
    Image.new("RGB", (800, 450), "white").save(screenshot)
    deck = Deck(
        source_path="lecture.pptx",
        source_type="pptx",
        pages=[SlidePage(slide_id=1, page_screenshot="screenshots/slide1.png")],
    )
    seen: dict[str, Path] = {}

    class FailingVisionClient:
        def __init__(self, **kwargs):
            pass

        def generate_image_with_usage(self, image_path: Path, prompt: str, system_prompt: str, image_detail: str):
            assert image_path.exists()
            seen["path"] = image_path
            raise RuntimeError("boom")

    monkeypatch.setattr("slidenote.vision.LLMClient", FailingVisionClient)

    with pytest.raises(RuntimeError, match="boom"):
        enrich_deck_with_vision(deck, tmp_path, mode="auto", provider="openai", api_key="test", cache_dir=tmp_path / "cache")

    assert seen["path"].exists() is False


def test_vision_prompt_includes_page_context():
    from slidenote.vision import VisionTarget, _vision_prompt

    page = SlidePage(
        slide_id=2,
        title="TCP",
        text_blocks=[TextBlock(id="s2_t1", type="paragraph", content="三次握手")],
    )
    prompt = _vision_prompt(VisionTarget(slide_id=2, kind="page_screenshot", path="screenshots/slide2.png"), page)

    assert "三次握手" in prompt
    assert "如果图片和本页文字互相解释" in prompt
