from __future__ import annotations

import ast
import hashlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gui.studio_core import (
    StudioConfig,
    TextbookConfig,
    build_env,
    build_slidenote_command,
    build_study_pack_command,
    build_textbook_command,
    command_for_display,
    discover_outputs,
    discover_textbook_outputs,
    performance_tips,
    progress_percent,
    safe_run_name,
)


def test_command_builds_gui_options_and_redacts_keys(tmp_path: Path):
    cfg = StudioConfig(
        input_path=tmp_path / "a.pdf",
        output_dir=tmp_path / "out",
        progress_json=tmp_path / "out" / "progress.json",
        preset="lecture",
        provider="openai",
        api_key="sk-secret",
        vision="auto",
        vision_provider="qwen",
        vision_api_key="qwen-secret",
        ocr="auto",
        ocr_api_key="ocr-secret",
        ocr_secret_key="ocr-secret-2",
    )
    cmd = build_slidenote_command(cfg)
    assert cmd[:3] == [sys.executable, "-m", "slidenote"]
    assert cmd[cmd.index("--preset") + 1] == "lecture"
    assert cmd[cmd.index("--provider") + 1] == "openai"
    assert cmd[cmd.index("--vision") + 1] == "auto"
    assert "--use-llm" not in cmd
    assert "--concurrency" not in cmd
    assert "--review-mode" not in cmd
    assert "--exam-mode" not in cmd
    assert "--api-key" not in cmd
    assert "--vision-api-key" not in cmd
    assert "--ocr-api-key" not in cmd
    assert "--ocr-secret-key" not in cmd
    display = command_for_display(cmd)
    assert "sk-secret" not in display
    assert "qwen-secret" not in display
    assert "ocr-secret-2" not in display
    env = build_env({}, cfg)
    assert env["OPENAI_API_KEY"] == "sk-secret"
    assert env["QWEN_API_KEY"] == "qwen-secret"
    assert env["BAIDU_OCR_API_KEY"] == "ocr-secret"
    assert env["BAIDU_OCR_SECRET_KEY"] == "ocr-secret-2"


def test_study_pack_command_is_separate_from_build(tmp_path: Path):
    cfg = StudioConfig(
        input_path=tmp_path / "a.pdf",
        output_dir=tmp_path / "out",
        progress_json=tmp_path / "out" / "progress.json",
        provider="deepseek",
        api_key="deep-key",
    )

    build_cmd = build_slidenote_command(cfg)
    study_cmd = build_study_pack_command(cfg.output_dir, question_count=18)
    env = build_env({}, cfg)

    assert "study-pack" not in build_cmd
    assert study_cmd[:4] == [sys.executable, "-m", "slidenote", "study-pack"]
    assert study_cmd[study_cmd.index("--question-count") + 1] == "18"
    assert env["DEEPSEEK_API_KEY"] == "deep-key"


def test_textbook_command_uses_env_for_ocr_keys(tmp_path: Path):
    cfg = TextbookConfig(
        input_path=tmp_path / "book.pdf",
        output_dir=tmp_path / "textbook",
        ocr="auto",
        ocr_api_key="ocr-key",
        ocr_secret_key="ocr-secret",
    )

    cmd = build_textbook_command(cfg)
    env = build_env({}, cfg)
    display = command_for_display(cmd)

    assert cmd[:4] == [sys.executable, "-m", "slidenote", "textbook-index"]
    assert cmd[cmd.index("--ocr") + 1] == "auto"
    assert "--ocr-api-key" not in cmd
    assert "--ocr-secret-key" not in cmd
    assert "ocr-key" not in display
    assert env["BAIDU_OCR_API_KEY"] == "ocr-key"
    assert env["BAIDU_OCR_SECRET_KEY"] == "ocr-secret"


def test_command_display_redacts_secret_flags_defensively():
    display = command_for_display(
        ["slidenote", "build", "lecture.pdf", "--api-key", "text-secret", "--vision-api-key", "vision-secret"]
    )

    assert display == "slidenote build lecture.pdf --api-key *** --vision-api-key ***"


def test_discover_outputs_includes_markdown_zip_and_exports(tmp_path: Path):
    for filename in ("notes.zip", "notes.toc.md", "notes.docx", "notes.pdf", "notes.tex"):
        (tmp_path / filename).write_bytes(b"x")

    outputs = discover_outputs(tmp_path)

    assert outputs["notes_zip"] == tmp_path / "notes.zip"
    assert outputs["notes_toc"] == tmp_path / "notes.toc.md"
    assert outputs["docx"] == tmp_path / "notes.docx"
    assert outputs["pdf"] == tmp_path / "notes.pdf"
    assert outputs["latex"] == tmp_path / "notes.tex"


