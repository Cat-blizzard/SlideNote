# DeepSeek Harness / DeepSeek 后端实验设计

本文档描述 `experiment/dsh-backend` 分支的实验路线。它从 `experiment/claude-backend`
分支继承"把高层写作交给外部 agent"的假设，但把写作后端从闭源的 Claude Code 换成
DeepSeek（通过 `slidenote.llm` 的 OpenAI 兼容 API 直连），并配套一个 DeepSeek
Harness skill，让 DSH agent 可以直接驱动整条实验流程。

> SlideNote 保留解析、资产管理、coverage、source map、文件写入和确定性校验；
> "讲义生成、图文组织、局部修订"交给 agent 后端（DeepSeek，或保留的 Claude 对照组）。

## 与 claude-backend 分支的关系

- `experiment/claude-backend`：Claude Code CLI 作为唯一后端（`--backend claude`）。
- `experiment/dsh-backend`：基于新 main 重建；`--backend` 支持 `claude` 与 `dsh`；
  编排核心 `_run_agent_pack_core` 通过 runner 回调注入后端，两个后端共享同一套
  pack / JSON 契约 / 校验 / repair / 合并逻辑。
- 两个分支都保持实验状态，不修改 `main` 的稳定 `build` 入口。

## Agent Pack

与 claude-backend 相同（`agent-pack` 复用确定性管线，产出）：

```text
agent_pack/
  manifest.json
  style.md
  skill.md
  sections/section_001.md
  assets/{screenshots,images,figures}/
```

JSON 结果契约不变：

```json
{
  "markdown": "## Section ...",
  "used_asset_paths": ["assets/images/example.png"],
  "covered_source_ids": ["s1_t1", "s1_img1"],
  "warnings": []
}
```

## DeepSeek 后端（--backend dsh）

`agent_backend.py::_run_dsh_command`：

- 使用 `slidenote.llm.LLMClient`（OpenAI 兼容，provider 默认 `deepseek`；
  支持 `--dsh-provider/--dsh-model/--dsh-api-key/--dsh-base-url`）。
- 系统提示词 `_AGENT_SYSTEM_PROMPT` 约束模型只返回 SlideNote JSON 契约。
- 每次 section 调用可走本地磁盘缓存（`--dsh-cache on --dsh-cache-dir <dir>`，
  缓存键 = prompt + provider + model + schema 版本）。
- 结果解析复用 `_parse_agent_json_text`（fence 剥离 + JSON 提取 + 字段校验），
  与 claude 后端共用 `_validate_agent_result_payload`。
- 元数据记录在 `agent_run.json` 的 `sections[].dsh`：`source/model/usage/cache_status`。

Claude 后端（`--backend claude`）原样保留，作为对照组与迁移期保底。

## Repair Loop

两个后端共用同一 repair 逻辑（`--repair auto --repair-rounds 1`）：

1. 首次生成所有 section → 合并 → `analyze_coverage`。
2. 缺失项按 slide -> section 映射回对应 section，交给后端整节修订。
3. 替换、重合并、重跑 coverage；失败不阻断，保留首版输出并记录。

进入 repair 的问题类型（trace_missing / required_visible_missing / figure_missing /
figure_unexplained / figure_needs_review）与 claude-backend 分支一致。

## 与 DeepSeek Harness 的集成

### 已落地：Skill（零 TypeScript）

`.agents/skills/slidenote-agent/SKILL.md` 是 DSH 的正式 skill（kebab-case 命名 +
frontmatter）。任何 DSH 会话加载该 skill 后，模型可以直接驱动
`agent-pack -> agent-run/agent-build -> agent-eval` 流程并按要求核对报告。

### 未落地（后续可选）

- **DSH headless / SDK 驱动**：`_run_dsh_command` 换成调用 DSH 运行时
  （`npx @deepseek-ai/dsh` headless 或 Python SDK）。注意：DSH Python SDK 的
  PTY 组合当前不支持 Windows；Windows 上优先 headless CLI 路径。
- **Cordis 插件**：注册 tool plugin（`slidenote_agent_build` / `slidenote_agent_eval`）
  或 subagent provider，让模型一键触发；发布 npm + `dsh-plugin` topic。
- **workflow 编排**：多 section 并行写作 + repair 循环交给 DSH workflow。

## Agent Eval

`agent-eval` 同时运行 baseline（`slidenote build --preset local`）与 agent 构建，
产出 `eval_report.json/.md`，对比 coverage ratio、trace/visible missing、
figure missing/unexplained、note 规模、估计调用数与 review checklist。
这是决定"哪些旧能力可以降级"和"哪个后端更强"的证据来源。

## 判断标准

沿用 claude-backend 分支的验收哲学：dsh 后端必须在漏内容、图片解释、讲义结构、
repair 改善、失败诊断、重跑成本、可追溯性上不劣于对照组，才谈得上替换正式入口。
任何结论都要有 `agent-eval` 数据支撑，不凭感觉。
