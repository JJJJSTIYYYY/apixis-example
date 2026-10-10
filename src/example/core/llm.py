"""DeepSeek LLM API client using OpenAI-compatible endpoint."""

import json
import os
from collections.abc import Callable
from typing import Any

import httpx

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            base_url=DEEPSEEK_BASE_URL,
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            timeout=120.0,
        )
    return _client


def _require_api_key() -> None:
    if not DEEPSEEK_API_KEY:
        raise RuntimeError(
            "DEEPSEEK_API_KEY environment variable is not set. "
            "Set it with: export DEEPSEEK_API_KEY=your_key"
        )


def _build_payload(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    *,
    stream: bool,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "stream": stream,
    }
    if max_tokens is None and os.environ.get("DEEPSEEK_MAX_TOKENS"):
        max_tokens = int(os.environ["DEEPSEEK_MAX_TOKENS"])
    if max_tokens is not None:
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        payload["max_tokens"] = max_tokens
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    return payload


def _accumulate_tool_call(
    accumulator: dict[int, dict[str, Any]],
    fragment: dict[str, Any],
) -> None:
    """Merge one streamed tool-call fragment into the accumulator by index."""
    index = fragment.get("index", 0)
    entry = accumulator.setdefault(
        index,
        {
            "id": "",
            "type": "function",
            "function": {"name": "", "arguments": ""},
        },
    )
    if fragment.get("id"):
        entry["id"] = fragment["id"]
    if fragment.get("type"):
        entry["type"] = fragment["type"]

    function = fragment.get("function") or {}
    # Name and arguments may arrive split across several fragments.
    if function.get("name"):
        entry["function"]["name"] += function["name"]
    if function.get("arguments"):
        entry["function"]["arguments"] += function["arguments"]


async def stream_deepseek(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    on_text: Callable[[str], None] | None = None,
    *,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """Stream a DeepSeek completion and assemble the full assistant message.

    Args:
        messages: OpenAI-format message list.
        tools: Optional OpenAI-format tool definitions.
        on_text: Called with each text delta as it arrives.
        max_tokens: Optional output budget for small smoke tests.

    Returns:
        The assembled assistant message dict (``content`` and/or ``tool_calls``).
    """
    _require_api_key()
    client = _get_client()
    payload = _build_payload(messages, tools, stream=True, max_tokens=max_tokens)

    completed = False
    finish_reason = None
    content_parts: list[str] = []
    tool_calls: dict[int, dict[str, Any]] = {}

    async with client.stream("POST", "/v1/chat/completions", json=payload) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            data = line[len("data:") :].strip()
            if data == "[DONE]":
                completed = True
                break
            if not data:
                continue

            choices = json.loads(data).get("choices") or []
            if not choices:
                continue
            finish_reason = choices[0].get("finish_reason") or finish_reason
            delta = choices[0].get("delta") or {}

            text = delta.get("content")
            if text:
                content_parts.append(text)
                if on_text is not None:
                    on_text(text)

            for fragment in delta.get("tool_calls") or []:
                _accumulate_tool_call(tool_calls, fragment)

    if not completed:
        raise RuntimeError("The completion stream ended before [DONE]; use /retry")
    if finish_reason == "length":
        raise RuntimeError(
            "The completion hit its token limit; increase DEEPSEEK_MAX_TOKENS"
        )

    message: dict[str, Any] = {
        "role": "assistant",
        "content": "".join(content_parts) if content_parts else None,
    }
    if tool_calls:
        message["tool_calls"] = [tool_calls[index] for index in sorted(tool_calls)]
    return message


async def call_deepseek(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return the assembled assistant message without a streaming callback."""
    return await stream_deepseek(messages, tools)


async def close_client() -> None:
    """Close the reusable HTTP client before the event loop is stopped."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