def test_discover_textbook_outputs(tmp_path: Path):
    for filename in ("textbook_manifest.json", "textbook_chunks.jsonl", "textbook_report.md"):
        (tmp_path / filename).write_text("{}", encoding="utf-8")

    outputs = discover_textbook_outputs(tmp_path)

    assert outputs["manifest"] == tmp_path / "textbook_manifest.json"
    assert outputs["chunks"] == tmp_path / "textbook_chunks.jsonl"
    assert outputs["report"] == tmp_path / "textbook_report.md"


def test_local_preset_does_not_require_api_env(tmp_path: Path):
    cfg = StudioConfig(
        input_path=tmp_path / "a.pdf",
        output_dir=tmp_path / "out",
        progress_json=tmp_path / "out" / "progress.json",
        preset="local",
        provider="deepseek",
        api_key="deep-key",
        vision="auto",
        vision_provider="qwen",
        vision_api_key="qwen-secret",
    )

    cmd = build_slidenote_command(cfg)
    env = build_env({}, cfg)

    assert cmd[cmd.index("--preset") + 1] == "local"
    assert "DEEPSEEK_API_KEY" not in env
    assert "QWEN_API_KEY" not in env


def test_env_and_speed_tips(tmp_path: Path):
    cfg = StudioConfig(
        input_path=tmp_path / "a.pdf",
        output_dir=tmp_path / "out",
        progress_json=tmp_path / "out" / "progress.json",
        preset="lecture",
        provider="deepseek",
        api_key="deep-key",
        vision="off",
    )
    env = build_env({}, cfg)
    assert env["DEEPSEEK_API_KEY"] == "deep-key"
    tips = " ".join(performance_tips(cfg))
    assert "Lecture preset" in tips
    assert "Vision is off" in tips
    assert safe_run_name("我的 课件!!.pdf")


def test_progress_percent_uses_planned_stage_count():
    progress = {
        "status": "running",
        "planned_stages": ["parse", "understand", "notes", "export"],
        "stages": [{"stage": "parse"}],
    }
    assert progress_percent(progress) == pytest.approx(0.25)


def test_gui_api_status_accepts_provider_alias_env(monkeypatch):
    pytest.importorskip("streamlit")
    from gui.app import _api_status

    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-key")

    assert _api_status(True, "", "qwen") == ("Ready", "DASHSCOPE_API_KEY", "good")


def test_gui_first_run_surface_has_two_presets_and_markdown_zip_default():
    pytest.importorskip("streamlit")
    from gui.app import PRESETS, _selected_export_formats

    assert list(PRESETS) == ["Lecture quality", "Local preview"]
    assert PRESETS["Lecture quality"] == {"preset": "lecture", "vision": "auto"}
    assert PRESETS["Local preview"] == {"preset": "local", "vision": "off"}
    assert _selected_export_formats(True, False, False, False, False) == ["markdown-zip"]


def test_gui_workbench_surface_replaces_hero_cards():
    source = (Path(__file__).resolve().parents[1] / "gui" / "app.py").read_text(encoding="utf-8")

    assert "def _render_hero" not in source
    assert "hero-card" not in source
    assert "Run settings" not in source
    assert "Notes workspace" in source
    assert "Textbook library" in source
    assert "Usage & diagnostics" in source
    assert "_render_empty_upload_panel" in source


def test_gui_app_invokes_main_when_streamlit_executes_the_script():
    source = (Path(__file__).resolve().parents[1] / "gui" / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    final_statement = tree.body[-1]

    assert isinstance(final_statement, ast.If)
    assert isinstance(final_statement.test, ast.Compare)
    assert ast.unparse(final_statement.test) == "__name__ == '__main__'"
    assert any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "main"
        for node in ast.walk(final_statement)
    )


def test_gui_workbench_file_size_helper():
    pytest.importorskip("streamlit")
    from gui.app import _format_file_size

    assert _format_file_size(512) == "512 B"
    assert _format_file_size(1536) == "1.5 KB"
    assert _format_file_size(None) == "unknown size"


def test_gui_carries_modality_corrections_for_identical_source_only(tmp_path: Path):
    pytest.importorskip("streamlit")
    from gui.app import _carry_modality_overrides

    previous_source = tmp_path / "previous.pdf"
    previous_source.write_bytes(b"same input bytes")
    current_source = tmp_path / "current.pdf"
    current_source.write_bytes(previous_source.read_bytes())
    changed_source = tmp_path / "changed.pdf"
    changed_source.write_bytes(b"same input byteX")
    previous_output = tmp_path / "previous-output"
    previous_output.mkdir()
    (previous_output / "content.json").write_text(json.dumps({"source_path": str(previous_source)}), encoding="utf-8")
    manifest = {"schema_version": 1, "pages": {"1": {"modality": "image_only", "note": "scan"}}}
    (previous_output / "page_modalities.overrides.json").write_text(json.dumps(manifest), encoding="utf-8")

    matching_output = tmp_path / "matching-output"
    matching_output.mkdir()
    assert _carry_modality_overrides(previous_output, current_source, matching_output)
    assert json.loads((matching_output / "page_modalities.overrides.json").read_text(encoding="utf-8")) == manifest

    changed_output = tmp_path / "changed-output"
    changed_output.mkdir()
    assert not _carry_modality_overrides(previous_output, changed_source, changed_output)
    assert not (changed_output / "page_modalities.overrides.json").exists()


