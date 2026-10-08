# SlideNote 固定样本集与评测流程（P0）

目的：让"单份课件的笔记可靠、好读"成为**可复核、可比较**的工程目标，而不是
只看 coverage 分数或单次人工感觉。本目录是 ROADMAP P0-1（真实课件评测与内容
核对）的落地工具。

## 目录结构

```text
benchmarks/
  README.md                  本文件
  samples.manifest.json      固定样本清单（评测的输入，改动前后必须用同一份）
  samples/                   样本课件
    synthetic_course.pdf     固定合成样例（scripts/make_sample_deck.py 生成）
  rubric.template.json       人工 1-5 分评分 rubric（结构/连贯/解释深度等 8 维度）
  spot-check.template.md     人工抽查记录模板（关键事实/漏项/图文对应/来源定位）
  runs/<run_id>/             每次评测的输出（eval_report.json/md、outputs/、failures/）
```

## 固定样本集要求

按 ROADMAP P0 的特征矩阵收集，每类至少一份，真实课件优先：

| 特征 | 要求 | 记录在 manifest 的 `features` |
| --- | --- | --- |
| 文字密集页 | 定义/推理段落为主的课件 | `text-dense` |
| 图表 | 流程图、示意图，需要图文对应 | `chart` |
| 表格 | 有对比结论的表格 | `table` |
| 公式 | 含数学公式或代码推导 | `formula` |
| 扫描页 | 图片化、需要 OCR 的页面 | `scanned` |
| 长课件 | 50 页以上 | `long` |

加入真实课件时，在 `samples.manifest.json` 增加一个 case：

```json
{
  "case_id": "cs168-lecture05",
  "source": "samples/cs168-lecture05.pdf",
  "description": "真实课件：xxx 课程第 5 讲",
  "features": ["text-dense", "chart"],
  "language": "zh",
  "expected_pages": 42,
  "source_origin": "user-provided",
  "preset": "local",
  "export": "markdown-zip",
  "manual_notes": "samples/notes/cs168-lecture05.human-notes.md（如有，且允许作为参考时不进仓库）",
  "allow_fewshot": false
}
```

规则：

- 真实课件与人工笔记**不要提交**到公共仓库（版权/隐私）；`source_origin:
  user-provided` 的 case 只保留本地路径并在 `manual_notes` 记录笔记位置。
- `allow_fewshot: true` 表示该笔记允许摘出去内容化的片段作为风格示例；
  默认 `false`，避免内容泄漏进 prompt。
- `synthetic_course.pdf` 由 `scripts/make_sample_deck.py` 生成，内容固定，
  覆盖六类特征，用于 CI 和无 API 冒烟；它不能替代真实课件的人工评分。

## 评测流程

1. **生成/确认样本**：`python scripts/make_sample_deck.py`（或放入真实课件并更新 manifest）。
2. **跑基线**：
   ```powershell
   python scripts/eval_decks.py benchmarks/samples.manifest.json --out benchmarks/runs/<日期>-baseline
   ```
   无 API 时用 manifest 里的 `preset: local`（离线，确定性指标）；正式评测把
   `preset` 设为 `lecture` 并配置 API key，运行时间和成本才有产品意义。
3. **改动后再跑同一 manifest**，并和基线比较：
   ```powershell
   python scripts/eval_decks.py benchmarks/samples.manifest.json --out benchmarks/runs/<日期>-after --baseline benchmarks/runs/<日期>-baseline
   ```
   `eval_report.md` 会给出每个用例的耗时/tokens/覆盖/启发式分数差值；注意
   离线 `local` 预设不调用模型，耗时差值主要反映解析与导出。
4. **人工抽查**：对 lecture 运行结果使用 `spot-check.template.md`（关键事实
   10 条、漏项、图文对应、来源定位 10 处）+ `rubric.template.json` 打分。
   两位评审独立打分，不要共享。
5. **失败案例**：任何硬门槛失败（关键报告缺失或无效、必讲内容漏项 > 0、lecture
   产物出现逐页标题、构建/导出失败、正文为空）会在 `runs/<run>/failures/<case_id>/` 留下 notes.md、
   coverage、质量报告和命令记录，保证失败可复核、可进 issue。

## 硬门槛与启发式的边界

- **硬门槛（自动，失败即失败）**：关键构建/coverage/quality/export 报告存在且
  关键字段有效、必讲内容漏项为 0、lecture 产物无"第 N 页"式正文标题、构建与
  导出无阻塞错误。这是回归防线。local 预览允许逐页标题，但报告仍明确显示检测结果。
- **启发式分数（自动，仅用于比较）**：coherence、explanation_depth、结构契约
  分数等只能发现明显回归，**不代表内容正确或好读**。
- **人工抽查（最终质量结论）**：内容是否讲对、漏了什么、图文是否对应，只能
  通过对照原课件的人工抽查判断。三者不可互相替代，详见
  [docs/quality-and-guard.zh-CN.md](../docs/quality-and-guard.zh-CN.md)。

## 排版验收

评测通过后，用排版验收工具检查阅读体验（标题层级、段落密度、长表格、图文
错位、断页/溢出），问题能定位到样本与页：

```powershell
python scripts/verify_note_layout.py benchmarks/runs/<日期>-after/outputs
```

详见该脚本 `--help` 与 [docs/benchmark.zh-CN.md](../docs/benchmark.zh-CN.md)。
