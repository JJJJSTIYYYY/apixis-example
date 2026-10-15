# my-llm-py 接入

本项目使用已安装的 `my-llm-py>=0.1.4`，导入名为 `my_llm`。接入依据是该项目
`docs/reference/api.md`、`configuration.md` 和 `design-coverage.md` 中的公共接口。

## 调用边界

| 公共接口 | 本项目的使用方式 |
| --- | --- |
| `ProviderProfile` | `core/providers.py` 直接声明供应商的协议、API、地址和模型列表 |
| `ModelProfile` | 声明模型 ID、输出预算、工具能力与模型专属参数 |
| `ProviderProfile.list_models()` | 选择匹配模型的完整配置；未匹配时创建支持工具调用的 ModelProfile |
| `MyLLM(provider=..., model=...)` | `core/llm.py` 通过 Profile 构造调用入口 |
| `await MyLLM.generate(messages=..., tools=..., stream=True)` | 将 Apixis 节点已有消息和工具描述直接交给库 |
| `async with MessageStream` | 正常结束、显示异常或取消时关闭本轮请求 |
| `MessageChunk`、`+=`、`to_message(require_finished=True)` | 累加统一消息，交给库拼接工具调用和保留原生输出 |
| `ConfigurationError`、`StreamError` 及子类 | 显示配置或流错误；SDK 的认证、网络和 HTTP 异常保留原类型处理 |

项目没有自建 HTTP/SSE、工具参数片段拼接、消息协议转换或 SDK Client 缓存。
每次请求使用 my-llm-py 自行管理的 SDK Client，关闭流时由库释放，不需要额外的
`close_client()`。Apixis 继续负责工具审批、工具执行、图快照和重试。

流式回答只展示 `chunk.content`，完整 `Message` 原样进入图状态和 SQLite。
`reasoning_content`、Responses `response_output`、Anthropic 签名及 Gemini 原生 parts
因此可以在下一轮、`/history` 和重启后继续回传。旧数据库自动增加完整消息 JSON 列，
旧消息仍按原字段读取。应用采用完整本地历史，不混用 `previous_response_id`。

MyLLM 0.1.4 统一返回 `finish_reason`：正常结束为 `stop`、长度截断为 `length`、
完整工具调用为 `tool_calls`、可映射的内容过滤为 `content_filter`。
应用只根据统一的 `length` 抛出带 partial_message 的 StreamError，避免将截断回复
保存为成功对话；不再判断 Anthropic 原生的 `max_tokens` 等别名。
`pause_turn`、未知原因及 Responses 的异常语义由库保留，应用不另建映射表。

## 供应商配置

通过 `/provider <供应商> [模型ID]` 和 `/model <模型ID>` 选择模型。
不带参数时显示当前选择和预设列表；预设之外的模型 ID 也可使用。
六种供应商为 `deepseek`、`kimi`、`openai`、`anthropic`、`gemini` 和 `ollama`。
启动参数 `--provider`、`--model` 指定初始选择，也支持单次模式。

