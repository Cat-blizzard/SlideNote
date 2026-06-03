from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Callable, Protocol

from slidenote.models import Deck, ImageAsset, SlidePage, TableBlock, TextBlock
from slidenote.utils import find_executable


class ParserAdapter(Protocol):
    name: str
    supported_suffixes: tuple[str, ...]

    def supports(self, input_path: Path) -> bool:
        ...

    def extract(self, input_path: Path, output_root: Path) -> Deck:
        ...


CommandBuilder = Callable[[str, Path, Path], list[list[str]]]


@dataclass(frozen=True, slots=True)
class ParserAdapterInfo:
    name: str
    kind: str
    supported_suffixes: tuple[str, ...]
    description: str
    command_env: str | None = None
    executable_candidates: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BuiltInParserAdapter:
    name: str = "builtin"
    supported_suffixes: tuple[str, ...] = (".pptx", ".pdf", ".ppt")

    def supports(self, input_path: Path) -> bool:
        return input_path.suffix.lower() in self.supported_suffixes

    def extract(self, input_path: Path, output_root: Path) -> Deck:
        suffix = input_path.suffix.lower()
        if suffix == ".pptx":
            from slidenote.extractors.pptx import extract_pptx

            return extract_pptx(input_path, output_root)
        if suffix == ".pdf":
            from slidenote.extractors.pdf import extract_pdf

            return extract_pdf(input_path, output_root)
        if suffix == ".ppt":
            from slidenote.extractors.ppt import extract_ppt

            return extract_ppt(input_path, output_root)
        raise ValueError(f"Unsupported input format for builtin parser: {input_path.suffix}")


@dataclass(frozen=True, slots=True)
class ExternalCliParserAdapter:
    name: str
    supported_suffixes: tuple[str, ...]
    executable_candidates: tuple[str, ...]
    command_env: str
    command_builder: CommandBuilder
    description: str

    def supports(self, input_path: Path) -> bool:
        return input_path.suffix.lower() in self.supported_suffixes

    def extract(self, input_path: Path, output_root: Path) -> Deck:
        if not self.supports(input_path):
            raise ValueError(f"Parser adapter `{self.name}` does not support {input_path.suffix}.")

        work_dir = output_root / "_parser" / self.name
        work_dir.mkdir(parents=True, exist_ok=True)
        command_template = os.getenv(self.command_env)
        completed: list[subprocess.CompletedProcess[str]] = []
        errors: list[str] = []

        if command_template:
            result = _run_shell_template(command_template, input_path, work_dir)
            completed.append(result)
            if result.returncode != 0:
                raise RuntimeError(_external_parser_error(self.name, [result], [self.command_env]))
        else:
            executable = find_executable(self.executable_candidates)
            if executable is None:
                candidates = ", ".join(self.executable_candidates)
                raise RuntimeError(
                    f"Parser adapter `{self.name}` is not available. Install one of: {candidates}, "
                    f"or set {self.command_env} to a command template using {{input}} and {{out}}."
                )
            for command in self.command_builder(executable, input_path, work_dir):
                result = _run_command(command)
                completed.append(result)
                if result.returncode == 0:
                    break
                errors.append(" ".join(command))
            else:
                raise RuntimeError(_external_parser_error(self.name, completed, errors))

        deck = _load_external_deck(self.name, input_path, output_root, work_dir, completed)
        deck.warnings.append(f"Parsed with external parser adapter `{self.name}`.")
        return deck

    def info(self) -> ParserAdapterInfo:
        return ParserAdapterInfo(
            name=self.name,
            kind="external_cli",
            supported_suffixes=self.supported_suffixes,
            description=self.description,
            command_env=self.command_env,
            executable_candidates=self.executable_candidates,
        )


def extract_deck(input_path: Path, output_root: Path, parser: str = "auto") -> Deck:
    adapter = resolve_parser_adapter(parser, input_path)
    deck = adapter.extract(input_path, output_root)
    if not any(str(warning).startswith("parser_adapter:") for warning in deck.warnings):
        deck.warnings.append(f"parser_adapter:{adapter.name}")
    return deck


