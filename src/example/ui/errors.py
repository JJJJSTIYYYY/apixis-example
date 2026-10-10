"""Translate transport and workflow failures into useful next steps."""

import httpx

from .terminal import plain_text


def explain_error(error: Exception, *, can_retry: bool = False) -> tuple[str, str]:
    """Keep credentials, response bodies, and internal tracebacks out of the UI."""
    retry = (
        "输入 /retry 继续，或输入新问题开始下一轮。"
        if can_retry
        else "请稍后重新运行。"
    )
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        if status in (401, 403):
            return (
                "API 凭证无效或没有访问权限。",
                "请检查 DEEPSEEK_API_KEY 与模型权限后重新启动。",
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
    if isinstance(error, (httpx.TimeoutException, TimeoutError)):
        return "等待回复超时，本轮没有保存到历史。", "请检查网络或稍后再试。" + retry
    if isinstance(error, (httpx.NetworkError, httpx.ProxyError)):
        return "无法连接模型服务。", "请检查网络、代理与 DEEPSEEK_BASE_URL。" + retry
    detail = plain_text(error)
    if "DEEPSEEK_API_KEY environment variable is not set" in detail:
        return (
            "尚未设置 API Key。",
            "请设置 DEEPSEEK_API_KEY 环境变量后重新启动；uv run example --check 可用于本地检查。",
        )
    if "maximum" in detail and "steps" in detail:
        return (
            "本轮已达到执行步数上限。",
            "请缩小任务范围并重新提问；/retry 不会增加步数额度。",
        )
    if "token limit" in detail:
        return "回复达到输出长度上限。", "请提高 DEEPSEEK_MAX_TOKENS 后重新启动。"
    if "before [DONE]" in detail:
        return "回复传输中断，屏幕上的内容可能不完整。", retry
    return "操作未完成。", (detail[:240] or "请稍后再试。") + (
        "\n" + retry if can_retry else ""
    )
