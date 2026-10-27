# Apixis 接口覆盖

核查依据为 PyPI 安装的 `apixis==1.1.5` 和当前公开接口文档。没有修改 Apixis 本身。

## 已实现的使用场景

| 核心能力 / 接口 | 当前使用场景 | 实现位置 |
| --- | --- | --- |
| `start_core()` | CLI 启动共享事件核心 | `__init__.py` |
| `GraphManager`、`add_node()`、`compile_graph()` | 主 agent 图与独立工作目录子图 | `core/agent.py`、`core/workspace.py` |
| `Command.goto` 字符串、`None` | ReAct 循环与正常结束 | `core/agent.py` |
| 并发入口 `entry_point=[...]` 与相同下一跳合流 | 并发目录/运行环境采集；两分支合流到 summary | `core/workspace.py` |
| `AutoMerge()` | 对话消息追加；子图的两个结果按声明顺序合并 | `core/agent.py`、`core/workspace.py` |
| `Reset()` | prepare 阶段覆盖可追加的工作目录上下文 | `core/agent.py` |
| `invoke()`、嵌套图调用 | 主图调用工作目录子图；本地 `--check` 直接调用子图 | `core/agent.py`、`__init__.py` |
| `create_context()`、`stream()`、`get_stream_writer()` | 准备每轮运行并流式展示内容与工具进度 | `core/runtime.py`、`core/agent.py` |
| `GraphContext.stream_writer()` | 输入过滤插件直接发送拒绝消息，由应用统一消费流 | `plugins/plugins.py`、`app.py` |
| `get_snapshot()`、`get_all_snapshots()` | `/checkpoints` 查看最近运行；选择最新失败快照 | `core/runtime.py`、`app.py` |
| `compile_graph(max_steps=..., max_snapshots=...)` | 编译时设置运行预算；`--max-snapshots` 控制保留条数，裁剪后仍可重试最新失败节点 | `core/runtime.py`、`__init__.py` |
| `compile_graph(no_snapshot=True)` | 临时环境子图不积累恢复历史，主图继续保留快照 | `core/workspace.py` |
| `restore_context()` | `/retry` 恢复最新快照，保留目标节点和步数 | `core/runtime.py` |
| `GraphContext.status`、`run_id`、`graph_id` | 决定历史是否提交、关联运行、验证快照归属 | `core/runtime.py`、`app.py` |
| `NodeGraph.abort()`、关闭 stream | 程序接入时中止；关闭 wrapper 同时关闭底层流 | `core/runtime.py` |
| 节点 `timeout`、编译参数 `max_steps` | 约束目录采集、模型请求和工具循环 | `core/agent.py`、`core/workspace.py` |
| `interrupt()`、`add_interrupted_hook()`、`Block.accept/resolve/fail/cancel` | 将审批 Future 迁移为公开 Block；延迟到对应运行的流消费者处理，支持界面失败和取消 | `core/agent.py`、`core/runtime.py`、`app.py` |
| `ApixEventHandler`、`subscribe()`、`between_handlers`、`event.accept()` | 输入过滤在主图 dispatch 前拦截 | `plugins/plugins.py` |
| `get_event_pipe().post_event()`、`EventType` | 自定义工具请求/回复与运行生命周期事件 | `core/events.py` |
| `wait_for_event(..., point="processed")`、`EventWaiter.wait()` | 先订阅再发布工具请求，按唯一回复事件名等待结果；退出上下文自动注销 | `core/events.py` |
| `get_stream_writer()` 自定义数据 | 发送独立的 typed `Usage` 用量事件，不写入 state、历史或快照 | `core/runtime.py` |
| `get_graph_context()`、`get_current_run_id()`、`get_current_namespace()` | 请求关联与中止后跳过尚未开始的工具 | `core/events.py` |
| 通配订阅、`background=True` | 观察自有事件，汇总 `/stats` 计数 | `core/events.py` |
| handler 的 `on_error` 回调 | 工具 worker 异常时发布错误回复，防止调用方一直等 | `core/events.py` |
| `unsubscribe()`、`decompose()`、事件组件 `stop()` | 关闭会话时注销插件与事件服务、分解图；CLI 退出时停止共享核心 | `core/runtime.py` |

