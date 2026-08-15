# SlideNote 基准测试指南（Benchmark Guide）

> 目的：用**真实课件**建立 SlideNote 笔记生成质量的量化基线，让测试人员可以在
> 不同分支 / 不同配置之间做可复现的对比。基准结论是后续一切优化（prompt、
> 模型、架构）的对照组——**先有数字，再谈改进**。

## 适用分支

| 基准 | 命令 | 分支 |
| --- | --- | --- |
| A. 确定性管线 | `slidenote build`（`lecture` vs `local`） | `main`（所有分支可用） |
| B. Agent 后端 | `slidenote agent-eval` / `agent-build --backend dsh` | `experiment/dsh-backend` |

## 准备

1. 环境：`.\install.ps1`（或 `python -m pip install -e ".[dev,llm]"`），`python -m slidenote doctor` 确认无红叉。
2. API key（基准 B 与 lecture preset 需要）：`DEEPSEEK_API_KEY`（文本模型）；课件含图时可再配 vision/OCR 的 key。
3. 课件建议（覆盖面比数量重要，至少准备 3-5 份）：
   - 类型：理论课（多文字）、图文课（多图/流程图）、表格多的课
   - 规模：10-20 页的短课件 + 50 页以上的长课件各若干
   - 格式：PDF（原生文本）和扫描/低文本 PDF（触发 OCR）各准备
   - 用途说明：同一份课件在**同一个输出目录**下重复跑时，LLM 缓存会命中，
     第二次结果用于验证缓存；**换输出目录**才算新的一次独立生成。

---

## 基准 A：确定性管线（main 分支）

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
| `quality_report.json` | `coherence_score` / `explanation_depth_score` / `figure_integration_score` / `hallucination_risk` / `suggested_repairs` |
| `run_summary.json` | `run.preset` / `stage_timings`（各阶段耗时，找瓶颈） |
| `progress.json` | 阶段进度与 ETA |

---

## 基准 B：Agent 后端对比（experiment/dsh-backend 分支）

```powershell
git checkout experiment/dsh-backend
python -m slidenote doctor
```

### B1. 一键对比（推荐起点）

`agent-eval` 自动跑两条线并出对比报告：基线（`slidenote build --preset local`）
vs agent 流程（`agent-pack` + `agent-run --backend dsh`）。

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

## 对比维度（A/B 通用记录表）

对每一份课件，按下面模板记录（建议存成 `benchmark-YYYYMMDD.md`）：

| 维度 | 说明 | 怎么判 |
| --- | --- | --- |
| coverage ratio | 元素覆盖率 | 越高越好；`missing` 应说明遗漏类型 |
| required visible missing | 必讲内容缺失 | 应为 0；>0 说明关键知识点被跳过 |
| figure missing / unexplained | 图片缺失/插了没解释 | 越低越好 |
| 讲义结构 | 章节、小标题、连贯性 | 人工阅读评分（1-5） |
| 讲解深度 | 是否解释了"为什么" | 人工阅读评分（1-5） |
| 图片解释质量 | 图旁是否有说明文字 | 人工阅读评分（1-5） |
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
| 耗时 | | | | |
| token 总量 | | | | |
| 失败/警告 | | | | |

观察与问题：
- ____
```

---

## 注意事项

1. **对比一致性**：同一课件对比时，尽量固定 API key、模型（`--provider`/`--dsh-model`）、
   并发数；不同日期的重跑因模型版本变化可能有波动，记录时注明。
2. **缓存**：LLM 输出有本地磁盘缓存（lecture 与 agent 流程均有）。"换输出目录 = 新生成"；
   想强制重跑用 `--cache refresh`（build）或 `--dsh-cache refresh`（agent-run）。
3. **耗时测量**：`build` 看 `run_summary.json` 的 `stage_timings`；agent 流程可对比
   `--dsh-concurrency 1` 与默认 3 的差异。
4. **不要只看数字**：coverage 高不代表笔记好——必须人工读 1-2 份 `notes.md` 评
   讲解质量。数字防回归，阅读定质量。
5. **Windows**：命令均为 PowerShell 语法；Linux/macOS 去掉 `.ps1` 脚本与 `$env:`，
   直接用 `python -m slidenote ...`。
