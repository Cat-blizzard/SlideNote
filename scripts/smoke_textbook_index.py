from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

import fitz
from _smoke_common import OCR_ENV_KEYS, env_without, run


REQUIRED_FILES = {
    "textbook_manifest.json",
    "textbook_pages.jsonl",
    "textbook_toc.json",
    "textbook_sections.json",
    "textbook_chunks.jsonl",
    "textbook_index.json",
    "textbook_report.md",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the no-API textbook-index smoke path.")
    parser.add_argument("--out", type=Path, default=None, help="Directory for smoke artifacts. Defaults to a temporary directory.")
    parser.add_argument("--keep-output", action="store_true", help="Keep the temporary smoke directory for inspection.")
    args = parser.parse_args()

    root = args.out.resolve() if args.out else Path(tempfile.mkdtemp(prefix="slidenote-textbook-")).resolve()
    cleanup = args.out is None and not args.keep_output
    source = root / "textbook_fixture.pdf"
    out = root / "textbook_index"

    try:
        root.mkdir(parents=True, exist_ok=True)
        _write_fixture_pdf(source)
        env = env_without(OCR_ENV_KEYS)
        run(
            [
                sys.executable,
                "-m",
                "slidenote",
                "textbook-index",
                str(source),
                "--out",
                str(out),
                "--ocr",
                "off",
                "--quiet",
            ],
            env=env,
        )
        _require_files(out)
        manifest = json.loads((out / "textbook_manifest.json").read_text(encoding="utf-8"))
        if manifest["counts"]["pages"] != 3 or manifest["counts"]["chunks"] < 1:
            raise RuntimeError(f"Unexpected textbook manifest counts: {manifest['counts']}")
        if (out / "ocr_usage.json").exists():
            raise RuntimeError("ocr_usage.json should not be generated for --ocr off smoke.")
        print(f"Textbook-index smoke passed: {out}")
        return 0
    finally:
        if cleanup:
            shutil.rmtree(root, ignore_errors=True)


def _write_fixture_pdf(path: Path) -> None:
    doc = fitz.open()
    pages = [
        ["Contents", "Chapter 1 Transport .... 2", "Chapter 2 Congestion .... 3"],
        ["Chapter 1 Transport", "TCP provides reliable ordered delivery.", "UDP is lightweight and connectionless."],
        ["Chapter 2 Congestion", "Congestion control adapts the sending rate using network feedback."],
    ]
    for lines in pages:
        page = doc.new_page(width=520, height=720)
        y = 72
        for line in lines:
            page.insert_text((72, y), line, fontsize=12)
            y += 28
    doc.save(path)
    doc.close()


def _require_files(root: Path) -> None:
    missing = sorted(name for name in REQUIRED_FILES if not (root / name).exists())
    if missing:
        raise RuntimeError(f"Missing expected files in {root}: {', '.join(missing)}")


if __name__ == "__main__":
    raise SystemExit(main())
