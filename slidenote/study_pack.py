from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

from slidenote.content_guard import REQUIRED_CONFIDENCE_THRESHOLD
from slidenote.exporting import clean_markdown_for_export
from slidenote.llm import LLMClient, resolve_provider_runtime
from slidenote.llm_cache import LLM_CACHE_SCHEMA_VERSION, LLMCache, make_cache_key, sha256_text, stable_json, utc_now_iso
from slidenote.models import Deck, ImageAsset, SlidePage, TableBlock, TextBlock


STUDY_PACK_MODES = {"off", "auto", "local", "llm"}
STUDY_PACK_PROMPT_VERSION = "study-pack-v1"
STUDY_PACK_SYSTEM_PROMPT = (
    "You are an exam-oriented course review designer. Return strict JSON only. "
    "Do not include Markdown fences, apologies, or task explanations."
)

IMPORTANCE_LABELS = {
    "must": "必考",
    "key": "重点",
    "frequent": "高频",
    "background": "了解",
}

QUESTION_TYPE_LABELS = {
    "choice": "选择题",
    "true_false": "判断题",
    "short": "简答题",
    "essay": "论述题",
    "comprehensive": "综合题",
}


def build_study_pack(
    deck: Deck,
    notes_markdown: str,
    output_root: Path,
    review_mode: str = "off",
    exam_mode: str = "off",
    question_count: int = 12,
    use_llm: bool = False,
    provider: str = "openai",
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    cache_mode: str = "on",
    cache_dir: Path | None = None,
    max_output_tokens: int = 4096,
    temperature: float | None = 0.0,
    note_language: str = "zh",
    section_plan: dict[str, Any] | None = None,
    deck_brief: dict[str, Any] | None = None,
    content_guard: dict[str, Any] | None = None,
    coverage_report: dict[str, Any] | None = None,
    source_map: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if review_mode not in STUDY_PACK_MODES:
        raise ValueError(f"review_mode must be one of: {', '.join(sorted(STUDY_PACK_MODES))}")
    if exam_mode not in STUDY_PACK_MODES:
        raise ValueError(f"exam_mode must be one of: {', '.join(sorted(STUDY_PACK_MODES))}")

    review_requested = review_mode != "off"
    exam_requested = exam_mode != "off"
    if not review_requested and not exam_requested:
        return None

    effective_review_mode = _effective_mode(review_mode, use_llm) if review_requested else "off"
    effective_exam_mode = _effective_mode(exam_mode, use_llm) if exam_requested else "off"
    requested_question_count = max(1, min(int(question_count or 12), 60))

    local_data = _build_local_data(
        deck=deck,
        notes_markdown=notes_markdown,
        section_plan=section_plan,
        deck_brief=deck_brief,
        content_guard=content_guard,
        question_count=requested_question_count,
    )
    final_data = {
        "review": local_data.get("review") if review_requested else None,
        "exam": local_data.get("exam") if exam_requested else None,
    }

    warnings: list[str] = []
    llm_record: dict[str, Any] | None = None
    generator = "local"
    if effective_review_mode == "llm" or effective_exam_mode == "llm":
        llm_data, llm_record, llm_warnings = _generate_llm_data(
            deck=deck,
            notes_markdown=notes_markdown,
            output_root=output_root,
            review_requested=review_requested,
            exam_requested=exam_requested,
            question_count=requested_question_count,
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            cache_mode=cache_mode,
            cache_dir=cache_dir,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            note_language=note_language,
            section_plan=section_plan,
            deck_brief=deck_brief,
            content_guard=content_guard,
            coverage_report=coverage_report,
            source_map=source_map,
            fallback=local_data,
        )
        warnings.extend(llm_warnings)
        if llm_data is not None:
            if effective_review_mode == "llm" and review_requested:
                final_data["review"] = llm_data.get("review") or final_data["review"]
            if effective_exam_mode == "llm" and exam_requested:
                final_data["exam"] = llm_data.get("exam") or final_data["exam"]
            generator = "llm" if effective_review_mode == effective_exam_mode else "mixed"
        else:
            generator = "local_fallback"

    review = final_data.get("review") if isinstance(final_data.get("review"), dict) else None
    exam = final_data.get("exam") if isinstance(final_data.get("exam"), dict) else None
    figure_table_notes = _figure_table_notes(deck)
    if review is not None:
        review["figure_table_notes"] = figure_table_notes
    if exam is not None:
        _attach_inline_figure_refs(exam, figure_table_notes)
    question_quality = build_question_quality_report({"exam": exam, "figure_table_notes": figure_table_notes})
    section_study_pack = _build_section_study_pack(review, exam, figure_table_notes) if review or exam else None
    final_exam = _build_final_exam(exam) if exam else None
    wrong_answer_review = _build_wrong_answer_review(exam, question_quality) if exam else None
    exam_review_pack = (
        {
            "schema_version": 1,
            "review": review,
            "exam": exam,
            "final_exam": final_exam,
            "question_quality": question_quality,
            "wrong_answer_review": wrong_answer_review,
        }
        if review or exam
        else None
    )
    return {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "prompt_version": STUDY_PACK_PROMPT_VERSION,
        "generator": generator,
        "modes": {
            "review": review_mode,
            "exam": exam_mode,
            "effective_review": effective_review_mode,
            "effective_exam": effective_exam_mode,
        },
        "request": {
            "question_count": requested_question_count,
            "note_language": note_language,
        },
        "review": review,
        "exam": exam,
        "figure_table_notes": figure_table_notes,
        "section_study_pack": section_study_pack,
        "exam_review_pack": exam_review_pack,
        "final_exam": final_exam,
        "wrong_answer_review": wrong_answer_review,
        "question_quality": question_quality,
        "llm": llm_record,
        "summary": {
            "review_enabled": review is not None,
            "exam_enabled": exam is not None,
            "review_items_total": len(review.get("checklist") or []) if review else 0,
            "logic_chains_total": len(review.get("logic_chains") or []) if review else 0,
            "questions_total": len(exam.get("questions") or []) if exam else 0,
            "question_quality_score": question_quality.get("overall_score"),
            "choice_distractor_score": question_quality.get("choice_distractor_score"),
            "figure_question_score": question_quality.get("figure_question_score"),
            "section_study_pack_sections": len(section_study_pack.get("sections") or []) if section_study_pack else 0,
            "final_exam_questions_total": len(final_exam.get("questions") or []) if final_exam else 0,
            "llm_call": bool(llm_record and llm_record.get("llm_call")),
            "local_cache_hits": 1 if llm_record and llm_record.get("cache_status") == "local_hit" else 0,
            "input_tokens": (llm_record or {}).get("input_tokens") or 0,
            "output_tokens": (llm_record or {}).get("output_tokens") or 0,
            "total_tokens": (llm_record or {}).get("total_tokens") or 0,
        },
        "warnings": warnings,
        "artifacts": {
            "study_pack": "study_pack.json",
            "review_markdown": "review.md" if review else None,
            "exam_markdown": "exam.md" if exam else None,
            "exam_json": "exam.json" if exam else None,
            "exam_html": "exam.html" if exam else None,
            "section_study_pack": "section_study_pack.json" if section_study_pack else None,
            "exam_review_pack": "exam_review_pack.json" if exam_review_pack else None,
            "final_exam_markdown": "final_exam.md" if final_exam else None,
            "final_exam_answers": "final_exam.answers.md" if final_exam else None,
            "wrong_answer_review_prompt": "wrong_answer_review_prompt.md" if wrong_answer_review else None,
        },
    }


def render_review_markdown(report: dict[str, Any]) -> str:
    review = report.get("review") if isinstance(report.get("review"), dict) else {}
    title = _clean_inline(review.get("title")) or _source_title(report)
    lines = [f"# {title} - 复习清单", ""]
    summary = _clean_inline(review.get("summary"))
    if summary:
        lines.extend(["## 复习总览", "", summary, ""])

    logic_chains = _dict_list(review.get("logic_chains"), limit=20)
    if logic_chains:
        lines.extend(["## 逻辑链", ""])
        for chain in logic_chains:
            chain_title = _clean_inline(chain.get("title")) or "知识链条"
            steps = _string_list(chain.get("steps") or chain.get("chain"), limit=8)
            lines.append(f"### {chain_title}")
            if steps:
                for index, step in enumerate(steps, start=1):
                    lines.append(f"{index}. {step}")
            else:
                text = _clean_inline(chain.get("summary") or chain.get("reason"))
                if text:
                    lines.append(text)
            lines.append("")

    checklist = _dict_list(review.get("checklist"), limit=400)
    if checklist:
        lines.extend(["## 考点清单", ""])
        current_section = None
        for item in checklist:
            section = _clean_inline(item.get("section")) or "核心知识点"
            if section != current_section:
                lines.extend([f"### {section}", ""])
                current_section = section
            label = IMPORTANCE_LABELS.get(str(item.get("importance") or "key"), "重点")
            point = _clean_inline(item.get("point")) or "知识点"
            explanation = _clean_inline(item.get("explanation")) or "需要结合原始笔记复习。"
            lines.append(f"- **[{label}] {point}**：{explanation}")
            why = _clean_inline(item.get("why"))
            if why:
                lines.append(f"  - 为什么考：{why}")
            pitfall = _clean_inline(item.get("pitfall"))
            if pitfall:
                lines.append(f"  - 易错点：{pitfall}")
            source_refs = _string_list(item.get("source_refs"), limit=8)
            if source_refs:
                lines.append(f"  - 来源：{', '.join(source_refs)}")
        lines.append("")

    methods = _dict_list(review.get("methods"), limit=100)
    if methods:
        lines.extend(["## 解题方法与记忆抓手", ""])
        for method in methods:
            name = _clean_inline(method.get("name")) or _clean_inline(method.get("point")) or "方法"
            detail = _clean_inline(method.get("detail") or method.get("explanation"))
            lines.append(f"- **{name}**" + (f"：{detail}" if detail else ""))
            example = _clean_inline(method.get("example"))
            if example:
                lines.append(f"  - 例子：{example}")
        lines.append("")

    figure_table_notes = _dict_list(review.get("figure_table_notes"), limit=120)
    if figure_table_notes:
        lines.extend(["## 图表 / 公式速查", ""])
        for note in figure_table_notes:
            title = _clean_inline(note.get("title")) or "图表"
            explanation = _clean_inline(note.get("explanation"))
            source_ref = _clean_inline(note.get("source_ref"))
            lines.append(f"- **{title}**" + (f"：{explanation}" if explanation else ""))
            if note.get("kind") == "image" and _clean_inline(note.get("path")):
                lines.append(f"  - ![{title}]({_clean_inline(note.get('path'))})")
            if source_ref:
                lines.append(f"  - 来源：{source_ref}")
        lines.append("")

    warnings = report.get("warnings") or []
    if warnings:
        lines.extend(["## 生成提示", ""])
        lines.extend(f"- {warning}" for warning in warnings)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_exam_markdown(report: dict[str, Any]) -> str:
    exam = report.get("exam") if isinstance(report.get("exam"), dict) else {}
    title = _clean_inline(exam.get("title")) or _source_title(report)
    questions = _dict_list(exam.get("questions"), limit=200)
    lines = [f"# {title} - 自测题", ""]
    subtitle = _clean_inline(exam.get("subtitle"))
    if subtitle:
        lines.extend([subtitle, ""])
    lines.extend(["## 题目", ""])
    for index, question in enumerate(questions, start=1):
        qtype = _normalize_question_type(question.get("type"))
        label = QUESTION_TYPE_LABELS.get(qtype, "题目")
        points = _as_int(question.get("points"), _default_points(qtype))
        lines.append(f"{index}. 【{label} · {points} 分】{_clean_inline(question.get('question'))}")
        if qtype == "choice":
            for option_index, option in enumerate(_string_list(question.get("options"), limit=8)):
                lines.append(f"   {chr(65 + option_index)}. {option}")
        for image_ref in _dict_list(question.get("image_refs"), limit=3):
            path = _clean_inline(image_ref.get("path"))
            title = _clean_inline(image_ref.get("title")) or "题目图"
            if path:
                lines.append(f"   ![{title}]({path})")
        if qtype in {"short", "essay", "comprehensive"}:
            lines.append("")
            lines.append("   答：")
        lines.append("")

    lines.extend(["## 答案与解析", ""])
    for index, question in enumerate(questions, start=1):
        qtype = _normalize_question_type(question.get("type"))
        answer = _answer_text(question, qtype)
        explanation = _clean_inline(question.get("explanation")) or "复习对应知识点后再核对答案。"
        lines.append(f"{index}. **答案**：{answer}")
        lines.append(f"   **解析**：{explanation}")
        pitfall = _clean_inline(question.get("pitfall"))
        if pitfall:
            lines.append(f"   **易错提醒**：{pitfall}")
        source_refs = _string_list(question.get("source_refs"), limit=8)
        if source_refs:
            lines.append(f"   **来源**：{', '.join(source_refs)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_final_exam_markdown(report: dict[str, Any]) -> str:
    final_exam = report.get("final_exam") if isinstance(report.get("final_exam"), dict) else {}
    title = _clean_inline(final_exam.get("title")) or _source_title(report)
    questions = _dict_list(final_exam.get("questions"), limit=200)
    total_points = sum(_as_int(question.get("points"), 0) for question in questions)
    duration = _as_int(final_exam.get("duration_minutes"), max(30, len(questions) * 5))
    lines = [f"# {title}", "", f"- 建议时长：{duration} 分钟", f"- 总分：{total_points} 分", ""]
    lines.extend(["## 试题", ""])
    for index, question in enumerate(questions, start=1):
        qtype = _normalize_question_type(question.get("type"))
        label = QUESTION_TYPE_LABELS.get(qtype, "题目")
        points = _as_int(question.get("points"), _default_points(qtype))
        lines.append(f"{index}. 【{label} · {points} 分】{_clean_inline(question.get('question'))}")
        if qtype == "choice":
            for option_index, option in enumerate(_string_list(question.get("options"), limit=8)):
                lines.append(f"   {chr(65 + option_index)}. {option}")
        for image_ref in _dict_list(question.get("image_refs"), limit=3):
            path = _clean_inline(image_ref.get("path"))
            title_text = _clean_inline(image_ref.get("title")) or "题目图"
            if path:
                lines.append(f"   ![{title_text}]({path})")
        if qtype in {"short", "essay", "comprehensive"}:
            lines.extend(["", "   答："])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_final_exam_answers_markdown(report: dict[str, Any]) -> str:
    final_exam = report.get("final_exam") if isinstance(report.get("final_exam"), dict) else {}
    title = _clean_inline(final_exam.get("title")) or _source_title(report)
    questions = _dict_list(final_exam.get("questions"), limit=200)
    lines = [f"# {title} - 答案与评分提示", ""]
    for index, question in enumerate(questions, start=1):
        qtype = _normalize_question_type(question.get("type"))
        answer = _answer_text(question, qtype)
        explanation = _clean_inline(question.get("explanation")) or "复习对应知识点后再核对答案。"
        lines.append(f"{index}. **答案**：{answer}")
        lines.append(f"   **解析**：{explanation}")
        pitfall = _clean_inline(question.get("pitfall"))
        if pitfall:
            lines.append(f"   **易错提醒**：{pitfall}")
        source_refs = _string_list(question.get("source_refs"), limit=8)
        if source_refs:
            lines.append(f"   **来源**：{', '.join(source_refs)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_wrong_answer_review_prompt(report: dict[str, Any]) -> str:
    wrong_review = report.get("wrong_answer_review") if isinstance(report.get("wrong_answer_review"), dict) else {}
    title = _clean_inline(wrong_review.get("title")) or "错题复盘 Prompt"
    template = wrong_review.get("prompt_template") or _wrong_answer_prompt_template([])
    lines = [f"# {title}", ""]
    lines.append("把 `exam.html` 批改后显示的错题 JSON 粘贴到下面占位处，再交给你选择的学习助手。")
    lines.extend(["", "```text", template, "```", ""])
    return "\n".join(lines)


def render_exam_html(report: dict[str, Any]) -> str:
    exam = report.get("exam") if isinstance(report.get("exam"), dict) else {}
    title = _clean_inline(exam.get("title")) or _source_title(report)
    questions = _dict_list(exam.get("questions"), limit=200)
    payload = json.dumps(questions, ensure_ascii=False).replace("</", "<\\/")
    escaped_title = html.escape(f"{title} - 自测题")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escaped_title}</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #1f2937;
      --muted: #64748b;
      --line: #d7dee8;
      --paper: #fffdf8;
      --accent: #2563eb;
      --ok: #15803d;
      --bad: #b91c1c;
    }}
    body {{
      margin: 0;
      background: #eef2f7;
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
      line-height: 1.65;
    }}
    main {{
      max-width: 920px;
      margin: 0 auto;
      padding: 32px 18px 56px;
    }}
    h1 {{ margin: 0 0 8px; font-size: 30px; }}
    .subtle {{ color: var(--muted); margin-bottom: 22px; }}
    .score {{
      display: none;
      margin: 18px 0;
      padding: 14px 16px;
      border: 1px solid var(--line);
      background: white;
    }}
    .question {{
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
      margin: 14px 0;
    }}
    .meta {{ color: var(--muted); font-size: 14px; margin-bottom: 8px; }}
    label.option {{
      display: block;
      cursor: pointer;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px 12px;
      margin: 8px 0;
      background: white;
    }}
    label.option.selected {{ border-color: var(--accent); }}
    label.option.correct {{ border-color: var(--ok); color: var(--ok); }}
    label.option.wrong {{ border-color: var(--bad); color: var(--bad); }}
    textarea {{
      width: 100%;
      min-height: 96px;
      box-sizing: border-box;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px;
      font: inherit;
    }}
    .result {{
      display: none;
      margin-top: 12px;
      border-top: 1px solid var(--line);
      padding-top: 12px;
    }}
    .question-image {{
      max-width: 100%;
      margin: 10px 0;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: white;
    }}
    .wrong-review {{
      display: none;
      margin-top: 18px;
      padding: 16px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: white;
    }}
    .wrong-review textarea {{ min-height: 220px; }}
    .badge {{
      display: inline-block;
      font-weight: 700;
      margin-right: 8px;
    }}
    .ok {{ color: var(--ok); }}
    .bad {{ color: var(--bad); }}
    button {{
      appearance: none;
      border: 0;
      border-radius: 6px;
      background: var(--accent);
      color: white;
      padding: 11px 18px;
      font: inherit;
      cursor: pointer;
    }}
    button:disabled {{ opacity: .55; cursor: default; }}
    @media print {{
      body {{ background: white; }}
      button, textarea {{ display: none; }}
      .question {{ break-inside: avoid; }}
      .result {{ display: block; }}
    }}
  </style>
