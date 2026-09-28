from __future__ import annotations

import html
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

# Fallback only; override with "exchange_rates": {"CNY": ...} in the pricing JSON.
DEFAULT_CNY_PER_USD = 7.2
DEFAULT_PRICING = {
    "currency": "USD",
    "exchange_rates": {"USD": 1.0, "CNY": DEFAULT_CNY_PER_USD},
    "models": {},
    "ocr": {},
}

# Usage files written by stages that report their own call records.
USAGE_FILES = (
    ("llm_usage.json", "llm"),
    ("vision_usage.json", "vision"),
    ("figure_usage.json", "figure"),
    ("ocr_usage.json", "ocr"),
)
# Stage reports that embed model usage in their own shape:
# (file, bucket name, provider/model block, calls key, cache-hits key).
EMBEDDED_USAGE_REPORTS = (
    ("sections.json", "sections", "llm", "llm_call", "local_cache_hits"),
    ("deck_brief.json", "deck_brief", "llm", "llm_call", "local_cache_hits"),
    ("content_guard.json", "content_guard", "llm", None, None),
    ("semantic_layout.json", "semantic_layout", "vision_enhancement", "vision_calls", "vision_cache_hits"),
    ("figure_grounding.json", "figure_grounding", "vision_grounding", "vision_calls", "vision_cache_hits"),
)

@dataclass(slots=True)
class UsageBucket:
    name: str
    provider: str | None = None
    model: str | None = None
    calls: int = 0
    local_cache_hits: int = 0
    local_cache_misses: int = 0
    skipped: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    text_chars: int = 0
    estimated_cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["estimated_cost_usd"] = round(self.estimated_cost_usd, 8)
        return data


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_pricing(path: Path | None = None) -> dict[str, Any]:
    merged = {key: dict(value) if isinstance(value, dict) else value for key, value in DEFAULT_PRICING.items()}
    if not (path and path.exists()):
        return merged
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return merged
    if not isinstance(data, dict):
        return merged
    for key, value in data.items():
        if key == "exchange_rates" and isinstance(value, dict):
            # Merge so a pricing file that only sets EUR keeps the CNY default.
            merged["exchange_rates"].update(value)
        else:
            merged[key] = value
    return merged


