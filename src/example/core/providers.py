"""Native my-llm-py presets verified against official docs on 2026-10-16.

Sources and selection notes: docs/model-presets.md. The first model is the
application default for each provider; explicit IDs remain accepted by /model.
"""

from my_llm import ModelProfile, ProviderProfile, omit

PRESETS_VERIFIED_ON = "2026-10-16"

PROVIDERS: dict[str, ProviderProfile] = {
    "deepseek": ProviderProfile(
        base_url="https://api.deepseek.com/v1",
        supports_reasoning_effort=True,
        reasoning_effort_mapping={"medium": "high", "xhigh": "high"},
        # Optional output budget belongs directly in ModelProfile.extra_body:
        # extra_body={"max_tokens": 4096}. Omission keeps the service default.
        models=[
            ModelProfile(
                model=model_id,
                max_context_window=1_048_576,
                supports_function_calling=True,
                supports_reasoning=True,
            )
            for model_id in ("deepseek-flash", "deepseek-v4-pro")
        ],
    ),
    "kimi": ProviderProfile(
        base_url="https://api.moonshot.ai/v1",
        # Output budgets use ModelProfile.extra_body: max_completion_tokens
        # for K3, max_tokens for K2.x. No application-side conversion.
        models=[
            ModelProfile(
                model="kimi-k3",
                max_context_window=1_000_000,
                supports_function_calling=True,
                supports_reasoning=True,
                supports_reasoning_effort=True,
                # K3 always thinks and rejects the old K2.x thinking switch.
                reasoning_enable_parameter=omit,
                reasoning_effort_mapping={"medium": None, "xhigh": None},
            ),
            *[
                ModelProfile(
                    model=model_id,
                    max_context_window=256_000,
                    supports_function_calling=True,
                    supports_reasoning=True,
                    supports_reasoning_effort=False,
                )
                for model_id in (
                    "kimi-k2.7-code",
                    "kimi-k2.7-code-highspeed",
                    "kimi-k2.6",
                )
            ],
        ],
    ),
    "openai": ProviderProfile(
        # Current Sol/Astra require Responses for function calling.
        api_type="responses",
        models=[
            ModelProfile(
                model=model_id,
                max_context_window=1_050_000,
                supports_function_calling=True,
                supports_reasoning=True,
                supports_reasoning_effort=True,
            )
            for model_id in ("gpt-6.1-sol", "gpt-6-astra", "gpt-6-luna")
        ],
    ),
    "anthropic": ProviderProfile(
        protocol="anthropic",
        supports_reasoning_effort=True,
        extra_body={"thinking": {"type": "adaptive"}},
        reasoning_enable_parameter={"extra_body": {"thinking": {"type": "adaptive"}}},
        models=[
            ModelProfile(
                model=model_id,
                max_context_window=1_000_000,
                max_tokens=4096,  # Application output budget, not the model limit.
                supports_function_calling=True,
                supports_reasoning=True,
            )
            for model_id in (
                "claude-sonnet-5-5",
                "claude-opus-5-5",
                "claude-haiku-5-5",
                "claude-fable-5-1",
            )
        ],
    ),
    "gemini": ProviderProfile(
        protocol="gemini",
        supports_reasoning_effort=True,
        reasoning_enable_parameter=omit,
        reasoning_effort_mapping={"xhigh": None, "max": None},
        models=[
            ModelProfile(
                model=model_id,
                max_context_window=1_048_576,
                supports_function_calling=True,
                supports_reasoning=True,
            )
            for model_id in ("gemini-3.8-flash", "gemini-3.5-flash-lite")
        ],
    ),
    "ollama": ProviderProfile(
        protocol="ollama",
        supports_reasoning_effort=False,
        models=[
            ModelProfile(
                model=model_id,
                max_context_window=256_000,
                supports_function_calling=True,
                supports_reasoning=True,
            )
            for model_id in ("qwen3.5:9b", "qwen3.6:27b", "qwen3.6:35b")
        ],
    ),
}

# Credentials belong to the selected provider; other settings stay in Profiles.
API_KEY_ENV: dict[str, tuple[str, ...]] = {
    "deepseek": ("DEEPSEEK_API_KEY",),
    "kimi": ("KIMI_API_KEY", "MOONSHOT_API_KEY"),
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "ollama": (),
}
