# SlideNote 基准测试指南（Benchmark Guide）

> 目的：用**真实课件**建立 SlideNote 笔记生成的基线，让测试人员可以在
> 不同分支 / 不同配置之间做可复现的对比。记录结构指标、人工核对结果、
> 耗时和成本，再判断 prompt、模型或架构改动是否真的改善了笔记。

## 适用范围

先用 `main` 的 `slidenote build` 建立单份课件的笔记基线。下文“实验分支附录”只适用于 `experiment/dsh-backend`，其 `agent-*` 命令不在 `main` 中。两条流程的自动指标可用于发现回归，质量结论还需要阅读原课件与笔记。

| 基准 | 命令 | 分支 |
| --- | --- | --- |
| A. 主线构建 | `slidenote build`（`lecture` 与 `local`） | `main` |
| B. Agent 实验 | `slidenote agent-eval` / `agent-build --backend dsh` | `experiment/dsh-backend` |

## 准备

1. 环境：`.\install.ps1`（或 `python -m pip install -e ".[llm]"`），运行 `python -m slidenote doctor` 检查当前流程所需依赖。
2. API key（lecture 和基准 B 需要）：`DEEPSEEK_API_KEY`（文本模型）；课件含图时按实际配置补齐 Vision/OCR 的 key。
3. 课件建议（覆盖面比数量重要，至少准备 3-5 份）：
   - 类型：理论课（多文字）、图文课（多图/流程图）、表格多的课
   - 规模：10-20 页的短课件 + 50 页以上的长课件各若干
   - 格式：PDF（原生文本）和扫描/低文本 PDF（触发 OCR）各准备
   - 同一份课件在同一个输出目录下重复跑时可能命中缓存；测量独立生成的耗时与用量时，使用新的输出目录并记录缓存状态。

---

## 基准 A：主线构建（main 分支）

### A1. 离线基线（无 API）

```powershell
python -m slidenote build path\to\lecture.pdf --out outputs\baseline-local --preset local
```

### A2. 质量流程（默认 lecture preset）

```powershell
$env:DEEPSEEK_API_KEY="..."
python -m slidenote build path\to\lecture.pdf --out outputs\baseline-lecture --export markdown-zip
```

### A1/A2 需要记录与解读的产物

| 产物 | 关键指标 |
| --- | --- |
| `notes.md` | 直接阅读：结构、讲解深度、图片是否插入且解释 |
| `coverage.md` / `coverage.json` | `missing`（未覆盖元素数）、`coverage_ratio`、`required_visible_coverage`（必讲内容漏没漏）、`figure_coverage`（图片缺失/未解释数） |
| `quality_report.json` | `coherence_score` / `explanation_depth_score` / `figure_integration_score` / `hallucination_risk` / `suggested_repairs`；均为启发式诊断，不能代替事实核查 |
| `run_summary.json` | `run.preset` / `stage_timings`（各阶段耗时，找瓶颈） |
| `progress.json` | 阶段进度与 ETA |

---

## 实验分支附录：Agent 后端（experiment/dsh-backend）

以下命令只在实验分支存在。`agent-eval` 内置的 `local` 对照适合检查实验流程能否运行及其结构指标；它与使用文本模型的 Agent 输入条件不同，不能据此断言 Agent 的讲义质量优于主线 `lecture`。质量对比应另跑同一课件的 `lecture`，记录所用模型、视觉/OCR 配置、缓存状态，并人工盲评笔记。

```powershell
git checkout experiment/dsh-backend
python -m slidenote doctor
```

### B1. 一键对比（推荐起点）

`agent-eval` 自动跑两条线并出结构指标报告：基线（`slidenote build --preset local`）
与 Agent 流程（`agent-pack` + `agent-run --backend dsh`）。

```powershell
$env:DEEPSEEK_API_KEY="..."
python -m slidenote agent-eval path\to\lecture.pdf --out outputs\eval-lecture
```

产物：

| 产物 | 内容 |
| --- | --- |
| `eval_report.md` | 人类可读的对比结论（推荐直接看这个） |
| `eval_report.json` | 结构化对比数据 |
| `baseline_build/` | 基线管线产物（notes.md、coverage.json…） |
| `agent_build/` | agent 流程产物（notes.md、agent_run.json…） |

