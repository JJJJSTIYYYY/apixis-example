"""Exercise real Apixis graphs and events with deterministic, free LLM stubs."""

import asyncio
import inspect
import json
import tempfile
import unittest
from contextlib import aclosing
from functools import wraps
from pathlib import Path
from unittest.mock import AsyncMock, patch

from apixis import (
    ApixEventHandler,
    get_event_pipe,
    get_handler_registry,
    start_core,
    subscribe,
    unsubscribe,
)

from example.app import chat_loop, run_prompt
from example.core import agent, history, llm, settings, tools, workspace
from example.core.runtime import AgentRuntime, shutdown_core

MESSAGES = [
    {"role": "system", "content": "Test agent"},
    {"role": "user", "content": "hello"},
]


def tool_call(call_id="call-1", command="printf apixis"):
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": "run_command",
            "arguments": json.dumps({"command": command}),
        },
    }


async def approve(call):
    return True


async def collect(runtime, messages=MESSAGES, *, context=None):
    async with aclosing(
        runtime.stream(None if context else messages, context=context)
    ) as stream:
        return [chunk async for chunk in stream]


class SharedLoopTests(unittest.TestCase):
    """A process-global Apixis core lives on one event loop, as in the CLI."""

    @classmethod
    def setUpClass(cls):
        cls.runner = asyncio.Runner()

    @classmethod
    def tearDownClass(cls):
        cls.runner.close()

    def __init_subclass__(cls):
        super().__init_subclass__()
        for name, method in list(vars(cls).items()):
            if name.startswith("test_") and inspect.iscoroutinefunction(method):

                @wraps(method)
                def run(self, method=method):
                    return self.runner.run(method(self))

                setattr(cls, name, run)

    def setUp(self):
        self.runner.run(self.asyncSetUp())

    def tearDown(self):
        self.runner.run(self.asyncTearDown())


