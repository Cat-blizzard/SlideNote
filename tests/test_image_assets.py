from slidenote.image_assets import classify_image_asset


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
