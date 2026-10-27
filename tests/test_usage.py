"""Report public MyLLM usage only for successfully consumed replies."""

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx
from apixis import get_event_pipe, start_core
from my_llm import IncompleteStreamError, StreamError, Usage
from sdk_transport import sdk_transport
from test_agent import SharedLoopTests, approve, collect, tool_call

from example.app import chat_loop
from example.core import agent, llm, settings
from example.core.runtime import AgentRuntime, shutdown_core


def chat_event(content="", finish_reason=None):
    return {
        "id": "usage-test",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "delta": {"content": content},
                "finish_reason": finish_reason,
            }
        ],
    }


def usage_event():
    event = chat_event()
    event["choices"] = []
    event["usage"] = {
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "total_tokens": 18,
        "prompt_tokens_details": {"cached_tokens": 3},
        "completion_tokens_details": {"reasoning_tokens": 2},
    }
    return event


class UsageTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, events, on_usage, *, on_text=None, body=None):
        def handle(request):
            return httpx.Response(
                200,
                stream=body,
                text=None
                if body is not None
                else "".join(f"data: {json.dumps(event)}\n\n" for event in events)
                + "data: [DONE]\n\n",
                headers={"Content-Type": "text/event-stream"},
            )

        with (
            patch.dict(os.environ, {"DEEPSEEK_API_KEY": "local-test-only"}, clear=True),
            sdk_transport(handle) as clients,
        ):
            try:
                return await llm.stream_reply(
                    [{"role": "user", "content": "hello"}],
                    on_text=on_text,
                    on_usage=on_usage,
                )
            finally:
                self.assertTrue(all(client.is_closed for client in clients))

    async def test_usage_arrives_after_text_as_typed_metadata_outside_message(self):
        seen = []
        result = await self.request(
            [chat_event("OK", "stop"), usage_event()],
            lambda usage: seen.append(usage),
            on_text=lambda text: seen.append(text),
        )
        self.assertEqual(seen, ["OK", Usage(11, 7, 18, 3, 2)])
        self.assertEqual(result, {"role": "assistant", "content": "OK"})

    async def test_missing_usage_does_not_fabricate_counters_or_call_observer(self):
        seen = []
        result = await self.request([chat_event("OK", "stop")], seen.append)
        self.assertEqual(seen, [])
        self.assertEqual(result["content"], "OK")

    async def test_truncation_and_disconnect_do_not_report_usage(self):
        for finish, error in (("length", StreamError), (None, IncompleteStreamError)):
            with self.subTest(finish=finish):
                seen = []
                with self.assertRaises(error):
                    await self.request(
                        [chat_event("partial", finish), usage_event()], seen.append
                    )
                self.assertEqual(seen, [])

    async def test_cancelled_stream_does_not_report_usage_and_closes_resources(self):
        entered = asyncio.Event()

        class Body(httpx.AsyncByteStream):
            closed = False

            async def __aiter__(self):
                yield f"data: {json.dumps(chat_event('partial'))}\n\n".encode()
                entered.set()
                await asyncio.Event().wait()

            async def aclose(self):
                self.closed = True

        seen = []
        body = Body()
        task = asyncio.create_task(self.request([], seen.append, body=body))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(seen, [])
        self.assertTrue(body.closed)

    async def test_observer_error_closes_resources(self):
        def fail(usage):
            raise RuntimeError("usage display failed")

        with self.assertRaisesRegex(RuntimeError, "usage display failed"):
            await self.request([chat_event("OK", "stop"), usage_event()], fail)


