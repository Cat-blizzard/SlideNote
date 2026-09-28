from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from .contexts import NoteContext

ContextResult = tuple[str, dict[str, Any]]
ContextFallback = Callable[[NoteContext, Exception], ContextResult]


def _run_note_contexts(
    contexts: list[NoteContext],
    process: Callable[[NoteContext], ContextResult],
    *,
    workers: int,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    fallback: ContextFallback | None = None,
) -> dict[str, ContextResult]:
    """Run ``process`` for each context serially or in a thread pool.

    A failing context is replaced by ``fallback`` so one bad model call does
    not discard the rest of the deck. When every context fails (e.g. a
    configuration error such as a missing API key) the first error is raised.
    """
    results: dict[str, ContextResult] = {}
    failures: list[tuple[NoteContext, Exception]] = []

    def record(context: NoteContext, result: ContextResult) -> None:
        results[context.id] = result
        if progress_callback:
            progress_callback(result[1])

    if max(1, workers) == 1:
        for context in contexts:
            try:
                result = process(context)
            except Exception as exc:
                failures.append((context, exc))
                continue
            record(context, result)
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(process, context): context for context in contexts}
            for future in as_completed(futures):
                context = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    failures.append((context, exc))
                    continue
                record(context, result)

    if failures:
        if fallback is None or len(failures) == len(contexts):
            raise failures[0][1]
        for context, exc in failures:
            record(context, fallback(context, exc))
    return results


def _failed_context_record(context: NoteContext, exc: Exception, *, generation_stage: str, fallback: str) -> dict[str, Any]:
    # Provider error text can contain credentials, so only the type is kept.
    return {
        "context_id": context.id,
        "context_kind": context.kind,
        "context_title": context.title,
        "slide_id": context.pages[0].slide_id if context.pages else None,
        "slide_ids": [page.slide_id for page in context.pages],
        "generation_stage": generation_stage,
        "cache_status": "failed",
        "llm_call": False,
        "error_type": type(exc).__name__,
        "fallback": fallback,
    }


def _context_failure_warnings(records: list[dict[str, Any]]) -> list[str]:
    return [
        f"note_context_failed:{record.get('generation_stage')}:{record.get('context_id')}:"
        f"{record.get('error_type')} (used {record.get('fallback')} fallback)"
        for record in records
        if record.get("cache_status") == "failed"
    ]
