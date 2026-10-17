# my-llm-py 1.0.0 升级验证（2026-10-17）

使用从 PyPI 安装的 my-llm-py 1.0.0，锁文件与环境均已更新。六个供应商的接入通过
官方 SDK 配合本地模拟传输验证，未发起付费请求。77 项 unittest（包含独立进程的
160 路背压测试）、CLI `--check` / `--help` 和基础静态检查均通过。当前接口与测试范围见
[1.0 接入说明](my-llm-coverage.md)。以下保留此前版本的验证记录，旧版 MessageChunk、
统一消息元数据和推理回放行为不适用于 1.0。

---

# 验证结果

验证日期：2026-10-10。环境：Linux x86_64、Python 3.14.8、锁定的 Apixis 1.1.0。没有修改 Apixis 依赖源码。

| 项目 | 结果 |
| --- | --- |
| `uv sync --locked` | 通过；项目安装为 `example==0.1.0` |
| `uv run example --help` | 显示新命令及单次运行参数 |
| `uv run example --check` | 并发工作目录子图完成，无 API 请求 |
| `uv run python -m example --check` | 模块启动通过 |
| `/help` 与未知命令提示 | 本地交互验证通过；显示全部命令和审批说明，无 API 请求 |
| `uv run example` 启动及退出 | 临时用户名目录、测试占位 Key，输入用户名后退出；无 API 请求 |
| `uv run python -m unittest discover -s tests -v` | 35 项全部通过 |
| 事件背压与队列饱和 | 独立进程中 160 路并发，背压 128、队列 8；全部完成且回复未串线 |
| 代码检查 | `ruff check --isolated --select E4,E7,E9,F,I src tests` 通过 |
| 格式检查 | `ruff format --check src tests` 通过 |
| `uv build` | wheel 与源码包构建成功；wheel 的入口为 `example = example:main` |
| 真实 API 冒烟测试（前一版） | 单次 agent 运行返回 `OK`；仅一次成功的付费请求，输出上限 24 tokens；本次 UI 改进没有追加请求 |
| Linux 真实 PTY：长流式回复 | 在 38×14 和 80×24 窗口中，120 行回复在最终滚动记录中各出现一次 |
| Linux 真实 PTY：工具审批 | 上箭头不会触发拒绝；`y` 批准、单独 Esc 拒绝；两种操作后终端设置均恢复 |

## 自动化覆盖

测试使用真实 Apixis 图与事件核心，替换 LLM 响应和部分工具执行，不调用付费接口。

- 普通流式对话、临时工作目录上下文和快照深拷贝隔离。
- 工作目录两分支互相等待，验证确实并发执行，且按声明顺序合流。
- 输入过滤在任何节点与 LLM 请求之前中止。
- 两个工具独立审批/提交后，LLM 故障；从失败快照恢复时工具没有重复执行。
- 用户拒绝与命令守卫拒绝均不执行工具，守卫原因传回工具结果。
- worker 故障通过 `on_error` 回传，调用方及时完成，不等到 40 秒超时。
- 并发回复使用独立事件名称，结果正确关联调用。
- 中止和关闭流使运行结束，迟到节点结果不能提交。
- 不同 runtime 的 handler 名称与注销互相隔离。
- 最大步数终止工具循环。
- 真实应用交互循环经过失败、重试和第二轮对话，SQLite 每轮只保存一条用户消息。
- 非法工具 JSON 返回错误；真实 `printf` shell 工具执行成功；取消运行时子进程被回收。
- SSE 文本、分片 tool calls 拼接、HTTP 错误、流意外断开、输出额度耗尽和缺失凭证。
- 真实应用流中，说明文字、两次工具审批、各自执行结果和最终回复依次显示，审批不会抢在已排队的内容前面。
- 38 列帮助和欢迎界面不超宽；用户名和历史标题中的方括号按字面显示。
- 工具长输出折叠后可查看全文；非零退出码显示执行未成功，拒绝显示跳过。
- 历史输入错误重新提示，预览最近 3 轮但完整记录可查看；取消重命名和删除保持当前对话。
- ANSI 控制序列不控制输出界面；审批中的命令控制字符显示为可见转义，避免隐藏执行内容。
- 缺失凭证、认证失败和步数上限有具体下一步提示，认证错误不显示服务端响应正文。

