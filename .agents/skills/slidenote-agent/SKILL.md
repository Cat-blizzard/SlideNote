---
name: slidenote-agent
description: Use when asked to generate, repair, or evaluate SlideNote agent-backend study notes from PPT/PDF course slides in the SlideNote repo — runs the agent-pack -> agent-run/agent-build (dsh backend) -> agent-eval pipeline and verifies coverage reports instead of hand-writing notes.
whenToUse: Triggered by requests like "把 X.pptx 做成讲义", "run the agent pipeline", "agent-build", "用 agent 后端生成笔记" or any SlideNote agent workflow.
---

# SlideNote Agent Backend Workflow

This skill drives the experimental agent pipeline in the SlideNote repository
(`experiment/dsh-backend` branch). SlideNote keeps every deterministic step
(parse, assets, coverage, source map, merge); the writing step is delegated to
the DeepSeek backend and validated afterward. The pipeline never trusts the
model's self-reported coverage — SlideNote always reruns `analyze_coverage`.

## Commands

```powershell
# 1. Build the agent pack (deterministic; no writing model involved)
python -m slidenote agent-pack <input> --out <dir> [--vision off] [--ocr off]

# 2. Run the DeepSeek backend over the pack, then validate/merge/repair
python -m slidenote agent-run <pack_dir> --out <dir> --backend dsh

# 3. Pack + run in one step
python -m slidenote agent-build <input> --out <dir> --backend dsh

# 4. Compare baseline build vs agent build
python -m slidenote agent-eval <input> --out <dir> --backend dsh
```

`agent-pack` defaults to offline parsing (`--vision off --ocr off`); enable
`--vision auto` / `--ocr auto` only when the user explicitly wants richer
figure context in the pack (needs provider keys).

## Backend

`--backend dsh` — DeepSeek API through `slidenote.llm` (OpenAI-compatible;
keys via `DEEPSEEK_API_KEY` or `--dsh-api-key`). Local cache is on by default
at `<out>/.dsh_cache`; override with `--dsh-cache-dir`, disable with
`--dsh-cache off`. The first generation pass runs sections in parallel
(`--dsh-concurrency`, default 3); repair stays sequential. Other providers
supported by `slidenote.llm` can be selected with `--dsh-provider`.

The agent pack caps per-page context (12 text blocks, 200 chars per block,
capped OCR/visual summaries) while keeping full source-id lists, so large
decks stay within prompt budgets without losing coverage mapping.

## Output contract

The backend returns one JSON object per section:

```json
{
  "markdown": "## Section ...",
  "used_asset_paths": ["assets/images/example.png"],
  "covered_source_ids": ["s1_t1", "s1_img1"],
  "warnings": []
}
```

SlideNote rejects unknown `assets/` paths, enforces the source marker format
`<!-- slidenote-source: p{slide_id}:{ids} -->`, and runs coverage repair
(`--repair auto`, at most one round) for trace/visible/figure issues.

## Verification checklist (always do after a run)

1. Exit code 0; `agent_diagnostics.json` absent (or only on failure).
2. `coverage.md` / `coverage.json`: `missing == 0` when repair succeeded;
   read `required_visible_coverage` and `figure_coverage.unexplained_note_figures`.
3. `agent_run.json`: `backend`, `summary.warnings`, `repair.attempted_sections`,
   `repair.failed_repairs`; every warning explained in the final answer.
4. `notes.md` references only `assets/` paths that exist in the output dir.
5. For quality claims, run `agent-eval` and cite `eval_report.md` numbers
   (coverage ratio, figure missing/unexplained, note chars) instead of
   describing quality by feel.

## Operating rules

- Never modify `main` from this workflow; all agent work stays on
  `experiment/dsh-backend` (or a branch the user names).
- Do not hand-write the notes when the agent pipeline is requested; run the
  commands and inspect the reports.
- If a run fails, fix the cause (missing key, pack mismatch, malformed agent
  JSON) and rerun; keep the first failing diagnostics in the report.
