from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from slidenote.llm_cache import utc_now_iso
from slidenote.utils import slugify, write_text


EXPORT_FORMATS = ("markdown-toc", "docx", "pdf", "latex")
PANDOC_FORMATS = ("docx", "pdf", "latex")

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", flags=re.DOTALL)


@dataclass(frozen=True)
class Heading:
    level: int
    text: str
    slug: str


def parse_export_formats(value: str | None) -> list[str]:
    if not value:
        return []
    requested: list[str] = []
    for raw_part in value.split(","):
        part = raw_part.strip().lower()
        if not part:
            continue
        if part == "all":
            return list(EXPORT_FORMATS)
        if part not in EXPORT_FORMATS:
            allowed = ", ".join((*EXPORT_FORMATS, "all"))
            raise ValueError(f"Unsupported export format `{part}`. Supported values: {allowed}.")
        if part not in requested:
            requested.append(part)
    return requested


def build_export_artifacts(notes_markdown: str, output_root: Path, formats: list[str], export_toc: str = "auto") -> dict[str, Any] | None:
    if not formats:
        return None

    output_root.mkdir(parents=True, exist_ok=True)
    cleaned_markdown = clean_markdown_for_export(notes_markdown)
    results: list[dict[str, Any]] = []
    warnings: list[str] = []
    toc_source: Path | None = None

    if "markdown-toc" in formats:
        if export_toc == "off":
            results.append({"format": "markdown-toc", "status": "skipped", "reason": "export_toc_off", "path": None})
            warnings.append("Markdown TOC export was requested but --export-toc is off.")
        else:
            toc_markdown = add_table_of_contents(cleaned_markdown)
            toc_source = output_root / "notes.toc.md"
            write_text(toc_source, toc_markdown)
            results.append({"format": "markdown-toc", "status": "ok", "path": "notes.toc.md"})

    pandoc_formats = [fmt for fmt in formats if fmt in PANDOC_FORMATS]
    if pandoc_formats:
        pandoc = shutil.which("pandoc")
        source = toc_source if toc_source is not None else _write_pandoc_source(cleaned_markdown, output_root)
        if not pandoc:
            for fmt in pandoc_formats:
                results.append(
                    {
                        "format": fmt,
                        "status": "failed",
                        "path": _output_name(fmt),
                        "reason": "pandoc_not_found",
                        "blocking": True,
                    }
                )
            warnings.append("Pandoc was not found on PATH; docx/pdf/latex exports were not generated.")
        else:
            for fmt in pandoc_formats:
                result = _run_pandoc(pandoc, source, output_root, fmt)
                results.append(result)
                if result["status"] != "ok":
                    warnings.append(f"{fmt} export failed: {result.get('reason') or result.get('stderr') or 'unknown error'}")

    failed = sum(1 for result in results if result["status"] == "failed")
    skipped = sum(1 for result in results if result["status"] == "skipped")
    succeeded = sum(1 for result in results if result["status"] == "ok")
    blocking_failures = sum(1 for result in results if result["status"] == "failed" and result.get("blocking"))
    return {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "requested_formats": formats,
        "export_toc": export_toc,
        "summary": {
            "requested": len(formats),
            "succeeded": succeeded,
            "failed": failed,
            "skipped": skipped,
            "blocking_failures": blocking_failures,
        },
        "results": results,
        "warnings": warnings,
    }


def export_blocking_failures(report: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not report:
        return []
    results = report.get("results")
    if not isinstance(results, list):
        return []
    return [result for result in results if isinstance(result, dict) and result.get("status") == "failed" and result.get("blocking")]


def export_warnings(report: dict[str, Any] | None) -> list[str]:
    if not report:
        return []
    warnings = report.get("warnings")
    if not isinstance(warnings, list):
        return []
    return [str(warning) for warning in warnings]


def clean_markdown_for_export(markdown: str) -> str:
    text = _HTML_COMMENT_RE.sub("", markdown)
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines).strip() + "\n"


