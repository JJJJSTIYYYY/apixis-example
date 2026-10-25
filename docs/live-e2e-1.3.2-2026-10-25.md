# MyLLM 1.3.2 对齐与端到端测试（2026-10-25）

## 迁移范围

基于本地 MyLLM 的 reference/api.md、reference/configuration.md、guides/semantic-loss.md，
并使用 PyPI 安装的 1.3.2 验证。最低依赖改为 my-llm-py[all]>=1.3.2，锁定版本 1.3.2。
官方 SDK 在新版本为可选依赖，[all] 保证现有六供应商可用。

- 模型 ID、上下文窗口和目录改用 Attribute；协议改用 Config，选择和显示从 default_value 读取。
- 移除 Config/Parameter 构造中的 name 和 phase，身份与来源由 Profile 声明绑定。
- 推理开关和强度能力改为 Parameter.supports；协议路径、强度映射及原生预算继续使用公开配置。
- API Key 改为 Parameter；通常走 $.api_key，Ollama 可选认证走 Authorization Bearer 连接头。
  仅密钥来自环境，不从环境读取模型或供应商选择。
- 沿用 async with MyLLM 和 async with stream，保证响应与客户端关闭。
- 用量从 get_last_usage() 读取，公开事件不再有 usage；不向历史消息添加用量或完成原因。
- 保留默认语义损失 raise；只增加面向用户的错误提示，不自动删除思考、伪造签名或忽略损失。
- Chat 可见思考回放已默认开启，无需旧预设显式开启。Anthropic 工具结果文本现在是标准内容块列表。

未增加应用层协议转换、HTTP/SSE、工具组装或完成原因映射。既有 18 个模型 ID 沿用
2026-10-16 核验目录；本次未重新研究或更新预设模型。

## 离线回归

`python -m unittest discover -s tests -v`：83 项全部通过。

包括六供应商、18 预设、真实官方 SDK 与 HTTPX 模拟传输、Chat/Responses 工具历史、
原生 Anthropic/Gemini/Ollama 工具结果持久化及回传、HTTP/流错误、取消、回调异常和资源关闭，
登录退出和历史界面，以及独立进程的 160 路并发、队列背压与嵌套图工具事件。

新增重点验证：

1. 上游 usage 尾块被过滤，公开流事件无 usage；实例成功后得到独立 Usage，失败不覆盖成功记录。
2. 外来 Chat reasoning_content 回传 Anthropic 被 SemanticLossError 拒绝，零网络请求；
   原消息与失败快照保留，切回 DeepSeek 后同一快照恢复成功，不暴露错误中的思考内容。
3. 本地 Ollama 不要求密钥，有 LLM_API_KEY 时进入 SDK 客户端 Authorization 头，
   不使用不存在的 Ollama api_key 参数；Provider repr 不显示密钥。

Ruff E4/E7/E9/F/I、格式、CLI --check/--help 和 git diff --check 均通过。

## 在线流程

使用此前授权的 DeepSeek、Moonshot 凭证。临时空工作目录、独立设置和 SQLite，
运行真实 chat_loop、AgentUI、Rich、一个 AgentRuntime/编译图、Apixis 审批与工具、MyLLM 和官方 SDK。
只脚本化键盘输入和审批按键；审批只允许固定 printf sdk132_17，实际 shell 执行一次。

透明包装公开 MessageStream 观察完成原因，流结束后调用公开 get_last_usage()；
HTTP 观察不解析响应或模拟服务，仅记录请求模式、消息数和 HTTP 状态。

| 请求 | 模型/行为 | HTTP | finish_reason | 输入 tokens | 输出 tokens |
| --- | --- | ---: | --- | ---: | ---: |
| 1 | Flash 非思考，调用工具 | 200 | tool_calls | 510 | 42 |
| 2 | 同模型开启思考，从工具提交后的失败快照 /retry | 200 | stop | 618 | 6 |
| 3 | /model 切换 V4 Pro，沿用完整对话 | 200 | stop | 628 | 6 |
| 4 | /provider 切换 Kimi K3，正确回复历史工具标记 | 200 | stop | 622 | 52 |
| 5 | Flash 非思考，1 token 上限触发真实截断 | 200 | length | 652 | 1 |
| 6 | 切换 K3，从截断快照 /retry，回复算术答案 4 | 200 | stop | 708 | 51 |
| 合计 | | | | 3,738 | 158 |

共 6 次推理请求，全部 HTTP 200，原生用量合计 3,896 tokens，不是账单金额。
reasoning 用量已包含在输出中，不重复相加；未报告字段为 None，不推导总量。

请求 1 的工具结果提交后，在下一次模型调用前注入一次本地异常，零额外 API 用量。
切换命令保持 last_context 对象身份，/retry 从快照恢复，工具没有重复执行。
最终完成原因序列为 tool_calls/stop/stop/stop/length/stop；所有公开事件均无 usage 字段。

随后实际操作 /history 选择、/transcript、/output、/logout 和用户名 /list-user；
第二用户无历史且 /retry 提示没有可重试运行，重新登录首用户可恢复对话。
最终 SQLite 一段对话、10 条消息、一个工具结果；两个用户名保留。
预期错误三次：本地注入故障、真实截断、退出后无重试状态。截断回复未保存为成功轮次。

临时测试 Profile 使用 DeepSeek 和 Moonshot CN 地址，SDK 重试 0、超时 45 秒，
usage_mode=stream_options。Flash/Pro/K3 原生上限 512，截断用 1，思考开启时使用 low。
预算和模式仅作用于测试；生产默认、Kimi 国际地址和模型目录保持原有选择。

## 隐私和边界

密钥通过隐藏终端输入，仅注入测试进程环境，未写入代码、文档、配置、SQLite 或终端日志文件。
临时脚本及状态目录测试后删除；未提交或推送修改。

在线仅覆盖 Flash、V4 Pro 和 Kimi K3。K2.x 及 OpenAI、Anthropic、Gemini、Ollama
通过官方 SDK 离线验证，不声称所有模型都发起了真实推理。
语义损失失败在离线验证，不为制造此错误消耗真实请求；跨协议不保证所有原生能力无损。
