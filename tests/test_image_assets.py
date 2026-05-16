from slidenote.image_assets import classify_image_asset
from slidenote.extractors.pdf import _is_page_like_bbox
from slidenote.extractors.pptx import _is_page_like_shape


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
