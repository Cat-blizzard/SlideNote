# Claude Code Backend 实验说明

这份文档只适用于 `experiment/claude-backend` 分支。它记录 SlideNote 正在验证的一条旁路：让 Claude Code 接管高层讲义写作和 coverage repair，同时保留 SlideNote 在解析、来源追踪、资产管理和落盘上的确定性。

## 分工原则

SlideNote 负责：

- 解析 PPT/PDF。
- 生成 `content.json`、`element_ir.json`、`source_map.json`。
- 管理图片资产、截图和 source id。
- 生成 agent pack。
- 校验 Claude 输出中的图片路径和来源标记。
- 写出 notes、coverage、diagnostics 和 eval report。

Claude Code 负责：

- 根据 section agent pack 写讲义。
- 决定图文组织和讲解顺序。
- 根据 coverage missing / required visible missing 做 section repair。
- 通过 stdout 返回 JSON。

Claude 不直接写文件，不直接改仓库，也不读取整个项目目录。

## 命令入口

只生成 agent pack：

```powershell
python -m slidenote agent-pack lecture.pdf --out outputs\agent-pack
```

运行已有 agent pack：

```powershell
python -m slidenote agent-run outputs\agent-pack\agent_pack --out outputs\agent-run
```

端到端生成 Claude 版笔记：

```powershell
python -m slidenote agent-build lecture.pdf --out outputs\agent-build
```

对比稳定 build 和 Claude backend：

```powershell
python -m slidenote agent-eval lecture.pdf --out outputs\agent-eval
```

## Claude CLI 要求

默认调用：

```text
claude -p --bare --output-format json
```

如果 `claude` 不在 PATH，可以传：

```powershell
--claude-command path\to\claude.exe
```

可选指定模型：

```powershell
--claude-model sonnet
```

Claude Code 的登录和认证由本机环境负责，SlideNote 不管理 Claude 账号状态。

## 产物

常见输出：

```text
agent_pack/
agent_pack_report.json
agent_run.json
agent_diagnostics.json
notes.md
coverage.json
coverage.md
source_map.json
eval_report.json
eval_report.md
```

`agent-eval` 会同时生成 baseline 和 agent build，方便比较：

```text
baseline_build/
agent_build/
eval_report.json
eval_report.md
```

## 当前实验重点

- 先用真实小课件建立 baseline vs Claude backend 样例。
- 用 coverage、required visible coverage 和 figure audit 判断 Claude 输出是否真的更好。
- 改进 repair prompt，让 Claude 更稳定地返回整节 revised markdown。
- 保留旧 build 作为 baseline，不急于让 `slidenote build` 变成 Claude-first。
- 记录 Claude 调用次数、repair 触发原因和失败诊断，方便估算成本和稳定性。

## 风险边界

- Claude 输出好看但漏内容，仍然是失败。
- Claude 不能直接写文件，否则 source map、coverage 和 GUI 审阅会失去确定性。
- Agent pack 要继续压缩信息密度，避免把无关字段都塞给模型。
- 如果 Claude backend 在多个真实课件上稳定胜出，再考虑把它作为可选 note strategy 或实验入口别名。
