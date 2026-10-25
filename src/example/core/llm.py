"""Use public my-llm-py profiles and message streams for the selected model."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from my_llm import (
    Attribute,
    Config,
    ConfigurationError,
    ModelProfile,
    MyLLM,
    Parameter,
    ProviderProfile,
    StreamError,
)

from .providers import API_KEY_ENV, PROVIDERS


def get_provider(name: str) -> ProviderProfile:
    """Look up application configuration without reading selection from the environment."""
    if name not in PROVIDERS:
        raise ConfigurationError(f"未知供应商：{name}；可用值：{', '.join(PROVIDERS)}")
    return PROVIDERS[name]


def get_model(name: str, model_id: str | None = None) -> ModelProfile:
    """Use a registered model's full profile, or accept an explicit model ID."""
    models = get_provider(name).list_models()
    model_id = (
        model_id
        if model_id is not None
        else (models[0].model.default_value if models else None)
    )
    if not model_id:
        raise ConfigurationError(f"请使用 /model <模型ID> 为 {name} 选择模型。")
    return next(
        (model for model in models if model.model.default_value == model_id),
        ModelProfile(
            model=Attribute(default_value=model_id),
            supports_function_calling=Config(default_value=True),
            extra_body=Config(default_value={"max_tokens": 4096})
            if name == "anthropic"
            else None,
        ),
    )


def get_profiles(
    name: str = "deepseek", model_id: str | None = None
) -> tuple[ProviderProfile, ModelProfile]:
    """Read only credentials from the environment; all other settings are Profiles."""
    preset = get_provider(name)
    model = get_model(name, model_id)
    keys = API_KEY_ENV[name]
    api_key = next(
        (os.environ[key] for key in keys if os.environ.get(key)),
        os.environ.get("LLM_API_KEY"),
    )
    if keys and not api_key:
        raise ConfigurationError(
            f"尚未设置 API Key；请设置 {' / '.join(keys)} 或 LLM_API_KEY。"
        )
    provider = replace(
        preset,
        api_key=Parameter(
            path="$.extra_headers.Authorization" if name == "ollama" else "$.api_key",
            default_value=f"Bearer {api_key}"
            if name == "ollama" and api_key
            else api_key,
        ),
        timeout=preset.timeout
        if preset.timeout is not None
        else Config(default_value=120.0),
        max_retries=preset.max_retries
        if preset.max_retries is not None
        else Config(default_value=0),
    )
    return provider, model


def create_llm(provider: str = "deepseek", model: str | None = None) -> MyLLM:
    """Create an instance whose SDK clients must be closed by its caller."""
    provider_profile, model_profile = get_profiles(provider, model)
    return MyLLM(
        provider=provider_profile, model=model_profile, stdio_protocol="openai_chat"
    )


def model_label(provider: str = "deepseek", model: str | None = None) -> str:
    preset = get_provider(provider)
    if model is None:
        models = preset.list_models()
        model = (
            models[0].model.default_value
            if models
            else "未选择模型，输入 /model <模型ID>"
        )
    return f"{provider} / {model}"


async def stream_reply(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    on_text: Callable[[str], None] | None = None,
    *,
    provider: str = "deepseek",
    model: str | None = None,
) -> dict[str, Any]:
    """Pass complete history unchanged; the library handles protocol compatibility."""
    async with create_llm(provider, model) as adapter:
        stream = await adapter.generate(messages=messages, tools=tools, stream=True)
        truncated = False
        async with stream:
            async for event in stream:
                for choice in event.get("choices", []):
                    if choice.get("finish_reason") == "length":
                        truncated = True
                    text = choice.get("delta", {}).get("content")
                    if text and on_text is not None:
                        on_text(text)
        if truncated:
            raise StreamError(
                "回复达到输出长度上限；请调整当前模型 Profile 的输出预算。",
                partial_message=stream.result,
            )
        return stream.result
