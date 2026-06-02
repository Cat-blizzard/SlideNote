from __future__ import annotations

import io
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
    DEFAULT_MODELS,
    VISION_DEFAULT_MODELS,
    StudioConfig,
    build_env,
    build_slidenote_command,
    command_for_display,
    discover_outputs,
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
    "⚡ Fast API draft": {
        "use_llm": True,
        "speed_mode": "fast",
        "ocr": "off",
        "vision": "off",
        "note_strategy": "direct",
        "note_depth": "concise",
        "deck_brief": "off",
        "content_guard": "off",
        "section_detection": "local",
        "figure_crop": "off",
        "figure_grounding": "auto",
        "concurrency": 4,
        "vision_max_targets": 10,
        "ocr_max_targets": 10,
    },
    "🧠 Balanced study notes": {
        "use_llm": True,
        "speed_mode": "balanced",
        "ocr": "auto",
        "vision": "auto",
        "note_strategy": "lecture-weave",
        "note_depth": "balanced",
        "deck_brief": "auto",
        "content_guard": "auto",
        "section_detection": "auto",
        "figure_crop": "auto",
        "figure_grounding": "auto",
        "concurrency": 4,
        "vision_max_targets": 30,
        "ocr_max_targets": 30,
    },
    "💎 Quality detailed notes": {
        "use_llm": True,
        "speed_mode": "quality",
        "ocr": "auto",
        "vision": "auto",
        "note_strategy": "lecture-weave",
        "note_depth": "detailed",
        "deck_brief": "auto",
        "content_guard": "auto",
        "section_detection": "auto",
        "figure_crop": "auto",
        "figure_grounding": "auto",
        "concurrency": 3,
        "vision_max_targets": 80,
        "ocr_max_targets": 80,
    },
    "🧪 Local safe preview": {
        "use_llm": False,
        "speed_mode": "fast",
        "ocr": "off",
        "vision": "off",
        "note_strategy": "direct",
        "note_depth": "concise",
        "deck_brief": "off",
        "content_guard": "off",
        "section_detection": "local",
        "figure_crop": "off",
        "figure_grounding": "auto",
        "concurrency": 1,
        "vision_max_targets": 0,
        "ocr_max_targets": 0,
    },
}

MODALITY_OPTIONS = ["native_text", "mixed", "image_only", "shape_diagram", "decorative", "unknown"]


