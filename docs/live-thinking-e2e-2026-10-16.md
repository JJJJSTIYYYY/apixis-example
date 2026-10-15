# 真实续测：思考工具历史、Kimi K3 与截断重试

日期：2026-10-16。使用用户授权的凭证，仍通过 DeepSeek 与 Moonshot 国内接口。
本轮共尝试发送 **10 次推理请求**，没有模型列表或余额查询；其中 9 次记录到 HTTP 200
和完整 usage，另 1 次没有记录到响应状态或 usage，不能据此断言其服务端结果。

## 配置与预算

- 实际运行 `chat_loop`、单个 AgentRuntime、Apixis 图、真实工具工作线程、MyLLM 与官方 SDK。
- Kimi：`kimi-k3`，`https://api.moonshot.cn/v1`，`reasoning_effort=low`，
  `ModelProfile.extra_body={"max_completion_tokens": 512, "reasoning_effort": "low"}`。
  不发送 K2.x 的 thinking 开关；[K3 官方说明](https://platform.kimi.ai/docs/guide/kimi-k3-quickstart)。
- DeepSeek：`deepseek-flash`，`ModelProfile.extra_body` 设置 `max_tokens=512`、
  `thinking.type=enabled`、`reasoning_effort=low`。截断检查临时改为 `max_tokens=1`、
  `thinking.type=disabled`；[原生字段说明](https://api-docs.deepseek.com/api/create-chat-completion/)。
- SDK 重试为 0，超时 45 秒。没有使用 `ModelProfile.max_tokens`，没有预算字段转换分支。
- 这些配置仅用于测试；仓库 Kimi 默认地址仍是国际接口，生产预设的预算保持原样。

## 第一组：完成的检查及测试脚本问题

上限 6 次请求，实际尝试 6 次。

| 请求 | 模型 | 检查 | 输入 tokens | 输出 tokens | 结果 |
| --- | --- | --- | ---: | ---: | --- |
| 1 | kimi-k3 | 思考后调用 `printf thinking_k3_7` | 396 | 77 | HTTP 200，tool_calls，可见推理 91 字符 |
| 2 | kimi-k3 | 回传完整自身工具推理及真实工具结果 | 528 | 40 | HTTP 200，正确返回标记，可见推理 94 字符 |
| 3 | deepseek-flash | 接收 K3 历史，思考后调用 `printf thinking_ds_9` | 660 | 59 | HTTP 200，tool_calls，可见推理 75 字符 |
| 4 | kimi-k3 | 从已提交工具结果的快照接收 DeepSeek 推理历史 | 722 | 20 | HTTP 200，正确返回标记；测试断言误判，见下文 |
| 5 | deepseek-flash | 1 token 输出预算 | 未取得 | 未取得 | 未记录响应状态，原因未确认 |
| 6 | kimi-k3 | 上一步失败后重试，回答简单算术 | 580 | 41 | HTTP 200，回复 4 |

第 4 次响应没有可见 reasoning_content，但 usage 报告 reasoning_tokens=1，且最终答案正确。
测试脚本错误地要求每条回复都有非空可见推理，因而在返回应用之前抛出了断言异常。
这不是已确认的模型或 MyLLM 故障；应用本身不要求可见推理非空。
该断言导致本组后续状态不再符合完整预期，因此**不把本组记为整体通过**。
第 5 次的诊断记录不足，不能把它记为已验证的长度截断，也不猜测是超时或服务端错误。

第 3 次工具完成后注入的异常发生在模型调用前，没有增加 API 请求；它用于产生可恢复
快照，不是供应商真实故障。本组两个真实工具各执行一次。

## 第二组：修正测试断言后，完整通过

补测上限 4 次，实际 4 次，全部 HTTP 200。

| 请求 | 模型 | 检查 | 输入 tokens | 输出 tokens | 结果 |
| --- | --- | --- | ---: | ---: | --- |
| 1 | deepseek-flash | 思考后调用 `printf thinking_ds_9` | 517 | 216 | tool_calls，可见推理 705 字符 |
| 2 | kimi-k3 | 工具提交后本地注入失败，切换并 `/retry` | 667 | 32 | 接收完整推理及工具结果，正确回复标记 |
| 3 | deepseek-flash | 1 token 上限，简单算术 | 596 | 1 | 原生 finish_reason=length，应用抛 StreamError |
| 4 | kimi-k3 | 保留失败快照，切换后 `/retry` | 718 | 41 | 正常回复 4 |

断言全部通过：

- 一个 runtime 和编译图贯穿测试；`/provider` 后原失败 context 对象保持不变。
- 每次请求发送前检查工具调用助手消息仍含完整 reasoning_content；未手写历史转换。
- 唯一允许的 shell 命令真实执行一次；恢复未重复执行工具。
- 长度截断消息未写入历史；重试没有重复用户消息。
- 临时 SQLite 只有一段对话、六条消息：
  `user → assistant → tool → assistant → user → assistant`。
- 保存再读取的工具调用消息仍含完整推理内容。

## 消耗与修复

第二组 usage 合计：输入 2,498、输出 290，共 2,788 tokens。
两组 **9 个具有完整 usage 的响应**合计输入 5,384、输出 527，共 **5,911 tokens**；
此数不包括第一组第 5 个未取得 usage 的请求，因此不是本轮完整计费量或账单金额。
各供应商的 reasoning_tokens 已包含在各自 completion_tokens 中，未重复相加。

发现并修正两处应用文案/文档问题：

1. 长度上限提示原先只要求调整预算后重新启动；现在提示可切换预算充足的模型，
   并在存在失败快照时提供 `/retry`。
2. 文档原先笼统写 Kimi 使用 max_tokens；现明确 K3 的官方原生字段是
   max_completion_tokens，仍直接配置在 ModelProfile.extra_body。

没有新增协议、流解析或历史兼容实现。凭证仅通过终端隐藏输入注入进程环境；
临时数据库、设置目录和脚本已删除，没有把凭证或完整思考文本保存到报告。
未继续付费探测第一组未知响应，也未验证真实断网、全部推理档位或其他供应商。
