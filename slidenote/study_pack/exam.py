from typing import Any
import html
import json
from .common import (
    QUESTION_TYPE_LABELS,
    _as_int,
    _clean_inline,
    _dict_list,
    _source_title,
    _string_list,
)
from .questions import (
    _answer_text,
    _default_points,
    _is_figure_question,
    _normalize_question_type,
)

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

def _build_wrong_answer_review(exam: dict[str, Any], question_quality: dict[str, Any]) -> dict[str, Any]:
    questions = _dict_list(exam.get("questions"), limit=300)
    return {
        "title": f"{_clean_inline(exam.get('title')) or '课程'} - 错题复盘",
        "question_quality": question_quality,
        "prompt_template": _wrong_answer_prompt_template(questions),
    }

def _section_for_source_ref(source_refs: list[str], sections: dict[str, dict[str, Any]]) -> str | None:
    if not source_refs:
        return None
    for section, bucket in sections.items():
        for item in bucket.get("checklist") or []:
            item_refs = set(_string_list(item.get("source_refs"), limit=8))
            if item_refs.intersection(source_refs):
                return section
    return None

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

def render_wrong_answer_review_prompt(report: dict[str, Any]) -> str:
    wrong_review = report.get("wrong_answer_review") if isinstance(report.get("wrong_answer_review"), dict) else {}
    title = _clean_inline(wrong_review.get("title")) or "错题复盘 Prompt"
    template = wrong_review.get("prompt_template") or _wrong_answer_prompt_template([])
    lines = [f"# {title}", ""]
    lines.append("把 `exam.html` 批改后显示的错题 JSON 粘贴到下面占位处，再交给你选择的学习助手。")
    lines.extend(["", "```text", template, "```", ""])
    return "\n".join(lines)
