# 真实验证：非思考 → 思考 → 切换模型与供应商

后续状态：以下为 0.1.3 的历史记录；其中的 DeepSeek HTTP 400 已在 MyLLM 0.1.4
[真实复测](live-retest-0.1.4-2026-10-16.md)中恢复成功。

日期：2026-10-16。使用 `my-llm-py 0.1.3`、OpenAI SDK `2.54.0`、Apixis `1.1.1`。
结果：**完成非思考回复后再切换的链路通过；未完成的非思考工具轮直接切换 DeepSeek
思考模式时出现 HTTP 400，随后保留快照切换 Kimi K3 恢复成功。**

## 测试配置与边界

运行真实 `chat_loop`、单个 AgentRuntime/编译图、Apixis 事件、shell 工具、MyLLM 和官方 SDK。
终端输入由脚本驱动，仅自动批准 `printf mode_mix_47` 和 `printf pending_mix_83`。
两条命令各执行一次，实际退出码均为 0。

- DeepSeek 地址：`https://api.deepseek.com/v1`，模型 `deepseek-flash`、`deepseek-v4-pro`。
- Kimi 国内地址：`https://api.moonshot.cn/v1`，模型 `kimi-k3`。
- 输出上限全部为 512 tokens；DeepSeek 直接配置 `ModelProfile.extra_body.max_tokens`，
  K3 直接配置 `ModelProfile.extra_body.max_completion_tokens`。
- DeepSeek 非思考模式设置 `thinking.type=disabled`；思考模式设置 `thinking.type=enabled`
  和 `reasoning_effort=low`。K3 设置 `reasoning_effort=low`，不传 thinking 开关。
- 当前应用没有思考模式切换命令；本次在进程内临时替换同 ID ModelProfile 的 extra_body，
  然后执行 `/model deepseek-flash`。供应商和模型切换均执行实际 `/provider`、`/model` 命令。
  未加入新的思考命令，未修改仓库预设或用环境变量选择模型。
- 超时 45 秒、SDK 自动重试 0、硬性上限 7 次推理请求。没有查询模型列表或余额。
- 全程同一段对话，没有 `/new`、`/history`、清空历史或手写消息兼容转换。

## 请求与结果

| 请求 | 模型与模式 | 行为 | HTTP | 输入 tokens | 输出 tokens |
| --- | --- | --- | ---: | ---: | ---: |
| 1 | DeepSeek Flash 非思考 | 记住并回复 `mode_mix_47`，不使用工具 | 200 | 482 | 5 |
| 2 | 同一 Flash 开启思考 | 接收非思考历史，调用 `printf mode_mix_47` | 200 | 548 | 71 |
| 3 | `/model deepseek-v4-pro`，思考 | 从失败快照接续 Flash 的思考工具结果 | 200 | 649 | 6 |
| 4 | `/provider kimi kimi-k3` | 接收完整混合历史，正确回复原标记 | 200 | 638 | 73 |
| 5 | 切回 Flash 非思考 | 调用 `printf pending_mix_83`，返回消息不含思考内容 | 200 | 664 | 42 |
| 6 | 同一 Flash 开启思考，`/retry` | 原样回放尚未完成的非思考工具轮 | **400** | 未返回 | 未返回 |
| 7 | `/provider kimi kimi-k3`，`/retry` | 使用同一失败快照接收上述工具结果 | 200 | 815 | 20 |
| 有 usage 的 6 个响应合计 | | | | **3,796** | **217** |

已返回的原生 usage 合计 **4,013 tokens**，reasoning tokens 已包含在输出统计中。
HTTP 400 没有返回 usage，未将其按 0 tokens 计费推断；统计不是账单金额。

## 通过的链路

请求 2 的助手工具消息返回了 123 字符可见推理。真实工具执行并提交结果后，脚本在
发起下一轮 API 请求之前注入一次本地异常，产生可恢复快照。这不是供应商故障，
不消耗额外 API 请求。

随后执行 `/model deepseek-v4-pro`，确认失败 context 仍是原对象，再执行 `/retry`。
Pro 接受 Flash 完整思考工具消息及工具结果，正确返回标记，工具没有重跑。
再通过 `/provider kimi kimi-k3` 继续同一对话，K3 仍能正确回复最初的标记。
Pro 请求中的 thinking/effort 参数已检查；其本次结果报告 0 个 reasoning tokens，
不将“已开启思考参数”等同于“每条回复一定有可见思考”。

## 已复现的兼容性限制

请求 5 在非思考模式产生了新的工具调用，助手消息没有 reasoning_content。
真实工具完成后，再次在后续模型请求前注入本地失败，保留待完成工具轮快照。

只将同 ID Profile 改为思考模式并 `/retry`，请求 6 原样携带这条工具消息和工具结果。
DeepSeek 返回 HTTP 400，错误正文为：

> The `reasoning_content` in the thinking mode must be passed back to the API.

该请求中两条历史助手工具消息只有一条含非空推理：旧的思考工具调用有推理，
当前未完成轮的非思考工具调用没有推理。原始响应本就没有这段内容，
不是应用在切换时清空了它。当前 MyLLM 没有自动补齐或绕过此限制；
**因此不能宣称非思考工具轮可以在任意位置直接切入 DeepSeek 思考模式。**

保留失败 context 和全部消息，执行 `/provider kimi kimi-k3` 后再 `/retry`，
请求 7 返回 HTTP 200、正确回复 `pending_mix_83`。同样缺少推理的工具调用消息
被原样回放，没有补造 reasoning_content，也没有重新执行工具。
这是本次特定模型、历史与服务返回的实测结果，不代表全部模型都接受此组合。

## 状态与本地回归检查

- 所有切换均保留原 runtime 和编译图；失败切换时保留原 context 对象及快照。
- 保存到临时 SQLite 的唯一对话有 12 条消息，角色依次为：
  `user → assistant → user → assistant → tool → assistant → user → assistant → user → assistant → tool → assistant`。
- 四条用户输入各保存一次；两个工具各执行一次。
- 非思考助手消息没有被补造推理；原思考工具消息的完整推理保留到数据库读取结果。
- 新增离线回归：真实 SDK 配合 MockTransport 复现非思考工具轮收到 400，
  再切换 K3 恢复快照，验证 SDK 异常传播、历史与工具结果保留、工具仅执行一次。
  模拟传输用于本地回归，不用于替代上述在线证据。
- 71 项单元测试全部通过；Ruff 代码/格式检查和 `git diff --check` 通过。

未修改应用协议实现、历史适配逻辑或 MyLLM 源码。未追加付费请求探索绕过方式。
国内 Kimi 地址和预算/思考配置仅对测试生效；仓库 Kimi 预设仍为国际地址。
凭证由终端隐藏输入注入环境，未落盘；临时脚本、设置和数据库在结束后删除。
