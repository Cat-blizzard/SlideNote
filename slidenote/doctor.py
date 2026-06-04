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


TEXT_API_CHECKS = {
    "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY",
    "ARK_API_KEY",
    "GLM_API_KEY",
    "GEMINI_API_KEY",
    "ANTHROPIC_API_KEY",
}
OCR_API_CHECKS = {"Baidu OCR", "Mathpix OCR", "GOOGLE_VISION_API_KEY"}


def run_doctor() -> dict[str, Any]:
    checks = [
        _python_check(),
        _package_check("PyMuPDF", "fitz", "Run `./install.ps1` or install with `python -m pip install PyMuPDF`.", "python -m pip install PyMuPDF", required=True),
        _package_check("python-pptx", "pptx", "Run `./install.ps1` or install with `python -m pip install python-pptx`.", "python -m pip install python-pptx", required=True),
        _package_check("Pillow", "PIL", "Run `./install.ps1` or install with `python -m pip install Pillow`.", "python -m pip install Pillow", required=True),
        _package_check("openai SDK", "openai", "Install with `python -m pip install openai` or `.[llm]`.", "python -m pip install openai"),
        _package_check(
            "pywin32",
            "win32com.client",
            "Install with `python -m pip install pywin32` only if you want PowerPoint screenshot export.",
            "python -m pip install pywin32",
        ),
        _executable_check(
            "LibreOffice soffice",
            ("soffice", "soffice.com", "libreoffice"),
            "Install LibreOffice and add its program directory to PATH.",
            "winget install -e --id TheDocumentFoundation.LibreOffice",
            "https://www.libreoffice.org/download/download-libreoffice/",
        ),
        _executable_check(
            "Pandoc",
            ("pandoc",),
            "Install Pandoc if you want Word, PDF, or LaTeX exports. Markdown output does not need it.",
            "winget install -e --id JohnMacFarlane.Pandoc",
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
    readiness = _readiness(checks)
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
        "readiness": readiness,
        "recommended_actions": recommended_actions,
        "install_guide": recommended_actions,
        "gui": {
            "ready_for_local_parse": _ready(readiness, "local_parse"),
            "ready_for_llm": _ready(readiness, "text_llm"),
            "ready_for_vision": _ready(readiness, "vision"),
            "ready_for_ocr": _ready(readiness, "ocr"),
            "ready_for_ppt_screenshots": _ready(readiness, "ppt_screenshots"),
            "ready_for_exports": _ready(readiness, "exports"),
            "ready_for_pdf_export": _ready(readiness, "pdf_export"),
        },
    }


def render_doctor_report(report: dict[str, Any]) -> str:
    lines = [
        "SlideNote setup guide",
        f"- Python: {report['platform']['python']}",
        f"- Executable: {report['platform']['executable']}",
        "",
        "Readiness:",
    ]
    for item in report.get("readiness", []):
        marker = "READY" if item.get("ready") else ("BLOCKED" if item.get("required") else "OPTIONAL")
        lines.append(f"- [{marker}] {item['label']}: {item['detail']}")
        if item.get("skip"):
            lines.append(f"  can skip: {item['skip']}")

    summary = report["summary"]
    lines.extend(["", f"Summary: {summary['ok']} OK, {summary['warn']} warnings, {summary['missing']} missing"])
    if summary["required_missing"]:
        lines.append("Required dependencies are missing. Run `./install.ps1`, then run doctor again.")
    else:
        lines.append("Local parsing is ready. Optional items only affect AI, OCR, screenshots, or exports.")

    if report.get("recommended_actions"):
        lines.append("")
        lines.append("Install guide:")
        for index, action in enumerate(report["recommended_actions"], start=1):
            skip = f" Skip if: {action['skip']}" if action.get("skip") else ""
            lines.append(f"{index}. {action['title']}: {action['detail']}{skip}")
            if action.get("fix"):
                lines.append(f"   fix: {action['fix']}")
            if action.get("docs"):
                lines.append(f"   docs: {action['docs']}")

    lines.extend(
        [
            "",
            "Useful starts:",
            "- GUI: ./run_gui.ps1",
            "- Local preview: python -m slidenote build lecture.pdf --out outputs/local --preset local --export markdown-zip",
            "- Recheck: python -m slidenote doctor",
        ]
    )
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


def _executable_check(
    name: str,
    executables: tuple[str, ...],
    hint: str,
    fix: str,
    docs: str | None = None,
    impact: str | None = None,
) -> Check:
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
        fix=fix,
        docs=docs,
    )


def _env_check(name: str, impact: str = "Optional API provider.") -> Check:
    if os.environ.get(name):
        return Check(name, "ok", "configured", "api_key", impact)
    return Check(
        name,
        "warn",
        "not set",
        "api_key",
        impact,
        hint=f"Set {name} when you want to use this provider, or paste the key in the GUI for one run.",
        fix=f"$env:{name}=\"...\"",
    )


def _env_pair_check(label: str, left: str, right: str, impact: str) -> Check:
    left_ok = bool(os.environ.get(left))
    right_ok = bool(os.environ.get(right))
    if left_ok and right_ok:
        return Check(label, "ok", f"{left} and {right} configured", "api_key", impact)
    missing = " ".join(value for value, ok in ((left, left_ok), (right, right_ok)) if not ok)
    return Check(
        label,
        "warn",
        f"missing {missing}".strip(),
        "api_key",
        impact,
        hint=f"Set both {left} and {right}, or paste them in the GUI for one run.",
        fix=f"$env:{left}=\"...\"; $env:{right}=\"...\"",
    )


