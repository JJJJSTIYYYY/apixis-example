# Apixis 接口覆盖

核查依据为锁定依赖 `apixis==1.1.1` 的源码和公开接口。没有修改 Apixis 本身。

## 已实现的使用场景

| 核心能力 / 接口 | 原项目 | 当前使用场景 | 实现位置 |
| --- | --- | --- | --- |
| `start_core()` | 已使用 | CLI 启动共享事件核心 | `__init__.py` |
| `GraphManager`、`add_node()`、`compile_graph()` | 已使用 | 主 agent 图与独立工作目录子图 | `core/agent.py`、`core/workspace.py` |
| `Command.goto` 字符串、`None` | 已使用 | ReAct 循环与正常结束 | `core/agent.py` |
| 并发入口 `entry_point=[...]` 与相同下一跳合流 | 未使用 | 并发目录/运行环境采集；两分支合流到 summary | `core/workspace.py` |
| `AutoMerge()` | 已使用 | 对话消息追加；子图的两个结果按声明顺序合并 | `core/agent.py`、`core/workspace.py` |
| `Reset()` | 未使用 | prepare 阶段覆盖可追加的工作目录上下文 | `core/agent.py` |
| `invoke()`、嵌套图调用 | 未使用 | 主图调用工作目录子图；本地 `--check` 直接调用子图 | `core/agent.py`、`__init__.py` |
| `create_context()`、`stream()`、`get_stream_writer()` | 已使用 | 准备每轮运行并流式展示内容与工具进度 | `core/runtime.py`、`core/agent.py` |
| `GraphContext.stream_writer()` | 已使用 | 输入过滤插件直接发送拒绝消息，由应用统一消费流 | `plugins/plugins.py`、`app.py` |
| `get_snapshot()`、`get_all_snapshots()` | 未使用 | `/checkpoints` 查看最近运行；选择最新失败快照 | `core/runtime.py`、`app.py` |
| `restore_context()` | 未使用 | `/retry` 恢复最新快照，保留目标节点和步数 | `core/runtime.py` |
| `GraphContext.status`、`run_id`、`graph_id` | 部分使用 | 决定历史是否提交、关联运行、验证快照归属 | `core/runtime.py`、`app.py` |
| `NodeGraph.abort()`、关闭 stream | 未使用 | 程序接入时中止；关闭 wrapper 同时关闭底层流 | `core/runtime.py` |
| 节点 `timeout`、`set_max_steps()` | 未使用 | 约束目录采集、模型请求和工具循环 | `core/agent.py`、`core/workspace.py` |
| `interrupt()`、`add_interrupted_hook()`、`Block.resolve()` | 已使用 | 每个工具独立审批；守卫拒绝时直接返回 False | `core/agent.py`、`core/runtime.py` |
| `ApixEventHandler`、`subscribe()`、`between_handlers`、`event.accept()` | 已使用 | 输入过滤在主图 dispatch 前拦截 | `plugins/plugins.py` |
| `get_event_pipe().post_event()`、`EventType` | 应用层未直接使用 | 自定义工具请求/回复与运行生命周期事件 | `core/events.py` |
| `wait_for_event(..., point="processed")`、`EventWaiter.wait()` | 未使用 | 先订阅再发布工具请求，按唯一回复事件名等待结果；退出上下文自动注销 | `core/events.py` |
| `get_graph_context()`、`get_current_run_id()`、`get_current_namespace()` | 未使用 | 请求关联与中止后跳过尚未开始的工具 | `core/events.py` |
| 通配订阅、`background=True` | 未使用 | 观察自有事件，汇总 `/stats` 计数 | `core/events.py` |
| handler 的 `on_error` 回调 | 未使用 | 工具 worker 异常时发布错误回复，防止调用方一直等 | `core/events.py` |
| `unsubscribe()`、`decompose()`、事件组件 `stop()` | 不完整 | 已使用图分解，插件注销和事件核心停止未接入退出流程 | `core/runtime.py` |

## 保留为替代用法的接口

没有为了数量强行调用所有底层方法：

| 未直接使用的接口类别 | 原因 / 可扩展方向 |
| --- | --- |
| `ParallelNode` | 当前子图已用并发节点入口展示相同的分支与合流能力；可以在需要把分支封装为一个命名节点时采用 |
| `KeepRef` | 模型配置、事件服务和子图通过模块或闭包持有，运行资源不会进入 state，也不会随快照深拷贝；无需引入共享可变 state |
| `NodeGraph.apply_command()`、`take_a_snapshot()` 手动调用 | 命令提交与节点前快照由引擎自动执行，业务不重复执行 |
| `Block.accept()`、`fail()`、`cancel()` | 示例使用即时人工决策，没有把审批交给外部系统延迟完成的需求 |
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

`runtime.select_model("kimi", "your-model-id")` 可切换后续 LLM 调用，包括从失败快照
恢复后的调用。该操作保留当前图、`last_context` 和快照，不改写已有消息或工具结果，
用于测试 my-llm-py 的跨供应商历史兼容性。交互界面的 `/provider`、`/model` 使用此接口。
