"""Validate bounded retry history and ephemeral workspace graph execution."""

import asyncio
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock, patch

from apixis import get_event_pipe, start_core
from test_agent import MESSAGES, SharedLoopTests, approve, collect, tool_call

from example.core import agent, workspace
from example.core.runtime import AgentRuntime, shutdown_core


class CheckpointTests(SharedLoopTests):
    async def asyncSetUp(self):
        await start_core()
        self.runtimes = []

    async def asyncTearDown(self):
        for runtime in self.runtimes:
            runtime.close()
        await asyncio.wait_for(get_event_pipe().join(), 5)
        await shutdown_core()

    async def test_bounded_history_retries_latest_failure_without_repeating_tools(self):
        runtime = AgentRuntime(approve, max_snapshots=2)
        self.runtimes.append(runtime)
        calls = [tool_call(str(index)) for index in range(3)]
        replies = AsyncMock(
            side_effect=[
                {"role": "assistant", "content": None, "tool_calls": calls},
                RuntimeError("transient model failure"),
                {"role": "assistant", "content": "recovered"},
            ]
        )
        execute = AsyncMock(return_value="tool output")
        with (
            patch.object(agent, "stream_reply", replies),
            patch("example.core.events.execute_tool", execute),
        ):
            with self.assertRaisesRegex(RuntimeError, "transient model failure"):
                await collect(runtime)
            failed = runtime.last_context
            snapshots = failed.get_all_snapshots()
            self.assertEqual(len(snapshots), 2)
            self.assertEqual(
                [snapshot["target_node_name"] for snapshot in snapshots],
                ["tools", "agent"],
            )
            self.assertEqual(failed.max_snapshots, 2)
            restored = runtime.retry_context()
            self.assertIsNot(restored, failed)
            self.assertEqual(restored.max_snapshots, 2)
            await collect(runtime, context=restored)
        self.assertEqual(execute.await_count, 3)
        self.assertEqual(failed.status, "failed")
        self.assertEqual(restored.status, "finished")
        self.assertLessEqual(len(restored.get_all_snapshots()), 2)
        self.assertEqual(restored.state["messages"][:2], MESSAGES)
        self.assertEqual(restored.state["messages"][-1]["content"], "recovered")

    async def test_workspace_still_merges_branches_without_keeping_snapshots(self):
        graph = workspace.build_workspace_graph()
        try:
            context = graph.create_context({"notes": []})
            result = await graph.invoke(graph_context=context)
            self.assertEqual(context.status, "finished")
            self.assertEqual(len(result["notes"]), 2)
            self.assertIn("工作目录", result["notes"][0])
            self.assertIn("Python", result["notes"][1])
            self.assertEqual(result["summary"], "\n".join(result["notes"]))
            self.assertEqual(context.get_all_snapshots(), [])
        finally:
            graph.decompose()


class CheckpointCLITests(unittest.TestCase):
    def test_help_explains_snapshot_retention(self):
        result = subprocess.run(
            [sys.executable, "-m", "example", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--max-snapshots", result.stdout)
        self.assertIn("最近快照数量", result.stdout)

    def test_nonpositive_snapshot_limit_is_rejected_before_starting(self):
        for value in ("0", "-1"):
            with self.subTest(value=value):
                result = subprocess.run(
                    [sys.executable, "-m", "example", "--max-snapshots", value],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn("--max-snapshots must be positive", result.stderr)
