from __future__ import annotations

import argparse
from pathlib import Path

from slidenote.costing import write_cost_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate SlideNote token and cost reports.")
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--pricing", type=Path, default=Path("pricing.template.json"))
    parser.add_argument("--currency", default="USD")
    args = parser.parse_args()
    report = write_cost_report(args.output_dir, args.pricing if args.pricing.exists() else None, args.currency)
    print(f"Wrote cost report to {args.output_dir / 'cost_report.md'}")
    print(f"Estimated cost: {report['summary']['estimated_cost']:.6f} {report['currency']}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
