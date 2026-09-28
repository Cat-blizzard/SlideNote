from __future__ import annotations

import json
from pathlib import Path

from slidenote.costing import build_cost_report, write_cost_report


def test_cost_report_uses_summary_without_double_counting(tmp_path: Path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "llm_usage.json").write_text(json.dumps({
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "summary": {
            "llm_calls": 2,
            "local_cache_hits": 1,
            "local_cache_misses": 2,
            "input_tokens": 1000,
            "provider_cached_input_tokens": 200,
            "output_tokens": 500,
            "total_tokens": 1500
        },
        "pages": [
            {"llm_call": True, "input_tokens": 1000, "output_tokens": 500, "total_tokens": 1500}
        ]
    }), encoding="utf-8")
    pricing = tmp_path / "pricing.json"
    pricing.write_text(json.dumps({
        "models": {
            "deepseek/deepseek-v4-flash": {
                "input_per_1m_tokens_usd": 1,
                "cached_input_per_1m_tokens_usd": 0.1,
                "output_per_1m_tokens_usd": 2
            }
        }
    }), encoding="utf-8")

    report = build_cost_report(out, pricing)
    assert report["summary"]["input_tokens"] == 1000
    assert report["summary"]["output_tokens"] == 500
    # (800/1e6)*1 + (200/1e6)*0.1 + (500/1e6)*2 = 0.00182
    assert abs(report["summary"]["estimated_cost_usd"] - 0.00182) < 1e-9


def test_write_cost_report_outputs_three_files(tmp_path: Path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "ocr_usage.json").write_text(json.dumps({
        "provider": "baidu",
        "summary": {"api_calls": 3, "text_chars": 1200, "local_cache_hits": 2}
    }), encoding="utf-8")
    pricing = tmp_path / "pricing.json"
    pricing.write_text(json.dumps({
        "ocr": {"baidu": {"per_1k_calls_usd": 1, "per_1k_chars_usd": 0.5}}
    }), encoding="utf-8")
    report = write_cost_report(out, pricing, currency="USD")
    assert (out / "cost_report.json").exists()
    assert (out / "cost_report.md").exists()
    assert (out / "cost_dashboard.html").exists()
    assert report["summary"]["calls"] == 3
    assert report["summary"]["estimated_cost_usd"] == 0.603


def test_cost_report_includes_stage_reports_with_embedded_usage(tmp_path: Path):
    out = tmp_path / "out"
    out.mkdir()
    reports = {
        "sections.json": {
            "llm": {"provider": "deepseek", "model": "deepseek-v4-flash"},
            "summary": {"llm_call": True, "local_cache_hits": 0, "input_tokens": 100, "output_tokens": 10, "total_tokens": 110},
        },
        "deck_brief.json": {
            "llm": {"provider": "deepseek", "model": "deepseek-v4-flash"},
            "summary": {"llm_call": False, "local_cache_hits": 1, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        },
        "content_guard.json": {
            "llm": {"provider": "deepseek", "model": "deepseek-v4-flash", "llm_call": True, "cache_status": "miss",
                    "input_tokens": 200, "output_tokens": 20, "total_tokens": 220},
        },
        "semantic_layout.json": {
            "vision_enhancement": {"provider": "qwen", "model": "qwen-vl-plus"},
            "summary": {"vision_calls": 2, "vision_cache_hits": 1, "input_tokens": 300, "output_tokens": 30, "total_tokens": 330},
        },
        "figure_grounding.json": {
            "vision_grounding": {"provider": None, "model": None},
            "summary": {"vision_calls": 0, "input_tokens": 0},
        },
    }
    for name, data in reports.items():
        (out / name).write_text(json.dumps(data), encoding="utf-8")

    report = build_cost_report(out)

    stages = {stage["name"]: stage for stage in report["stages"]}
    assert set(stages) == {"sections", "deck_brief", "content_guard", "semantic_layout"}
    assert stages["sections"]["calls"] == 1
    assert stages["deck_brief"]["calls"] == 0
    assert stages["deck_brief"]["local_cache_hits"] == 1
    assert stages["content_guard"]["input_tokens"] == 200
    assert stages["semantic_layout"]["provider"] == "qwen"
    assert report["summary"]["calls"] == 4
    assert report["summary"]["input_tokens"] == 600


def test_pricing_exchange_rates_merge_with_default_cny(tmp_path: Path):
    out = tmp_path / "out"
    out.mkdir()
    pricing = tmp_path / "pricing.json"
    pricing.write_text(json.dumps({"exchange_rates": {"EUR": 0.9}}), encoding="utf-8")
    assert build_cost_report(out, pricing, currency="CNY")["exchange_rate_from_usd"] == 7.2
    assert build_cost_report(out, pricing, currency="EUR")["exchange_rate_from_usd"] == 0.9

    pricing.write_text(json.dumps({"exchange_rates": {"CNY": 7.0}}), encoding="utf-8")
    assert build_cost_report(out, pricing, currency="CNY")["exchange_rate_from_usd"] == 7.0
