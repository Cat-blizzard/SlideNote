from typing import Any
import re
from .common import (
    IMPORTANCE_LABELS,
    _clean_inline,
    _dict_list,
    _source_title,
    _string_list,
)

def _headings_from_notes(markdown: str) -> list[str]:
    headings = []
    for line in markdown.splitlines():
        match = re.match(r"^#{2,4}\s+(.+?)\s*$", line.strip())
        if match:
            heading = re.sub(r"<[^>]+>", "", match.group(1)).strip()
            if heading:
                headings.append(heading)
    return headings[:20]

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
