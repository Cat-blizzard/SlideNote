from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from slidenote.llm import LLMClient, resolve_provider_runtime
from slidenote.llm_cache import LLM_CACHE_SCHEMA_VERSION, LLMCache, make_cache_key, sha256_text, stable_json, utc_now_iso
from slidenote.models import Deck, ImageAsset, SlidePage, TableBlock, TextBlock
from slidenote.table_understanding import table_preview
from slidenote.utils import (
    as_float,
    display_path,
    looks_like_outline_page,
    parse_json_object,
    preview,
)


CONTENT_GUARD_MODES = {"auto", "off"}
CONTENT_GUARD_PROMPT_VERSION = "content-guard-v1"
CONTENT_REPAIR_PROMPT_VERSION = "content-repair-v2"
REQUIRED_CONFIDENCE_THRESHOLD = 0.7

_STRUCTURAL_TITLES = {"目录", "课程目录", "本章目录", "章节导航", "contents", "outline", "agenda"}
_STRUCTURAL_LABELS = {"目录", "课程目录", "本章目录", "章节导航", "contents", "outline"}
_COVER_MARKERS = {"讲师", "教师", "教授", "联系邮箱", "邮箱", "主页", "email", "homepage", "http", "www"}
_DEFINITION_SIGNALS = ["定义", "称为", "是指", "definition", "means", "called"]
_CONDITION_SIGNALS = ["如果", "只有", "必须", "条件", "当且仅当", "because", "if ", "when ", "only if", "must"]
# "当" alone matches 当前/相当/应当; require the conditional "当……时" shape.
_CONDITION_PATTERNS = [re.compile(r"(?:^|[，,。；;：:\s])当(?![前今下中年天地然作做成即场])[^，,。；;]{1,40}?时")]
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", flags=re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"</?[A-Za-z][A-Za-z0-9-]*(?:\s+[A-Za-z_:][\w:.-]*\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s\"'<>]+))*\s*/?>")
_FORMULA_OPERAND = r"(?:\b[A-Za-z]\w{0,2}(?:\([^()]{0,20}\))?|\b\d+(?:\.\d+)?|\))"
_FORMULA_RELATION_RE = re.compile(
    rf"{_FORMULA_OPERAND}\s*(?:==|<=|>=|!=|=|<|>)\s*[-+]?(?:[A-Za-z]\w{{0,2}}\b|\d|\()"
)


@dataclass(frozen=True, slots=True)
class GuardCandidate:
    element_id: str
    slide_id: int
    kind: str
    preview: str
    local_role: str
    must_explain: bool
    confidence: float
    reason: str


