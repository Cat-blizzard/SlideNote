from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from slidenote.llm_cache import LLMCache, make_cache_key, sha256_text, utc_now_iso
from slidenote.ocr import OCRClient, _cleanup_temp_image, _prepare_image_for_ocr
from slidenote.utils import ensure_clean_dir, write_json, write_text

TEXTBOOK_SCHEMA_VERSION = 1
LOW_TEXT_CHARS = 80
CHUNK_MAX_CHARS = 1200


class TextbookIndexError(RuntimeError):
    """User-facing error raised by the textbook indexing pipeline."""


@dataclass(slots=True)
class TextbookOCRTarget:
    physical_page: int
    image_path: Path
    reason: str


def build_textbook_index(
    input_path: Path,
    output_root: Path,
    *,
    ocr: str = "auto",
    ocr_provider: str = "baidu",
    ocr_api_key: str | None = None,
    ocr_secret_key: str | None = None,
    quiet: bool = False,
) -> dict[str, Any]:
    input_path = input_path.resolve()
    output_root = output_root.resolve()
    if input_path.suffix.lower() != ".pdf":
        raise TextbookIndexError("textbook-index v1 only supports PDF input.")
    if ocr not in {"auto", "off", "all"}:
        raise TextbookIndexError("OCR mode must be one of: auto, off, all.")
    if not input_path.exists():
        raise FileNotFoundError(input_path)

    ensure_clean_dir(output_root)
    source_hash = _sha256_file(input_path)
    pages, metadata = extract_textbook_pages(input_path)
    ocr_report = None
    if ocr != "off":
        ocr_report = enrich_textbook_pages_with_ocr(
            input_path,
            output_root,
            pages,
            source_hash=source_hash,
            mode=ocr,
            provider=ocr_provider,
            api_key=ocr_api_key,
            secret_key=ocr_secret_key,
        )
    _refresh_combined_text(pages)
    toc = detect_textbook_toc(pages)
    sections = build_textbook_sections(pages, toc)
    chunks = build_textbook_chunks(pages, sections, source_hash=source_hash)
    manifest = _build_manifest(input_path, source_hash, metadata, pages, toc, sections, chunks, ocr, ocr_provider, ocr_report)
    index = _build_index(manifest, chunks)

    _write_jsonl(output_root / "textbook_pages.jsonl", pages)
    write_json(output_root / "textbook_toc.json", toc)
    write_json(output_root / "textbook_sections.json", {"schema_version": TEXTBOOK_SCHEMA_VERSION, "sections": sections})
    _write_jsonl(output_root / "textbook_chunks.jsonl", chunks)
    write_json(output_root / "textbook_manifest.json", manifest)
    write_json(output_root / "textbook_index.json", index)
    write_text(output_root / "textbook_report.md", _render_report(manifest, toc, sections, chunks))
    if ocr_report and ocr_report["summary"]["targets_total"] > 0:
        write_json(output_root / "ocr_usage.json", ocr_report)

    if not quiet:
        print(f"Textbook index written to {output_root}", flush=True)
        print(f"- pages:    {output_root / 'textbook_pages.jsonl'}", flush=True)
        print(f"- sections: {output_root / 'textbook_sections.json'}", flush=True)
        print(f"- chunks:   {output_root / 'textbook_chunks.jsonl'}", flush=True)

    return {
        "manifest": manifest,
        "toc": toc,
        "sections": sections,
        "chunks": chunks,
        "ocr": ocr_report,
    }


