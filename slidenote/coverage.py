from __future__ import annotations

import re
from dataclasses import dataclass

from slidenote.figure_grounding import note_candidate_images
from slidenote.models import Deck, ImageAsset, SlidePage


@dataclass(slots=True)
class CoverageItem:
    id: str
    slide_id: int
    kind: str
    trace_covered: bool
    visible_covered: bool
    preview: str
    structural: bool = False

    @property
    def covered(self) -> bool:
        return self.trace_covered

    @property
    def marker_only(self) -> bool:
        return self.trace_covered and not self.visible_covered


def analyze_coverage(deck: Deck, notes_markdown: str) -> dict[str, object]:
    items = _collect_items(deck, notes_markdown)
    trace_coverage = _coverage_totals(deck, items, coverage_attr="trace_covered")
    visible_coverage = _coverage_totals(deck, items, coverage_attr="visible_covered", exclude_structural=True)
    marker_only_items = [item for item in items if item.marker_only and not item.structural]
    structural_marker_only_items = [item for item in items if item.marker_only and item.structural]
    figure_coverage = _figure_coverage(deck, notes_markdown)
    return {
        "total": trace_coverage["total"],
        "covered": trace_coverage["covered"],
        "missing": trace_coverage["missing"],
        "coverage_ratio": trace_coverage["coverage_ratio"],
        "page_coverage": trace_coverage["page_coverage"],
        "trace_coverage": trace_coverage,
        "visible_coverage": visible_coverage,
        "marker_only": len(marker_only_items),
        "marker_only_items": [_item_record(item) for item in marker_only_items],
        "structural_marker_only": len(structural_marker_only_items),
        "structural_marker_only_items": [_item_record(item) for item in structural_marker_only_items],
        "figure_coverage": figure_coverage,
        "items": [_item_record(item) for item in items],
    }