def build_content_guard(
    deck: Deck,
    output_root: Path,
    mode: str = "auto",
    use_llm: bool = False,
    provider: str = "openai",
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    cache_mode: str = "on",
    cache_dir: Path | None = None,
    max_output_tokens: int = 1800,
    temperature: float | None = 0.0,
) -> dict[str, Any] | None:
    if mode == "off":
        return None
    if mode not in CONTENT_GUARD_MODES:
        raise ValueError(f"content guard mode must be one of: {', '.join(sorted(CONTENT_GUARD_MODES))}")

    candidates = _local_candidates(deck)
    local_report = _build_local_report(deck, candidates, mode=mode)
    if not use_llm or not candidates:
        local_report["classifier"] = "local"
        return local_report

    runtime = resolve_provider_runtime(provider, model=model, base_url=base_url)
    resolved_cache_dir = (cache_dir or (output_root / ".cache" / "llm")).resolve()
    cache = LLMCache(resolved_cache_dir, mode=cache_mode)
    prompt = _classification_prompt(deck, candidates)
    cache_key_payload = {
        "schema_version": LLM_CACHE_SCHEMA_VERSION,
        "prompt_version": CONTENT_GUARD_PROMPT_VERSION,
        "provider": runtime["provider"],
        "model": runtime["model"],
        "base_url": runtime["base_url"],
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "candidate_hash": sha256_text(stable_json([_item_record(candidate) for candidate in candidates])),
        "prompt_hash": sha256_text(prompt),
    }
    cache_key = make_cache_key(cache_key_payload)
    cache_path = cache.path_for(cache_key)
    cached = cache.read(cache_key)
    usage: dict[str, Any] = {}
    cache_status = "local_hit"
    llm_call = False
    raw_text = None
    if cached:
        raw_text = str(cached.get("output_text") or "")
        usage = cached.get("response_usage") or {}
    else:
        client = LLMClient(
            provider=str(runtime["provider"]),
            model=str(runtime["model"]),
            api_key=api_key,
            base_url=runtime["base_url"],
            max_output_tokens=max_output_tokens,
            temperature=temperature,
        )
        result = client.generate_with_usage(prompt)
        raw_text = result.text
        usage = result.usage or {}
        cache_status = "disabled" if cache_mode == "off" else "refresh" if cache_mode == "refresh" else "miss"
        llm_call = True
        written_path = cache.write(
            cache_key,
            {
                "provider": runtime["provider"],
                "model": runtime["model"],
                "base_url": runtime["base_url"],
                "prompt_version": CONTENT_GUARD_PROMPT_VERSION,
                "source_path": deck.source_path,
                "output_text": raw_text,
                "response_usage": usage,
            },
        )
        if written_path is not None:
            cache_path = written_path

    parsed = _parse_guard_json(raw_text)
    if parsed is None:
        local_report["classifier"] = "local_fallback"
        local_report.setdefault("warnings", []).append("content_guard_llm_output_not_json")
        local_report["llm"] = _llm_record(runtime, cache_key, cache_path, output_root, cache_status, llm_call, usage)
        return local_report

    report = _merge_llm_report(deck, candidates, parsed, mode=mode)
    report["classifier"] = "llm"
    report["llm"] = _llm_record(runtime, cache_key, cache_path, output_root, cache_status, llm_call, usage)
    return report


def learning_items_for_page(report: dict[str, Any] | None, slide_id: int) -> list[dict[str, Any]]:
    if not report:
        return []
    for page in report.get("pages", []):
        if page.get("slide_id") == slide_id:
            return [
                {
                    "element_id": item.get("element_id"),
                    "learning_role": item.get("learning_role"),
                    "must_explain": item.get("must_explain"),
                    "confidence": item.get("confidence"),
                    "reason": item.get("reason"),
                }
                for item in page.get("items", [])
            ]
    return []


def page_role_for_slide(report: dict[str, Any] | None, slide_id: int) -> str | None:
    if not report:
        return None
    for page in report.get("pages", []):
        if page.get("slide_id") == slide_id:
            role = str(page.get("page_role") or "")
            return role if role else None
    return None


def structural_slide_ids(report: dict[str, Any] | None) -> set[int] | None:
    if not report:
        return None
    return {
        int(page["slide_id"])
        for page in report.get("pages", [])
        if page.get("page_role") == "structural"
    }


def is_required_item(item: dict[str, Any], confidence_threshold: float = REQUIRED_CONFIDENCE_THRESHOLD) -> bool:
    """Single rule for "must be explained": flagged must_explain AND confident enough."""
    return bool(item.get("must_explain")) and as_float(item.get("confidence"), 0.0) >= confidence_threshold


def required_item_ids(report: dict[str, Any] | None, confidence_threshold: float = REQUIRED_CONFIDENCE_THRESHOLD) -> set[str]:
    if not report:
        return set()
    return {str(item.get("element_id")) for item in report.get("items", []) if is_required_item(item, confidence_threshold)}