def resolve_parser_adapter(parser: str, input_path: Path) -> ParserAdapter:
    normalized = (parser or "auto").lower()
    adapters = parser_adapters()
    if normalized in {"auto", "builtin"}:
        builtin = adapters["builtin"]
        if builtin.supports(input_path):
            return builtin
        if normalized == "builtin":
            raise ValueError(f"Unsupported input format for builtin parser: {input_path.suffix}")
        for name in ("docling", "marker", "mineru"):
            adapter = adapters[name]
            if adapter.supports(input_path) and _external_adapter_available(adapter):
                return adapter
        raise ValueError(f"Unsupported input format: {input_path.suffix}")

    adapter = adapters.get(normalized)
    if adapter is None:
        choices = ", ".join(available_parser_choices())
        raise ValueError(f"Unknown parser adapter `{parser}`. Available: {choices}")
    if not adapter.supports(input_path):
        raise ValueError(f"Parser adapter `{adapter.name}` does not support {input_path.suffix}.")
    return adapter


def parser_adapters() -> dict[str, ParserAdapter]:
    return {
        "builtin": BuiltInParserAdapter(),
        "docling": ExternalCliParserAdapter(
            name="docling",
            supported_suffixes=(".pdf", ".pptx", ".ppt", ".docx", ".html"),
            executable_candidates=("docling",),
            command_env="SLIDENOTE_DOCLING_COMMAND",
            command_builder=_docling_commands,
            description="Docling CLI adapter; normalizes JSON/Markdown output into the SlideNote Deck model.",
        ),
        "marker": ExternalCliParserAdapter(
            name="marker",
            supported_suffixes=(".pdf",),
            executable_candidates=("marker_single", "marker"),
            command_env="SLIDENOTE_MARKER_COMMAND",
            command_builder=_marker_commands,
            description="Marker CLI adapter; normalizes JSON/Markdown output into the SlideNote Deck model.",
        ),
        "mineru": ExternalCliParserAdapter(
            name="mineru",
            supported_suffixes=(".pdf",),
            executable_candidates=("mineru", "magic-pdf"),
            command_env="SLIDENOTE_MINERU_COMMAND",
            command_builder=_mineru_commands,
            description="MinerU CLI adapter; normalizes JSON/Markdown output into the SlideNote Deck model.",
        ),
    }


def parser_adapter_infos() -> list[ParserAdapterInfo]:
    infos: list[ParserAdapterInfo] = []
    for adapter in parser_adapters().values():
        if isinstance(adapter, ExternalCliParserAdapter):
            infos.append(adapter.info())
        else:
            infos.append(
                ParserAdapterInfo(
                    name=adapter.name,
                    kind="builtin",
                    supported_suffixes=adapter.supported_suffixes,
                    description="Built-in PPTX/PPT/PDF parser that returns the native SlideNote Deck model.",
                )
            )
    return infos


def available_parser_choices() -> list[str]:
    return ["auto", *parser_adapters().keys()]


def _docling_commands(executable: str, input_path: Path, out_dir: Path) -> list[list[str]]:
    return [
        [executable, str(input_path), "--to", "json", "--output", str(out_dir)],
        [executable, str(input_path), "--format", "json", "--output", str(out_dir)],
        [executable, str(input_path), "--format", "json", "-o", str(out_dir)],
    ]


def _marker_commands(executable: str, input_path: Path, out_dir: Path) -> list[list[str]]:
    return [
        [executable, str(input_path), "--output_format", "json", "--output_dir", str(out_dir)],
        [executable, str(input_path), "--output_format", "markdown", "--output_dir", str(out_dir)],
    ]


def _mineru_commands(executable: str, input_path: Path, out_dir: Path) -> list[list[str]]:
    return [
        [executable, "-p", str(input_path), "-o", str(out_dir)],
        [executable, str(input_path), "-o", str(out_dir)],
    ]


def _run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _run_shell_template(command_template: str, input_path: Path, out_dir: Path) -> subprocess.CompletedProcess[str]:
    command = command_template.format(
        input=str(input_path),
        out=str(out_dir),
        output=str(out_dir),
        stem=input_path.stem,
    )
    return subprocess.run(command, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)


def _external_parser_error(
    name: str,
    completed: list[subprocess.CompletedProcess[str]],
    attempted: list[str],
) -> str:
    tail = completed[-1] if completed else None
    stderr = _preview((tail.stderr if tail else "") or "")
    stdout = _preview((tail.stdout if tail else "") or "")
    attempts = "; ".join(attempted[-3:])
    return f"Parser adapter `{name}` failed. Attempts: {attempts}. stderr: {stderr}. stdout: {stdout}"


def _external_adapter_available(adapter: ParserAdapter) -> bool:
    if not isinstance(adapter, ExternalCliParserAdapter):
        return True
    if os.getenv(adapter.command_env):
        return True
    return find_executable(adapter.executable_candidates) is not None


