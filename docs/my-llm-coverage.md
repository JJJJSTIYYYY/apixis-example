# my-llm-py 1.3.2 接入

依赖为 `my-llm-py[all]>=1.3.2`，uv.lock 固定 1.3.2，导入名为 `my_llm`。
`[all]` 安装现有六供应商所需的官方 SDK；1.3.2 已将这些 SDK 改为可选依赖。
接入依据为本地源码的 docs/reference/api.md、configuration.md、semantic-loss 指南和实际发行包。

## 公开配置

| 接口 | 本项目的用途 |
| --- | --- |
| `Attribute(default_value=...)` | 模型 ID、上下文窗口和 Provider 模型目录；通过 `.default_value` 读取 |
| `Config(default_value=...)` | 协议、连接设置、工具能力、原生 extra_body 和推理强度映射 |
| `Parameter(path=..., default_value=..., supports=...)` | API Key、推理开关和强度的路径、缺失默认值及能力声明 |
| `ProviderProfile.list_models()` | 获取完整模型配置；显式未知 ID 创建支持工具的 ModelProfile |
| `dataclasses.replace()` | 创建覆盖配置，再创建新 MyLLM 实例 |

Parameter/Config 的 name 由所属 Profile 绑定，不再接受构造参数；Parameter 的来源由
Profile 声明固定，不再传 phase。推理能力由 reasoning_enable.supports、
reasoning_effort.supports 表达，不再使用 supports_reasoning/supports_reasoning_effort。

六供应商仍为 deepseek、kimi、openai、anthropic、gemini、ollama，18 预设与来源见
[模型预设](model-presets.md)。本次迁移没有更新模型清单，核查日期仍为 2026-10-16。

DeepSeek/Kimi 使用 Chat，OpenAI 使用 Responses，其余使用各自原生协议。
DeepSeek 与 Kimi K2.x 的 thinking 通过 `$.extra_body.thinking` 配置；K3 在模型层 omit
该旧开关，独立声明 reasoning_effort。K2.x 禁用分级强度；Anthropic 使用 adaptive thinking，
Gemini 使用 thinking_level，Ollama 沿用布尔 think。映射和字段生成均由库负责。
所有内置协议现在默认开启可见思考回放；应用不补写 reasoning_content。

生产输出预算直接放在 `ModelProfile.extra_body=Config(default_value={...})` 中。
DeepSeek/K2.x 使用 max_tokens，K3 使用 max_completion_tokens；两家预设仍省略预算，
沿用服务端默认。Anthropic 的 max_tokens=4096 是应用预算。应用不转换 ModelProfile.max_tokens。

## 选择、认证与生命周期

`/provider <供应商> [模型ID]`、`/model <模型ID>` 和启动参数选择模型，只有 API Key 从环境读取。
模型切换只更新选择，保留同一运行时、编译图、完整消息和失败快照，/retry 使用新选择恢复。
已提交工具结果继续回传，不重复执行。启动和选择模型不要求凭证，请求时才检查。

供应商专属凭证变量优先于 LLM_API_KEY，见 [README](../README.md#启动)。
密钥使用 Provider 的 input Parameter：通常路径为 $.api_key；Ollama 可选认证使用
$.extra_headers.Authorization，应用填入完整 Bearer 值。没有密钥的本地 Ollama 不添加认证头。
这是连接配置，不是生成请求体或协议转换；密钥不作为消息或 extra_body 发送。

每次 stream_reply 用 `async with MyLLM(...)` 管理实例，内部 `async with stream` 管理响应。
先关闭请求流，再关闭实例缓存的 SDK 客户端；成功、SDK 异常、显示异常和取消均测试资源关闭。
默认超时 120 秒、SDK 重试 0，图节点总时限仍为 130 秒。

## 消息、流和用量

应用明确使用 `stdio_protocol="openai_chat"`，消息、工具、流事件和最终结果均为标准 Chat。
上游协议独立选择。仍直接传入完整历史，由 MyLLM 负责协议转换、工具组装和最终消息收集；
应用没有自建 HTTP/SSE、Codec、工具参数拼接、SDK 缓存或完成原因映射。

终端消费 choices[*].delta.content；完成原因取 choices[*].finish_reason。
遇到 length，应用抛出携带部分消息的 StreamError，保留失败快照，不保存为成功对话。
Responses 的 failed/incomplete 沿用库异常；UI 按 details.get("reason") 提示输出预算不足。
details 是可读取的原生错误信息，本项目不要求它满足普通字典的 JSON 序列化行为。

完整消费后将 stream.result 直接写入图状态和 SQLite JSON。1.3.2 重复读取返回同一本次结果，
应用不修改该结果；失败或提前关闭时不把 result 当作成功消息使用。
库会过滤公开流事件中的 usage。需要用量时，在成功消费后使用 `llm.get_last_usage()`，
返回独立 Usage（input/output/total/cached/reasoning），未报告字段为 None。
失败不覆盖上次成功用量，并发时记录最后完成调用；用量不写入对话消息。

## 语义损失

保留 1.3.2 默认的 `semantic_loss_protocol="raise"`。目标协议无法表达支持内容时，
库抛出 SemanticLossError，例如向 Anthropic 回传没有有效签名的外来思考内容。
UI 提示切回兼容供应商后重试或 /new，新选择本身不清空历史和失败快照。
应用不删除思考、不伪造签名、不自动选择 ignore，也不加入修补 hook。

这意味着“可切换”保证选择和状态保留，不承诺任意历史均能无损回传给任意协议。
协议支持集合之外的字段仍可能被库过滤；SQLite 保存完整返回值不等于所有原生内容跨协议无损。

## 验证

[1.3.2 迁移报告](live-e2e-1.3.2-2026-10-25.md)记录全部离线测试和在线流程。
离线使用真实官方 SDK 与 HTTPX MockTransport，覆盖六供应商、18 预设、工具历史和持久化、
取消及失败恢复、语义损失后切回重试，以及新的实例用量接口。

1.0.0、1.0.1、1.1.0 报告保留为历史验证，不能代表当前接口。先前提及的 1.1.0 异常
详情视图差异不影响本项目按键读取路径，也未在应用增加兼容处理。