def render_coverage_markdown(report: dict[str, object]) -> str:
    total = int(report["total"])
    covered = int(report["covered"])
    missing = int(report["missing"])
    ratio = float(report["coverage_ratio"])
    visible_coverage = report.get("visible_coverage") if isinstance(report.get("visible_coverage"), dict) else {}
    visible_total = int(visible_coverage.get("total", 0)) if isinstance(visible_coverage, dict) else 0
    visible_covered = int(visible_coverage.get("covered", 0)) if isinstance(visible_coverage, dict) else 0
    visible_missing = int(visible_coverage.get("missing", 0)) if isinstance(visible_coverage, dict) else 0
    visible_ratio = float(visible_coverage.get("coverage_ratio", 1.0)) if isinstance(visible_coverage, dict) else 1.0
    marker_only = int(report.get("marker_only", 0))
    structural_marker_only = int(report.get("structural_marker_only", 0))
    lines = [
        "# SlideNote 覆盖率报告",
        "",
        f"- 总元素数：{total}",
        f"- 溯源覆盖：{covered} / {total}（{ratio:.1%}）",
        f"- 溯源可能遗漏：{missing}",
        f"- 正文解释覆盖：{visible_covered} / {visible_total}（{visible_ratio:.1%}）",
        f"- 正文未显式解释：{visible_missing}",
        f"- 仅溯源标记覆盖：{marker_only}",
        f"- 结构页仅溯源标记覆盖：{structural_marker_only}",
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
    figure_coverage = report.get("figure_coverage")
    if isinstance(figure_coverage, dict):
        lines.extend(
            [
                f"- 重要图片进入笔记：{figure_coverage.get('covered_figures', 0)} / {figure_coverage.get('total_figures', 0)}",
                f"- 已锚定图片：{figure_coverage.get('anchored_figures', 0)}",
                f"- 已解释图片：{figure_coverage.get('explained_figures', 0)}",
                f"- 需要人工复查图片：{figure_coverage.get('needs_review', 0)}",
            ]
        )
    lines.extend(
        [
            "",
            "| 溯源 | 正文 | 范围 | 页码 | 类型 | 元素 ID | 内容预览 |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for item in report["items"]:
        trace_status = "已覆盖" if item["trace_covered"] else "可能遗漏"
        if item.get("structural"):
            visible_status = "免查"
            scope = "结构页"
        else:
            visible_status = "已解释" if item["visible_covered"] else "未显式"
            scope = "内容页"
        lines.append(
            f"| {trace_status} | {visible_status} | {scope} | {item['slide_id']} | {item['kind']} | "
            f"`{item['id']}` | {_escape_table(str(item['preview']))} |"
        )
    if isinstance(figure_coverage, dict) and figure_coverage.get("figures"):
        lines.extend(
            [
                "",
                "## 图片覆盖",
                "",
                "| 状态 | 页码 | 图片 ID | 锚点 | 解释状态 | 复查 |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for figure in figure_coverage["figures"]:
            status = "已插入" if figure.get("covered") else "未插入"
            anchors = ", ".join(figure.get("anchor_element_ids") or []) or "无"
            review = figure.get("figure_audit_status") or "ok"
            lines.append(
                f"| {status} | {figure.get('slide_id')} | `{figure.get('id')}` | {_escape_table(anchors)} | "
                f"{_escape_table(str(figure.get('figure_explanation_status') or 'missing'))} | {_escape_table(str(review))} |"
            )
    return "\n".join(lines).rstrip() + "\n"


def _collect_items(deck: Deck, notes_markdown: str) -> list[CoverageItem]:
    items: list[CoverageItem] = []
    trace_tokens = _source_tokens(notes_markdown)
    visible_tokens = _visible_source_tokens(notes_markdown)
    structural_slide_ids = _structural_slide_ids(deck)
    for page in deck.pages:
        items.extend(
            _page_items(
                page,
                trace_tokens=trace_tokens,
                visible_tokens=visible_tokens,
                structural=page.slide_id in structural_slide_ids,
            )
        )
    return items


def _coverage_totals(
    deck: Deck,
    items: list[CoverageItem],
    coverage_attr: str,
    exclude_structural: bool = False,
) -> dict[str, object]:
    scoped_items = [item for item in items if not (exclude_structural and item.structural)]
    total = len(scoped_items)
    covered = sum(1 for item in scoped_items if bool(getattr(item, coverage_attr)))
    missing = [item for item in scoped_items if not bool(getattr(item, coverage_attr))]
    return {
        "total": total,
        "covered": covered,
        "missing": len(missing),
        "coverage_ratio": covered / total if total else 1.0,
        "page_coverage": _page_coverage(deck, scoped_items, coverage_attr=coverage_attr),
    }


def _page_coverage(deck: Deck, items: list[CoverageItem], coverage_attr: str = "trace_covered") -> dict[str, object]:
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
        covered_count = sum(1 for item in page_items if bool(getattr(item, coverage_attr)))
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


def _page_items(
    page: SlidePage,
    trace_tokens: set[str],
    visible_tokens: set[str],
    structural: bool = False,
) -> list[CoverageItem]:
    items: list[CoverageItem] = []
    for block in page.text_blocks:
        items.append(
            CoverageItem(
                id=block.id,
                slide_id=page.slide_id,
                kind=f"text:{block.type}",
                trace_covered=block.id in trace_tokens,
                visible_covered=block.id in visible_tokens,
                preview=_preview(block.content),
                structural=structural,
            )
        )
    for table in page.tables:
        preview = " / ".join(" | ".join(row) for row in table.rows[:2])
        items.append(
            CoverageItem(
                id=table.id,
                slide_id=page.slide_id,
                kind="table",
                trace_covered=table.id in trace_tokens,
                visible_covered=table.id in visible_tokens,
                preview=_preview(preview),
                structural=structural,
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
                trace_covered=image.id in trace_tokens,
                visible_covered=image.id in visible_tokens,
                preview=image.caption or image.path,
                structural=structural,
            )
        )
    return items


def _item_record(item: CoverageItem) -> dict[str, object]:
    return {
        "id": item.id,
        "slide_id": item.slide_id,
        "kind": item.kind,
        "covered": item.covered,
        "trace_covered": item.trace_covered,
        "visible_covered": item.visible_covered,
        "marker_only": item.marker_only,
        "structural": item.structural,
        "preview": item.preview,
    }


def _source_tokens(markdown: str) -> set[str]:
    return set(re.findall(r"\bs\d+_(?:t|tbl|img|fig)\d+\b", markdown))


def _visible_source_tokens(markdown: str) -> set[str]:
    tokens: set[str] = set()
    for block in _markdown_blocks(markdown):
        block_tokens = _source_tokens(block)
        if block_tokens and _block_has_visible_explanation(block):
            tokens.update(block_tokens)
    return tokens


def _markdown_blocks(markdown: str) -> list[str]:
    return [block.strip() for block in re.split(r"\n\s*\n", markdown) if block.strip()]


def _block_has_visible_explanation(block: str) -> bool:
    text = _strip_html_comments(block)
    text = re.sub(r"!\[[^\]]*]\([^)]+\)", " image ", text)
    text = re.sub(r"\u3010[^\u3011]*?PPT[^\u3011]*?\u3011", " ", text)
    text = re.sub(r"\uff08\s*PPT\s*\u7b2c\s*\d+\s*\u9875\s*\uff09", " ", text)
    text = re.sub(r"\bs\d+_(?:t|tbl|img|fig)\d+\b", " ", text)
    text = re.sub(r"\bp\d+:", " ", text)
    text = re.sub(r"[#>*_\-\s`~|:：,，.。;；、()（）\[\]【】]+", "", text)
    return bool(text)


def _strip_html_comments(markdown: str) -> str:
    return re.sub(r"<!--.*?-->", "", markdown, flags=re.DOTALL)


def _structural_slide_ids(deck: Deck) -> set[int]:
    return {
        page.slide_id
        for index, page in enumerate(deck.pages)
        if _looks_like_structural_page(page, index)
    }


def _looks_like_structural_page(page: SlidePage, index: int) -> bool:
    title = page.title or ""
    text = "\n".join([title, *(block.content for block in page.text_blocks)])
    normalized_title = _normalize_text_key(title)
    normalized_text = _normalize_text_key(text)
    if _has_structural_title(normalized_title):
        return True
    if index == 0 and any(marker in normalized_text for marker in _cover_markers()):
        return True
    if _has_standalone_structural_label(text):
        return True
    return _looks_like_outline_page(text)


def _has_structural_title(normalized_title: str) -> bool:
    exact_titles = {
        "\u76ee\u5f55",
        "\u8bfe\u7a0b\u76ee\u5f55",
        "\u672c\u7ae0\u76ee\u5f55",
        "\u7ae0\u8282\u5bfc\u822a",
        "contents",
        "outline",
        "agenda",
    }
    return normalized_title in exact_titles


def _has_standalone_structural_label(text: str) -> bool:
    labels = {"\u76ee\u5f55", "\u8bfe\u7a0b\u76ee\u5f55", "\u672c\u7ae0\u76ee\u5f55", "\u7ae0\u8282\u5bfc\u822a", "contents", "outline"}
    for line in text.splitlines()[:4]:
        normalized = _normalize_text_key(line)
        if normalized in labels:
            return True
    return False


def _looks_like_outline_page(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    numbered = [
        line
        for line in lines
        if re.match(r"^\s*(?:\d+|[\u4e00\u4e8c\u4e09\u56db\u4e94])\s*[.\u3001\uff0e]", line)
    ]
    return len(numbered) >= 3 and sum(len(line) for line in numbered) <= 260


def _cover_markers() -> set[str]:
    return {
        "\u8bb2\u5e08",
        "\u6559\u5e08",
        "\u6559\u6388",
        "\u8054\u7cfb\u90ae\u7bb1",
        "\u90ae\u7bb1",
        "\u4e3b\u9875",
        "email",
        "homepage",
        "http",
        "www",
    }


def _normalize_text_key(value: str) -> str:
    return re.sub(r"[\s:\uff1a,\uff0c.\u3002;\uff1b\u3001\-_\uff08\uff09()<>]+", "", value).lower()


def _figure_coverage(deck: Deck, notes_markdown: str) -> dict[str, object]:
    image_targets = [target.strip().strip("<>") for target in re.findall(r"!\[[^\]]*]\(([^)]+)\)", notes_markdown)]
    figures: list[dict[str, object]] = []
    for page in deck.pages:
        for image in note_candidate_images(page):
            covered = any(target == image.path or target.endswith(image.path) for target in image_targets)
            figures.append(_figure_record(page, image, covered))
    return {
        "total_figures": len(figures),
        "covered_figures": sum(1 for figure in figures if figure["covered"]),
        "missing_figures": sum(1 for figure in figures if not figure["covered"]),
        "anchored_figures": sum(1 for figure in figures if figure["anchor_element_ids"]),
        "explained_figures": sum(1 for figure in figures if figure["figure_explanation_status"] not in {None, "missing"}),
        "needs_review": sum(1 for figure in figures if figure["figure_audit_status"] == "needs_review"),
        "figures": figures,
    }


def _figure_record(page: SlidePage, image: ImageAsset, covered: bool) -> dict[str, object]:
    return {
        "id": image.id,
        "slide_id": page.slide_id,
        "path": image.path,
        "role": image.role,
        "covered": covered,
        "anchor_element_ids": list(image.anchor_element_ids),
        "anchor_reason": image.anchor_reason,
        "grounding_confidence": image.grounding_confidence,
        "figure_explanation_status": image.figure_explanation_status,
        "figure_audit_status": image.figure_audit_status,
        "importance_score": image.importance_score,
        "importance_rank": image.importance_rank,
    }


def _preview(text: str, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _escape_table(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")
