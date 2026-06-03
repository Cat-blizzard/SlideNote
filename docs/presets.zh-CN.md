# 用户侧 Preset

`--preset` 是用户侧完整工作流入口。它和 `--note-profile` 不是同一个概念：

- `--preset` 控制整条 pipeline：理解、写作、质检、导出倾向。
- `--note-profile` 只控制 Write 阶段的写作路线。

如果用户显式传入底层参数，显式参数优先于 preset 默认值。

## Preset 总览

| Preset | 适合场景 | 推荐心智模型 |
| --- | --- | --- |
| `fast` | 快速草稿、低成本试跑、本地优先。 | 先知道课件大概有什么。 |
| `faithful` | 严肃资料、保真覆盖、来源追踪。 | 先不漏，再可读。 |
| `lecture` | 高质量详细学习笔记。 | 像老师重新讲一遍。 |

## 建议命令

快速草稿：

```powershell
python -m slidenote build lecture.pdf --out outputs\fast --preset fast
```

保真笔记：

```powershell
$env:DEEPSEEK_API_KEY="..."
python -m slidenote build lecture.pdf --out outputs\faithful --preset faithful --use-llm --provider deepseek
```

教师讲义：

```powershell
$env:DASHSCOPE_API_KEY="..."
$env:DEEPSEEK_API_KEY="..."
python -m slidenote build lecture.pdf --out outputs\lecture --preset lecture --use-llm --provider deepseek
```

## 背后映射

| Preset | 背后倾向 |
| --- | --- |
| `fast` | 使用快速 speed mode，减少额外 Deck Brief / Content Guard / teaching pass，适合低成本试跑。 |
| `faithful` | 使用 Lecture-Weave、section context、Deck Brief auto、Content Guard auto，优先覆盖率和来源追踪。 |
| `lecture` | 映射到 `--note-profile lecture-notes`，启用 Lecture-Weave、Deck Brief auto、Content Guard auto、teaching enrichment auto。 |

`lecture` 在用户没有显式指定深度时，会倾向于 `--note-depth very-detailed`。它不会让 coverage 决定正文形状，而是把 coverage 留到最后做质检和局部修补。

## 和底层参数的关系

常见覆盖方式：

```powershell
python -m slidenote build lecture.pdf `
  --preset lecture `
  --use-llm `
  --provider deepseek `
  --vision off
```

这里仍然走 `lecture` 的写作和质检路线，但显式关闭视觉解析。

```powershell
python -m slidenote build lecture.pdf `
  --preset faithful `
  --use-llm `
  --provider deepseek `
  --content-guard off
```

这里保留 faithful 的大部分行为，但关闭 Content Guard。

## 选择建议

- 第一次跑新文件，先用 `fast` 看解析是否正常。
- 需要交作业、复习或长期保存，用 `faithful`。
- 想生成可以直接阅读学习的讲义，用 `lecture`。
- 对扫描版、流程图、医学图、网络拓扑、数学公式截图很多的课件，优先给 `lecture` 搭配 `--vision auto`，必要时再局部刷新。