故障注入测试会产生一条预期的 worker 错误日志，这不代表测试失败。

## Apixis 1.1.1 接口迁移验证

验证日期：2026-10-11。环境：macOS、Python 3.14.0、锁定的 Apixis 1.1.1。

- 工具请求改用 `async with wait_for_event(...) as waiter`，进入上下文完成订阅后发布请求，再调用 `await waiter.wait()`；退出上下文自动清理订阅。
- `.venv/bin/python -m unittest discover -s tests -v`：41 项全部通过，无付费 API 请求。
- 新增验证：回复在请求发布返回之前已到达时仍可获取；请求发布失败时等待订阅被移除。
- 原有并发回复关联、worker 故障回传，以及背压 128、队列 8 下的 160 路并发均通过。
- `git diff --check` 通过。

## my-llm-py 多供应商接入验证

验证日期：2026-10-16。环境：macOS、Python 3.14.0、Apixis 1.1.1、my-llm-py 0.1.3。

- `.venv/bin/python -m unittest discover -s tests -v`：60 项全部通过，无真实供应商请求。
- 通过真实官方 SDK 和 `httpx.MockTransport` 检查 DeepSeek/Kimi Chat 请求、OpenAI
  Responses 工具转换及原生输出回放，以及 Anthropic/Gemini/Ollama 原生文本和工具请求。
- 检查输出预算字段、供应商凭证隔离、认证错误、流中断、显示异常和取消时的流关闭。
- SQLite 保存完整 Message，覆盖推理、原生输出和内容块的往返；原有数据库自动迁移，
  旧消息和新消息可以同时读取。
- 六种供应商的 CLI 启动横幅、用户名输入、`/list-user` 和 `/logout` 均通过离线冒烟检查；
  未设置凭证时提示对应环境变量。
- `python -m example --check` 通过，仍无需模型凭证。
- Ruff 代码及格式检查、`git diff --check` 通过。

这些验证未覆盖真实账户权限和线上模型可用性，未发起付费 API 调用。

## 命令切换与跨供应商兼容性验证

验证日期：2026-10-16。

- `/provider [供应商] [模型ID]` 和 `/model [模型ID]` 负责交互选择，`--provider`、
  `--model` 指定初始选择。模型配置仅从代码中的 Profile 获取，环境变量只提供凭证。
- `.venv/bin/python -m unittest discover -s tests -v`：65 项全部通过。
- 真实应用循环覆盖：完成一轮对话、执行工具后失败、切换模型、切换供应商、再 `/retry`。
  编译图和对话保持不变，旧快照与原生输出保留，已完成工具只执行一次。
- 官方 SDK 配合模拟传输覆盖 Kimi Chat → OpenAI Responses → DeepSeek Chat，验证
  工具调用 ID、工具结果及可见推理的回传和转换。
- 非法选择不会改变已有选择或失败快照；没有凭证时仍可启动并切换，发起请求时提示
  当前供应商的凭证变量。CLI 的初始选择参数和 `--check` 均通过。
- Ruff 代码与格式检查、`git diff --check` 通过；未发起真实供应商或付费请求。

## 官方模型预设核查

核查日期：2026-10-16；[预设与官方来源](model-presets.md)。

- 实际查阅六家官方模型文档，配置 18 个预设，默认 DeepSeek 改为 `deepseek-flash`。
- `.venv/bin/python -m unittest discover -s tests -v`：69 项全部通过。
- 新增实际 SDK 模拟请求检查：Kimi K3 仅发送原生 effort、DeepSeek 强度映射、
  当前 OpenAI 模型使用 Responses 函数工具。原生协议测试使用实际默认模型的 Profile，
  验证 Claude adaptive thinking、Gemini thinking level 和 Ollama 布尔 think 参数。