def _int(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    try:
        return int(value)
    except Exception:
        return 0


def _summary_or_records(report: dict[str, Any]) -> dict[str, int]:
    """Return usage numbers without double-counting summary + per-record details."""
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    if summary:
        return {
            "calls": _int(summary.get("llm_calls") or summary.get("api_calls")),
            "local_cache_hits": _int(summary.get("local_cache_hits")),
            "local_cache_misses": _int(summary.get("local_cache_misses")),
            "skipped": _int(summary.get("skipped")),
            "input_tokens": _int(summary.get("input_tokens")),
            "cached_input_tokens": _int(summary.get("provider_cached_input_tokens")),
            "output_tokens": _int(summary.get("output_tokens")),
            "total_tokens": _int(summary.get("total_tokens")),
            "text_chars": _int(summary.get("text_chars")),
        }
    records: list[dict[str, Any]] = []
    for key in ("contexts", "pages", "targets", "page_contexts", "weave_contexts", "repair_contexts"):
        value = report.get(key)
        if isinstance(value, list):
            records.extend(item for item in value if isinstance(item, dict))
    return {
        "calls": sum(1 for r in records if r.get("llm_call") or r.get("api_call")),
        "local_cache_hits": sum(1 for r in records if r.get("cache_status") == "local_hit"),
        "local_cache_misses": sum(1 for r in records if r.get("cache_status") == "miss"),
        "skipped": sum(1 for r in records if r.get("cache_status") == "skipped"),
        "input_tokens": sum(_int(r.get("input_tokens")) for r in records),
        "cached_input_tokens": sum(_int(r.get("provider_cached_input_tokens")) for r in records),
        "output_tokens": sum(_int(r.get("output_tokens")) for r in records),
        "total_tokens": sum(_int(r.get("total_tokens")) for r in records),
        "text_chars": sum(_int(r.get("text_chars")) for r in records),
    }


def _model_price(pricing: dict[str, Any], provider: str | None, model: str | None) -> dict[str, float]:
    models = pricing.get("models") or {}
    keys = []
    if provider and model:
        keys.append(f"{provider}/{model}")
    if model:
        keys.append(model)
    if provider:
        keys.append(provider)
    for key in keys:
        if isinstance(models.get(key), dict):
            return models[key]
    return {}


def _llm_cost_usd(bucket: UsageBucket, pricing: dict[str, Any]) -> float:
    price = _model_price(pricing, bucket.provider, bucket.model)
    input_rate = float(price.get("input_per_1m_tokens_usd", 0) or 0)
    cached_rate = float(price.get("cached_input_per_1m_tokens_usd", input_rate) or 0)
    output_rate = float(price.get("output_per_1m_tokens_usd", 0) or 0)
    billable_input = max(bucket.input_tokens - bucket.cached_input_tokens, 0)
    return (billable_input / 1_000_000) * input_rate + (bucket.cached_input_tokens / 1_000_000) * cached_rate + (bucket.output_tokens / 1_000_000) * output_rate


def _ocr_cost_usd(bucket: UsageBucket, pricing: dict[str, Any]) -> float:
    ocr_prices = pricing.get("ocr") or {}
    price = ocr_prices.get(bucket.provider or "") if isinstance(ocr_prices, dict) else None
    if not isinstance(price, dict):
        return 0.0
    per_1k_calls = float(price.get("per_1k_calls_usd", 0) or 0)
    per_1k_chars = float(price.get("per_1k_chars_usd", 0) or 0)
    return (bucket.calls / 1000) * per_1k_calls + (bucket.text_chars / 1000) * per_1k_chars


def bucket_from_report(name: str, report: dict[str, Any] | None, pricing: dict[str, Any]) -> UsageBucket | None:
    if not report:
        return None
    bucket = UsageBucket(name=name, provider=report.get("provider"), model=report.get("model"))
    metrics = _summary_or_records(report)
    for key, value in metrics.items():
        if hasattr(bucket, key):
            setattr(bucket, key, value)
    if name == "ocr":
        bucket.estimated_cost_usd = _ocr_cost_usd(bucket, pricing)
    else:
        bucket.estimated_cost_usd = _llm_cost_usd(bucket, pricing)
    return bucket


def _embedded_usage_report(
    report: dict[str, Any] | None,
    runtime_key: str,
    calls_key: str | None,
    hits_key: str | None,
) -> dict[str, Any] | None:
    """Normalize a stage report with embedded model usage to the usage-file shape."""
    if not isinstance(report, dict):
        return None
    runtime = report.get(runtime_key)
    if not isinstance(runtime, dict) or not runtime.get("provider"):
        return None
    if calls_key is None:
        # content_guard keeps a single call record in its runtime block.
        source = runtime
        calls = 1 if runtime.get("llm_call") else 0
        hits = 1 if runtime.get("cache_status") == "local_hit" else 0
    else:
        source = report.get("summary") if isinstance(report.get("summary"), dict) else {}
        raw_calls = source.get(calls_key)
        # sections/deck_brief store a single boolean `llm_call`.
        calls = int(raw_calls) if isinstance(raw_calls, bool) else _int(raw_calls)
        hits = _int(source.get(hits_key)) if hits_key else 0
    billed = calls > 0
    return {
        "provider": runtime.get("provider"),
        "model": runtime.get("model"),
        "summary": {
            "llm_calls": calls,
            "local_cache_hits": hits,
            # Token counts of cache hits describe the original call, not this run.
            "input_tokens": source.get("input_tokens") if billed else 0,
            "output_tokens": source.get("output_tokens") if billed else 0,
            "total_tokens": source.get("total_tokens") if billed else 0,
            "provider_cached_input_tokens": source.get("provider_cached_input_tokens") if billed else 0,
        },
    }


def build_cost_report(output_dir: Path, pricing_path: Path | None = None, currency: str = "USD") -> dict[str, Any]:
    output_dir = output_dir.resolve()
    pricing = load_pricing(pricing_path)
    buckets = []
    for filename, name in USAGE_FILES:
        bucket = bucket_from_report(name, read_json(output_dir / filename), pricing)
        if bucket:
            buckets.append(bucket)
    for filename, name, runtime_key, calls_key, hits_key in EMBEDDED_USAGE_REPORTS:
        usage = _embedded_usage_report(read_json(output_dir / filename), runtime_key, calls_key, hits_key)
        bucket = bucket_from_report(name, usage, pricing)
        if bucket:
            buckets.append(bucket)

    total = UsageBucket(name="total")
    for bucket in buckets:
        total.calls += bucket.calls
        total.local_cache_hits += bucket.local_cache_hits
        total.local_cache_misses += bucket.local_cache_misses
        total.skipped += bucket.skipped
        total.input_tokens += bucket.input_tokens
        total.cached_input_tokens += bucket.cached_input_tokens
        total.output_tokens += bucket.output_tokens
        total.total_tokens += bucket.total_tokens
        total.text_chars += bucket.text_chars
        total.estimated_cost_usd += bucket.estimated_cost_usd

    exchange_rates = pricing.get("exchange_rates") or {"USD": 1.0}
    rate = float(exchange_rates.get(currency.upper(), 1.0) or 1.0)
    return {
        "schema_version": 1,
        "output_dir": str(output_dir),
        "currency": currency.upper(),
        "exchange_rate_from_usd": rate,
        "summary": _with_currency(total.to_dict(), rate),
        "stages": [_with_currency(bucket.to_dict(), rate) for bucket in buckets],
        "pricing_source": str(pricing_path) if pricing_path else None,
        "notes": [
            "Prices are estimates based on pricing.template.json. Keep that file updated with official provider prices.",
            "Local cache hits are not billed as fresh project calls; provider cached input tokens may still be billed at provider cached rates.",
        ],
    }


def _with_currency(data: dict[str, Any], rate: float) -> dict[str, Any]:
    data = dict(data)
    data["estimated_cost"] = round(float(data.get("estimated_cost_usd", 0) or 0) * rate, 8)
    return data


def write_cost_report(output_dir: Path, pricing_path: Path | None = None, currency: str = "USD") -> dict[str, Any]:
    report = build_cost_report(output_dir, pricing_path, currency)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "cost_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "cost_report.md").write_text(render_markdown(report), encoding="utf-8")
    (output_dir / "cost_dashboard.html").write_text(render_html(report), encoding="utf-8")
    return report


def render_markdown(report: dict[str, Any]) -> str:
    s = report.get("summary", {})
    currency = report.get("currency", "USD")
    lines = [
        "# SlideNote Cost & Token Report",
        "",
        "## Summary",
        f"- Total calls: {s.get('calls', 0)}",
        f"- Input tokens: {s.get('input_tokens', 0):,}",
        f"- Provider cached input tokens: {s.get('cached_input_tokens', 0):,}",
        f"- Output tokens: {s.get('output_tokens', 0):,}",
        f"- Total tokens: {s.get('total_tokens', 0):,}",
        f"- Local cache hits: {s.get('local_cache_hits', 0)}",
        f"- Local cache misses: {s.get('local_cache_misses', 0)}",
        f"- Estimated cost: {s.get('estimated_cost', 0):.6f} {currency}",
        "",
        "## By Stage",
        "| Stage | Provider | Model | Calls | In | Cached In | Out | Cache Hits | Est. Cost |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for stage in report.get("stages", []):
        lines.append(
            f"| {stage.get('name')} | {stage.get('provider') or ''} | {stage.get('model') or ''} | "
            f"{stage.get('calls', 0)} | {stage.get('input_tokens', 0):,} | "
            f"{stage.get('cached_input_tokens', 0):,} | {stage.get('output_tokens', 0):,} | "
            f"{stage.get('local_cache_hits', 0)} | {stage.get('estimated_cost', 0):.6f} {currency} |"
        )
    lines.extend(["", "## Notes"] + [f"- {note}" for note in report.get("notes", [])])
    return "\n".join(lines) + "\n"


def render_html(report: dict[str, Any]) -> str:
    s = report.get("summary", {})
    currency = html.escape(str(report.get("currency", "USD")))
    rows = "".join(
        f"<tr><td>{html.escape(str(stage.get('name')))}</td>"
        f"<td>{html.escape(str(stage.get('provider') or ''))}</td>"
        f"<td>{html.escape(str(stage.get('model') or ''))}</td>"
        f"<td>{stage.get('calls', 0)}</td>"
        f"<td>{stage.get('input_tokens', 0):,}</td>"
        f"<td>{stage.get('output_tokens', 0):,}</td>"
        f"<td>{stage.get('local_cache_hits', 0)}</td>"
        f"<td>{stage.get('estimated_cost', 0):.6f} {currency}</td></tr>"
        for stage in report.get("stages", [])
    )
    return f"""<!doctype html>
<html lang=\"en\"><head><meta charset=\"utf-8\"><title>SlideNote Cost Dashboard</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;margin:0;background:#f7f7fb;color:#1f2937}}
.wrap{{max-width:1100px;margin:36px auto;padding:0 20px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin:20px 0}}
.card{{background:#fff;border:1px solid #e5e7eb;border-radius:16px;padding:18px;box-shadow:0 8px 24px rgba(0,0,0,.05)}}
.label{{font-size:12px;color:#6b7280;text-transform:uppercase;letter-spacing:.04em}}
.value{{font-size:26px;font-weight:750;margin-top:6px}}
table{{width:100%;border-collapse:collapse;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 8px 24px rgba(0,0,0,.05)}}
th,td{{padding:12px 14px;border-bottom:1px solid #eef2f7;text-align:left}}th{{background:#111827;color:white}}tr:last-child td{{border-bottom:none}}
</style></head><body><main class=\"wrap\">
<h1>SlideNote Cost & Token Dashboard</h1>
<div class=\"cards\">
<div class=\"card\"><div class=\"label\">Estimated cost</div><div class=\"value\">{s.get('estimated_cost',0):.6f} {currency}</div></div>
<div class=\"card\"><div class=\"label\">Calls</div><div class=\"value\">{s.get('calls',0)}</div></div>
<div class=\"card\"><div class=\"label\">Input tokens</div><div class=\"value\">{s.get('input_tokens',0):,}</div></div>
<div class=\"card\"><div class=\"label\">Output tokens</div><div class=\"value\">{s.get('output_tokens',0):,}</div></div>
<div class=\"card\"><div class=\"label\">Cache hits</div><div class=\"value\">{s.get('local_cache_hits',0)}</div></div>
</div>
<table><thead><tr><th>Stage</th><th>Provider</th><th>Model</th><th>Calls</th><th>Input</th><th>Output</th><th>Cache Hits</th><th>Cost</th></tr></thead><tbody>{rows}</tbody></table>
</main></body></html>"""