def required_items_for_slides(
    report: dict[str, Any] | None,
    slide_ids: set[int] | None = None,
    confidence_threshold: float = REQUIRED_CONFIDENCE_THRESHOLD,
) -> list[dict[str, Any]]:
    if not report:
        return []
    result: list[dict[str, Any]] = []
    for item in report.get("items", []):
        slide_id = int(item.get("slide_id") or 0)
        if slide_ids is not None and slide_id not in slide_ids:
            continue
        if is_required_item(item, confidence_threshold):
            result.append(item)
    return result


def missing_required_items(
    report: dict[str, Any] | None,
    coverage: dict[str, Any],
    confidence_threshold: float = REQUIRED_CONFIDENCE_THRESHOLD,
) -> list[dict[str, Any]]:
    if not report:
        return []
    required_coverage = coverage.get("required_visible_coverage")
    if not isinstance(required_coverage, dict):
        return []
    missing_records = required_coverage.get("missing_items")
    if not isinstance(missing_records, list):
        return []
    missing_ids = {
        str(item.get("id"))
        for item in missing_records
        if isinstance(item, dict) and item.get("id")
    }
    if not missing_ids:
        return []
    coverage_by_id = {
        str(item.get("id")): item
        for item in missing_records
        if isinstance(item, dict) and item.get("id")
    }
    result: list[dict[str, Any]] = []
    for item in required_items_for_slides(report, confidence_threshold=confidence_threshold):
        element_id = str(item.get("element_id") or "")
        if element_id not in missing_ids:
            continue
        merged = dict(item)
        coverage_record = coverage_by_id.get(element_id)
        if coverage_record:
            merged["coverage"] = coverage_record
        result.append(merged)
    return result


def record_required_coverage(report: dict[str, Any], coverage: dict[str, Any], stage: str = "final") -> None:
    report.setdefault("coverage", {})[stage] = coverage.get("required_visible_coverage") or {}
    required_missing = int((coverage.get("required_visible_coverage") or {}).get("missing", 0))
    summary = report.setdefault("summary", {})
    summary["required_missing"] = required_missing
    if stage == "final":
        summary["residual_risks"] = required_missing


def record_repair(report: dict[str, Any], record: dict[str, Any]) -> None:
    repairs = report.setdefault("repairs", [])
    repairs.append(record)
    summary = report.setdefault("summary", {})
    summary["repair_attempts"] = len(repairs)
    summary["repair_rejections"] = sum(repair.get("accepted") is False for repair in repairs)
    if record.get("unresolved_items"):
        report["summary"]["residual_risks"] = int(report["summary"].get("residual_risks", 0)) + len(record["unresolved_items"])


def content_guard_warnings(report: dict[str, Any] | None) -> list[str]:
    if not report:
        return []
    warnings = list(report.get("warnings") or [])
    required_missing = int(report.get("summary", {}).get("required_missing") or 0)
    residual = int(report.get("summary", {}).get("residual_risks") or 0)
    if required_missing:
        warnings.append(f"content_guard_required_missing:{required_missing}")
    if residual:
        warnings.append(f"content_guard_residual_risks:{residual}")
    rejected = int(report.get("summary", {}).get("repair_rejections") or 0)
    if rejected:
        warnings.append(f"content_guard_repair_rejected:{rejected}")
    return warnings


def _local_candidates(deck: Deck) -> list[GuardCandidate]:
    candidates: list[GuardCandidate] = []
    for page in deck.pages:
        for block in page.text_blocks:
            candidate = _text_candidate(page, block)
            if candidate is not None:
                candidates.append(candidate)
        for table in page.tables:
            candidate = _table_candidate(page, table)
            if candidate is not None:
                candidates.append(candidate)
        for image in page.images:
            candidate = _image_candidate(page, image)
            if candidate is not None:
                candidates.append(candidate)
    return candidates


