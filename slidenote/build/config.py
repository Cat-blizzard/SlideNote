from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from slidenote.llm import get_provider_spec


def _friendly_build_error(exc: Exception, args: argparse.Namespace) -> str | None:
    message = str(exc)
    if "Missing API key for provider `" not in message:
        return None
    provider = _provider_from_missing_key_error(message)
    if not provider:
        return None
    try:
        missing_spec = get_provider_spec(provider)
    except ValueError:
        return None

    if _vision_features_enabled(args):
        try:
            vision_spec = get_provider_spec(args.vision_provider)
        except ValueError:
            vision_spec = None
        if vision_spec and missing_spec.canonical_name == vision_spec.canonical_name:
            envs = ", ".join(vision_spec.api_key_envs)
            features = []
            if args.vision != "off":
                features.append("vision")
            if args.figure_crop == "vision" or (args.figure_crop == "auto" and args.vision != "off"):
                features.append("figure-crop")
            if args.semantic_layout == "vision" or (args.semantic_layout == "auto" and args.vision != "off"):
                features.append("semantic-layout")
            if args.figure_grounding == "vision":
                features.append("figure-grounding")
            feature_text = "/".join(features) or "vision"
            return (
                f"当前启用了 {feature_text}，但视觉模型 provider `{vision_spec.canonical_name}` 没有可用 API key。\n"
                f"请设置环境变量：{envs}。\n"
                "如果暂时没有视觉 API key，请使用 `--vision off`；如果想完全离线生成，请使用 `--preset local`。"
            )

    if args.use_llm:
        try:
            text_spec = get_provider_spec(args.provider)
        except ValueError:
            text_spec = None
        if text_spec and missing_spec.canonical_name == text_spec.canonical_name:
            envs = ", ".join(text_spec.api_key_envs)
            return (
                f"当前默认的 `lecture` preset 会启用文本模型，但 provider `{text_spec.canonical_name}` 没有可用 API key。\n"
                f"请设置环境变量：{envs}。\n"
                "如果只想先生成本地规则草稿，请使用 `--preset local`。"
            )
    return None


def _provider_from_missing_key_error(message: str) -> str | None:
    marker = "Missing API key for provider `"
    start = message.find(marker)
    if start == -1:
        return None
    start += len(marker)
    end = message.find("`", start)
    if end == -1:
        return None
    return message[start:end]


def _vision_features_enabled(args: argparse.Namespace) -> bool:
    return (
        args.vision != "off"
        or args.semantic_layout == "vision"
        or (args.semantic_layout == "auto" and args.vision != "off")
        or args.figure_grounding == "vision"
        or args.figure_crop == "vision"
        or (args.figure_crop == "auto" and args.vision != "off")
    )


def _should_build_deck_brief(args: argparse.Namespace) -> bool:
    if args.deck_brief == "off":
        return False
    if args.deck_brief == "force":
        return True
    return bool(args.use_llm and args.note_strategy == "lecture-weave")


def _apply_note_profile_defaults(args: argparse.Namespace) -> None:
    if getattr(args, "note_depth", None) is None:
        if getattr(args, "note_profile", "auto") == "lecture-notes":
            args.note_depth = "very-detailed"
        else:
            args.note_depth = "detailed"


QUALITY_BUILD_DEFAULTS: dict[str, Any] = {
    "speed_mode": "quality",
    "concurrency": 3,
    "llm_concurrency": None,
    "vision_concurrency": None,
    "ocr_concurrency": None,
    "figure_concurrency": None,
    "global_cache_dir": None,
    "refresh_pages": None,
    "use_llm": True,
    "model": None,
    "api_key": None,
    "base_url": None,
    "max_output_tokens": 12000,
    "temperature": 0.2,
    "content_guard": "auto",
    "export_toc": "auto",
    "asset_mode": "bundle",
    "source_display": "hidden",
    "note_context": "section",
    "note_style": "article",
    "note_profile": "lecture-notes",
    "note_language": "zh",
    "term_policy": "bilingual",
    "note_strategy": "lecture-weave",
    "note_depth": None,
    "teaching_enrichment": "auto",
    "weave_dedup": "soft",
    "page_neighborhood": 1,
    "deck_brief": "auto",
    "screenshot_policy": "fallback",
    "section_detection": "auto",
    "semantic_layout": "auto",
    "section_cache": "on",
    "cache": "on",
    "cache_dir": None,
    "section_cache_dir": None,
    "ocr": "auto",
    "ocr_provider": "baidu",
    "ocr_api_key": None,
    "ocr_secret_key": None,
    "ocr_endpoint": None,
    "ocr_language": "CHN_ENG",
    "ocr_cache": "on",
    "ocr_cache_dir": None,
    "ocr_max_targets": 0,
    "ocr_min_text_chars": 80,
    "ocr_min_area": 120000,
    "ocr_max_edge": 2200,
    "figure_crop": "auto",
    "composite_figures": "auto",
    "figure_max_targets": 0,
    "figure_max_crops_per_page": 3,
    "figure_min_confidence": 0.45,
    "figure_min_area": 40000,
    "figure_cache": "on",
    "figure_cache_dir": None,
    "image_ranking": "local",
    "figure_grounding": "auto",
    "figure_placement": "inline",
    "figure_audit": "local",
    "vision_provider": "qwen",
    "vision_model": None,
    "vision_api_key": None,
    "vision_base_url": None,
    "vision_cache": "on",
    "vision_cache_dir": None,
    "vision_max_targets": 0,
    "vision_min_area": 120000,
    "vision_max_edge": 2200,
    "vision_max_output_tokens": 2400,
    "vision_temperature": 0.0,
    "vision_detail": "high",
}