</head>
<body>
  <main>
    <h1>{escaped_title}</h1>
    <div class="subtle">选择/判断题可自动批改；简答和综合题会显示参考解析。</div>
    <div id="score" class="score"></div>
    <div id="questions"></div>
    <button id="grade" type="button">一键批改</button>
    <section id="wrongReview" class="wrong-review">
      <h2>错题复盘</h2>
      <p class="subtle">复制下面的 Prompt 给学习助手，让它按知识点、错因和来源页帮你复盘。</p>
      <textarea id="wrongPrompt" readonly></textarea>
    </section>
  </main>
  <script>
    const QUESTIONS = {payload};
    const labels = {{choice:"选择题", true_false:"判断题", short:"简答题", essay:"论述题", comprehensive:"综合题"}};
    function esc(value) {{
      return String(value ?? "").replace(/[&<>"']/g, ch => ({{"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;","'":"&#39;"}}[ch]));
    }}
    function answerText(q) {{
      if (q.type === "choice") return String.fromCharCode(65 + Number(q.answer || 0)) + ". " + esc((q.options || [])[Number(q.answer || 0)] || "");
      if (q.type === "true_false") return q.answer ? "正确" : "错误";
      return esc(q.answer || "见解析");
    }}
    function build() {{
      const root = document.getElementById("questions");
      root.innerHTML = QUESTIONS.map((q, i) => {{
        const type = q.type || "short";
        let body = `<div class="question" id="q${{i}}"><div class="meta">${{i + 1}}. ${{labels[type] || "题目"}} · ${{q.points || 0}} 分</div><div><strong>${{esc(q.question)}}</strong></div>`;
        if (type === "choice") {{
          body += (q.options || []).map((opt, j) => `<label class="option" id="q${{i}}o${{j}}"><input type="radio" name="q${{i}}" value="${{j}}"> ${{String.fromCharCode(65 + j)}}. ${{esc(opt)}}</label>`).join("");
        }} else if (type === "true_false") {{
          body += `<label class="option" id="q${{i}}o0"><input type="radio" name="q${{i}}" value="true"> 正确</label>`;
          body += `<label class="option" id="q${{i}}o1"><input type="radio" name="q${{i}}" value="false"> 错误</label>`;
        }} else {{
          body += `<textarea placeholder="在这里写下你的答案"></textarea>`;
        }}
        if (Array.isArray(q.image_refs) && q.image_refs.length) {{
          body += q.image_refs.map(ref => ref.path ? `<img class="question-image" src="${{esc(ref.path)}}" alt="${{esc(ref.title || "题目图")}}">` : "").join("");
        }}
        body += `<div class="result" id="r${{i}}"><span class="badge"></span><div><strong>参考答案：</strong>${{answerText(q)}}</div><div><strong>解析：</strong>${{esc(q.explanation || "")}}</div>${{q.pitfall ? `<div><strong>易错提醒：</strong>${{esc(q.pitfall)}}</div>` : ""}}</div></div>`;
        return body;
      }}).join("");
      document.querySelectorAll("label.option").forEach(label => {{
        label.addEventListener("click", () => {{
          document.querySelectorAll(`label.option input[name="${{label.querySelector("input").name}}"]`).forEach(input => input.parentElement.classList.remove("selected"));
          label.classList.add("selected");
        }});
      }});
    }}
    function grade() {{
      let score = 0;
      let possible = 0;
      const wrong = [];
      QUESTIONS.forEach((q, i) => {{
        const result = document.getElementById(`r${{i}}`);
        const badge = result.querySelector(".badge");
        result.style.display = "block";
        possible += Number(q.points || 0);
        if (q.type === "choice") {{
          const picked = document.querySelector(`input[name="q${{i}}"]:checked`);
          const ok = picked && Number(picked.value) === Number(q.answer || 0);
          if (ok) score += Number(q.points || 0);
          if (!ok) wrong.push({{index: i + 1, id: q.id, type: q.type, question: q.question, picked: picked ? Number(picked.value) : null, answer: q.answer, source_refs: q.source_refs || [], pitfall: q.pitfall || ""}});
          badge.textContent = ok ? "正确" : "错误";
          badge.className = `badge ${{ok ? "ok" : "bad"}}`;
          if (picked) document.getElementById(`q${{i}}o${{picked.value}}`).classList.add(ok ? "correct" : "wrong");
          const correct = document.getElementById(`q${{i}}o${{Number(q.answer || 0)}}`);
          if (correct) correct.classList.add("correct");
        }} else if (q.type === "true_false") {{
          const picked = document.querySelector(`input[name="q${{i}}"]:checked`);
          const expected = q.answer ? "true" : "false";
          const ok = picked && picked.value === expected;
          if (ok) score += Number(q.points || 0);
          if (!ok) wrong.push({{index: i + 1, id: q.id, type: q.type, question: q.question, picked: picked ? picked.value : null, answer: expected, source_refs: q.source_refs || [], pitfall: q.pitfall || ""}});
          badge.textContent = ok ? "正确" : "错误";
          badge.className = `badge ${{ok ? "ok" : "bad"}}`;
        }} else {{
          badge.textContent = "参考解析";
          badge.className = "badge ok";
        }}
      }});
      const box = document.getElementById("score");
      box.style.display = "block";
      box.innerHTML = `<strong>客观题得分：</strong>${{score}} / ${{possible}}`;
      const reviewBox = document.getElementById("wrongReview");
      const promptBox = document.getElementById("wrongPrompt");
      reviewBox.style.display = "block";
      promptBox.value = buildWrongPrompt(wrong);
      document.getElementById("grade").disabled = true;
      box.scrollIntoView({{behavior: "smooth"}});
    }}
    function buildWrongPrompt(wrong) {{
      return [
        "请基于下面的错题记录，帮我做一次课程错题复盘。",
        "",
        "要求：",
        "1. 按知识点归类，而不是逐题流水账。",
        "2. 分析我的可能错因：概念混淆、条件遗漏、图表没读懂、公式变量不清、只背结论等。",
        "3. 每个错因给出重新学习建议、回看来源页、同类变式题。",
        "4. 不要编造课件没有依据的具体事实；如果需要补背景，请标明是帮助理解的通用解释。",
        "",
        "错题 JSON：",
        JSON.stringify(wrong, null, 2)
      ].join("\\n");
    }}
    build();
    document.getElementById("grade").addEventListener("click", grade);
  </script>
</body>
</html>
"""


def _effective_mode(mode: str, use_llm: bool) -> str:
    if mode == "auto":
        return "llm" if use_llm else "local"
    return mode


def _build_local_data(
    deck: Deck,
    notes_markdown: str,
    section_plan: dict[str, Any] | None,
    deck_brief: dict[str, Any] | None,
    content_guard: dict[str, Any] | None,
    question_count: int,
) -> dict[str, Any]:
    title = _deck_title(deck, deck_brief)
    items = _collect_study_items(deck, notes_markdown, content_guard, limit=max(question_count * 2, 16))
    review = {
        "title": title,
        "summary": _local_summary(deck, deck_brief),
        "logic_chains": _local_logic_chains(deck, section_plan, deck_brief),
        "checklist": items,
        "methods": _local_methods(items),
    }
    exam = {
        "title": title,
        "subtitle": f"共 {min(question_count, len(items))} 题；建议先独立作答，再核对解析。",
        "questions": _local_questions(items, question_count),
    }
    return {"review": review, "exam": exam}


def build_question_quality_report(study_pack_report: dict[str, Any] | None) -> dict[str, Any]:
    exam = study_pack_report.get("exam") if isinstance(study_pack_report, dict) and isinstance(study_pack_report.get("exam"), dict) else {}
    questions = _dict_list(exam.get("questions"), limit=300)
    figure_table_notes = _dict_list((study_pack_report or {}).get("figure_table_notes"), limit=300) if isinstance(study_pack_report, dict) else []
    if not questions:
        return {
            "schema_version": 1,
            "overall_score": 0.0,
            "choice_distractor_score": 0.0,
            "explanation_score": 0.0,
            "source_ref_score": 0.0,
            "question_mix_score": 0.0,
            "figure_question_score": 1.0 if not figure_table_notes else 0.0,
            "mechanical_definition_score": 0.0,
            "flags": ["no_questions"],
        }
    choice_questions = [question for question in questions if _normalize_question_type(question.get("type")) == "choice"]
    source_ref_hits = sum(1 for question in questions if _string_list(question.get("source_refs"), limit=8))
    explanation_hits = sum(1 for question in questions if _clean_inline(question.get("explanation")) and _clean_inline(question.get("pitfall")))
    applied_hits = sum(1 for question in questions if _is_applied_question(str(question.get("question") or "")))
    figure_hits = sum(1 for question in questions if _dict_list(question.get("image_refs"), limit=4))
    type_count = len({_normalize_question_type(question.get("type")) for question in questions})
    choice_score = _choice_distractor_score(choice_questions)
    explanation_score = explanation_hits / max(1, len(questions))
    source_ref_score = source_ref_hits / max(1, len(questions))
    question_mix_score = min(1.0, type_count / 4)
    figure_question_score = 1.0 if not figure_table_notes else min(1.0, figure_hits / max(1, min(len(figure_table_notes), len(questions))))
    mechanical_definition_score = 1.0 - min(1.0, applied_hits / max(1, len(questions)))
    overall = (
        0.24 * choice_score
        + 0.22 * explanation_score
        + 0.18 * source_ref_score
        + 0.16 * question_mix_score
        + 0.12 * figure_question_score
        + 0.08 * (1.0 - mechanical_definition_score)
    )
    flags = _question_quality_flags(
        choice_score=choice_score,
        explanation_score=explanation_score,
        source_ref_score=source_ref_score,
        question_mix_score=question_mix_score,
        figure_question_score=figure_question_score,
        mechanical_definition_score=mechanical_definition_score,
        has_figures=bool(figure_table_notes),
    )
    return {
        "schema_version": 1,
        "overall_score": _round_score(overall),
        "choice_distractor_score": _round_score(choice_score),
        "explanation_score": _round_score(explanation_score),
        "source_ref_score": _round_score(source_ref_score),
        "question_mix_score": _round_score(question_mix_score),
        "figure_question_score": _round_score(figure_question_score),
        "mechanical_definition_score": _round_score(mechanical_definition_score),
        "questions_total": len(questions),
        "choice_questions_total": len(choice_questions),
        "flags": flags,
    }


def _figure_table_notes(deck: Deck) -> list[dict[str, Any]]:
    notes: list[dict[str, Any]] = []
    for page in deck.pages:
        section = page.title or f"第 {page.slide_id} 页"
        for table in page.tables:
            explanation = _clean_inline(table.table_conclusion or table.table_summary or _table_text(table))
            if not explanation:
                continue
            notes.append(
                {
                    "id": table.id,
                    "kind": "table",
                    "section": section,
                    "title": f"P{page.slide_id} 表格",
                    "explanation": explanation,
                    "source_ref": f"P{page.slide_id}",
                    "source_ids": [table.id],
                }
            )
        for image in page.images:
            if image.ignored:
                continue
            explanation = _clean_inline(image.figure_explanation or image.visual_summary or image.ocr_text or image.caption)
            if not explanation:
                continue
            notes.append(
                {
                    "id": image.id,
                    "kind": "image",
                    "section": section,
                    "title": _clean_inline(image.caption) or f"P{page.slide_id} 图示",
                    "explanation": explanation,
                    "path": image.path,
                    "source_ref": f"P{page.slide_id}",
                    "source_ids": [image.id, *image.source_element_ids],
                    "importance_score": image.importance_score,
                }
            )
    return notes[:200]


def _attach_inline_figure_refs(exam: dict[str, Any], figure_table_notes: list[dict[str, Any]]) -> None:
    image_notes = [note for note in figure_table_notes if note.get("kind") == "image" and note.get("path")]
    if not image_notes:
        return
    questions = _dict_list(exam.get("questions"), limit=300)
    for question in questions:
        if _dict_list(question.get("image_refs"), limit=4):
            continue
        question_refs = set(_string_list(question.get("source_refs"), limit=8))
        matched = [note for note in image_notes if note.get("source_ref") in question_refs]
        if not matched and _is_figure_question(str(question.get("question") or "") + " " + str(question.get("explanation") or "")):
            matched = image_notes[:1]
        if matched:
            question["image_refs"] = [
                {
                    "id": note.get("id"),
                    "title": note.get("title"),
                    "path": note.get("path"),
                    "source_ref": note.get("source_ref"),
                }
                for note in matched[:2]
            ]


def _build_section_study_pack(review: dict[str, Any] | None, exam: dict[str, Any] | None, figure_table_notes: list[dict[str, Any]]) -> dict[str, Any]:
    sections: dict[str, dict[str, Any]] = {}
    for item in _dict_list((review or {}).get("checklist"), limit=500):
        section = _clean_inline(item.get("section")) or "核心知识点"
        bucket = sections.setdefault(section, {"section": section, "checklist": [], "questions": [], "figure_table_notes": []})
        bucket["checklist"].append(item)
    for question in _dict_list((exam or {}).get("questions"), limit=300):
        refs = _string_list(question.get("source_refs"), limit=8)
        section = _section_for_source_ref(refs, sections) or "综合复习"
        bucket = sections.setdefault(section, {"section": section, "checklist": [], "questions": [], "figure_table_notes": []})
        bucket["questions"].append(question)
    for note in figure_table_notes:
        section = _clean_inline(note.get("section")) or "图表速查"
        bucket = sections.setdefault(section, {"section": section, "checklist": [], "questions": [], "figure_table_notes": []})
        bucket["figure_table_notes"].append(note)
    return {"schema_version": 1, "sections": list(sections.values())}


def _build_final_exam(exam: dict[str, Any]) -> dict[str, Any]:
    questions = _dict_list(exam.get("questions"), limit=200)
    total_points = sum(_as_int(question.get("points"), _default_points(_normalize_question_type(question.get("type")))) for question in questions)
    return {
        "title": f"{_clean_inline(exam.get('title')) or '课程'} - 期末模拟卷",
        "mode": "mock_final",
        "duration_minutes": max(30, min(180, len(questions) * 6)),
        "total_points": total_points,
        "instructions": "先独立完成，再核对 final_exam.answers.md；错题回到来源页和 review.md 对应章节复盘。",
        "questions": questions,
    }


def _build_wrong_answer_review(exam: dict[str, Any], question_quality: dict[str, Any]) -> dict[str, Any]:
    questions = _dict_list(exam.get("questions"), limit=300)
    return {
        "title": f"{_clean_inline(exam.get('title')) or '课程'} - 错题复盘",
        "question_quality": question_quality,
        "prompt_template": _wrong_answer_prompt_template(questions),
    }


def _wrong_answer_prompt_template(questions: list[dict[str, Any]]) -> str:
    compact = [
        {
            "id": question.get("id"),
            "type": question.get("type"),
            "question": question.get("question"),
            "answer": question.get("answer"),
            "source_refs": question.get("source_refs"),
            "pitfall": question.get("pitfall"),
        }
        for question in questions[:80]
    ]
    return (
        "请基于下面的课程自测题和我的错题记录，帮我做一次错题复盘。\n\n"
        "要求：\n"
        "1. 按知识点归类，不要逐题流水账。\n"
        "2. 分析可能错因：概念混淆、条件遗漏、图表没读懂、公式变量不清、只背结论等。\n"
        "3. 每个错因给出重新学习建议、回看来源页、同类变式题。\n"
        "4. 不要编造课件没有依据的具体事实；补充背景时请标明是帮助理解的通用解释。\n\n"
        "题库摘要：\n"
        f"{json.dumps(compact, ensure_ascii=False, indent=2)}\n\n"
        "我的错题 JSON 粘贴在这里：\n"
        "[]\n"
    )


def _generate_llm_data(
    deck: Deck,
    notes_markdown: str,
    output_root: Path,
    review_requested: bool,
    exam_requested: bool,
    question_count: int,
    provider: str,
    model: str | None,
    api_key: str | None,
    base_url: str | None,
    cache_mode: str,
    cache_dir: Path | None,
    max_output_tokens: int,
    temperature: float | None,
    note_language: str,
    section_plan: dict[str, Any] | None,
    deck_brief: dict[str, Any] | None,
    content_guard: dict[str, Any] | None,
    coverage_report: dict[str, Any] | None,
    source_map: dict[str, Any] | None,
    fallback: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[str]]:
    runtime = resolve_provider_runtime(provider, model=model, base_url=base_url)
    resolved_cache_dir = (cache_dir or (output_root / ".cache" / "llm")).resolve()
    cache = LLMCache(resolved_cache_dir, mode=cache_mode)
    prompt = _study_pack_prompt(
        deck=deck,
        notes_markdown=notes_markdown,
        review_requested=review_requested,
        exam_requested=exam_requested,
        question_count=question_count,
        note_language=note_language,
        section_plan=section_plan,
        deck_brief=deck_brief,
        content_guard=content_guard,
        coverage_report=coverage_report,
        source_map=source_map,
    )
    cache_key_payload = {
        "schema_version": LLM_CACHE_SCHEMA_VERSION,
        "prompt_version": STUDY_PACK_PROMPT_VERSION,
        "generation_stage": "study_pack",
        "provider": runtime["provider"],
        "model": runtime["model"],
        "base_url": runtime["base_url"],
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "review_requested": review_requested,
        "exam_requested": exam_requested,
        "question_count": question_count,
        "note_language": note_language,
        "deck_digest": _deck_digest(deck),
        "notes_hash": sha256_text(clean_markdown_for_export(notes_markdown)),
        "section_plan_hash": sha256_text(stable_json(section_plan or {})),
        "deck_brief_hash": sha256_text(stable_json(deck_brief or {})),
        "content_guard_hash": sha256_text(stable_json(content_guard or {})),
        "system_prompt_hash": sha256_text(STUDY_PACK_SYSTEM_PROMPT),
        "user_prompt_hash": sha256_text(prompt),
        "user_prompt": prompt,
    }
    cache_key = make_cache_key(cache_key_payload)
    cache_path = cache.path_for(cache_key)
    prompt_hash = sha256_text(stable_json(cache_key_payload))
    cached = cache.read(cache_key)
    cache_status = "local_hit"
    llm_call = False
    usage: dict[str, Any] = {}
    raw_text = ""
    warnings: list[str] = []

    try:
        if cached:
            raw_text = str(cached.get("output_text") or "")
            usage = cached.get("response_usage") or {}
        else:
            client = LLMClient(
                provider=str(runtime["provider"]),
                model=str(runtime["model"]),
                api_key=api_key,
                base_url=runtime["base_url"],
                max_output_tokens=max_output_tokens,
                temperature=temperature,
            )
            result = client.generate_with_usage(prompt, system_prompt=STUDY_PACK_SYSTEM_PROMPT)
            raw_text = result.text
            usage = result.usage or {}
            llm_call = True
            cache_status = "disabled" if cache_mode == "off" else "refresh" if cache_mode == "refresh" else "miss"
            written_path = cache.write(
                cache_key,
                {
                    "provider": runtime["provider"],
                    "model": runtime["model"],
                    "base_url": runtime["base_url"],
                    "prompt_version": STUDY_PACK_PROMPT_VERSION,
                    "generation_stage": "study_pack",
                    "request": {
                        "temperature": temperature,
                        "max_output_tokens": max_output_tokens,
                        "review_requested": review_requested,
                        "exam_requested": exam_requested,
                        "question_count": question_count,
                    },
                    "prompt_hash": prompt_hash,
                    "output_text": raw_text,
                    "response_usage": usage,
                },
            )
            if written_path is not None:
                cache_path = written_path
    except Exception as exc:
        warnings.append(f"study_pack_llm_failed:{type(exc).__name__}:{exc}")
        return None, _llm_record(runtime, cache_key, cache_path, output_root, prompt_hash, "error", llm_call, usage), warnings

    parsed = _parse_json_object(raw_text)
    if parsed is None:
        warnings.append("study_pack_invalid_json")
        return None, _llm_record(runtime, cache_key, cache_path, output_root, prompt_hash, cache_status, llm_call, usage), warnings

    normalized = _normalize_study_data(parsed, fallback=fallback, question_count=question_count)
    return normalized, _llm_record(runtime, cache_key, cache_path, output_root, prompt_hash, cache_status, llm_call, usage), warnings


def _study_pack_prompt(
    deck: Deck,
    notes_markdown: str,
    review_requested: bool,
    exam_requested: bool,
    question_count: int,
    note_language: str,
    section_plan: dict[str, Any] | None,
    deck_brief: dict[str, Any] | None,
    content_guard: dict[str, Any] | None,
    coverage_report: dict[str, Any] | None,
    source_map: dict[str, Any] | None,
) -> str:
    payload = {
        "task": "build_exam_review_pack",
        "source_file": Path(deck.source_path).name,
        "source_type": deck.source_type,
        "requested_outputs": {
            "review": review_requested,
            "exam": exam_requested,
            "question_count": question_count,
            "language": note_language,
        },
        "deck_outline": _deck_outline_for_prompt(deck, section_plan, deck_brief),
        "high_value_items": _content_guard_for_prompt(content_guard),
        "figure_table_notes": _figure_table_notes(deck),
        "coverage_summary": _coverage_for_prompt(coverage_report),
        "source_map_summary": _source_map_for_prompt(source_map),
        "notes_markdown": _trim_text(clean_markdown_for_export(notes_markdown), 42000),
    }
    return (
        "请基于 SlideNote 已生成的保真课程笔记，生成考试复习包。"
        "复习包要服务于短期备考，但不能牺牲来源忠实性；只使用输入材料中的信息，不要编造教材外事实。\n"
        "review 的目标：把知识点重组成可扫读的考点清单，讲清「是什么、为什么、怎么用/易错点」，并给出逻辑链。\n"
        "exam 的目标：生成能检验理解的自测题，包含选择题、判断题、简答题和必要的综合题；题目要有答案、解析和易错提醒。\n"
        "重要规则：\n"
        "1. 输出严格 JSON，不要 Markdown 代码围栏。\n"
        "2. importance 只能是 must/key/frequent/background。\n"
        "3. question.type 只能是 choice/true_false/short/essay/comprehensive。\n"
        "4. choice.answer 使用 0 起始选项索引；true_false.answer 使用 true/false；主观题 answer 使用字符串。\n"
        "5. 选择题干扰项必须来自同一概念簇、常见错因、相邻概念或错误推理链；不要使用一眼排除的选项，例如“页面装饰”“完全无关”“只需背名字”。\n"
        "6. 涉及图、表、公式的题目要把 image_refs 指向输入 figure_table_notes 中的 id/path，让图文就地出现在题目旁边，而不是集中放在附录。\n"
        "7. source_refs 使用简洁页码，如 P1、P3-P5；不要输出内部 element id，除非原文必须调试。\n"
        "8. 如果 requested_outputs.review=false，review 可为 null；如果 requested_outputs.exam=false，exam 可为 null。\n"
        "JSON schema:\n"
        "{\n"
        '  "review": {"title": "...", "summary": "...", "logic_chains": [{"title": "...", "steps": ["..."]}], "checklist": [{"section": "...", "importance": "must", "point": "...", "explanation": "...", "why": "...", "pitfall": "...", "source_refs": ["P1"]}], "methods": [{"name": "...", "detail": "...", "example": "..."}]},\n'
        '  "exam": {"title": "...", "subtitle": "...", "questions": [{"id": "q1", "type": "choice", "points": 2, "question": "...", "options": ["..."], "answer": 0, "explanation": "...", "pitfall": "...", "source_refs": ["P1"], "image_refs": [{"id": "s1_img1", "title": "...", "path": "images/fig.png", "source_ref": "P1"}]}]}\n'
        "}\n\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _build_local_report_item(
    section: str,
    point: str,
    explanation: str,
    slide_id: int | None,
    importance: str = "key",
    role: str | None = None,
    image_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    clean_point = _clean_inline(point)
    clean_explanation = _clean_inline(explanation) or clean_point
    return {
        "section": section or "核心知识点",
        "importance": _infer_importance(clean_point + " " + clean_explanation, role),
        "point": clean_point or "知识点",
        "explanation": clean_explanation or "需要结合原始笔记复习。",
        "why": _local_why(_infer_importance(clean_point + " " + clean_explanation, role)),
        "pitfall": _local_pitfall(clean_point, role),
        "source_refs": [f"P{slide_id}"] if slide_id else [],
        "image_refs": image_refs or [],
    }


def _collect_study_items(
    deck: Deck,
    notes_markdown: str,
    content_guard: dict[str, Any] | None,
    limit: int,
) -> list[dict[str, Any]]:
    lookup = _element_lookup(deck)
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for guard_item in _guard_items(content_guard):
        element_id = str(guard_item.get("element_id") or "")
        element = lookup.get(element_id, {})
        preview = str(element.get("text") or guard_item.get("preview") or guard_item.get("reason") or "").strip()
        if not preview:
            continue
        section = str(element.get("section") or f"第 {guard_item.get('slide_id') or element.get('slide_id')} 页")
        item = _build_local_report_item(
            section=section,
            point=_point_from_text(preview),
            explanation=preview,
            slide_id=_as_int(guard_item.get("slide_id") or element.get("slide_id"), 0) or None,
            role=str(guard_item.get("learning_role") or element.get("kind") or ""),
        )
        if _add_unique(items, item, seen):
            continue
        if len(items) >= limit:
            return items

    for page in deck.pages:
        section = page.title or f"第 {page.slide_id} 页"
        for table in page.tables:
            text = table.table_conclusion or table.table_summary or _table_text(table)
            if text:
                item = _build_local_report_item(section, _point_from_text(text), text, page.slide_id, role="table_conclusion")
                _add_unique(items, item, seen)
        for image in page.images:
            if image.ignored:
                continue
            text = image.figure_explanation or image.visual_summary or image.ocr_text or image.caption
            if text:
                item = _build_local_report_item(
                    section,
                    _point_from_text(text),
                    text,
                    page.slide_id,
                    role="figure_explanation",
                    image_refs=[{"id": image.id, "title": image.caption or f"P{page.slide_id} 图示", "path": image.path, "source_ref": f"P{page.slide_id}"}],
                )
                _add_unique(items, item, seen)
        for block in page.text_blocks:
            text = _text_block_text(block)
            if _skip_text(text):
                continue
            item = _build_local_report_item(section, _point_from_text(text), text, page.slide_id, role=block.type)
            _add_unique(items, item, seen)
            if len(items) >= limit:
                return items

    for heading in _headings_from_notes(notes_markdown):
        item = _build_local_report_item("笔记结构", heading, heading, None, role="heading")
        _add_unique(items, item, seen)
        if len(items) >= limit:
            break
    if not items:
        items.append(
            {
                "section": "核心知识点",
                "importance": "key",
                "point": "课程核心内容",
                "explanation": "请回到 notes.md 中按章节复习主要定义、公式、图表和例子。",
                "why": "这是本次材料的主体学习目标。",
                "pitfall": "不要只背标题，要能解释概念之间的关系。",
                "source_refs": [],
            }
        )
    return items[:limit]


def _local_questions(items: list[dict[str, Any]], question_count: int) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = []
    if not items:
        return questions
    for index in range(1, max(1, question_count) + 1):
        item = items[(index - 1) % len(items)]
        point = _clean_inline(item.get("point")) or "知识点"
        explanation = _clean_inline(item.get("explanation")) or point
        source_refs = _string_list(item.get("source_refs"), limit=8)
        image_refs = _normalize_image_refs(item.get("image_refs"))
        if index % 4 == 1:
            options = _local_choice_options(item, items)
            questions.append(
                {
                    "id": f"q{index}",
                    "type": "choice",
                    "points": 2,
                    "question": f"关于「{point}」，哪一项最符合材料中的含义？",
                    "options": options,
                    "answer": 0,
                    "explanation": explanation,
                    "pitfall": _clean_inline(item.get("pitfall")),
                    "source_refs": source_refs,
                    "image_refs": image_refs,
                }
            )
        elif index % 4 == 2:
            questions.append(
                {
                    "id": f"q{index}",
                    "type": "true_false",
                    "points": 1,
                    "question": f"判断：「{point}」只要背下名称即可，不需要理解它解决的问题或使用场景。",
                    "answer": False,
                    "explanation": f"错误。复习时应说明它的含义、作用和易错点：{explanation}",
                    "pitfall": "把概念当成孤立名词，是短期备考最常见的失分方式。",
                    "source_refs": source_refs,
                    "image_refs": image_refs,
                }
            )
        elif index % 4 == 3:
            questions.append(
                {
                    "id": f"q{index}",
                    "type": "short",
                    "points": 6,
                    "question": f"请用自己的话解释「{point}」，并说明它为什么重要。",
                    "answer": explanation,
                    "explanation": explanation,
                    "pitfall": _clean_inline(item.get("pitfall")),
                    "source_refs": source_refs,
                    "image_refs": image_refs,
                }
            )
        else:
            questions.append(
                {
                    "id": f"q{index}",
                    "type": "short",
                    "points": 6,
                    "question": f"围绕「{point}」列出一个容易混淆或容易漏写的点。",
                    "answer": _clean_inline(item.get("pitfall")) or explanation,
                    "explanation": explanation,
                    "pitfall": _clean_inline(item.get("pitfall")),
                    "source_refs": source_refs,
                    "image_refs": image_refs,
                }
            )
    return questions[:question_count]


def _local_choice_options(item: dict[str, Any], items: list[dict[str, Any]]) -> list[str]:
    point = _clean_inline(item.get("point")) or "该知识点"
    explanation = _clean_inline(item.get("explanation")) or point
    distractors: list[str] = []
    for other in items:
        other_point = _clean_inline(other.get("point"))
        other_explanation = _clean_inline(other.get("explanation"))
        if not other_point or other_point == point:
            continue
        distractors.append(f"把「{other_point}」的作用误当成「{point}」的主要含义。")
        if other_explanation:
            distractors.append(f"只记住相邻结论“{_trim_text(other_explanation, 52)}”，但忽略它和「{point}」的适用条件。")
        if len(distractors) >= 3:
            break
    fallback = [
        f"只背「{point}」这个名称，但不能说明它解决的问题和限制。",
        f"把「{point}」理解成任何场景都成立的结论，忽略材料给出的条件。",
        f"只记住最终结论，却不能解释「{point}」与前后概念的关系。",
    ]
    for option in fallback:
        if len(distractors) >= 3:
            break
        distractors.append(option)
    return [explanation, *distractors[:3]]


def _local_logic_chains(deck: Deck, section_plan: dict[str, Any] | None, deck_brief: dict[str, Any] | None) -> list[dict[str, Any]]:
    brief = deck_brief.get("brief") if isinstance(deck_brief, dict) and isinstance(deck_brief.get("brief"), dict) else {}
    chains = _dict_list(brief.get("concept_dependencies"), limit=8)
    if chains:
        return [
            {
                "title": f"{_clean_inline(item.get('source'))} -> {_clean_inline(item.get('target'))}",
                "steps": [_clean_inline(item.get("reason")) or "前一个概念为后一个概念提供理解基础。"],
            }
            for item in chains
        ]
    sections = _dict_list((section_plan or {}).get("sections"), limit=8)
    if sections:
        return [
            {
                "title": _clean_inline(section.get("title")) or f"第 {index} 节",
                "steps": [f"复习页码范围：P{section.get('start_slide_id') or '?'} 起。", _clean_inline(section.get("reason")) or "按章节顺序复习。"],
            }
            for index, section in enumerate(sections, start=1)
        ]
    titles = [_clean_inline(page.title) for page in deck.pages if _clean_inline(page.title)]
    return [{"title": "按材料顺序复习", "steps": [f"先掌握：{title}" for title in titles[:6]] or ["先读 notes.md，再用自测题检查理解。"]}]


def _local_methods(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    methods = []
    for item in items:
        text = f"{item.get('point')} {item.get('explanation')}"
        if any(token in text for token in ("公式", "计算", "步骤", "算法", "推导", "example", "例")):
            methods.append(
                {
                    "name": _clean_inline(item.get("point")) or "解题方法",
                    "detail": _clean_inline(item.get("explanation")),
                    "example": "复习时尝试重新写出步骤，并解释每一步为什么成立。",
                }
            )
        if len(methods) >= 8:
            break
    return methods


def _local_summary(deck: Deck, deck_brief: dict[str, Any] | None) -> str:
    brief = deck_brief.get("brief") if isinstance(deck_brief, dict) and isinstance(deck_brief.get("brief"), dict) else {}
    summary = _clean_inline(brief.get("one_sentence_summary"))
    if summary:
        return summary
    return f"本复习清单基于 {len(deck.pages)} 页课程材料生成，优先覆盖定义、公式、表格结论、图示解释和容易混淆的概念。"


def _normalize_study_data(raw: dict[str, Any], fallback: dict[str, Any], question_count: int) -> dict[str, Any]:
    review = raw.get("review") if isinstance(raw.get("review"), dict) else fallback.get("review")
    exam = raw.get("exam") if isinstance(raw.get("exam"), dict) else fallback.get("exam")
    normalized_review = _normalize_review(review, fallback.get("review") or {})
    normalized_exam = _normalize_exam(exam, fallback.get("exam") or {}, question_count)
    return {"review": normalized_review, "exam": normalized_exam}


def _normalize_review(raw: dict[str, Any] | None, fallback: dict[str, Any]) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    checklist = []
    for item in _dict_list(data.get("checklist"), limit=400):
        checklist.append(
            {
                "section": _clean_inline(item.get("section")) or "核心知识点",
                "importance": _normalize_importance(item.get("importance")),
                "point": _clean_inline(item.get("point")) or "知识点",
                "explanation": _clean_inline(item.get("explanation")) or "需要结合原始笔记复习。",
                "why": _clean_inline(item.get("why")),
                "pitfall": _clean_inline(item.get("pitfall")),
                "source_refs": _string_list(item.get("source_refs"), limit=12),
            }
        )
    if not checklist:
        checklist = list(fallback.get("checklist") or [])
    return {
        "title": _clean_inline(data.get("title")) or fallback.get("title") or "课程复习",
        "summary": _clean_inline(data.get("summary")) or fallback.get("summary") or "",
        "logic_chains": _dict_list(data.get("logic_chains"), limit=40) or fallback.get("logic_chains") or [],
        "checklist": checklist,
        "methods": _dict_list(data.get("methods"), limit=80) or fallback.get("methods") or [],
    }


def _normalize_exam(raw: dict[str, Any] | None, fallback: dict[str, Any], question_count: int) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    questions = []
    for index, question in enumerate(_dict_list(data.get("questions"), limit=question_count), start=1):
        qtype = _normalize_question_type(question.get("type"))
        normalized = {
            "id": _clean_inline(question.get("id")) or f"q{index}",
            "type": qtype,
            "points": _as_int(question.get("points"), _default_points(qtype)),
            "question": _clean_inline(question.get("question")) or "请解释本节核心知识点。",
            "answer": _normalize_answer(question.get("answer"), qtype),
            "explanation": _clean_inline(question.get("explanation")),
            "pitfall": _clean_inline(question.get("pitfall")),
            "source_refs": _string_list(question.get("source_refs"), limit=12),
            "image_refs": _normalize_image_refs(question.get("image_refs")),
        }
        if qtype == "choice":
            options = _string_list(question.get("options"), limit=8)
            if len(options) < 2:
                normalized["type"] = "short"
                normalized["answer"] = _clean_inline(question.get("answer")) or normalized["explanation"]
            else:
                normalized["options"] = options
                answer_index = _as_int(normalized["answer"], 0)
                normalized["answer"] = min(max(answer_index, 0), len(options) - 1)
        questions.append(normalized)
    if not questions:
        questions = list((fallback.get("exam") if "exam" in fallback else fallback).get("questions") or [])
    return {
        "title": _clean_inline(data.get("title")) or fallback.get("title") or "课程自测",
        "subtitle": _clean_inline(data.get("subtitle")) or fallback.get("subtitle") or "",
        "questions": questions[:question_count],
    }


def _deck_outline_for_prompt(deck: Deck, section_plan: dict[str, Any] | None, deck_brief: dict[str, Any] | None) -> dict[str, Any]:
    brief = deck_brief.get("brief") if isinstance(deck_brief, dict) and isinstance(deck_brief.get("brief"), dict) else {}
    return {
        "title": _deck_title(deck, deck_brief),
        "pages_total": len(deck.pages),
        "sections": _dict_list((section_plan or {}).get("sections"), limit=80),
        "core_questions": _string_list(brief.get("core_questions"), limit=12),
        "key_concepts": _dict_list(brief.get("key_concepts"), limit=80),
        "page_titles": [{"slide_id": page.slide_id, "title": page.title} for page in deck.pages[:300]],
    }


def _content_guard_for_prompt(content_guard: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not content_guard:
        return []
    result = []
    for item in _dict_list(content_guard.get("items"), limit=300):
        confidence = _as_float(item.get("confidence"), 0.0)
        if item.get("must_explain") or confidence >= REQUIRED_CONFIDENCE_THRESHOLD:
            result.append(
                {
                    "slide_id": item.get("slide_id"),
                    "learning_role": item.get("learning_role"),
                    "confidence": confidence,
                    "reason": item.get("reason"),
                }
            )
    return result


def _coverage_for_prompt(coverage_report: dict[str, Any] | None) -> dict[str, Any]:
    if not coverage_report:
        return {}
    return {
        "total": coverage_report.get("total"),
        "covered": coverage_report.get("covered"),
        "missing": coverage_report.get("missing"),
        "coverage_ratio": coverage_report.get("coverage_ratio"),
        "required_visible_coverage": coverage_report.get("required_visible_coverage"),
    }


def _source_map_for_prompt(source_map: dict[str, Any] | None) -> dict[str, Any]:
    if not source_map:
        return {}
    return {
        "note_blocks": len(source_map.get("note_blocks") or []),
        "default_display_mode": source_map.get("default_display_mode"),
    }


def _llm_record(
    runtime: dict[str, Any],
    cache_key: str,
    cache_path: Path,
    output_root: Path,
    prompt_hash: str,
    cache_status: str,
    llm_call: bool,
    usage: dict[str, Any],
) -> dict[str, Any]:
    return {
        "provider": runtime["provider"],
        "model": runtime["model"],
        "base_url": runtime["base_url"],
        "cache_key": cache_key,
        "cache_file": _display_path(cache_path, output_root),
        "prompt_hash": prompt_hash,
        "cache_status": cache_status,
        "llm_call": llm_call,
        "input_tokens": usage.get("input_tokens") if llm_call else 0,
        "output_tokens": usage.get("output_tokens") if llm_call else 0,
        "total_tokens": usage.get("total_tokens") if llm_call else 0,
        "provider_cached_input_tokens": usage.get("provider_cached_input_tokens") if llm_call else 0,
        "provider_usage": usage if llm_call else {},
    }


def _element_lookup(deck: Deck) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for page in deck.pages:
        section = page.title or f"第 {page.slide_id} 页"
        for block in page.text_blocks:
            lookup[block.id] = {"slide_id": page.slide_id, "section": section, "kind": block.type, "text": _text_block_text(block)}
        for table in page.tables:
            lookup[table.id] = {"slide_id": page.slide_id, "section": section, "kind": "table", "text": table.table_conclusion or table.table_summary or _table_text(table)}
        for image in page.images:
            lookup[image.id] = {"slide_id": page.slide_id, "section": section, "kind": image.role or "image", "text": image.figure_explanation or image.visual_summary or image.ocr_text or image.caption or ""}
    return lookup


def _guard_items(content_guard: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not content_guard:
        return []
    items = _dict_list(content_guard.get("items"), limit=1000)
    result = []
    for item in items:
        if item.get("must_explain") or _as_float(item.get("confidence"), 0.0) >= REQUIRED_CONFIDENCE_THRESHOLD:
            result.append(item)
    return result


def _text_block_text(block: TextBlock) -> str:
    return " ".join((block.content or "").split())


def _table_text(table: TableBlock) -> str:
    rows = []
    for row in table.rows[:6]:
        rows.append(" / ".join(cell.strip() for cell in row[:6] if cell.strip()))
    return "；".join(row for row in rows if row)


def _skip_text(text: str) -> bool:
    clean = _clean_inline(text)
    if len(clean) < 3:
        return True
    if re.fullmatch(r"[\d\s/._:-]+", clean):
        return True
    if len(clean) <= 12 and any(token in clean.lower() for token in ("email", "@", "http", "www")):
        return True
    return False


def _point_from_text(text: str) -> str:
    clean = _clean_inline(text)
    if not clean:
        return "知识点"
    for sep in ("：", ":", "。", ".", "；", ";", "\n"):
        if sep in clean:
            head = clean.split(sep, 1)[0].strip()
            if 2 <= len(head) <= 36:
                return head
    return clean[:36].rstrip()


def _infer_importance(text: str, role: str | None = None) -> str:
    role_text = (role or "").lower()
    text_lower = text.lower()
    if role_text in {"definition", "formula", "condition", "concept"} or any(token in text for token in ("定义", "定理", "公式", "性质", "结论", "必须", "核心")):
        return "must"
    if role_text in {"table_conclusion", "figure_explanation", "example", "code_example"} or any(token in text for token in ("例", "步骤", "算法", "方法", "流程", "图", "表")):
        return "key"
    if any(token in text_lower for token in ("高频", "常见", "易错", "frequent", "common")):
        return "frequent"
    return "key"


def _local_why(importance: str) -> str:
    return {
        "must": "这类内容通常支撑定义、推导、公式或综合题。",
        "key": "这类内容常用于解释过程、比较概念或连接例子。",
        "frequent": "这类内容适合通过判断题和选择题检查是否混淆。",
        "background": "这类内容帮助理解上下文，复习时保持基本印象即可。",
    }.get(importance, "这类内容是理解本节材料的组成部分。")


def _local_pitfall(point: str, role: str | None = None) -> str:
    if role and "formula" in role:
        return "不要只背公式，要能说明符号含义、适用条件和计算对象。"
    if any(token in point for token in ("图", "流程", "结构")):
        return "不要只看图名，要能沿箭头或结构关系讲出因果链。"
    return "不要只背关键词，要能说明它解决的问题和使用场景。"


def _add_unique(items: list[dict[str, Any]], item: dict[str, Any], seen: set[str]) -> bool:
    key = re.sub(r"\s+", "", str(item.get("point") or item.get("explanation") or "")).lower()[:80]
    if not key or key in seen:
        return False
    seen.add(key)
    items.append(item)
    return True


def _headings_from_notes(markdown: str) -> list[str]:
    headings = []
    for line in markdown.splitlines():
        match = re.match(r"^#{2,4}\s+(.+?)\s*$", line.strip())
        if match:
            heading = re.sub(r"<[^>]+>", "", match.group(1)).strip()
            if heading:
                headings.append(heading)
    return headings[:20]


def _deck_title(deck: Deck, deck_brief: dict[str, Any] | None) -> str:
    brief = deck_brief.get("brief") if isinstance(deck_brief, dict) and isinstance(deck_brief.get("brief"), dict) else {}
    title = _clean_inline(brief.get("course_title"))
    if title:
        return title
    for page in deck.pages:
        title = _clean_inline(page.title)
        if title:
            return title
    return Path(deck.source_path).stem or "课程材料"


def _source_title(report: dict[str, Any]) -> str:
    return Path(str(report.get("source_path") or "课程材料")).stem or "课程材料"


def _normalize_importance(value: Any) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "必考": "must",
        "must": "must",
        "重点": "key",
        "key": "key",
        "高频": "frequent",
        "freq": "frequent",
        "frequent": "frequent",
        "了解": "background",
        "background": "background",
        "info": "background",
    }
    return aliases.get(text, "key")


def _normalize_question_type(value: Any) -> str:
    text = str(value or "").strip().lower()
    aliases = {
        "choice": "choice",
        "multiple_choice": "choice",
        "选择题": "choice",
        "tf": "true_false",
        "true_false": "true_false",
        "判断题": "true_false",
        "short": "short",
        "short_answer": "short",
        "简答题": "short",
        "essay": "essay",
        "论述题": "essay",
        "comprehensive": "comprehensive",
        "综合题": "comprehensive",
    }
    return aliases.get(text, "short")


def _normalize_image_refs(value: Any) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for item in _dict_list(value, limit=8):
        path = _clean_inline(item.get("path"))
        if not path:
            continue
        refs.append(
            {
                "id": _clean_inline(item.get("id")),
                "title": _clean_inline(item.get("title")) or "题目图",
                "path": path,
                "source_ref": _clean_inline(item.get("source_ref")),
            }
        )
    return refs


def _section_for_source_ref(source_refs: list[str], sections: dict[str, dict[str, Any]]) -> str | None:
    if not source_refs:
        return None
    for section, bucket in sections.items():
        for item in bucket.get("checklist") or []:
            item_refs = set(_string_list(item.get("source_refs"), limit=8))
            if item_refs.intersection(source_refs):
                return section
    return None


def _choice_distractor_score(choice_questions: list[dict[str, Any]]) -> float:
    if not choice_questions:
        return 1.0
    scores = []
    bad_tokens = ["装饰", "无关", "不需要", "只需要记住名称", "只需背", "decoration", "unrelated"]
    for question in choice_questions:
        options = _string_list(question.get("options"), limit=8)
        if len(options) < 4:
            scores.append(0.25)
            continue
        unique_ratio = len({option.lower() for option in options}) / max(1, len(options))
        length_score = min(1.0, sum(1 for option in options if len(option) >= 10) / len(options))
        bad_penalty = min(0.6, sum(1 for option in options for token in bad_tokens if token.lower() in option.lower()) * 0.2)
        scores.append(max(0.0, 0.45 * unique_ratio + 0.55 * length_score - bad_penalty))
    return sum(scores) / len(scores)


def _is_applied_question(question_text: str) -> bool:
    lowered = question_text.lower()
    tokens = ["为什么", "如何", "比较", "解释", "推导", "场景", "条件", "作用", "易错", "why", "how", "compare", "explain", "apply"]
    return any(token in lowered for token in tokens)


def _is_figure_question(text: str) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in ["图", "表", "公式", "流程", "截图", "figure", "table", "formula", "diagram"])


def _question_quality_flags(
    *,
    choice_score: float,
    explanation_score: float,
    source_ref_score: float,
    question_mix_score: float,
    figure_question_score: float,
    mechanical_definition_score: float,
    has_figures: bool,
) -> list[str]:
    flags: list[str] = []
    if choice_score < 0.55:
        flags.append("choice_distractors_need_same_concept_cluster")
    if explanation_score < 0.65:
        flags.append("questions_need_explanations_and_pitfalls")
    if source_ref_score < 0.75:
        flags.append("questions_need_source_refs")
    if question_mix_score < 0.5:
        flags.append("question_types_too_narrow")
    if has_figures and figure_question_score < 0.35:
        flags.append("figure_table_questions_need_inline_refs")
    if mechanical_definition_score > 0.65:
        flags.append("questions_too_definition_like")
    return flags


def _round_score(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 3)


def _normalize_answer(value: Any, qtype: str) -> Any:
    if qtype == "choice":
        return _as_int(value, 0)
    if qtype == "true_false":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        return text in {"true", "1", "yes", "y", "正确", "对"}
    return _clean_inline(value)


def _answer_text(question: dict[str, Any], qtype: str) -> str:
    if qtype == "choice":
        options = _string_list(question.get("options"), limit=8)
        answer = min(max(_as_int(question.get("answer"), 0), 0), max(len(options) - 1, 0))
        return f"{chr(65 + answer)}. {options[answer] if options else ''}".strip()
    if qtype == "true_false":
        return "正确" if bool(question.get("answer")) else "错误"
    return _clean_inline(question.get("answer")) or "见解析"


def _default_points(qtype: str) -> int:
    return {"choice": 2, "true_false": 1, "short": 6, "essay": 10, "comprehensive": 15}.get(qtype, 5)


def _parse_json_object(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def _deck_digest(deck: Deck) -> str:
    payload = [
        {
            "slide_id": page.slide_id,
            "title": page.title,
            "texts": [_text_block_text(block) for block in page.text_blocks[:20]],
            "tables": [_table_text(table) for table in page.tables[:5]],
            "images": [image.visual_summary or image.ocr_text or image.caption for image in page.images[:8]],
        }
        for page in deck.pages
    ]
    return sha256_text(stable_json(payload))


def _trim_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = text[: int(limit * 0.7)]
    tail = text[-int(limit * 0.3) :]
    return head.rstrip() + "\n\n...[notes truncated for prompt budget]...\n\n" + tail.lstrip()


def _clean_inline(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    return " ".join(text.split()).strip()


def _string_list(value: Any, limit: int = 100) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value[:limit]:
        text = _clean_inline(item)
        if text:
            result.append(text)
    return result


def _dict_list(value: Any, limit: int = 100) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value[:limit] if isinstance(item, dict)]


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _display_path(path: Path, output_root: Path) -> str:
    try:
        return path.resolve().relative_to(output_root.resolve()).as_posix()
    except ValueError:
        return str(path)