def _package_impact(name: str) -> str:
    impacts = {
        "PyMuPDF": "Required for PDF parsing, text extraction, images, and page screenshots.",
        "python-pptx": "Required for PPTX structural parsing.",
        "Pillow": "Required for image metadata, resizing, screenshots, and figure crops.",
        "openai SDK": "Required for OpenAI-compatible providers such as OpenAI, DeepSeek, Qwen, Doubao, and GLM.",
        "pywin32": "Optional for PowerPoint-based screenshot export when PowerPoint is installed.",
    }
    return impacts.get(name, "Python package dependency.")


def _readiness(checks: list[Check]) -> list[dict[str, Any]]:
    required_missing = [check.name for check in checks if check.required and check.status == "missing"]
    text_api_ready = any(check.name in TEXT_API_CHECKS and check.status == "ok" for check in checks)
    ocr_ready = any(check.name in OCR_API_CHECKS and check.status == "ok" for check in checks)
    vision_ready = _check_ok(checks, "DASHSCOPE_API_KEY") or _check_ok(checks, "OPENAI_API_KEY") or _check_ok(checks, "GEMINI_API_KEY") or _check_ok(checks, "ANTHROPIC_API_KEY")
    libreoffice_ready = _check_ok(checks, "LibreOffice soffice")
    pywin32_ready = _check_ok(checks, "pywin32")
    pandoc_ready = _check_ok(checks, "Pandoc")
    return [
        {
            "id": "local_parse",
            "label": "Local parsing",
            "ready": not required_missing,
            "required": True,
            "detail": "Ready for PDF/PPTX parsing." if not required_missing else f"Missing: {', '.join(required_missing)}.",
        },
        {
            "id": "text_llm",
            "label": "Text LLM notes",
            "ready": text_api_ready,
            "required": False,
            "detail": "At least one text provider key is configured." if text_api_ready else "No text provider key in the environment.",
            "skip": "Skip for local preview, or paste a text API key in the GUI for one run.",
        },
        {
            "id": "vision",
            "label": "Vision understanding",
            "ready": vision_ready,
            "required": False,
            "detail": "A vision-capable provider key is configured." if vision_ready else "No vision provider key in the environment.",
            "skip": "Skip with `--vision off`, or paste a vision API key in the GUI.",
        },
        {
            "id": "ocr",
            "label": "OCR",
            "ready": ocr_ready,
            "required": False,
            "detail": "At least one OCR provider is configured." if ocr_ready else "No OCR provider key pair is configured.",
            "skip": "Skip for normal digital slides; enable only for scanned/image-only pages.",
        },
        {
            "id": "ppt_screenshots",
            "label": "PPT screenshots",
            "ready": libreoffice_ready or pywin32_ready,
            "required": False,
            "detail": "LibreOffice or PowerPoint automation is available." if (libreoffice_ready or pywin32_ready) else "Optional screenshot tool not found.",
            "skip": "Skip when processing PDFs or when PPTX structural text/images are enough.",
        },
        {
            "id": "exports",
            "label": "Word/LaTeX exports",
            "ready": pandoc_ready,
            "required": False,
            "detail": "Pandoc is available." if pandoc_ready else "Pandoc is not on PATH.",
            "skip": "Skip if `notes.md` is enough.",
        },
        {
            "id": "pdf_export",
            "label": "PDF export",
            "ready": pandoc_ready and libreoffice_ready,
            "required": False,
            "detail": "Pandoc and LibreOffice are available." if (pandoc_ready and libreoffice_ready) else "PDF export prefers both Pandoc and LibreOffice.",
            "skip": "Skip if Markdown or Word output is enough.",
        },
    ]


def _ready(readiness: list[dict[str, Any]], key: str) -> bool:
    return any(item.get("id") == key and bool(item.get("ready")) for item in readiness)


def _check_ok(checks: list[Check], name: str) -> bool:
    return any(check.name == name and check.status == "ok" for check in checks)


def _recommended_actions(checks: list[Check]) -> list[dict[str, str | bool | None]]:
    actions: list[dict[str, str | bool | None]] = []
    for check in checks:
        if check.status == "ok":
            continue
        if check.required:
            actions.append(
                {
                    "title": check.name,
                    "detail": check.hint or check.detail,
                    "fix": check.fix,
                    "docs": check.docs,
                    "required": True,
                    "skip": None,
                }
            )
        elif check.name == "openai SDK":
            actions.append(
                {
                    "title": check.name,
                    "detail": "Install LLM extras when you want AI rewriting with OpenAI-compatible providers.",
                    "fix": 'python -m pip install -e ".[llm]"',
                    "docs": check.docs,
                    "required": False,
                    "skip": "Skip for local preview.",
                }
            )
        elif check.name == "LibreOffice soffice":
            actions.append(
                {
                    "title": check.name,
                    "detail": check.hint or check.detail,
                    "fix": check.fix,
                    "docs": check.docs,
                    "required": False,
                    "skip": "Skip for PDF input or when full-slide screenshots / PDF export are not needed.",
                }
            )
        elif check.name == "Pandoc":
            actions.append(
                {
                    "title": check.name,
                    "detail": check.hint or check.detail,
                    "fix": check.fix,
                    "docs": check.docs,
                    "required": False,
                    "skip": "Skip if Markdown output is enough.",
                }
            )
    if not any(check.category == "api_key" and check.status == "ok" for check in checks):
        actions.append(
            {
                "title": "API keys",
                "detail": "No LLM/OCR API key is configured in the environment. The GUI can still accept keys for a single run.",
                "fix": "Paste keys in the GUI, or set a provider variable such as DEEPSEEK_API_KEY or DASHSCOPE_API_KEY.",
                "docs": "CONFIG.zh-CN.md",
                "required": False,
                "skip": "Skip for local preview with `--vision off`.",
            }
        )
    return actions
