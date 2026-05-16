from slidenote.image_assets import classify_image_asset
from slidenote.image_ranking import rank_deck_images, sorted_images_by_importance
from slidenote.extractors.pdf import _is_page_like_bbox
from slidenote.extractors.pptx import _is_page_like_shape
from slidenote.models import Deck, ImageAsset, SlidePage


def test_classify_tiny_image_as_decorative():
    role, ignored, reason = classify_image_asset({"width": 60, "height": 24, "file_size": 135})

    assert role == "decorative"
    assert ignored is True
    assert reason in {"tiny_file", "tiny_area", "tiny_dimension"}


def test_classify_normal_image_as_content():
    role, ignored, reason = classify_image_asset({"width": 640, "height": 480, "file_size": 90_000})

    assert role == "content"
    assert ignored is False
    assert reason is None


def test_pdf_page_like_bbox_detection():
    assert _is_page_like_bbox([0, 0, 950, 950], (1000, 1000)) is True
    assert _is_page_like_bbox([100, 100, 500, 500], (1000, 1000)) is False


def test_pptx_page_like_shape_detection():
    assert _is_page_like_shape([0, 0, 950, 950], 1000, 1000) is True
    assert _is_page_like_shape([0, 0, 400, 400], 1000, 1000) is False


def test_image_importance_ranks_figure_crop_above_generic_image(tmp_path):
    deck = Deck(
        source_path="lecture.pdf",
        source_type="pdf",
        pages=[
            SlidePage(
                slide_id=1,
                page_modality="shape_diagram",
                images=[
                    ImageAsset(id="s1_img1", path="images/background.png", width=1400, height=900, role="page_image"),
                    ImageAsset(id="s1_fig1", path="figures/diagram.png", width=600, height=360, role="figure_crop", confidence=0.82),
                ],
            )
        ],
    )

    report = rank_deck_images(deck, tmp_path)

    assert report["summary"]["ranked_images"] == 2
    assert deck.pages[0].images[1].importance_rank == 1
    assert deck.pages[0].images[1].importance_score > deck.pages[0].images[0].importance_score
    assert sorted_images_by_importance(deck.pages[0].images)[0].id == "s1_fig1"
