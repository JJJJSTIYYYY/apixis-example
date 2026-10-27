# 验证说明

当前锁定 Apixis 1.1.5 与 my-llm-py 1.3.2。本文说明本地检查方法、自动化覆盖和可选的真实供应商测试。

## 本地验证

在项目根目录执行：

```bash
uv sync --locked
uv run python -m unittest discover -s tests -q
uv run example --check
uv run example --help
uv build
```

依赖已缓存时可用 `uv sync --locked --offline`。自动化测试和 `--check` 不需要真实密钥，
不会访问付费接口；`--check` 执行工作目录子图，验证本地图调度。

需要代码与格式检查时，通过 uv 临时运行 Ruff：

```bash
uvx ruff check --isolated --select E4,E7,E9,F,I src tests
uvx ruff format --check src tests
git diff --check
```

## 自动化覆盖

| 范围 | 覆盖内容 |
| --- | --- |
| 图与恢复 | 并发子图、状态隔离、步数限制、快照裁剪、失败恢复时跳过已提交工具 |
| 审批与事件 | 独立 Block 审批、并发流归属、批准与拒绝、取消与界面异常、回复超时和订阅清理 |
| 背压 | 独立进程配置背压 128、队列 8，执行 160 路并发 |
| 模型接入 | 六供应商官方 SDK 的模拟传输、Profile 与认证、完整消息、分片工具、断流及资源关闭 |
| 用量 | 完成请求回调、缺失值与零值、最近 20 条上限、不进入历史或快照、退出用户时清理 |
| 历史与界面 | 用户隔离、SQLite 保存、失败重试、长输出与窄窗口、模型名原样显示 |

测试中的预期故障注入可能输出错误日志，以 unittest 的最终结果判断通过或失败。
Apixis 核心在同一进程中共用事件循环；独立配置通过子进程验证。

## 可选真实供应商验证

```bash
# DeepSeek and Kimi China; enter both credentials at the hidden prompts.
uv run python tests/scenarios/live_upgrade.py --kimi-region cn

# Only Kimi China.
uv run python tests/scenarios/live_upgrade.py --provider kimi --kimi-region cn

# Kimi global uses the default region.
uv run python tests/scenarios/live_upgrade.py --provider kimi
```

脚本通过隐藏标准输入读取凭证，不保存凭证、设置、对话或日志。每家最多两次生成请求，
只批准固定命令 `printf APIXIS_UPGRADE_OK`，验证延迟审批、工具后失败恢复、两条快照保留和用量。
每次请求超时 35 秒，输出预算 256 token，SDK 重试为 0，整个场景限时 180 秒。
脚本的测试配置关闭 thinking，生产预设沿用原有设置。

`kimi-cn` 使用中国站连接，`kimi` 使用国际站连接，按密钥所属站点选择。
离线模拟传输覆盖不代表真实账户权限、所有预设模型或所有协议组合均已在线验证。

## 接入细节

- [Apixis 图、事件与审批](guides/apixis-coverage.md)
- [my-llm-py 配置、流与协议边界](guides/my-llm-coverage.md)
- [模型预设与官方来源](guides/model-presets.md)
