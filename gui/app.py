from __future__ import annotations

import io
import html
import json
import os
import re
import shutil
import subprocess
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st

try:
    from slidenote.costing import write_cost_report
except Exception:  # pragma: no cover - GUI fallback only
    write_cost_report = None

try:
    from slidenote.doctor import run_doctor
except Exception:  # pragma: no cover - GUI fallback only
    run_doctor = None

from gui.studio_core import (
    PROVIDER_ENV_KEYS,
    StudioConfig,
    TextbookConfig,
    build_env,
    build_slidenote_command,
    build_study_pack_command,
    build_textbook_command,
    command_for_display,
    discover_outputs,
    discover_textbook_outputs,
    needs_text_api,
    performance_tips,
    progress_percent,
    provider_env_key,
    safe_run_name,
)

ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = ROOT / "gui_runs"
UPLOADS_DIR = RUNS_DIR / "uploads"
OUTPUTS_DIR = RUNS_DIR / "outputs"
GLOBAL_CACHE_DIR = RUNS_DIR / ".global_cache"
PRICING_PATH = ROOT / "pricing.template.json"

PRESETS = {
    "Lecture quality": {"preset": "lecture", "vision": "auto"},
    "Local preview": {"preset": "local", "vision": "off"},
}

MODALITY_OPTIONS = ["native_text", "mixed", "image_only", "shape_diagram", "decorative", "unknown"]


def _run_simplified_app() -> None:
    st.set_page_config(page_title="SlideNote Studio", page_icon="SN", layout="wide", initial_sidebar_state="collapsed")
    _style()
    _ensure_dirs()
    _render_top_bar()
    mode = st.radio("Workspace", ["Course notes", "Textbook library"], horizontal=True, label_visibility="collapsed")
    if mode == "Textbook library":
        _render_textbook_library()
        return

    uploaded = st.session_state.get("source_upload")
    if uploaded is None:
        _render_empty_upload_panel()
        uploaded = st.file_uploader(
            "Upload PPTX / PPT / PDF",
            type=["pptx", "ppt", "pdf"],
            key="source_upload",
            label_visibility="collapsed",
        )
        if uploaded is not None:
            st.rerun()
        return

    left, right = st.columns([0.36, 0.64], gap="large")
    with left:
        st.markdown("### Source")
        uploaded = st.file_uploader(
            "Replace PPTX / PPT / PDF",
            type=["pptx", "ppt", "pdf"],
            key="source_upload",
            label_visibility="collapsed",
        )
        if uploaded is None:
            st.info("Choose a course file to continue.")
            return
        _render_source_file(uploaded)

        st.markdown("### Run")
        preset_name = st.selectbox("Workflow preset", list(PRESETS.keys()), index=0)
        preset = PRESETS[preset_name]
        preset_value = str(preset["preset"])
        provider = st.selectbox("Text provider", ["deepseek", "openai", "qwen", "doubao", "glm", "gemini", "claude"], index=0)
        vision = st.selectbox("Vision", ["auto", "off"], index=["auto", "off"].index(str(preset["vision"])), disabled=preset_value == "local")
        if preset_value == "local":
            vision = "off"
        vision_provider = "qwen"

        with st.expander("API keys", expanded=preset_value == "lecture"):
            api_key = st.text_input("Text API key", type="password", placeholder="Used for lecture builds and study packs")
            vision_api_key = st.text_input("Vision API key", type="password", help="Qwen/DashScope key. Can be the same key when your provider is qwen.")
            ocr_api_key = st.text_input("OCR API key / app id", type="password", help="Optional. Lecture preset uses OCR auto; scanned PDFs need OCR credentials.")
            ocr_secret_key = st.text_input("OCR secret / app key", type="password")

        with st.expander("Exports", expanded=False):
            export_markdown_zip = st.checkbox(
                "Markdown note package (.zip)",
                value=True,
                help="Recommended for sharing Markdown notes. The ZIP contains notes.md and notes.assets so images render on another computer.",
            )
            export_markdown_toc = st.checkbox("Markdown with table of contents (.md)", value=True)
            export_docx = st.checkbox("Word document (.docx)", value=False)
            export_pdf = st.checkbox("PDF handout (.pdf)", value=False)
            export_latex = st.checkbox("LaTeX source (.tex)", value=False)
            export_options = _selected_export_formats(export_markdown_zip, export_markdown_toc, export_docx, export_pdf, export_latex)
            _render_export_readiness(export_options)

        with st.expander("Save", expanded=False):
            save_mode = st.radio(
                "Output location",
                ["Default workspace", "Custom folder"],
                index=0,
                help="Default saves under gui_runs/outputs. Custom lets you choose a parent folder on this computer.",
            )
            custom_output_base_text = st.text_input(
                "Custom output folder",
                value=str(Path.home() / "Desktop" / "SlideNote_outputs"),
                disabled=save_mode == "Default workspace",
                help="Paste a local folder path, for example C:\\Users\\student\\Desktop\\SlideNote_outputs.",
            )
            timestamped_subfolder = st.toggle("Create a timestamped subfolder", value=True)

    preview_config = StudioConfig(
        input_path=ROOT / "example.pdf",
        output_dir=OUTPUTS_DIR / "preview",
        progress_json=OUTPUTS_DIR / "preview" / "progress.json",
        preset=preset_value,
        provider=provider,
        api_key=api_key or None,
        vision=vision,
        vision_provider=vision_provider,
        vision_api_key=vision_api_key or (api_key if provider == vision_provider else None) or None,
        ocr="auto" if preset_value == "lecture" else "off",
        ocr_api_key=ocr_api_key or None,
        ocr_secret_key=ocr_secret_key or None,
        export=",".join(export_options) if export_options else None,
    )

    with left:
        text_status = _api_status(needs_text_api(preview_config), api_key, provider)
        vision_status = _api_status(_needs_vision_api(preview_config), preview_config.vision_api_key, vision_provider)
        ocr_status = _ocr_status(preview_config.ocr != "off", ocr_api_key, ocr_secret_key, "baidu")
        _render_compact_run_state(preview_config, export_options, text_status, vision_status, ocr_status)
        _render_key_warnings(preview_config, provider, text_status, vision_status, ocr_status)
        with st.expander("Command", expanded=False):
            st.code(command_for_display(build_slidenote_command(preview_config)), language="bash")
        with st.expander("Usage & diagnostics", expanded=False):
            tips = performance_tips(preview_config)
            if tips:
                for tip in tips:
                    st.caption(tip)
            _render_doctor_panel(text_status=text_status, vision_status=vision_status, ocr_status=ocr_status)
        run_clicked = st.button("Run SlideNote build", type="primary", use_container_width=True, disabled=uploaded is None)

    if run_clicked and uploaded is not None:
        output_base = OUTPUTS_DIR if save_mode == "Default workspace" else Path(custom_output_base_text).expanduser()
        try:
            input_path, output_dir, progress_json = _prepare_run_paths(uploaded, output_base, timestamped_subfolder)
        except Exception as exc:
            st.error(f"Could not prepare output folder: {exc}")
            return
        config = _clone_config_for_run(preview_config, input_path=input_path, output_dir=output_dir, progress_json=progress_json)
        _run_build(config)
        st.session_state["last_output_dir"] = str(output_dir)

    last_output_dir = Path(st.session_state.get("last_output_dir", "")) if st.session_state.get("last_output_dir") else None
    with right:
        _render_notes_workspace(last_output_dir, preview_config)

    if last_output_dir and last_output_dir.exists():
        st.divider()
        _render_detail_results(last_output_dir, preview_config)


