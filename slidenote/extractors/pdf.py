from __future__ import annotations

import contextlib
import io
from pathlib import Path

from slidenote.image_assets import image_metadata, refine_image_role_for_placement
from slidenote.models import Deck, ImageAsset, SlidePage, TableBlock, TextBlock, normalize_rel_path
from slidenote.utils import unique_path


def extract_pdf(input_path: Path, output_root: Path) -> Deck:
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("PyMuPDF is required for PDF extraction. Install with `pip install PyMuPDF`.") from exc

    images_dir = output_root / "images"
    screenshots_dir = output_root / "screenshots"
    images_dir.mkdir(parents=True, exist_ok=True)
    screenshots_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(input_path)
    pages: list[SlidePage] = []
    warnings: list[str] = []

    for page_index, page in enumerate(doc, start=1):
        slide = SlidePage(slide_id=page_index)
        page_size = _page_size(page)
        if page_size:
            slide.page_width, slide.page_height = page_size
        text_blocks = _extract_text_blocks(page, page_index)
        slide.title = _guess_title(text_blocks)
        slide.text_blocks = text_blocks
        slide.tables = _extract_tables(page, page_index)
        slide.images = _extract_images(doc, page, page_index, images_dir, output_root)
        slide.page_screenshot = _render_page(page, page_index, screenshots_dir, output_root)
        if not text_blocks and not any(not image.ignored for image in slide.images):
            slide.warnings.append("No selectable text or embedded images detected. This page may need OCR.")
        pages.append(slide)

    doc.close()
    return Deck(source_path=str(input_path), source_type="pdf", pages=pages, warnings=warnings)


def _extract_text_blocks(page: object, page_index: int) -> list[TextBlock]:
    data = page.get_text("dict")
    blocks: list[TextBlock] = []
    text_count = 1
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        lines: list[str] = []
        for line in block.get("lines", []):
            spans = [span.get("text", "") for span in line.get("spans", [])]
            text = "".join(spans).strip()
            if text:
                lines.append(text)
        content = "\n".join(lines).strip()
        if not content:
            continue
        blocks.append(
            TextBlock(
                id=f"s{page_index}_t{text_count}",
                type=_classify_text(content),
                content=content,
                bbox=[float(x) for x in block.get("bbox", [])] or None,
            )
        )
        text_count += 1
    return blocks


def _extract_tables(page: object, page_index: int) -> list[TableBlock]:
    tables: list[TableBlock] = []
    finder = getattr(page, "find_tables", None)
    if finder is None:
        return tables
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            found = finder()
    except Exception:
        return tables
    for table_index, table in enumerate(getattr(found, "tables", []) or [], start=1):
        try:
            rows = table.extract()
        except Exception:
            continue
        cleaned = [["" if cell is None else str(cell).strip() for cell in row] for row in rows]
        if any(any(cell for cell in row) for row in cleaned):
            tables.append(TableBlock(id=f"s{page_index}_tbl{table_index}", rows=cleaned))
    return tables


def _extract_images(doc: object, page: object, page_index: int, images_dir: Path, output_root: Path) -> list[ImageAsset]:
    images: list[ImageAsset] = []
    seen: set[int] = set()
    for image_index, image in enumerate(page.get_images(full=True), start=1):
        xref = int(image[0])
        if xref in seen:
            continue
        seen.add(xref)
        extracted = doc.extract_image(xref)
        ext = extracted.get("ext", "png")
        image_path = unique_path(images_dir / f"slide{page_index}_img{image_index}.{ext}")
        image_path.write_bytes(extracted["image"])
        meta = image_metadata(image_path)
        bbox = _image_bbox(page, xref)
        page_size = _page_size(page)
        page_like = _is_page_like_bbox(bbox, page_size)
        role = "page_image" if page_like else meta["role"]
        ignored = True if page_like else meta["ignored"]
        ignore_reason = "full_page_image" if page_like else meta["ignore_reason"]
        role, ignored, ignore_reason = refine_image_role_for_placement(
            role,
            ignored,
            ignore_reason,
            _bbox_area_ratio_xyxy(bbox, page_size),
            _bbox_near_page_edge_xyxy(bbox, page_size),
        )
        images.append(
            ImageAsset(
                id=f"s{page_index}_img{image_index}",
                path=normalize_rel_path(image_path, output_root),
                caption=f"第 {page_index} 页嵌入图片 {image_index}",
                source_format=ext,
                bbox=bbox,
                width=meta["width"],
                height=meta["height"],
                file_size=meta["file_size"],
                role=role,
                ignored=ignored,
                ignore_reason=ignore_reason,
            )
        )
    return images


def _image_bbox(page: object, xref: int) -> list[float] | None:
    try:
        rects = page.get_image_rects(xref)
    except Exception:
        return None
    if not rects:
        return None
    rect = rects[0]
    return [float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)]


def _page_size(page: object) -> tuple[float, float] | None:
    try:
        return float(page.rect.width), float(page.rect.height)
    except Exception:
        return None


def _is_page_like_bbox(bbox: list[float] | None, page_size: tuple[float, float] | None) -> bool:
    if not bbox or not page_size:
        return False
    width, height = page_size
    if width <= 0 or height <= 0:
        return False
    x1, y1, x2, y2 = bbox
    area_ratio = max(0.0, x2 - x1) * max(0.0, y2 - y1) / (width * height)
    return area_ratio >= 0.85


def _bbox_area_ratio_xyxy(bbox: list[float] | None, page_size: tuple[float, float] | None) -> float | None:
    if not bbox or not page_size:
        return None
    width, height = page_size
    if width <= 0 or height <= 0:
        return None
    x1, y1, x2, y2 = bbox
    return max(0.0, x2 - x1) * max(0.0, y2 - y1) / (width * height)


def _bbox_near_page_edge_xyxy(bbox: list[float] | None, page_size: tuple[float, float] | None) -> bool:
    if not bbox or not page_size:
        return False
    width, height = page_size
    if width <= 0 or height <= 0:
        return False
    x1, y1, x2, y2 = bbox
    margin_x = width * 0.08
    margin_y = height * 0.08
    return x1 <= margin_x or y1 <= margin_y or x2 >= width - margin_x or y2 >= height - margin_y


def _render_page(page: object, page_index: int, screenshots_dir: Path, output_root: Path) -> str:
    try:
        import fitz

        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
    except Exception:
        pix = page.get_pixmap(alpha=False)
    screenshot_path = screenshots_dir / f"slide{page_index}.png"
    pix.save(screenshot_path)
    return normalize_rel_path(screenshot_path, output_root)


def _guess_title(blocks: list[TextBlock]) -> str | None:
    if not blocks:
        return None
    first = blocks[0].content.splitlines()[0].strip()
    return first[:140] if first else None


def _classify_text(content: str) -> str:
    stripped = content.strip()
    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    if len(lines) > 1 and all(line[:1] in {"-", "•", "*", "·"} for line in lines[: min(3, len(lines))]):
        return "bullet"
    if len(stripped) <= 120 and "\n" not in stripped:
        return "heading"
    return "paragraph"
