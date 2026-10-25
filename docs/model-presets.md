# 模型预设与官方来源

核查日期：**2026-10-16（Asia/Shanghai）**。下表来自本次实际打开的官方页面，
不是知识库中的旧模型清单。预设定义在 `src/example/core/providers.py`，共 18 个。

每行第一个模型是应用选择的默认值，并不表示供应商推荐排序。`/provider <供应商>`
会选择该默认值，`/model` 列出该供应商的预设；仍可显式输入其他模型 ID。

| 供应商 | 预设模型 ID（首项为应用默认） | 官方模型来源 |
| --- | --- | --- |
| DeepSeek | `deepseek-flash`、`deepseek-v4-pro` | [模型列表](https://api-docs.deepseek.com/api/list-models/) |
| Kimi | `kimi-k3`、`kimi-k2.7-code`、`kimi-k2.7-code-highspeed`、`kimi-k2.6` | [模型列表](https://platform.kimi.ai/docs/models) |
| OpenAI | `gpt-6.1-sol`、`gpt-6-astra`、`gpt-6-luna` | [Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol)、[Astra](https://developers.openai.com/api/docs/models/gpt-6-astra)、[Luna](https://developers.openai.com/api/docs/models/gpt-6-luna) |
| Anthropic | `claude-sonnet-5-5`、`claude-opus-5-5`、`claude-haiku-5-5`、`claude-fable-5-1` | [Claude 模型概览](https://platform.claude.com/docs/en/models/overview) |
| Gemini | `gemini-3.8-flash`、`gemini-3.5-flash-lite` | [3.8 Flash](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash)、[3.5 Flash-Lite](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite) |
| Ollama | `qwen3.5:9b`、`qwen3.6:27b`、`qwen3.6:35b` | [Qwen 3.5](https://ollama.com/library/qwen3.5)、[Qwen 3.6](https://ollama.com/library/qwen3.6) |

Ollama 的默认选用较小的 9B 模型，并提供较新的 Qwen 3.6。列表不表示这些模型已经
下载到本机；使用前需要自行安装对应 tag。本次没有下载模型或发起推理请求。

## 协议与能力配置

- **DeepSeek**：模型列表明确给出两个当前 ID；保留 Chat Completions 路径。
  [Chat API](https://api-docs.deepseek.com/api/create-chat-completion/) 支持工具调用、
  thinking 开关和 `low/high/max` 强度，兼容别名 `medium/xhigh` 对应 `high`。
  Profile 中声明了相应能力和映射，需要输出上限时直接在模型的 `extra_body` 中声明：
  DeepSeek/Kimi K2.x 使用 `max_tokens`，Kimi K3 使用 `max_completion_tokens`，
  应用不转换 `ModelProfile.max_tokens`。
- **Kimi**：K3 始终推理，只接受 `low/high/max`，不发送 K2.x 的 thinking 开关，
  不将 `medium/xhigh` 当作原生强度。见 [推理强度](https://platform.kimi.ai/docs/guide/use-reasoning-effort)。
  [K2.7 Code](https://platform.kimi.ai/docs/guide/kimi-k2-7-code-quickstart) 为始终推理模型，
  [K2.6](https://platform.kimi.ai/docs/guide/kimi-k2-6-quickstart) 支持 thinking 开关。
  旧 `kimi-k2.5` 已列入停用模型，不纳入预设。
- **OpenAI**：Sol/Astra 的工具调用使用 Responses；Luna 也统一使用该 API，避免
  Chat 工具调用对推理配置的额外限制。三个预设均声明函数工具和推理能力。
  Sol/Astra 不支持发送原生 `none` effort；本项目调用参数缺省时不发送该字段，
  由服务端使用默认值。此处未启用程序工具或其他新的工具类型。
- **Anthropic**：四个当前模型均支持工具和 adaptive thinking。
  Profile 使用 `thinking.type=adaptive`，显式请求推理时也沿用该配置，避免发送旧版
  `enabled/budget_tokens`。五档强度见 [effort 文档](https://platform.claude.com/docs/en/build-with-claude/effort)。
  `extra_body=Config(default_value={"max_tokens": 4096})` 是应用的输出预算，**不是模型最大输出限制**。
- **Gemini**：两个稳定模型均支持函数调用与 thinking，配置原生 `thinking_level`。
  预设允许公共接口中的 `low/medium/high`，不把 `xhigh/max` 透传为无效等级。
- **Ollama**：官网标签确认这些模型支持 tools 和 thinking，使用原生协议；
  Profile 保留布尔 thinking 能力，不声明这几个 Qwen 模型支持分级 effort。

`max_context_window` 仅是 my-llm-py 的描述性元数据，不执行截断、计数或额度检查。
DeepSeek、OpenAI、Gemini 页面提供的精确数值直接记录；Kimi、Claude 和 Ollama
仅标注 `1M` / `256K` 的页面，按十进制换算为描述值，不能视为服务端精确边界。

## 本地验证与边界

测试通过已安装的 my-llm-py 和官方 SDK 配合本地模拟传输，检查预设 Profile
有效性、K3 请求无旧 thinking 字段、DeepSeek effort 映射、Responses 函数工具格式，
以及原生协议请求。原有跨供应商工具历史转换和切换后重试测试继续保留。

文档核查不能证明某个账户已获模型权限、当地服务可达或本机已安装 Ollama 模型。
本次未调用付费接口。后续更新预设时，应重新打开这些官方页面并更新核查日期。

## MyLLM 1.3.2 配置形式

本次只迁移配置接口，未重新核验模型目录，以上来源日期保持不变。
模型 ID、上下文窗口和目录分别使用 Attribute；协议和 extra_body 使用 Config；
推理开关、强度与能力使用 Parameter.path/supports。name/source 由 Profile 绑定。
跨协议无法表达的历史内容默认抛 SemanticLossError，不会因切换自动清空历史或修补签名。
