from __future__ import annotations

import io
import os
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from slidenote.llm import PROVIDERS as LLM_PROVIDERS
from slidenote.utils import as_float, as_int

# Provider metadata derives from slidenote.llm.ProviderSpec (single source of truth).
PROVIDER_ENV_KEYS: dict[str, tuple[str, ...]] = {name: spec.api_key_envs for name, spec in LLM_PROVIDERS.items()}

DEFAULT_TEXT_PROVIDER = "deepseek"
# Selectable text providers, default first, then slidenote.llm registry order.
TEXT_PROVIDERS: list[str] = sorted(LLM_PROVIDERS, key=lambda name: name != DEFAULT_TEXT_PROVIDER)

# Build caches are large, machine-local and not useful to share.
ZIP_EXCLUDED_DIRS = frozenset({".cache"})

SAFE_OUTPUT_RE = re.compile(r"[^a-zA-Z0-9_.-]+")

# The build and textbook-index CLIs only use Baidu OCR (no --ocr-provider flag);
# credentials are passed through these environment variables.
OCR_API_KEY_ENV = "BAIDU_OCR_API_KEY"
OCR_SECRET_KEY_ENV = "BAIDU_OCR_SECRET_KEY"


@dataclass(slots=True)
class StudioConfig:
    """GUI build settings. Only options the `slidenote build` CLI accepts are kept."""

    input_path: Path
    output_dir: Path
    progress_json: Path
    preset: str = "lecture"
    provider: str = "deepseek"
    api_key: str | None = None
    ocr: str = "auto"
    ocr_api_key: str | None = None
    ocr_secret_key: str | None = None
    vision: str = "auto"
    vision_provider: str = "qwen"
    vision_api_key: str | None = None
    export: str | None = None
    quiet: bool = True


@dataclass(slots=True)
class TextbookConfig:
    input_path: Path
    output_dir: Path
    ocr: str = "auto"
    ocr_api_key: str | None = None
    ocr_secret_key: str | None = None
    quiet: bool = True


def safe_run_name(filename: str) -> str:
    stem = Path(filename).stem.strip() or "slidenote"
    stem = SAFE_OUTPUT_RE.sub("_", stem).strip("._-") or "slidenote"
    return stem[:80]


def masked_key_status(value: str | None) -> str:
    if not value:
        return "not set"
    if len(value) <= 8:
        return "set"
    return f"{value[:4]}...{value[-4:]}"


def provider_env_key(provider: str) -> str:
    return PROVIDER_ENV_KEYS.get(provider, (f"{provider.upper()}_API_KEY",))[0]


def needs_vision_api(config: StudioConfig) -> bool:
    return config.preset == "lecture" and config.vision != "off"


def needs_text_api(config: StudioConfig) -> bool:
    return config.preset == "lecture"


def build_env(base_env: dict[str, str] | None, config: StudioConfig | TextbookConfig) -> dict[str, str]:
    env = dict(os.environ if base_env is None else base_env)
    if isinstance(config, StudioConfig):
        text_env = None
        if needs_text_api(config) and config.api_key:
            text_env = provider_env_key(config.provider)
            env[text_env] = config.api_key
        if needs_vision_api(config) and config.vision_api_key:
            vision_env = provider_env_key(config.vision_provider)
            # Same provider for text and vision shares one env var: keep the text key.
            if vision_env != text_env:
                env[vision_env] = config.vision_api_key
    if config.ocr != "off":
        if config.ocr_api_key:
            env[OCR_API_KEY_ENV] = config.ocr_api_key
        if config.ocr_secret_key:
            env[OCR_SECRET_KEY_ENV] = config.ocr_secret_key
    return env


def build_slidenote_command(config: StudioConfig) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "slidenote",
        "build",
        str(config.input_path),
        "--out",
        str(config.output_dir),
        "--progress-json",
        str(config.progress_json),
        "--preset",
        config.preset,
        "--provider",
        config.provider,
        "--vision",
        config.vision,
        "--ocr",
        config.ocr,
    ]
    if config.quiet:
        cmd.append("--quiet")
    if config.export:
        cmd.extend(["--export", config.export])
    return cmd


def build_study_pack_command(output_dir: Path, question_count: int = 12, quiet: bool = True) -> list[str]:
    cmd = [sys.executable, "-m", "slidenote", "study-pack", str(output_dir), "--question-count", str(max(1, int(question_count)))]
    if quiet:
        cmd.append("--quiet")
    return cmd


def build_textbook_command(config: TextbookConfig) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "slidenote",
        "textbook-index",
        str(config.input_path),
        "--out",
        str(config.output_dir),
        "--ocr",
        config.ocr,
    ]
    if config.quiet:
        cmd.append("--quiet")
    return cmd


def command_for_display(cmd: list[str]) -> str:
    redacted: list[str] = []
    redact_next = False
    secret_flags = {"--api-key", "--vision-api-key", "--ocr-api-key", "--ocr-secret-key"}
    for token in cmd:
        if redact_next:
            redacted.append("***")
            redact_next = False
            continue
        redacted.append(token)
        redact_next = token in secret_flags
    return " ".join(redacted)


def performance_tips(config: StudioConfig) -> list[str]:
    tips: list[str] = []
    if config.preset == "lecture":
        tips.append("Lecture preset uses the strongest default pipeline and expects provider API keys.")
    if config.vision == "off":
        tips.append("Vision is off, so image-heavy slides may lose diagram explanations.")
    if config.preset == "local":
        tips.append("Local preset avoids API calls and is best for parsing checks or offline drafts.")
    return tips


