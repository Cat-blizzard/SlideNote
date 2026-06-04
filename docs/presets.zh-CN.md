# 用户侧 Preset

`--preset` 是普通用户选择工作流的入口。现在只保留两个值：`lecture` 和 `local`。

## Preset 总览

| Preset | 适合场景 | 推荐心智模型 |
| --- | --- | --- |
| `lecture` | 正式学习、长期保存、需要图文理解。 | 像老师重新讲一遍。 |
| `local` | 没有 API key、离线预览、检查解析是否正常。 | 先确认课件能被读出来。 |

## 建议命令

教师讲义：

```powershell
$env:DEEPSEEK_API_KEY="..."
$env:DASHSCOPE_API_KEY="..."
python -m slidenote build lecture.pdf --out outputs\lecture --provider deepseek --export markdown-zip
```

本地预览：

```powershell
python -m slidenote build lecture.pdf --out outputs\local --preset local --export markdown-zip
```

已有输出目录生成复习包：

```powershell
python -m slidenote study-pack outputs\lecture --question-count 12
```

## 背后行为

| Preset | 行为 |
| --- | --- |
| `lecture` | 默认启用 LLM、OCR auto、Vision auto、图文锚定、Deck Brief、Content Guard、Lecture-Weave、teaching enrichment 和本地缓存。 |
| `local` | 强制关闭外部 API 调用，只保留本地解析、本地语义布局、本地图片排序、coverage、source map 和基础 Markdown。 |

`lecture` 是质量优先默认值，不再要求用户手动理解 `note-profile`、`note-depth`、`speed-mode`、缓存、并发、OCR/Vision targets 等底层参数。

## 选择建议

- 第一次跑新文件且没有 API key，用 `--preset local`。
- 正式学习或要分享笔记，用默认 `lecture`，并加 `--export markdown-zip`。
- 图很多、流程图多、截图多的课件，保持 `--vision auto`。
- 只想用文本模型、不想调用视觉模型，用 `--vision off`。
- 复习题和自测题不要放进 build 命令里，使用 `slidenote study-pack <输出目录>`。

旧的 `fast` / `faithful` preset 已经移除。对应迁移方式见 [CONFIG.zh-CN.md](../CONFIG.zh-CN.md)。
