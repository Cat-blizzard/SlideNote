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
    category: str
    impact: str
    required: bool = False
    hint: str | None = None
    fix: str | None = None
    docs: str | None = None


def run_doctor() -> dict[str, Any]:
    checks = [
        _python_check(),
        _package_check("PyMuPDF", "fitz", "Install with `python -m pip install PyMuPDF`.", "python -m pip install PyMuPDF", required=True),
        _package_check("python-pptx", "pptx", "Install with `python -m pip install python-pptx`.", "python -m pip install python-pptx", required=True),
        _package_check("Pillow", "PIL", "Install with `python -m pip install Pillow`.", "python -m pip install Pillow", required=True),
        _package_check("openai SDK", "openai", "Install with `python -m pip install openai` or `.[llm]`.", "python -m pip install openai"),
        _package_check(
            "pywin32",
            "win32com.client",
            "Install with `python -m pip install pywin32` if you want PowerPoint screenshot export.",
            "python -m pip install pywin32",
        ),
        _executable_check(
            "LibreOffice soffice",
            ("soffice", "libreoffice"),
            "Install LibreOffice and add its program directory to PATH.",
            "https://www.libreoffice.org/download/download-libreoffice/",
        ),
        _executable_check(
            "Pandoc",
            ("pandoc",),
            "Install Pandoc and add it to PATH if you want Word, PDF, or LaTeX exports.",
            "https://pandoc.org/installing.html",
            impact="Needed only for optional docx/pdf/latex note exports.",
        ),
        _env_check("OPENAI_API_KEY", "OpenAI text and vision providers."),
        _env_check("DEEPSEEK_API_KEY", "DeepSeek text rewriting."),
        _env_check("DASHSCOPE_API_KEY", "Qwen text and vision providers."),
        _env_check("ARK_API_KEY", "Doubao / Volcengine Ark text and vision providers."),
        _env_check("GLM_API_KEY", "GLM text rewriting."),
        _env_check("GEMINI_API_KEY", "Gemini text and vision providers."),
        _env_check("ANTHROPIC_API_KEY", "Claude text and vision providers."),
        _env_pair_check("Baidu OCR", "BAIDU_OCR_API_KEY", "BAIDU_OCR_SECRET_KEY", "Baidu OCR API."),
        _env_pair_check("Mathpix OCR", "MATHPIX_APP_ID", "MATHPIX_APP_KEY", "Mathpix OCR API."),
        _env_check("GOOGLE_VISION_API_KEY", "Google Vision OCR API."),
    ]
    recommended_actions = _recommended_actions(checks)
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
            "required_missing": sum(1 for check in checks if check.required and check.status == "missing"),
            "optional_missing_or_warn": sum(1 for check in checks if not check.required and check.status in {"missing", "warn"}),
        },
        "recommended_actions": recommended_actions,
        "gui": {
            "ready_for_local_parse": not any(check.required and check.status == "missing" for check in checks),
            "ready_for_llm": any(
                check.name in {"OPENAI_API_KEY", "DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY", "ARK_API_KEY", "GLM_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"}
                and check.status == "ok"
                for check in checks
            ),
            "ready_for_ocr": any(check.name in {"Baidu OCR", "Mathpix OCR", "GOOGLE_VISION_API_KEY"} and check.status == "ok" for check in checks),
            "ready_for_ppt_screenshots": any(check.name in {"LibreOffice soffice", "pywin32"} and check.status == "ok" for check in checks),
            "ready_for_exports": any(check.name == "Pandoc" and check.status == "ok" for check in checks),
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
        lines.append(f"       impact: {check['impact']}")
        if check.get("hint"):
            lines.append(f"       hint: {check['hint']}")
        if check.get("fix"):
            lines.append(f"       fix: {check['fix']}")
    summary = report["summary"]
    lines.extend(["", f"Summary: {summary['ok']} OK, {summary['warn']} warnings, {summary['missing']} missing"])
    if report.get("recommended_actions"):
        lines.append("")
        lines.append("Recommended next steps:")
        for index, action in enumerate(report["recommended_actions"], start=1):
            lines.append(f"{index}. {action['title']}: {action['detail']}")
            if action.get("fix"):
                lines.append(f"   fix: {action['fix']}")
    return "\n".join(lines)


def _python_check() -> Check:
    version = sys.version_info
    if version >= (3, 10):
        return Check("Python >= 3.10", "ok", f"{version.major}.{version.minor}.{version.micro}", "runtime", "Required to run SlideNote.", required=True)
    return Check(
        "Python >= 3.10",
        "missing",
        f"{version.major}.{version.minor}.{version.micro}",
        "runtime",
        "SlideNote cannot run reliably on this Python version.",
        required=True,
        hint="Install Python 3.10 or newer.",
        fix="Install Python 3.10+ and recreate the virtual environment.",
        docs="https://www.python.org/downloads/",
    )


def _package_check(name: str, module: str, hint: str, fix: str, required: bool = False) -> Check:
    if _module_available(module):
        return Check(name, "ok", "available", "python_package", _package_impact(name), required=required)
    return Check(
        name,
        "missing" if required else "warn",
        "not installed",
        "python_package",
        _package_impact(name),
        required=required,
        hint=hint,
        fix=fix,
    )


def _module_available(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, AttributeError, ValueError):
        return False


def _executable_check(name: str, executables: tuple[str, ...], hint: str, docs: str | None = None, impact: str | None = None) -> Check:
    for executable in executables:
        found = shutil.which(executable)
        if found:
            return Check(name, "ok", found, "external_tool", impact or "Needed for .ppt conversion and reliable PPTX page screenshots.")
    return Check(
        name,
        "warn",
        "not found on PATH",
        "external_tool",
        impact or "PPT/PPTX screenshot export may be unavailable; PDF and structural PPTX parsing can still work.",
        hint=hint,
        docs=docs,
    )


def _env_check(name: str, impact: str = "Optional API provider.") -> Check:
    if os.environ.get(name):
        return Check(name, "ok", "configured", "api_key", impact)
    return Check(name, "warn", "not set", "api_key", impact, hint=f"Set environment variable {name} when you want to use this provider.")


def _env_pair_check(label: str, left: str, right: str, impact: str) -> Check:
    left_ok = bool(os.environ.get(left))
    right_ok = bool(os.environ.get(right))
    if left_ok and right_ok:
        return Check(label, "ok", f"{left} and {right} configured", "api_key", impact)
    missing = " ".join(value for value, ok in ((left, left_ok), (right, right_ok)) if not ok)
    return Check(label, "warn", f"missing {missing}".strip(), "api_key", impact, hint=f"Set both {left} and {right}.")


def _package_impact(name: str) -> str:
    impacts = {
        "PyMuPDF": "Required for PDF parsing, text extraction, images, and page screenshots.",
        "python-pptx": "Required for PPTX structural parsing.",
        "Pillow": "Required for image metadata, resizing, screenshots, and figure crops.",
        "openai SDK": "Required for OpenAI-compatible providers such as OpenAI, DeepSeek, Qwen, Doubao, and GLM.",
        "pywin32": "Optional on Windows for PowerPoint-based screenshot export when PowerPoint is installed.",
    }
    return impacts.get(name, "Python package dependency.")


def _recommended_actions(checks: list[Check]) -> list[dict[str, str | None]]:
    actions: list[dict[str, str | None]] = []
    for check in checks:
        if check.status == "ok":
            continue
        if check.required or check.name in {"LibreOffice soffice", "Pandoc", "openai SDK", "pywin32"}:
            actions.append({"title": check.name, "detail": check.hint or check.detail, "fix": check.fix, "docs": check.docs})
    if not any(check.category == "api_key" and check.status == "ok" for check in checks):
        actions.append(
            {
                "title": "API keys",
                "detail": "No LLM/OCR API key is configured. Local parsing can still run with `--vision off`, but AI rewriting, OCR APIs, and vision APIs need keys.",
                "fix": "Set at least one provider key, for example DEEPSEEK_API_KEY for text or DASHSCOPE_API_KEY for Qwen vision.",
                "docs": "CONFIG.zh-CN.md",
            }
        )
    return actions