class AgentTests(SharedLoopTests):
    async def asyncSetUp(self):
        await start_core()
        self.runtimes = []

    def runtime(self, callback=approve, **kwargs):
        runtime = AgentRuntime(callback, **kwargs)
        self.runtimes.append(runtime)
        return runtime

    async def asyncTearDown(self):
        for runtime in self.runtimes:
            runtime.close()
        await llm.close_client()
        await asyncio.wait_for(get_event_pipe().join(), 5)
        await shutdown_core()

    async def test_stream_workspace_and_snapshot_isolation(self):
        async def reply(messages, tools, on_text):
            self.assertIn("工作目录", messages[1]["content"])
            on_text("hello")
            return {"role": "assistant", "content": "hello"}

        runtime = self.runtime()
        with patch.object(agent, "stream_deepseek", reply):
            chunks = await collect(runtime)
        self.assertEqual(chunks, [{"type": "content", "text": "hello"}])
        context = runtime.last_context
        self.assertEqual(context.status, "finished")
        self.assertEqual(len(context.state["messages"]), 3)
        self.assertEqual(MESSAGES[-1]["content"], "hello")
        snapshots = context.get_all_snapshots()
        self.assertEqual(
            [s["target_node_name"] for s in snapshots],
            ["prepare", "workspace", "agent"],
        )
        snapshots[-1]["state"]["messages"].clear()
        self.assertEqual(len(context.get_snapshot()["state"]["messages"]), 2)
        self.assertEqual(len(context.state["workspace_notes"]), 1)

    async def test_workspace_branches_run_concurrently_and_join_once(self):
        started = [asyncio.Event(), asyncio.Event()]

        async def branch(index):
            async def node(state):
                from apixis import Command

                started[index].set()
                await asyncio.wait_for(started[1 - index].wait(), 2)
                return Command(update={"notes": [str(index)]}, goto="summary")

            return node

        with (
            patch.object(workspace, "directory_node", await branch(0)),
            patch.object(workspace, "environment_node", await branch(1)),
        ):
            graph = workspace.build_workspace_graph()
            try:
                result = await asyncio.wait_for(graph.invoke({"notes": []}), 3)
                self.assertEqual(result["notes"], ["0", "1"])
                self.assertEqual(result["summary"], "0\n1")
            finally:
                graph.decompose()

    async def test_filter_rejects_before_any_node_or_api(self):
        runtime = self.runtime()
        stub = AsyncMock()
        with patch.object(agent, "stream_deepseek", stub):
            self.assertEqual(
                await collect(
                    runtime, MESSAGES[:1] + [{"role": "user", "content": "炸弹"}]
                ),
                [
                    {
                        "type": "error",
                        "message": "这条输入被当前过滤规则拦截：炸弹。",
                        "hint": "请调整表达后重新发送，本轮未保存。",
                    }
                ],
            )
        stub.assert_not_awaited()
        self.assertEqual(runtime.last_context.status, "aborted")
        self.assertEqual(
            runtime.last_context.state,
            {
                "messages": MESSAGES[:1] + [{"role": "user", "content": "炸弹"}],
                "tool_calls": [],
                "workspace_notes": [],
            },
        )
        self.assertEqual(runtime.last_context.get_all_snapshots(), [])

    async def test_filter_stream_notifications_are_isolated_between_runs(self):
        runtime = self.runtime()
        inputs = ["炸弹", "hello", "枪支、毒品"]
        contexts = [
            runtime.graph.create_context(
                {"messages": MESSAGES[:1] + [{"role": "user", "content": text}]}
            )
            for text in inputs
        ]

        async def reply(messages, tools, on_text):
            self.assertEqual(messages[-1]["content"], "hello")
            on_text("allowed")
            return {"role": "assistant", "content": "allowed"}

        with patch.object(agent, "stream_deepseek", reply):
            chunks = await asyncio.wait_for(
                asyncio.gather(*(collect(runtime, context=c) for c in contexts)), 5
            )
        self.assertEqual([c.status for c in contexts], ["aborted", "finished", "aborted"])
        self.assertEqual(len(chunks[0]), 1)
        self.assertEqual(chunks[0][0]["type"], "error")
        self.assertIn("炸弹", chunks[0][0]["message"])
        self.assertEqual(chunks[1], [{"type": "content", "text": "allowed"}])
        self.assertEqual(len(chunks[2]), 1)
        self.assertEqual(chunks[2][0]["type"], "error")
        self.assertIn("毒品、枪支", chunks[2][0]["message"])

    async def test_filter_also_aborts_non_streaming_invocations(self):
        runtime = self.runtime()
        context = runtime.graph.create_context(
            {"messages": MESSAGES[:1] + [{"role": "user", "content": "炸弹"}]}
        )
        stub = AsyncMock()
        with patch.object(agent, "stream_deepseek", stub):
            await asyncio.wait_for(runtime.graph.invoke(graph_context=context), 3)
        stub.assert_not_awaited()
        self.assertEqual(context.status, "aborted")
        self.assertEqual(context.get_all_snapshots(), [])

    async def test_run_prompt_reports_streamed_filter_rejection(self):
        stub = AsyncMock()
        with (
            patch.object(llm, "DEEPSEEK_API_KEY", "test-placeholder"),
            patch.object(agent, "stream_deepseek", stub),
            patch("builtins.print"),
        ):
            with self.assertRaisesRegex(RuntimeError, "过滤规则拦截：炸弹"):
                await asyncio.wait_for(run_prompt("炸弹"), 3)
        stub.assert_not_awaited()

    async def test_approved_tools_commit_individually_and_retry_skips_them(self):
        calls = [tool_call("one"), tool_call("two")]
        attempts = 0

        async def reply(messages, tools, on_text):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return {"role": "assistant", "content": None, "tool_calls": calls}
            if attempts == 2:
                raise RuntimeError("transient LLM failure")
            return {"role": "assistant", "content": "done"}

        execute = AsyncMock(return_value='{"output":"ok"}')
        approval = AsyncMock(return_value=True)
        runtime = self.runtime(approval)
        with (
            patch.object(agent, "stream_deepseek", reply),
            patch("example.core.events.execute_tool", execute),
        ):
            with self.assertRaisesRegex(RuntimeError, "transient"):
                await collect(runtime)
            old = runtime.last_context
            self.assertEqual(old.status, "failed")
            self.assertEqual(old.get_snapshot()["target_node_name"], "agent")
            restored = runtime.retry_context()
            self.assertEqual(restored.status, "pending")
            self.assertEqual(restored.graph_id, old.graph_id)
            await collect(runtime, context=restored)
        self.assertEqual(execute.await_count, 2)
        self.assertEqual(approval.await_count, 2)
        self.assertEqual(runtime.last_context.status, "finished")
        roles = [m["role"] for m in runtime.last_context.state["messages"]]
        self.assertEqual(
            roles, ["system", "user", "assistant", "tool", "tool", "assistant"]
        )
        self.assertEqual(old.status, "failed")

    async def test_guard_and_user_rejection_never_dispatch_tools(self):
        for command, decision in [("rm -rf /tmp/test", True), ("printf safe", False)]:
            runtime = self.runtime(AsyncMock(return_value=decision))
            replies = [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call(command=command)],
                },
                {"role": "assistant", "content": "rejected"},
            ]
            execute = AsyncMock()
            with (
                patch.object(agent, "stream_deepseek", AsyncMock(side_effect=replies)),
                patch("example.core.events.execute_tool", execute),
            ):
                await collect(runtime)
            execute.assert_not_awaited()
            result = json.loads(runtime.last_context.state["messages"][-2]["content"])
            self.assertTrue(result["error"])
            if decision:
                self.assertIn("安全策略", result["error"])
            else:
                self.assertIn("rejected", result["error"])

    async def test_worker_failure_replies_instead_of_hanging(self):
        runtime = self.runtime()
        replies = [
            {"role": "assistant", "content": None, "tool_calls": [tool_call()]},
            {"role": "assistant", "content": "handled"},
        ]
        with (
            patch.object(agent, "stream_deepseek", AsyncMock(side_effect=replies)),
            patch(
                "example.core.events.execute_tool",
                AsyncMock(side_effect=RuntimeError("worker failed")),
            ),
        ):
            await asyncio.wait_for(collect(runtime), 3)
        self.assertIn(
            "worker failed", runtime.last_context.state["messages"][-2]["content"]
        )

    async def test_tool_reply_arriving_before_publication_returns_is_retained(self):
        runtime = self.runtime()
        pipe = get_event_pipe()
        publish = pipe.post_event
        registered = set(get_handler_registry().registry)

        async def publish_and_wait_for_reply(**kwargs):
            if kwargs["event_name"] != f"{runtime.events.prefix}.tool.request":
                return await publish(**kwargs)
            processed = asyncio.Event()

            async def mark_processed(event):
                processed.set()

            handler = ApixEventHandler(mark_processed, name="test_reply_processed")
            # Run after the reply waiter so publication only returns once the
            # reply is already retained, before execute() calls waiter.wait().
            reply = kwargs["context"]["reply"]
            reply_waiter = next(
                h
                for h in get_handler_registry().get_handlers_for_event(reply)
                if h.name.startswith("resolve_future-")
            )
            subscribe(reply, between_handlers=(reply_waiter.name, None))(handler)
            try:
                await publish(**kwargs)
                await asyncio.wait_for(processed.wait(), 2)
            finally:
                unsubscribe(handler.name)

        replies = [
            {"role": "assistant", "content": None, "tool_calls": [tool_call()]},
            {"role": "assistant", "content": "done"},
        ]
        with (
            patch.object(pipe, "post_event", publish_and_wait_for_reply),
            patch.object(agent, "stream_deepseek", AsyncMock(side_effect=replies)),
            patch("example.core.events.execute_tool", AsyncMock(return_value="early")),
        ):
            await asyncio.wait_for(collect(runtime), 3)
        self.assertEqual(runtime.last_context.status, "finished")
        self.assertEqual(runtime.last_context.state["messages"][-2]["content"], "early")
        self.assertEqual(set(get_handler_registry().registry), registered)

    async def test_tool_publication_failure_removes_reply_subscription(self):
        runtime = self.runtime()
        pipe = get_event_pipe()
        publish = pipe.post_event
        registered = set(get_handler_registry().registry)

        async def fail_request(**kwargs):
            if kwargs["event_name"] == f"{runtime.events.prefix}.tool.request":
                raise RuntimeError("publication failed")
            return await publish(**kwargs)

        execute = AsyncMock()
        with (
            patch.object(pipe, "post_event", fail_request),
            patch.object(
                agent,
                "stream_deepseek",
                AsyncMock(
                    return_value={
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [tool_call()],
                    }
                ),
            ),
            patch("example.core.events.execute_tool", execute),
        ):
            with self.assertRaisesRegex(RuntimeError, "publication failed"):
                await asyncio.wait_for(collect(runtime), 3)
        execute.assert_not_awaited()
        self.assertEqual(runtime.last_context.status, "failed")
        self.assertEqual(set(get_handler_registry().registry), registered)

    async def test_concurrent_reply_correlation(self):
        runtime = self.runtime()

        async def reply(messages, tools, on_text):
            user = next(m["content"] for m in messages if m["role"] == "user")
            if messages[-1]["role"] != "tool":
                return {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call(user, user)],
                }
            return {"role": "assistant", "content": messages[-1]["content"]}

        async def execute(call):
            return call["id"]

        with (
            patch.object(agent, "stream_deepseek", reply),
            patch("example.core.events.execute_tool", execute),
        ):
            contexts = [
                runtime.graph.create_context(
                    {"messages": MESSAGES[:1] + [{"role": "user", "content": str(i)}]}
                )
                for i in range(12)
            ]
            await asyncio.wait_for(
                asyncio.gather(*(collect(runtime, context=c) for c in contexts)), 10
            )
        for i, context in enumerate(contexts):
            self.assertEqual(context.state["messages"][-1]["content"], str(i))

    async def test_abort_returns_without_waiting_for_old_node(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def reply(messages, tools, on_text):
            entered.set()
            await release.wait()
            return {"role": "assistant", "content": "late"}

        runtime = self.runtime()
        with patch.object(agent, "stream_deepseek", reply):
            task = asyncio.create_task(collect(runtime))
            await asyncio.wait_for(entered.wait(), 3)
            await runtime.abort()
            await asyncio.wait_for(task, 1)
            self.assertEqual(runtime.last_context.status, "aborted")
            release.set()
            await asyncio.wait_for(get_event_pipe().join(), 3)
        self.assertEqual(len(runtime.last_context.state["messages"]), 2)
        with self.assertRaisesRegex(RuntimeError, "失败"):
            runtime.retry_context()

    async def test_closing_stream_aborts_and_unregistration_is_isolated(self):
        first, second = self.runtime(), self.runtime()
        release = asyncio.Event()

        async def reply(messages, tools, on_text):
            on_text("partial")
            await release.wait()
            return {"role": "assistant", "content": "late"}

        with patch.object(agent, "stream_deepseek", reply):
            stream = first.stream(MESSAGES)
            self.assertEqual((await anext(stream))["text"], "partial")
            await stream.aclose()
            self.assertEqual(first.last_context.status, "aborted")
            release.set()
            await asyncio.wait_for(get_event_pipe().join(), 3)
        first.close()
        self.assertIsNone(
            get_handler_registry().get_handler(f"{first.graph.namespace}.input_filter")
        )
        self.assertIsNotNone(
            get_handler_registry().get_handler(f"{second.graph.namespace}.input_filter")
        )

    async def test_max_steps_stops_tool_loop(self):
        runtime = self.runtime(max_steps=4)
        stub = AsyncMock(
            return_value={
                "role": "assistant",
                "content": None,
                "tool_calls": [tool_call()],
            }
        )
        with (
            patch.object(agent, "stream_deepseek", stub),
            patch("example.core.events.execute_tool", AsyncMock(return_value="ok")),
        ):
            with self.assertRaisesRegex(RuntimeError, "maximum"):
                await collect(runtime)
        self.assertEqual(runtime.last_context.status, "failed")

    async def test_chat_history_has_one_user_message_per_turn_including_retry(self):
        class UI:
            def __init__(self):
                self.ask = unittest.mock.Mock(
                    side_effect=["hello", "/retry", "next", "quit"]
                )

            def __getattr__(self, name):
                return unittest.mock.Mock()

        replies = [
            RuntimeError("retry me"),
            {"role": "assistant", "content": "one"},
            {"role": "assistant", "content": "two"},
        ]
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(settings, "SETTINGS_DIR", Path(folder)),
            patch.object(settings, "get_username", return_value="tester"),
            patch.object(llm, "DEEPSEEK_API_KEY", "test-placeholder"),
            patch("example.app.AgentUI", UI),
            patch.object(agent, "stream_deepseek", AsyncMock(side_effect=replies)),
        ):
            await chat_loop()
            conv = history.list_conversations("tester")[0]
            messages = history.load_messages(conv.id)
            self.assertEqual(
                [m["role"] for m in messages],
                ["user", "assistant", "user", "assistant"],
            )
            self.assertEqual(
                [m["content"] for m in messages], ["hello", "one", "next", "two"]
            )
            self.assertEqual(history.list_conversations("someone-else"), [])

    async def test_chat_displays_streamed_rejection_once_and_preserves_history(self):
        instances = []

        class UI:
            def __init__(self):
                self.ask = unittest.mock.Mock(
                    side_effect=["hello", "炸弹", "next", "quit"]
                )
                self.error = unittest.mock.Mock()
                instances.append(self)

            def __getattr__(self, name):
                return unittest.mock.Mock()

        stub = AsyncMock(
            side_effect=[
                {"role": "assistant", "content": "one"},
                {"role": "assistant", "content": "two"},
            ]
        )
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(settings, "SETTINGS_DIR", Path(folder)),
            patch.object(settings, "get_username", return_value="tester"),
            patch.object(llm, "DEEPSEEK_API_KEY", "test-placeholder"),
            patch("example.app.AgentUI", UI),
            patch.object(agent, "stream_deepseek", stub),
        ):
            await asyncio.wait_for(chat_loop(), 5)
            conv = history.list_conversations("tester")[0]
            self.assertEqual(
                history.load_messages(conv.id),
                [
                    {"role": "user", "content": "hello"},
                    {"role": "assistant", "content": "one"},
                    {"role": "user", "content": "next"},
                    {"role": "assistant", "content": "two"},
                ],
            )
        instances[0].error.assert_called_once_with(
            "这条输入被当前过滤规则拦截：炸弹。",
            hint="请调整表达后重新发送，本轮未保存。",
        )
        self.assertEqual(stub.await_count, 2)
        self.assertEqual(
            [m["content"] for m in stub.await_args_list[1].args[0] if m["role"] == "user"],
            ["hello", "next"],
        )

    async def test_tools_handle_invalid_arguments_and_real_shell(self):
        invalid = tool_call()
        invalid["function"]["arguments"] = "[1]"
        self.assertIn("error", json.loads(await tools.execute_tool(invalid)))
        invalid["function"]["arguments"] = "{"
        self.assertIn("error", json.loads(await tools.execute_tool(invalid)))
        result = json.loads(await tools.run_command("printf apixis"))
        self.assertEqual(result["output"], "apixis")
        self.assertEqual(result["exit_code"], 0)

    async def test_cancelling_shell_reaps_its_process(self):
        spawned = asyncio.Event()
        original = asyncio.create_subprocess_shell
        process = None

        async def capture(*args, **kwargs):
            nonlocal process
            process = await original(*args, **kwargs)
            spawned.set()
            return process

        with patch.object(tools.asyncio, "create_subprocess_shell", capture):
            task = asyncio.create_task(tools.run_command("sleep 60"))
            await asyncio.wait_for(spawned.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
        self.assertIsNotNone(process.returncode)

    async def test_application_serializes_text_results_and_two_approvals(self):
        displayed = []

        class UI:
            def __init__(self):
                self.ask = unittest.mock.Mock(side_effect=["hello", "quit"])

            def __getattr__(self, name):
                return unittest.mock.Mock()

            def content(self, text):
                displayed.append("text:" + text)

            def tool_result(self, result, **kwargs):
                displayed.append("result:" + result)

            async def confirm_command(self, arguments, **kwargs):
                displayed.append("approval:" + json.loads(arguments)["command"])
                return True

        calls = [
            tool_call("first", "printf first"),
            tool_call("second", "printf second"),
        ]
        attempts = 0

        async def reply(messages, tools, on_text):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                on_text("先确认两条命令")
                return {
                    "role": "assistant",
                    "content": "先确认两条命令",
                    "tool_calls": calls,
                }
            on_text("完成")
            return {"role": "assistant", "content": "完成"}

        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(settings, "SETTINGS_DIR", Path(folder)),
            patch.object(settings, "get_username", return_value="tester"),
            patch.object(llm, "DEEPSEEK_API_KEY", "test-placeholder"),
            patch("example.app.AgentUI", UI),
            patch.object(agent, "stream_deepseek", reply),
            patch(
                "example.core.events.execute_tool",
                AsyncMock(side_effect=["FIRST", "SECOND"]),
            ),
        ):
            await asyncio.wait_for(chat_loop(), 5)
        self.assertEqual(
            displayed,
            [
                "text:先确认两条命令",
                "approval:printf first",
                "result:FIRST",
                "approval:printf second",
                "result:SECOND",
                "text:完成",
            ],
        )