def main() -> None:
    _run_simplified_app()

def _ensure_dirs() -> None:
    RUNS_DIR.mkdir(exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    GLOBAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _render_top_bar() -> None:
    st.markdown(
        """
        <div class="topbar">
          <div class="topbar-brand">
            <div class="brand-mark">SN</div>
            <div>
              <div class="brand-name">SlideNote Studio</div>
              <div class="brand-subtitle">Learning workspace</div>
            </div>
          </div>
          <div class="topbar-status">
            <span class="chip chip-neutral">lecture/local</span>
            <span class="chip chip-neutral">markdown zip</span>
            <span class="chip chip-neutral">study pack</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_empty_upload_panel() -> None:
    st.markdown(
        """
        <div class="empty-state">
          <div class="empty-kicker">Start</div>
          <h2>Drop a PPT or PDF into the upload box.</h2>
          <p>Run Local preview first when checking a new install; switch to Lecture quality for final notes.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_textbook_library() -> None:
    left, right = st.columns([0.36, 0.64], gap="large")
    with left:
        st.markdown("### Textbook source")
        uploaded = st.file_uploader(
            "Upload textbook PDF",
            type=["pdf"],
            key="textbook_upload",
            label_visibility="collapsed",
        )
        if uploaded is not None:
            _render_source_file(uploaded)

        st.markdown("### Build library")
        ocr_label = st.selectbox("OCR", ["Auto scanned pages", "Off", "All pages"], index=0)
        ocr_mode = {"Auto scanned pages": "auto", "Off": "off", "All pages": "all"}[ocr_label]
        with st.expander("OCR API key", expanded=ocr_mode != "off"):
            ocr_api_key = st.text_input("OCR API key / app id", type="password", key="textbook_ocr_api_key")
            ocr_secret_key = st.text_input("OCR secret / app key", type="password", key="textbook_ocr_secret_key")
        preview_config = TextbookConfig(
            input_path=ROOT / "textbook.pdf",
            output_dir=OUTPUTS_DIR / "textbook_preview",
            ocr=ocr_mode,
            ocr_api_key=ocr_api_key or None,
            ocr_secret_key=ocr_secret_key or None,
        )
        ocr_status = _ocr_status(ocr_mode != "off", ocr_api_key, ocr_secret_key, "baidu")
        _render_chip_row(
            [
                ("input", "PDF", "neutral"),
                ("ocr", ocr_mode, "good" if ocr_mode == "auto" else "neutral"),
                ("ocr key", ocr_status[0], ocr_status[2]),
                ("rag", "chunks only", "neutral"),
            ]
        )
        if ocr_mode != "off" and ocr_status[0] == "Missing key":
            st.markdown("<div class='inline-alert'>OCR auto only needs keys when scanned or low-text pages are found.</div>", unsafe_allow_html=True)
        st.markdown("<div class='inline-alert'>Textbook libraries are not connected to note generation yet.</div>", unsafe_allow_html=True)
        with st.expander("Command", expanded=False):
            st.code(command_for_display(build_textbook_command(preview_config)), language="bash")
        run_clicked = st.button("Build textbook library", type="primary", use_container_width=True, disabled=uploaded is None)

    if run_clicked and uploaded is not None:
        try:
            input_path, output_dir = _prepare_textbook_paths(uploaded)
        except Exception as exc:
            st.error(f"Could not prepare textbook folder: {exc}")
            return
        config = TextbookConfig(
            input_path=input_path,
            output_dir=output_dir,
            ocr=ocr_mode,
            ocr_api_key=ocr_api_key or None,
            ocr_secret_key=ocr_secret_key or None,
            quiet=True,
        )
        _run_textbook_index(config)
        st.session_state["last_textbook_output_dir"] = str(output_dir)

    last_output_dir = Path(st.session_state.get("last_textbook_output_dir", "")) if st.session_state.get("last_textbook_output_dir") else None
    with right:
        _render_textbook_workspace(last_output_dir)


def _render_source_file(uploaded: Any) -> None:
    name = html.escape(str(getattr(uploaded, "name", "uploaded file")))
    suffix = html.escape((Path(name).suffix.lstrip(".") or "file").upper())
    size = _format_file_size(getattr(uploaded, "size", None))
    st.markdown(
        f"""
        <div class="source-file">
          <div class="source-ext">{suffix}</div>
          <div class="source-meta">
            <div class="source-name">{name}</div>
            <div class="source-size">{html.escape(size)}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _format_file_size(size: Any) -> str:
    if size is None:
        return "unknown size"
    try:
        value = float(size)
    except Exception:
        return "unknown size"
    units = ["B", "KB", "MB", "GB"]
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def _render_compact_run_state(
    config: StudioConfig,
    export_options: list[str],
    text_status: tuple[str, str, str],
    vision_status: tuple[str, str, str],
    ocr_status: tuple[str, str, str],
) -> None:
    exports = "markdown zip" if "markdown-zip" in export_options else (export_options[0].replace("-", " ") if export_options else "no export")
    chips = [
        ("preset", config.preset, "neutral"),
        ("provider", config.provider, "neutral"),
        ("vision", config.vision, "good" if config.vision != "off" else "muted"),
        ("export", exports, "good" if "markdown-zip" in export_options else "neutral"),
        ("text", text_status[0], text_status[2]),
        ("vision key", vision_status[0], vision_status[2]),
        ("ocr", ocr_status[0], ocr_status[2]),
    ]
    _render_chip_row(chips)


def _render_key_warnings(
    config: StudioConfig,
    provider: str,
    text_status: tuple[str, str, str],
    vision_status: tuple[str, str, str],
    ocr_status: tuple[str, str, str],
) -> None:
    messages: list[str] = []
    if needs_text_api(config) and text_status[0] == "Missing key":
        messages.append(f"Missing text key: set {provider_env_key(provider)}, fill API keys here, or use Local preview.")
    if _needs_vision_api(config) and vision_status[0] == "Missing key":
        messages.append("Missing vision key: fill Qwen/DashScope key or set Vision off.")
    if config.ocr != "off" and ocr_status[0] == "Missing key":
        messages.append("OCR auto is enabled; scanned PDFs may need Baidu OCR key and secret.")
    for message in messages:
        st.markdown(f"<div class='inline-alert'>{html.escape(message)}</div>", unsafe_allow_html=True)


def _render_chip_row(chips: list[tuple[str, str, str]]) -> None:
    rendered = []
    for label, value, tone in chips:
        rendered.append(
            "<span class='chip chip-{tone}'><span class='chip-label'>{label}</span>{value}</span>".format(
                tone=html.escape(tone),
                label=html.escape(label),
                value=html.escape(value),
            )
        )
    st.markdown(f"<div class='chip-row'>{''.join(rendered)}</div>", unsafe_allow_html=True)


def _render_notes_workspace(output_dir: Path | None, config: StudioConfig | None = None) -> None:
    st.markdown("### Notes workspace")
    if not output_dir or not output_dir.exists():
        st.markdown(
            """
            <div class="notes-empty">
              <div class="empty-kicker">Notes</div>
              <h2>Run a build to preview notes here.</h2>
              <p>Generated Markdown and download buttons will appear in this workspace.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    outputs = discover_outputs(output_dir)
    st.markdown(f"<div class='output-path'>Output saved to<br><code>{html.escape(str(output_dir))}</code></div>", unsafe_allow_html=True)
    _render_workspace_downloads(output_dir, outputs)

    notes_path = outputs.get("notes")
    if notes_path:
        st.markdown("<div class='preview-label'>notes.md preview</div>", unsafe_allow_html=True)
        _render_markdown_file(notes_path, "notes.md")
    else:
        st.info("notes.md was not generated yet.")

    _render_study_pack_compact(output_dir, config)
    with st.expander("Usage & diagnostics", expanded=False):
        _render_usage_snapshot(output_dir)


def _render_workspace_downloads(output_dir: Path, outputs: dict[str, Path]) -> None:
    c1, c2, c3 = st.columns(3)
    notes_zip = outputs.get("notes_zip")
    notes = outputs.get("notes")
    if notes_zip:
        c1.download_button("notes.zip", data=notes_zip.read_bytes(), file_name="notes.zip", mime="application/zip", use_container_width=True)
    else:
        c1.button("notes.zip", disabled=True, use_container_width=True)
    if notes:
        c2.download_button("notes.md", data=notes.read_bytes(), file_name="notes.md", mime="text/markdown", use_container_width=True)
    else:
        c2.button("notes.md", disabled=True, use_container_width=True)
    c3.download_button("all results", data=_zip_output_dir(output_dir), file_name=f"{output_dir.name}.zip", mime="application/zip", use_container_width=True)
    if notes_zip:
        st.caption("Share Markdown notes with notes.zip; it includes notes.md and notes.assets.")


def _render_study_pack_compact(output_dir: Path, config: StudioConfig | None = None) -> None:
    outputs = discover_outputs(output_dir)
    key_suffix = safe_run_name(output_dir.name)
    with st.expander("Study pack", expanded=False):
        question_count = st.slider("Question count", 4, 40, 12, step=2, key=f"study_count_{key_suffix}")
        if st.button("Generate study pack", type="primary", use_container_width=True, key=f"study_button_{key_suffix}"):
            run_config = config or StudioConfig(input_path=ROOT / "example.pdf", output_dir=output_dir, progress_json=output_dir / "progress.json")
            run_config = _clone_config_for_run(run_config, input_path=run_config.input_path, output_dir=output_dir, progress_json=output_dir / "progress.json")
            _run_study_pack(run_config, question_count)
            st.rerun()

        rows = [
            ("review.md", outputs.get("review")),
            ("exam.md", outputs.get("exam")),
            ("exam.html", outputs.get("exam_html")),
            ("study_pack.json", outputs.get("study_pack")),
        ]
        st.dataframe([{"file": name, "status": "ready" if path else "not generated"} for name, path in rows], use_container_width=True, hide_index=True)
        ready = [(name, path) for name, path in rows if path]
        if ready:
            cols = st.columns(min(3, len(ready)))
            for col, (name, path) in zip(cols, ready):
                col.download_button(name, data=path.read_bytes(), file_name=path.name, mime=_mime_for_path(path), use_container_width=True)


def _render_usage_snapshot(output_dir: Path) -> None:
    run_summary = _read_json(output_dir / "run_summary.json") or {}
    cost_report = _read_json(output_dir / "cost_report.json") or {}
    counts = run_summary.get("counts") if isinstance(run_summary.get("counts"), dict) else {}
    cost_summary = cost_report.get("summary") if isinstance(cost_report.get("summary"), dict) else {}
    rows = [
        {"item": "pages", "value": counts.get("pages", "unknown")},
        {"item": "sections", "value": counts.get("sections", "unknown")},
        {"item": "total tokens", "value": cost_summary.get("total_tokens", "not recorded")},
        {"item": "estimated cost", "value": cost_summary.get("estimated_cost", "not recorded")},
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)
    for filename in ("progress.json", "run_summary.json", "cost_report.json", "coverage.json"):
        path = output_dir / filename
        if path.exists():
            st.caption(f"{filename}: {path}")


def _selected_export_formats(markdown_zip: bool, markdown_toc: bool, docx: bool, pdf: bool, latex: bool) -> list[str]:
    selected: list[str] = []
    if markdown_zip:
        selected.append("markdown-zip")
    if markdown_toc:
        selected.append("markdown-toc")
    if docx:
        selected.append("docx")
    if pdf:
        selected.append("pdf")
    if latex:
        selected.append("latex")
    return selected


def _render_export_readiness(export_options: list[str]) -> None:
    needs_pandoc = any(fmt in export_options for fmt in ("docx", "pdf", "latex"))
    needs_libreoffice = "pdf" in export_options
    pandoc_path = shutil.which("pandoc")
    libreoffice_path = _find_libreoffice()
    if needs_pandoc and not pandoc_path:
        st.warning("Word/LaTeX/PDF exports need Pandoc. Install Pandoc, then rerun. Markdown TOC still works.")
        st.code("winget install JohnMacFarlane.Pandoc", language="powershell")
    elif needs_pandoc:
        st.success(f"Pandoc ready: {pandoc_path}")
    else:
        st.caption("No Pandoc needed for the selected exports.")
    if needs_libreoffice and not libreoffice_path:
        st.warning("PDF export now converts notes.docx with LibreOffice for better Chinese/CJK layout. Install LibreOffice if notes.pdf is needed.")
        st.code("winget install -e --id TheDocumentFoundation.LibreOffice", language="powershell")
    elif needs_libreoffice:
        st.success(f"LibreOffice ready for PDF: {libreoffice_path}")


def _find_libreoffice() -> str | None:
    for executable in ("soffice", "soffice.com", "libreoffice"):
        found = shutil.which(executable)
        if found:
            return found
    return None


def _api_status(enabled: bool, typed_key: str | None, provider: str) -> tuple[str, str, str]:
    if not enabled:
        return "Off", "Not used", "muted"
    env_keys = PROVIDER_ENV_KEYS.get(provider, (provider_env_key(provider),))
    configured_env = next((key for key in env_keys if os.getenv(key)), None)
    if typed_key or configured_env:
        source = "page key" if typed_key else str(configured_env)
        return "Ready", source, "good"
    return "Missing key", " / ".join(env_keys), "bad"


def _ocr_status(enabled: bool, api_key: str | None, secret_key: str | None, provider: str) -> tuple[str, str, str]:
    if not enabled:
        return "Off", "Not used", "muted"
    provider = provider.lower()
    if provider == "baidu":
        env_ready = bool(os.getenv("BAIDU_OCR_API_KEY") and os.getenv("BAIDU_OCR_SECRET_KEY"))
        page_ready = bool(api_key and secret_key)
        return ("Ready", "page keys" if page_ready else "BAIDU_*", "good") if (page_ready or env_ready) else ("Missing key", "API key + secret", "bad")
    if provider == "mathpix":
        env_ready = bool(os.getenv("MATHPIX_APP_ID") and os.getenv("MATHPIX_APP_KEY"))
        page_ready = bool(api_key and secret_key)
        return ("Ready", "page keys" if page_ready else "MATHPIX_*", "good") if (page_ready or env_ready) else ("Missing key", "app id + app key", "bad")
    env_ready = bool(os.getenv("GOOGLE_VISION_API_KEY") or os.getenv("GOOGLE_API_KEY"))
    return ("Ready", "page key" if api_key else "GOOGLE_*", "good") if (api_key or env_ready) else ("Missing key", "Google Vision key", "bad")


def _cache_status(cache_mode: str, use_global_cache: bool) -> tuple[str, str, str]:
    if cache_mode == "off":
        return "Off", "Disabled", "muted"
    if cache_mode == "refresh":
        return "Refresh", "Bypass selected", "muted"
    return "On", "Shared" if use_global_cache else "Local", "good"


def _concurrency_status(concurrency: int) -> tuple[str, str, str]:
    detail = "worker" if int(concurrency) == 1 else "workers"
    return str(concurrency), detail, "good" if int(concurrency) <= 4 else "muted"


def _status_card(label: str, status: str, detail: str, tone: str, icon: str = "•") -> None:
    st.markdown(
        f"""
        <div class="status-card tone-{tone}">
          <div class="status-top"><span>{icon}</span><span>{label}</span></div>
          <div class="status-main">{status}</div>
          <div class="status-detail">{detail}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _needs_vision_api(config: StudioConfig) -> bool:
    return config.preset == "lecture" and config.vision != "off"


def _render_doctor_panel(text_status: tuple[str, str, str], vision_status: tuple[str, str, str], ocr_status: tuple[str, str, str]) -> None:
    if run_doctor is None:
        st.warning("Doctor module is unavailable in this environment.")
        return
    report = run_doctor()
    summary = report.get("summary", {})
    d1, d2, d3, d4 = st.columns(4)
    d1.metric("Required missing", summary.get("required_missing", 0))
    d2.metric("Warnings", summary.get("warn", 0))
    d3.metric("Local parse", _readiness_label(report, "local_parse"))
    d4.metric("Text API", text_status[0])

    if summary.get("required_missing", 0):
        st.error("Required dependencies are missing. Local parsing may fail.")
    else:
        st.success("Required local parsing dependencies look ready.")
    st.caption(f"Text API: {text_status[0]} · Vision API: {vision_status[0]} · OCR API: {ocr_status[0]}")

    readiness = report.get("readiness") or []
    if readiness:
        st.markdown("**Setup readiness**")
        cards = st.columns(3)
        for index, item in enumerate(readiness):
            ready = bool(item.get("ready"))
            required = bool(item.get("required"))
            tone = "good" if ready else ("bad" if required else "muted")
            status = "Ready" if ready else ("Blocked" if required else "Optional")
            with cards[index % 3]:
                _status_card(str(item.get("label", "")), status, str(item.get("detail", "")), tone)

    checks = report.get("checks", [])
    if checks:
        compact = [
            {
                "name": item.get("name"),
                "status": item.get("status"),
                "category": item.get("category"),
                "detail": item.get("detail"),
                "fix": item.get("fix") or "",
            }
            for item in checks
        ]
        st.dataframe(compact, use_container_width=True, hide_index=True)
    actions = report.get("recommended_actions") or []
    if actions:
        st.markdown("**Install guide**")
        for action in actions:
            st.markdown(f"**{action.get('title')}** — {action.get('detail')}")
            if action.get("skip"):
                st.caption(f"Can skip: {action.get('skip')}")
            if action.get("fix"):
                st.code(str(action.get("fix")), language="bash")


def _readiness_label(report: dict[str, Any], key: str) -> str:
    for item in report.get("readiness") or []:
        if item.get("id") == key:
            return "Ready" if item.get("ready") else "Blocked"
    return "Unknown"


def _clone_config_for_run(config: StudioConfig, input_path: Path, output_dir: Path, progress_json: Path) -> StudioConfig:
    values = {field: getattr(config, field) for field in StudioConfig.__dataclass_fields__}
    values.update({"input_path": input_path, "output_dir": output_dir, "progress_json": progress_json})
    return StudioConfig(**values)


def _prepare_run_paths(uploaded, output_base: Path, timestamped_subfolder: bool) -> tuple[Path, Path, Path]:
    run_name = f"{safe_run_name(uploaded.name)}_{int(time.time())}"
    input_path = UPLOADS_DIR / f"{run_name}{Path(uploaded.name).suffix.lower()}"
    input_path.write_bytes(uploaded.getbuffer())
    output_base.mkdir(parents=True, exist_ok=True)
    output_dir = output_base / run_name if timestamped_subfolder else output_base
    output_dir.mkdir(parents=True, exist_ok=True)
    progress_json = output_dir / "progress.json"
    return input_path, output_dir, progress_json


def _prepare_textbook_paths(uploaded) -> tuple[Path, Path]:
    if Path(uploaded.name).suffix.lower() != ".pdf":
        raise ValueError("Textbook library v1 only accepts PDF files.")
    run_name = f"textbook_{safe_run_name(uploaded.name)}_{int(time.time())}"
    input_path = UPLOADS_DIR / f"{run_name}.pdf"
    input_path.write_bytes(uploaded.getbuffer())
    output_dir = OUTPUTS_DIR / "textbooks" / run_name
    output_dir.mkdir(parents=True, exist_ok=True)
    return input_path, output_dir


def _run_build(config: StudioConfig) -> None:
    cmd = build_slidenote_command(config)
    env = build_env(os.environ, config)
    st.subheader("Live run")
    progress_bar = st.progress(0.01)
    status_box = st.empty()
    stage_box = st.empty()
    log_box = st.empty()
    logs: list[str] = []

    process = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    while process.poll() is None:
        if process.stdout is not None:
            line = process.stdout.readline()
            if line:
                logs.append(line.rstrip())
        _update_progress_ui(config.progress_json, progress_bar, status_box, stage_box)
        log_box.code("\n".join(logs[-80:]) or "Running...", language="text")
        time.sleep(0.25)

    if process.stdout is not None:
        rest = process.stdout.read()
        if rest:
            logs.extend(rest.splitlines())
    _update_progress_ui(config.progress_json, progress_bar, status_box, stage_box)
    log_box.code("\n".join(logs[-120:]) or "No console output.", language="text")

    if process.returncode == 0:
        _generate_cost_report(config.output_dir)
        st.success(f"Build finished. Output saved to: {config.output_dir}")
    else:
        st.error(f"Build failed with exit code {process.returncode}. Check the log above.")


def _run_textbook_index(config: TextbookConfig) -> None:
    cmd = build_textbook_command(config)
    env = build_env(os.environ, config)
    result = subprocess.run(
        cmd,
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.stdout:
        st.code(result.stdout, language="text")
    if result.returncode == 0:
        st.success(f"Textbook library generated. Output saved to: {config.output_dir}")
    else:
        st.error(f"Textbook library build failed with exit code {result.returncode}.")


def _run_study_pack(config: StudioConfig, question_count: int) -> None:
    cmd = build_study_pack_command(config.output_dir, question_count=question_count, quiet=False)
    env = build_env(os.environ, config)
    result = subprocess.run(
        cmd,
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.stdout:
        st.code(result.stdout, language="text")
    if result.returncode == 0:
        st.success("Study pack generated. Review and exam files are in the output directory.")
    else:
        st.error(f"Study pack failed with exit code {result.returncode}.")


def _update_progress_ui(progress_path: Path, progress_bar, status_box, stage_box) -> None:
    progress = _read_json(progress_path)
    if not progress:
        status_box.info("Waiting for progress.json...")
        return
    pct = progress_percent(progress)
    progress_bar.progress(min(max(pct, 0.0), 1.0))
    status = progress.get("status", "running")
    message = progress.get("message", "")
    elapsed = float(progress.get("elapsed_seconds", 0) or 0)
    eta = _estimate_eta(pct, elapsed, status)
    eta_text = f" · **ETA:** `{eta}`" if eta else ""
    status_box.markdown(f"**Status:** `{status}` · **Elapsed:** `{elapsed:.1f}s`{eta_text} · {message}")
    current = progress.get("current_stage")
    if current:
        stage_box.json(current)
    else:
        stages = progress.get("stages") or []
        if stages:
            stage_box.json(stages[-1])


def _estimate_eta(pct: float, elapsed: float, status: str) -> str | None:
    if status == "complete":
        return "0s"
    if status == "failed" or pct <= 0.05 or elapsed <= 0:
        return None
    remaining = max(elapsed / pct - elapsed, 0)
    if remaining < 60:
        return f"{remaining:.0f}s"
    return f"{remaining / 60:.1f}m"


def _generate_cost_report(output_dir: Path) -> None:
    if write_cost_report is None:
        return
    try:
        write_cost_report(output_dir, PRICING_PATH if PRICING_PATH.exists() else None, currency="USD")
    except Exception as exc:
        st.warning(f"Build finished, but cost report generation failed: {exc}")


def _render_detail_results(output_dir: Path, config: StudioConfig | None = None) -> None:
    st.subheader("Details")
    tab_names = ["Quality", "Page explorer", "Exports", "Coverage", "Usage", "Files"]
    tabs = st.tabs(tab_names)

    with tabs[0]:
        _render_quality_panel(output_dir)
    with tabs[1]:
        _render_page_explorer(output_dir)
    with tabs[2]:
        _render_exports_tab(output_dir)
    with tabs[3]:
        _render_markdown_file(output_dir / "coverage.md", "coverage.md")
    with tabs[4]:
        _render_cost_tab(output_dir)
        _render_run_summary_tab(output_dir)
    with tabs[5]:
        st.write(f"Output directory: `{output_dir}`")
        for path in sorted(output_dir.glob("*")):
            if path.is_file():
                _download_file(path)


def _render_results(output_dir: Path, config: StudioConfig | None = None) -> None:
    _render_notes_workspace(output_dir, config)
    st.divider()
    _render_detail_results(output_dir, config)


def _render_textbook_workspace(output_dir: Path | None) -> None:
    st.markdown("### Textbook library")
    if not output_dir or not output_dir.exists():
        st.markdown(
            """
            <div class="notes-empty">
              <div class="empty-kicker">Textbook</div>
              <h2>Build a textbook library to inspect chunks here.</h2>
              <p>The generated corpus is RAG-ready but not used by note generation yet.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return
    outputs = discover_textbook_outputs(output_dir)
    st.markdown(f"<div class='output-path'>Output saved to<br><code>{html.escape(str(output_dir))}</code></div>", unsafe_allow_html=True)
    manifest = _read_json(outputs.get("manifest") or output_dir / "textbook_manifest.json") or {}
    counts = manifest.get("counts") if isinstance(manifest.get("counts"), dict) else {}
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Pages", counts.get("pages", "—"))
    c2.metric("Sections", counts.get("sections", "—"))
    c3.metric("Chunks", counts.get("chunks", "—"))
    c4.metric("OCR calls", counts.get("ocr_api_calls", "—"))
    st.info("This textbook library is a RAG-ready corpus. It does not change generated notes yet.")
    _render_textbook_downloads(output_dir, outputs)
    report = outputs.get("report")
    if report:
        _render_markdown_file(report, "textbook_report.md")
    chunks = _read_jsonl(outputs.get("chunks"))
    if chunks:
        with st.expander("Chunk preview", expanded=False):
            st.dataframe(
                [
                    {
                        "chunk_id": chunk.get("chunk_id"),
                        "section": chunk.get("section_title"),
                        "pages": f"{chunk.get('page_start')}-{chunk.get('page_end')}",
                        "chars": chunk.get("text_chars"),
                    }
                    for chunk in chunks[:100]
                ],
                use_container_width=True,
                hide_index=True,
            )


def _render_textbook_downloads(output_dir: Path, outputs: dict[str, Path]) -> None:
    labels = [
        ("manifest", outputs.get("manifest")),
        ("sections", outputs.get("sections")),
        ("chunks", outputs.get("chunks")),
        ("report", outputs.get("report")),
    ]
    cols = st.columns(4)
    for col, (label, path) in zip(cols, labels):
        if path:
            col.download_button(path.name, data=path.read_bytes(), file_name=path.name, mime=_mime_for_path(path), use_container_width=True)
        else:
            col.button(label, disabled=True, use_container_width=True)
    st.download_button("all textbook files", data=_zip_output_dir(output_dir), file_name=f"{output_dir.name}.zip", mime="application/zip", use_container_width=True)


def _render_quick_downloads(output_dir: Path, outputs: dict[str, Path]) -> None:
    c1, c2, c3, c4 = st.columns(4)
    if outputs.get("notes_zip"):
        c1.download_button("Download Markdown notes ZIP", data=outputs["notes_zip"].read_bytes(), file_name="notes.zip", mime="application/zip", use_container_width=True)
        st.info("Reminder: the shareable Markdown notes are inside notes.zip with their image assets.")
    elif outputs.get("notes"):
        c1.download_button("Download notes.md", data=outputs["notes"].read_bytes(), file_name="notes.md", mime="text/markdown", use_container_width=True)
    else:
        c1.button("notes.md not found", disabled=True, use_container_width=True)
    if outputs.get("coverage"):
        c2.download_button("Download coverage.md", data=outputs["coverage"].read_bytes(), file_name="coverage.md", mime="text/markdown", use_container_width=True)
    else:
        c2.button("coverage.md not found", disabled=True, use_container_width=True)
    if outputs.get("cost_markdown"):
        c3.download_button("Download cost_report.md", data=outputs["cost_markdown"].read_bytes(), file_name="cost_report.md", mime="text/markdown", use_container_width=True)
    else:
        c3.button("cost_report.md not found", disabled=True, use_container_width=True)
    c4.download_button("Download all results (.zip)", data=_zip_output_dir(output_dir), file_name=f"{output_dir.name}.zip", mime="application/zip", use_container_width=True)

    exported = [("Markdown ZIP", outputs.get("notes_zip")), ("Word", outputs.get("docx")), ("PDF", outputs.get("pdf")), ("LaTeX", outputs.get("latex")), ("TOC Markdown", outputs.get("notes_toc"))]
    available = [(label, path) for label, path in exported if path]
    if available:
        st.caption("Exported files")
        cols = st.columns(min(5, len(available)))
        for col, (label, path) in zip(cols, available):
            col.download_button(f"Download {label}", data=path.read_bytes(), file_name=path.name, mime=_mime_for_path(path), use_container_width=True)
    study_files = [("Review", outputs.get("review")), ("Exam", outputs.get("exam")), ("Exam HTML", outputs.get("exam_html"))]
    available_study = [(label, path) for label, path in study_files if path]
    if available_study:
        st.caption("Study pack")
        cols = st.columns(min(3, len(available_study)))
        for col, (label, path) in zip(cols, available_study):
            col.download_button(f"Download {label}", data=path.read_bytes(), file_name=path.name, mime=_mime_for_path(path), use_container_width=True)


def _render_study_pack_tab(output_dir: Path, config: StudioConfig | None = None) -> None:
    outputs = discover_outputs(output_dir)
    report = _read_json(output_dir / "study_pack.json")
    question_count = st.slider("Question count", 4, 40, 12, step=2)
    if st.button("Generate study pack from this output", type="primary", use_container_width=True):
        run_config = config or StudioConfig(input_path=ROOT / "example.pdf", output_dir=output_dir, progress_json=output_dir / "progress.json")
        run_config = _clone_config_for_run(run_config, input_path=run_config.input_path, output_dir=output_dir, progress_json=output_dir / "progress.json")
        _run_study_pack(run_config, question_count)
        st.rerun()
    rows = [
        ("Review checklist", outputs.get("review"), "review.md"),
        ("Self-test Markdown", outputs.get("exam"), "exam.md"),
        ("Interactive self-test", outputs.get("exam_html"), "exam.html"),
        ("Question JSON", outputs.get("exam_json"), "exam.json"),
    ]
    st.dataframe(
        [{"artifact": label, "file": filename, "status": "ready" if path else "not generated"} for label, path, filename in rows],
        use_container_width=True,
        hide_index=True,
    )
    ready = [(label, path) for label, path, _ in rows if path]
    if ready:
        cols = st.columns(min(4, len(ready)))
        for col, (label, path) in zip(cols, ready):
            col.download_button(f"Download {label}", data=path.read_bytes(), file_name=path.name, mime=_mime_for_path(path), use_container_width=True)
    else:
        st.info("No study pack was generated yet. Click the button above; the notes are in the existing build output directory.")

    if outputs.get("review"):
        _render_markdown_file(outputs["review"], "review.md")
    if outputs.get("exam"):
        _render_markdown_file(outputs["exam"], "exam.md")
    if report:
        with st.expander("study_pack.json", expanded=False):
            st.json(report)


def _render_exports_tab(output_dir: Path) -> None:
    outputs = discover_outputs(output_dir)
    export_report = _read_json(output_dir / "export_report.json")
    export_paths = [
        ("Markdown ZIP", outputs.get("notes_zip"), "notes.zip"),
        ("Markdown TOC", outputs.get("notes_toc"), "notes.toc.md"),
        ("Word", outputs.get("docx"), "notes.docx"),
        ("PDF", outputs.get("pdf"), "notes.pdf"),
        ("LaTeX", outputs.get("latex"), "notes.tex"),
    ]
    rows = []
    for label, path, filename in export_paths:
        rows.append({"format": label, "file": filename, "status": "ready" if path else "not generated"})
    st.dataframe(rows, use_container_width=True, hide_index=True)

    ready = [(label, path) for label, path, _ in export_paths if path]
    if ready:
        cols = st.columns(min(5, len(ready)))
        for col, (label, path) in zip(cols, ready):
            col.download_button(f"Download {label}", data=path.read_bytes(), file_name=path.name, mime=_mime_for_path(path), use_container_width=True)
        if outputs.get("notes_zip"):
            st.info("Reminder: the Markdown notes are inside notes.zip with notes.assets.")
    else:
        st.info("No extra export files were generated. Select export formats in the sidebar before running the build.")

    if export_report:
        st.subheader("export_report.json")
        st.json(export_report)
        warnings = export_report.get("warnings") or []
        for warning in warnings:
            st.warning(str(warning))
    else:
        st.caption("export_report.json will appear when extra exports are requested.")


def _zip_output_dir(output_dir: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(output_dir).as_posix())
    buffer.seek(0)
    return buffer.getvalue()


def _render_quality_panel(output_dir: Path) -> None:
    coverage = _read_json(output_dir / "coverage.json") or {}
    run_summary = _read_json(output_dir / "run_summary.json") or {}
    total = int(coverage.get("total") or 0)
    covered = int(coverage.get("covered") or 0)
    missing = int(coverage.get("missing") or 0)
    score = (covered / total * 100) if total else 100.0
    q1, q2, q3, q4 = st.columns(4)
    q1.metric("Coverage score", f"{score:.1f}%")
    q2.metric("Covered elements", covered)
    q3.metric("Missing elements", missing)
    q4.metric("Pages", (run_summary.get("counts") or {}).get("pages", "—"))
    st.progress(min(max(score / 100, 0), 1))

    if missing:
        st.warning("Some elements are not visibly covered. Use the repair queue below to decide which pages need refresh.")
        missing_items = _coverage_missing_items(coverage)
        if missing_items:
            st.dataframe(missing_items, use_container_width=True, hide_index=True)
            missing_pages = sorted({str(item.get("slide_id")) for item in missing_items if item.get("slide_id")})
            if missing_pages:
                pages_text = ",".join(missing_pages)
                st.code(pages_text, language="text")
                st.caption("Copy this into 'Refresh only these pages' to rerun only the affected pages.")
    else:
        st.success("No missing coverage items reported.")

    _render_stage_timings(run_summary)


def _coverage_missing_items(coverage: dict[str, Any]) -> list[dict[str, Any]]:
    explicit = coverage.get("missing_items") or coverage.get("marker_only_items") or []
    items = explicit if isinstance(explicit, list) else []
    if not items and isinstance(coverage.get("items"), list):
        items = [item for item in coverage["items"] if not item.get("covered")]
    normalized = []
    for item in items[:200]:
        normalized.append(
            {
                "slide_id": item.get("slide_id") or _slide_id_from_element(item.get("id") or item.get("element_id")),
                "element_id": item.get("id") or item.get("element_id"),
                "kind": item.get("kind") or item.get("type") or "",
                "reason": item.get("reason") or item.get("status") or "missing",
            }
        )
    return normalized


def _slide_id_from_element(element_id: str | None) -> int | None:
    if not element_id:
        return None
    match = re.match(r"s(\d+)_", str(element_id))
    return int(match.group(1)) if match else None


def _render_stage_timings(run_summary: dict[str, Any]) -> None:
    timings = run_summary.get("stage_timings") or {}
    rows: list[dict[str, Any]] = []
    if isinstance(timings, dict):
        for key in ("slowest_stages", "stages", "items"):
            value = timings.get(key)
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        rows.append(item)
        if not rows:
            for key, value in timings.items():
                if isinstance(value, (int, float)):
                    rows.append({"stage": key, "seconds": value})
                elif isinstance(value, dict):
                    rows.append({"stage": key, **value})
    elif isinstance(timings, list):
        rows = [item for item in timings if isinstance(item, dict)]
    if rows:
        st.markdown("#### Stage timing")
        st.dataframe(rows, use_container_width=True, hide_index=True)


def _render_page_explorer(output_dir: Path) -> None:
    content = _read_json(output_dir / "content.json") or {}
    modalities = _read_json(output_dir / "page_modalities.json") or {}
    pages = content.get("pages") or []
    if not pages:
        st.info("content.json not found or has no pages.")
        return
    page_ids = [int(page.get("slide_id", index + 1)) for index, page in enumerate(pages)]
    selected_page_id = st.selectbox("Page", page_ids, format_func=lambda value: f"Page {value}")
    page = next((item for item in pages if int(item.get("slide_id", 0)) == selected_page_id), pages[0])
    modality_info = _find_modality(modalities, selected_page_id)

    c1, c2 = st.columns([1.1, 1], gap="large")
    with c1:
        st.markdown("#### Original page")
        screenshot_path = _resolve_screenshot_path(output_dir, page)
        if screenshot_path and screenshot_path.exists():
            st.image(str(screenshot_path), use_container_width=True)
        else:
            st.info("No page screenshot found for this page.")
        st.caption(f"Detected modality: `{page.get('page_modality') or modality_info.get('modality') or 'unknown'}`")
        if page.get("warnings"):
            st.warning("\n".join(str(item) for item in page.get("warnings", [])))

    with c2:
        st.markdown("#### Elements and note block")
        e1, e2, e3 = st.columns(3)
        e1.metric("Text blocks", len(page.get("text_blocks") or []))
        e2.metric("Tables", len(page.get("tables") or []))
        e3.metric("Images", len(page.get("images") or []))
        note_excerpt = _note_excerpt_for_page(output_dir / "notes.md", selected_page_id)
        with st.expander("Generated note for this page", expanded=True):
            st.markdown(note_excerpt or "No page-specific note block found.")
        with st.expander("Elements", expanded=False):
            st.json(_compact_page_elements(page))

        st.markdown("#### Manual routing correction")
        current_modality = page.get("page_modality") or modality_info.get("modality") or "unknown"
        index = MODALITY_OPTIONS.index(current_modality) if current_modality in MODALITY_OPTIONS else len(MODALITY_OPTIONS) - 1
        corrected = st.selectbox("Correct page modality", MODALITY_OPTIONS, index=index, key=f"modality_{selected_page_id}")
        note = st.text_area("Reviewer note", value="", key=f"modality_note_{selected_page_id}", placeholder="Example: this is a scanned page; force OCR next run.")
        if st.button("Save correction manifest", key=f"save_modality_{selected_page_id}"):
            _save_modality_override(output_dir, selected_page_id, corrected, note)
            st.success("Saved to page_modalities.overrides.json")


def _find_modality(modalities: dict[str, Any], slide_id: int) -> dict[str, Any]:
    pages = modalities.get("pages") or []
    if isinstance(pages, list):
        return next((item for item in pages if int(item.get("slide_id", 0)) == slide_id), {})
    return {}


def _resolve_screenshot_path(output_dir: Path, page: dict[str, Any]) -> Path | None:
    raw = page.get("page_screenshot")
    candidates: list[Path] = []
    if raw:
        raw_path = Path(str(raw))
        candidates.extend([output_dir / raw_path, output_dir / "notes.assets" / raw_path])
        candidates.extend([output_dir / "notes.assets" / "screenshots" / raw_path.name, output_dir / "screenshots" / raw_path.name])
    slide_id = page.get("slide_id")
    if slide_id:
        candidates.extend([output_dir / "notes.assets" / "screenshots" / f"slide{slide_id}.png", output_dir / "screenshots" / f"slide{slide_id}.png"])
    return next((path for path in candidates if path.exists()), None)


def _compact_page_elements(page: dict[str, Any]) -> dict[str, Any]:
    return {
        "slide_id": page.get("slide_id"),
        "title": page.get("title"),
        "text_blocks": [{"id": item.get("id"), "content": _shorten(item.get("content", ""), 180)} for item in page.get("text_blocks") or []],
        "tables": [{"id": item.get("id"), "rows": len(item.get("rows") or [])} for item in page.get("tables") or []],
        "images": [
            {
                "id": item.get("id"),
                "path": item.get("path"),
                "role": item.get("role"),
                "ignored": item.get("ignored", False),
                "importance_score": item.get("importance_score"),
            }
            for item in page.get("images") or []
        ],
    }


def _note_excerpt_for_page(notes_path: Path, slide_id: int) -> str:
    if not notes_path.exists():
        return ""
    text = notes_path.read_text(encoding="utf-8", errors="replace")
    pattern = re.compile(rf"(^### .*第\s*{slide_id}\s*页.*?$)(.*?)(?=^### .*第\s*\d+\s*页|\Z)", re.M | re.S)
    match = pattern.search(text)
    if match:
        return (match.group(1) + match.group(2)).strip()
    marker = f"p{slide_id}:"
    blocks = [block for block in text.split("\n\n") if marker in block]
    return "\n\n".join(blocks[:4]).strip()


def _save_modality_override(output_dir: Path, slide_id: int, modality: str, note: str) -> None:
    path = output_dir / "page_modalities.overrides.json"
    data = _read_json(path) or {"schema_version": 1, "pages": {}}
    pages = data.setdefault("pages", {})
    pages[str(slide_id)] = {"modality": modality, "note": note, "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z"}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _shorten(value: str, limit: int) -> str:
    text = str(value).replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _render_cost_tab(output_dir: Path) -> None:
    cost = _read_json(output_dir / "cost_report.json")
    if cost:
        summary = cost.get("summary", {})
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Estimated cost", f"{summary.get('estimated_cost', 0):.6f} {cost.get('currency', 'USD')}")
        c2.metric("Calls", summary.get("calls", 0))
        c3.metric("Input tokens", f"{summary.get('input_tokens', 0):,}")
        c4.metric("Output tokens", f"{summary.get('output_tokens', 0):,}")
        c5.metric("Cache hits", summary.get("local_cache_hits", 0))
        stages = cost.get("stages", [])
        if stages:
            st.dataframe(stages, use_container_width=True)
            chart_data = {stage.get("name", "stage"): stage.get("total_tokens", 0) for stage in stages}
            st.bar_chart(chart_data)
        cost_md = output_dir / "cost_report.md"
        if cost_md.exists():
            with st.expander("cost_report.md", expanded=False):
                st.markdown(cost_md.read_text(encoding="utf-8"))
    else:
        st.info("No cost report yet. Run a build first.")


def _render_run_summary_tab(output_dir: Path) -> None:
    for filename in ["run_summary.json", "page_modalities.json", "element_ir.json", "llm_usage.json", "vision_usage.json", "ocr_usage.json", "progress.json"]:
        path = output_dir / filename
        if path.exists():
            with st.expander(filename, expanded=filename == "run_summary.json"):
                st.json(_read_json(path) or {})


def _render_markdown_file(path: Path, label: str) -> None:
    if not path.exists():
        st.info(f"{label} not found.")
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    st.download_button(f"Download {label}", data=text.encode("utf-8"), file_name=label, mime="text/markdown")
    st.markdown(text)


def _download_file(path: Path) -> None:
    st.download_button(f"Download {path.name}", data=path.read_bytes(), file_name=path.name, mime=_mime_for_path(path))


def _mime_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".md":
        return "text/markdown"
    if suffix == ".json":
        return "application/json"
    if suffix == ".html":
        return "text/html"
    if suffix == ".docx":
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if suffix == ".pdf":
        return "application/pdf"
    if suffix == ".tex":
        return "application/x-tex"
    if suffix == ".zip":
        return "application/zip"
    return "application/octet-stream"


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_jsonl(path: Path | None) -> list[dict[str, Any]]:
    if not path or not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _style() -> None:
    st.markdown(
        """
        <style>
        :root {
          --sn-bg: #f4f5f7;
          --sn-surface: #ffffff;
          --sn-surface-soft: #f9fafb;
          --sn-line: #d9dee7;
          --sn-line-strong: #c5ccd8;
          --sn-text: #1f2937;
          --sn-heading: #111827;
          --sn-muted: #6b7280;
          --sn-blue: #2563eb;
          --sn-green: #15803d;
          --sn-red: #b91c1c;
          --sn-amber: #92400e;
        }
        .stApp {
          background: var(--sn-bg) !important;
          color: var(--sn-text) !important;
        }
        header[data-testid="stHeader"] { background: transparent !important; }
        [data-testid="stToolbar"], [data-testid="stDecoration"], #MainMenu { display: none !important; }
        .stApp h1, .stApp h2, .stApp h3, .stApp h4,
        .stApp [data-testid="stMarkdownContainer"] h1,
        .stApp [data-testid="stMarkdownContainer"] h2,
        .stApp [data-testid="stMarkdownContainer"] h3,
        .stApp [data-testid="stMarkdownContainer"] p,
        .stApp [data-testid="stWidgetLabel"] p {
          color: var(--sn-heading) !important;
        }
        .stApp [data-testid="stCaptionContainer"], .stApp small { color: var(--sn-muted) !important; }
        .block-container { padding: 1rem 2rem 2rem; max-width: 1360px; }
        section[data-testid="stSidebar"] {
          background: var(--sn-surface) !important;
          border-right: 1px solid var(--sn-line);
        }
        section[data-testid="stSidebar"] h1,
        section[data-testid="stSidebar"] h2,
        section[data-testid="stSidebar"] h3,
        section[data-testid="stSidebar"] p,
        section[data-testid="stSidebar"] label,
        section[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p {
          color: var(--sn-heading) !important;
        }
        .topbar {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 1rem;
          min-height: 56px;
          padding: .65rem .8rem;
          margin-bottom: 1rem;
          border: 1px solid var(--sn-line);
          border-radius: 8px;
          background: var(--sn-surface);
        }
        .topbar-brand {
          display: flex;
          align-items: center;
          gap: .7rem;
          min-width: 0;
        }
        .brand-mark {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          width: 32px;
          height: 32px;
          border-radius: 8px;
          background: #111827;
          color: #ffffff;
          font-size: .78rem;
          font-weight: 800;
          letter-spacing: 0;
          flex: 0 0 auto;
        }
        .brand-name {
          color: var(--sn-heading);
          font-size: .98rem;
          font-weight: 760;
          line-height: 1.15;
          letter-spacing: 0;
        }
        .brand-subtitle {
          color: var(--sn-muted);
          font-size: .78rem;
          line-height: 1.2;
          margin-top: .12rem;
          letter-spacing: 0;
        }
        .topbar-status, .chip-row {
          display: flex;
          align-items: center;
          flex-wrap: wrap;
          gap: .4rem;
        }
        .chip-row { margin: .75rem 0 .6rem; }
        .chip {
          display: inline-flex;
          align-items: center;
          min-height: 26px;
          max-width: 100%;
          padding: .28rem .48rem;
          border: 1px solid var(--sn-line);
          border-radius: 6px;
          background: var(--sn-surface-soft);
          color: var(--sn-text);
          font-size: .78rem;
          line-height: 1.15;
          font-weight: 650;
          letter-spacing: 0;
          white-space: nowrap;
        }
        .chip-label {
          color: var(--sn-muted);
          font-weight: 600;
          margin-right: .28rem;
        }
        .chip-good { border-color: #b7dfc0; background: #f2fbf4; color: var(--sn-green); }
        .chip-bad { border-color: #f0b9bd; background: #fff7f7; color: var(--sn-red); }
        .chip-muted { color: var(--sn-muted); }
        .chip-neutral { color: var(--sn-text); }
        .empty-state, .notes-empty {
          border: 1px solid var(--sn-line);
          border-radius: 8px;
          background: var(--sn-surface);
          padding: 2rem;
        }
        .empty-state {
          max-width: 760px;
          margin: 1.1rem auto .75rem;
        }
        .notes-empty {
          min-height: 360px;
          display: flex;
          flex-direction: column;
          justify-content: center;
        }
        .empty-kicker {
          color: var(--sn-blue);
          text-transform: uppercase;
          font-size: .72rem;
          font-weight: 760;
          letter-spacing: 0;
          margin-bottom: .4rem;
        }
        .empty-state h2, .notes-empty h2 {
          font-size: 1.45rem;
          line-height: 1.25;
          margin: 0 0 .5rem;
          letter-spacing: 0;
        }
        .empty-state p, .notes-empty p {
          color: var(--sn-muted) !important;
          font-size: .95rem;
          line-height: 1.55;
          margin: 0;
          max-width: 620px;
        }
        div[data-testid="stFileUploader"] {
          max-width: 960px;
          margin: .75rem auto 0;
          padding: .65rem;
          border: 1px solid var(--sn-line);
          border-radius: 8px;
          background: var(--sn-surface);
        }
        div[data-testid="stFileUploaderDropzone"] {
          min-height: 112px;
          border: 1px dashed var(--sn-line-strong) !important;
          border-radius: 8px !important;
          background: var(--sn-surface-soft) !important;
        }
        .source-file {
          display: flex;
          align-items: center;
          gap: .75rem;
          padding: .8rem;
          margin: .65rem 0 1rem;
          border: 1px solid var(--sn-line);
          border-radius: 8px;
          background: var(--sn-surface);
        }
        .source-ext {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          width: 44px;
          height: 38px;
          border-radius: 8px;
          background: #eef2ff;
          color: #1d4ed8;
          font-size: .72rem;
          font-weight: 780;
          flex: 0 0 auto;
        }
        .source-meta { min-width: 0; }
        .source-name {
          color: var(--sn-heading);
          font-weight: 720;
          line-height: 1.25;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .source-size {
          color: var(--sn-muted);
          font-size: .78rem;
          margin-top: .12rem;
        }
        .inline-alert {
          margin: .45rem 0 0;
          padding: .55rem .65rem;
          border: 1px solid #efc6c9;
          border-radius: 8px;
          background: #fff7f7;
          color: var(--sn-red);
          font-size: .84rem;
          line-height: 1.35;
        }
        .stButton>button, .stDownloadButton>button {
          border-radius: 8px !important;
          padding: .58rem .85rem !important;
          font-weight: 700 !important;
          transition: border-color .16s ease, background .16s ease;
        }
        .stButton>button:hover, .stDownloadButton>button:hover {
          border-color: var(--sn-blue) !important;
        }
        div[data-testid="stMetric"], .status-card, .output-path {
          border: 1px solid var(--sn-line);
          background: var(--sn-surface);
          border-radius: 8px;
        }
        div[data-testid="stMetric"] { padding: .75rem; }
        .status-card {
          min-height: 92px;
          padding: .75rem;
          overflow: hidden;
        }
        .status-top {
          display: flex;
          align-items: center;
          gap: .38rem;
          color: var(--sn-muted) !important;
          font-size: .76rem;
          line-height: 1.15;
          font-weight: 700;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .status-main {
          font-size: 1.02rem;
          line-height: 1.12;
          letter-spacing: 0;
          font-weight: 760;
          margin-top: .65rem;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
          word-break: keep-all;
          overflow-wrap: normal;
        }
        .status-detail {
          color: var(--sn-muted) !important;
          font-size: .76rem;
          line-height: 1.25;
          margin-top: .55rem;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
          word-break: keep-all;
        }
        .tone-good .status-main { color: var(--sn-green) !important; }
        .tone-bad .status-main { color: var(--sn-red) !important; }
        .tone-muted .status-main { color: var(--sn-muted) !important; }
        .output-path { padding: .75rem .9rem; margin-bottom: 1rem; color: var(--sn-muted); }
        .output-path code { color: #111827; font-size: .86rem; }
        .preview-label {
          color: var(--sn-muted);
          font-size: .78rem;
          font-weight: 720;
          margin: .8rem 0 .2rem;
          text-transform: uppercase;
          letter-spacing: 0;
        }
        div[data-baseweb="tab-list"] { gap: .35rem; }
        button[data-baseweb="tab"] { border-radius: 6px !important; }
        div[data-testid="stExpander"] details {
          border: 1px solid var(--sn-line) !important;
          border-radius: 8px !important;
          background: var(--sn-surface) !important;
        }
        @media (max-width: 980px) {
          .block-container { padding: .75rem 1rem 1.5rem; }
          .topbar { align-items: flex-start; flex-direction: column; }
          .notes-empty { min-height: 260px; }
          .empty-state { padding: 1.35rem; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
