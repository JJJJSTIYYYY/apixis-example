"""Check native provider presets through official SDKs without network access."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from my_llm import MyLLM, ResponseFailedError, StreamError
from sdk_transport import sdk_transport

from example.core import history, llm, settings
from example.core.tools import get_tools_schema


def events_for(protocol, finish_reason=None, *, with_tool=False):
    if protocol == "anthropic":
        events = [
            {
                "type": "message_start",
                "message": {
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "model": "test-model",
                    "content": [],
                    "stop_reason": None,
                    "usage": {"input_tokens": 1, "output_tokens": 0},
                },
            },
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "OK"},
            },
            {"type": "content_block_stop", "index": 0},
            {
                "type": "message_delta",
                "delta": {
                    "stop_reason": finish_reason or "end_turn",
                    "stop_sequence": None,
                },
                "usage": {"output_tokens": 1},
            },
            {"type": "message_stop"},
        ]
        if with_tool:
            events[4:4] = [
                {
                    "type": "content_block_start",
                    "index": 1,
                    "content_block": {
                        "type": "tool_use",
                        "id": "call_1",
                        "name": "run_command",
                        "input": {"command": "printf apixis"},
                    },
                },
                {"type": "content_block_stop", "index": 1},
            ]
        return events
    if protocol == "gemini":
        return [
            {
                "candidates": [
                    {
                        "index": 0,
                        "content": {
                            "role": "model",
                            "parts": [{"text": "OK"}]
                            + (
                                [
                                    {
                                        "functionCall": {
                                            "name": "run_command",
                                            "args": {"command": "printf apixis"},
                                        }
                                    }
                                ]
                                if with_tool
                                else []
                            ),
                        },
                        "finishReason": finish_reason or "STOP",
                    }
                ]
            }
        ]
    return [
        {
            "model": "test-model",
            "message": {
                "role": "assistant",
                "content": "OK",
                **(
                    {
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "run_command",
                                    "arguments": {"command": "printf apixis"},
                                }
                            }
                        ]
                    }
                    if with_tool
                    else {}
                ),
            },
            "done": True,
            "done_reason": finish_reason or "stop",
        }
    ]


def response_for(protocol, events):
    if protocol == "ollama":
        body = "\n".join(json.dumps(event) for event in events) + "\n"
        mime = "application/x-ndjson"
    else:
        body = "".join(
            (f"event: {event['type']}\n" if protocol == "anthropic" else "")
            + f"data: {json.dumps(event)}\n\n"
            for event in events
        )
        mime = "text/event-stream"
    return httpx.Response(200, text=body, headers={"content-type": mime})


class NativeLLMTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_presets_use_unified_completion_and_reject_truncation(self):
        for name, native_finish, unified_finish in (
            ("anthropic", "end_turn", "stop"),
            ("anthropic", "max_tokens", "length"),
            (
                "anthropic",
                "model_context_window_exceeded",
                "model_context_window_exceeded",
            ),
            ("anthropic", "tool_use", "tool_calls"),
            ("anthropic", "pause_turn", "pause_turn"),
            ("anthropic", "refusal", "content_filter"),
            ("gemini", "STOP", "stop"),
            ("gemini", "MAX_TOKENS", "length"),
            ("gemini", "STOP", "tool_calls"),
            ("ollama", "stop", "stop"),
            ("ollama", "length", "length"),
            ("ollama", "stop", "tool_calls"),
        ):
            with (
                self.subTest(name=name, finish=native_finish, expected=unified_finish),
                patch.dict(
                    os.environ,
                    {
                        "LLM_API_KEY": "test",
                    },
                    clear=True,
                ),
            ):
                requests = []

                def handler(request):
                    requests.append(request)
                    events = events_for(
                        name, native_finish, with_tool=unified_finish == "tool_calls"
                    )
                    return response_for(name, events)

                with sdk_transport(handler):
                    provider, model = llm.get_profiles(name)
                    adapter = MyLLM(
                        provider=provider, model=model, stdio_protocol="openai_chat"
                    )
                    with patch.object(llm, "create_llm", return_value=adapter):
                        if unified_finish in (
                            "pause_turn",
                            "model_context_window_exceeded",
                        ):
                            with self.assertRaises(ResponseFailedError) as error:
                                await llm.stream_reply(
                                    [{"role": "user", "content": "hello"}],
                                    get_tools_schema(),
                                    provider=name,
                                    model=model.model.default_value,
                                )
                            self.assertEqual(
                                error.exception.details, {"reason": native_finish}
                            )
                            continue
                        if unified_finish == "length":
                            with self.assertRaisesRegex(
                                StreamError, "长度上限"
                            ) as error:
                                await llm.stream_reply(
                                    [{"role": "user", "content": "hello"}],
                                    get_tools_schema(),
                                    provider=name,
                                    model=model.model.default_value,
                                )
                            message = error.exception.partial_message
                        else:
                            message = await llm.stream_reply(
                                [{"role": "user", "content": "hello"}],
                                get_tools_schema(),
                                provider=name,
                                model=model.model.default_value,
                            )
                    self.assertEqual(message["content"], "OK")
                    self.assertNotIn("finish_reason", message)
                    if unified_finish == "tool_calls":
                        self.assertEqual(len(message["tool_calls"]), 1)
                        self.assertEqual(
                            message["tool_calls"][0]["function"]["name"], "run_command"
                        )
                    body = json.loads(requests[0].content)
                    if name == "anthropic":
                        self.assertEqual(body["max_tokens"], 4096)
                        self.assertEqual(body["model"], model.model.default_value)
                        self.assertEqual(body["thinking"], {"type": "adaptive"})
                        self.assertEqual(body["tools"][0]["name"], "run_command")
                    elif name == "gemini":
                        self.assertEqual(
                            body["tools"][0]["functionDeclarations"][0]["name"],
                            "run_command",
                        )
                    else:
                        self.assertEqual(body["tools"], get_tools_schema())
                    # Explicit reasoning must use the selected preset's native
                    # settings, including adaptive rather than legacy budgets.
                    adapter = MyLLM(
                        provider=provider, model=model, stdio_protocol="openai_chat"
                    )
                    self.addAsyncCleanup(adapter.aclose)
                    await adapter.generate(
                        messages=[{"role": "user", "content": "hello"}],
                        tools=get_tools_schema(),
                        reasoning_effort="high",
                    )
                    reasoning_body = json.loads(requests[-1].content)
                    if name == "anthropic":
                        self.assertEqual(
                            reasoning_body["thinking"], {"type": "adaptive"}
                        )
                        self.assertEqual(
                            reasoning_body["output_config"]["effort"], "high"
                        )
                    elif name == "gemini":
                        thinking = reasoning_body["generationConfig"]["thinkingConfig"]
                        self.assertEqual(thinking["thinking_level"].lower(), "high")
                        self.assertNotIn("thinking_budget", thinking)
                    else:
                        self.assertIs(reasoning_body["think"], True)

    async def test_native_tool_messages_survive_persistence_and_replay(self):
        for name in ("anthropic", "gemini", "ollama"):
            with (
                self.subTest(name=name),
                tempfile.TemporaryDirectory() as folder,
                patch.object(settings, "SETTINGS_DIR", Path(folder)),
                patch.dict(os.environ, {"LLM_API_KEY": "local-test-only"}, clear=True),
            ):
                requests = []

                def handler(request):
                    requests.append(json.loads(request.content))
                    first = len(requests) == 1
                    finish = "tool_use" if first and name == "anthropic" else None
                    return response_for(name, events_for(name, finish, with_tool=first))

                messages = [{"role": "user", "content": "run a command"}]
                with sdk_transport(handler):
                    result = await llm.stream_reply(
                        messages, get_tools_schema(), provider=name
                    )
                    call = result["tool_calls"][0]
                    messages.extend(
                        [
                            result,
                            {
                                "role": "tool",
                                "tool_call_id": call["id"],
                                "content": "persisted-result",
                            },
                        ]
                    )
                    conversation = history.create_conversation("tester", "tool replay")
                    history.append_messages(conversation.id, messages)
                    restored = history.load_messages(conversation.id)
                    self.assertEqual(restored, messages)
                    reply = await llm.stream_reply(
                        restored, get_tools_schema(), provider=name
                    )
                self.assertEqual(reply["content"], "OK")
                self.assertEqual(len(requests), 2)
                replay = requests[-1]
                if name == "anthropic":
                    tool = replay["messages"][-1]["content"][0]
                    self.assertEqual(tool["type"], "tool_result")
                    self.assertEqual(tool["tool_use_id"], call["id"])
                    self.assertEqual(
                        tool["content"], [{"type": "text", "text": "persisted-result"}]
                    )
                elif name == "gemini":
                    tool = replay["contents"][-1]["parts"][0]["functionResponse"]
                    self.assertEqual(tool["name"], "run_command")
                    self.assertIn("persisted-result", str(tool["response"]))
                else:
                    tool = replay["messages"][-1]
                    self.assertEqual(tool["role"], "tool")
                    self.assertEqual(tool["content"], "persisted-result")
