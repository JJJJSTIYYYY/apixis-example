"""Public MyLLM profiles; model catalogue verified on 2026-10-16.

See docs/guides/model-presets.md for sources. The first model is each provider's default.
"""

from dataclasses import replace

from my_llm import Attribute, Config, ModelProfile, Parameter, ProviderProfile, omit

PRESETS_VERIFIED_ON = "2026-10-16"

PROVIDERS: dict[str, ProviderProfile] = {
    "deepseek": ProviderProfile(
        protocol=Config(default_value="openai_chat"),
        base_url=Config(default_value="https://api.deepseek.com/v1"),
        usage_mode=Config(default_value="stream_options"),
        reasoning_effort=Parameter(path="$.reasoning_effort"),
        reasoning_effort_mapping=Config(
            default_value={"medium": "high", "xhigh": "high"}
        ),
        reasoning_enable=Parameter(
            path="$.extra_body.thinking", default_value={"type": "enabled"}
        ),
        # Optional native output budgets go directly in ModelProfile.extra_body:
        # Config(default_value={"max_tokens": 4096}).
        models=Attribute(
            default_value=[
                ModelProfile(
                    model=Attribute(default_value=model_id),
                    max_context_window=Attribute(default_value=1_048_576),
                    supports_function_calling=Config(default_value=True),
                )
                for model_id in ("deepseek-flash", "deepseek-v4-pro")
            ]
        ),
    ),
    "kimi": ProviderProfile(
        protocol=Config(default_value="openai_chat"),
        base_url=Config(default_value="https://api.moonshot.ai/v1"),
        usage_mode=Config(default_value="stream_options"),
        reasoning_enable=Parameter(
            path="$.extra_body.thinking", default_value={"type": "enabled"}
        ),
        # Native budget: max_completion_tokens for K3, max_tokens for K2.x.
        models=Attribute(
            default_value=[
                ModelProfile(
                    model=Attribute(default_value="kimi-k3"),
                    max_context_window=Attribute(default_value=1_000_000),
                    supports_function_calling=Config(default_value=True),
                    reasoning_effort=Parameter(path="$.reasoning_effort"),
                    # K3 always thinks and rejects the old K2.x thinking switch.
                    reasoning_enable=omit,
                    reasoning_effort_mapping=Config(
                        default_value={"medium": None, "xhigh": None}
                    ),
                ),
                *[
                    ModelProfile(
                        model=Attribute(default_value=model_id),
                        max_context_window=Attribute(default_value=256_000),
                        supports_function_calling=Config(default_value=True),
                        reasoning_effort=Parameter(
                            path="$.reasoning_effort", supports=False
                        ),
                    )
                    for model_id in (
                        "kimi-k2.7-code",
                        "kimi-k2.7-code-highspeed",
                        "kimi-k2.6",
                    )
                ],
            ]
        ),
    ),
    "openai": ProviderProfile(
        protocol=Config(default_value="openai_responses"),
        models=Attribute(
            default_value=[
                ModelProfile(
                    model=Attribute(default_value=model_id),
                    max_context_window=Attribute(default_value=1_050_000),
                    supports_function_calling=Config(default_value=True),
                )
                for model_id in ("gpt-6.1-sol", "gpt-6-astra", "gpt-6-luna")
            ]
        ),
    ),
    "anthropic": ProviderProfile(
        protocol=Config(default_value="anthropic"),
        reasoning_effort=Parameter(path="$.output_config.effort"),
        extra_body=Config(default_value={"thinking": {"type": "adaptive"}}),
        reasoning_enable=Parameter(
            path="$.thinking", default_value={"type": "adaptive"}
        ),
        models=Attribute(
            default_value=[
                ModelProfile(
                    model=Attribute(default_value=model_id),
                    max_context_window=Attribute(default_value=1_000_000),
                    extra_body=Config(default_value={"max_tokens": 4096}),
                    supports_function_calling=Config(default_value=True),
                )
                for model_id in (
                    "claude-sonnet-5-5",
                    "claude-opus-5-5",
                    "claude-haiku-5-5",
                    "claude-fable-5-1",
                )
            ]
        ),
    ),
    "gemini": ProviderProfile(
        protocol=Config(default_value="gemini"),
        reasoning_effort=Parameter(path="$.config.thinking_config.thinking_level"),
        reasoning_enable=omit,
        reasoning_effort_mapping=Config(default_value={"xhigh": None, "max": None}),
        models=Attribute(
            default_value=[
                ModelProfile(
                    model=Attribute(default_value=model_id),
                    max_context_window=Attribute(default_value=1_048_576),
                    supports_function_calling=Config(default_value=True),
                )
                for model_id in ("gemini-3.8-flash", "gemini-3.5-flash-lite")
            ]
        ),
    ),
    "ollama": ProviderProfile(
        protocol=Config(default_value="ollama"),
        models=Attribute(
            default_value=[
                ModelProfile(
                    model=Attribute(default_value=model_id),
                    max_context_window=Attribute(default_value=256_000),
                    supports_function_calling=Config(default_value=True),
                )
                for model_id in ("qwen3.5:9b", "qwen3.6:27b", "qwen3.6:35b")
            ]
        ),
    ),
}

# The regional connection shares Kimi's model and capability declarations.
PROVIDERS["kimi-cn"] = replace(
    PROVIDERS["kimi"],
    base_url=Config(default_value="https://api.moonshot.cn/v1"),
)

API_KEY_ENV: dict[str, tuple[str, ...]] = {
    "deepseek": ("DEEPSEEK_API_KEY",),
    "kimi": ("KIMI_API_KEY", "MOONSHOT_API_KEY"),
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "ollama": (),
}
API_KEY_ENV["kimi-cn"] = API_KEY_ENV["kimi"]