def _load_external_deck(
    adapter_name: str,
    input_path: Path,
    output_root: Path,
    work_dir: Path,
    completed: list[subprocess.CompletedProcess[str]],
) -> Deck:
    for result in completed:
        deck = _deck_from_json_text(result.stdout, input_path, output_root, work_dir)
        if deck is not None:
            return deck

    json_files = sorted(work_dir.rglob("*.json"), key=lambda path: (path.name != "content.json", len(path.parts), path.name))
    for path in json_files:
        try:
            deck = _deck_from_json_text(path.read_text(encoding="utf-8"), input_path, output_root, path.parent)
        except UnicodeDecodeError:
            continue
        if deck is not None:
            return deck

    markdown_files = sorted([*work_dir.rglob("*.md"), *work_dir.rglob("*.markdown")], key=lambda path: (len(path.parts), path.name))
    for path in markdown_files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        if text.strip():
            return _deck_from_markdown(text, input_path)

    raise RuntimeError(
        f"Parser adapter `{adapter_name}` finished but did not produce a readable JSON or Markdown deck in {work_dir}."
    )


def _deck_from_json_text(text: str, input_path: Path, output_root: Path, asset_root: Path) -> Deck | None:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, (dict, list)):
        return None
    if _looks_like_slidenote_deck(data):
        return _deck_from_slidenote_dict(data, input_path)
    return _deck_from_generic_json(data, input_path, output_root, asset_root)


def _looks_like_slidenote_deck(data: object) -> bool:
    if not isinstance(data, dict) or not isinstance(data.get("pages"), list):
        return False
    if not data["pages"]:
        return True
    first = data["pages"][0]
    return isinstance(first, dict) and any(key in first for key in ("slide_id", "text_blocks", "tables", "images"))


def _deck_from_slidenote_dict(data: dict[str, Any], input_path: Path) -> Deck:
    pages = [_slide_page_from_dict(page, index + 1) for index, page in enumerate(data.get("pages") or []) if isinstance(page, dict)]
    return Deck(
        source_path=str(data.get("source_path") or input_path),
        source_type=str(data.get("source_type") or input_path.suffix.lower().lstrip(".") or "external"),
        pages=pages,
        warnings=[str(warning) for warning in data.get("warnings", []) if warning],
    )


def _slide_page_from_dict(raw: dict[str, Any], fallback_slide_id: int) -> SlidePage:
    field_names = {field.name for field in fields(SlidePage)}
    kwargs = {name: raw.get(name) for name in field_names if name in raw}
    kwargs["slide_id"] = _as_int(raw.get("slide_id") or raw.get("page_id") or raw.get("page_number"), fallback_slide_id)
    kwargs["text_blocks"] = [
        _text_block_from_dict(block, kwargs["slide_id"], index + 1)
        for index, block in enumerate(raw.get("text_blocks") or [])
        if isinstance(block, dict)
    ]
    kwargs["tables"] = [
        _table_block_from_dict(table, kwargs["slide_id"], index + 1)
        for index, table in enumerate(raw.get("tables") or [])
        if isinstance(table, dict)
    ]
    kwargs["images"] = [
        _image_asset_from_dict(image, kwargs["slide_id"], index + 1)
        for index, image in enumerate(raw.get("images") or [])
        if isinstance(image, dict)
    ]
    return SlidePage(**kwargs)


def _text_block_from_dict(raw: dict[str, Any], slide_id: int, index: int) -> TextBlock:
    return TextBlock(
        id=str(raw.get("id") or f"s{slide_id}_t{index}"),
        type=str(raw.get("type") or raw.get("block_type") or "paragraph"),
        content=str(raw.get("content") or raw.get("text") or "").strip(),
        bbox=_bbox_or_none(raw.get("bbox")),
        style_runs=[item for item in raw.get("style_runs", []) if isinstance(item, dict)],
    )


def _table_block_from_dict(raw: dict[str, Any], slide_id: int, index: int) -> TableBlock:
    rows = _table_rows(raw) or []
    return TableBlock(
        id=str(raw.get("id") or f"s{slide_id}_tbl{index}"),
        rows=rows,
        bbox=_bbox_or_none(raw.get("bbox")),
        table_summary=_str_or_none(raw.get("table_summary") or raw.get("summary")),
        table_conclusion=_str_or_none(raw.get("table_conclusion") or raw.get("conclusion")),
        key_rows=[item for item in raw.get("key_rows", []) if isinstance(item, dict)],
    )


