# Claude Code Backend 实验设计

本文档描述 `experiment/claude-backend` 分支的实验路线。它不是 `main` 分支稳定 pipeline 的替代说明，而是记录一条新的实现假设：

> SlideNote 不再试图自己实现所有高层写作能力，而是把“讲义生成、图文组织、局部修订”交给 Claude Code；SlideNote 保留解析、资产管理、coverage、source map、文件写入和确定性校验。

## 当前状态

已经实现的实验命令：

```powershell
python -m slidenote agent-pack <input> --out <dir>
python -m slidenote agent-run <agent_pack_dir> --backend claude --out <dir>
python -m slidenote agent-build <input> --out <dir>
python -m slidenote agent-eval <input> --out <dir>
```

当前边界：

- 只支持官方 Claude Code CLI。
- 只支持 CLI，不接 GUI。
- Claude 使用 stdout JSON 返回结果，不直接写文件。
- 不接入 `claw-code`、opencode 或其他复写版。
- 不改 `slidenote build` 的默认行为。
- repair 第一版最多一轮。

## 为什么这样拆

传统 `build` pipeline 已经承担了很多确定性职责：

- PPT/PDF 解析。
- 页面截图、嵌入图、局部图、组合图资产管理。
- OCR / Vision / table summary / semantic layout 的结构化中间结果。
- 稳定 source id 和 Element IR。
- coverage、required visible coverage、figure audit、source map。
- 输出文件和报告落盘。

这些能力适合由 SlideNote 保留，因为它们需要可重复、可检查、可测试。

Claude Code 更适合接管的是另一类能力：

- 把分散 slide 内容写成自然讲义。
- 决定图片应该插在哪里。
- 用已有 OCR、visual summary、table summary 解释图表。
- 根据 coverage 缺失清单重写某一节。
- 在上下文足够时做更好的篇章组织。

所以这个分支不是“让 Claude Code 替代 SlideNote”，而是验证一个更窄的结构：

```text
SlideNote parse/check/write
        ->
agent pack
        ->
Claude Code write/repair
        ->
SlideNote validate/merge/coverage/eval
```

## Agent Pack

`agent-pack` 会运行现有解析和结构化阶段，但不生成最终讲义。输出目录固定为：

```text
agent_pack/
  manifest.json
  style.md
  skill.md
  sections/
    section_001.md
    section_002.md
  assets/
    screenshots/
    images/
    figures/
```

核心文件：

- `manifest.json`：schema version、source、sections、assets、deck、section_plan、content_guard。
- `sections/section_XXX.md`：每节的文本、表格、图片引用、source ids、必讲内容要求。
- `assets/`：Claude 允许引用的图片资产。
- `style.md`：讲义风格规则。
- `skill.md`：Claude Code 任务说明，后续可迁移成正式 skill。

图片引用规则：

- Claude 只能引用 pack 内已有 `assets/...` 路径。
- Markdown 图片必须使用现有资产路径，例如 `![Figure](assets/figures/s3_fig1.png)`。
- SlideNote 会校验 Claude 返回的 `used_asset_paths` 和 Markdown 里的图片路径。

## Claude stdout JSON

Claude Code 调用形态：

```powershell
claude -p --bare --output-format json --add-dir <agent_pack_dir> <prompt>
```

SlideNote 期待 Claude 返回 JSON。第一版字段为：

```json
{
  "markdown": "## Section ...",
  "used_asset_paths": ["assets/images/example.png"],
  "covered_source_ids": ["s1_t1", "s1_img1"],
  "warnings": []
}
```

约束：

- `markdown` 是完整 section Markdown，不是 diff。
- `used_asset_paths` 必须只包含 pack 内路径。
- `covered_source_ids` 表示 Claude 认为已经覆盖的原始元素。
- `warnings` 用于记录 OCR 不清、图片含义不确定、可能遗漏等风险。

即使 Claude 返回了 `covered_source_ids`，SlideNote 仍会重新跑 coverage，不完全信任模型自报。

## Repair Loop

`agent-run` / `agent-build` 默认：

```powershell
--repair auto --repair-rounds 1
```

流程：

1. Claude 首次生成所有 section。
2. SlideNote 合并 `notes.md`。
3. SlideNote 运行 `analyze_coverage`。
4. 如果发现缺失，按 slide -> section 映射回对应 section。
5. SlideNote 把缺失清单、原 section pack、当前 section markdown 交给 Claude。
6. Claude 返回整节 revised markdown。
7. SlideNote 替换该节、重新合并、重新跑 coverage。

