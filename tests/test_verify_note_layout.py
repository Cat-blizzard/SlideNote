import zipfile

import pymupdf

from scripts.verify_note_layout import check_docx, check_markdown, check_pdf


def test_markdown_layout_checker_flags_broken_structure(tmp_path):
    notes = tmp_path / "notes.md"
    notes.write_text(
        "# One\n\n# Two\n\n### Jumped\n\n![](image.png)\n\n```python\nprint('open')\n",
        encoding="utf-8",
    )

    issues, _stats = check_markdown(notes)
    rules = {item["rule"] for item in issues}

    assert "multiple_h1" in rules
    assert "heading_level_jump" in rules
    assert "image_missing_alt" in rules
    assert "unbalanced_code_fence" in rules


def test_parent_heading_with_populated_subsection_is_not_empty(tmp_path):
    notes = tmp_path / "notes.md"
    notes.write_text(
        "# Course\n\n## Topic\n\n### Concept\n\nA complete explanation lives in the subsection.\n",
        encoding="utf-8",
    )

    issues, _stats = check_markdown(notes)

    assert not any(item["rule"] == "empty_section" for item in issues)


def test_docx_layout_checker_flags_missing_heading_styles(tmp_path):
    docx = tmp_path / "notes.docx"
    document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body><w:p><w:r><w:t>Plain text only</w:t></w:r></w:p></w:body>
    </w:document>"""
    with zipfile.ZipFile(docx, "w") as archive:
        archive.writestr("word/document.xml", document_xml)

    issues = check_docx(docx, {"headings": 1, "tables": 0, "images": 0})

    assert any(item["rule"] == "docx_no_headings" for item in issues)


def test_pdf_layout_checker_flags_blank_page(tmp_path):
    pdf = tmp_path / "notes.pdf"
    document = pymupdf.open()
    document.new_page()
    document.save(pdf)
    document.close()

    issues = check_pdf(pdf)

    assert any(item["rule"] == "blank_page" for item in issues)
