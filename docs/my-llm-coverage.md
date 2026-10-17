# my-llm-py 1.0 接入

本项目依赖 `my-llm-py>=1.0.1`，锁文件固定为 1.0.1，导入名为 `my_llm`。
接入依据是源码仓库的 `docs/reference/api.md`、`configuration.md` 和实际发行包。

## 调用边界

| 公共接口 | 本项目的使用方式 |
| --- | --- |
| `ProviderProfile.protocol` | 选择上游 API：`openai_chat`、`openai_responses`、`anthropic`、`gemini`、`ollama` |
| `ModelProfile` | 声明模型 ID、输出预算、工具能力与原生参数 |
| `ProviderProfile.list_models()` | 选择完整模型配置；显式输入未登记 ID 时创建支持工具的 ModelProfile |
| `MyLLM(..., stdio_protocol="openai_chat")` | 应用消息、工具声明、流事件和最终结果统一使用标准 Chat 格式 |
| `await MyLLM.generate(messages=..., tools=..., stream=True)` | 将完整历史和工具描述直接交给库，转换到所选上游协议 |
| `async with MessageStream` | 正常结束、显示异常或取消时关闭本轮请求 |
| `stream.result` | 完整消费后直接取标准 assistant 消息，工具参数与文本均由库组装 |
| `ConfigurationError`、`StreamError` 及子类 | 保留库的错误类型及部分消息；SDK HTTP/网络异常保持原类型 |

项目没有自建 HTTP/SSE、工具参数片段拼接、协议转换器或 SDK Client 缓存。
1.0 不再提供 `Message` / `MessageChunk`，也不接受 `MyLLM(client=...)`；
每次请求的 SDK Client 和上游流均由库创建、关闭。Apixis 负责工具审批、执行、快照和重试。

`stdio_protocol` 与上游 `protocol` 独立。应用明确绑定 Chat，避免纯文本历史的格式识别歧义；
切换到 Responses、Anthropic、Gemini 或 Ollama 时，仍向库传入相同格式的完整历史。
无需在应用中调用 `MyCodec` 再次转换，也不混用 `previous_response_id`。

## 流事件、历史与错误

终端只显示 `event["choices"][*]["delta"]["content"]`。完整消费后把 `stream.result`
原样写入图状态和 SQLite JSON，包括工具调用、可见 `reasoning_content` 和库保留的扩展。
工具参数完整性及缺少终止事件的检查均由库完成；应用不从增量重建消息。

完成原因读取 Chat 流事件中的 `choices[*].finish_reason`，用量尾块允许 `choices=[]`；
这些元数据不再额外写入 assistant 消息。遇到库输出的 `length`，应用抛出携带
`stream.result` 的 `StreamError`，保留失败快照供 `/retry`，不把截断回复保存为成功对话。
Responses 的 `response.failed` / `response.incomplete` 直接使用库的
`ResponseFailedError` / `ResponseIncompleteError`；输出预算不足时根据公开的
`details.reason == "max_output_tokens"` 给出提高预算的提示。应用不维护各供应商完成原因映射表。
1.0.1 不能将 `pause_turn`、`model_context_window_exceeded` 或未知完成原因表示为标准
Chat 终止值时，会抛出 ResponseFailedError，原始原因保留在 details.reason；
应用沿用失败快照与通用错误提示，不把它们改写成正常结束。

1.0 的原生输出取决于所选 stdio 协议。Chat 历史不再使用旧版统一消息的
`response_output` / `response_id` 包装字段；跨协议转换也不保证保留目标协议不支持的
Responses encrypted reasoning、Anthropic thinking 签名或 Gemini parts。SQLite 仍保存完整
返回字典，旧记录与旧数据库可继续读取，但这不等于将旧版原生包装字段迁移为 1.0 协议格式。

## 供应商与模型

六个供应商为 `deepseek`、`kimi`、`openai`、`anthropic`、`gemini` 和 `ollama`。
DeepSeek/Kimi 使用 `openai_chat`，OpenAI 使用 `openai_responses`，其余使用各自原生协议。
1.0 移除了 `api_type`，更换 OpenAI API 时直接设置 `ProviderProfile.protocol`。
原有 18 个模型预设及核查来源见[模型预设](model-presets.md)，本次升级没有更新模型清单。

