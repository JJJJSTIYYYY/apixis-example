# MyLLM 1.0.0 真实端到端验证

日期：2026-10-17（Asia/Shanghai）。发行包版本：`my-llm-py 1.0.0`。
结果：**本次验证链路全部通过，未发现新的兼容性问题。**

## 执行边界

运行用户更新后的真实 `chat_loop`、单个 AgentRuntime/编译图、Apixis 事件与 shell 工具、
MyLLM 1.0.0 和官方 SDK。终端输入由测试脚本驱动，仅批准精确匹配的
`printf sdk_v1_e2e_17`；命令真实执行一次，退出码 0。

测试在临时进程中替换 Profile，不修改生产预设：

- DeepSeek 国内地址 `https://api.deepseek.com/v1`，Flash 与 V4 Pro。
- Kimi 国内地址 `https://api.moonshot.cn/v1`，K3；仓库 Kimi 默认仍为国际地址。
- 预算直接配置在 ModelProfile.extra_body：DeepSeek 使用 max_tokens，K3 使用
  max_completion_tokens。常规输出上限 512，截断检查为 1 token。
- DeepSeek 按阶段设置 thinking.type=disabled/enabled；开启时原生 reasoning_effort=low。
  K3 只设置原生 reasoning_effort=low，不发送 thinking 开关。
- SDK 重试为 0，超时 45 秒，硬性最多 6 次推理请求；没有模型列表或余额查询。

沿用应用的 `stdio_protocol="openai_chat"` 和每次请求的真实 SDK Client 生命周期。
测试包装公开流接口，仅观察事件中的完成原因、文本长度与 usage，再原样交给应用；
HTTP 层只记录实际发送的模型/模式及历史字段统计，没有模拟响应、拼接工具参数或自建 SSE。
每次调用前后深比较输入消息，确认未原地改写应用历史。

凭证通过隐藏终端输入注入进程环境，未保存到项目、历史数据库、日志或报告。
测试设置和 SQLite 位于独立临时目录；脚本及临时数据结束后删除。

## 请求与消耗

| 请求 | 模型/模式 | 行为 | HTTP | 流 finish_reason | 输入 tokens | 输出 tokens |
| --- | --- | --- | ---: | --- | ---: | ---: |
| 1 | DeepSeek Flash 非思考 | 调用一次固定 printf 工具 | 200 | tool_calls | 495 | 46 |
| 2 | 同模型开启思考 | 从已提交工具结果的失败快照 `/retry` | 200 | stop | 615 | 10 |
| 3 | DeepSeek V4 Pro 思考 | `/model` 切换后正确回复原工具标记 | 200 | stop | 631 | 45 |
| 4 | Kimi K3 | `/provider` 切换后接收完整历史，正确回复原标记 | 200 | stop | 662 | 54 |
| 5 | DeepSeek Flash 非思考 | 1 token 预算触发真实长度截断 | 200 | length | 665 | 1 |
| 6 | Kimi K3 | 保留截断快照，切换供应商后 `/retry`，正确回复 4 | 200 | stop | 747 | 40 |
| 合计 | | | | | **3,815** | **196** |

六个响应均取得原生 usage，总计 **4,011 tokens**。供应商的 reasoning tokens
已包含在 completion_tokens 中，没有重复相加；这不是账单金额。

## 1.0 流与最终消息

- 六次请求均从流事件 `choices[*].finish_reason` 读取完成原因。
- 六次均收到独立用量尾块，`choices=[]`、usage 非空，没有导致显示或结果处理错误。
- 正常完成后的 `stream.result` 为标准 assistant 字典，不包含自定义 finish_reason/usage。
- 截断时 partial_message 同样没有自定义 finish_reason，应用仍能根据此前的流事件
  正确抛 StreamError，界面提供切换模型和 `/retry` 提示。
- 无需在应用中重新拼接消息、重建工具参数或映射各供应商的完成原因。

## 模式切换与快照

请求 1 的非思考工具助手消息没有可见推理。真实 shell 完成并提交结果后，
脚本在发起下一个模型请求之前注入一次本地异常，产生真实失败快照。
这不是供应商故障，不增加 API 请求。

临时把同 ID Profile 的思考开关开启，执行 `/model deepseek-flash` 后确认原失败
context 对象保持不变，再执行 `/retry`。实际请求中该工具助手消息由 MyLLM
编码为 reasoning_content 空字符串，DeepSeek 成功继续；原非思考历史没有被改写。

V4 Pro 的请求同时携带该工具消息和普通助手回复：工具消息补空推理，缺少推理的普通
助手消息没有一律补空字段，符合 1.0 的配置约定。Pro 本次返回 141 字符可见推理。
K3 接收完整对话及已有推理，正确返回原工具标记；该请求没有为旧工具消息补空推理，
本次完整对话仍被服务端接受。不能据此推断所有未完成工具轮或其他模型都接受该组合。

请求 5 在原对话触发 length 后，切换 K3 仍保留同一失败 context 对象与快照；
重试未重复执行工具或添加重复用户消息。

## 历史、检查与限制

- 全程一个 runtime、同一个编译图、一段对话，没有清空或裁剪历史。
- 临时 SQLite 有 10 条消息、四条用户输入，角色依次为：
  `user → assistant → tool → assistant → user → assistant → user → assistant → user → assistant`。
- 截断的 assistant 回复未保存，已有非思考工具消息保持原始形式。
- 已保存助手消息没有额外的 finish_reason 或 usage 字段，已有可见推理按返回消息保存。
- 77 项离线单元测试通过；Ruff 代码/格式、本地 `--check` 和 git diff --check 通过。
- 未修改应用实现或新增兼容分支，仅新增本报告并更新验证说明。

本次在线覆盖 DeepSeek Flash、V4 Pro、Kimi K3；其他上游协议、旧版签名/加密元数据的
跨协议回放、全部思考档位、真实断网及服务端故障没有新增在线验证。
0.1.x 的历史实测记录继续保留，不视为 1.0 的在线验证结果。