- 无凭证 CLI 检查通过：六家默认模型、`/model` 预设列表、命令切换和 `/retry` 提示。
- Ruff 代码和格式检查、`git diff --check` 通过，未发起付费请求或下载本地模型。

## 输出预算改为直接配置

- DeepSeek/Kimi 直接使用 `ModelProfile.extra_body.max_tokens`；删除了应用中按供应商
  将 `ModelProfile.max_tokens` 转成原生字段的分支，未新增默认输出限制。
- SDK 模拟传输验证直接配置产生原生 `max_tokens`，不产生 `max_completion_tokens`，
  原始 ModelProfile 及其 extra_body 保持不变。
- 移除依赖旧转换行为的校验测试后，68 项测试全部通过；Ruff 和 `git diff --check` 通过。

## 真实端到端验证

2026-10-16 使用用户授权的 DeepSeek、Moonshot 国内接口凭证进行验证，
[完整记录](live-e2e-2026-10-16.md)。两次模型列表查询与三次推理请求均返回 HTTP 200。

- 实际模型：`deepseek-flash`、`kimi-k2.6`；每次输出上限 128 tokens，SDK 重试为 0。
- 真实 shell 工具执行一次；在本地注入失败后，切换供应商并从原快照成功恢复。
- 切回 DeepSeek 后继续原对话，SQLite 单段历史保存及 `/history` 恢复均通过。
- 供应商 usage 合计输入 1,457、输出 62 tokens，未追加推理请求。
- 测试使用临时 Profile 和临时历史目录，凭证未持久化。

## 思考模式与 Kimi K3 真实续测

2026-10-16，[完整请求、消耗及限制记录](live-thinking-e2e-2026-10-16.md)。

- 本轮尝试发送 10 次推理请求；9 次取得 HTTP 200 和完整 usage，已知合计 5,911 tokens。
  另 1 次没有响应状态或 usage，未计入合计，未继续付费追查。
- 首组因测试脚本要求每条回复都有可见推理而误判；修正后补测 4 次，整组通过。
- K3 自身思考工具历史回放成功；DeepSeek 思考工具历史切换 K3 并从失败快照重试成功。
- 真实 `finish_reason=length` 后切换 K3 重试成功，没有重复工具、用户消息或保存截断回复。
- 修正截断错误提示，明确可切换模型重试；文档区分 K3 的原生 `max_completion_tokens`
  与 DeepSeek/Kimi K2.x 的 `max_tokens`，两者均直接放入 ModelProfile.extra_body。
- 新增 K3 原生预算字段和截断重试提示的离线验证；70 项单元测试全部通过。

## 非思考 → 思考 → 供应商/模型切换

2026-10-16，[完整记录与兼容性限制](live-mode-switch-e2e-2026-10-16.md)。

- 单一对话、runtime 和编译图；7 次真实推理请求，6 次 HTTP 200、1 次 HTTP 400。
  6 个有 usage 的响应合计 4,013 tokens，400 无 usage，不估算计费。
- 完成非思考回复后开启思考，工具调用后切换 DeepSeek Flash → V4 Pro 并 `/retry`，
  再切换 Kimi K3 继续原对话，均通过。
- 非思考工具轮尚未完成时直接开启 DeepSeek 思考模式，400 要求回传 reasoning_content。
  原消息没有该内容，当前 MyLLM 未自动兼容；原快照切换 Kimi K3 后成功恢复。
- 两个真实 shell 工具各执行一次；单段 SQLite 历史 12 条，四条用户输入没有重复。
- 未增加协议兼容补丁。新增实际 SDK/模拟传输的失败恢复回归；71 项测试通过，
  Ruff 代码及格式检查、`git diff --check` 通过。

## MyLLM 0.1.4 对齐与失败场景复测

