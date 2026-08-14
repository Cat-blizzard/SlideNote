from pathlib import Path
from typing import Any
import re

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

def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

def _clean_inline(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    return " ".join(text.split()).strip()

def _dict_list(value: Any, limit: int = 100) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value[:limit] if isinstance(item, dict)]

def _source_title(report: dict[str, Any]) -> str:
    return Path(str(report.get("source_path") or "课程材料")).stem or "课程材料"

def _string_list(value: Any, limit: int = 100) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value[:limit]:
        text = _clean_inline(item)
        if text:
            result.append(text)
    return result
