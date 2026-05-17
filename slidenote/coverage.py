from __future__ import annotations

import re
from dataclasses import dataclass

from slidenote.models import Deck, SlidePage


@dataclass(slots=True)
class CoverageItem:
    id: str
    slide_id: int
    kind: str
    covered: bool
    preview: str


def analyze_coverage(deck: Deck, notes_markdown: str) -> dict[str, object]:
    items = _collect_items(deck, notes_markdown)
    total = len(items)
    covered = sum(1 for item in items if item.covered)
    missing = [item for item in items if not item.covered]
    page_coverage = _page_coverage(deck, items)
    return {
        "total": total,
        "covered": covered,
        "missing": len(missing),
        "coverage_ratio": covered / total if total else 1.0,
        "page_coverage": page_coverage,
        "items": [
            {
                "id": item.id,
                "slide_id": item.slide_id,
                "kind": item.kind,
                "covered": item.covered,
                "preview": item.preview,
            }
            for item in items
        ],
    }


def render_coverage_markdown(report: dict[str, object]) -> str:
    total = int(report["total"])
    covered = int(report["covered"])
    missing = int(report["missing"])
    ratio = float(report["coverage_ratio"])
    lines = [
        "# SlideNote 覆盖率报告",
        "",
        f"- 总元素数：{total}",
        f"- 已覆盖：{covered}",
        f"- 可能遗漏：{missing}",
        f"- 覆盖率：{ratio:.1%}",
    ]
    page_coverage = report.get("page_coverage")
    if isinstance(page_coverage, dict):
        missing_slide_ids = page_coverage.get("missing_slide_ids") or []
        missing_slide_text = ", ".join(str(slide_id) for slide_id in missing_slide_ids) if missing_slide_ids else "无"
        lines.extend(
            [
                f"- 有内容页覆盖：{page_coverage.get('covered_pages', 0)} / {page_coverage.get('pages_with_expected_content', 0)}",
                f"- 完全未引用页：{missing_slide_text}",
            ]
        )
    lines.extend(
        [
            "",
            "| 状态 | 页码 | 类型 | 元素 ID | 内容预览 |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for item in report["items"]:
        status = "已覆盖" if item["covered"] else "可能遗漏"
        lines.append(
            f"| {status} | {item['slide_id']} | {item['kind']} | `{item['id']}` | {_escape_table(str(item['preview']))} |"
        )
    return "\n".join(lines).rstrip() + "\n"


def _collect_items(deck: Deck, notes_markdown: str) -> list[CoverageItem]:
    items: list[CoverageItem] = []
    tokens = set(re.findall(r"\bs\d+_(?:t|tbl|img|fig)\d+\b", notes_markdown))
    for page in deck.pages:
        items.extend(_page_items(page, tokens))
    return items


def _page_coverage(deck: Deck, items: list[CoverageItem]) -> dict[str, object]:
    items_by_slide: dict[int, list[CoverageItem]] = {}
    for item in items:
        items_by_slide.setdefault(item.slide_id, []).append(item)
    pages: list[dict[str, object]] = []
    missing_slide_ids: list[int] = []
    covered_pages = 0
    pages_with_expected_content = 0
    for page in deck.pages:
        page_items = items_by_slide.get(page.slide_id, [])
        expected = bool(page_items)
        covered_count = sum(1 for item in page_items if item.covered)
        if expected:
            pages_with_expected_content += 1
            if covered_count > 0:
                covered_pages += 1
            else:
                missing_slide_ids.append(page.slide_id)
        pages.append(
            {
                "slide_id": page.slide_id,
                "title": page.title,
                "expected_items": len(page_items),
                "covered_items": covered_count,
                "missing_items": len(page_items) - covered_count,
                "covered": covered_count > 0 if expected else True,
            }
        )
    return {
        "pages_total": len(deck.pages),
        "pages_with_expected_content": pages_with_expected_content,
        "covered_pages": covered_pages,
        "missing_pages": len(missing_slide_ids),
        "missing_slide_ids": missing_slide_ids,
        "pages": pages,
    }


def _page_items(page: SlidePage, tokens: set[str]) -> list[CoverageItem]:
    items: list[CoverageItem] = []
    for block in page.text_blocks:
        items.append(
            CoverageItem(
                id=block.id,
                slide_id=page.slide_id,
                kind=f"text:{block.type}",
                covered=block.id in tokens,
                preview=_preview(block.content),
            )
        )
    for table in page.tables:
        preview = " / ".join(" | ".join(row) for row in table.rows[:2])
        items.append(
            CoverageItem(
                id=table.id,
                slide_id=page.slide_id,
                kind="table",
                covered=table.id in tokens,
                preview=_preview(preview),
            )
        )
    for image in page.images:
        if image.ignored:
            continue
        items.append(
            CoverageItem(
                id=image.id,
                slide_id=page.slide_id,
                kind="image",
                covered=image.id in tokens,
                preview=image.caption or image.path,
            )
        )
    return items


def _preview(text: str, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _escape_table(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")