仅凭证从环境变量读取，具体变量见 [README](../README.md#启动)。
供应商专属凭证优先于通用 `LLM_API_KEY`。启动和选择模型不要求凭证，发起请求时才检查。
地址、API 类型、输出预算等均使用 Profile 配置，不读取环境覆盖。

六个供应商均提供预设，默认 DeepSeek 模型为 `deepseek-flash`。模型 ID 与能力已按
2026-10-16 的官方文档核查，完整列表与来源见 [模型预设](model-presets.md)。
也可通过 `/model` 或 `/provider <供应商> <模型ID>` 显式选择其他模型。
选择的模型应支持本应用的函数工具调用；差异在原生 Profile 上声明。
DeepSeek、Kimi 默认使用 Chat Completions，OpenAI 默认使用 Responses；可修改
ProviderProfile.api_type 使用 Chat。供应商地址依据
[DeepSeek 官方文档](https://api-docs.deepseek.com/) 和
[Kimi 官方文档](https://platform.kimi.ai/docs/overview) 配置。

切换仅更新 AgentRuntime 的供应商和模型选择，不替换运行时、编译图、对话消息、
待重试输入或失败快照。LLM 节点执行时读取当前选择，所以 `/retry` 会用新模型继续
旧快照，已提交的工具结果继续回传。完整历史直接交给 my-llm-py 测试跨供应商和
跨 API 兼容性；本项目不预先裁剪、改写或丢弃推理、工具或原生输出。

MyLLM 0.1.4 默认在 Chat 请求的每条 assistant 消息中回传 `reasoning_content`，
缺失时由库补空字符串，已有推理原样保留；应用状态和 SQLite 消息不为此改写。
0.1.3 中“非思考工具轮尚未完成时直接开启 DeepSeek 思考模式”曾出现 HTTP 400，
0.1.4 [真实复测](live-retest-0.1.4-2026-10-16.md)已在 DeepSeek 内直接恢复成功，工具未重跑。
此前混合模式、切换模型与供应商的记录见[原实测](live-mode-switch-e2e-2026-10-16.md)。

DeepSeek、Kimi K2.x 的 Chat API 使用原生 `max_tokens` 字段，需要限制输出时直接配置
`ModelProfile(extra_body={"max_tokens": 4096})`；Kimi K3 按其官方文档使用
`ModelProfile(extra_body={"max_completion_tokens": 4096})`。这两家均不使用 `ModelProfile.max_tokens`，
应用不做字段转换。现有预设未显式设置这两家的输出预算，继续使用服务端默认值。
OpenAI 使用库的标准 `max_tokens` 参数，由库映射为 Responses 的 `max_output_tokens`
或 Chat 的 `max_completion_tokens`。原生协议也由库映射输出上限，Anthropic 默认
为 4096。默认超时 120 秒，SDK 重试设为 0，失败恢复交给 Apixis 的 `/retry`；可在
ProviderProfile 上显式调整。图节点仍有 130 秒的总时限。

## 扩展模型和能力

直接编辑 `core/providers.py` 的原生 Profile，无需实现新适配器。例如为 Kimi
配置一个需要显式 thinking 开关的模型：

```python
from my_llm import ModelProfile, ProviderProfile

# 替换为账户中真实可用、接受这些参数的模型 ID。
kimi = ProviderProfile(
    base_url="https://api.moonshot.ai/v1",
    models=[ModelProfile(
        model="your-thinking-model",
        supports_function_calling=True,
        extra_body={"thinking": {"type": "enabled"}},
    )],
)
```

Profile 的 `extra_body`、`extra_headers`、推理参数绑定、能力开关、输出解析路径和
模型级覆盖由 my-llm-py 解析。修改配置时创建新的 Profile / MyLLM 实例；不要修改库的
内部解析结果或 Codec。新增供应商时在 `PROVIDERS` 添加 ProviderProfile，并在
`API_KEY_ENV` 声明其凭证变量；可继续复用现有四种协议。

## 验证范围

测试使用已安装的 my-llm-py、官方 SDK 和本地模拟传输，覆盖 DeepSeek/Kimi Chat
请求、OpenAI Responses 工具格式及原生输出回传、Anthropic/Gemini/Ollama 原生请求，
以及输出上限、流中断与流关闭、Kimi Chat → OpenAI Responses → DeepSeek Chat 的工具历史回传。配置测试覆盖六个供应商及凭证隔离；历史测试覆盖原生
元数据和旧数据库迁移。
切换与重试测试验证同一个编译图保留失败快照、完整消息及工具结果，且成功的工具不重跑。
真实 DeepSeek 与 Kimi 的小预算验证另记于[复测报告](live-retest-0.1.4-2026-10-16.md)
及其关联历史报告；其他供应商仍仅离线验证，未穷举所有模型、推理档位或计费行为。