def _text_candidate(page: SlidePage, block: TextBlock) -> GuardCandidate | None:
    text = " ".join(block.content.split())
    if not text:
        return None
    # Keyword signals ignore URLs ("example.com", "?if=") so links are not learning content.
    signal_text = _URL_RE.sub(" ", text)
    role = "concept"
    confidence = 0.45
    must = False
    reasons: list[str] = []
    if block.type in {"title", "heading"}:
        reasons.append("heading")
        confidence = 0.48
    if _looks_like_formula(text):
        role = "formula"
        confidence = 0.84
        must = True
        reasons.append("formula_like")
    elif _contains_any(signal_text, _DEFINITION_SIGNALS):
        role = "definition"
        confidence = 0.78
        must = True
        reasons.append("definition_signal")
    elif _contains_any(signal_text, _CONDITION_SIGNALS) or any(pattern.search(signal_text) for pattern in _CONDITION_PATTERNS):
        role = "condition"
        confidence = 0.74
        must = True
        reasons.append("condition_signal")
    elif _contains_any(signal_text, ["例如", "案例", "example", "e.g."]):
        role = "example"
        confidence = 0.68
        must = True
        reasons.append("example_signal")
    elif len(text) >= 120 and not _looks_like_structural_text(text):
        role = "concept"
        confidence = 0.7
        must = True
        reasons.append("long_content_text")
    if _looks_like_structural_text(text) and not must:
        role = "structural"
        confidence = 0.72
        reasons.append("structural_text")
    if not must and role not in {"structural"}:
        return None
    return GuardCandidate(block.id, page.slide_id, f"text:{block.type}", preview(text, limit=220), role, must, confidence, ";".join(reasons))


def _table_candidate(page: SlidePage, table: TableBlock) -> GuardCandidate | None:
    table_text = table_preview(table, raw_rows=3)
    if not table_text.strip():
        return None
    return GuardCandidate(table.id, page.slide_id, "table", preview(table_text, limit=220), "table_conclusion", True, 0.82, "table")


def _image_candidate(page: SlidePage, image: ImageAsset) -> GuardCandidate | None:
    if image.ignored and image.role != "composite_child":
        return None
    text = " ".join(part for part in [image.caption, image.figure_explanation, image.visual_summary, image.ocr_text] if part)
    role = "figure_explanation"
    must = False
    confidence = 0.5
    reasons: list[str] = []
    if image.role in {"figure_crop", "composite_figure"}:
        must = True
        confidence = 0.8
        reasons.append(str(image.role))
    if image.figure_explanation or image.visual_summary:
        must = True
        confidence = max(confidence, 0.78)
        reasons.append("has_visual_summary")
    if image.ocr_text and len(image.ocr_text.strip()) >= 40:
        must = True
        confidence = max(confidence, 0.76)
        role = "ocr_key_text"
        reasons.append("long_image_ocr")
    if not must:
        return None
    return GuardCandidate(image.id, page.slide_id, "image", preview(text or image.path, limit=220), role, must, confidence, ";".join(reasons))


def _build_local_report(deck: Deck, candidates: list[GuardCandidate], mode: str) -> dict[str, Any]:
    items = [_item_record(candidate) for candidate in candidates]
    by_slide: dict[int, list[dict[str, Any]]] = {}
    for item in items:
        by_slide.setdefault(int(item["slide_id"]), []).append(item)
    pages = []
    for index, page in enumerate(deck.pages):
        page_items = by_slide.get(page.slide_id, [])
        structural = looks_like_structural_page(page, index)
        has_required = any(item.get("must_explain") for item in page_items)
        page_role = "mixed" if structural and has_required else "structural" if structural else "content"
        pages.append(
            {
                "slide_id": page.slide_id,
                "title": page.title,
                "page_role": page_role,
                "items": page_items,
            }
        )
    return {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "mode": mode,
        "classifier": "local",
        "prompt_version": CONTENT_GUARD_PROMPT_VERSION,
        "required_confidence_threshold": REQUIRED_CONFIDENCE_THRESHOLD,
        "summary": _summary(pages, items),
        "pages": pages,
        "items": items,
        "repairs": [],
        "warnings": [],
    }


