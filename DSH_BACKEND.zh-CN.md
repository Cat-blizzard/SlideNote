# DeepSeek 后端实验设计（DSH 生态）

本文档描述 `experiment/dsh-backend` 分支的实验路线：把"讲义生成、图文组织、
局部修订"这类高层写作能力交给 DeepSeek 后端（通过 `slidenote.llm` 的
OpenAI 兼容 API），SlideNote 保留解析、资产管理、coverage、source map、
文件写入和确定性校验。配套一个 DeepSeek Harness skill
（`.agents/skills/slidenote-agent/SKILL.md`），让 DSH agent 可以直接驱动
整条实验流程。

> 本分支已完全移除 Claude 相关元素（`--backend claude`、Claude Code CLI
> 调用、相关文档与测试），全面走 DeepSeek / 国产模型路线。
> 历史可追溯至 `experiment/claude-backend` 分支（已删除）。

## Agent Pack

`agent-pack` 复用确定性管线（解析、OCR/vision、sections、content guard、
资产复制），产出：

```text
agent_pack/
  manifest.json
  style.md
  skill.md
  sections/section_001.md
  assets/{screenshots,images,figures}/
```

后端按 section 返回 SlideNote JSON 契约：

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

- 使用 `slidenote.llm.LLMClient`（OpenAI 兼容；provider 默认 `deepseek`，
  支持 `--dsh-provider/--dsh-model/--dsh-api-key/--dsh-base-url`，
  其它 `slidenote.llm` 支持的 provider 均可选择）。
- 系统提示词 `_AGENT_SYSTEM_PROMPT` 约束模型只返回 SlideNote JSON 契约。
- 每次 section 调用默认启用本地磁盘缓存（`--dsh-cache on`，目录默认
  `<out>/.dsh_cache`，可用 `--dsh-cache-dir` 覆盖、`--dsh-cache off` 关闭；
  缓存键 = prompt + provider + model + schema 版本）。
- **首轮生成并行**：多个 section 通过 ThreadPoolExecutor 并行调用
  （`--dsh-concurrency`，默认 3；`slidenote.llm` 每次请求新建客户端，线程安全）；
  repair 轮保持串行，结果按 section 顺序落盘。
- **pack 上下文裁剪**：每页最多 12 个文本块、每块 200 字符，OCR/视觉摘要/
  图片说明设字符上限，超限块报告省略；**source ids 列表保持全量**，保证
  repair 的 coverage 映射不受裁剪影响。
- 结果解析走 `_parse_agent_json_text`（fence 剥离 + JSON 提取 + 字段校验）。
- 元数据记录在 `agent_run.json` 的 `sections[].dsh`：
  `source/model/usage/cache_status`。

## Repair Loop

`--repair auto --repair-rounds 1`：

1. 后端首次生成所有 section → 合并 → `analyze_coverage`。
2. 缺失项按 slide -> section 映射回对应 section，交给后端整节修订。
3. 替换、重合并、重跑 coverage；失败不阻断，保留首版输出并记录。

进入 repair 的问题类型：trace_missing / required_visible_missing /
figure_missing / figure_unexplained / figure_needs_review。
coverage 的图片解释判定（`note_explained` / `unexplained_note_figures` /
`matched_markdown_targets`）由本分支的 `coverage.py` 提供，基于笔记正文分析。

## 与 DeepSeek Harness 的集成

### 已落地：Skill（零 TypeScript）

`.agents/skills/slidenote-agent/SKILL.md` 是 DSH 的正式 skill。任何 DSH 会话
加载后，模型可以直接驱动 `agent-pack -> agent-run/agent-build -> agent-eval`
流程并按要求核对报告。

### 未落地（后续可选）

- **DSH headless / SDK 驱动**：`_run_dsh_command` 换成调用 DSH 运行时
  （`npx @deepseek-ai/dsh` headless 或 Python SDK）。注意 DSH Python SDK 的
  PTY 组合当前不支持 Windows；Windows 上优先 headless CLI 路径。
- **Cordis 插件**：注册 tool plugin（`slidenote_agent_build` /
  `slidenote_agent_eval`）或 subagent provider；发布 npm + `dsh-plugin` topic。
- **workflow 编排**：多 section 并行写作 + repair 循环交给 DSH workflow。

## Agent Eval

`agent-eval` 同时运行 baseline（`slidenote build --preset local`）与 agent 构建
（`--backend dsh`），产出 `eval_report.json/.md`，对比 coverage ratio、
trace/visible missing、figure missing/unexplained、note 规模、估计调用数与
review checklist。`agent_run.json` 的 `backend` 字段会写入 eval 报告，
便于未来接入其它后端时做横向对比。

## 判断标准

沿用实验的验收哲学：dsh 后端必须在漏内容、图片解释、讲义结构、repair 改善、
失败诊断、重跑成本、可追溯性上足够可靠，才有资格讨论替换正式入口。
任何结论都要有 `agent-eval` 数据支撑，不凭感觉。
