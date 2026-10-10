"""Test streaming assembly and transport errors without paid API requests."""

import json
import unittest
from unittest.mock import patch

import httpx

from example.core import llm


def sse(delta, finish_reason=None):
    return (
        "data: "
        + json.dumps({"choices": [{"delta": delta, "finish_reason": finish_reason}]})
        + "\n\n"
    )


class LLMTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, body, *, status=200, on_text=None):
        def handle(request):
            payload = json.loads(request.content)
            self.assertTrue(payload["stream"])
            self.assertEqual(request.url.path, "/v1/chat/completions")
            return httpx.Response(status, text=body)

        async with httpx.AsyncClient(
            base_url="https://llm.test", transport=httpx.MockTransport(handle)
        ) as client:
            with (
                patch.object(llm, "_client", client),
                patch.object(llm, "DEEPSEEK_API_KEY", "test-placeholder"),
            ):
                return await llm.stream_deepseek([], on_text=on_text)

    async def test_content_and_fragmented_tool_calls(self):
        chunks = []
        body = sse(
            {
                "content": "hi",
                "tool_calls": [
                    {
                        "index": 1,
                        "id": "two",
                        "function": {"name": "run_", "arguments": '{"command":'},
                    }
                ],
            }
        )
        body += sse(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "one",
                        "function": {"name": "run_command", "arguments": "{}"},
                    },
                    {
                        "index": 1,
                        "function": {"name": "command", "arguments": '"pwd"}'},
                    },
                ]
            },
            "tool_calls",
        )
        result = await self.request(body + "data: [DONE]\n\n", on_text=chunks.append)
        self.assertEqual(chunks, ["hi"])
        self.assertEqual([c["id"] for c in result["tool_calls"]], ["one", "two"])
        self.assertEqual(
            result["tool_calls"][1]["function"],
            {"name": "run_command", "arguments": '{"command":"pwd"}'},
        )

    async def test_disconnect_is_not_a_successful_turn(self):
        with self.assertRaisesRegex(RuntimeError, "before"):
            await self.request(sse({"content": "partial"}))

    async def test_token_limit_is_actionable(self):
        with self.assertRaisesRegex(RuntimeError, "token limit"):
            await self.request(
                sse({"content": "partial"}, "length") + "data: [DONE]\n\n"
            )

    async def test_http_error_is_preserved(self):
        with self.assertRaises(httpx.HTTPStatusError):
            await self.request('{"error":"unauthorized"}', status=401)

    async def test_max_tokens_and_missing_key(self):
        with patch.dict("os.environ", {"DEEPSEEK_MAX_TOKENS": "24"}):
            self.assertEqual(
                llm._build_payload([], None, stream=True)["max_tokens"], 24
            )
            self.assertEqual(
                llm._build_payload([], None, stream=True, max_tokens=12)["max_tokens"],
                12,
            )
        with patch.object(llm, "DEEPSEEK_API_KEY", ""):
            with self.assertRaisesRegex(RuntimeError, "not set"):
                await llm.stream_deepseek([])