def extract_textbook_pages(input_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        import fitz
    except ImportError as exc:  # pragma: no cover - dependency is required by project
        raise TextbookIndexError("PyMuPDF is required for textbook-index.") from exc

    doc = fitz.open(input_path)
    metadata = {key: value for key, value in (doc.metadata or {}).items() if value}
    pages: list[dict[str, Any]] = []
    try:
        for index, page in enumerate(doc, start=1):
            native_text = _normalize_page_text(page.get_text("text") or "")
            label = _page_label(doc, page, index)
            pages.append(
                {
                    "schema_version": TEXTBOOK_SCHEMA_VERSION,
                    "source_type": "textbook_page",
                    "physical_page": index,
                    "printed_page_label": label,
                    "native_text": native_text,
                    "ocr_text": "",
                    "combined_text": native_text,
                    "text_chars": len(native_text.strip()),
                    "needs_ocr": len(native_text.strip()) < LOW_TEXT_CHARS,
                    "ocr_status": "not_requested",
                    "title_hint": _title_hint(native_text),
                }
            )
    finally:
        doc.close()
    return pages, metadata


def select_textbook_ocr_targets(pages: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    if mode == "off":
        return []
    targets: list[dict[str, Any]] = []
    for page in pages:
        if mode == "all" or bool(page.get("needs_ocr")):
            reason = "all_pages" if mode == "all" else "low_native_text"
            targets.append({"physical_page": int(page["physical_page"]), "reason": reason})
    return targets


def enrich_textbook_pages_with_ocr(
    input_path: Path,
    output_root: Path,
    pages: list[dict[str, Any]],
    *,
    source_hash: str,
    mode: str,
    provider: str,
    api_key: str | None,
    secret_key: str | None,
) -> dict[str, Any]:
    targets = select_textbook_ocr_targets(pages, mode)
    cache_dir = output_root / ".cache" / "textbook_ocr"
    rendered_dir = cache_dir / "rendered_pages"
    rendered_dir.mkdir(parents=True, exist_ok=True)
    cache = LLMCache(cache_dir, mode="on")
    records: list[dict[str, Any]] = []

    if not targets:
        return _ocr_report(input_path, provider, mode, cache_dir, records)

    try:
        import fitz
    except ImportError as exc:  # pragma: no cover - dependency is required by project
        raise TextbookIndexError("PyMuPDF is required for textbook OCR rendering.") from exc

    doc = fitz.open(input_path)
    try:
        for target in targets:
            page_number = int(target["physical_page"])
            rendered = rendered_dir / f"page_{page_number:04d}.png"
            _render_page_image(doc, page_number, rendered)
            record, text = _recognize_textbook_page(
                rendered,
                page_number=page_number,
                reason=str(target["reason"]),
                source_hash=source_hash,
                cache=cache,
                provider=provider,
                api_key=api_key,
                secret_key=secret_key,
            )
            records.append(record)
            page = pages[page_number - 1]
            page["ocr_text"] = text
            page["ocr_status"] = "parsed" if text else "empty"
    finally:
        doc.close()

    return _ocr_report(input_path, provider, mode, cache_dir, records)


def detect_textbook_toc(pages: list[dict[str, Any]], max_scan_pages: int = 25) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    toc_pages: list[int] = []
    for page in pages[:max_scan_pages]:
        text = str(page.get("combined_text") or page.get("native_text") or "")
        lines = _clean_lines(text)
        page_entries = [_parse_toc_line(line, int(page["physical_page"])) for line in lines]
        page_entries = [entry for entry in page_entries if entry]
        has_heading = any(_looks_like_toc_heading(line) for line in lines[:12])
        if has_heading or len(page_entries) >= 2:
            toc_pages.append(int(page["physical_page"]))
            entries.extend(page_entries)
    deduped = _dedupe_toc_entries(entries)
    confidence = 0.0
    if deduped:
        confidence = min(0.95, 0.45 + 0.08 * len(deduped) + (0.15 if toc_pages else 0.0))
    return {
        "schema_version": TEXTBOOK_SCHEMA_VERSION,
        "summary": {
            "has_toc": bool(deduped),
            "toc_pages": toc_pages,
            "entries_count": len(deduped),
            "confidence": round(confidence, 3),
            "source": "toc_pages" if deduped else "none",
        },
        "entries": deduped,
    }


def build_textbook_sections(pages: list[dict[str, Any]], toc: dict[str, Any]) -> list[dict[str, Any]]:
    entries = [entry for entry in toc.get("entries", []) if int(entry.get("level") or 1) <= 2]
    page_lookup = _printed_page_lookup(pages)
    sections: list[dict[str, Any]] = []
    if entries:
        starts: list[tuple[dict[str, Any], int]] = []
        for entry in entries:
            start = _resolve_printed_page(str(entry.get("printed_page_label") or ""), page_lookup)
            if start is None:
                start = min(max(int(entry.get("toc_page") or 1), 1), len(pages))
            starts.append((entry, start))
        starts.sort(key=lambda item: (item[1], int(item[0].get("level") or 1), str(item[0].get("title") or "")))
        for index, (entry, start) in enumerate(starts, start=1):
            next_start = starts[index][1] if index < len(starts) else len(pages) + 1
            end = max(start, min(len(pages), next_start - 1))
            sections.append(
                {
                    "section_id": f"tb_sec{index:03d}",
                    "title": str(entry.get("title") or f"Section {index}"),
                    "level": int(entry.get("level") or 1),
                    "start_page": start,
                    "end_page": end,
                    "printed_page_label": str(entry.get("printed_page_label") or ""),
                    "source": "toc",
                    "toc_entry_index": entry.get("entry_index"),
                }
            )
    else:
        sections = _fallback_sections_from_headings(pages)
    if not sections and pages:
        sections = [_full_textbook_section(len(pages))]
    return _normalize_section_ranges(sections, len(pages))


def build_textbook_chunks(
    pages: list[dict[str, Any]],
    sections: list[dict[str, Any]],
    *,
    source_hash: str,
    max_chars: int = CHUNK_MAX_CHARS,
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    source_prefix = source_hash.split(":", 1)[-1][:12]
    for section in sections:
        section_pages = [
            page
            for page in pages
            if int(section["start_page"]) <= int(page["physical_page"]) <= int(section["end_page"])
        ]
        units = _section_text_units(section_pages)
        buffer: list[str] = []
        source_pages: set[int] = set()
        for text, page_number in units:
            candidate = "\n\n".join(buffer + [text]).strip()
            if buffer and len(candidate) > max_chars:
                _append_chunk(chunks, source_prefix, section, buffer, source_pages, pages)
                buffer = [text]
                source_pages = {page_number}
            else:
                buffer.append(text)
                source_pages.add(page_number)
        if buffer:
            _append_chunk(chunks, source_prefix, section, buffer, source_pages, pages)
    return chunks


def _recognize_textbook_page(
    rendered_path: Path,
    *,
    page_number: int,
    reason: str,
    source_hash: str,
    cache: LLMCache,
    provider: str,
    api_key: str | None,
    secret_key: str | None,
) -> tuple[dict[str, Any], str]:
    cache_key = make_cache_key(
        {
            "schema_version": TEXTBOOK_SCHEMA_VERSION,
            "task": "textbook_page_ocr",
            "provider": provider,
            "source_hash": source_hash,
            "physical_page": page_number,
            "rendered_hash": _sha256_file(rendered_path),
        }
    )
    cache_path = cache.path_for(cache_key)
    cached = cache.read(cache_key)
    record = {
        "physical_page": page_number,
        "reason": reason,
        "cache_key": cache_key,
        "cache_file": _display_path(cache_path, cache.cache_dir.parent.parent),
        "api_call": False,
        "cache_status": "local_hit" if cached else "miss",
    }
    if cached:
        text = str(cached.get("output_text") or "")
        record["text_chars"] = len(text)
        return record, text

    prepared = _prepare_image_for_ocr(rendered_path, max_edge=2200)
    if prepared is None:
        record.update({"cache_status": "skipped", "skip_reason": "unreadable_rendered_page"})
        return record, ""
    prepared_path, image_meta = prepared
    try:
        client = OCRClient(provider=provider, api_key=api_key, secret_key=secret_key)
        result = client.recognize(prepared_path)
        text = result.text.strip()
        record.update(
            {
                "api_call": True,
                "text_chars": len(text),
                "provider_usage": result.usage,
                "image_meta": image_meta,
            }
        )
        written = cache.write(
            cache_key,
            {
                "provider": provider,
                "physical_page": page_number,
                "output_text": text,
                "response_usage": result.usage,
                "raw_response": result.raw,
            },
        )
        if written is not None:
            record["cache_file"] = _display_path(written, cache.cache_dir.parent.parent)
        return record, text
    finally:
        _cleanup_temp_image(prepared_path)


def _ocr_report(input_path: Path, provider: str, mode: str, cache_dir: Path, records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": TEXTBOOK_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "source_path": str(input_path),
        "provider": provider,
        "mode": mode,
        "cache": {"mode": "on", "dir": str(cache_dir)},
        "summary": {
            "targets_total": len(records),
            "api_calls": sum(1 for record in records if record.get("api_call")),
            "local_cache_hits": sum(1 for record in records if record.get("cache_status") == "local_hit"),
            "skipped": sum(1 for record in records if record.get("cache_status") == "skipped"),
            "text_chars": sum(int(record.get("text_chars") or 0) for record in records),
        },
        "targets": records,
    }


def _refresh_combined_text(pages: list[dict[str, Any]]) -> None:
    for page in pages:
        native = str(page.get("native_text") or "").strip()
        ocr = str(page.get("ocr_text") or "").strip()
        if not ocr:
            combined = native
        elif not native or len(native) < LOW_TEXT_CHARS:
            combined = ocr
        elif ocr in native:
            combined = native
        else:
            combined = f"{native}\n\n[OCR]\n{ocr}"
        page["combined_text"] = combined
        page["combined_text_chars"] = len(combined)


def _parse_toc_line(line: str, toc_page: int) -> dict[str, Any] | None:
    line = _normalize_spaces(line)
    if not line or _looks_like_toc_heading(line) or len(line) > 180:
        return None
    patterns = [
        r"^(?P<title>.+?)(?:\.{2,}|…{2,}|\s{2,}|\t+)\s*(?P<page>[ivxlcdmIVXLCM]+|\d{1,4})$",
        r"^(?P<title>(?:第\s*[一二三四五六七八九十百千万零〇\d]+\s*[章节篇部].+?|Chapter\s+\d+.+?|\d+(?:\.\d+)*\s+.+?))\s+(?P<page>\d{1,4})$",
    ]
    for pattern in patterns:
        match = re.match(pattern, line, flags=re.IGNORECASE)
        if not match:
            continue
        title = _strip_toc_leader(match.group("title"))
        page_label = match.group("page").strip()
        if not _looks_like_section_title(title):
            continue
        return {
            "title": title,
            "level": _toc_level(title),
            "printed_page_label": page_label,
            "toc_page": toc_page,
            "raw_line": line,
        }
    return None


def _dedupe_toc_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for entry in entries:
        key = (str(entry.get("title") or ""), str(entry.get("printed_page_label") or ""))
        if key in seen:
            continue
        seen.add(key)
        entry = dict(entry)
        entry["entry_index"] = len(deduped) + 1
        deduped.append(entry)
    return deduped


def _fallback_sections_from_headings(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    headings: list[tuple[int, str, int]] = []
    for page in pages:
        for line in _clean_lines(str(page.get("combined_text") or ""))[:12]:
            if _looks_like_section_title(line):
                headings.append((int(page["physical_page"]), _strip_toc_leader(line), _toc_level(line)))
                break
    if not headings:
        return []
    sections: list[dict[str, Any]] = []
    for index, (start, title, level) in enumerate(headings, start=1):
        next_start = headings[index][0] if index < len(headings) else len(pages) + 1
        sections.append(
            {
                "section_id": f"tb_sec{index:03d}",
                "title": title,
                "level": level,
                "start_page": start,
                "end_page": max(start, min(len(pages), next_start - 1)),
                "printed_page_label": str(start),
                "source": "heading_fallback",
            }
        )
    return sections


def _normalize_section_ranges(sections: list[dict[str, Any]], page_count: int) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for section in sections:
        start = min(max(int(section.get("start_page") or 1), 1), max(page_count, 1))
        end = min(max(int(section.get("end_page") or start), start), max(page_count, 1))
        item = dict(section)
        item["start_page"] = start
        item["end_page"] = end
        item["page_count"] = end - start + 1
        normalized.append(item)
    return normalized


def _full_textbook_section(page_count: int) -> dict[str, Any]:
    return {
        "section_id": "tb_sec001",
        "title": "Full textbook",
        "level": 1,
        "start_page": 1,
        "end_page": max(page_count, 1),
        "printed_page_label": "1",
        "source": "fallback",
        "page_count": max(page_count, 1),
    }


def _section_text_units(pages: list[dict[str, Any]]) -> list[tuple[str, int]]:
    units: list[tuple[str, int]] = []
    for page in pages:
        page_number = int(page["physical_page"])
        paragraphs = _paragraphs(str(page.get("combined_text") or ""))
        for paragraph in paragraphs:
            units.append((paragraph, page_number))
    return units


def _append_chunk(
    chunks: list[dict[str, Any]],
    source_prefix: str,
    section: dict[str, Any],
    buffer: list[str],
    source_pages: set[int],
    all_pages: list[dict[str, Any]],
) -> None:
    text = "\n\n".join(buffer).strip()
    if not text:
        return
    index = sum(1 for chunk in chunks if chunk["section_id"] == section["section_id"]) + 1
    pages = sorted(source_pages)
    page_lookup = {int(page["physical_page"]): page for page in all_pages}
    source_refs = [
        {
            "type": "textbook_page",
            "physical_page": page_number,
            "printed_page_label": page_lookup.get(page_number, {}).get("printed_page_label"),
        }
        for page_number in pages
    ]
    chunks.append(
        {
            "schema_version": TEXTBOOK_SCHEMA_VERSION,
            "chunk_id": f"tb_{source_prefix}_{section['section_id']}_c{index:03d}",
            "section_id": section["section_id"],
            "section_title": section["title"],
            "chunk_index": index,
            "text": text,
            "text_chars": len(text),
            "content_hash": sha256_text(text),
            "page_start": pages[0] if pages else section["start_page"],
            "page_end": pages[-1] if pages else section["end_page"],
            "source_refs": source_refs,
        }
    )


def _build_manifest(
    input_path: Path,
    source_hash: str,
    metadata: dict[str, Any],
    pages: list[dict[str, Any]],
    toc: dict[str, Any],
    sections: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
    ocr_mode: str,
    ocr_provider: str,
    ocr_report: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "schema_version": TEXTBOOK_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "source_path": str(input_path),
        "source_type": "pdf",
        "source_hash": source_hash,
        "metadata": metadata,
        "pipeline": {
            "kind": "textbook_index",
            "rag_status": "ready_for_embedding",
            "note_generation": "not_connected",
            "ocr": {"mode": ocr_mode, "provider": ocr_provider},
            "vision": {"mode": "off"},
        },
        "counts": {
            "pages": len(pages),
            "low_text_pages": sum(1 for page in pages if page.get("needs_ocr")),
            "ocr_targets": (ocr_report or {}).get("summary", {}).get("targets_total", 0),
            "ocr_api_calls": (ocr_report or {}).get("summary", {}).get("api_calls", 0),
            "toc_entries": len(toc.get("entries") or []),
            "sections": len(sections),
            "chunks": len(chunks),
        },
        "artifacts": {
            "pages": "textbook_pages.jsonl",
            "toc": "textbook_toc.json",
            "sections": "textbook_sections.json",
            "chunks": "textbook_chunks.jsonl",
            "index": "textbook_index.json",
            "report": "textbook_report.md",
            "ocr_usage": "ocr_usage.json" if ocr_report and ocr_report["summary"]["targets_total"] > 0 else None,
        },
    }


def _build_index(manifest: dict[str, Any], chunks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": TEXTBOOK_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "source_hash": manifest["source_hash"],
        "retrieval_status": "chunks_ready_embeddings_not_built",
        "embedding_status": "not_built",
        "chunks_file": "textbook_chunks.jsonl",
        "chunk_count": len(chunks),
        "chunks": [
            {
                "chunk_id": chunk["chunk_id"],
                "section_id": chunk["section_id"],
                "section_title": chunk["section_title"],
                "page_start": chunk["page_start"],
                "page_end": chunk["page_end"],
                "text_chars": chunk["text_chars"],
                "content_hash": chunk["content_hash"],
            }
            for chunk in chunks
        ],
    }


def _render_report(manifest: dict[str, Any], toc: dict[str, Any], sections: list[dict[str, Any]], chunks: list[dict[str, Any]]) -> str:
    counts = manifest["counts"]
    lines = [
        "# Textbook Library Report",
        "",
        f"- Source: `{manifest['source_path']}`",
        f"- Pages: {counts['pages']}",
        f"- OCR targets: {counts['ocr_targets']} ({counts['ocr_api_calls']} API calls)",
        f"- TOC entries: {counts['toc_entries']}",
        f"- Sections: {counts['sections']}",
        f"- Chunks: {counts['chunks']}",
        "",
        "This textbook library is RAG-ready but is not connected to note generation yet.",
        "",
        "## TOC",
    ]
    for entry in (toc.get("entries") or [])[:80]:
        indent = "  " * max(0, int(entry.get("level") or 1) - 1)
        lines.append(f"- {indent}{entry.get('title')} -> {entry.get('printed_page_label')}")
    if not toc.get("entries"):
        lines.append("- No explicit TOC detected; sections used heading fallback.")
    lines.extend(["", "## Sections"])
    for section in sections[:120]:
        lines.append(f"- {section['section_id']}: {section['title']} (pages {section['start_page']}-{section['end_page']})")
    lines.extend(["", "## Chunk Files", "", "- `textbook_chunks.jsonl` contains one JSON object per retrievable chunk."])
    return "\n".join(lines).rstrip() + "\n"


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + ("\n" if rows else ""), encoding="utf-8")


def _page_label(doc: Any, page: Any, index: int) -> str:
    get_label = getattr(page, "get_label", None)
    if callable(get_label):
        try:
            label = str(get_label()).strip()
            if label:
                return label
        except Exception:
            pass
    try:
        labels = doc.get_page_labels()
    except Exception:
        labels = None
    if labels:
        for item in labels:
            if isinstance(item, dict) and int(item.get("startpage", -1)) == index - 1 and item.get("firstpagenum"):
                return str(item["firstpagenum"])
    return str(index)


def _render_page_image(doc: Any, physical_page: int, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    page = doc[physical_page - 1]
    try:
        import fitz

        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
    except Exception:
        pix = page.get_pixmap(alpha=False)
    pix.save(path)


def _printed_page_lookup(pages: list[dict[str, Any]]) -> dict[str, int]:
    lookup: dict[str, int] = {}
    for page in pages:
        physical = int(page["physical_page"])
        labels = {str(physical), str(page.get("printed_page_label") or "").strip()}
        for label in labels:
            if label:
                lookup.setdefault(label, physical)
                normalized = _normalize_page_label(label)
                if normalized:
                    lookup.setdefault(normalized, physical)
    return lookup


def _resolve_printed_page(label: str, lookup: dict[str, int]) -> int | None:
    if not label:
        return None
    return lookup.get(label) or lookup.get(_normalize_page_label(label))


def _normalize_page_label(label: str) -> str:
    label = label.strip()
    if label.isdigit():
        return str(int(label))
    return label.lower()


def _paragraphs(text: str) -> list[str]:
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    if len(blocks) > 1:
        return blocks
    lines = _clean_lines(text)
    paragraphs: list[str] = []
    buffer: list[str] = []
    for line in lines:
        buffer.append(line)
        if len(" ".join(buffer)) >= 360 or _looks_like_section_title(line):
            paragraphs.append(" ".join(buffer).strip())
            buffer = []
    if buffer:
        paragraphs.append(" ".join(buffer).strip())
    return paragraphs


def _clean_lines(text: str) -> list[str]:
    return [_normalize_spaces(line) for line in text.splitlines() if _normalize_spaces(line)]


def _normalize_page_text(text: str) -> str:
    lines = [_normalize_spaces(line) for line in text.replace("\x00", "").splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\u3000", " ")).strip()


def _looks_like_toc_heading(line: str) -> bool:
    normalized = line.strip().lower()
    return normalized in {"目录", "目 录", "contents", "table of contents"} or normalized.startswith("目录 ")


def _looks_like_section_title(title: str) -> bool:
    title = _strip_toc_leader(_normalize_spaces(title))
    if len(title) < 2 or len(title) > 140:
        return False
    patterns = [
        r"^第\s*[一二三四五六七八九十百千万零〇\d]+\s*[章节篇部]",
        r"^Chapter\s+\d+",
        r"^\d+(?:\.\d+)*\s+[\w\u4e00-\u9fff]",
    ]
    return any(re.search(pattern, title, flags=re.IGNORECASE) for pattern in patterns)


def _toc_level(title: str) -> int:
    title = _normalize_spaces(title)
    if re.match(r"^第\s*[一二三四五六七八九十百千万零〇\d]+\s*[篇部章]", title):
        return 1
    if re.match(r"^第\s*[一二三四五六七八九十百千万零〇\d]+\s*节", title):
        return 2
    match = re.match(r"^(\d+(?:\.\d+)*)\b", title)
    if match:
        return min(6, match.group(1).count(".") + 1)
    if re.match(r"^Chapter\s+\d+", title, flags=re.IGNORECASE):
        return 1
    return 1


def _strip_toc_leader(title: str) -> str:
    title = re.sub(r"\.{2,}|…{2,}", " ", title)
    return _normalize_spaces(title).strip(".·- ")


def _title_hint(text: str) -> str | None:
    for line in _clean_lines(text)[:8]:
        if _looks_like_toc_heading(line):
            return line
        if _looks_like_section_title(line):
            return _strip_toc_leader(line)[:140]
    first = next(iter(_clean_lines(text)), "")
    return first[:140] if first else None


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)
