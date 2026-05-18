from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import streamlit as st

try:
    from slidenote.costing import write_cost_report
except Exception:  # pragma: no cover - GUI fallback only
    write_cost_report = None

from gui.studio_core import (
    DEFAULT_MODELS,
    VISION_DEFAULT_MODELS,
    StudioConfig,
    build_env,
    build_slidenote_command,
    command_for_display,
    discover_outputs,
    masked_key_status,
    performance_tips,
    progress_percent,
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


def main() -> None:
    st.set_page_config(page_title="SlideNote Studio", page_icon="📝", layout="wide")
    _style()
    st.title("📝 SlideNote Studio")
    st.caption("Upload slides, connect API keys in the page, run with concurrency/cache controls, and monitor tokens/costs without touching the command line.")

    RUNS_DIR.mkdir(exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    GLOBAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)

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

    col_left, col_right = st.columns([0.95, 1.05], gap="large")

    with col_left:
        st.subheader("Run settings")
        speed_mode = st.segmented_control("Speed mode", ["fast", "balanced", "quality", "debug"], default=str(preset["speed_mode"]))
        concurrency = st.slider("Concurrency", 1, 10, int(preset["concurrency"]), help="Parallel API calls. Higher is faster but may hit rate limits.")
        cache_mode = st.selectbox("LLM cache", ["on", "refresh", "off"], index=0)
        use_global_cache = st.toggle("Use shared global cache", value=True, help="Keeps OCR/Vision/LLM cache across different GUI runs.")
        refresh_pages = st.text_input("Refresh only these pages", value="", placeholder="Example: 3,5-8", help="Bypass cache for selected slide IDs only.")

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
            export_options = st.multiselect("Extra exports", ["markdown-toc", "docx", "pdf", "latex"], default=[])

        if use_llm and not api_key:
            st.warning("Text LLM is enabled but no API key is entered. You can still run only if the key already exists in your environment.")
        if vision != "off" and not vision_api_key and vision_provider != provider:
            st.info("Vision provider is different from text provider. Add a vision key or make sure it exists in environment variables.")

    with col_right:
        st.subheader("Connection and runtime overview")
        metric_cols = st.columns(4)
        metric_cols[0].metric("Text API", masked_key_status(api_key or os.getenv(f"{provider.upper()}_API_KEY")))
        metric_cols[1].metric("Vision API", masked_key_status(vision_api_key or os.getenv(f"{vision_provider.upper()}_API_KEY")))
        metric_cols[2].metric("Concurrency", concurrency)
        metric_cols[3].metric("Cache", cache_mode)

        dummy_input = ROOT / "example.pdf"
        dummy_out = OUTPUTS_DIR / "preview"
        dummy_progress = dummy_out / "progress.json"
        preview_config = StudioConfig(
            input_path=dummy_input,
            output_dir=dummy_out,
            progress_json=dummy_progress,
            speed_mode=speed_mode,
            concurrency=concurrency,
            global_cache_dir=GLOBAL_CACHE_DIR if use_global_cache else None,
            refresh_pages=refresh_pages.strip() or None,
            use_llm=use_llm,
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
            export=",".join(export_options) if export_options else None,
        )
        tips = performance_tips(preview_config)
        if tips:
            st.info("\n".join(f"• {tip}" for tip in tips))
        else:
            st.success("Settings are already speed-friendly.")

        with st.expander("Generated command preview", expanded=False):
            st.code(command_for_display(build_slidenote_command(preview_config)), language="bash")

        run_clicked = st.button("🚀 Run SlideNote build", type="primary", use_container_width=True, disabled=uploaded is None)

    if run_clicked and uploaded is not None:
        input_path, output_dir, progress_json = _prepare_run_paths(uploaded)
        config = StudioConfig(
            input_path=input_path,
            output_dir=output_dir,
            progress_json=progress_json,
            speed_mode=speed_mode,
            concurrency=concurrency,
            global_cache_dir=GLOBAL_CACHE_DIR if use_global_cache else None,
            refresh_pages=refresh_pages.strip() or None,
            use_llm=use_llm,
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
            export=",".join(export_options) if export_options else None,
        )
        _run_build(config)
        st.session_state["last_output_dir"] = str(output_dir)

    last_output_dir = Path(st.session_state.get("last_output_dir", "")) if st.session_state.get("last_output_dir") else None
    if last_output_dir and last_output_dir.exists():
        st.divider()
        _render_results(last_output_dir)


def _prepare_run_paths(uploaded) -> tuple[Path, Path, Path]:
    run_name = f"{safe_run_name(uploaded.name)}_{int(time.time())}"
    input_path = UPLOADS_DIR / f"{run_name}{Path(uploaded.name).suffix.lower()}"
    input_path.write_bytes(uploaded.getbuffer())
    output_dir = OUTPUTS_DIR / run_name
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
        st.success(f"Build finished: {config.output_dir}")
    else:
        st.error(f"Build failed with exit code {process.returncode}. Check the log above.")


def _update_progress_ui(progress_path: Path, progress_bar, status_box, stage_box) -> None:
    progress = _read_json(progress_path)
    if not progress:
        status_box.info("Waiting for progress.json...")
        return
    pct = progress_percent(progress)
    progress_bar.progress(min(max(pct, 0.0), 1.0))
    status = progress.get("status", "running")
    message = progress.get("message", "")
    elapsed = progress.get("elapsed_seconds", 0)
    status_box.markdown(f"**Status:** `{status}` · **Elapsed:** `{elapsed}` s · {message}")
    current = progress.get("current_stage")
    if current:
        stage_box.json(current)
    else:
        stages = progress.get("stages") or []
        if stages:
            stage_box.json(stages[-1])


def _generate_cost_report(output_dir: Path) -> None:
    if write_cost_report is None:
        return
    try:
        write_cost_report(output_dir, PRICING_PATH if PRICING_PATH.exists() else None, currency="USD")
    except Exception as exc:
        st.warning(f"Build finished, but cost report generation failed: {exc}")


def _render_results(output_dir: Path) -> None:
    st.subheader("Results")
    outputs = discover_outputs(output_dir)
    tab_names = ["Token & cost", "Notes", "Coverage", "Run summary", "Files"]
    tabs = st.tabs(tab_names)

    with tabs[0]:
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

    with tabs[1]:
        _render_markdown_file(output_dir / "notes.md", "notes.md")
    with tabs[2]:
        _render_markdown_file(output_dir / "coverage.md", "coverage.md")
    with tabs[3]:
        for filename in ["run_summary.json", "llm_usage.json", "vision_usage.json", "ocr_usage.json", "progress.json"]:
            path = output_dir / filename
            if path.exists():
                with st.expander(filename, expanded=filename == "run_summary.json"):
                    st.json(_read_json(path) or {})
    with tabs[4]:
        st.write(f"Output directory: `{output_dir}`")
        for path in sorted(output_dir.glob("*")):
            if path.is_file():
                _download_file(path)


def _render_markdown_file(path: Path, label: str) -> None:
    if not path.exists():
        st.info(f"{label} not found.")
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    st.download_button(f"Download {label}", data=text.encode("utf-8"), file_name=label, mime="text/markdown")
    st.markdown(text)


def _download_file(path: Path) -> None:
    mime = "application/octet-stream"
    if path.suffix == ".md":
        mime = "text/markdown"
    elif path.suffix == ".json":
        mime = "application/json"
    elif path.suffix == ".html":
        mime = "text/html"
    st.download_button(f"Download {path.name}", data=path.read_bytes(), file_name=path.name, mime=mime)


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
        .stButton>button { border-radius: 14px; padding: .65rem 1rem; font-weight: 700; }
        div[data-testid="stMetric"] { background: #ffffff; border: 1px solid #eceff3; padding: 14px; border-radius: 16px; box-shadow: 0 6px 18px rgba(15,23,42,.04); }
        section[data-testid="stSidebar"] { background: #fafafa; }
        </style>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
