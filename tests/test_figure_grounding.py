from PIL import Image

from slidenote.figure_grounding import enrich_deck_with_figure_grounding, normalized_image_bbox, ordered_page_elements
from slidenote.models import Deck, ImageAsset, SlidePage, TextBlock


def test_figure_grounding_anchors_image_to_nearby_text():
    deck = Deck(
        source_path="lecture.pptx",
        source_type="pptx",
        pages=[
            SlidePage(
                slide_id=1,
                page_width=1000,
                page_height=800,
                text_blocks=[
                    TextBlock(id="s1_t1", type="paragraph", content="Quorum intersection", bbox=[100, 100, 700, 80]),
                    TextBlock(id="s1_t2", type="paragraph", content="Later detail", bbox=[100, 650, 700, 80]),
                ],
                images=[
                    ImageAsset(
                        id="s1_img1",
                        path="images/quorum.png",
                        bbox=[120, 220, 600, 280],
                        visual_summary="图中展示读写 quorum 的交集区域",
                        role="content",
                        importance_score=0.8,
                    )
                ],
            )
        ],
    )

    report = enrich_deck_with_figure_grounding(deck, output_root="out")
    image = deck.pages[0].images[0]

    assert image.anchor_element_ids == ["s1_t1"]
    assert image.anchor_reason == "bbox_nearest_preceding_element"
    assert image.figure_explanation_status == "visual_summary"
    assert report["summary"]["anchored_images"] == 1
    assert report["summary"]["explained_images"] == 1


def test_figure_grounding_normalizes_crop_bbox_and_orders_elements():
    page = SlidePage(
        slide_id=2,
        page_width=1000,
        page_height=600,
        text_blocks=[TextBlock(id="s2_t1", type="paragraph", content="Architecture", bbox=[50, 50, 800, 80])],
        images=[
            ImageAsset(
                id="s2_fig1",
                path="figures/slide2_fig1.png",
                role="figure_crop",
                crop_bbox=[0.1, 0.3, 0.7, 0.8],
            )
        ],
    )
    deck = Deck(source_path="lecture.pdf", source_type="pdf", pages=[page])

    assert normalized_image_bbox(deck, page, page.images[0]) == [0.1, 0.3, 0.7, 0.8]
    elements = ordered_page_elements(deck, page)

    assert [element["id"] for element in elements] == ["s2_t1", "s2_fig1"]
    assert elements[1]["kind"] == "image"


def test_figure_grounding_vision_applies_group_anchor(monkeypatch, tmp_path):
    screenshot = tmp_path / "screenshots" / "slide1.png"
    screenshot.parent.mkdir()
    Image.new("RGB", (20, 20), "white").save(screenshot)

    class FakeVisionClient:
        def __init__(self, **kwargs):
            pass

        def generate_image_with_usage(self, image_path, prompt, system_prompt=None, image_detail="low"):
            class Result:
                text = """
                {
                  "figures": [
                    {
                      "image_id": "s1_img1",
                      "anchor_element_ids": ["s1_t1"],
                      "anchor_group_id": "p1_sg1",
                      "figure_explanation": "This diagram supports quorum intersection.",
                      "grounding_confidence": 0.88,
                      "anchor_reason": "Matches the nearby quorum explanation",
                      "audit_status": "ok"
                    }
                  ],
                  "warnings": []
                }
                """
                usage = {"input_tokens": 12, "output_tokens": 18, "total_tokens": 30}

            return Result()

    monkeypatch.setattr("slidenote.figure_grounding.LLMClient", FakeVisionClient)
    page = SlidePage(
        slide_id=1,
        page_width=1000,
        page_height=800,
        page_screenshot="screenshots/slide1.png",
        semantic_groups=[{"group_id": "p1_sg1", "block_ids": ["s1_t1"], "scene_type": "visual_explanation"}],
        text_blocks=[TextBlock(id="s1_t1", type="paragraph", content="Quorum intersection", bbox=[100, 100, 700, 80])],
        images=[ImageAsset(id="s1_img1", path="images/quorum.png", bbox=[120, 220, 600, 280], role="content", importance_score=0.8)],
    )
    deck = Deck(source_path="lecture.pptx", source_type="pptx", pages=[page])

    report = enrich_deck_with_figure_grounding(
        deck,
        output_root=tmp_path,
        mode="vision",
        provider="openai",
        api_key="test",
        cache_dir=tmp_path / "cache",
    )

    image = deck.pages[0].images[0]
    assert image.anchor_element_ids == ["s1_t1"]
    assert image.anchor_group_id == "p1_sg1"
    assert image.figure_explanation_status == "vision_grounding"
    assert report["summary"]["vision_calls"] == 1
    assert report["summary"]["vision_applied_images"] == 1


