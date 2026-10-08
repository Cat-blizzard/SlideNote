import pytest

from scripts.verify_note_layout import check_markdown


def _check(tmp_path, text):
    notes = tmp_path / "notes.md"
    notes.write_text(text, encoding="utf-8")
    return check_markdown(notes)


def test_contiguous_five_row_table_is_counted_once(tmp_path):
    issues, stats = _check(
        tmp_path,
        "# Course\n\n"
        "| Name | Value |\n"
        "| --- | --- |\n"
        "| A | 1 |\n"
        "| B | 2 |\n"
        "| C | 3 |\n",
    )

    assert stats["tables"] == 1
    assert not any(item["rule"] == "ragged_table_row" for item in issues)


def test_missing_column_in_third_table_row_is_reported(tmp_path):
    issues, stats = _check(
        tmp_path,
        "# Course\n\n"
        "| Name | Value |\n"
        "| --- | --- |\n"
        "| A |\n"
        "| B | 2 |\n",
    )

    assert stats["tables"] == 1
    ragged = [item for item in issues if item["rule"] == "ragged_table_row"]
    assert len(ragged) == 1
    assert "5" in ragged[0]["detail"]
    assert "1 != 2" in ragged[0]["detail"]


@pytest.mark.parametrize("separator", ["\n", "\nSeparate discussion.\n\n"])
def test_separated_tables_have_independent_column_counts(tmp_path, separator):
    issues, stats = _check(
        tmp_path,
        "# Course\n\n"
        "| Name | Value |\n"
        "| --- | --- |\n"
        "| A | 1 |\n"
        + separator
        + "| Name | Value | Units |\n"
        "| --- | --- | --- |\n"
        "| B | 2 | kg |\n",
    )

    assert stats["tables"] == 2
    assert not any(item["rule"] == "ragged_table_row" for item in issues)


@pytest.mark.parametrize(
    ("opening", "closing"),
    [("```markdown", "```"), ("````markdown", "````"), ("~~~markdown", "~~~~")],
)
def test_code_examples_do_not_create_layout_errors(tmp_path, opening, closing):
    issues, stats = _check(
        tmp_path,
        "# Course\n\n## Example\n\n"
        + opening
        + "\n# Example heading\n#### Example jump\n"
        "![](not-an-image.png)\n"
        "| Name | Value |\n| --- | --- |\n| Incomplete |\n"
        "$$\n"
        + closing
        + "\n\n## Explanation\n\nThe syntax above is a code example.\n",
    )

    assert stats["h1"] == 1
    assert stats["headings"] == 3
    assert stats["images"] == 0
    assert stats["tables"] == 0
    assert issues == []


def test_shorter_fence_inside_long_fence_does_not_close_code(tmp_path):
    issues, stats = _check(
        tmp_path,
        "# Course\n\n## Example\n\n````markdown\n"
        "```python\n# This heading belongs to the example\n```\n"
        "![](not-an-image.png)\n$$\n````\n",
    )

    assert stats["headings"] == 2
    assert stats["images"] == 0
    assert issues == []


@pytest.mark.parametrize(
    ("opening", "invalid_closing"),
    [("~~~", "```"), ("````", "```"), ("```", "```with-info")],
)
def test_invalid_closing_fence_remains_unclosed(tmp_path, opening, invalid_closing):
    issues, stats = _check(
        tmp_path,
        "# Course\n\n## Example\n\n"
        + opening
        + "\ncode\n"
        + invalid_closing
        + "\n# Still code\n![](not-an-image.png)\n$$\n",
    )

    assert stats["h1"] == 1
    assert stats["images"] == 0
    assert {item["rule"] for item in issues} == {"unbalanced_code_fence"}


def test_real_errors_after_code_keep_original_line_numbers(tmp_path):
    issues, stats = _check(
        tmp_path,
        "# Course\n```markdown\n![](example.png)\n$$\n```\n![](real.png)\n$$\n",
    )

    assert stats["images"] == 1
    image_issue = next(item for item in issues if item["rule"] == "image_missing_alt")
    assert "6" in image_issue["detail"]
    assert any(item["rule"] == "unbalanced_math" for item in issues)