def main() -> None:
    st.set_page_config(page_title="SlideNote Studio", page_icon="📝", layout="wide")
    _style()
    _ensure_dirs()
    _render_hero()

    with st.sidebar:
        st.header("1. File")
        uploaded = st.file_uploader("Upload PPTX / PPT / PDF", type=["pptx", "ppt", "pdf"])
        preset_name = st.selectbox("Workflow preset", list(PRESETS.keys()), index=0)
        preset = PRESETS[preset_name]

        st.header("2. API connection")
        use_llm = st.toggle("Use text LLM", value=bool(preset["use_llm"]))
        provider = st.selectbox("Text provider", ["deepseek", "openai", "qwen", "doubao", "glm", "gemini", "claude"], index=0)
        model_default = DEFAULT_MODELS.get(provider, "")
        model = st.text_input("Text model", value=model_default, help="Leave empty only when your provider requires endpoint IDs in environment variables.")
        api_key = st.text_input("Text API key", type="password", placeholder="Paste key here; it is passed only to this run")
        base_url = st.text_input("Base URL override", value="", help="Optional. Use only for compatible/proxy endpoints.")

        st.header("3. Visual/OCR APIs")
        ocr = st.selectbox("OCR mode", ["off", "auto", "all"], index=["off", "auto", "all"].index(str(preset["ocr"])))
        ocr_provider = st.selectbox("OCR provider", ["baidu", "mathpix", "google"], index=0)
        ocr_api_key = st.text_input("OCR API key / app id", type="password")
        ocr_secret_key = st.text_input("OCR secret / app key", type="password")
        vision = st.selectbox("Vision mode", ["off", "auto", "all"], index=["off", "auto", "all"].index(str(preset["vision"])))
        vision_provider = st.selectbox("Vision provider", ["qwen", "openai", "doubao", "gemini", "claude"], index=0)
        vision_model = st.text_input("Vision model", value=VISION_DEFAULT_MODELS.get(vision_provider, ""))
        vision_api_key = st.text_input("Vision API key", type="password", help="Can be the same as the text provider key if provider is the same.")

        st.header("4. Save")
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
        timestamped_subfolder = st.toggle(
            "Create a timestamped subfolder",
            value=True,
            help="Recommended. Keeps each run separated and prevents old results from being overwritten.",
        )

        st.header("5. Review / Exam")
        study_generation = st.selectbox(
            "Study pack generation",
            ["auto", "local", "llm"],
            index=0,
            help="auto uses the text LLM when note generation uses an LLM; otherwise it falls back to local generation.",
        )
        review_enabled = st.checkbox("Review checklist (review.md)", value=False)
        exam_enabled = st.checkbox("Self-test pack (exam.md + exam.html)", value=False)
        exam_question_count = st.slider("Question count", 4, 40, 12, step=2, disabled=not exam_enabled)
        review_mode = study_generation if review_enabled else "off"
        exam_mode = study_generation if exam_enabled else "off"

        st.header("6. Exports")
        st.caption("Optional final files generated from notes.md. Word/LaTeX need Pandoc; PDF is generated from Word via LibreOffice for stable Chinese/CJK layout. Markdown TOC works without Pandoc.")
        export_markdown_toc = st.checkbox("Markdown with table of contents (.md)", value=True)
        export_docx = st.checkbox("Word document (.docx)", value=False)
        export_pdf = st.checkbox("PDF handout (.pdf)", value=False)
        export_latex = st.checkbox("LaTeX source (.tex)", value=False)
        export_options = _selected_export_formats(export_markdown_toc, export_docx, export_pdf, export_latex)
        _render_export_readiness(export_options)

    col_left, col_right = st.columns([0.95, 1.05], gap="large")

    with col_left:
        st.subheader("Run settings")
        speed_mode = st.segmented_control("Speed mode", ["fast", "balanced", "quality", "debug"], default=str(preset["speed_mode"]))
        concurrency = st.slider("Concurrency", 1, 10, int(preset["concurrency"]), help="Parallel API calls. Higher is faster but may hit rate limits.")
        with st.expander("Advanced API concurrency", expanded=False):
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                llm_concurrency = st.number_input("LLM", min_value=1, max_value=20, value=int(concurrency), step=1)
            with c2:
                vision_concurrency = st.number_input("Vision", min_value=1, max_value=20, value=int(concurrency), step=1)
            with c3:
                ocr_concurrency = st.number_input("OCR", min_value=1, max_value=20, value=int(concurrency), step=1)
            with c4:
                figure_concurrency = st.number_input("Figure", min_value=1, max_value=20, value=int(concurrency), step=1)
        cache_mode = st.selectbox("LLM cache", ["on", "refresh", "off"], index=0)
        use_global_cache = st.toggle("Use shared global cache", value=True, help="Keeps OCR/Vision/LLM cache across different GUI runs.")
        refresh_default = st.session_state.get("refresh_pages_value", "")
        refresh_pages = st.text_input(
            "Refresh only these pages",
            value=refresh_default,
            key="refresh_pages_text",
            placeholder="Example: 3,5-8",
            help="Bypass cache for selected slide IDs only. Use this for local page refresh after review.",
        )

        with st.expander("Quality and time controls", expanded=True):
            c1, c2 = st.columns(2)
            with c1:
                note_strategy = st.selectbox("Note strategy", ["direct", "lecture-weave"], index=["direct", "lecture-weave"].index(str(preset["note_strategy"])))
                note_depth = st.selectbox("Note depth", ["concise", "balanced", "detailed"], index=["concise", "balanced", "detailed"].index(str(preset["note_depth"])))
                note_context = st.selectbox("Note context", ["auto", "document", "section", "page"], index=2)
                note_language = st.selectbox("Language", ["zh", "en", "auto"], index=0)
                term_policy = st.selectbox("Term policy", ["bilingual", "preserve", "translate"], index=0)
            with c2:
                deck_brief = st.selectbox("Deck brief", ["off", "auto", "force"], index=["off", "auto", "force"].index(str(preset["deck_brief"])))
                content_guard = st.selectbox("Content guard", ["off", "auto"], index=["off", "auto"].index(str(preset["content_guard"])))
                section_detection = st.selectbox("Section detection", ["local", "auto", "llm"], index=["local", "auto", "llm"].index(str(preset["section_detection"])))
                max_output_tokens = st.number_input("Max output tokens", min_value=512, max_value=12000, value=2500 if speed_mode == "fast" else 4096, step=256)
                temperature = st.number_input("Temperature", min_value=0.0, max_value=1.5, value=0.2, step=0.1)

        with st.expander("Visual/OCR speed limits", expanded=True):
            c1, c2, c3 = st.columns(3)
            with c1:
                ocr_max_targets = st.number_input("OCR max targets", min_value=0, max_value=500, value=int(preset["ocr_max_targets"]), step=5)
                ocr_max_edge = st.number_input("OCR max edge", min_value=600, max_value=3000, value=1200 if speed_mode == "fast" else 1800, step=100)
            with c2:
                vision_max_targets = st.number_input("Vision max targets", min_value=0, max_value=500, value=int(preset["vision_max_targets"]), step=5)
                vision_max_edge = st.number_input("Vision max edge", min_value=600, max_value=3000, value=1000 if speed_mode == "fast" else 1400, step=100)
            with c3:
                vision_detail = st.selectbox("Vision detail", ["low", "auto", "high"], index=0)
                figure_crop = st.selectbox("Figure crop", ["off", "auto", "vision"], index=["off", "auto", "vision"].index(str(preset["figure_crop"])))
                figure_max_targets = st.number_input("Figure max targets", min_value=0, max_value=500, value=10 if speed_mode == "fast" else 30, step=5)

        with st.expander("Export and display", expanded=False):
            source_display = st.selectbox("Source display", ["hidden", "footnote", "inline"], index=0)
            screenshot_policy = st.selectbox("Screenshot policy", ["fallback", "never", "always"], index=0)
            st.caption("Export formats are configured in the sidebar under '5. Exports'.")
            st.write(", ".join(export_options) if export_options else "No extra exports selected")

    preview_config = StudioConfig(
        input_path=ROOT / "example.pdf",
        output_dir=OUTPUTS_DIR / "preview",
        progress_json=OUTPUTS_DIR / "preview" / "progress.json",
        speed_mode=str(speed_mode),
        concurrency=int(concurrency),
        llm_concurrency=int(llm_concurrency),
        vision_concurrency=int(vision_concurrency),
        ocr_concurrency=int(ocr_concurrency),
        figure_concurrency=int(figure_concurrency),
        global_cache_dir=GLOBAL_CACHE_DIR if use_global_cache else None,
        refresh_pages=refresh_pages.strip() or None,
        use_llm=bool(use_llm),
        provider=provider,
        model=model.strip() or None,
        api_key=api_key or None,
        base_url=base_url.strip() or None,
        max_output_tokens=int(max_output_tokens),
        temperature=float(temperature),
        content_guard=content_guard,
        note_context=note_context,
        note_language=note_language,
        term_policy=term_policy,
        note_strategy=note_strategy,
        note_depth=note_depth,
        deck_brief=deck_brief,
        section_detection=section_detection,
        cache=cache_mode,
        ocr=ocr,
        ocr_provider=ocr_provider,
        ocr_api_key=ocr_api_key or None,
        ocr_secret_key=ocr_secret_key or None,
        ocr_max_targets=int(ocr_max_targets),
        ocr_max_edge=int(ocr_max_edge),
        vision=vision,
        vision_provider=vision_provider,
        vision_model=vision_model.strip() or None,
        vision_api_key=vision_api_key or (api_key if vision_provider == provider else None) or None,
        vision_max_targets=int(vision_max_targets),
        vision_max_edge=int(vision_max_edge),
        vision_detail=vision_detail,
        figure_crop=figure_crop,
        figure_max_targets=int(figure_max_targets),
        figure_grounding="auto",
        source_display=source_display,
        screenshot_policy=screenshot_policy,
        review_mode=review_mode,
        exam_mode=exam_mode,
        exam_question_count=int(exam_question_count),
        export=",".join(export_options) if export_options else None,
    )

    with col_right:
        st.subheader("Connection and runtime overview")
        text_status = _api_status(needs_text_api(preview_config), api_key, provider)
        vision_status = _api_status(_needs_vision_api(preview_config), preview_config.vision_api_key, vision_provider)
        cache_status = _cache_status(cache_mode, use_global_cache)
        concurrency_status = _concurrency_status(concurrency)
        grid = st.columns(4)
        with grid[0]:
            _status_card("Text", *text_status, icon="✦")
        with grid[1]:
            _status_card("Vision", *vision_status, icon="◈")
        with grid[2]:
            _status_card("Workers", *concurrency_status, icon="⇄")
        with grid[3]:
            _status_card("Cache", *cache_status, icon="◎")

        tips = performance_tips(preview_config)
        if tips:
            st.info("\n".join(f"• {tip}" for tip in tips))
        else:
            st.success("Settings are already speed-friendly.")

        with st.expander("Generated command preview", expanded=False):
            st.code(command_for_display(build_slidenote_command(preview_config)), language="bash")

        with st.expander("Doctor panel", expanded=False):
            _render_doctor_panel(text_status=text_status, vision_status=vision_status)

        run_clicked = st.button("🚀 Run SlideNote build", type="primary", use_container_width=True, disabled=uploaded is None)

    if needs_text_api(preview_config) and not api_key and not os.getenv(provider_env_key(provider)):
        st.warning("Text LLM is enabled but no API key is entered or configured in the environment.")
    if _needs_vision_api(preview_config) and vision_status[0] == "Missing key":
        st.warning("Vision is enabled but no vision API key was entered or found in the environment.")

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
    if last_output_dir and last_output_dir.exists():
        st.divider()
        _render_results(last_output_dir)