### B2. 单独跑 agent 流程

```powershell
# 只打包（确定性，无 LLM 调用）
python -m slidenote agent-pack path\to\lecture.pdf --out outputs\agent-pack-out

# 写作 + 校验 + 一轮 repair
python -m slidenote agent-run outputs\agent-pack-out\agent_pack --out outputs\agent-run-out --backend dsh

# 打包 + 写作一步完成
python -m slidenote agent-build path\to\lecture.pdf --out outputs\agent-build-out --backend dsh
```

`agent_run.json` 关键字段：`backend`、`summary.coverage_ratio`、`summary.warnings`、
`repair.attempted_sections` / `failed_repairs`、`sections[].dsh.usage`（token 用量）。
更多设计说明见实验分支根目录的 `DSH_BACKEND.zh-CN.md`。

---

## 对比维度与人工验收

对每一份课件，按下面模板记录（建议存成 `benchmark-YYYYMMDD.md`）。人工评分应对照原页检查事实、条件、数字、公式和图表解释；最好隐藏生成路线，让评审先读结果再揭晓配置。导出 DOCX/PDF 时还要打开实际文件，检查标题层级、图文相邻、中文换行、公式表格和分页。

| 维度 | 说明 | 怎么判 |
| --- | --- | --- |
| coverage ratio | 元素结构覆盖率 | 记录数值与遗漏类型；高分不证明讲解正确 |
| required visible missing | 必讲项可见覆盖缺失 | 记录并回看原页；0 也不证明语义完整 |
| figure missing / unexplained | 图片缺失/插了没解释 | 越低越好 |
| 讲义结构 | 章节、小标题、连贯性 | 人工阅读评分（1-5） |
| 讲解深度 | 是否解释了"为什么" | 人工阅读评分（1-5） |
| 图片解释质量 | 图片说明是否准确、是否支持相邻论述 | 对照原图评分（1-5） |
| 内容准确性 | 数字、条件、公式、图表解读是否与原页相符 | 对照课件抽查，记录错误页码 |
| 阅读排版 | 标题、图文位置、公式表格、换行与分页 | 检查 Markdown；导出时检查实际 DOCX/PDF |
| 耗时 | 全程耗时 + 各阶段 | 记录，便于后续优化对比 |
| token / 成本 | `llm_usage.json` / `agent_run.json` | 记录总量 |
| 失败与警告 | 诊断、repair 失败、warnings | 如实记录 |

### 记录模板

```markdown
## 课件：<文件名>（<页数>页，<类型>）

- 日期 / 分支 / commit：____
- 命令：____

| 指标 | local 基线 | lecture 基线 | agent (dsh) | 备注 |
| --- | --- | --- | --- | --- |
| coverage ratio | | | | |
| missing | | | | |
| required visible missing | | | | |
| figure missing / unexplained | | | | |
| 讲义结构（1-5） | | | | |
| 讲解深度（1-5） | | | | |
| 图片解释（1-5） | | | | |
| 内容准确性（1-5） | | | | |
| 阅读排版（1-5） | | | | |
| 耗时 | | | | |
| token 总量 | | | | |
| 失败/警告 | | | | |

观察与问题：
- ____
```

---

## 注意事项

1. **对比一致性**：同一课件对比时，固定或记录模型、provider、视觉/OCR 配置和运行日期；实验分支额外记录 `--dsh-model`。`local`、`lecture` 和 Agent 成本与能力不同，不宜只凭一个总分排序。
2. **缓存**：LLM 输出有本地磁盘缓存。主线 `build` 没有公开的 `--cache refresh` 参数；测独立运行时使用新的输出目录并记录缓存状态。实验分支的 `agent-run` 可使用 `--dsh-cache refresh`。
3. **耗时测量**：`build` 看 `run_summary.json` 的 `stage_timings`；agent 流程可对比
   `--dsh-concurrency 1` 与默认 3 的差异。
4. **不要只看数字**：coverage 和本地质量分数是结构信号，不验证事实。每种课件类型都应人工核对原页与笔记，记录具体错误和导出排版问题。
5. **Windows**：命令均为 PowerShell 语法；Linux/macOS 去掉 `.ps1` 脚本与 `$env:`，
   直接用 `python -m slidenote ...`。