BUILD_PRESET_DEFAULTS: dict[str, dict[str, Any]] = {
    "lecture": QUALITY_BUILD_DEFAULTS,
    "local": {
        **QUALITY_BUILD_DEFAULTS,
        "speed_mode": "fast",
        "concurrency": 1,
        "use_llm": False,
        "max_output_tokens": 2500,
        "content_guard": "off",
        "note_profile": "auto",
        "note_strategy": "direct",
        "note_depth": "balanced",
        "teaching_enrichment": "off",
        "deck_brief": "off",
        "section_detection": "local",
        "semantic_layout": "local",
        "ocr": "off",
        "vision": "off",
        "figure_crop": "off",
        "composite_figures": "auto",
        "figure_grounding": "auto",
    },
}


def _apply_build_preset_defaults(args: argparse.Namespace) -> None:
    preset = getattr(args, "preset", "lecture")
    preset_defaults = BUILD_PRESET_DEFAULTS[preset]
    explicit_options = set(getattr(args, "_explicit_options", set()) or set())
    for name, value in preset_defaults.items():
        if preset == "local" or name not in explicit_options:
            setattr(args, name, value)


def _apply_speed_mode_defaults(args: argparse.Namespace) -> None:
    presets = {
        "fast": {
            "max_output_tokens": 2500,
            "ocr_max_targets": 40,
            "ocr_max_edge": 1200,
            "figure_max_targets": 25,
            "vision_max_targets": 25,
            "vision_max_edge": 1000,
            "vision_max_output_tokens": 800,
            "vision_detail": "low",
        },
        "balanced": {
            "max_output_tokens": 4096,
            "ocr_max_targets": 120,
            "ocr_max_edge": 1800,
            "figure_max_targets": 80,
            "vision_max_targets": 80,
            "vision_max_edge": 1400,
            "vision_max_output_tokens": 1200,
            "vision_detail": "low",
        },
        "quality": {
            "max_output_tokens": 7000,
            "ocr_max_targets": 0,
            "ocr_max_edge": 2200,
            "figure_max_targets": 160,
            "vision_max_targets": 160,
            "vision_max_edge": 1800,
            "vision_max_output_tokens": 2000,
            "vision_detail": "high",
        },
        "debug": {
            "max_output_tokens": 4096,
            "ocr_max_targets": 20,
            "ocr_max_edge": 1400,
            "figure_max_targets": 20,
            "vision_max_targets": 20,
            "vision_max_edge": 1200,
            "vision_max_output_tokens": 1000,
            "vision_detail": "low",
        },
    }
    preset = presets[args.speed_mode]
    for name, value in preset.items():
        if getattr(args, name, None) is None:
            setattr(args, name, value)


def _resolve_api_concurrency(args: argparse.Namespace) -> dict[str, int]:
    fallback = max(1, int(getattr(args, "concurrency", 1) or 1))
    return {
        "llm": _coerce_api_concurrency(getattr(args, "llm_concurrency", None), fallback),
        "vision": _coerce_api_concurrency(getattr(args, "vision_concurrency", None), fallback),
        "ocr": _coerce_api_concurrency(getattr(args, "ocr_concurrency", None), fallback),
        "figure": _coerce_api_concurrency(getattr(args, "figure_concurrency", None), fallback),
    }


def _coerce_api_concurrency(value: int | None, fallback: int) -> int:
    if value is None:
        value = fallback
    return max(1, int(value))


def _resolve_cache_dirs(args: argparse.Namespace, output_root: Path) -> dict[str, Path | None]:
    global_cache_dir = getattr(args, "global_cache_dir", None)
    global_cache = global_cache_dir.resolve() if global_cache_dir else None
    return {
        "llm": _cache_dir(args, "cache_dir", global_cache, "llm"),
        "ocr": _cache_dir(args, "ocr_cache_dir", global_cache, "ocr"),
        "vision": _cache_dir(args, "vision_cache_dir", global_cache, "vision"),
        "figure": _cache_dir(args, "figure_cache_dir", global_cache, "figure"),
        "sections": _cache_dir(args, "section_cache_dir", global_cache, "sections"),
    }


def _cache_dir(args: argparse.Namespace, attr: str, global_cache: Path | None, name: str) -> Path | None:
    value = getattr(args, attr, None)
    if value:
        return value.resolve()
    return global_cache / name if global_cache else None


def _parse_slide_ranges(value: str | None) -> set[int]:
    if not value:
        return set()
    result: set[int] = set()
    for raw_part in value.split(","):
        part = raw_part.strip()
        if not part:
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            if start <= 0 or end <= 0 or end < start:
                raise ValueError(f"Invalid slide range: {part}")
            result.update(range(start, end + 1))
        else:
            slide_id = int(part)
            if slide_id <= 0:
                raise ValueError(f"Invalid slide id: {part}")
            result.add(slide_id)
    return result