class RuntimeUsageTests(SharedLoopTests):
    async def asyncSetUp(self):
        await start_core()
        self.runtimes = []

    def runtime(self):
        runtime = AgentRuntime(approve)
        self.runtimes.append(runtime)
        return runtime

    async def asyncTearDown(self):
        for runtime in self.runtimes:
            runtime.close()
        await asyncio.wait_for(get_event_pipe().join(), 5)
        await shutdown_core()

    async def test_tool_turn_reports_both_calls_without_polluting_state_or_snapshots(
        self,
    ):
        calls = []

        def handle(request):
            calls.append(json.loads(request.content))
            if len(calls) == 1:
                event = chat_event(finish_reason="tool_calls")
                event["choices"][0]["delta"] = {
                    "tool_calls": [{"index": 0, **tool_call()}]
                }
            else:
                event = chat_event("done", "stop")
            return httpx.Response(
                200,
                text="".join(
                    f"data: {json.dumps(value)}\n\n" for value in (event, usage_event())
                )
                + "data: [DONE]\n\n",
                headers={"Content-Type": "text/event-stream"},
            )

        runtime = self.runtime()
        execute = AsyncMock(return_value="tool result")
        with (
            patch.dict(os.environ, {"DEEPSEEK_API_KEY": "local-test-only"}, clear=True),
            sdk_transport(handle) as clients,
            patch("example.core.events.execute_tool", execute),
        ):
            chunks = await collect(runtime)
        self.assertTrue(all(client.is_closed for client in clients))
        self.assertEqual(len(calls), 2)
        self.assertTrue(
            all(request["stream_options"]["include_usage"] for request in calls)
        )
        execute.assert_awaited_once()
        records = list(runtime.usage)
        expected = {
            "provider": runtime.provider,
            "model": runtime.model,
            "usage": Usage(11, 7, 18, 3, 2),
        }
        self.assertEqual(records, [expected, expected])
        self.assertEqual(
            [chunk for chunk in chunks if chunk.get("type") == "usage"],
            [{"type": "usage", **expected}, {"type": "usage", **expected}],
        )
        context = runtime.last_context
        self.assertEqual(context.status, "finished")
        self.assertEqual(
            set(context.state), {"messages", "tool_calls", "workspace_notes"}
        )
        for snapshot in context.get_all_snapshots():
            self.assertEqual(
                set(snapshot["state"]),
                {"messages", "tool_calls", "workspace_notes"},
            )
            for message in snapshot["state"]["messages"]:
                self.assertNotIn("usage", message)

    async def test_session_records_are_bounded_and_isolated_between_runtimes(self):
        first, second = self.runtime(), self.runtime()
        calls = 0

        async def reply(messages, tools, on_text, *, on_usage, **selection):
            nonlocal calls
            calls += 1
            on_usage(Usage(input=calls))
            return {"role": "assistant", "content": "OK"}

        with patch.object(agent, "stream_reply", reply):
            for _ in range(22):
                await collect(first)
            self.assertEqual(list(second.usage), [])
            second.select_model("kimi", "kimi-test")
            await collect(second)
        self.assertEqual(len(first.usage), 20)
        self.assertEqual(
            [record["usage"].input for record in first.usage], list(range(3, 23))
        )
        self.assertEqual(len(second.usage), 1)
        self.assertEqual(second.usage[0]["provider"], "kimi")
        self.assertEqual(second.usage[0]["model"], "kimi-test")
        self.assertEqual(second.usage[0]["usage"], Usage(input=23))

    async def test_logout_clears_usage_before_another_user_continues(self):
        ui = Mock()
        runtimes = []
        inputs = iter(["hello", "/logout", "quit"])

        def make_runtime(*args, **kwargs):
            runtime = AgentRuntime(*args, **kwargs)
            runtimes.append(runtime)
            return runtime

        def ask():
            text = next(inputs)
            if text == "/logout":
                self.assertEqual(len(runtimes[0].usage), 1)
            elif text == "quit":
                self.assertEqual(list(runtimes[0].usage), [])
            return text

        async def reply(messages, tools, on_text, *, on_usage, **selection):
            on_usage(Usage(input=5, output=2, total=7))
            return {"role": "assistant", "content": "OK"}

        ui.ask.side_effect = ask
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(settings, "SETTINGS_DIR", Path(folder)),
            patch.object(settings, "get_username", return_value="first-user"),
            patch.object(settings, "clear_username"),
            patch("example.commands.handle_login", return_value="second-user"),
            patch("example.app.AgentUI", return_value=ui),
            patch("example.app.AgentRuntime", side_effect=make_runtime),
            patch.object(agent, "stream_reply", reply),
        ):
            await chat_loop()
        self.assertEqual(len(runtimes), 1)
        self.assertEqual(ui.username, "second-user")
