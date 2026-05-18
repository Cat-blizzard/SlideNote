from __future__ import annotations

import re
from typing import Any

from slidenote.llm_cache import utc_now_iso
from slidenote.models import Deck, TableBlock


def enrich_deck_with_table_understanding(deck: Deck) -> dict[str, Any]:
    pages: list[dict[str, Any]] = []
    tables_total = 0
    tables_with_summary = 0
    tables_with_conclusion = 0
    key_rows_total = 0

    for page in deck.pages:
        table_records: list[dict[str, Any]] = []
        for table in page.tables:
            tables_total += 1
            analysis = analyze_table(table)
            table.table_summary = analysis["table_summary"]
            table.table_conclusion = analysis["table_conclusion"]
            table.key_rows = analysis["key_rows"]
            if table.table_summary:
                tables_with_summary += 1
            if table.table_conclusion:
                tables_with_conclusion += 1
            key_rows_total += len(table.key_rows)
            table_records.append(
                {
                    "id": table.id,
                    "row_count": analysis["row_count"],
                    "column_count": analysis["column_count"],
                    "has_header": analysis["has_header"],
                    "headers": analysis["headers"],
                    "table_summary": table.table_summary,
                    "table_conclusion": table.table_conclusion,
                    "key_rows": table.key_rows,
                    "method": analysis["method"],
                    "warnings": analysis["warnings"],
                }
            )
        pages.append({"slide_id": page.slide_id, "title": page.title, "tables": table_records})

    return {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "source_path": deck.source_path,
        "source_type": deck.source_type,
        "method": "local_rules_v1",
        "llm_enhancement": "not_enabled_v1",
        "summary": {
            "pages_total": len(deck.pages),
            "tables_total": tables_total,
            "tables_with_summary": tables_with_summary,
            "tables_with_conclusion": tables_with_conclusion,
            "key_rows_total": key_rows_total,
        },
        "pages": pages,
    }


def analyze_table(table: TableBlock) -> dict[str, Any]:
    cleaned = _clean_rows(table.rows)
    if not cleaned:
        return {
            "row_count": 0,
            "column_count": 0,
            "has_header": False,
            "headers": [],
            "table_summary": None,
            "table_conclusion": None,
            "key_rows": [],
            "method": "local_rules_v1",
            "warnings": ["empty_table"],
        }

    width = max(len(row) for _, row in cleaned)
    normalized = [(index, row + [""] * (width - len(row))) for index, row in cleaned]
    has_header = _has_header(normalized)
    header_row = normalized[0][1] if has_header else []
    headers = _header_names(header_row, width) if has_header else [f"列 {index}" for index in range(1, width + 1)]
    data_rows = normalized[1:] if has_header else normalized
    key_rows = _key_rows(data_rows, headers)
    table_summary = _table_summary(headers, data_rows, width, has_header)
    table_conclusion = _table_conclusion(key_rows, headers, has_header)
    return {
        "row_count": len(cleaned),
        "column_count": width,
        "has_header": has_header,
        "headers": headers,
        "table_summary": table_summary,
        "table_conclusion": table_conclusion,
        "key_rows": key_rows,
        "method": "local_rules_v1",
        "warnings": [],
    }


def table_text_for_prompt(table: TableBlock, raw_rows: int = 3) -> str:
    parts = [table.table_conclusion, table.table_summary]
    if table.key_rows:
        key_row_text = "；".join(_key_row_sentence(row) for row in table.key_rows[:3])
        if key_row_text:
            parts.append(f"关键行：{key_row_text}")
    raw_preview = _raw_table_preview(table, raw_rows=raw_rows)
    if raw_preview:
        parts.append(f"原始表格片段：{raw_preview}")
    return " / ".join(part for part in parts if part)


def table_preview(table: TableBlock, limit: int = 160, raw_rows: int = 2) -> str:
    return _preview(table_text_for_prompt(table, raw_rows=raw_rows), limit=limit)


def _clean_rows(rows: list[list[str]]) -> list[tuple[int, list[str]]]:
    cleaned: list[tuple[int, list[str]]] = []
    for index, row in enumerate(rows, start=1):
        cells = [_clean_cell(cell) for cell in row]
        while cells and not cells[-1]:
            cells.pop()
        if any(cells):
            cleaned.append((index, cells))
    return cleaned


