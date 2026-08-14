from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from slidenote.llm import PROVIDERS as LLM_PROVIDERS

# Provider metadata derives from slidenote.llm.ProviderSpec (single source of truth).
PROVIDER_ENV_KEYS: dict[str, tuple[str, ...]] = {name: spec.api_key_envs for name, spec in LLM_PROVIDERS.items()}

DEFAULT_MODELS: dict[str, str] = {name: (spec.default_model or "") for name, spec in LLM_PROVIDERS.items()}

VISION_DEFAULT_MODELS: dict[str, str] = {name: (spec.default_vision_model or "") for name, spec in LLM_PROVIDERS.items()}

SAFE_OUTPUT_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


@dataclass(slots=True)
class StudioConfig:
    """GUI-facing build configuration.

    Only the fields the GUI actually forwards to the `slidenote build` CLI
    (or injects via env) are kept. Defaults mirror slidenote/build/config.py
    QUALITY_BUILD_DEFAULTS so the GUI surface cannot silently drift.
    """

    input_path: Path
    output_dir: Path
    progress_json: Path
    preset: str = "lecture"
    provider: str = "deepseek"
    api_key: str | None = None
    vision: str = "auto"
    vision_provider: str = "qwen"
    vision_api_key: str | None = None
    ocr: str = "auto"
    ocr_provider: str = "baidu"
    ocr_api_key: str | None = None
    ocr_secret_key: str | None = None
    export: str | None = None
    quiet: bool = True


@dataclass(slots=True)
class TextbookConfig:
    input_path: Path
    output_dir: Path
    ocr: str = "auto"
    ocr_provider: str = "baidu"
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
    env = dict(base_env or os.environ)
    if isinstance(config, StudioConfig) and needs_text_api(config) and config.api_key:
        env[provider_env_key(config.provider)] = config.api_key
    if isinstance(config, StudioConfig) and needs_vision_api(config) and config.vision_api_key:
        env[provider_env_key(config.vision_provider)] = config.vision_api_key
    if config.ocr != "off" and config.ocr_api_key:
        env[f"{config.ocr_provider.upper()}_OCR_API_KEY"] = config.ocr_api_key
        if config.ocr_provider == "baidu":
            env["BAIDU_OCR_API_KEY"] = config.ocr_api_key
        if config.ocr_provider == "mathpix":
            env["MATHPIX_APP_ID"] = config.ocr_api_key
        if config.ocr_provider == "google":
            env["GOOGLE_VISION_API_KEY"] = config.ocr_api_key
    if config.ocr != "off" and config.ocr_secret_key:
        if config.ocr_provider == "baidu":
            env["BAIDU_OCR_SECRET_KEY"] = config.ocr_secret_key
        if config.ocr_provider == "mathpix":
            env["MATHPIX_APP_KEY"] = config.ocr_secret_key
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
    # Keys never travel through the command line (they are injected via env),
    # so no redaction is needed here.
    return " ".join(cmd)


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
    current = progress.get("current_stage") or {}
    stages = progress.get("stages") or []
    completed = len(stages)
    total_known_stages = 13
    base = min(completed / total_known_stages, 0.95)
    stage_total = current.get("total") or 0
    stage_current = current.get("current") or 0
    if stage_total:
        base = min((completed + min(stage_current / stage_total, 1.0)) / total_known_stages, 0.98)
    if progress.get("status") == "complete":
        return 1.0
    if progress.get("status") == "failed":
        return max(base, 0.02)
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
