# Example · Apixis Agent

一个可以日常使用的终端智能助手，同时展示 Apixis 的图调度、事件和恢复能力。支持 DeepSeek 流式对话、逐次审批终端命令，以及自动保存和继续历史对话。项目、Python 包和命令均为 `example`。

## 启动

需要 uv，项目使用 Python 3.14。uv 会根据 `.python-version` 准备解释器。

```bash
uv sync --locked
# Enter your own key for this shell session; no credential file is required.
read -r -s DEEPSEEK_API_KEY
export DEEPSEEK_API_KEY
uv run example
```

也可自行在当前进程环境中设置 `DEEPSEEK_API_KEY`。不要把真实 Key 放入源码或配置文件。

| 环境变量 | 默认值 | 作用 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | 无 | API 凭证，仅从进程环境读取 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | OpenAI 兼容 API 地址 |
| `DEEPSEEK_MODEL` | `deepseek-chat` | 模型名 |
| `DEEPSEEK_MAX_TOKENS` | 不额外设置 | 每次请求的输出 token 上限，必须为正整数 |
| `EXAMPLE_HOME` | `~/.example` | 设置和 SQLite 历史所在目录 |

项目目录里的 `config.yaml` 控制 Apixis 日志。Apixis 从**运行命令时的工作目录**加载该文件。可添加 `PIPELINE.event_loop_backpressure` 和 `PIPELINE.event_pipe_max_len` 调整容量；Apixis 1.1.0 的背压下限为 128。

## 使用

首次启动填写显示名称，之后直接输入问题。例如：“帮我解释这段报错”或“查看当前目录，说明项目结构”。回复完成后会自动保存；输入 `/history` 可以选择已有对话继续聊天。

需要执行工具时，界面会先展示完整命令和工作目录，再逐个请求审批。回车或 `y` 批准，Esc 或 `n` 拒绝。命令守卫会直接拒绝匹配到的危险命令。执行结果按实际退出码区分成功和失败；拒绝会显示“工具已跳过”。

| 命令 | 行为 |
| --- | --- |
| `/help` | 显示全部命令及工具审批操作说明 |
| `/history` | 选择并继续当前用户名的历史对话 |
| `/new` | 开始新对话，清除当前重试记录 |
| `/rename`、`/delete` | 重命名或删除当前对话 |
| `/output` | 查看最近一次工具的完整输出 |
| `/transcript` | 查看当前对话的完整记录 |
| `/retry` | 从最近一次失败运行的最新快照恢复 |
| `quit`、`exit`、`q`、Ctrl-C | 退出 |

长工具输出先显示摘要，输入 `/output` 查看全文。打开历史时先预览最近 3 轮，输入 `/transcript` 查看完整对话；模型仍会使用完整历史。在终端中长记录使用分页器，通常按 `q` 返回；输出重定向时直接打印全文。历史选择输错序号会提示重新输入，回车返回当前对话。

下面两条命令用于了解 Apixis 的运行情况，不需要在普通聊天中使用：

| 命令 | 行为 |
| --- | --- |
| `/checkpoints` | 查看最近运行的状态、快照版本和恢复目标 |
| `/stats` | 查看当前会话的运行事件和工具事件计数 |

每轮开始时，工作目录子图并发采集少量目录名称与运行环境，再合并为临时上下文。不会读取文件内容或环境变量，也不会额外请求 LLM。临时环境信息不写入对话历史。

工具以独立事件请求执行，通过唯一回复事件等待结果。审批不设超时；shell 命令执行上限为 30 秒，事件结果等待上限为 40 秒，LLM 节点上限为 130 秒。主图默认最多 32 个节点步骤，每执行一个工具占用一个步骤。

### 快照与重试

快照由 Apixis 在节点执行前自动保存。`/retry` 恢复的是新 context，不会重新使用已经消耗的失败 context。

- 工具结果已提交、后续 LLM 请求失败：恢复到 LLM 节点，已提交的工具不会重跑。
- 工具节点自身失败：恢复可能重新执行该工具，仍需再次审批。节点快照不保证外部命令的 exactly-once 执行。
- 达到最大步数的运行应开始新一轮；`/retry` 保留快照的步数，不增加额度。
- 输入被过滤的运行不会调用 LLM，也不写入历史。
  插件通过 `GraphContext.stream_writer()` 发送 `type="error"` 的消息（包含 `message` 和 `hint`），再用 `event.accept()` 中止调度；调用方直接消费流，无需读取 state 中的额外标记。
- 快照只属于当前进程中的编译图，**不支持退出后恢复**。SQLite 保存的是完成的对话，不是图运行快照。
- 切换、删除或新建对话后，旧失败运行不再可重试。

### 单次运行与本地检查

```bash
# No API key and no paid request; execute the concurrent workspace subgraph.
uv run example --check

# One turn, streamed to stdout, without writing settings or conversation history.
# Tools are rejected by default in this mode.
DEEPSEEK_MAX_TOKENS=24 uv run example --prompt '不要调用工具。只回复 OK。'

# Explicitly approve guarded tools for this one prompt.
uv run example --prompt '执行 printf hello，然后说明结果' --approve-tools

# Adjust the step budget for one-shot mode.
uv run example --prompt '你好' --max-steps 16
```

单次模式也经过输入过滤和命令守卫。超小的 token 上限主要用于纯文本冒烟测试，工具调用可能需要更大的输出预算。

## 结构

```text
src/example/
  __init__.py          CLI 参数与启动/关闭
  app.py               交互、单次运行、历史保存
  commands.py          原有历史命令
  core/
    runtime.py         会话资源、context 生命周期与快照恢复
    agent.py           prepare → workspace → agent ↔ tools
    workspace.py       并发子图：directory + environment → summary
    events.py          工具请求/回复、自定义运行事件、后台计数
    llm.py             SSE 拼接、HTTP 客户端与输出预算
    tools.py           工具注册与 shell 执行
    history.py         SQLite 历史
    settings.py        用户名与保存路径
  plugins/             输入过滤和命令守卫
  ui/                  终端输入、流式 Markdown 与组件
```

所有工具由应用提供，未启用 Provider 原生搜索或工具。输入过滤和命令守卫是简单模式检查，示例没有实现 shell 沙箱；交互模式保留逐次审批。工具完整输出交给模型，终端折叠显示并可查看全文。

详细的接口覆盖与取舍见 [docs/apixis-coverage.md](docs/apixis-coverage.md)。界面检查与改进见 [docs/ui-review.md](docs/ui-review.md)，验证结果见 [docs/validation.md](docs/validation.md)。

## 本地测试

```bash
uv run python -m unittest discover -s tests -v
```

测试不需要 API Key，也不会访问付费接口。覆盖真实 Apixis 图与事件流程、审批顺序、异常、恢复、中止、流关闭、历史去重、SSE，以及窄窗口、长输出和历史交互；另在独立进程中验证背压 128、队列 8 下的 160 路并发。
