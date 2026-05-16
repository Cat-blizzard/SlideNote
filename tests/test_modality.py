from slidenote.modality import classify_page_modality, enrich_deck_with_modalities
from slidenote.models import Deck, ImageAsset, SlidePage, TableBlock, TextBlock


def test_modality_detects_mixed_page_with_embedded_content_image():
    page = SlidePage(
        slide_id=1,
        text_blocks=[TextBlock(id="s1_t1", type="paragraph", content="A" * 120)],
        images=[ImageAsset(id="s1_img1", path="images/diagram.png")],
        page_screenshot="screenshots/slide1.png",
    )

    result = classify_page_modality(page)

    assert result.modality == "mixed"
    assert "use_embedded_images" in result.processing_hints
    assert "crop_figures_from_screenshot" not in result.processing_hints


def test_modality_detects_image_only_page_and_recommends_visual_pipeline():
    page = SlidePage(
        slide_id=2,
        images=[ImageAsset(id="s2_img1", path="images/page.png", role="page_image", ignored=True)],
        page_screenshot="screenshots/slide2.png",
        warnings=["No selectable text or embedded images detected. This page may need OCR."],
    )

    result = classify_page_modality(page)

    assert result.modality == "image_only"
    assert "ocr_page_screenshot" in result.processing_hints
    assert "crop_figures_from_screenshot" in result.processing_hints
    assert "vision_page_screenshot" in result.processing_hints


def test_modality_detects_shape_diagram_page_without_embedded_images():
    page = SlidePage(
        slide_id=3,
        text_blocks=[TextBlock(id="s3_t1", type="paragraph", content="短文字说明")],
        tables=[TableBlock(id="s3_tbl1", rows=[["A", "B"]])],
        page_screenshot="screenshots/slide3.png",
    )

    result = classify_page_modality(page)

    assert result.modality == "shape_diagram"
    assert "crop_figures_from_screenshot" in result.processing_hints


def test_enrich_deck_with_modalities_writes_page_fields_and_report():
    deck = Deck(
        source_path="lecture.pdf",
        source_type="pdf",
        pages=[
            SlidePage(slide_id=1, text_blocks=[TextBlock(id="s1_t1", type="paragraph", content="A" * 200)]),
            SlidePage(slide_id=2, page_screenshot="screenshots/slide2.png"),
        ],
    )

    report = enrich_deck_with_modalities(deck)

    assert deck.pages[0].page_modality == "native_text"
    assert deck.pages[1].page_modality == "image_only"
    assert report["summary"]["pages_total"] == 2
    assert report["summary"]["ocr_recommended_pages"] == 1
