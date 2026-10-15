"""Exercise the public my-llm-py API through real SDKs and offline transports."""

import asyncio
import json
import os
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, Mock, patch

import httpx
from my_llm import (
    ConfigurationError,
    IncompleteStreamError,
    MessageChunk,
    MessageStream,
    MyLLM,
    StreamError,
)
from openai import AsyncOpenAI, AuthenticationError

from example.core import llm
from example.core.tools import get_tools_schema

MESSAGES = [{"role": "user", "content": "hello"}]


def chat_event(delta, finish_reason=None):
    return {
        "id": "chat_1",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "test-model",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


def response_completed(output):
    return {
        "type": "response.completed",
        "sequence_number": 10,
        "response": {
            "id": "resp_1",
            "object": "response",
            "created_at": 1,
            "model": "test-model",
            "status": "completed",
            "output": output,
            "parallel_tool_calls": True,
            "tools": [],
            "tool_choice": "auto",
        },
    }


class LLMTests(unittest.IsolatedAsyncioTestCase):
    async def request(
        self,
        events,
        *,
        provider="deepseek",
        status=200,
        on_text=None,
        tools=None,
        max_tokens=None,
        extra_body=None,
        messages=None,
    ):
        self.requests = []

        def handle(request):
            self.requests.append(request)
            if status != 200:
                return httpx.Response(status, json={"error": {"message": "PRIVATE"}})
            body = "".join("data: " + json.dumps(event) + "\n\n" for event in events)
            return httpx.Response(
                200,
                text=body + "data: [DONE]\n\n",
                headers={"Content-Type": "text/event-stream"},
            )

        with (
            patch.dict(
                os.environ,
                {
                    "LLM_API_KEY": "test-placeholder",
                },
                clear=True,
            ),
            patch.dict(
                llm.PROVIDERS,
                {
                    provider: replace(
                        llm.get_provider(provider),
                        models=[
                            replace(
                                llm.get_model(provider, "test-model"),
                                max_tokens=max_tokens,
                                extra_body=extra_body,
                            )
                        ],
                    )
                },
            ),
        ):
            profile, model = llm.get_profiles(provider, "test-model")
            async with AsyncOpenAI(
                api_key="test-placeholder",
                base_url=profile.base_url or "https://api.openai.com/v1",
                max_retries=0,
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
            ) as client:
                adapter = MyLLM(provider=profile, model=model, client=client)
                with patch.object(llm, "create_llm", return_value=adapter):
                    return await llm.stream_reply(
                        messages or MESSAGES,
                        tools,
                        on_text,
                        provider=provider,
                        model="test-model",
                    )

    async def test_content_reasoning_and_fragmented_tools_use_library_assembly(self):
        chunks = []
        events = [
            chat_event(
                {
                    "content": "hi",
                    "reasoning_content": "thought",
                    "tool_calls": [
                        {
                            "index": 1,
                            "id": "two",
                            "type": "function",
                            "function": {
                                "name": "run_command",
                                "arguments": '{"command":',
                            },
                        },
                    ],
                }
            ),
            chat_event(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "one",
                            "type": "function",
                            "function": {"name": "run_command", "arguments": "{}"},
                        },
                        {"index": 1, "function": {"arguments": '"pwd"}'}},
                    ]
                },
                "tool_calls",
            ),
        ]
        result = await self.request(events, on_text=chunks.append)
        self.assertEqual(chunks, ["hi"])
        self.assertEqual(result["reasoning_content"], "thought")
        self.assertEqual([call["id"] for call in result["tool_calls"]], ["one", "two"])
        self.assertEqual(
            result["tool_calls"][1]["function"],
            {
                "name": "run_command",
                "arguments": '{"command":"pwd"}',
            },
        )

    async def test_deepseek_and_kimi_configure_native_token_limit_and_tools(self):
        for provider, host in (
            ("deepseek", "api.deepseek.com"),
            ("kimi", "api.moonshot.ai"),
        ):
            with self.subTest(provider=provider):
                result = await self.request(
                    [chat_event({"content": "OK"}, "stop")],
                    provider=provider,
                    tools=get_tools_schema(),
                    extra_body={"max_tokens": 24},
                )
                self.assertEqual(result["content"], "OK")
                request = self.requests[0]
                self.assertEqual(request.url.host, host)
                self.assertEqual(request.url.path, "/v1/chat/completions")
                payload = json.loads(request.content)
                self.assertTrue(payload["stream"])
                self.assertEqual(payload["max_tokens"], 24)
                self.assertNotIn("max_completion_tokens", payload)
                self.assertEqual(payload["tools"], get_tools_schema())

    async def test_openai_responses_preserves_native_output_and_replays_tools(self):
        item = {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_1",
            "name": "run_command",
            "arguments": '{"command":"pwd"}',
            "status": "completed",
        }
        result = await self.request(
            [response_completed([item])],
            provider="openai",
            tools=get_tools_schema(),
            max_tokens=64,
        )
        self.assertEqual(result["response_output"], [item])
        self.assertEqual(result["response_id"], "resp_1")
        self.assertEqual(result["tool_calls"][0]["id"], "call_1")
        self.assertEqual(result["finish_reason"], "tool_calls")
        payload = json.loads(self.requests[0].content)
        self.assertEqual(self.requests[0].url.path, "/v1/responses")
        self.assertEqual(payload["max_output_tokens"], 64)
        self.assertEqual(payload["tools"][0]["name"], "run_command")
        await self.request(
            [response_completed([])],
            provider="openai",
            messages=MESSAGES
            + [result, {"role": "tool", "tool_call_id": "call_1", "content": "done"}],
        )
        replay = json.loads(self.requests[0].content)["input"]
        self.assertTrue(
            any(
                entry.get("call_id") == "call_1" and entry["type"] == "function_call"
                for entry in replay
            )
        )
        self.assertTrue(
            any(
                entry.get("call_id") == "call_1"
                and entry["type"] == "function_call_output"
                for entry in replay
            )
        )

    async def test_disconnect_remains_a_failed_turn(self):
        with self.assertRaises(IncompleteStreamError):
            await self.request([chat_event({"content": "partial"})])

    async def test_cross_provider_chat_and_responses_tool_history(self):
        call = {
            "index": 0,
            "id": "cross_call",
            "type": "function",
            "function": {"name": "run_command", "arguments": '{"command":"pwd"}'},
        }
        chat = await self.request(
            [
                chat_event(
                    {"reasoning_content": "kept reasoning", "tool_calls": [call]},
                    "tool_calls",
                )
            ],
            provider="kimi",
        )
        tool = {"role": "tool", "tool_call_id": "cross_call", "content": "result"}
        native = {
            "type": "function_call",
            "id": "fc_2",
            "call_id": "second_call",
            "name": "run_command",
            "arguments": '{"command":"pwd"}',
            "status": "completed",
        }
        responses = await self.request(
            [response_completed([native])],
            provider="openai",
            messages=MESSAGES + [chat, tool],
        )
        items = json.loads(self.requests[0].content)["input"]
        self.assertTrue(
            any(
                item.get("call_id") == "cross_call" and item["type"] == "function_call"
                for item in items
            )
        )
        self.assertTrue(
            any(
                item.get("call_id") == "cross_call"
                and item["type"] == "function_call_output"
                for item in items
            )
        )
        next_tool = {
            "role": "tool",
            "tool_call_id": "second_call",
            "content": "next result",
        }
        await self.request(
            [chat_event({"content": "continued"}, "stop")],
            provider="deepseek",
            messages=MESSAGES + [chat, tool, responses, next_tool],
        )
        replay = json.loads(self.requests[0].content)["messages"]
        self.assertEqual(replay[1]["reasoning_content"], "kept reasoning")
        self.assertEqual(replay[-2]["tool_calls"][0]["id"], "second_call")
        self.assertEqual(replay[-1]["tool_call_id"], "second_call")

    async def test_token_limit_is_not_committed_as_success(self):
        with self.assertRaisesRegex(StreamError, "长度上限") as failure:
            await self.request([chat_event({"content": "partial"}, "length")])
        self.assertEqual(failure.exception.partial_message["finish_reason"], "length")

    async def test_other_completion_reasons_are_returned_without_application_mapping(
        self,
    ):
        for reason in ("stop", "content_filter", "custom_reason"):
            with self.subTest(reason=reason):
                message = await self.request([chat_event({"content": "OK"}, reason)])
                self.assertEqual(message["finish_reason"], reason)

    async def test_sdk_authentication_error_is_preserved(self):
        with self.assertRaises(AuthenticationError):
            await self.request([], status=401)

    async def test_stream_closes_when_ui_callback_fails(self):
        closed = []

        async def chunks():
            try:
                yield MessageChunk(content="hi")
                yield MessageChunk(finish_reason="stop")
            finally:
                closed.append(True)

        adapter = Mock(generate=AsyncMock(return_value=MessageStream(chunks())))
        with patch.object(llm, "create_llm", return_value=adapter):
            with self.assertRaisesRegex(RuntimeError, "display failed"):
                await llm.stream_reply(
                    MESSAGES, on_text=Mock(side_effect=RuntimeError("display failed"))
                )
        self.assertEqual(closed, [True])

    async def test_stream_closes_on_cancellation(self):
        entered = asyncio.Event()
        closed = asyncio.Event()

        async def chunks():
            try:
                entered.set()
                yield MessageChunk(content="hi")
                await asyncio.Event().wait()
            finally:
                closed.set()

        adapter = Mock(generate=AsyncMock(return_value=MessageStream(chunks())))
        with patch.object(llm, "create_llm", return_value=adapter):
            task = asyncio.create_task(llm.stream_reply(MESSAGES))
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(closed.is_set())


