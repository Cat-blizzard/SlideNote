from pathlib import Path
from typing import Any

from slidenote.utils import as_int
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

_as_int = as_int

# Only strip real HTML markup (a tag name followed by name=value attributes), so
# inline math such as "a < b and c > d" or "a<b and c>d" survives.
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", flags=re.DOTALL)
_HTML_TAG_RE = re.compile(
    r"</?[A-Za-z][A-Za-z0-9-]*"
    r"(?:\s+[A-Za-z_:][\w:.-]*\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s\"'<>]+))*"
    r"\s*/?>"
)


def _strip_html(value: str) -> str:
    return _HTML_TAG_RE.sub("", _HTML_COMMENT_RE.sub("", value))


def _clean_inline(value: Any) -> str:
    text = _strip_html(str(value or ""))
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
