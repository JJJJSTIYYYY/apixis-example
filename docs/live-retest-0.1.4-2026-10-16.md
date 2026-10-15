# MyLLM 0.1.4：完成原因对齐与失败场景复测

日期：2026-10-16。已安装 `my-llm-py 0.1.4`，项目依赖和锁文件均为该版本约束。
结果：**此前 DeepSeek 非思考工具轮直接切入思考模式的 HTTP 400 已不再复现，
同模型 `/retry` 成功；长度截断后切换 Kimi K3 重试也成功。**

## 对齐方式

依据 MyLLM 本地 `docs/reference/api.md`、`configuration.md`，完整 Message 和最终
MessageChunk 均使用统一 finish_reason。应用只消费结果，不转换协议完成原因：

| 情况 | 统一值 | 项目处理 |
| --- | --- | --- |
| 正常结束 | stop | 保留结果 |
| 长度截断 | length | 抛 StreamError，保留 partial_message 和失败快照，不保存截断回复 |
| 完整工具调用 | tool_calls | 保留完整工具消息，由现有图执行工具 |
| 可映射的内容过滤 | content_filter | 保留库返回值，未增加新策略 |
| 特殊原因 | 例如 pause_turn、未知值 | 原样保留，不另建映射 |

删除 `core/llm.py` 中对原生 `max_tokens` 完成原因的判断和 lower() 处理，
不在应用中重复实现 MyLLM 的归一化。
Responses 不完整/失败响应仍由库抛出异常；未将它们改为正常完成。

MyLLM 0.1.4 默认对 Chat 请求中每条 assistant 消息回传 reasoning_content，
原本缺失时在请求编码阶段补空字符串。应用仍原样提交自己的消息，
没有补字段、裁剪历史、伪造推理或改写 SQLite。

## 真实执行与请求

使用用户已授权的凭证，终端隐藏输入，仅注入进程环境。运行真实 chat_loop、
单个 AgentRuntime/编译图、Apixis 图、事件、真实 shell 工具、MyLLM 和官方 SDK。
测试脚本驱动 UI 输入，只批准精确匹配的 `printf replay_fixed_14`。

| 请求 | 接口/模型 | 行为 | HTTP | 完成原因 | 输入 tokens | 输出 tokens |
| --- | --- | --- | ---: | --- | ---: | ---: |
| 1 | DeepSeek / deepseek-flash | 非思考模式，调用真实 printf 工具 | 200 | tool_calls | 491 | 42 |
| 2 | 同一 DeepSeek 模型 | 开启思考，从原失败快照 `/retry` | 200 | stop | 600 | 7 |
| 3 | 同一 DeepSeek 模型 | 非思考、1 token 输出预算，触发真实截断 | 200 | length | 597 | 1 |
| 4 | Kimi / kimi-k3 | 切换供应商，从长度截断快照 `/retry` | 200 | stop | 555 | 48 |
| 合计 | | | | | **2,243** | **98** |

四次推理请求原生 usage 合计 **2,341 tokens**，包含供应商计入 completion_tokens
的 reasoning tokens；不是账单金额。没有模型列表/余额查询，也没有 SDK 自动重试。

临时 Profile 的 DeepSeek 地址为 `https://api.deepseek.com/v1`，Kimi 地址为
`https://api.moonshot.cn/v1`。超时 45 秒，重试 0；请求 1/2/4 的输出上限为 512。
DeepSeek 预算直接使用 ModelProfile.extra_body.max_tokens，K3 使用
ModelProfile.extra_body.max_completion_tokens。思考开关和原生 effort 也仅临时配置在
Profile 中；没有新增命令或修改生产预设。

## 原失败场景的复测证据

1. 非思考响应返回工具调用，不含可见 reasoning_content；真实工具仅执行一次，退出码 0。
2. 工具结果提交后、下一次网络请求前，注入一次本地异常，保留真实图快照。
   这是为了复现原重试位置，不是供应商故障，不产生额外 API 请求。
3. 临时替换同 ID ModelProfile，开启 thinking 并设原生 reasoning_effort=low。
   经过 `/model deepseek-flash` 后，失败 context 对象仍相同。
4. `/retry` 的实际请求中，原非思考工具助手消息带 `reasoning_content=""`，
   system/user/tool 消息没有被补该字段；DeepSeek 返回 200，正确回复工具输出标记。
5. 此次回复没有可见推理，usage 报告 reasoning_tokens=0，这是合法结果；
   不再用“开启思考必须返回非空推理”作为测试断言。
6. 后续请求 3、4 的两条历史助手消息都在请求中补空推理，覆盖无工具调用的助手消息。
   每次调用前后对原应用消息做深比较，确认 MyLLM 未原地改写输入历史。

相比 [0.1.3 的失败记录](live-mode-switch-e2e-2026-10-16.md)，这次无需切换 K3 来绕过
缺少 reasoning_content 的 400，DeepSeek 本身即可继续。

## 长度截断与状态

请求 3 原生返回 length，应用按统一值抛 StreamError；切换 `/provider kimi kimi-k3`
后仍保留同一失败 context，`/retry` 正常回复算术答案 4。

临时 SQLite 只保存一段对话、六条消息：
`user → assistant → tool → assistant → user → assistant`。
保存的助手 finish_reason 依次为 `tool_calls → stop → stop`，没有 length 结果，
没有重复用户消息或工具执行。非思考工具助手消息仍保持原始形式，未补造持久化推理。

## 离线验证与限制

73 项单元测试全部通过，Ruff 代码/格式检查及 git diff --check 通过。
真实 SDK 配合模拟传输覆盖：

- Chat 和 Responses 的完成原因及工具结果。
- Anthropic end_turn/max_tokens/model_context_window_exceeded/tool_use/refusal/pause_turn。
- Gemini STOP（带/不带工具）和 MAX_TOKENS。
- Ollama stop（带/不带工具）和 length。
- 三种原生协议的长度统一为 length 后，应用均拒绝将其作为成功回复。
- 旧非思考工具快照在同模型启用思考后直接成功重试；其他模拟 HTTP 400 仍可保留
  快照并切换供应商，工具不重复执行。

在线覆盖仅 DeepSeek Flash 与 Kimi K3；其他协议的归一化使用离线 SDK 传输验证，
没有用在线请求穷举全部模型、推理档位或签名历史组合。
临时脚本、设置和数据库已删除；凭证、完整推理文本未写入项目或报告。
