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
    speed_mode: str = "fast"
    concurrency: int = 1
    llm_concurrency: int | None = None
    vision_concurrency: int | None = None
    ocr_concurrency: int | None = None
    figure_concurrency: int | None = None
    global_cache_dir: Path | None = None
    refresh_pages: str | None = None
    use_llm: bool = False
    provider: str = "deepseek"
    model: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    max_output_tokens: int | None = None
    temperature: float | None = None
    content_guard: str = "off"
    note_context: str = "section"
    note_style: str = "article"
    note_language: str = "zh"
    term_policy: str = "bilingual"
    note_strategy: str = "direct"
    note_depth: str = "concise"
    weave_dedup: str = "normal"
    page_neighborhood: int = 1
    deck_brief: str = "off"
    section_detection: str = "local"
    section_cache: str = "on"
    cache: str = "on"
    ocr: str = "off"
    ocr_provider: str = "baidu"
    ocr_api_key: str | None = None
    ocr_secret_key: str | None = None
    ocr_language: str = "CHN_ENG"
    ocr_cache: str = "on"
    ocr_max_targets: int | None = None
    ocr_max_edge: int | None = None
    vision: str = "off"
    vision_provider: str = "qwen"
    vision_model: str | None = None
    vision_api_key: str | None = None
    vision_base_url: str | None = None
    vision_cache: str = "on"
    vision_max_targets: int | None = None
    vision_max_edge: int | None = None
    vision_detail: str | None = "low"
    vision_max_output_tokens: int | None = None
    figure_crop: str = "off"
    figure_max_targets: int | None = None
    figure_grounding: str = "auto"
    figure_audit: str = "local"
    composite_figures: str = "auto"
    image_ranking: str = "local"
    screenshot_policy: str = "fallback"
    source_display: str = "hidden"
    asset_mode: str = "bundle"
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
    return config.vision != "off" or config.figure_crop == "vision" or config.figure_grounding == "vision"


def build_env(base_env: dict[str, str] | None, config: StudioConfig) -> dict[str, str]:
    env = dict(base_env or os.environ)
    if config.use_llm and config.api_key:
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
        "--speed-mode",
        config.speed_mode,
        "--concurrency",
        str(max(1, int(config.concurrency))),
        "--content-guard",
        config.content_guard,
        "--note-context",
        config.note_context,
        "--note-style",
        config.note_style,
        "--note-language",
        config.note_language,
        "--term-policy",
        config.term_policy,
        "--note-strategy",
        config.note_strategy,
        "--note-depth",
        config.note_depth,
        "--weave-dedup",
        config.weave_dedup,
        "--page-neighborhood",
        str(config.page_neighborhood),
        "--deck-brief",
        config.deck_brief,
        "--section-detection",
        config.section_detection,
        "--section-cache",
        config.section_cache,
        "--cache",
        config.cache,
        "--ocr",
        config.ocr,
        "--ocr-provider",
        config.ocr_provider,
        "--ocr-language",
        config.ocr_language,
        "--ocr-cache",
        config.ocr_cache,
        "--vision",
        config.vision,
        "--vision-provider",
        config.vision_provider,
        "--vision-cache",
        config.vision_cache,
        "--figure-crop",
        config.figure_crop,
        "--figure-grounding",
        config.figure_grounding,
        "--figure-audit",
        config.figure_audit,
        "--composite-figures",
        config.composite_figures,
        "--image-ranking",
        config.image_ranking,
        "--screenshot-policy",
        config.screenshot_policy,
        "--source-display",
        config.source_display,
        "--asset-mode",
        config.asset_mode,
    ]
    for flag, value in (
        ("--llm-concurrency", config.llm_concurrency),
        ("--vision-concurrency", config.vision_concurrency),
        ("--ocr-concurrency", config.ocr_concurrency),
        ("--figure-concurrency", config.figure_concurrency),
    ):
        if value is not None:
            cmd.extend([flag, str(max(1, int(value)))])
    if config.quiet:
        cmd.append("--quiet")
    if config.global_cache_dir:
        cmd.extend(["--global-cache-dir", str(config.global_cache_dir)])
    if config.refresh_pages:
        cmd.extend(["--refresh-pages", config.refresh_pages])
    if config.use_llm:
        cmd.append("--use-llm")
        cmd.extend(["--provider", config.provider])
        if config.model:
            cmd.extend(["--model", config.model])
        if config.base_url:
            cmd.extend(["--base-url", config.base_url])
    if config.max_output_tokens:
        cmd.extend(["--max-output-tokens", str(config.max_output_tokens)])
    if config.temperature is not None:
        cmd.extend(["--temperature", str(config.temperature)])
    if config.ocr_max_targets is not None:
        cmd.extend(["--ocr-max-targets", str(config.ocr_max_targets)])
    if config.ocr_max_edge is not None:
        cmd.extend(["--ocr-max-edge", str(config.ocr_max_edge)])
    if needs_vision_api(config):
        if config.vision_model:
            cmd.extend(["--vision-model", config.vision_model])
        if config.vision_base_url:
            cmd.extend(["--vision-base-url", config.vision_base_url])
    if config.vision_max_targets is not None:
        cmd.extend(["--vision-max-targets", str(config.vision_max_targets)])
    if config.vision_max_edge is not None:
        cmd.extend(["--vision-max-edge", str(config.vision_max_edge)])
    if config.vision_detail:
        cmd.extend(["--vision-detail", config.vision_detail])
    if config.vision_max_output_tokens is not None:
        cmd.extend(["--vision-max-output-tokens", str(config.vision_max_output_tokens)])
    if config.figure_max_targets is not None:
        cmd.extend(["--figure-max-targets", str(config.figure_max_targets)])
    if config.export:
        cmd.extend(["--export", config.export])
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
    if config.concurrency <= 1 and (config.use_llm or config.ocr != "off" or config.vision != "off"):
        tips.append("Increase concurrency to 3-6 for API stages if your provider rate limit allows it.")
    if config.vision == "all":
        tips.append("Vision=all is slow. Prefer vision=auto with a small max target count for normal lecture slides.")
    if config.ocr == "all":
        tips.append("OCR=all is slow. Prefer OCR=auto unless the file is a scanned PDF.")
    if config.note_strategy == "lecture-weave":
        tips.append("lecture-weave is higher quality but slower. Use direct for quick previews.")
    if config.deck_brief != "off":
        tips.append("Deck brief adds extra LLM calls. Turn it off for quick drafts.")
    if config.content_guard != "off":
        tips.append("Content guard improves coverage awareness but costs extra LLM calls.")
    if config.cache == "off" or config.vision_cache == "off" or config.ocr_cache == "off":
        tips.append("Keep caches on so repeated runs reuse previous API results.")
    if config.figure_crop == "vision":
        tips.append("Vision figure cropping is useful but adds API calls. Use off/auto for speed.")
    return tips


def progress_percent(progress: dict[str, Any]) -> float:
    current = progress.get("current_stage") or {}
    stages = progress.get("stages") or []
    completed = len(stages)
    total_known_stages = 12
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
    }
    return {key: output_dir / filename for key, filename in names.items() if (output_dir / filename).exists()}