2026-10-16，[完整复测记录](live-retest-0.1.4-2026-10-16.md)。安装版本、项目依赖和
锁文件均已是 0.1.4；依照本地 reference 文档更新完成原因的消费方式。

- 应用只判断统一 `finish_reason == "length"`，删除原生 `max_tokens` 别名分支。
- 原生 SDK 模拟传输验证正常结束、长度截断、工具调用的统一原因，以及特殊原因保留；
  长度截断仍抛 StreamError，partial_message 保留统一完成原因。
- 原先失败的非思考工具轮开启 DeepSeek 思考模式后 `/retry`，真实请求返回 HTTP 200。
  MyLLM 自动在请求中补空 reasoning_content，应用历史未改写，工具没有重复执行。
- 真实长度截断后切换 Kimi K3 并 `/retry` 仍通过；单段 SQLite 六条消息，没有保存截断回复。
- 共 4 次真实请求，均 HTTP 200；原生 usage 合计输入 2,243、输出 98，共 2,341 tokens。
- 73 项单元测试全部通过；Ruff 代码/格式和 `git diff --check` 通过。

## MyLLM 1.0.0 真实端到端回归

2026-10-17，已安装 1.0.0 发行包，运行用户更新后的应用；
[完整记录](live-e2e-1.0.0-2026-10-17.md)。

- 77 项离线测试通过；Ruff 代码及格式、本地 `--check`、`git diff --check` 通过。
- 最多 6 次的真实请求全部 HTTP 200，原生 usage 合计输入 3,815、输出 196，
  总计 4,011 tokens。没有 SDK 自动重试或额外模型列表查询。
- 非思考工具轮提交后注入本地失败，同模型开启思考并 `/retry` 成功；
  随后切换 DeepSeek V4 Pro 和 Kimi K3，完整上下文保持可用。
- 真实 length 截断后切换 K3 重试成功，截断回复未保存，工具仅执行一次。
- 六次均收到独立 `choices=[]` usage 尾块；完成原因从流事件读取，
  `stream.result` 和 SQLite 保存标准消息，不再额外包含完成原因和用量。
- 单个 runtime/编译图、单段历史 10 条消息、四条用户输入无重复。
- 没有修改应用实现或新增兼容层；临时 Profile、数据库及脚本均已清理，凭证未持久化。

## MyLLM 1.0.1 全量回归

2026-10-17，[完整记录](live-e2e-1.0.1-2026-10-17.md)。项目最低版本和 uv.lock 均更新为
1.0.1，虚拟环境已同步；仅 MyLLM 的锁定版本变化，核对的公共接口调用签名未变化。

- 修正依赖 MyLLM 内部 SDK 导入位置的测试助手，改用 HTTPX 公开 transport 注入。
- 按 1.0.1 的新默认值，为 DeepSeek/Kimi 显式配置 thinking 参数绑定和思考回传；
  不可表示为标准 Chat 的完成原因按库公开的 ResponseFailedError 验证，不新增应用映射。
- 80 项离线测试全部通过，包括六供应商/18 模型配置、SDK 模拟请求、流关闭、失败/取消、
  原生工具历史与 SQLite 回放、用户界面和独立进程的 160 路背压测试。
- 8 次真实请求全部 HTTP 200，原生 usage 合计输入 5,531、输出 273，共 5,804 tokens。
  模型为 DeepSeek Flash、V4 Pro、Kimi K3、K2.6；没有自动重试或额外查询。
- 真实 UI 完成初始 `/list-user`、登录、两次工具审批、模式与模型/供应商切换、失败重试、
  length 恢复、`/history`、`/transcript`、`/output`、`/logout`、重新登录和用户隔离。
- 两条真实 shell 命令各执行一次；单段历史 14 条、五条用户输入无重复，截断回复未保存。
- Ruff 代码/格式、`--check`、`--help`、`git diff --check` 通过；无新增协议兼容实现，
  凭证未持久化，临时脚本/数据已清理。其余供应商仅通过离线 SDK 验证。