def _ensure_dirs() -> None:
    RUNS_DIR.mkdir(exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    GLOBAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _render_hero() -> None:
    st.markdown(
        """
        <div class="hero-card">
          <div class="hero-glow hero-glow-a"></div>
          <div class="hero-glow hero-glow-b"></div>
          <div class="hero-kicker">AI course notes · local-first workspace</div>
          <h1>📝 SlideNote Studio</h1>
          <p>Upload slides, connect API keys in the page, run with concurrency/cache controls, and monitor tokens, coverage and cost without touching the command line.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _selected_export_formats(markdown_toc: bool, docx: bool, pdf: bool, latex: bool) -> list[str]:
    selected: list[str] = []
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
    env_key = provider_env_key(provider)
    if typed_key or os.getenv(env_key):
        source = "page key" if typed_key else env_key
        return "Ready", source, "good"
    return "Missing key", env_key, "bad"


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
    return config.vision != "off" or config.figure_crop == "vision" or config.figure_grounding == "vision"


def _render_doctor_panel(text_status: tuple[str, str, str], vision_status: tuple[str, str, str]) -> None:
    if run_doctor is None:
        st.warning("Doctor module is unavailable in this environment.")
        return
    report = run_doctor()
    summary = report.get("summary", {})
    ready = report.get("gui", {})
    d1, d2, d3, d4 = st.columns(4)
    d1.metric("Required missing", summary.get("required_missing", 0))
    d2.metric("Warnings", summary.get("warn", 0))
    d3.metric("Local parse", "Ready" if ready.get("ready_for_local_parse") else "Blocked")
    d4.metric("Text API", text_status[0])

    if summary.get("required_missing", 0):
        st.error("Required dependencies are missing. Local parsing may fail.")
    else:
        st.success("Required local parsing dependencies look ready.")
    st.caption(f"Vision API: {vision_status[0]} · PPT screenshots: {'Ready' if ready.get('ready_for_ppt_screenshots') else 'Optional tool missing'} · Exports: {'Ready' if ready.get('ready_for_exports') else 'Pandoc optional'} · PDF prefers LibreOffice")

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
        with st.expander("Recommended fixes", expanded=False):
            for action in actions:
                st.markdown(f"**{action.get('title')}** — {action.get('detail')}")
                if action.get("fix"):
                    st.code(str(action.get("fix")), language="bash")


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
        st.error(f"Build failed with exit code {process.returncode}. Check the log above. You can keep the same output folder and use Refresh only these pages for a partial retry.")


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


def _render_results(output_dir: Path) -> None:
    st.subheader("Results")
    st.markdown(f"<div class='output-path'>Output saved to<br><code>{output_dir}</code></div>", unsafe_allow_html=True)
    outputs = discover_outputs(output_dir)
    _render_quick_downloads(output_dir, outputs)
    tab_names = ["Quality", "Page explorer", "Token & cost", "Study pack", "Exports", "Notes", "Coverage", "Run summary", "Files"]
    tabs = st.tabs(tab_names)

    with tabs[0]:
        _render_quality_panel(output_dir)
    with tabs[1]:
        _render_page_explorer(output_dir)
    with tabs[2]:
        _render_cost_tab(output_dir)
    with tabs[3]:
        _render_study_pack_tab(output_dir)
    with tabs[4]:
        _render_exports_tab(output_dir)
    with tabs[5]:
        _render_markdown_file(output_dir / "notes.md", "notes.md")
    with tabs[6]:
        _render_markdown_file(output_dir / "coverage.md", "coverage.md")
    with tabs[7]:
        _render_run_summary_tab(output_dir)
    with tabs[8]:
        st.write(f"Output directory: `{output_dir}`")
        for path in sorted(output_dir.glob("*")):
            if path.is_file():
                _download_file(path)


def _render_quick_downloads(output_dir: Path, outputs: dict[str, Path]) -> None:
    c1, c2, c3, c4 = st.columns(4)
    if outputs.get("notes"):
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

    exported = [("Word", outputs.get("docx")), ("PDF", outputs.get("pdf")), ("LaTeX", outputs.get("latex")), ("TOC Markdown", outputs.get("notes_toc"))]
    available = [(label, path) for label, path in exported if path]
    if available:
        st.caption("Exported files")
        cols = st.columns(min(4, len(available)))
        for col, (label, path) in zip(cols, available):
            col.download_button(f"Download {label}", data=path.read_bytes(), file_name=path.name, mime=_mime_for_path(path), use_container_width=True)
    study_files = [("Review", outputs.get("review")), ("Exam", outputs.get("exam")), ("Exam HTML", outputs.get("exam_html"))]
    available_study = [(label, path) for label, path in study_files if path]
    if available_study:
        st.caption("Study pack")
        cols = st.columns(min(3, len(available_study)))
        for col, (label, path) in zip(cols, available_study):
            col.download_button(f"Download {label}", data=path.read_bytes(), file_name=path.name, mime=_mime_for_path(path), use_container_width=True)


def _render_study_pack_tab(output_dir: Path) -> None:
    outputs = discover_outputs(output_dir)
    report = _read_json(output_dir / "study_pack.json")
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
        st.info("No study pack was generated. Enable Review / Exam in the sidebar before running the build.")

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
        cols = st.columns(min(4, len(ready)))
        for col, (label, path) in zip(cols, ready):
            col.download_button(f"Download {label}", data=path.read_bytes(), file_name=path.name, mime=_mime_for_path(path), use_container_width=True)
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
    return "application/octet-stream"


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _style() -> None:
    st.markdown(
        """
        <style>
        :root {
          --sn-bg: #f5f7fb;
          --sn-card: rgba(255, 255, 255, 0.84);
          --sn-line: rgba(15, 23, 42, 0.10);
          --sn-text: #111827;
          --sn-heading: #0f172a;
          --sn-muted: #64748b;
          --sn-red: #ff3b50;
          --sn-blue: #007aff;
          --sn-green: #30d158;
          --sn-amber: #ffb020;
          --sn-shadow: 0 18px 50px rgba(15,23,42,.08), inset 0 1px 0 rgba(255,255,255,.80);
        }
        .stApp {
          background:
            radial-gradient(circle at 22% 5%, rgba(0, 122, 255, .14), transparent 24rem),
            radial-gradient(circle at 78% 0%, rgba(175, 82, 222, .12), transparent 22rem),
            linear-gradient(180deg, #fbfcff 0%, #f5f7fb 42%, #f7f8fb 100%) !important;
          color: var(--sn-text) !important;
        }
        .stApp h1, .stApp h2, .stApp h3, .stApp h4,
        .stApp [data-testid="stMarkdownContainer"] h1,
        .stApp [data-testid="stMarkdownContainer"] h2,
        .stApp [data-testid="stMarkdownContainer"] h3,
        .stApp [data-testid="stMarkdownContainer"] p,
        .stApp [data-testid="stWidgetLabel"] p {
          color: var(--sn-heading) !important;
        }
        .stApp [data-testid="stCaptionContainer"], .stApp small { color: var(--sn-muted) !important; }
        .block-container { padding-top: 2.1rem; max-width: 1260px; }
        .hero-card {
          position: relative;
          overflow: hidden;
          padding: 2.1rem 2.35rem;
          margin-bottom: 1.7rem;
          border: 1px solid rgba(255,255,255,.75);
          border-radius: 34px;
          background: linear-gradient(135deg, rgba(255,255,255,.90), rgba(255,255,255,.64));
          box-shadow: 0 28px 90px rgba(31, 41, 55, .14), inset 0 1px 0 rgba(255,255,255,.9);
          backdrop-filter: blur(26px) saturate(1.2);
        }
        .hero-card h1 { font-size: clamp(2.1rem, 4vw, 3.1rem); line-height: 1.02; margin: .25rem 0 .75rem; letter-spacing: -.055em; color: var(--sn-heading) !important; }
        .hero-card p { max-width: 780px; color: #5f6673 !important; font-size: 1.05rem; line-height: 1.65; }
        .hero-kicker { color: var(--sn-blue); text-transform: uppercase; font-size: .72rem; letter-spacing: .12em; font-weight: 800; }
        .hero-glow { position: absolute; border-radius: 999px; filter: blur(10px); pointer-events: none; }
        .hero-glow-a { width: 240px; height: 240px; right: -70px; top: -90px; background: rgba(0,122,255,.22); }
        .hero-glow-b { width: 300px; height: 300px; right: 160px; bottom: -210px; background: rgba(255,45,85,.13); }
        section[data-testid="stSidebar"] {
          background: rgba(248,250,252,.86) !important;
          backdrop-filter: blur(22px);
          border-right: 1px solid rgba(148,163,184,.18);
        }
        section[data-testid="stSidebar"] h1,
        section[data-testid="stSidebar"] h2,
        section[data-testid="stSidebar"] h3,
        section[data-testid="stSidebar"] p,
        section[data-testid="stSidebar"] label,
        section[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p {
          color: var(--sn-heading) !important;
        }
        .stButton>button, .stDownloadButton>button {
          border-radius: 999px !important;
          padding: .72rem 1.1rem !important;
          font-weight: 760 !important;
          transition: transform .18s ease, box-shadow .18s ease, border-color .18s ease;
        }
        .stButton>button:hover, .stDownloadButton>button:hover {
          transform: translateY(-1px);
          box-shadow: 0 12px 30px rgba(15,23,42,.12);
        }
        div[data-testid="stMetric"], .status-card, .output-path {
          border: 1px solid var(--sn-line);
          background: var(--sn-card);
          border-radius: 24px;
          box-shadow: var(--sn-shadow);
          backdrop-filter: blur(18px) saturate(1.2);
        }
        div[data-testid="stMetric"] { padding: 16px; }
        .status-card {
          min-height: 122px;
          padding: 14px 13px;
          overflow: hidden;
        }
        .status-top {
          display: flex;
          align-items: center;
          gap: .38rem;
          color: var(--sn-muted) !important;
          font-size: clamp(.68rem, .9vw, .78rem);
          line-height: 1.15;
          font-weight: 780;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .status-main {
          font-size: clamp(1.02rem, 1.8vw, 1.28rem);
          line-height: 1.12;
          letter-spacing: -.02em;
          font-weight: 840;
          margin-top: .82rem;
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
        .tone-good .status-main { color: #138a3d !important; }
        .tone-bad .status-main { color: #c6283a !important; }
        .tone-muted .status-main { color: #64748b !important; }
        .output-path { padding: 14px 18px; margin-bottom: 1rem; color: var(--sn-muted); }
        .output-path code { color: #111827; font-size: .86rem; }
        div[data-baseweb="tab-list"] { gap: .35rem; }
        button[data-baseweb="tab"] { border-radius: 999px !important; }
        @media (max-width: 980px) {
          .status-card { min-height: 116px; }
          .status-main { font-size: 1.16rem; }
          .status-top { font-size: .68rem; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