当前会进入 repair 的问题：

- `trace_missing`：source id 没有被覆盖。
- `required_visible_missing`：必讲内容只有 marker 或完全没解释。
- `figure_missing`：重要图没有插入笔记。
- `figure_unexplained`：图被插入了，但图片旁边没有可见解释。
- `figure_needs_review`：图像解析阶段标记需要复查，并且仍存在缺图或未解释问题。

repair 失败不会阻断整体输出。SlideNote 保留首次生成结果，并在 `agent_run.json` 里记录失败原因。

## 输出文件

`agent-run` / `agent-build` 输出：

```text
agent_sections/
  section_001.md
  section_002.md
assets/
notes.md
coverage.json
coverage.md
source_map.json
agent_run.json
agent_diagnostics.json   # 仅失败时
```

`agent_run.json` 记录：

- section 总数。
- 每节 Claude metadata、warnings、used assets、covered ids。
- repair before / after summary。
- repair attempted sections、failed repairs、unmapped issues。
- coverage ratio 和 warnings 汇总。

## Agent Eval

`agent-eval` 用来避免“凭感觉重构”。它会同时运行：

```text
baseline_build/  -> slidenote build
agent_build/     -> slidenote agent-build
```

然后生成：

```text
eval_report.json
eval_report.md
```

比较指标：

- coverage ratio。
- trace missing。
- visible missing。
- required visible missing。
- figure missing。
- figure unexplained。
- note chars / words。
- image count。
- source marker count。
- repair status。
- estimated Claude calls。
- review checklist。

这个命令是后续决定“哪些旧能力可以降级”的依据。

## 与旧 Pipeline 的关系

`slidenote build` 仍然是稳定入口。当前分支不把 build 改成 Claude-first，原因是：

- 旧 pipeline 是对照组。
- GUI、导出、配置、缓存、usage 报告仍依赖 build。
- Claude Code 的账号、认证、成本、稳定性不应影响旧入口。
- 需要先用 `agent-eval` 积累质量证据。

迁移策略：

1. 保留旧 build。
2. 强化 `agent-build`。
3. 用 `agent-eval` 对比质量。
4. 找出 Claude 明显更强的环节。
5. 在实验分支逐步把这些环节交给 Claude。
6. 只有当质量和可控性都足够，再讨论是否让正式入口 Claude-first。

## Claude Code 认证与 API

SlideNote 当前只把 Claude Code 当作一个本机可执行后端：

- SlideNote 不保存 Claude 账号。
- SlideNote 不管理 Claude Code 登录态。
- SlideNote 不内置第三方 Claude Code API 适配。
- Claude CLI 的认证、模型和网络配置由用户本机 Claude Code 环境负责。

如果用户本机的 Claude Code 支持某种自定义网关或环境变量配置，SlideNote 不阻止；但本分支第一版不把这些变成 SlideNote 配置项。

## 不做什么

第一阶段明确不做：

- 不接 `claw-code`。
- 不接 opencode。
- 不让 Claude 直接写输出文件。
- 不让 Claude 修改仓库。
- 不把 GUI 改成 Claude backend。
- 不删除旧 notes generation。
- 不做多轮无限 repair。

这些限制不是保守，而是为了让实验可测、可回滚、可对比。

## 下一步

优先级从高到低：

1. 用真实小课件跑 `agent-eval`，积累 baseline vs agent 对比样例。
2. 改进 agent pack 的信息密度，减少无关字段，强化图/表/公式上下文。
3. 增强 repair prompt，让 Claude 对缺失项给出更稳定的整节修订。
4. 将 figure audit、required visible coverage 的报告变得更适合人工审阅。
5. 增加可复现的 fixture，用 fake Claude 覆盖更多失败场景。
6. 评估哪些旧的高层写作逻辑可以在实验分支降级。
7. 再讨论是否需要 GUI 或 Claude-first build。

## 判断标准

Claude backend 不是因为“用了 Claude”就算成功。它需要在这些方面赢：

- 漏内容更少。
- 图片解释更自然。
- section 结构更像讲义，而不是逐页摘要。
- repair 后 coverage 明确改善。
- 失败时诊断清楚。
- 重跑成本可接受。
- 输出仍然能被 source map 和 coverage 检查。

只要这些指标不能稳定胜过旧 pipeline，就不应该替换正式入口。