## 保留为替代用法的接口

没有为了数量强行调用所有底层方法：

| 未直接使用的接口类别 | 原因 / 可扩展方向 |
| --- | --- |
| `ParallelNode` | 当前子图已用并发节点入口展示相同的分支与合流能力；可以在需要把分支封装为一个命名节点时采用 |
| `KeepRef` | 模型配置、事件服务和子图通过模块或闭包持有，运行资源不会进入 state，也不会随快照深拷贝；无需引入共享可变 state |
| `NodeGraph.apply_command()`、`take_a_snapshot()` 手动调用 | 命令提交与节点前快照由引擎自动执行，业务不重复执行 |
| `EventWaiter.keep_listening`、`get_result()` | 当前 shell 执行上限 30 秒，回复等待上限 40 秒；超时即失败并清理监听，不保留后台未完成任务 |
| handler 动态替换和额外生命周期回调 | 示例保留明确的注册/注销关系；worker 的错误回传使用 `on_error`，不人为制造其他回调场景 |
| `EventCore` 的手动装配、`BuiltinChannel` 与 registry 的直接变更 | 应用复用工厂构建的共享核心和内置通道；不另造一套队列或调度器 |
| `aget_*`、handler 元数据查询、手动命名空间管理等便利接口 | 与已使用的 getter、订阅和编译生命周期重复；不是这个 agent 需要另设功能的执行能力 |

## 程序接入

同一个进程里的共享核心应运行在同一个 asyncio event loop 上。CLI 使用一次 `asyncio.run()`；测试也让核心保持在同一 loop，独立配置场景使用独立进程。

```python
import asyncio
from contextlib import aclosing
from apixis import start_core
from example.core.runtime import AgentRuntime, shutdown_core

async def main():
    await start_core()

    async def approve(tool_call):
        # Replace with an application-owned approval UI.
        return False

    runtime = AgentRuntime(approve)
    try:
        async with aclosing(runtime.stream([
            {"role": "system", "content": "Reply briefly."},
            {"role": "user", "content": "Hello"},
        ])) as stream:
            async for chunk in stream:
                print(chunk)
        # Another task can await runtime.abort() while this session is running.
    finally:
        runtime.close()
        await shutdown_core()

asyncio.run(main())
```

一个 `AgentRuntime` 的 `last_context` 表示该会话最近的尝试。需要分别跟踪多段会话时创建多个 runtime；它们共用事件核心，但 handler 名称与事件命名空间相互隔离。`abort()` 按 Apixis 契约立即终止调用等待，已经开始的节点工作可能继续，不等同于撤销已发生的 shell 副作用。

省略 `AgentRuntime` 的审批回调时，流中的 `type="approval"` 提供已接管的 `block`，消费者必须调用 `block.resolve(bool)`、`fail(error)` 或 `cancel()`。调用方仍可传入异步布尔回调用于自动决策。审批数据中的 writer 来自原工具节点，仅存在中断载荷中；不使用 `last_context` 路由并发运行，也不将 writer 或 Block 放入图 state。

1.1.5 中每次 `waiter.wait()` 独立设置预算，省略 `time_out` 表示无限等待。因此工具回包处明确调用 `waiter.wait(time_out=40)`，等待预算在发布请求后开始。

`runtime.select_model("kimi", "your-model-id")` 可切换后续 LLM 调用，包括从失败快照
恢复后的调用。该操作保留当前图、`last_context` 和快照，不改写已有消息或工具结果，
用于测试 my-llm-py 的跨供应商历史兼容性。交互界面的 `/provider`、`/model` 使用此接口。

验证方法见[验证说明](../validation.md)，完整文档入口见[文档索引](../README.md)。
