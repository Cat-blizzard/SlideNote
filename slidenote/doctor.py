from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import sys
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class Check:
    name: str
    status: str
    detail: str
    hint: str | None = None


def run_doctor() -> dict[str, Any]:
    checks = [
        _python_check(),
        _package_check("PyMuPDF", "fitz", "Install with `python -m pip install PyMuPDF`."),
        _package_check("python-pptx", "pptx", "Install with `python -m pip install python-pptx`."),
        _package_check("Pillow", "PIL", "Install with `python -m pip install Pillow`."),
        _package_check("openai SDK", "openai", "Install with `python -m pip install openai` or `.[llm]`."),
        _package_check("pywin32", "win32com.client", "Install with `python -m pip install pywin32` if you want PowerPoint screenshot export."),
        _executable_check("LibreOffice soffice", ("soffice", "libreoffice"), "Install LibreOffice and add its program directory to PATH."),
        _env_check("OPENAI_API_KEY"),
        _env_check("DEEPSEEK_API_KEY"),
        _env_check("DASHSCOPE_API_KEY"),
        _env_check("ARK_API_KEY"),
        _env_check("GLM_API_KEY"),
        _env_check("GEMINI_API_KEY"),
        _env_check("ANTHROPIC_API_KEY"),
        _env_pair_check("Baidu OCR", "BAIDU_OCR_API_KEY", "BAIDU_OCR_SECRET_KEY"),
        _env_pair_check("Mathpix OCR", "MATHPIX_APP_ID", "MATHPIX_APP_KEY"),
        _env_check("GOOGLE_VISION_API_KEY"),
    ]
    return {
        "schema_version": 1,
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "python": sys.version.split()[0],
            "executable": sys.executable,
        },
        "checks": [asdict(check) for check in checks],
        "summary": {
            "ok": sum(1 for check in checks if check.status == "ok"),
            "warn": sum(1 for check in checks if check.status == "warn"),
            "missing": sum(1 for check in checks if check.status == "missing"),
        },
    }


def render_doctor_report(report: dict[str, Any]) -> str:
    lines = [
        "SlideNote doctor",
        f"- OS: {report['platform']['system']} {report['platform']['release']}",
        f"- Python: {report['platform']['python']}",
        f"- Executable: {report['platform']['executable']}",
        "",
    ]
    for check in report["checks"]:
        marker = {"ok": "OK", "warn": "WARN", "missing": "MISSING"}.get(check["status"], check["status"].upper())
        lines.append(f"[{marker}] {check['name']}: {check['detail']}")
        if check.get("hint"):
            lines.append(f"       hint: {check['hint']}")
    summary = report["summary"]
    lines.extend(["", f"Summary: {summary['ok']} OK, {summary['warn']} warnings, {summary['missing']} missing"])
    return "\n".join(lines)


def _python_check() -> Check:
    version = sys.version_info
    if version >= (3, 10):
        return Check("Python >= 3.10", "ok", f"{version.major}.{version.minor}.{version.micro}")
    return Check("Python >= 3.10", "missing", f"{version.major}.{version.minor}.{version.micro}", "Install Python 3.10 or newer.")


def _package_check(name: str, module: str, hint: str) -> Check:
    spec = importlib.util.find_spec(module)
    if spec:
        return Check(name, "ok", "available")
    optional = name in {"openai SDK", "pywin32"}
    return Check(name, "warn" if optional else "missing", "not installed", hint)


def _executable_check(name: str, executables: tuple[str, ...], hint: str) -> Check:
    for executable in executables:
        found = shutil.which(executable)
        if found:
            return Check(name, "ok", found)
    return Check(name, "warn", "not found on PATH", hint)


def _env_check(name: str) -> Check:
    if os.environ.get(name):
        return Check(name, "ok", "configured")
    return Check(name, "warn", "not set")


def _env_pair_check(label: str, left: str, right: str) -> Check:
    left_ok = bool(os.environ.get(left))
    right_ok = bool(os.environ.get(right))
    if left_ok and right_ok:
        return Check(label, "ok", f"{left} and {right} configured")
    return Check(label, "warn", f"missing {left if not left_ok else ''} {right if not right_ok else ''}".strip())
