"""Shared test factories (auto-importable from every test module via
``from conftest import ...``).

These replace the copy-pasted fake LLM clients (~31 classes across test
files) and fitz PDF builders (~15 sites) with parameterized factories, so
LLMClient interface changes need one edit here instead of dozens.
"""

import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def make_fake_client(
    *,
    text: str | Callable[[str], str] | None = None,
    usage: dict[str, Any] | Callable[[str], dict[str, Any]] | None = None,
    on_call: Callable[[str], None] | None = None,
    supports_image_input: bool = False,
    fail: Exception | type[Exception] | None = None,
    raise_on_init: Exception | type[Exception] | None = None,
) -> type:
    """Build a fake LLMClient class for ``monkeypatch.setattr(...)``.

    - ``text``: static response text, or a callable(prompt) -> str.
      Defaults to a minimal markdown with the prompt's source ids.
    - ``usage``: static usage dict, or a callable(prompt) -> dict.
      Defaults to ``{}``.
    - ``on_call``: invoked with the prompt before returning (record calls).
    - ``supports_image_input``: mirrors LLMClient.supports_image_input.
    - ``fail``: raised by ``generate_with_usage`` instead of returning.
    - ``raise_on_init``: raised from ``__init__`` (simulates a client that
      must never be constructed, e.g. cache-hit assertions).
    """

    def default_text(prompt: str) -> str:
        import re

        ids = re.findall(r"\bs\d+_(?:t|tbl|img|fig)\d+\b", prompt)
        marker = f"<!-- slidenote-source: p1:{','.join(sorted(ids))} -->" if ids else ""
        return f"## Notes\n\n{marker}"

    def _raise_init(self, kwargs):
        if raise_on_init is not None:
            if isinstance(raise_on_init, type):
                raise raise_on_init()
            raise raise_on_init

    def generate_with_usage(self, prompt, system_prompt=None):
        if fail is not None:
            if isinstance(fail, type):
                raise fail()
            raise fail
        if on_call is not None:
            on_call(prompt)
        resolved_text = text(prompt) if callable(text) else (text if text is not None else default_text(prompt))
        resolved_usage = usage(prompt) if callable(usage) else (usage or {})

        class Result:
            pass

        result = Result()
        result.text = resolved_text
        result.usage = resolved_usage
        return result

    # Built with type() because a class body is not a closure: a class-level
    # attribute that referenced the factory parameters would raise NameError.
    return type(
        "FakeClient",
        (),
        {
            "supports_image_input": supports_image_input,
            "__init__": lambda self, **kwargs: _raise_init(self, kwargs),
            "generate_with_usage": generate_with_usage,
        },
    )


def write_pdf(path: Path, pages: list[list[str]]) -> None:
    """Write a fixture PDF; ``pages`` is one list of lines per page.

    Uses the default letter page so long lines are extracted intact (fitz
    truncates over-wide text on small pages).
    """
    import fitz

    doc = fitz.open()
    for lines in pages:
        page = doc.new_page()
        for index, line in enumerate(lines):
            page.insert_text((72, 72 + index * 32), line)
    doc.save(path)
    doc.close()