def _merge_llm_report(deck: Deck, candidates: list[GuardCandidate], parsed: dict[str, Any], mode: str) -> dict[str, Any]:
    candidate_by_id = {candidate.element_id: candidate for candidate in candidates}
    llm_items: dict[str, dict[str, Any]] = {}
    for page in parsed.get("pages", []):
        if not isinstance(page, dict):
            continue
        for item in page.get("items", []):
            if not isinstance(item, dict):
                continue
            element_id = str(item.get("element_id") or "")
            if element_id not in candidate_by_id:
                continue
            llm_items[element_id] = item
    items = []
    for candidate in candidates:
        source = llm_items.get(candidate.element_id) or {}
        items.append(
            {
                "element_id": candidate.element_id,
                "slide_id": candidate.slide_id,
                "kind": candidate.kind,
                "preview": candidate.preview,
                "learning_role": str(source.get("learning_role") or candidate.local_role),
                "must_explain": _as_bool(source.get("must_explain"), candidate.must_explain),
                "confidence": round(as_float(source.get("confidence"), candidate.confidence), 3),
                "reason": str(source.get("reason") or candidate.reason),
                "local_role": candidate.local_role,
                "local_reason": candidate.reason,
            }
        )
    by_slide: dict[int, list[dict[str, Any]]] = {}
    for item in items:
        by_slide.setdefault(int(item["slide_id"]), []).append(item)
    page_role_by_slide = _page_roles_from_llm(parsed)
    pages = []
    for index, page in enumerate(deck.pages):
        page_items = by_slide.get(page.slide_id, [])
        local_structural = looks_like_structural_page(page, index)
        has_required = any(is_required_item(item) for item in page_items)
        role = page_role_by_slide.get(page.slide_id)
        if role not in {"structural", "content", "mixed"}:
            role = "mixed" if local_structural and has_required else "structural" if local_structural else "content"
        if role == "structural" and has_required:
            role = "mixed"
        pages.append({"slide_id": page.slide_id, "title": page.title, "page_role": role, "items": page_items})
    return {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "mode": mode,
        "classifier": "llm",
        "prompt_version": CONTENT_GUARD_PROMPT_VERSION,
        "required_confidence_threshold": REQUIRED_CONFIDENCE_THRESHOLD,
        "summary": _summary(pages, items),
        "pages": pages,
        "items": items,
        "repairs": [],
        "warnings": [],
    }