def _image_asset_from_dict(raw: dict[str, Any], slide_id: int, index: int) -> ImageAsset:
    kwargs = {field.name: raw.get(field.name) for field in fields(ImageAsset) if field.name in raw}
    kwargs["id"] = str(raw.get("id") or raw.get("image_id") or f"s{slide_id}_img{index}")
    kwargs["path"] = str(raw.get("path") or raw.get("image_path") or raw.get("uri") or "")
    kwargs["caption"] = _str_or_none(raw.get("caption") or raw.get("alt_text"))
    kwargs["bbox"] = _bbox_or_none(raw.get("bbox"))
    kwargs["role"] = str(raw.get("role") or "content")
    kwargs["ignored"] = bool(raw.get("ignored", False))
    return ImageAsset(**kwargs)


def _deck_from_generic_json(data: dict[str, Any] | list[Any], input_path: Path, output_root: Path, asset_root: Path) -> Deck | None:
    raw_pages = _find_pages(data)
    if not raw_pages:
        text = _generic_text(data)
        if not text:
            return None
        return _deck_from_markdown(text, input_path)

    pages: list[SlidePage] = []
    for index, raw_page in enumerate(raw_pages, start=1):
        if not isinstance(raw_page, dict):
            continue
        slide_id = _as_int(
            raw_page.get("slide_id") or raw_page.get("page_id") or raw_page.get("page") or raw_page.get("page_number"),
            index,
        )
        text_blocks = _generic_text_blocks(raw_page, slide_id)
        tables = _generic_tables(raw_page, slide_id)
        images = _generic_images(raw_page, slide_id, output_root, asset_root)
        pages.append(
            SlidePage(
                slide_id=slide_id,
                title=_title_from_blocks(text_blocks) or _str_or_none(raw_page.get("title")),
                page_width=_as_float_or_none(raw_page.get("width") or raw_page.get("page_width")),
                page_height=_as_float_or_none(raw_page.get("height") or raw_page.get("page_height")),
                text_blocks=text_blocks,
                tables=tables,
                images=images,
                page_screenshot=_str_or_none(raw_page.get("page_screenshot") or raw_page.get("screenshot")),
            )
        )
    if not pages:
        return None
    return Deck(source_path=str(input_path), source_type=input_path.suffix.lower().lstrip(".") or "external", pages=pages)


def _find_pages(data: dict[str, Any] | list[Any]) -> list[Any]:
    if isinstance(data, list):
        if data and all(isinstance(item, dict) for item in data):
            return data
        return []
    for key in ("pages", "slides", "children"):
        value = data.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            return list(value.values())
    document = data.get("document")
    if isinstance(document, dict):
        return _find_pages(document)
    return []


def _generic_text_blocks(raw_page: dict[str, Any], slide_id: int) -> list[TextBlock]:
    explicit_blocks = raw_page.get("text_blocks") or raw_page.get("blocks") or raw_page.get("children") or []
    blocks: list[TextBlock] = []
    for item in _iter_dicts(explicit_blocks):
        text = _text_from_node(item)
        if not text:
            continue
        kind = str(item.get("type") or item.get("label") or item.get("block_type") or "paragraph").lower()
        if "table" in kind or _table_rows(item):
            continue
        blocks.append(TextBlock(id=str(item.get("id") or f"s{slide_id}_t{len(blocks) + 1}"), type=_text_type(kind, text), content=text, bbox=_bbox_or_none(item.get("bbox"))))
    if blocks:
        return blocks
    text = _text_from_node(raw_page)
    if not text:
        return []
    return [TextBlock(id=f"s{slide_id}_t1", type="paragraph", content=text)]


def _generic_tables(raw_page: dict[str, Any], slide_id: int) -> list[TableBlock]:
    tables: list[TableBlock] = []
    for item in _iter_dicts(raw_page.get("tables") or raw_page.get("blocks") or raw_page.get("children") or []):
        kind = str(item.get("type") or item.get("label") or item.get("block_type") or "").lower()
        rows = _table_rows(item)
        if not rows and "table" not in kind:
            continue
        tables.append(
            TableBlock(
                id=str(item.get("id") or f"s{slide_id}_tbl{len(tables) + 1}"),
                rows=rows or [[_text_from_node(item)]],
                bbox=_bbox_or_none(item.get("bbox")),
            )
        )
    return tables


