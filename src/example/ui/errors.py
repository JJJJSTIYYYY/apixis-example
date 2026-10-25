"""Translate public my-llm-py and SDK failures into useful next steps."""

import anthropic
import httpx
import openai
from google.genai.errors import APIError as GeminiAPIError
from my_llm import (
    ConfigurationError,
    IncompleteStreamError,
    ResponseIncompleteError,
    SemanticLossError,
    StreamError,
)
from ollama import ResponseError as OllamaResponseError

from .terminal import plain_text


def explain_error(error: Exception, *, can_retry: bool = False) -> tuple[str, str]:
    """Keep credentials, response bodies, and internal tracebacks out of the UI."""
    retry = (
        "输入 /retry 继续，或输入新问题开始下一轮。"
        if can_retry
        else "请稍后重新运行。"
    )
    status = None
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
    elif isinstance(
        error, (openai.APIStatusError, anthropic.APIStatusError, OllamaResponseError)
    ):
        status = error.status_code
    elif isinstance(error, GeminiAPIError):
        status = error.code
    if status is not None:
        if status in (401, 403):
            return (
                "API 凭证无效或没有访问权限。",
                "请检查当前供应商的 API Key 与模型权限后重新启动。",
            )
        if status == 402:
            return "模型服务余额不足。", "请检查服务账户余额后重新启动。"
        if status == 429:
            return "请求过于频繁，模型服务暂时限流。", "稍等片刻后再试。" + retry
        if status >= 500:
            return "模型服务暂时不可用。", retry
        return (
            f"模型服务拒绝了请求（HTTP {status}）。",
            "请检查模型名、API 地址与请求配置。",
        )
    if isinstance(
        error,
        (
            httpx.TimeoutException,
            TimeoutError,
            openai.APITimeoutError,
            anthropic.APITimeoutError,
        ),
    ):
        return "等待回复超时，本轮没有保存到历史。", "请检查网络或稍后再试。" + retry
    if isinstance(
        error,
        (
            httpx.NetworkError,
            httpx.ProxyError,
            openai.APIConnectionError,
            anthropic.APIConnectionError,
        ),
    ):
        return (
            "无法连接模型服务。",
            "请检查网络、代理与当前 ProviderProfile.base_url。" + retry,
        )
    if isinstance(error, SemanticLossError):
        return (
            "当前供应商协议无法完整表达这段对话的部分内容。",
            "对话和重试状态已保留；请切回兼容的供应商或模型后重试，或用 /new 开始新对话。"
            + (retry if can_retry else ""),
        )
    if isinstance(error, ConfigurationError):
        return "模型配置无效。", plain_text(error)
    if isinstance(error, StreamError):
        if "长度上限" in str(error) or (
            isinstance(error, ResponseIncompleteError)
            and isinstance(error.details, dict)
            and error.details.get("reason") == "max_output_tokens"
        ):
            return (
                "回复达到输出长度上限。",
                "请提高当前模型 Profile 的输出预算，或切换到预算充足的模型。" + retry,
            )
        if isinstance(error, IncompleteStreamError):
            return "回复传输中断，屏幕上的内容可能不完整。", retry
        return (
            "模型未能完成回复，本轮没有保存。",
            "请检查模型服务状态与输出长度设置。" + retry,
        )
    detail = plain_text(error)
    if "maximum" in detail and "steps" in detail:
        return (
            "本轮已达到执行步数上限。",
            "请缩小任务范围并重新提问；/retry 不会增加步数额度。",
        )
    return "操作未完成。", (detail[:240] or "请稍后再试。") + (
        "\n" + retry if can_retry else ""
    )
