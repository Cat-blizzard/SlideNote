from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterable

from PIL import Image

from slidenote.models import Deck, SlidePage


def ensure_clean_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def slugify(value: str, fallback: str = "item") -> str:
    value = value.strip().lower()
    value = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-")
    return value or fallback


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    index = 2
    while True:
        candidate = parent / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def find_executable(names: Iterable[str]) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def run_command(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


# ---------------------------------------------------------------------------
# Shared helpers (consolidated from per-module duplicates)
# ---------------------------------------------------------------------------


def display_path(path: Path | None, output_root: Path | None) -> str | None:
    """Render a path relative to output_root, falling back to the absolute path."""
    if path is None:
        return None
    if output_root is None:
        return str(path)
    try:
        return path.resolve().relative_to(output_root.resolve()).as_posix()
    except ValueError:
        return str(path)


def as_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def sum_int(values: object) -> int:
    total = 0
    for value in values:
        if isinstance(value, int):
            total += value
    return total


def str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def preview(text: str | None, limit: int = 160) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def escape_md(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


def looks_like_outline_page(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    numbered = [
        line
        for line in lines
        if re.match(r"^\s*(?:\d+|[\u4e00\u4e8c\u4e09\u56db\u4e94])\s*[.\u3001\uff0e]", line)
    ]
    return len(numbered) >= 3 and sum(len(line) for line in numbered) <= 260


def looks_like_section_title_page(page: SlidePage) -> bool:
    content_blocks = [
        block
        for block in page.text_blocks
        if block.content.strip() and not re.fullmatch(r"\d+", block.content.strip())
    ]
    if not page.title or len(content_blocks) > 3:
        return False
    text_len = sum(len(block.content.strip()) for block in content_blocks)
    return text_len <= 120


def context_title(pages: list[SlidePage], index: int) -> str:
    for page in pages:
        if page.title:
            return page.title
    return f"第 {index} 节"


def parse_json_object(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def looks_normalized(bbox: list[float]) -> bool:
    return len(bbox) == 4 and all(-0.001 <= float(value) <= 1.001 for value in bbox)


def union_bbox(boxes: list[list[float]]) -> list[float]:
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def pixel_box(bbox: list[float], width: int, height: int) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = bbox
    return (
        max(0, min(width - 1, int(round(x1 * width)))),
        max(0, min(height - 1, int(round(y1 * height)))),
        max(1, min(width, int(round(x2 * width)))),
        max(1, min(height, int(round(y2 * height)))),
    )


def bbox_area(bbox: list[float]) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def file_sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes().hex().encode("utf-8")).hexdigest()


def cleanup_temp_image(path: Path) -> None:
    if path.parent != Path(tempfile.gettempdir()):
        return
    try:
        path.unlink()
    except OSError:
        pass


def first_env(names: tuple[str, ...]) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def image_area(path: Path) -> int | None:
    try:
        with Image.open(path) as image:
            return image.width * image.height
    except Exception:
        return None


def prepare_image_for_api(path: Path, max_edge: int) -> tuple[Path, dict[str, Any]] | None:
    try:
        with Image.open(path) as image:
            original = {"width": image.width, "height": image.height, "mode": image.mode, "format": image.format}
            image = image.convert("RGB")
            scale = min(1.0, max_edge / max(image.width, image.height))
            if scale < 1.0:
                image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))))
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
            tmp_path = Path(tmp.name)
            tmp.close()
            image.save(tmp_path, format="JPEG", quality=85, optimize=True)
            meta = {
                "original": original,
                "prepared": {"width": image.width, "height": image.height, "mime_type": "image/jpeg", "bytes": tmp_path.stat().st_size},
            }
            return tmp_path, meta
    except Exception:
        return None


def round_score(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 3)


def page_by_id(deck: Deck, slide_id: int) -> SlidePage | None:
    return next((page for page in deck.pages if page.slide_id == slide_id), None)


def source_tokens(markdown: str) -> set[str]:
    return set(re.findall(r"\bs\d+_(?:t|tbl|img|fig)\d+\b", markdown))
