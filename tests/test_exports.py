import subprocess
import zipfile

from slidenote.exporting import add_table_of_contents, build_export_artifacts, clean_markdown_for_export, parse_export_formats


def test_parse_export_formats_expands_all_and_deduplicates():
    assert parse_export_formats("markdown-zip,markdown-toc,docx,docx") == ["markdown-zip", "markdown-toc", "docx"]
    assert parse_export_formats("all") == ["markdown-zip", "markdown-toc", "docx", "pdf", "latex"]


def test_markdown_toc_inserts_after_h1_with_stable_slugs():
    markdown = """# Lecture

Intro paragraph.

## TCP Basics

### 拥塞控制

## TCP Basics
"""

    result = add_table_of_contents(markdown)

    assert result.startswith("# Lecture\n\n## 目录\n\n- [TCP Basics](#tcp-basics)\n  - [拥塞控制](#拥塞控制)\n- [TCP Basics](#tcp-basics-2)")
    assert result.index("## 目录") < result.index("Intro paragraph.")


def test_markdown_toc_ignores_hidden_source_markers():
    markdown = """# Lecture <!-- slidenote-source: p1:s1_t1 -->

## Definition <!-- slidenote-source: p2:s2_t1 -->

Consistency keeps reads predictable. <!-- slidenote-source: p2:s2_t1 -->
"""

    result = add_table_of_contents(markdown)

    assert "<!--" not in result
    assert "- [Definition](#definition)" in result
    assert "s2_t1" not in result


def test_clean_markdown_for_export_removes_hidden_markers():
    result = clean_markdown_for_export("Text <!-- slidenote-source: p1:s1_t1 -->\n")

    assert result == "Text\n"


def test_markdown_toc_export_does_not_need_pandoc(tmp_path, monkeypatch):
    monkeypatch.setattr("slidenote.exporting.shutil.which", lambda name: None)

    report = build_export_artifacts("# Lecture\n\n## Topic\n", tmp_path, ["markdown-toc"])

    assert report["summary"]["succeeded"] == 1
    assert (tmp_path / "notes.toc.md").exists()
    assert not (tmp_path / "notes.docx").exists()


def test_markdown_zip_export_packages_notes_and_assets_without_pandoc(tmp_path, monkeypatch):
    monkeypatch.setattr("slidenote.exporting.shutil.which", lambda name: None)
    assets = tmp_path / "notes.assets" / "images"
    assets.mkdir(parents=True)
    (assets / "diagram.png").write_bytes(b"image")
    (tmp_path / "notes.md").write_text("# Lecture\n\n![Diagram](notes.assets/images/diagram.png)\n", encoding="utf-8")

    report = build_export_artifacts("# Lecture\n\n![Diagram](notes.assets/images/diagram.png)\n", tmp_path, ["markdown-zip"])

    assert report["summary"]["succeeded"] == 1
    assert report["results"][0]["format"] == "markdown-zip"
    assert report["results"][0]["asset_files"] == 1
    assert "Markdown notes are inside notes.zip" in report["messages"][0]
    assert report["warnings"] == []
    with zipfile.ZipFile(tmp_path / "notes.zip") as archive:
        assert set(archive.namelist()) == {"notes.md", "notes.assets/images/diagram.png", "README.txt"}


def test_pandoc_exports_record_success_and_pdf_uses_libreoffice(tmp_path, monkeypatch):
    commands = []

    def fake_which(name):
        return {"pandoc": "pandoc", "soffice": "soffice"}.get(name)

    def fake_run(command, cwd=None, text=None, stdout=None, stderr=None, check=None):
        commands.append(command)
        if command[0] == "pandoc":
            output = tmp_path / command[command.index("-o") + 1]
            output.write_bytes(b"exported")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[0] == "soffice":
            (tmp_path / "notes.pdf").write_bytes(b"pdf")
            return subprocess.CompletedProcess(command, 0, stdout="convert ok", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("slidenote.exporting.shutil.which", fake_which)
    monkeypatch.setattr("slidenote.exporting.subprocess.run", fake_run)

    report = build_export_artifacts("# Lecture\n\n## Topic\n", tmp_path, ["docx", "pdf", "latex"])

    assert report["summary"]["succeeded"] == 3
    assert {result["path"] for result in report["results"]} == {"notes.docx", "notes.pdf", "notes.tex"}
    assert (tmp_path / "notes.docx").exists()
    assert (tmp_path / "notes.pdf").exists()
    assert (tmp_path / "notes.tex").exists()
    assert any(command[0] == "soffice" and "--convert-to" in command for command in commands)
    latex_command = next(command for command in commands if command[0] == "pandoc" and command[command.index("-o") + 1] == "notes.tex")
    assert "markdown-implicit_figures" in latex_command
    assert "documentclass=ctexart" in latex_command


def test_pdf_export_requires_libreoffice_even_when_docx_can_be_built(tmp_path, monkeypatch):
    def fake_which(name):
        return "pandoc" if name == "pandoc" else None

    def fake_run(command, cwd=None, text=None, stdout=None, stderr=None, check=None):
        output = tmp_path / command[command.index("-o") + 1]
        output.write_bytes(b"docx")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("slidenote.exporting.shutil.which", fake_which)
    monkeypatch.setattr("slidenote.exporting.subprocess.run", fake_run)

    report = build_export_artifacts("# Lecture\n\n## Topic\n", tmp_path, ["pdf"])

    assert report["summary"]["failed"] == 1
    assert report["summary"]["blocking_failures"] == 1
    assert report["results"][0]["format"] == "pdf"
    assert report["results"][0]["reason"] == "libreoffice_not_found"
    assert (tmp_path / "notes.docx").exists()
    assert not (tmp_path / "notes.pdf").exists()


def test_pandoc_missing_marks_requested_formats_failed(tmp_path, monkeypatch):
    monkeypatch.setattr("slidenote.exporting.shutil.which", lambda name: None)

    report = build_export_artifacts("# Lecture\n\n## Topic\n", tmp_path, ["docx", "pdf"])

    assert report["summary"]["failed"] == 2
    assert report["summary"]["blocking_failures"] == 2
    assert all(result["reason"] == "pandoc_not_found" for result in report["results"])
