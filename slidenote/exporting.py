from __future__ import annotations

import re
import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from slidenote.llm_cache import utc_now_iso
from slidenote.utils import slugify, write_text


EXPORT_FORMATS = ("markdown-zip", "markdown-toc", "docx", "pdf", "latex")
PANDOC_FORMATS = ("docx", "pdf", "latex")
MARKDOWN_ZIP_NAME = "notes.zip"

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", flags=re.DOTALL)
_LIBREOFFICE_CANDIDATES = ("soffice", "soffice.com", "libreoffice")


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
    messages: list[str] = []
    toc_source: Path | None = None

    if "markdown-zip" in formats:
        zip_result = _build_markdown_zip(notes_markdown, output_root)
        results.append(zip_result)
        if zip_result["status"] == "ok":
            messages.append("Markdown notes are inside notes.zip with notes.assets for images.")
        else:
            warnings.append(f"markdown-zip export failed: {zip_result.get('reason') or 'unknown error'}")

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
            docx_path: Path | None = None
            docx_result: dict[str, Any] | None = None

            if "docx" in pandoc_formats or "pdf" in pandoc_formats:
                docx_result = _run_pandoc(pandoc, source, output_root, "docx")
                if docx_result["status"] == "ok":
                    docx_path = output_root / "notes.docx"
                if "docx" in pandoc_formats:
                    results.append(docx_result)
                    if docx_result["status"] != "ok":
                        warnings.append(f"docx export failed: {docx_result.get('reason') or docx_result.get('stderr') or 'unknown error'}")

            if "pdf" in pandoc_formats:
                if docx_path is None:
                    pdf_result = {
                        "format": "pdf",
                        "status": "failed",
                        "path": "notes.pdf",
                        "reason": "docx_required_failed",
                        "blocking": True,
                        "dependency": docx_result,
                    }
                else:
                    pdf_result = _run_pdf_from_docx(docx_path, output_root)
                results.append(pdf_result)
                if pdf_result["status"] != "ok":
                    warnings.append(f"pdf export failed: {pdf_result.get('reason') or pdf_result.get('stderr') or 'unknown error'}")

            if "latex" in pandoc_formats:
                latex_result = _run_pandoc(pandoc, source, output_root, "latex")
                results.append(latex_result)
                if latex_result["status"] != "ok":
                    warnings.append(f"latex export failed: {latex_result.get('reason') or latex_result.get('stderr') or 'unknown error'}")

    failed = sum(1 for result in results if result["status"] == "failed")
    skipped = sum(1 for result in results if result["status"] == "skipped")
    succeeded = sum(1 for result in results if result["status"] == "ok")
    blocking_failures = sum(1 for result in results if result["status"] == "failed" and result.get("blocking"))
    return {
        "schema_version": 2,
        "generated_at": utc_now_iso(),
        "requested_formats": formats,
        "export_toc": export_toc,
        "pdf_strategy": "docx_to_pdf_via_libreoffice",
        "latex_strategy": "pandoc_ctexart_source",
        "summary": {
            "requested": len(formats),
            "succeeded": succeeded,
            "failed": failed,
            "skipped": skipped,
            "blocking_failures": blocking_failures,
        },
        "results": results,
        "warnings": warnings,
        "messages": messages,
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


def _build_markdown_zip(notes_markdown: str, output_root: Path) -> dict[str, Any]:
    output_path = output_root / MARKDOWN_ZIP_NAME
    notes_path = output_root / "notes.md"
    try:
        if not notes_path.exists():
            write_text(notes_path, notes_markdown)
        if output_path.exists():
            output_path.unlink()
        asset_files = _markdown_asset_files(output_root)
        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(notes_path, "notes.md")
            for asset_path in asset_files:
                archive.write(asset_path, asset_path.relative_to(output_root).as_posix())
            archive.writestr(
                "README.txt",
                "Open notes.md after extracting this ZIP. Keep notes.assets next to notes.md so images render.\n",
            )
    except OSError as exc:
        return {
            "format": "markdown-zip",
            "status": "failed",
            "path": MARKDOWN_ZIP_NAME,
            "reason": "markdown_zip_failed",
            "stderr": str(exc),
            "blocking": True,
        }

    return {
        "format": "markdown-zip",
        "status": "ok",
        "path": MARKDOWN_ZIP_NAME,
        "message": "Markdown notes are inside notes.zip with image assets.",
        "includes": ["notes.md", "notes.assets/"] if asset_files else ["notes.md"],
        "asset_files": len(asset_files),
    }


def _markdown_asset_files(output_root: Path) -> list[Path]:
    assets_root = output_root / "notes.assets"
    if not assets_root.exists():
        return []
    return sorted(path for path in assets_root.rglob("*") if path.is_file())


def _run_pandoc(pandoc: str, source: Path, output_root: Path, fmt: str) -> dict[str, Any]:
    output_name = _output_name(fmt)
    output_path = output_root / output_name
    command = [pandoc, "-f", "markdown-implicit_figures", _relative_to_output(source, output_root), "-o", output_name]
    if fmt == "latex":
        command.extend(["--standalone", "--pdf-engine=xelatex", "-V", "documentclass=ctexart", "-V", "geometry:margin=1in"])

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


def _run_pdf_from_docx(docx_path: Path, output_root: Path) -> dict[str, Any]:
    output_name = "notes.pdf"
    output_path = output_root / output_name
    libreoffice = _find_libreoffice()
    result: dict[str, Any] = {
        "format": "pdf",
        "path": output_name,
        "source": docx_path.name,
        "strategy": "docx_to_pdf_via_libreoffice",
    }
    if not libreoffice:
        result.update(
            {
                "status": "failed",
                "blocking": True,
                "reason": "libreoffice_not_found",
                "message": "PDF export uses LibreOffice to convert notes.docx for reliable Chinese/CJK layout.",
            }
        )
        return result

    if output_path.exists():
        output_path.unlink()
    command = [libreoffice, "--headless", "--convert-to", "pdf", "--outdir", str(output_root), str(docx_path)]
    result["command"] = command
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
        result.update({"status": "failed", "blocking": True, "reason": "libreoffice_launch_failed", "stderr": str(exc), "returncode": None})
        return result
    result["returncode"] = completed.returncode
    if completed.returncode == 0 and output_path.exists():
        result["status"] = "ok"
        return result
    result.update(
        {
            "status": "failed",
            "blocking": True,
            "reason": "libreoffice_pdf_failed" if completed.returncode else "libreoffice_output_missing",
            "stderr": _summarize_stream(completed.stderr),
            "stdout": _summarize_stream(completed.stdout),
        }
    )
    return result


def _find_libreoffice() -> str | None:
    for executable in _LIBREOFFICE_CANDIDATES:
        found = shutil.which(executable)
        if found:
            return found
    return None


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
