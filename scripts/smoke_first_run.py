from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import fitz


API_ENV_KEYS = {
    "ANTHROPIC_API_KEY",
    "ARK_API_KEY",
    "BAIDU_OCR_API_KEY",
    "BAIDU_OCR_SECRET_KEY",
    "CLAUDE_API_KEY",
    "DASHSCOPE_API_KEY",
    "DEEPSEEK_API_KEY",
    "DOUBAO_API_KEY",
    "GEMINI_API_KEY",
    "GLM_API_KEY",
    "GOOGLE_API_KEY",
    "GOOGLE_VISION_API_KEY",
    "MATHPIX_APP_ID",
    "MATHPIX_APP_KEY",
    "OPENAI_API_KEY",
    "QWEN_API_KEY",
    "SLIDENOTE_API_KEY",
    "VOLCENGINE_API_KEY",
    "ZAI_API_KEY",
    "ZHIPUAI_API_KEY",
}

REQUIRED_BUILD_FILES = {
    "notes.md",
    "notes.zip",
    "content.json",
    "coverage.json",
    "coverage.md",
    "source_map.json",
    "run_summary.json",
}

REQUIRED_STUDY_FILES = {
    "study_pack.json",
    "review.md",
    "exam.md",
    "exam.json",
    "exam.html",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the no-API first-run smoke path.")
    parser.add_argument("--out", type=Path, default=None, help="Directory for smoke artifacts. Defaults to a temporary directory.")
    parser.add_argument("--keep-output", action="store_true", help="Keep the temporary smoke directory for inspection.")
    args = parser.parse_args()

    root = args.out.resolve() if args.out else Path(tempfile.mkdtemp(prefix="slidenote-first-run-")).resolve()
    cleanup = args.out is None and not args.keep_output
    build_out = root / "build"
    source = root / "first_run_fixture.pdf"

    try:
        root.mkdir(parents=True, exist_ok=True)
        _write_fixture_pdf(source)
        env = _no_api_env()

        _run(
            [
                sys.executable,
                "-m",
                "slidenote",
                "build",
                str(source),
                "--out",
                str(build_out),
                "--quiet",
                "--preset",
                "local",
                "--export",
                "markdown-zip",
            ],
            env=env,
        )
        _require_files(build_out, REQUIRED_BUILD_FILES)
        _require_zip_members(build_out / "notes.zip", {"notes.md", "README.txt"})

        _run(
            [
                sys.executable,
                "-m",
                "slidenote",
                "study-pack",
                str(build_out),
                "--question-count",
                "6",
                "--quiet",
            ],
            env=env,
        )
        _require_files(build_out, REQUIRED_STUDY_FILES)

        print(f"First-run smoke passed: {build_out}")
        return 0
    finally:
        if cleanup:
            shutil.rmtree(root, ignore_errors=True)


def _write_fixture_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=420, height=260)
    page.insert_text((48, 56), "Transport Layer")
    page.insert_text((48, 92), "TCP provides reliable ordered delivery.")
    page.insert_text((48, 128), "UDP is lightweight and connectionless.")
    doc.save(path)
    doc.close()


def _no_api_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in API_ENV_KEYS:
        env.pop(key, None)
    return env


def _run(command: list[str], *, env: dict[str, str]) -> None:
    completed = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {completed.returncode}: {' '.join(command)}\n{completed.stdout}")


def _require_files(root: Path, names: set[str]) -> None:
    missing = sorted(name for name in names if not (root / name).exists())
    if missing:
        raise RuntimeError(f"Missing expected files in {root}: {', '.join(missing)}")


def _require_zip_members(path: Path, members: set[str]) -> None:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
    missing = sorted(members - names)
    if missing:
        raise RuntimeError(f"Missing expected ZIP members in {path}: {', '.join(missing)}")


if __name__ == "__main__":
    raise SystemExit(main())
