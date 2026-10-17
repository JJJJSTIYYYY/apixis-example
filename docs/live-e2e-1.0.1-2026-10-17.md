# MyLLM 1.0.1 升级与全量端到端回归

日期：2026-10-17（Asia/Shanghai）。结果：**修正配置与测试注入后，全量离线回归及
有凭证供应商的真实端到端流程全部通过。**

## 升级范围和检查发现

- pyproject.toml 的最低版本改为 my-llm-py>=1.0.1，uv.lock 精确锁定 1.0.1，虚拟环境同步。
- 对比升级前后锁文件，仅 MyLLM 从 1.0.0 升为 1.0.1，其他依赖版本保持原样。
- 核对 MyLLM、MyLLM.generate、ProviderProfile、ModelProfile、MessageStream、StreamError、
  ResponseIncompleteError 的调用签名；排除 omit 实例的进程内地址差异后，签名一致。

公开接口没有变化，但内部布局和部分默认行为改变，首次回归发现了以下问题，均已处理：

1. 旧测试助手替换 my_llm.invoke 中的 SDK 构造名称；1.0.1 调整内部导入后失效。
   tests/sdk_transport.py 现在只在 HTTPX 公开构造接口注入 MockTransport，保留真实 SDK
   序列化以及 MyLLM 自建/关闭 Client 的行为，不依赖库内部模块路径。
2. 官方 Chat 默认关闭 reasoning_content 回放，默认使用官方 reasoning_effort 开启推理。
   DeepSeek/Kimi 现在通过原生 ProviderProfile 显式开启回放，并声明 thinking 扩展绑定；
   reasoning_enable_parameter 内用 reasoning_effort=omit 移除继承值，支持强度时再发送映射值。
   K3 继续在 ModelProfile 禁用旧 thinking 开关。配置依据为 MyLLM 本地 reference 文档。
3. 无法表示为标准 Chat 的未知完成原因、Anthropic pause_turn/model_context_window_exceeded，
   库明确抛 ResponseFailedError、保留 details.reason。测试改为验证失败语义，
   没有在应用中添加完成原因映射或吞掉库异常。

生产调用代码和消息历史处理没有新增协议兼容实现；改动为依赖、Profile、测试助手/断言与文档。
新增回归也检查了 Kimi K2 不发送不支持的 effort，以及官方 OpenAI Chat 不继承兼容服务的思考字段。

## 全量离线与本地检查

- `python -m unittest discover -s tests -v`：**80 项全部通过**。
- 覆盖六供应商、18 模型配置及凭证隔离，Chat/Responses 和原生 Anthropic/Gemini/Ollama
  官方 SDK 请求、工具关联与历史回放、SQLite 完整消息/旧库读取、流关闭、取消及错误恢复。
- 包括独立进程的 160 路并发测试，backpressure=128、queue=8，工具事件及子图全部完成。
- Ruff E4/E7/E9/F/I 检查、格式检查、CLI --check/--help、git diff --check 通过。

## 在线测试方式

使用用户已授权的 DeepSeek 和 Moonshot 国内凭证。运行真实 chat_loop、AgentUI、
Rich 输出、登录与历史界面、Apixis 图/事件、shell 工具、MyLLM 1.0.1 与官方 SDK。

只替换键盘输入和审批按键；真实 UI 照常渲染，输出保存在进程内字符串中，未保存完整终端日志。
审批前严格匹配仅允许 `printf sdk101_ds_17`、`printf sdk101_k3_37`，随后执行真实审批界面。
两条命令均由真实 shell 执行、退出码 0。

测试包装公开 MessageStream 观察完成原因和 usage，再原样交给应用；HTTP 观察仅记录模型、
模式与历史字段数量，没有模拟在线响应、手写 SSE 或工具参数组装。

临时 Profile 配置：

- DeepSeek：`https://api.deepseek.com/v1`，Flash、V4 Pro，直接在 ModelProfile.extra_body
  配置 max_tokens=512，思考开启时使用原生 thinking.type=enabled 和 reasoning_effort=low。
- Moonshot：`https://api.moonshot.cn/v1`。K3 直接配置 max_completion_tokens=512、
  reasoning_effort=low；K2.6 配置 max_tokens=128、thinking.type=disabled。
