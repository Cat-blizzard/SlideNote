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