def _generic_images(raw_page: dict[str, Any], slide_id: int, output_root: Path, asset_root: Path) -> list[ImageAsset]:
    images: list[ImageAsset] = []
    for item in _iter_dicts(raw_page.get("images") or raw_page.get("pictures") or raw_page.get("blocks") or raw_page.get("children") or []):
        path = _str_or_none(item.get("path") or item.get("image_path") or item.get("uri") or item.get("src"))
        kind = str(item.get("type") or item.get("label") or item.get("block_type") or "").lower()
        if not path and "image" not in kind and "picture" not in kind:
            continue
        images.append(
            ImageAsset(
                id=str(item.get("id") or item.get("image_id") or f"s{slide_id}_img{len(images) + 1}"),
                path=_normalize_external_asset_path(path or "", output_root, asset_root),
                caption=_str_or_none(item.get("caption") or item.get("alt_text") or item.get("text")),
                bbox=_bbox_or_none(item.get("bbox")),
                role=str(item.get("role") or "content"),
                ignored=bool(item.get("ignored", False)),
            )
        )
    return images


def _deck_from_markdown(markdown: str, input_path: Path) -> Deck:
    chunks = _markdown_page_chunks(markdown)
    pages: list[SlidePage] = []
    for index, chunk in enumerate(chunks, start=1):
        title = _markdown_title(chunk) or (input_path.stem if index == 1 else None)
        pages.append(
            SlidePage(
                slide_id=index,
                title=title,
                text_blocks=[TextBlock(id=f"s{index}_t1", type="paragraph", content=chunk.strip())],
            )
        )
    return Deck(source_path=str(input_path), source_type=input_path.suffix.lower().lstrip(".") or "external", pages=pages)


def _markdown_page_chunks(markdown: str) -> list[str]:
    chunks = [chunk.strip() for chunk in re.split(r"\f|<!--\s*page[:=\s]\s*\d+\s*-->", markdown) if chunk.strip()]
    if len(chunks) > 1:
        return chunks
    return [markdown.strip()] if markdown.strip() else []


def _markdown_title(markdown: str) -> str | None:
    match = re.search(r"^\s{0,3}#{1,3}\s+(.+?)\s*$", markdown, flags=re.MULTILINE)
    if match:
        return _preview(match.group(1), 140)
    first = next((line.strip() for line in markdown.splitlines() if line.strip()), "")
    return _preview(first, 140) if first else None


def _iter_dicts(value: object) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if isinstance(value, dict):
        result.append(value)
        for child in value.values():
            result.extend(_iter_dicts(child))
    elif isinstance(value, list):
        for item in value:
            result.extend(_iter_dicts(item))
    return result


def _text_from_node(node: dict[str, Any]) -> str:
    for key in ("content", "text", "markdown", "html"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return _clean_text(value)
    return ""


def _generic_text(data: object) -> str:
    parts: list[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            if key in {"bbox", "metadata", "width", "height"}:
                continue
            if isinstance(value, str) and value.strip() and key in {"text", "content", "markdown", "html"}:
                parts.append(_clean_text(value))
            elif isinstance(value, (dict, list)):
                text = _generic_text(value)
                if text:
                    parts.append(text)
    elif isinstance(data, list):
        for item in data:
            text = _generic_text(item)
            if text:
                parts.append(text)
    return "\n\n".join(_dedupe(parts))


def _table_rows(raw: dict[str, Any]) -> list[list[str]]:
    for key in ("rows", "data", "cells"):
        value = raw.get(key)
        if isinstance(value, list) and value and all(isinstance(row, list) for row in value):
            return [[str(cell or "").strip() for cell in row] for row in value]
    return []


def _text_type(kind: str, text: str) -> str:
    if "title" in kind or "heading" in kind:
        return "heading"
    if len(text) <= 120 and "\n" not in text:
        return "heading"
    return "paragraph"


def _title_from_blocks(blocks: list[TextBlock]) -> str | None:
    for block in blocks:
        if block.type in {"title", "heading"} and block.content.strip():
            return _preview(block.content.splitlines()[0], 140)
    if blocks and blocks[0].content.strip():
        return _preview(blocks[0].content.splitlines()[0], 140)
    return None


def _normalize_external_asset_path(path: str, output_root: Path, asset_root: Path) -> str:
    if not path:
        return path
    candidate = Path(path)
    if not candidate.is_absolute():
        local = asset_root / candidate
        if local.exists():
            candidate = local
    try:
        return candidate.resolve().relative_to(output_root.resolve()).as_posix()
    except ValueError:
        return str(candidate)


def _clean_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _bbox_or_none(value: object) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError):
        return None


def _as_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _as_float_or_none(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _str_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _preview(value: str, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "..."
