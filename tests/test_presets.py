"""Verify actual preset capabilities through public my-llm and SDK requests."""

import json
import os
import unittest
from dataclasses import replace
from unittest.mock import patch

import httpx
from my_llm import Config, ConfigurationError, MyLLM
from sdk_transport import sdk_transport

from example.core import llm
from example.core.tools import get_tools_schema


class PresetTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, provider, model, *, effort="none", extra_body=None):
        requests = []
        with patch.dict(os.environ, {"LLM_API_KEY": "local-test-only"}, clear=True):
            profile, selected = llm.get_profiles(provider, model)
        if extra_body is not None:
            selected = replace(selected, extra_body=Config(default_value=extra_body))

        def handle(request):
            requests.append(request)
            if profile.protocol.default_value == "openai_responses":
                event = {
                    "type": "response.completed",
                    "sequence_number": 1,
                    "response": {
                        "id": "resp_1",
                        "object": "response",
                        "created_at": 1,
                        "model": model,
                        "status": "completed",
                        "output": [],
                        "parallel_tool_calls": True,
                        "tools": [],
                        "tool_choice": "auto",
                    },
                }
            else:
                event = {
                    "id": "chat_1",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": "OK"},
                            "finish_reason": "stop",
                        }
                    ],
                }
            return httpx.Response(
                200,
                text=f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n",
                headers={"Content-Type": "text/event-stream"},
            )

        with sdk_transport(handle):
            async with MyLLM(
                provider=profile, model=selected, stdio_protocol="openai_chat"
            ) as adapter:
                await adapter.generate(
                    messages=[{"role": "user", "content": "hello"}],
                    tools=get_tools_schema(),
                    reasoning_effort=effort,
                )
        return json.loads(requests[0].content)

    async def test_kimi_k3_uses_effort_without_legacy_thinking_toggle(self):
        body = await self.request("kimi", "kimi-k3", effort="high")
        self.assertEqual(body["model"], "kimi-k3")
        self.assertEqual(body["reasoning_effort"], "high")
        self.assertNotIn("thinking", body)
        with self.assertRaises(ConfigurationError):
            await self.request("kimi", "kimi-k3", effort="medium")

    async def test_kimi_k3_native_output_budget_comes_from_model_extras(self):
        body = await self.request(
            "kimi", "kimi-k3", effort="low", extra_body={"max_completion_tokens": 512}
        )
        self.assertEqual(body["max_completion_tokens"], 512)
        self.assertNotIn("max_tokens", body)
        self.assertNotIn("thinking", body)

    async def test_kimi_k2_explicit_thinking_binding_omits_native_effort(self):
        body = await self.request("kimi", "kimi-k2.6", effort="high")
        self.assertEqual(body["thinking"], {"type": "enabled"})
        self.assertNotIn("reasoning_effort", body)

    async def test_deepseek_current_models_enable_thinking_and_map_supported_effort(
        self,
    ):
        for model in ("deepseek-flash", "deepseek-v4-pro"):
            with self.subTest(model=model):
                body = await self.request("deepseek", model, effort="medium")
                self.assertEqual(body["model"], model)
                self.assertEqual(body["thinking"], {"type": "enabled"})
                self.assertEqual(body["reasoning_effort"], "high")

    async def test_openai_current_models_use_responses_function_tools(self):
        for model in ("gpt-6.1-sol", "gpt-6-astra", "gpt-6-luna"):
            with self.subTest(model=model):
                body = await self.request("openai", model, effort="high")
                self.assertIn("input", body)
                self.assertEqual(body["model"], model)
                self.assertEqual(body["reasoning"]["effort"], "high")
                self.assertEqual(body["tools"][0]["name"], "run_command")

    async def test_all_presets_have_valid_function_calling_profiles(self):
        with patch.dict(os.environ, {"LLM_API_KEY": "local-test-only"}, clear=True):
            for name, preset in llm.PROVIDERS.items():
                self.assertTrue(preset.list_models(), name)
                for model in preset.list_models():
                    with self.subTest(provider=name, model=model.model.default_value):
                        self.assertTrue(model.supports_function_calling.default_value)
                        llm.create_llm(name, model.model.default_value)
        self.assertEqual(
            llm.get_model("deepseek").model.default_value, "deepseek-flash"
        )