- 截断请求把 DeepSeek 输出上限临时改为 1，关闭思考。
- 超时 45 秒、SDK 重试 0；硬性最多 8 次推理请求，没有模型列表或余额查询。

预算与模式只用于测试，仓库默认预算和 Kimi 国际地址保持原样。

## 在线请求与用量

| 请求 | 模型/模式 | 行为 | HTTP | 流完成原因 | 输入 tokens | 输出 tokens |
| --- | --- | --- | ---: | --- | ---: | ---: |
| 1 | Flash 非思考 | 调用固定 printf，真实执行一次 | 200 | tool_calls | 493 | 44 |
| 2 | 同模型开启思考 | 原失败快照 `/retry`，正确返回工具标记 | 200 | stop | 607 | 8 |
| 3 | V4 Pro 思考 | `/model` 切换后正确回复原标记 | 200 | stop | 621 | 71 |
| 4 | Kimi K3 | 接收原历史，调用另一个固定 printf | 200 | tool_calls | 689 | 90 |
| 5 | Flash 思考 | 切换供应商后从 K3 工具快照恢复，正确回复新标记 | 200 | stop | 861 | 8 |
| 6 | Kimi K2.6 非思考 | 接收混合历史，正确回复两次工具标记 | 200 | stop | 511 | 14 |
| 7 | Flash 非思考 | 1 token 预算触发真实截断 | 200 | length | 797 | 1 |
| 8 | Kimi K3 | 从长度截断快照 `/retry`，回复算术答案 4 | 200 | stop | 952 | 37 |
| 合计 | | | | | **5,531** | **273** |

供应商原生 usage 合计 **5,804 tokens**；reasoning tokens 包含在 completion_tokens 中，
不重复相加，统计不是账单金额。8 次均收到独立 choices=[] 的 usage 尾块。
标准 stream.result 不含自定义 finish_reason/usage；截断判断仍从流事件读取。

请求 1、4 的工具结果提交后，在下一次模型调用前各注入一次本地异常，以产生真实失败快照。
这不是供应商故障，也不增加 API 请求。原失败 context 经 /model、/provider 后仍为同一对象，
/retry 使用原快照继续，两个工具没有重复执行。

请求 2 的实际请求包含库编码的空 reasoning_content，原非思考助手消息没有被原地修改。
Pro 本次返回 231 字符可见推理，K3 工具调用返回 139 字符可见推理；后续供应商均成功接收
混合历史。测试不要求每条开启思考的回复都包含可见推理。

## 真实 UI、持久化与用户隔离

全部步骤在同一个 AgentUI、AgentRuntime 和编译图中完成，供应商/模型切换不清空上下文。

1. 初始用户名界面 `/list-user` 显示空列表；登录第一个临时用户。
2. 完成上述模型、工具审批和重试流程；最后一轮截断内容未保存。
3. 实际 `/history` 选择界面恢复同一对话，执行 `/transcript` 和 `/output`。
4. `/logout` 返回用户名界面；`/list-user` 能看到第一个用户。
5. 登录第二个用户，`/retry` 正确提示没有可重试运行，`/history` 为空。
6. 再次 `/logout`，`/list-user` 显示两个用户；登录原用户，实际 `/history` 再次打开原对话，
   `/transcript` 返回相同消息，最终 quit。

SQLite 中仅原用户有一段对话，14 条消息、五条用户输入：
`user → assistant → tool → assistant → user → assistant → user → assistant → tool → assistant → user → assistant → user → assistant`。
两次历史恢复的完整消息相等；没有重复用户消息、工具结果或保存截断回复。
新用户无法看到原用户历史；退出只重置活动状态，保留用户列表和已有对话。

## 隐私与验证边界

凭证通过隐藏终端输入注入进程环境，未写入脚本、项目、配置、SQLite、终端记录文件或报告。
临时设置目录、数据库、脚本已删除。

本次“全量”指全部本地测试和上述应用端到端流程；在线覆盖已有凭证的 DeepSeek/Moonshot
四个模型。OpenAI、Anthropic、Gemini、Ollama 使用真实官方 SDK 的离线模拟传输验证，
没有凭证或本地模型，因此未声称对全部六供应商、18 模型都发起真实推理。
全部推理强度、真实断网/供应商故障、跨协议签名/加密元数据没有穷举在线验证。