def test_figure_grounding_vision_low_confidence_keeps_local_anchor(monkeypatch, tmp_path):
    screenshot = tmp_path / "screenshots" / "slide2.png"
    screenshot.parent.mkdir()
    Image.new("RGB", (20, 20), "white").save(screenshot)

    class FakeVisionClient:
        def __init__(self, **kwargs):
            pass

        def generate_image_with_usage(self, image_path, prompt, system_prompt=None, image_detail="low"):
            class Result:
                text = '{"figures":[{"image_id":"s2_img1","anchor_element_ids":["s2_t2"],"grounding_confidence":0.2,"anchor_reason":"bad"}]}'
                usage = {"input_tokens": 5, "output_tokens": 5, "total_tokens": 10}

            return Result()

    monkeypatch.setattr("slidenote.figure_grounding.LLMClient", FakeVisionClient)
    page = SlidePage(
        slide_id=2,
        page_width=1000,
        page_height=800,
        page_screenshot="screenshots/slide2.png",
        text_blocks=[
            TextBlock(id="s2_t1", type="paragraph", content="Primary explanation", bbox=[100, 100, 700, 80]),
            TextBlock(id="s2_t2", type="paragraph", content="Secondary text", bbox=[100, 650, 700, 80]),
        ],
        images=[ImageAsset(id="s2_img1", path="images/q.png", bbox=[120, 220, 600, 280], role="content", importance_score=0.8)],
    )
    deck = Deck(source_path="lecture.pptx", source_type="pptx", pages=[page])

    enrich_deck_with_figure_grounding(deck, output_root=tmp_path, mode="vision", provider="openai", api_key="test", cache_dir=tmp_path / "cache")

    image = deck.pages[0].images[0]
    assert image.anchor_element_ids == ["s2_t1"]
    assert image.anchor_reason == "bbox_nearest_preceding_element"


def test_figure_grounding_vision_bad_json_falls_back_local(monkeypatch, tmp_path):
    screenshot = tmp_path / "screenshots" / "slide3.png"
    screenshot.parent.mkdir()
    Image.new("RGB", (20, 20), "white").save(screenshot)

    class FakeVisionClient:
        def __init__(self, **kwargs):
            pass

        def generate_image_with_usage(self, image_path, prompt, system_prompt=None, image_detail="low"):
            class Result:
                text = "not-json"
                usage = {"input_tokens": 5, "output_tokens": 5, "total_tokens": 10}

            return Result()

    monkeypatch.setattr("slidenote.figure_grounding.LLMClient", FakeVisionClient)
    page = SlidePage(
        slide_id=3,
        page_width=1000,
        page_height=800,
        page_screenshot="screenshots/slide3.png",
        text_blocks=[TextBlock(id="s3_t1", type="paragraph", content="Primary explanation", bbox=[100, 100, 700, 80])],
        images=[ImageAsset(id="s3_img1", path="images/q.png", bbox=[120, 220, 600, 280], role="content", importance_score=0.8)],
    )
    deck = Deck(source_path="lecture.pptx", source_type="pptx", pages=[page])

    report = enrich_deck_with_figure_grounding(
        deck,
        output_root=tmp_path,
        mode="vision",
        provider="openai",
        api_key="test",
        cache_dir=tmp_path / "cache",
    )

    image = deck.pages[0].images[0]
    assert image.anchor_element_ids == ["s3_t1"]
    assert report["summary"]["vision_fallback_images"] == 1
