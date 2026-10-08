"""Verify the reading layout of generated SlideNote notes (ROADMAP P0-2).

Checks three layers for one or more SlideNote output directories and reports
every issue with the sample, file and page it belongs to:

- Markdown (notes.md): heading hierarchy jumps, duplicate H1, empty sections,
  images without alt/nearby explanation, oversized tables, unbalanced code
  fences or math delimiters, missing source markers.
- Word (notes.docx, built by pandoc): heading/table/image counts against the
  Markdown source, and per-table column consistency (misaligned rows cause
  broken tables in Word).
- PDF (notes.pdf, built from docx via LibreOffice): clipped or overflowing
  text blocks, image/text overlap (misplacement), empty pages and headings
  stranded at the bottom of a page (orphan headings), per page.

Missing export files are reported as ``skipped`` with the reason, so the tool
works on runs that only requested markdown-zip and on machines without
pandoc/LibreOffice.

Usage:
    python scripts/verify_note_layout.py outputs/lecture [--json report.json]
    python scripts/verify_note_layout.py benchmarks/runs/<run>/outputs/*

Exit codes: 0 = no errors; 1 = at least one error (or warning with --strict).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pymupdf

WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
HEADING_STYLES = {"Heading1", "Heading2", "Heading3", "Heading4", "heading 1", "heading 2", "heading 3", "heading 4"}
MAX_TABLE_COLUMNS = 10
MAX_TABLE_CELL_CHARS = 120
IMAGE_EXPLANATION_MIN_CHARS = 30
PAGE_MARGIN_TOLERANCE = 2.0
IMAGE_TEXT_OVERLAP_LIMIT = 0.30


def _visible(text: str) -> str:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    return text.strip()


def _mask_fenced_code(text: str) -> tuple[str, bool]:
    """Hide code syntax from layout checks while preserving source offsets."""
    masked_lines: list[str] = []
    fence_marker = ""
    fence_length = 0
    for line in text.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line.rstrip("\r\n"))
        if fence_marker:
            if (
                marker
                and marker.group(1)[0] == fence_marker
                and len(marker.group(1)) >= fence_length
                and not marker.group(2).strip()
            ):
                fence_marker = ""
            masked_lines.append(re.sub(r"[^\r\n]", " ", line))
        elif marker and (marker.group(1)[0] == "~" or "`" not in marker.group(2)):
            fence_marker = marker.group(1)[0]
            fence_length = len(marker.group(1))
            masked_lines.append(re.sub(r"[^\r\n]", " ", line))
        else:
            masked_lines.append(line)
    return "".join(masked_lines), bool(fence_marker)


def check_markdown(notes_path: Path) -> tuple[list[dict], dict]:
    issues: list[dict] = []
    text = notes_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    layout_text, unclosed_fence = _mask_fenced_code(text)
    layout_lines = layout_text.splitlines()
    sample = notes_path.parent.name
    stats = {"headings": 0, "h1": 0, "images": 0, "tables": 0, "chars": len(_visible(text))}

    h1_count = sum(1 for line in layout_lines if re.match(r"^#\s+\S", line))
    stats["h1"] = h1_count
    if h1_count == 0:
        issues.append(issue("error", sample, "notes.md", None, "missing_h1", "文档缺少一个 H1 标题。"))
    elif h1_count > 1:
        issues.append(issue("error", sample, "notes.md", None, "multiple_h1", f"发现 {h1_count} 个 H1，应只有一个课程标题。"))

    headings = [(index, len(match.group(1)), match.group(2).strip()) for index, line in enumerate(layout_lines) if (match := re.match(r"^(#{1,6})\s+(.*)$", line))]
    heading_lines = {index for index, _level, _title in headings}
    stats["headings"] = len(headings)
    previous_level = 0
    for index, level, title in headings:
        if previous_level and level > previous_level + 1:
            issues.append(issue(
                "error", sample, "notes.md", None, "heading_level_jump",
                f"第 {index + 1} 行标题“{title}”从 H{previous_level} 直接跳到 H{level}。",
            ))
        previous_level = level

    for pos, (index, level, title) in enumerate(headings):
        if level == 1:
            # The document H1 is normally followed directly by section headings.
            continue
        # A parent section may legitimately start with a child heading. Treat
        # the whole nested section as its body until the next heading at the
        # same or a higher level, and ignore heading text itself.
        end = len(lines)
        for next_index, next_level, _next_title in headings[pos + 1:]:
            if next_level <= level:
                end = next_index
                break
        # Code examples are section content, even when their lines resemble
        # headings. Remove only the real Markdown headings identified above.
        section_text = "\n".join(lines[line_index] for line_index in range(index + 1, end) if line_index not in heading_lines)
        body = _visible(section_text)
        if not body:
            issues.append(issue(
                "warning", sample, "notes.md", None, "empty_section",
                f"标题“{title}”（第 {index + 1} 行）下没有正文内容。",
            ))

    if unclosed_fence:
        issues.append(issue("error", sample, "notes.md", None, "unbalanced_code_fence", "存在未闭合的代码围栏；结束围栏须使用相同字符且长度不少于开始围栏。"))
    if layout_text.count("$$") % 2:
        issues.append(issue("error", sample, "notes.md", None, "unbalanced_math", "$$ 定界符数量为奇数，公式块可能未闭合。"))

    image_positions = [match for match in re.finditer(r"(?m)^!\[([^\]]*)\]\(([^)]+)\)", layout_text)]
    stats["images"] = len(image_positions)
    for match in image_positions:
        alt, target = match.group(1).strip(), match.group(2).strip()
        line_no = layout_text.count("\n", 0, match.start()) + 1
        if not alt:
            issues.append(issue("error", sample, "notes.md", None, "image_missing_alt", f"第 {line_no} 行图片缺少 alt 文本：{target}"))
        window = _visible(layout_text[match.end(): match.end() + 600])
        before = _visible(layout_text[max(0, match.start() - 600): match.start()])
        if len(window) < IMAGE_EXPLANATION_MIN_CHARS and len(before) < IMAGE_EXPLANATION_MIN_CHARS:
            issues.append(issue(
                "warning", sample, "notes.md", None, "image_without_explanation",
                f"第 {line_no} 行图片“{alt or target}”前后 600 字符内没有讲解文字。",
            ))

    table_rows: list[list[list[str]]] = []
    table_starts: list[int] = []
    previous_table_row: int | None = None
    for index, line in enumerate(layout_lines):
        if re.match(r"^\s*\|.*\|\s*$", line):
            if previous_table_row is None or index != previous_table_row + 1:
                table_starts.append(index)
                table_rows.append([])
            table_rows[-1].append([cell.strip() for cell in line.strip().strip("|").split("|")])
            previous_table_row = index
    stats["tables"] = len(table_rows)
    for table_index, rows in enumerate(table_starts and table_rows or []):
        if not rows:
            continue
        columns = len(rows[0])
        line_no = table_starts[table_index] + 1
        if columns > MAX_TABLE_COLUMNS:
            issues.append(issue(
                "warning", sample, "notes.md", None, "wide_table",
                f"第 {line_no} 行表格有 {columns} 列（>{MAX_TABLE_COLUMNS}），导出后大概率横向溢出。",
            ))
        for row_index, row in enumerate(rows):
            row_line_no = table_starts[table_index] + row_index + 1
            if len(row) != columns:
                issues.append(issue(
                    "error", sample, "notes.md", None, "ragged_table_row",
                    f"第 {row_line_no} 行表格行列数不一致（{len(row)} != {columns}）。",
                ))
                break
            for cell in row:
                if len(cell) > MAX_TABLE_CELL_CHARS:
                    issues.append(issue(
                        "warning", sample, "notes.md", None, "oversized_table_cell",
                        f"第 {line_no} 行表格单元格超过 {MAX_TABLE_CELL_CHARS} 字符，导出后可能断页错位。",
                    ))
                    break

    if "slidenote-source:" not in text and stats["chars"] > 800:
        issues.append(issue(
            "warning", sample, "notes.md", None, "no_source_markers",
            "笔记没有可追溯来源标记；如为 lecture 产物请检查 source_display 配置。",
        ))
    return issues, stats


def check_docx(docx_path: Path, markdown_stats: dict | None) -> list[dict]:
    issues: list[dict] = []
    sample = docx_path.parent.name
    try:
        with zipfile.ZipFile(docx_path) as archive:
            document = archive.read("word/document.xml")
            media = [name for name in archive.namelist() if name.startswith("word/media/")]
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        return [issue("error", sample, "notes.docx", None, "docx_unreadable", f"无法读取 docx：{type(exc).__name__}: {exc}")]

    root = ET.fromstring(document)
    body = root.find(f"{WORD_NS}body")
    headings = tables = 0
    for element in body.iter():
        if element.tag == f"{WORD_NS}p":
            style = element.find(f"{WORD_NS}pPr/{WORD_NS}pStyle")
            if style is not None and style.get(f"{WORD_NS}val") in HEADING_STYLES:
                headings += 1
        elif element.tag == f"{WORD_NS}tbl":
            tables += 1
    if markdown_stats and markdown_stats.get("headings") and headings == 0:
        issues.append(issue("error", sample, "notes.docx", None, "docx_no_headings", "Word 文档没有任何标题样式，标题层级在导出中丢失。"))
    if markdown_stats and markdown_stats.get("tables") and tables < markdown_stats["tables"]:
        issues.append(issue(
            "warning", sample, "notes.docx", None, "docx_table_count_mismatch",
            f"Word 表格数（{tables}）少于 Markdown（{markdown_stats['tables']}），可能有表格未导出。",
        ))
    if markdown_stats and markdown_stats.get("images") and len(media) < markdown_stats["images"]:
        issues.append(issue(
            "warning", sample, "notes.docx", None, "docx_image_count_mismatch",
            f"Word 内嵌图片数（{len(media)}）少于 Markdown（{markdown_stats['images']}），可能有图片丢失。",
        ))

    for table_index, table in enumerate([element for element in body.iter(f"{WORD_NS}tbl")]):
        rows = table.findall(f"{WORD_NS}tr")
        widths = []
        for row in rows:
            cells = row.findall(f"{WORD_NS}tc")
            widths.append(len(cells))
        if widths and (min(widths) != max(widths)):
            issues.append(issue(
                "error", sample, "notes.docx", None, "docx_ragged_table",
                f"Word 第 {table_index + 1} 个表格行内单元格数不一致（{sorted(set(widths))}），渲染会错位。",
            ))
    return issues


def _rects_overlap_area(a: pymupdf.Rect, b: pymupdf.Rect) -> float:
    inter = a & b
    if inter.is_empty or inter.width <= 0 or inter.height <= 0:
        return 0.0
    smaller = min(a.get_area(), b.get_area())
    return (inter.get_area() / smaller) if smaller else 0.0


def check_pdf(pdf_path: Path) -> list[dict]:
    issues: list[dict] = []
    sample = pdf_path.parent.name
    try:
        doc = pymupdf.open(pdf_path)
    except Exception as exc:
        return [issue("error", sample, "notes.pdf", None, "pdf_unreadable", f"无法打开 PDF：{type(exc).__name__}: {exc}")]
    try:
        if len(doc) == 0:
            return [issue("error", sample, "notes.pdf", None, "pdf_empty", "PDF 没有任何页面。")]
        for page_index, page in enumerate(doc):
            page_no = page_index + 1
            page_rect = page.rect
            blocks = [block for block in page.get_text("dict")["blocks"]]
            text_blocks = [block for block in blocks if block["type"] == 0]
            image_blocks = [block for block in blocks if block["type"] == 1]
            if not text_blocks and not image_blocks:
                issues.append(issue("error", sample, "notes.pdf", page_no, "blank_page", "页面没有任何文本或图片。"))
                continue
            for block in text_blocks:
                bbox = pymupdf.Rect(block["bbox"])
                if (
                    bbox.x0 < page_rect.x0 - PAGE_MARGIN_TOLERANCE
                    or bbox.y0 < page_rect.y0 - PAGE_MARGIN_TOLERANCE
                    or bbox.x1 > page_rect.x1 + PAGE_MARGIN_TOLERANCE
                    or bbox.y1 > page_rect.y1 + PAGE_MARGIN_TOLERANCE
                ):
                    snippet = " ".join(span["text"] for line in block["lines"] for span in line["spans"])[:40]
                    issues.append(issue(
                        "error", sample, "notes.pdf", page_no, "text_overflow",
                        f"文本块超出页面边界（{bbox}）：“{snippet}…”",
                    ))
            for image in image_blocks:
                image_rect = pymupdf.Rect(image["bbox"])
                for block in text_blocks:
                    if _rects_overlap_area(image_rect, pymupdf.Rect(block["bbox"])) > IMAGE_TEXT_OVERLAP_LIMIT:
                        snippet = " ".join(span["text"] for line in block["lines"] for span in line["spans"])[:30]
                        issues.append(issue(
                            "warning", sample, "notes.pdf", page_no, "image_text_overlap",
                            f"图片与文本“{snippet}…”重叠超过 {IMAGE_TEXT_OVERLAP_LIMIT:.0%}，疑似图文错位。",
                        ))
                        break
            if text_blocks:
                last = text_blocks[-1]
                spans = [span for line in last["lines"] for span in line["spans"]]
                if spans:
                    max_size = max(span["size"] for span in spans)
                    sizes = [span["size"] for block in text_blocks for line in block["lines"] for span in line["spans"]]
                    body_size = sorted(sizes)[len(sizes) // 2]
                    if max_size >= body_size * 1.35 and last["bbox"][3] > page_rect.y1 * 0.92:
                        text = " ".join(span["text"] for span in spans)[:30]
                        issues.append(issue(
                            "warning", sample, "notes.pdf", page_no, "orphan_heading",
                            f"页尾最后一段是大号标题“{text}…”，内容被分到下一页。",
                        ))
    finally:
        doc.close()
    return issues


def issue(level: str, sample: str, file: str, page: int | None, rule: str, detail: str) -> dict:
    return {"level": level, "sample": sample, "file": file, "page": page, "rule": rule, "detail": detail}


def verify_output_dir(output_dir: Path) -> list[dict]:
    issues: list[dict] = []
    notes = output_dir / "notes.md"
    if not notes.exists():
        return [issue("error", output_dir.name, "notes.md", None, "notes_missing", f"{output_dir} 缺少 notes.md，请先运行 build。")]
    markdown_issues, markdown_stats = check_markdown(notes)
    issues.extend(markdown_issues)

    docx = output_dir / "notes.docx"
    pdf = output_dir / "notes.pdf"
    if docx.exists():
        issues.extend(check_docx(docx, markdown_stats))
    else:
        issues.append(issue("skipped", output_dir.name, "notes.docx", None, "docx_missing", "未请求 docx 导出或 pandoc 缺失，Word 层检查跳过。"))
    if pdf.exists():
        issues.extend(check_pdf(pdf))
    else:
        reason = "未请求 pdf 导出；或 LibreOffice 缺失导致 docx→pdf 转换未生成。"
        issues.append(issue("skipped", output_dir.name, "notes.pdf", None, "pdf_missing", reason))
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("outputs", type=Path, nargs="+", help="SlideNote build 输出目录（可传多个或 runs/<run>/outputs 下的子目录）")
    parser.add_argument("--json", type=Path, default=None, help="同时把结构化报告写入该文件")
    parser.add_argument("--strict", action="store_true", help="warning 也视为失败")
    args = parser.parse_args()

    dirs: list[Path] = []
    for path in args.outputs:
        if path.is_dir() and (path / "notes.md").exists():
            dirs.append(path)
        elif path.is_dir():
            dirs.extend(child for child in sorted(path.iterdir()) if child.is_dir() and (child / "notes.md").exists())
    if not dirs:
        print("没有找到包含 notes.md 的输出目录。", file=sys.stderr)
        return 2

    all_issues: list[dict] = []
    for directory in dirs:
        all_issues.extend(verify_output_dir(directory))

    errors = [item for item in all_issues if item["level"] == "error"]
    warnings = [item for item in all_issues if item["level"] == "warning"]
    skipped = [item for item in all_issues if item["level"] == "skipped"]

    report = {"issues": all_issues, "summary": {"samples": len(dirs), "errors": len(errors), "warnings": len(warnings), "skipped": len(skipped)}}
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"检查样本：{len(dirs)}；错误 {len(errors)}；警告 {len(warnings)}；跳过 {len(skipped)}")
    for item in all_issues:
        if item["level"] == "skipped":
            continue
        where = f"{item['file']}" + (f" 第 {item['page']} 页" if item["page"] else "")
        print(f"[{item['level'].upper():7s}] {item['sample']} / {where} / {item['rule']}: {item['detail']}")
    for item in skipped:
        print(f"[skipped] {item['sample']} / {item['rule']}: {item['detail']}")

    failed = bool(errors) or (args.strict and bool(warnings))
    if failed:
        print("排版验收未通过。" + ("" if not args.strict else "（--strict：警告也算失败）"))
    else:
        print("排版验收通过（启发式检查；渲染结论仍建议人工翻阅一次导出文件）。")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
