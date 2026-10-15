"""Check native provider presets through official SDKs without network access."""

import json
import os
import unittest
from unittest.mock import patch

import httpx
from anthropic import AsyncAnthropic
from google import genai
from google.genai import types
from my_llm import MyLLM, StreamError
from ollama import AsyncClient

from example.core import llm
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


class NativeLLMTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_presets_use_unified_completion_and_reject_truncation(self):
        for name, native_finish, unified_finish in (
            ("anthropic", "end_turn", "stop"),
            ("anthropic", "max_tokens", "length"),
            ("anthropic", "model_context_window_exceeded", "length"),
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
                    if name == "ollama":
                        body = "\n".join(json.dumps(event) for event in events) + "\n"
                        mime = "application/x-ndjson"
                    else:
                        body = "".join(
                            (f"event: {event['type']}\n" if name == "anthropic" else "")
                            + f"data: {json.dumps(event)}\n\n"
                            for event in events
                        )
                        mime = "text/event-stream"
                    return httpx.Response(
                        200, text=body, headers={"content-type": mime}
                    )

                transport = httpx.MockTransport(handler)
                http = None
                if name == "anthropic":
                    http = httpx.AsyncClient(transport=transport)
                    client = AsyncAnthropic(
                        api_key="test",
                        base_url="https://native.test",
                        max_retries=0,
                        http_client=http,
                    )
                elif name == "gemini":
                    http = httpx.AsyncClient(transport=transport)
                    client = genai.Client(
                        api_key="test",
                        http_options=types.HttpOptions(
                            base_url="https://native.test",
                            httpx_async_client=http,
                            retry_options=types.HttpRetryOptions(attempts=1),
                        ),
                    )
                else:
                    client = AsyncClient(
                        host="https://native.test", transport=transport
                    )
                try:
                    provider, model = llm.get_profiles(name)
                    adapter = MyLLM(provider=provider, model=model, client=client)
                    with patch.object(llm, "create_llm", return_value=adapter):
                        if unified_finish == "length":
                            with self.assertRaisesRegex(
                                StreamError, "长度上限"
                            ) as error:
                                await llm.stream_reply(
                                    [{"role": "user", "content": "hello"}],
                                    get_tools_schema(),
                                    provider=name,
                                    model=model.model,
                                )
                            message = error.exception.partial_message
                        else:
                            message = await llm.stream_reply(
                                [{"role": "user", "content": "hello"}],
                                get_tools_schema(),
                                provider=name,
                                model=model.model,
                            )
                    self.assertEqual(message["content"], "OK")
                    self.assertEqual(message["finish_reason"], unified_finish)
                    if unified_finish == "tool_calls":
                        self.assertEqual(len(message["tool_calls"]), 1)
                        self.assertEqual(
                            message["tool_calls"][0]["function"]["name"], "run_command"
                        )
                    body = json.loads(requests[0].content)
                    if name == "anthropic":
                        self.assertEqual(body["max_tokens"], 4096)
                        self.assertEqual(body["model"], model.model)
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
                finally:
                    if name == "gemini":
                        await client.aio.aclose()
                        client.close()
                    else:
                        await client.close()
                    if http is not None:
                        await http.aclose()
