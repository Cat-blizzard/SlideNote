import hashlib
import json

from slidenote.figures import select_figure_targets
from slidenote.modality import apply_modality_overrides, classify_page_modality, enrich_deck_with_modalities
from slidenote.models import Deck, ImageAsset, SlidePage, TableBlock, TextBlock
from slidenote.ocr import select_ocr_targets
from slidenote.vision import select_vision_targets


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


def test_manual_image_only_override_updates_report_and_visual_routing(tmp_path):
    original_source = tmp_path / "first-upload.pdf"
    new_source = tmp_path / "second-upload.pdf"
    original_source.write_bytes(b"same deck bytes")
    new_source.write_bytes(original_source.read_bytes())
    page = SlidePage(
        slide_id=1,
        text_blocks=[TextBlock(id="s1_t1", type="paragraph", content="A" * 1000)],
        page_screenshot="screenshots/slide1.png",
    )
    deck = Deck(source_path=str(new_source), source_type="pdf", pages=[page])
    report = enrich_deck_with_modalities(deck)
    assert page.page_modality == "native_text"

    manifest_path = tmp_path / "page_modalities.overrides.json"
    manifest_path.write_text(json.dumps({
        "schema_version": 1,
        "source_path": str(original_source),
        "source_sha256": hashlib.sha256(original_source.read_bytes()).hexdigest(),
        "pages": {"1": {"modality": "image_only", "note": "Scanned screenshot", "updated_at": "2026-09-23T10:00:00Z"}},
    }), encoding="utf-8")

    updated = apply_modality_overrides(deck, report, manifest_path)

    assert updated is report
    assert page.page_modality == "image_only"
    assert page.modality_reasons == ["manual_override"]
    assert {"ocr_page_screenshot", "vision_page_screenshot", "crop_figures_from_screenshot"} <= set(page.processing_hints)
    assert report["summary"]["modalities"] == {"image_only": 1}
    assert report["summary"]["ocr_recommended_pages"] == 1
    assert report["summary"]["override_pages"] == 1
    assert report["pages"][0]["classifier_modality"] == "native_text"
    assert report["pages"][0]["manual_override"]["note"] == "Scanned screenshot"
    assert [target.slide_id for target in select_ocr_targets(deck, tmp_path, mode="auto")] == [1]
    assert [target.slide_id for target in select_vision_targets(deck, tmp_path, mode="auto")] == [1]
    assert [target.slide_id for target in select_figure_targets(deck)] == [1]


def test_manual_native_text_override_suppresses_auto_visual_routing(tmp_path):
    page = SlidePage(slide_id=1, page_screenshot="screenshots/slide1.png")
    deck = Deck(source_path=str(tmp_path / "lecture.pdf"), source_type="pdf", pages=[page])
    report = enrich_deck_with_modalities(deck)
    assert page.page_modality == "image_only"
    manifest_path = tmp_path / "page_modalities.overrides.json"
    manifest_path.write_text(json.dumps({
        "schema_version": 1,
        "pages": {"1": {"modality": "native_text", "note": "Reviewed page"}},
    }), encoding="utf-8")

    apply_modality_overrides(deck, report, manifest_path)

    assert page.page_modality == "native_text"
    assert select_ocr_targets(deck, tmp_path, mode="auto") == []
    assert select_vision_targets(deck, tmp_path, mode="auto") == []
    assert select_figure_targets(deck) == []
    assert report["pages"][0]["classifier_modality"] == "image_only"
    assert report["pages"][0]["manual_override"]["note"] == "Reviewed page"


def test_manual_image_only_override_uses_embedded_image_when_no_screenshot(tmp_path):
    page = SlidePage(
        slide_id=1,
        text_blocks=[TextBlock(id="s1_t1", type="paragraph", content="A" * 1000)],
        images=[ImageAsset(id="s1_img1", path="images/diagram.png")],
    )
    deck = Deck(source_path=str(tmp_path / "lecture.pptx"), source_type="pptx", pages=[page])
    report = enrich_deck_with_modalities(deck)
    manifest_path = tmp_path / "page_modalities.overrides.json"
    manifest_path.write_text(json.dumps({
        "schema_version": 1,
        "pages": {"1": {"modality": "image_only"}},
    }), encoding="utf-8")

    apply_modality_overrides(deck, report, manifest_path)

    assert "ocr_page_screenshot" in page.processing_hints
    assert [target.image_id for target in select_ocr_targets(deck, tmp_path, mode="auto")] == ["s1_img1"]
    assert [target.slide_id for target in select_vision_targets(deck, tmp_path, mode="auto")] == [1]


def test_manual_unknown_preserves_auto_visual_routing(tmp_path):
    page = SlidePage(slide_id=1, page_screenshot="screenshots/slide1.png")
    deck = Deck(source_path=str(tmp_path / "lecture.pdf"), source_type="pdf", pages=[page])
    report = enrich_deck_with_modalities(deck)
    original_hints = list(page.processing_hints)
    manifest_path = tmp_path / "page_modalities.overrides.json"
    manifest_path.write_text(json.dumps({
        "schema_version": 1,
        "pages": {"1": {"modality": "unknown"}},
    }), encoding="utf-8")

    apply_modality_overrides(deck, report, manifest_path)

    assert page.page_modality == "unknown"
    assert page.modality_confidence == 0.0
    assert page.processing_hints == original_hints
    assert [target.slide_id for target in select_ocr_targets(deck, tmp_path, mode="auto")] == [1]
    assert [target.slide_id for target in select_vision_targets(deck, tmp_path, mode="auto")] == [1]
    assert [target.slide_id for target in select_figure_targets(deck)] == [1]


def test_manual_override_skips_invalid_pages_and_modalities(tmp_path):
    deck = Deck(source_path="lecture.pdf", source_type="pdf", pages=[SlidePage(slide_id=1, page_screenshot="s1.png")])
    report = enrich_deck_with_modalities(deck)
    manifest_path = tmp_path / "page_modalities.overrides.json"
    manifest_path.write_text(json.dumps({
        "schema_version": 1,
        "pages": {
            "0": {"modality": "decorative"},
            "2": {"modality": "decorative"},
            "1": {"modality": []},
        },
    }), encoding="utf-8")

    apply_modality_overrides(deck, report, manifest_path)

    assert deck.pages[0].page_modality == "image_only"
    assert report["summary"]["override_pages"] == 0
    assert len(report["overrides"]["warnings"]) == 3


def test_manual_override_rejects_mismatched_source_hash(tmp_path):
    source = tmp_path / "deck.pdf"
    source.write_bytes(b"current deck")
    deck = Deck(source_path=str(source), source_type="pdf", pages=[SlidePage(slide_id=1)])
    report = enrich_deck_with_modalities(deck)
    manifest_path = tmp_path / "page_modalities.overrides.json"
    manifest_path.write_text(json.dumps({
        "schema_version": 1,
        "source_sha256": hashlib.sha256(b"different deck").hexdigest(),
        "pages": {"1": {"modality": "image_only"}},
    }), encoding="utf-8")

    apply_modality_overrides(deck, report, manifest_path)

    assert deck.pages[0].page_modality == "decorative"
    assert report["summary"]["override_pages"] == 0
    assert "does not match" in report["overrides"]["warnings"][0]