def add_table_of_contents(markdown: str) -> str:
    text = clean_markdown_for_export(markdown)
    lines = text.splitlines()
    headings = _collect_toc_headings(lines)
    if not headings:
        return text

    first_h1_index = _first_h1_index(lines)
    insert_index = _toc_insert_index(lines, first_h1_index)
    min_level = min(heading.level for heading in headings)
    toc_lines = ["## 目录", ""]
    for heading in headings:
        indent = "  " * max(0, heading.level - min_level)
        toc_lines.append(f"{indent}- [{heading.text}](#{heading.slug})")
    toc_lines.append("")

    merged = lines[:insert_index] + toc_lines + lines[insert_index:]
    return "\n".join(merged).strip() + "\n"


def _collect_toc_headings(lines: list[str]) -> list[Heading]:
    headings: list[Heading] = []
    used_slugs: dict[str, int] = {}
    first_h1_seen = False
    for line in lines:
        parsed = _parse_heading(line)
        if parsed is None:
            continue
        level, text = parsed
        if level > 3:
            continue
        if level == 1 and not first_h1_seen:
            first_h1_seen = True
            continue
        slug = _unique_slug(slugify(text), used_slugs)
        headings.append(Heading(level=level, text=text, slug=slug))
    return headings


def _parse_heading(line: str) -> tuple[int, str] | None:
    match = _HEADING_RE.match(_HTML_COMMENT_RE.sub("", line).strip())
    if not match:
        return None
    text = _clean_heading_text(match.group(2))
    if not text:
        return None
    return len(match.group(1)), text


def _clean_heading_text(text: str) -> str:
    text = _HTML_COMMENT_RE.sub("", text)
    text = re.sub(r"\[([^\]]+)]\([^)]+\)", r"\1", text)
    text = re.sub(r"[`*_~]", "", text)
    return " ".join(text.strip().split())


def _unique_slug(slug: str, used_slugs: dict[str, int]) -> str:
    count = used_slugs.get(slug, 0) + 1
    used_slugs[slug] = count
    return slug if count == 1 else f"{slug}-{count}"


def _first_h1_index(lines: list[str]) -> int | None:
    for index, line in enumerate(lines):
        parsed = _parse_heading(line)
        if parsed and parsed[0] == 1:
            return index
    return None


def _toc_insert_index(lines: list[str], first_h1_index: int | None) -> int:
    if first_h1_index is None:
        return 0
    index = first_h1_index + 1
    while index < len(lines) and not lines[index].strip():
        index += 1
    return index


def _write_pandoc_source(markdown: str, output_root: Path) -> Path:
    source = output_root / ".cache" / "export" / "notes.export.md"
    write_text(source, markdown)
    return source


def _run_pandoc(pandoc: str, source: Path, output_root: Path, fmt: str) -> dict[str, Any]:
    output_name = _output_name(fmt)
    output_path = output_root / output_name
    command = [pandoc, _relative_to_output(source, output_root), "-o", output_name]
    if fmt == "pdf":
        command.append("--pdf-engine=xelatex")
    elif fmt == "latex":
        command.append("--standalone")

    result: dict[str, Any] = {
        "format": fmt,
        "path": output_name,
        "command": command,
    }
    try:
        completed = subprocess.run(
            command,
            cwd=str(output_root),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        result.update({"status": "failed", "blocking": True, "reason": "pandoc_launch_failed", "stderr": str(exc), "returncode": None})
        return result
    result["returncode"] = completed.returncode
    if completed.returncode == 0 and output_path.exists():
        result["status"] = "ok"
        return result
    result["status"] = "failed"
    result["blocking"] = True
    result["stderr"] = _summarize_stream(completed.stderr)
    result["stdout"] = _summarize_stream(completed.stdout)
    result["reason"] = "pandoc_failed" if completed.returncode else "pandoc_output_missing"
    return result


def _output_name(fmt: str) -> str:
    return {"docx": "notes.docx", "pdf": "notes.pdf", "latex": "notes.tex"}[fmt]


def _relative_to_output(path: Path, output_root: Path) -> str:
    try:
        return path.resolve().relative_to(output_root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _summarize_stream(value: str, limit: int = 800) -> str:
    text = " ".join((value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