def _classification_prompt(deck: Deck, candidates: list[GuardCandidate]) -> str:
    payload = {
        "task": "classify_learning_items",
        "source_file": Path(deck.source_path).stem,
        "rules": {
            "page_role": ["structural", "content", "mixed"],
            "must_explain": "true only for content a student needs in visible prose; false for pure navigation, duplicates, decoration, contact info",
            "confidence": "0.0-1.0",
        },
        "candidates": [_item_record(candidate) for candidate in candidates],
    }
    return (
        "You are a course-note quality classifier. Return strict JSON only, no Markdown.\n"
        "Classify which source elements contain independent learning value that must be explained in visible prose.\n"
        "Do not require cover pages, tables of contents, navigation labels, contact info, or repeated decorative fragments.\n"
        "If a page is mostly structural but includes a real definition, condition, formula, table conclusion, OCR key text, or figure explanation, mark page_role=mixed and mark that element must_explain=true.\n"
        "JSON schema: {\"pages\":[{\"slide_id\":1,\"page_role\":\"structural|content|mixed\",\"items\":[{\"element_id\":\"s1_t1\",\"learning_role\":\"definition|condition|formula|table_conclusion|figure_explanation|ocr_key_text|concept|example|structural|decorative|repeated\",\"must_explain\":true,\"confidence\":0.0,\"reason\":\"short reason\"}]}]}.\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _parse_guard_json(text: str) -> dict[str, Any] | None:
    parsed = parse_json_object(text)
    if parsed is None or not isinstance(parsed.get("pages"), list):
        return None
    return parsed


def _page_roles_from_llm(parsed: dict[str, Any]) -> dict[int, str]:
    result: dict[int, str] = {}
    for page in parsed.get("pages", []):
        if not isinstance(page, dict):
            continue
        try:
            slide_id = int(page.get("slide_id"))
        except (TypeError, ValueError):
            continue
        result[slide_id] = str(page.get("page_role") or "")
    return result


def _item_record(candidate: GuardCandidate) -> dict[str, Any]:
    return {
        "element_id": candidate.element_id,
        "slide_id": candidate.slide_id,
        "kind": candidate.kind,
        "preview": candidate.preview,
        "learning_role": candidate.local_role,
        "must_explain": candidate.must_explain,
        "confidence": round(candidate.confidence, 3),
        "reason": candidate.reason,
    }


def _summary(pages: list[dict[str, Any]], items: list[dict[str, Any]]) -> dict[str, Any]:
    required = [item for item in items if is_required_item(item)]
    return {
        "pages_total": len(pages),
        "structural_pages": sum(1 for page in pages if page.get("page_role") == "structural"),
        "mixed_pages": sum(1 for page in pages if page.get("page_role") == "mixed"),
        "content_pages": sum(1 for page in pages if page.get("page_role") == "content"),
        "candidate_items": len(items),
        "required_items": len(required),
        "repair_attempts": 0,
        "required_missing": 0,
        "residual_risks": 0,
    }


def _llm_record(
    runtime: dict[str, Any],
    cache_key: str,
    cache_path: Path,
    output_root: Path,
    cache_status: str,
    llm_call: bool,
    usage: dict[str, Any],
) -> dict[str, Any]:
    return {
        "provider": runtime["provider"],
        "model": runtime["model"],
        "base_url": runtime["base_url"],
        "cache_status": cache_status,
        "cache_key": cache_key,
        "cache_file": display_path(cache_path, output_root),
        "llm_call": llm_call,
        # Cache hits cost nothing this run; report zero like deck_brief/study_pack.
        "input_tokens": usage.get("input_tokens") if llm_call else 0,
        "output_tokens": usage.get("output_tokens") if llm_call else 0,
        "total_tokens": usage.get("total_tokens") if llm_call else 0,
        "provider_cached_input_tokens": usage.get("provider_cached_input_tokens") if llm_call else 0,
    }


def looks_like_structural_page(page: SlidePage, index: int) -> bool:
    """Shared structural-page heuristic (cover, TOC, navigation); also used by coverage."""
    title = page.title or ""
    text = "\n".join([title, *(block.content for block in page.text_blocks)])
    if normalize_text_key(title) in _STRUCTURAL_TITLES:
        return True
    if index == 0 and any(marker in normalize_text_key(text) for marker in _COVER_MARKERS):
        return True
    if any(normalize_text_key(line) in _STRUCTURAL_LABELS for line in text.splitlines()[:4]):
        return True
    return looks_like_outline_page(text)


def _looks_like_structural_text(text: str) -> bool:
    return normalize_text_key(text) in _STRUCTURAL_TITLES or looks_like_outline_page(text)


def _looks_like_formula(text: str) -> bool:
    # URLs carry "=" in query strings and HTML tags carry "<>"; neither is math.
    text = _HTML_TAG_RE.sub(" ", _URL_RE.sub(" ", text))
    if re.search(r"[≤≥≠≈∑∫∀∃]|\\frac|\\sum|\\forall|\\int|\bO\([^)]+\)", text):
        return True
    # A relational operator between short math-like operands: "x = 3", "a+b<c", "f(x) >= 0".
    return bool(_FORMULA_RELATION_RE.search(text))


def _contains_any(text: str, needles: list[str]) -> bool:
    lowered = text.lower()
    return any(needle.lower() in lowered for needle in needles)


def normalize_text_key(value: str) -> str:
    return re.sub(r"[\s:：,，.。;；、\-_（）()<>]+", "", value).lower()


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return default