`/provider <供应商> [模型ID]`、`/model <模型ID>` 和启动参数 `--provider` / `--model`
选择模型。切换只更新选择，保留运行时、编译图、消息和失败快照；`/retry` 在旧快照上使用新选择，
已提交的工具结果继续回传。启动和选择模型不要求凭证，发起请求时才检查。

仅 API Key 从环境读取，变量见 [README](../README.md#启动)；供应商专属变量优先于
`LLM_API_KEY`。地址、协议、预算、推理参数和能力差异均直接使用库的 Profile，
不读取其他环境覆盖，也不修改库的内部解析结果或 Codec。

DeepSeek、Kimi K2.x 的输出预算直接配置在 `ModelProfile.extra_body.max_tokens`；
Kimi K3 使用 `extra_body.max_completion_tokens`。预设未限制这两家的输出预算，
沿用服务端默认。其他协议使用库的 `ModelProfile.max_tokens`，由库映射原生字段；
Anthropic 预设为 4096。默认超时 120 秒、SDK 重试 0；可用新的 Profile 调整，
应用层恢复使用 `/retry`，图节点总时限仍为 130 秒。

1.0.1 的官方 Chat 默认不回放扩展 reasoning_content，默认推理开启参数为
reasoning_effort。DeepSeek/Kimi 在 ProviderProfile 显式打开
`replay_reasoning_content_when_tool_calls=True`，并通过公开的 reasoning_enable_parameter
绑定原生 thinking 开关；该字典用 `reasoning_effort=omit` 去掉继承值，支持强度的模型
再通过 reasoning_effort_parameter 发送实际映射强度。K3 模型仍使用 omit 禁用旧 thinking 开关。
这些都是配置，没有在调用路径上增加协议转换分支。

开启回放后，库保留已有可见思考；仅在思考已开启且 assistant 当条包含工具调用时，
为缺失思考补空字符串。普通 assistant 或关闭思考时不一律补空值，应用不改写历史来补字段。

新增供应商时，在 `core/providers.py` 添加原生 `ProviderProfile` 并在 `API_KEY_ENV`
声明凭证变量；模型级覆盖使用 `ModelProfile`，配置变更使用 `dataclasses.replace()` 后创建新实例。
库支持的原生参数、推理绑定、headers 和解析路径都可通过 Profile 配置，无需新增适配器。

## 验证范围

测试使用已安装的 1.0.1 发行包、官方 SDK 和本地模拟传输，覆盖六个供应商的请求、
Chat → Responses → Chat 工具历史转换、原生协议输出上限、Responses 失败和部分消息、
用量尾块、取消和显示错误时资源关闭，以及切换思考模式/供应商后恢复且工具不重跑。
配置测试覆盖 18 个模型和凭证隔离；Anthropic/Gemini/Ollama 的真实返回消息经过 SQLite
保存、重载后继续回传工具结果，另覆盖旧数据库与完整 JSON 的读取。

2026-10-17 使用 1.0.0 发行包完成了 DeepSeek Flash/V4 Pro、Kimi K3 的
[真实端到端验证](live-e2e-1.0.0-2026-10-17.md)：6 次请求均 HTTP 200，覆盖非思考工具轮
开启思考后重试、切换模型与供应商、长度截断后恢复及独立 usage 尾块。
工具只执行一次，同一对话和失败快照保留；原生 usage 合计 4,011 tokens。
其他供应商仍仅离线验证。仓库中 0.1.x 的实测报告是历史记录，不代表 1.0 的验证结果。

2026-10-17 升级 1.0.1 后完成[全量回归与在线流程验证](live-e2e-1.0.1-2026-10-17.md)：
80 项离线测试通过；8 次真实请求全部 HTTP 200，涵盖 Flash/V4 Pro、K3/K2.6，
并通过真实 UI 验证登录、审批、历史恢复、退出和用户隔离。
测试助手改为通过 HTTPX 的公开 transport 注入模拟响应，不再依赖 MyLLM 内部 SDK 导入位置。