def progress_percent(progress: dict[str, Any]) -> float:
    if progress.get("status") == "complete":
        return 1.0
    planned = progress.get("planned_stages")
    if not isinstance(planned, list) or not planned:
        # No stage plan yet (build just started): progress is indeterminate.
        return 0.02
    current = progress.get("current_stage") or {}
    completed = len(progress.get("stages") or [])
    total_stages = len(planned)
    base = min(completed / total_stages, 0.95)
    stage_total = current.get("total") or 0
    stage_current = current.get("current") or 0
    if stage_total:
        base = min((completed + min(stage_current / stage_total, 1.0)) / total_stages, 0.98)
    return max(base, 0.02)


def discover_outputs(output_dir: Path) -> dict[str, Path]:
    names = {
        "notes": "notes.md",
        "notes_zip": "notes.zip",
        "notes_toc": "notes.toc.md",
        "docx": "notes.docx",
        "pdf": "notes.pdf",
        "latex": "notes.tex",
        "coverage": "coverage.md",
        "cost_markdown": "cost_report.md",
        "cost_json": "cost_report.json",
        "dashboard": "cost_dashboard.html",
        "run_summary": "run_summary.json",
        "llm_usage": "llm_usage.json",
        "vision_usage": "vision_usage.json",
        "ocr_usage": "ocr_usage.json",
        "content": "content.json",
        "progress": "progress.json",
        "study_pack": "study_pack.json",
        "review": "review.md",
        "exam": "exam.md",
        "exam_json": "exam.json",
        "exam_html": "exam.html",
    }
    return {key: output_dir / filename for key, filename in names.items() if (output_dir / filename).exists()}


def discover_textbook_outputs(output_dir: Path) -> dict[str, Path]:
    names = {
        "manifest": "textbook_manifest.json",
        "pages": "textbook_pages.jsonl",
        "toc": "textbook_toc.json",
        "sections": "textbook_sections.json",
        "chunks": "textbook_chunks.jsonl",
        "index": "textbook_index.json",
        "report": "textbook_report.md",
        "ocr_usage": "ocr_usage.json",
    }
    return {key: output_dir / filename for key, filename in names.items() if (output_dir / filename).exists()}


def _zip_members(output_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in output_dir.rglob("*")
        if path.is_file() and not ZIP_EXCLUDED_DIRS.intersection(path.relative_to(output_dir).parts)
    )


def output_signature(output_dir: Path) -> tuple[tuple[str, int, int], ...]:
    """Cheap fingerprint of the files `zip_output_dir` would package."""
    signature = []
    for path in _zip_members(output_dir):
        stat = path.stat()
        signature.append((path.relative_to(output_dir).as_posix(), stat.st_size, stat.st_mtime_ns))
    return tuple(signature)


def zip_output_dir(output_dir: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in _zip_members(output_dir):
            archive.write(path, path.relative_to(output_dir).as_posix())
    return buffer.getvalue()


def coverage_summary(coverage: dict[str, Any]) -> dict[str, Any]:
    """Visible (prose) coverage metrics from coverage.json, falling back to trace totals for old reports."""
    visible = coverage.get("visible_coverage")
    source = visible if isinstance(visible, dict) else coverage
    required = coverage.get("required_visible_coverage")
    required = required if isinstance(required, dict) else {}
    return {
        "total": as_int(source.get("total")),
        "covered": as_int(source.get("covered")),
        "missing": as_int(source.get("missing")),
        "ratio": as_float(source.get("coverage_ratio"), 1.0),
        "visible": isinstance(visible, dict),
        "trace_ratio": as_float(coverage.get("coverage_ratio"), 1.0),
        "required_total": as_int(required.get("total")),
        "required_missing": as_int(required.get("missing")),
    }


def slide_id_from_element(element_id: str | None) -> int | None:
    if not element_id:
        return None
    match = re.match(r"s(\d+)_", str(element_id))
    return int(match.group(1)) if match else None


def coverage_missing_items(coverage: dict[str, Any], limit: int = 200) -> list[dict[str, Any]]:
    """Elements not explained in visible prose (the coverage.json `items` schema), required ones first."""
    items = [item for item in coverage.get("items") or [] if isinstance(item, dict)]
    has_visible = any("visible_covered" in item for item in items)
    rows: list[dict[str, Any]] = []
    for item in items:
        required = bool(item.get("required"))
        if has_visible:
            # Structural pages are exempt from prose coverage unless an item is required.
            if item.get("visible_covered") or (item.get("structural") and not required):
                continue
        elif item.get("covered"):
            continue
        if required:
            reason = "required, not explained in prose"
        elif not item.get("trace_covered", item.get("covered")):
            reason = "not referenced in notes"
        else:
            reason = "source marker only, no prose explanation"
        element_id = item.get("id") or item.get("element_id")
        rows.append(
            {
                "slide_id": item.get("slide_id") or slide_id_from_element(element_id),
                "element_id": element_id,
                "kind": item.get("kind") or item.get("type") or "",
                "required": required,
                "reason": reason,
            }
        )
    rows.sort(key=lambda row: (not row["required"], as_int(row["slide_id"])))
    return rows[:limit]


def format_cost(value: Any, currency: str | None = "USD") -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "not recorded"
    return f"{amount:.6f} {currency or 'USD'}"


def format_count(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "—"
