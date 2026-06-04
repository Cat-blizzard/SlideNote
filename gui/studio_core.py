from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROVIDER_ENV_KEYS: dict[str, tuple[str, ...]] = {
    "openai": ("OPENAI_API_KEY",),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "qwen": ("QWEN_API_KEY", "DASHSCOPE_API_KEY"),
    "doubao": ("DOUBAO_API_KEY", "ARK_API_KEY", "VOLCENGINE_API_KEY"),
    "glm": ("GLM_API_KEY", "ZAI_API_KEY", "ZHIPUAI_API_KEY"),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "claude": ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY"),
}

DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-4.1-mini",
    "deepseek": "deepseek-v4-flash",
    "qwen": "qwen-plus",
    "doubao": "",
    "glm": "glm-5.1",
    "gemini": "gemini-3-flash-preview",
    "claude": "claude-sonnet-4-20250514",
}

VISION_DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-4.1-mini",
    "qwen": "qwen-vl-plus",
    "doubao": "",
    "gemini": "gemini-3-flash-preview",
    "claude": "claude-sonnet-4-20250514",
}

SAFE_OUTPUT_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


@dataclass(slots=True)
class StudioConfig:
    input_path: Path
    output_dir: Path
    progress_json: Path
    preset: str = "lecture"
    speed_mode: str = "quality"
    concurrency: int = 1
    llm_concurrency: int | None = None
    vision_concurrency: int | None = None
    ocr_concurrency: int | None = None
    figure_concurrency: int | None = None
    global_cache_dir: Path | None = None
    refresh_pages: str | None = None
    use_llm: bool = True
    provider: str = "deepseek"
    model: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    max_output_tokens: int | None = None
    temperature: float | None = None
    content_guard: str = "auto"
    note_context: str = "section"
    note_style: str = "article"
    note_language: str = "zh"
    term_policy: str = "bilingual"
    note_strategy: str = "lecture-weave"
    note_depth: str = "very-detailed"
    weave_dedup: str = "normal"
    page_neighborhood: int = 1
    deck_brief: str = "auto"
    section_detection: str = "auto"
    section_cache: str = "on"
    cache: str = "on"
    ocr: str = "auto"
    ocr_provider: str = "baidu"
    ocr_api_key: str | None = None
    ocr_secret_key: str | None = None
    ocr_language: str = "CHN_ENG"
    ocr_cache: str = "on"
    ocr_max_targets: int | None = None
    ocr_max_edge: int | None = None
    vision: str = "auto"
    vision_provider: str = "qwen"
    vision_model: str | None = None
    vision_api_key: str | None = None
    vision_base_url: str | None = None
    vision_cache: str = "on"
    vision_max_targets: int | None = None
    vision_max_edge: int | None = None
    vision_detail: str | None = "low"
    vision_max_output_tokens: int | None = None
    figure_crop: str = "auto"
    figure_max_targets: int | None = None
    figure_grounding: str = "auto"
    figure_audit: str = "local"
    composite_figures: str = "auto"
    image_ranking: str = "local"
    screenshot_policy: str = "fallback"
    source_display: str = "hidden"
    asset_mode: str = "bundle"
    review_mode: str = "off"
    exam_mode: str = "off"
    exam_question_count: int = 12
    export: str | None = None
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


def build_env(base_env: dict[str, str] | None, config: StudioConfig) -> dict[str, str]:
    env = dict(base_env or os.environ)
    if needs_text_api(config) and config.api_key:
        env[provider_env_key(config.provider)] = config.api_key
    if needs_vision_api(config) and config.vision_api_key:
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


def command_for_display(cmd: list[str]) -> str:
    redacted = []
    skip_redact_value = False
    secret_flags = {"--api-key", "--vision-api-key", "--ocr-api-key", "--ocr-secret-key"}
    for token in cmd:
        if skip_redact_value:
            redacted.append("***")
            skip_redact_value = False
            continue
        redacted.append(token)
        if token in secret_flags:
            skip_redact_value = True
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
