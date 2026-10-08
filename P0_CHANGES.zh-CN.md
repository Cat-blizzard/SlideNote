# P0 修改说明（2026-10-06，2026-10-07 复核修订）

本包基于官方最新版 `SlideNote-main`（GitHub commit `3fca464`）制作，包含两部分工作：
移植旧版本里未同步的本地改进 + 落实 ROADMAP 的 P0 三项。全部改动已通过验证：
**354 个测试全绿（基线 337 + 新增 17）、无 API 冒烟通过、评测与排版工具实测跑通。**

## 一、从旧版本移植的改动（微信包里未提交的本地改进）

| 文件 | 改动 |
| --- | --- |
| `slidenote/notes/structure.py`（新） | 讲义结构契约评估：必备栏目（本讲目标/主题章节/本讲总结/章节自测）+ 推荐栏目（例子/易错点），检测"第 N 页"式逐页标题和重复模板栏目 |
| `slidenote/notes/document_frame.py`（新） | 确定性文档框架：从 deck brief 提取内容补齐缺失的全局栏目，幂等、不改章节正文、不新增课件外事实 |
| `slidenote/notes/prompt_templates.py` | 单页生成改为"中间证据卡"定位；weave/教学增强按上下文选择全文结构契约或自适应章节规则；收紧事实边界（禁止新增无依据数字/结论）；新增结构修复 prompt |
| `slidenote/notes/repair.py` | 新增 `_repair_note_structure_once` 并接线到 finalize。复核版要求修订后契约通过、正文至少保留 80%、精确保留来源标记/图片目标、coverage 不回退且输出未截断；任一不满足即保留原稿 |
| `slidenote/notes/finalize.py` | lecture-weave 收尾时应用文档框架；契约仍不通过时触发一次结构修复（带守护） |
| `slidenote/notes/quality.py` | 质量报告新增 `structure_contract`、`human_note_structure_score`、`mechanical_structure_pass` 等字段与对应改进建议 |
| `slidenote/notes/versions.py` | prompt 版本号升级（page-lecture-v5 / weave-v5 / teaching-enrichment-v3 / structure-repair-v2），旧缓存自动失效 |
| `slidenote/notes/usage.py` | 用量报告增加 `repair_calls` 计数 |
| `slidenote/build/config.py`、`notes/__init__.py` | 默认 `temperature` 0.2→0.0（可复现评测）、`weave_dedup` soft→normal（配合新去重文案） |
| `tests/test_notes.py` | 断言适配新 prompt 文案 |
| `tests/test_note_structure.py`（新） | 结构契约/文档框架/结构修复守护/质量报告的 9 个测试，包含假阳性和严重截断回归测试 |

## 二、P0 三项落地（ROADMAP）

### P0-1 真实课件评测与内容核对

- `benchmarks/README.md`：样本集要求、评测流程、硬门槛与启发式/人工复核的边界。
- `benchmarks/samples.manifest.json`：固定样本清单（改动前后必须用同一份）。
- `benchmarks/rubric.template.json`：8 维度人工评分 rubric（1-5 分，含锚点）。
- `benchmarks/spot-check.template.md`：人工抽查模板（关键事实 10 条/漏项/图文对应/来源定位 10 处/耗时成本）。
- `scripts/make_sample_deck.py`：生成固定合成样例（文字密集页、表格、公式、折线图、扫描页、18 页），可重复构建、可提交仓库。
- `scripts/eval_decks.py`：评测 harness——按 manifest 逐例运行 build，记录耗时/阶段耗时/tokens/成本估算/coverage/启发式分数；硬门槛失败自动把 notes、coverage、质量报告留档到 `runs/<run>/failures/<case>/`；`--baseline` 与上次运行逐项对比。

用法：

```powershell
python scripts/make_sample_deck.py
python scripts/eval_decks.py benchmarks/samples.manifest.json --out benchmarks/runs/baseline
# 改动后：
python scripts/eval_decks.py benchmarks/samples.manifest.json --out benchmarks/runs/after --baseline benchmarks/runs/baseline
```

### P0-2 笔记阅读与导出排版验收

- `scripts/verify_note_layout.py`：三层检查，每个问题定位到样本/文件/页码。
  - Markdown：标题跳级、多个 H1、空章节、图片无 alt/无邻近解释、超宽表格、行列不齐、代码围栏/公式未闭合、缺来源标记。
  - Word（pandoc 导出）：标题/表格/图片数量与 Markdown 对比、表格行列错位。
  - PDF：文本溢出页边界、图片与文字重叠（图文错位）、空白页、页尾孤立标题。
  - 缺 pandoc/LibreOffice 时对应层显示 skipped 并注明原因。

```powershell
python scripts/verify_note_layout.py outputs\你的输出目录
python scripts/verify_note_layout.py benchmarks\runs\after\outputs
```

### P0-3 来源与质量提示说清边界

- GUI Quality 标签页：新增三层信号说明（coverage=结构线索、quality_report=启发式、人工复核=最终结论），新增"建议人工复核"清单（合并 coverage 缺项、图片缺失、幻觉风险、结构契约失败，每行注明信号来源和查看位置）。
- `docs/quality-and-guard.zh-CN.md`：三层质量信号对照表（各自能回答/不能回答什么）、结构契约字段说明。
- `docs/benchmark.zh-CN.md`：新工具用法。
- `ROADMAP.zh-CN.md`：标注 P0 工具已就绪，真实课件样本与人工评分待收集。

## 三、验证结论

- `python -m pytest`：354 passed（完整安装 dev/llm/gui，0 skipped）。
- `python scripts/smoke_first_run.py`：通过。
- 评测 harness 实测：合成样例约 0.9s，仓库外绝对 `--out` 与 `--baseline` 正常；报告会明确显示 local 产物的逐页标题，但不把它作为 lecture 硬门槛。
- 排版验收：Markdown/docx/PDF 三层规则均有自动回归测试；当前合成运行只请求 markdown-zip，因此实际 docx/PDF 导出仍按环境显示 skipped。
- GUI 导入与复核面板冒烟通过。

## 四、2026-10-07 独立复核修复

- 阻止结构修复把长讲义截断成短提纲后仍被接受。
- 校验 manifest `case_id` 并证明输出目录位于评测根目录内，消除路径穿越删除风险。
- `coverage.json`、`quality_report.json`、`run_summary.json`、`export_report.json` 缺失或关键字段无效时评测失败，不再默认当作 0 漏项。
- Markdown 报告直接使用实际 `page_listing_hits`，不再把检测到的逐页标题显示成“无”。
- 收紧结构标题匹配，识别 `Slide 1 - Topic`，排除“这不是总结”“例子与应用被当作主题”等假阳性，并让重复模板信号参与机械结构判定。
- 支持仓库外绝对输出路径；CI 安装 GUI 依赖并执行全部测试。
- 新增 `.gitattributes`，已有文本统一为 LF，PowerShell 脚本保留 CRLF，避免全仓库伪 diff。
- 详细说明见 `REVIEW_FIXES.zh-CN.md`。

## 五、待你完成的事

1. 真实课件（文字密集/图表/表格/公式/扫描页/50 页以上）按 `benchmarks/README.md` 加入 manifest——合成样例不能替代真实课件。
2. 用 `spot-check.template.md` 对 lecture 产物做一次人工抽查（需要 API key）。
3. 本机安装 LibreOffice 后 PDF 导出与 PDF 层排版检查才可用（当前会优雅跳过）。