class ProviderTests(unittest.TestCase):
    def test_provider_keys_models_and_protocols(self):
        for name, key, protocol, api in (
            ("deepseek", "DEEPSEEK_API_KEY", "openai", "chat_completions"),
            ("kimi", "KIMI_API_KEY", "openai", "chat_completions"),
            ("kimi", "MOONSHOT_API_KEY", "openai", "chat_completions"),
            ("openai", "OPENAI_API_KEY", "openai", "responses"),
            ("anthropic", "ANTHROPIC_API_KEY", "anthropic", "chat_completions"),
            ("gemini", "GOOGLE_API_KEY", "gemini", "chat_completions"),
            ("ollama", None, "ollama", "chat_completions"),
        ):
            with (
                self.subTest(name=name, key=key),
                patch.dict(
                    os.environ, {key: "test-placeholder"} if key else {}, clear=True
                ),
            ):
                provider, model = llm.get_profiles(name, "test-model")
                self.assertEqual(provider.protocol, protocol)
                self.assertEqual(provider.api_type, api)
                self.assertTrue(model.supports_function_calling)
                llm.create_llm(name, "test-model")

    def test_only_credentials_are_read_from_environment(self):
        with patch.dict(
            os.environ,
            {
                "DEEPSEEK_API_KEY": "test-placeholder",
                "LLM_PROVIDER": "unknown",
                "LLM_MODEL": "ignored",
                "DEEPSEEK_MODEL": "ignored",
                "LLM_BASE_URL": "https://ignored.test",
                "LLM_MAX_TOKENS": "invalid",
                "LLM_API_TYPE": "invalid",
            },
            clear=True,
        ):
            provider, model = llm.get_profiles("deepseek", "explicit-model")
            self.assertEqual(model.model, "explicit-model")
            self.assertEqual(provider.api_type, "chat_completions")
            self.assertEqual(provider.base_url, "https://api.deepseek.com/v1")
            self.assertIsNone(model.max_tokens)

    def test_native_profile_settings_are_preserved_without_mutating_presets(self):
        model = llm.ModelProfile(
            model="custom",
            supports_function_calling=True,
            extra_body={"max_tokens": 12, "thinking": {"type": "enabled"}},
        )
        preset = replace(
            llm.PROVIDERS["deepseek"], models=[model], timeout=90, max_retries=1
        )
        with (
            patch.dict(llm.PROVIDERS, {"deepseek": preset}),
            patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test"}, clear=True),
        ):
            provider, configured_model = llm.get_profiles("deepseek", "custom")
            self.assertEqual(provider.timeout, 90)
            self.assertEqual(provider.max_retries, 1)
            self.assertIs(configured_model, model)
            self.assertEqual(
                configured_model.extra_body,
                {"max_tokens": 12, "thinking": {"type": "enabled"}},
            )
            self.assertIsNone(configured_model.max_tokens)
            self.assertIsNone(preset.api_key)

    def test_missing_credentials_do_not_fall_back_to_openai_key(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "unrelated"}, clear=True):
            with self.assertRaisesRegex(ConfigurationError, "DEEPSEEK_API_KEY"):
                llm.create_llm()

    def test_missing_model_and_unknown_provider_have_command_guidance(self):
        with self.assertRaisesRegex(ConfigurationError, "未知供应商"):
            llm.get_provider("unknown")
        with patch.dict(
            llm.PROVIDERS, {"kimi": replace(llm.PROVIDERS["kimi"], models=[])}
        ):
            with self.assertRaisesRegex(ConfigurationError, "/model"):
                llm.get_model("kimi")
