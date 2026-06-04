# SlideNote 文档中心

README 只保留项目首页需要的信息：它是什么、怎么跑、为什么值得做。更细的机制、参数和路线图说明放在这里。

## 推荐阅读顺序

1. 新用户先看 [README.zh-CN.md](../README.zh-CN.md) 的快速开始和 preset。
2. 想理解整体结构，看 [Pipeline](pipeline.zh-CN.md)。
3. 想选择运行方式，看 [Presets](presets.zh-CN.md)。
4. 想快速查命令或完整参数，看 [CONFIG 配置指南与完整参考](../CONFIG.zh-CN.md)。
5. 想调质量和成本，看 [Provider、OCR、Vision、缓存与成本](providers-and-cost.zh-CN.md)。
6. 想理解可追溯结构，看 [Element IR 与 Source Map](ir-and-source-map.zh-CN.md)。
7. 想看覆盖率、复习包和考试包，看 [Quality And Guard](quality-and-guard.zh-CN.md)。
8. 想看后续方向，看 [ROADMAP.zh-CN.md](../ROADMAP.zh-CN.md) 和 [路线图设计笔记](roadmap/extension-notes.zh-CN.md)。

## 文档列表

| 文档 | 说明 |
| --- | --- |
| [Pipeline](pipeline.zh-CN.md) | 五阶段产品流水线：Ingest、Understand、Write、Guard、Export。 |
| [Presets](presets.zh-CN.md) | 用户侧 `--preset fast|faithful|lecture` 的定位和映射。 |
| [Quality And Guard](quality-and-guard.zh-CN.md) | coverage、content guard、quality report、review/exam 学习包。 |
| [Element IR And Source Map](ir-and-source-map.zh-CN.md) | `content.json`、`element_ir.json`、`source_map.json` 和图片资产。 |
| [Providers And Cost](providers-and-cost.zh-CN.md) | LLM provider、OCR、Vision、缓存、并发和导出依赖。 |
| [Claude Backend](claude-backend.zh-CN.md) | `experiment/claude-backend` 分支的 agent-pack / agent-build / agent-eval 实验说明。 |
| [Roadmap Design Notes](roadmap/extension-notes.zh-CN.md) | 从旧 Roadmap 迁出的模块设计笔记。 |
| [CONFIG.zh-CN.md](../CONFIG.zh-CN.md) | 配置指南与完整参数参考；先按场景选命令，再查高级参数。 |
| [GUI 文档](../gui/README_GUI.zh-CN.md) | SlideNote Studio 的安装、运行和界面说明。 |

## 维护原则

- README 应该像项目首页，不要再次长成参数手册。
- 详细设计优先放进 `docs/`，README 只保留一句话解释和链接。
- 愿景、起源、致谢、项目动机保留在 README，因为它们不是噪音，而是项目气质。
- ROADMAP 负责方向和优先级，长设计说明放到 `docs/roadmap/`。
