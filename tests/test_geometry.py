from slidenote.geometry import asset_source_bbox, normalize_asset_bbox, normalize_bbox
from slidenote.models import ImageAsset, SlidePage
from slidenote.utils import file_sha256


def test_normalize_bbox_handles_pptx_xywh_and_pdf_xyxy():
    assert normalize_bbox("pptx", [100, 50, 200, 100], 1000, 500) == [0.1, 0.1, 0.3, 0.3]
    assert normalize_bbox("pdf", [100, 50, 200, 100], 1000, 500) == [0.1, 0.1, 0.2, 0.2]
    assert normalize_bbox("pdf", [0.2, 0.1, 0.4, 0.3], None, None) == [0.2, 0.1, 0.4, 0.3]
    assert normalize_bbox("pdf", [100, 50, 200, 100], None, 500) is None


def test_screenshot_crop_pixel_bbox_is_never_treated_as_page_coordinates():
    page = SlidePage(slide_id=1, page_width=720, page_height=540)
    crop = ImageAsset(
        id="s1_fig1",
        path="figures/crop.png",
        bbox=[288.0, 216.0, 1152.0, 864.0],
        crop_source_path="screenshots/slide-1.png",
        crop_bbox=[0.2, 0.2, 0.8, 0.8],
    )
    assert normalize_asset_bbox("pdf", page, crop) == [0.2, 0.2, 0.8, 0.8]

    crop.crop_bbox = None
    assert asset_source_bbox(crop) is None


def test_file_sha256_hashes_file_bytes(tmp_path):
    path = tmp_path / "data.bin"
    path.write_bytes(b"abc")
    assert file_sha256(path) == "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
