# DeepSeek 后端与 Harness 适配

实验分支：`experiment/dsh-backend`。SlideNote 负责解析、资产复制、coverage、
source map、合并和文件写入，写作步骤可选择两个运行方式：

| 后端 | 执行方式 | 配置 |
| --- | --- | --- |
| `--backend harness` | 启动真实 DeepSeek Harness Headless，每节独立会话 | `--harness-*`；Harness 自己的 profile、patch 和凭据 |
| `--backend api` | 通过 `slidenote.llm` 直接请求模型 | `--dsh-*`；默认 DeepSeek |
| `--backend dsh` | 历史 API 后端名称，与 `api` 相同，仍为默认值 | 保持旧命令兼容 |

## Harness 版本与运行方式

适配版本固定为官方发布的 **0.1.6-alpha.2**：
[release dsh-v0.1.6-alpha.2](https://github.com/deepseek-ai/deepseek-harness/releases/tag/dsh-v0.1.6-alpha.2)。
Harness 仍处于 alpha 阶段；启动前校验版本，版本不符时明确失败，避免静默误读事件。
更新 Harness 后应重新执行协议测试再变更支持版本。

使用已安装的 `dsh`：

```powershell
python -m slidenote agent-build lecture.pdf --out outputs/harness --backend harness
python -m slidenote agent-eval lecture.pdf --out outputs/harness-eval --backend harness
```

使用 D 盘已有源码仓库时，先按 Harness 仓库说明更新依赖并构建，再显式指定入口：

```powershell
python -m slidenote agent-build lecture.pdf --out outputs/harness --backend harness --harness-command node D:/deepseek-harness/apps/cli/lib/bin.js
```

`--harness-command` 接受可执行文件及固定参数列表，路径有空格时加引号。
脚本入口应使用绝对路径，因为子进程的工作目录是 agent pack。
不会经 shell 拼接执行；不要传入一整条带管道、重定向或 PowerShell 表达式的命令。
固定参数含 `--flag` 时，可按顺序重复使用 `--harness-arg=...`，例如
`--harness-command node --harness-arg=--max-old-space-size=4096 --harness-arg=D:/deepseek-harness/apps/cli/lib/bin.js`。
若使用自定义包装器，包装器也必须支持 `--version` 和下面的 Headless 协议。

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `--harness-command` | `dsh` | 可执行文件与固定参数 |
| `--harness-arg` | 无 | 按顺序追加固定参数，可重复；以 `-` 开头时用等号赋值 |
| `--harness-profile` | `headless` | 必须启用 Headless app 的 profile |
| `--harness-patch` | 无 | 配置补丁路径，可重复指定 |
| `--harness-home` | 沿用现有环境 | 为子进程设置独立 `DSH_HOME` |
| `--harness-timeout` | 600 秒 | 每节进程的总时限，包含工具调用 |
| `--harness-concurrency` | 1 | 首轮独立会话并发数；repair 仍串行 |

模型、API key、Messages 协议、Files API 和工具权限由 Harness 本身配置。
`--dsh-model`、`--dsh-api-key`、`--dsh-cache` 等只影响 API 后端，
不会覆盖 Harness 配置。适配器不放宽权限；Headless 环境需要预先配置可用的权限规则。
Harness 会话中只要求按需读取 pack 内的素材，生成结果由 SlideNote 写盘；这项提示约束不替代权限配置。

### Windows PowerShell 工具的宿主问题

若本机 PowerShell 7 因 CET/运行时错误无法启动，而 Windows PowerShell 5.1 可用，
可保存下面的可选 Harness patch，再通过 `--harness-patch <绝对路径>` 加载：

```yaml
- id: pwsh-sandbox
  config:
    pwshPath: C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe
```

这是已验证的本机工具兼容回退；5.1 与 7 的语法/模块能力不同。
适配器不会自动修改用户默认 shell 或放宽工具权限。普通版本与协议检查不需要这个补丁。

### Headless 协议

适配器使用 `--profile <profile> [--patch <file> ...] --json -`，通过 stdin 传入任务，
以 agent pack 目录为工作目录。每节及 repair 都创建独立会话，不复用其它节的上下文。

stdout 是 NDJSON 事件流，并不是笔记 JSON。适配器验证 session、成功的 turn_end、
终结 final 事件及进程退出码；只将 `final.text` 作为 SlideNote JSON 解析。
thinking、流式文本和工具结果不会混入笔记。超时、非零退出、事件损坏或无最终结果都会失败。
原始 stderr、思考及工具正文不写入诊断文件。

`agent_run.json` 的 `sections[].harness` 记录版本、会话 ID、耗时、事件/工具调用数、usage。
Harness 后端没有 SlideNote API 缓存；会话状态和运行记录由 Harness 管理。

## Agent Pack 与输出契约

`agent-pack` 复用确定性管线，默认离线解析（`--vision off --ocr off`），产出：

```text
agent_pack/
  manifest.json
  style.md
  skill.md
  sections/section_001.md
  assets/{screenshots,images,figures}/
```

每节最终返回一个 JSON 对象：

```json
{
  "markdown": "## Section ...",
  "used_asset_paths": ["assets/images/example.png"],
  "covered_source_ids": ["s1_t1", "s1_img1"],
  "warnings": []
}
```

SlideNote 验证字段、资产路径和 source marker，并重新计算 coverage。
trace coverage 包含根据模型 `covered_source_ids` 补全的合法隐藏标记，不能单独证明正文完整；
还必须检查 visible/required-visible coverage 与实际内容。pack 每页保留最多 12 个文本块、每块 200 字符，
OCR/视觉摘要也设上限；source ID 列表保持全量以支持修复。

## API 后端

`--backend api`（或历史别名 `dsh`）使用 `slidenote.llm.LLMClient`。
通过 `--dsh-provider/--dsh-model/--dsh-api-key/--dsh-base-url` 配置；
默认使用 `DEEPSEEK_API_KEY`。首轮并发默认为 3（`--dsh-concurrency`），repair 串行。
`--dsh-timeout` 现在传入底层 SDK/HTTP 请求，按每次请求计时；重试会使总耗时更长。

默认缓存目录 `<out>/.dsh_cache`，可用 `--dsh-cache-dir` 更改、`--dsh-cache off` 关闭。
`agent_run.json` 在 `sections[].api` 或 `sections[].dsh` 记录模型、usage、缓存状态。
传输失败会生成简明诊断，不保存可能包含凭据的 SDK 原始错误。

## Repair 与验收

`--repair auto --repair-rounds 1`：首次生成、合并并分析 coverage，
再按 section 修复 trace_missing / required_visible_missing / figure_missing /
figure_unexplained / figure_needs_review。修复失败时保留首版，记录 failed_repairs；
成功重跑会清除旧的 `agent_diagnostics.json`。

`agent-run` 运行现有 pack；`agent-build` 执行 pack + run；
`agent-eval` 对比本地 baseline 与所选后端，产出 `eval_report.json/.md`。
三种命令共享全部后端选项。验收应检查：

- `coverage.json/.md`：trace 与 visible 缺失、图片缺失和未解释项。
- `agent_run.json`：实际 backend、Harness 版本、warnings、repair 结果。
- `source_map.json` 与 `notes.md`：来源可追溯、资产存在。
- `eval_report.md`：比较 coverage 与笔记规模；协议测试通过不等于笔记质量提升。

配套 [slidenote-agent skill](.agents/skills/slidenote-agent/SKILL.md) 可驱动整条流程。
当 SlideNote 已启动一个 Harness 写作会话时，该会话应只返回 JSON，避免再次运行整条 pipeline。