def test_gui_carries_hashed_corrections_after_original_upload_is_removed(tmp_path: Path):
    pytest.importorskip("streamlit")
    from gui.app import _carry_modality_overrides

    current_source = tmp_path / "current.pdf"
    current_source.write_bytes(b"same input bytes")
    previous_output = tmp_path / "previous-output"
    previous_output.mkdir()
    manifest = {
        "schema_version": 1,
        "source_sha256": hashlib.sha256(current_source.read_bytes()).hexdigest(),
        "pages": {"1": {"modality": "image_only"}},
    }
    (previous_output / "page_modalities.overrides.json").write_text(json.dumps(manifest), encoding="utf-8")
    next_output = tmp_path / "next-output"
    next_output.mkdir()

    assert _carry_modality_overrides(previous_output, current_source, next_output)
    assert json.loads((next_output / "page_modalities.overrides.json").read_text(encoding="utf-8")) == manifest


def test_gui_preserves_stale_corrections_in_reused_output_dir(tmp_path: Path):
    pytest.importorskip("streamlit")
    from gui.app import _carry_modality_overrides

    old_source = tmp_path / "old.pdf"
    old_source.write_bytes(b"old")
    new_source = tmp_path / "new.pdf"
    new_source.write_bytes(b"new")
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / "content.json").write_text(json.dumps({"source_path": str(old_source)}), encoding="utf-8")
    manifest_path = output_dir / "page_modalities.overrides.json"
    manifest_path.write_text('{"pages":{"1":{"modality":"image_only"}}}', encoding="utf-8")

    assert not _carry_modality_overrides(None, new_source, output_dir)
    assert not manifest_path.exists()
    backups = list(output_dir.glob("page_modalities.overrides.stale-*.json"))
    assert len(backups) == 1
    assert 'image_only' in backups[0].read_text(encoding="utf-8")


def test_gui_saved_correction_records_source_hash(tmp_path: Path):
    pytest.importorskip("streamlit")
    from gui.app import _save_modality_override

    source = tmp_path / "source.pdf"
    source.write_bytes(b"source bytes")
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / "content.json").write_text(json.dumps({"source_path": str(source)}), encoding="utf-8")

    _save_modality_override(output_dir, 2, "image_only", "scan")
    manifest = json.loads((output_dir / "page_modalities.overrides.json").read_text(encoding="utf-8"))
    assert manifest["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert manifest["pages"]["2"]["modality"] == "image_only"


def test_gui_quiet_build_polls_progress_before_stdout(tmp_path: Path, monkeypatch):
    pytest.importorskip("streamlit")
    import gui.app as app

    output_dir = tmp_path / "output"
    output_dir.mkdir()
    marker = tmp_path / "finished.txt"
    script = tmp_path / "quiet_build.py"
    script.write_text(
        "import json, sys, time\n"
        "from pathlib import Path\n"
        "progress = Path(sys.argv[1])\n"
        "marker = Path(sys.argv[2])\n"
        "progress.write_text(json.dumps({'status': 'running', 'message': 'working'}), encoding='utf-8')\n"
        "time.sleep(1.0)\n"
        "marker.write_text('done', encoding='utf-8')\n"
        "print('finished', flush=True)\n",
        encoding="utf-8",
    )
    config = StudioConfig(
        input_path=tmp_path / "source.pdf",
        output_dir=output_dir,
        progress_json=output_dir / "progress.json",
        preset="local",
    )
    monkeypatch.setattr(app, "build_slidenote_command", lambda cfg: [sys.executable, str(script), str(cfg.progress_json), str(marker)])
    monkeypatch.setattr(app, "_generate_cost_report", lambda _: None)
    fake_st = MagicMock()
    slots = [MagicMock() for _ in range(3)]
    fake_st.empty.side_effect = slots
    monkeypatch.setattr(app, "st", fake_st)
    original_update = app._update_progress_ui
    observed: list[tuple[dict | None, bool]] = []

    def record_update(progress_path, progress_bar, status_box, stage_box):
        original_update(progress_path, progress_bar, status_box, stage_box)
        observed.append((app._read_json(progress_path), marker.exists()))

    monkeypatch.setattr(app, "_update_progress_ui", record_update)
    app._run_build(config)

    assert sum(bool(progress and progress.get("status") == "running" and not finished) for progress, finished in observed) >= 2
    assert "finished" in slots[2].code.call_args.args[0]
    fake_st.success.assert_called_once()
    fake_st.error.assert_not_called()