def _clean_cell(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _has_header(rows: list[tuple[int, list[str]]]) -> bool:
    if len(rows) < 2:
        return False
    first = rows[0][1]
    non_empty = [cell for cell in first if cell]
    if len(non_empty) < 2:
        return False
    numeric_cells = sum(1 for cell in non_empty if _looks_numeric(cell))
    return numeric_cells < len(non_empty)


def _looks_numeric(value: str) -> bool:
    text = value.strip().replace(",", "")
    return bool(re.fullmatch(r"[-+]?\d+(?:\.\d+)?%?", text))


def _header_names(header: list[str], width: int) -> list[str]:
    result: list[str] = []
    seen: dict[str, int] = {}
    for index in range(width):
        name = _clean_cell(header[index] if index < len(header) else "")
        if not name:
            name = f"列 {index + 1}"
        count = seen.get(name, 0) + 1
        seen[name] = count
        result.append(name if count == 1 else f"{name} {count}")
    return result


def _key_rows(data_rows: list[tuple[int, list[str]]], headers: list[str]) -> list[dict[str, Any]]:
    if not data_rows:
        return []
    ranked = sorted(data_rows, key=lambda item: (-_row_score(item[1]), item[0]))
    selected_indexes = {index for index, _ in ranked[: min(3, len(ranked))]}
    selected = [item for item in data_rows if item[0] in selected_indexes]
    return [_key_row_record(original_index, row, headers) for original_index, row in selected]


def _row_score(row: list[str]) -> float:
    cells = [cell for cell in row if cell]
    text = " ".join(cells)
    score = len(cells) * 2 + min(len(text) / 30.0, 4.0)
    if re.search(r"合计|总计|小计|平均|均值|结论|关键|重点|total|summary|average|mean", text, re.IGNORECASE):
        score += 3
    if re.search(r"\d+(?:\.\d+)?\s*(?:%|ms|s|MB|GB|KB|次|倍|年|月|天)?", text):
        score += 1.5
    return score


def _key_row_record(original_index: int, row: list[str], headers: list[str]) -> dict[str, Any]:
    values: list[dict[str, str]] = []
    for index, cell in enumerate(row):
        if not cell:
            continue
        column = headers[index] if index < len(headers) else f"列 {index + 1}"
        values.append({"column": column, "value": cell})
    label = _row_label(row, original_index)
    return {
        "row_index": original_index,
        "label": label,
        "values": values,
        "reason": _key_row_reason(row),
    }


def _row_label(row: list[str], original_index: int) -> str:
    for cell in row:
        if cell:
            return cell
    return f"第 {original_index} 行"


def _key_row_reason(row: list[str]) -> str:
    text = " ".join(cell for cell in row if cell)
    if re.search(r"合计|总计|小计|平均|均值|结论|关键|重点|total|summary|average|mean", text, re.IGNORECASE):
        return "summary_or_signal_row"
    if re.search(r"\d", text):
        return "numeric_data_row"
    return "representative_data_row"


def _table_summary(headers: list[str], data_rows: list[tuple[int, list[str]]], width: int, has_header: bool) -> str:
    data_count = len(data_rows)
    if has_header and headers:
        dimensions = _join_terms(headers[:4])
        suffix = f"等 {len(headers)} 个维度" if len(headers) > 4 else "这些维度"
        count_text = f"{data_count} 条记录" if data_count else "表头维度"
        return f"表格围绕「{dimensions}」{suffix}组织，主要用于对比或归纳 {count_text}。"
    row_count = data_count
    return f"表格包含 {row_count} 行、{width} 列信息，主要用于并列展示相关条目。"


def _table_conclusion(key_rows: list[dict[str, Any]], headers: list[str], has_header: bool) -> str | None:
    if not key_rows:
        return None
    if has_header and len(headers) >= 2:
        row_summaries = [_key_row_sentence(row) for row in key_rows[:3]]
        dimensions = _join_terms(headers[1:4])
        return f"表格重点比较 {_join_terms([str(row.get('label') or '') for row in key_rows[:3]])}，差异主要落在「{dimensions}」等维度；{_join_terms(row_summaries, sep='；')}。"
    labels = [str(row.get("label") or "") for row in key_rows[:3]]
    return f"表格中的 {_join_terms(labels)} 是需要优先关注的代表性条目。"


def _key_row_sentence(row: dict[str, Any]) -> str:
    label = str(row.get("label") or f"第 {row.get('row_index', '?')} 行")
    values = row.get("values")
    if not isinstance(values, list):
        return label
    details: list[str] = []
    for value in values[:4]:
        if not isinstance(value, dict):
            continue
        column = str(value.get("column") or "").strip()
        cell = str(value.get("value") or "").strip()
        if cell and cell != label:
            details.append(f"{column}={cell}" if column else cell)
    if details:
        return f"{label}（{'，'.join(details)}）"
    return label


def _raw_table_preview(table: TableBlock, raw_rows: int = 3) -> str:
    rows = [" | ".join(_clean_cell(cell) for cell in row if _clean_cell(cell)) for row in table.rows[:raw_rows]]
    return " / ".join(row for row in rows if row)


def _join_terms(values: list[str], sep: str = "、") -> str:
    cleaned = [value.strip() for value in values if value and value.strip()]
    if not cleaned:
        return ""
    return sep.join(cleaned)


def _preview(text: str, limit: int = 160) -> str:
    value = re.sub(r"\s+", " ", text).strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"
