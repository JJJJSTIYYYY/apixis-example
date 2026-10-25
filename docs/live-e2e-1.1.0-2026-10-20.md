# MyLLM 1.1.0 对齐与端到端测试（2026-10-20）

## 配置与资源生命周期

- pyproject 最低版本和 uv.lock 对齐 1.1.0，实际安装版本为 1.1.0。
- 按本地源码 docs/reference/api.md、configuration.md 和已安装发行包迁移：
  连接、能力、extra_body、强度映射及思考回放改用公开 Config；旧 reasoning_enable_parameter
  改用 reasoning_enable Parameter，声明路径、默认值和 decode 阶段。
- DeepSeek/Kimi thinking 路径为 $.extra_body.thinking，Anthropic 为 $.thinking；
  K3 在模型层 omit 旧开关，K2.x 不发送不支持的 reasoning_effort。
- 所有生产输出预算直接放在 ModelProfile.extra_body 的 Config 字典中，
  DeepSeek/K2.x/Anthropic 使用 max_tokens，K3 使用 max_completion_tokens。
- stream_reply 使用 async with MyLLM，先关闭请求流，再关闭实例缓存的 SDK 客户端。
  没有增加 HTTP/SSE、工具拼接、reasoning_content 补写或 finish_reason 映射实现。
- 不清空切换时的对话、工具结果和失败快照。预设模型目录沿用此前已核验配置，未新增模型。

## 离线验证

80 项 unittest 全部通过，覆盖六供应商、18 预设配置、真实官方 SDK 序列化和模拟传输，
标准 Chat/Responses/Anthropic/Gemini/Ollama、工具历史、思考回放、重试、历史持久化、
登录退出、用户隔离，以及独立进程的 160 路并发图与工具事件。

调用助手在 finally 检查所有 HTTPX 客户端关闭，涵盖成功、HTTP 错误、协议错误、
输出截断、显示回调异常和取消。Responses SDK 异常实际经过 explain_error，输出预算提示
与 /retry 建议正常。Ruff、CLI --check/--help 和 git diff --check 另行检查。

## 最终在线流程

真实 chat_loop、AgentUI、Rich、一个 AgentRuntime/编译图、Apixis 审批事件、shell、
MyLLM 和 SDK；只脚本化键盘输入与审批按键，响应未模拟。
在临时空工作目录中运行，设置和数据库隔离，终端内容仅保存在进程内。
审批只允许精确的 printf sdk110_ds_17 和 printf sdk110_k3_37，两条命令各执行一次。

| 请求 | 模型与路径 | HTTP | finish_reason | 输入 tokens | 输出 tokens |
| --- | --- | ---: | --- | ---: | ---: |
| 1 | Flash 非思考，发起第一个工具 | 200 | tool_calls | 508 | 44 |
| 2 | 同模型开启思考，从工具提交后快照 /retry | 200 | stop | 622 | 8 |
| 3 | /model 切换 V4 Pro，沿用历史 | 200 | stop | 634 | 8 |
| 4 | /provider 切换 K3，发起第二个工具 | 200 | tool_calls | 639 | 98 |
| 5 | 切回 Flash 思考，从 K3 工具快照 /retry | 200 | stop | 812 | 8 |
| 6 | 切换 K2.6 非思考，沿用混合历史 | 200 | stop | 513 | 15 |
| 7 | Flash 非思考，1 token 上限触发截断 | 200 | length | 796 | 1 |
| 8 | 切换 K3，对截断快照 /retry，得到 4 | 200 | stop | 904 | 37 |
| 合计 | | | | 5,428 | 219 |

最终流程原生 usage 共 5,647 tokens，推理 tokens 已包含在输出中，不重复计算。
开启思考不要求每次出现可见思考；同 Flash、Pro 的本次回复 reasoning_tokens 为 0。

在两个工具提交后的下一次模型调用前分别注入一次本地异常，不消耗 API 请求。
通过 /model 和 /provider 时断言 last_context 对象保持不变；恢复后工具没有重跑。
截断由真实服务返回 length，应用保留失败快照，最终算术答案正确且不保存截断回复。

继续执行真实 /history 选择、/transcript、/output、/logout、用户名 /list-user，
第二用户 /retry 无快照且历史为空，再登录首用户恢复历史。
最终 SQLite 一段对话、14 条消息、两个工具结果；第二用户无对话，两个用户名均保留。
预期错误共四次：两次注入故障、一次真实截断、退出后无可重试状态。

临时 Profile：DeepSeek/Moonshot CN 端点，timeout 45、SDK 重试 0，Chat usage_mode 为
stream_options；Flash/Pro/K3 上限 512，K2.6 为 128，思考使用 low。
预算通过 Config 包装的 extra_body 直接传入；仓库默认预算、模式及 Kimi 国际地址保持原样。

## 额外测试用量与库侧问题

前两次脚本启动因临时 usage_mode 配置错误，在创建 MyLLM 时中止，零网络请求。
首轮在线脚本把同一工具快照重复注入本地故障，目标重试未完全覆盖，不能算通过：
其 7 次真实请求均 HTTP 200，共输入 4,979、输出 599、合计 5,578 tokens。
修正为每个工具结果只注入一次后，才得到上面的完整通过结果。
**本次所有在线测试共 15 次请求、11,225 tokens**，不是账单金额。

离线发现 1.1.0 的 Responses 异常 details 是内部字典视图：
按键 get、迭代和 dict(details) 保留完整 reason/code/message，
但直接与普通字典比较不相等，json.dumps(details) 得到 {}。
现有 UI 按键读取 reason，真实 SDK 模拟响应下输出预算提示正常；
测试按公开映射内容检查，并验证 UI 提示，没有在应用添加私有类型识别或运行时修补。
这是库的异常详情普通字典行为限制，若下游直接 JSON 序列化异常详情需在库侧修复。

密钥通过隐藏终端输入，仅注入测试进程环境，不写入代码、配置、数据库、文档或报告。
临时脚本和临时状态目录在测试后删除。没有提交或推送本次修改。

OpenAI、Anthropic、Gemini、Ollama 仅离线验证；本次不声称全部供应商/模型均在线测试，
也未穷举跨协议签名、加密思考等原生能力。
