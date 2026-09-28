import json

from slidenote.build.artifacts import remove_stale_build_artifacts


def test_stale_artifacts_are_removed_but_cache_overrides_and_input_are_kept(tmp_path):
    (tmp_path / "run_summary.json").write_text(
        json.dumps({"schema_version": 1, "source_path": "lecture.pdf", "source_type": "pdf", "artifacts": {"content": "content.json"}}),
        encoding="utf-8",
    )
    (tmp_path / "vision_usage.json").write_text("{}", encoding="utf-8")
    (tmp_path / "notes.assets").mkdir()
    (tmp_path / "notes.assets" / "old.png").write_bytes(b"x")
    (tmp_path / ".cache").mkdir()
    (tmp_path / "page_modalities.overrides.json").write_text("{}", encoding="utf-8")
    (tmp_path / "my_notes.txt").write_text("keep", encoding="utf-8")
    images = tmp_path / "images"
    images.mkdir()
    lecture = images / "lecture.pdf"
    lecture.write_bytes(b"%PDF")

    removed = remove_stale_build_artifacts(tmp_path, keep=(lecture,))

    assert {"run_summary.json", "vision_usage.json", "notes.assets"} <= set(removed)
    assert not (tmp_path / "vision_usage.json").exists()
    assert (tmp_path / ".cache").is_dir()
    assert (tmp_path / "page_modalities.overrides.json").exists()
    assert (tmp_path / "my_notes.txt").exists()
    assert lecture.exists()


def test_unrelated_output_directory_is_never_cleaned(tmp_path):
    (tmp_path / "progress.json").write_text('{"status": "failed"}', encoding="utf-8")
    (tmp_path / "run_summary.json").write_text("{}", encoding="utf-8")
    (tmp_path / "notes.md").write_text("my own notes", encoding="utf-8")
    (tmp_path / "images").mkdir()
    (tmp_path / "images" / "photo.png").write_bytes(b"x")

    assert remove_stale_build_artifacts(tmp_path) == []
    assert (tmp_path / "notes.md").read_text(encoding="utf-8") == "my own notes"
    assert (tmp_path / "images" / "photo.png").exists()


def test_partial_build_content_marker_is_cleaned(tmp_path):
    (tmp_path / "content.json").write_text(
        json.dumps({"source_path": "lecture.pdf", "source_type": "pdf", "pages": [{"slide_id": 1}]}),
        encoding="utf-8",
    )
    (tmp_path / "vision_usage.json").write_text("{}", encoding="utf-8")

    removed = remove_stale_build_artifacts(tmp_path)

    assert set(removed) == {"content.json", "vision_usage.json"}
