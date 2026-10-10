"""Safety plugins for example.

Two layers of protection, both implemented as non-invasive apixis plugins:

1. Input filter - event-driven, registered as a high-priority handler on the
   graph's dispatch event. Blocks forbidden words before they reach the LLM,
   without any modification to chat_loop.

2. Command guard - uses the graph's interrupt hook to auto-reject dangerous
   commands (like `rm -rf`) before the user even sees the approval prompt.

Both plugins run in the main process rather than the LLM. These simple
pattern checks are demonstrations, not a shell sandbox; explicit approval
is still required for every command that passes the guard.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from apixis import ApixEventHandler, get_graph_dispatch_name, subscribe, unsubscribe

# ── Input filter ──────────────────────────────────────────────────────


@dataclass
class InputCheckResult:
    """Result of checking user input against forbidden words."""

    ok: bool
    matched: list[str]

    @property
    def message(self) -> str:
        if self.ok:
            return ""
        words = "、".join(f"[bold red]{w}[/]" for w in self.matched)
        return f"输入包含违禁词：{words}，已拒绝"


class InputFilter:
    """Check user input for forbidden words before it reaches the LLM.

    The forbidden list is configurable; defaults cover the most common cases.
    Matching is case-insensitive substring match.
    """

    # Default forbidden words - can be extended via constructor
    DEFAULT_FORBIDDEN = [
        "自杀",
        "杀人",
        "炸弹",
        "毒品",
        "枪支",
    ]

    def __init__(self, extra_forbidden: list[str] | None = None) -> None:
        self.forbidden = list(self.DEFAULT_FORBIDDEN)
        if extra_forbidden:
            self.forbidden.extend(extra_forbidden)

    def check(self, text: str) -> InputCheckResult:
        """Check text against forbidden words, returning matched ones if any."""
        text_lower = text.lower()
        matched = [word for word in self.forbidden if word.lower() in text_lower]
        return InputCheckResult(ok=not matched, matched=matched)


def get_last_user_message(messages: list[dict]) -> str | None:
    """Extract the last user message from a message list."""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content")
            if isinstance(content, str):
                return content
    return None


def create_input_filter_handler(
    input_filter: InputFilter,
    *,
    name: str = "example_input_filter",
) -> ApixEventHandler:
    """Create an apixis event handler that intercepts forbidden input.

    The handler runs before the graph's dispatch handler (ordered subscription),
    checks the last user message for forbidden words, writes a rejection to the
    graph context's stream, and calls event.accept() to skip execution if found.

    Args:
        input_filter: The InputFilter instance to use for checking.

    Returns:
        An ApixEventHandler ready to be subscribed.
    """

    async def core_func(event) -> None:
        """Check for forbidden words and accept the event to skip LLM."""
        context = event.context
        messages = context.state.get("messages", [])
        last_user_msg = get_last_user_message(messages)

        if last_user_msg is None:
            return  # No user message to check

        result = input_filter.check(last_user_msg)
        if not result.ok:
            # Emit before accepting dispatch: abort closes the stream after
            # already queued chunks have been consumed.
            context.stream_writer(
                {
                    "type": "error",
                    "message": f"这条输入被当前过滤规则拦截：{'、'.join(result.matched)}。",
                    "hint": "请调整表达后重新发送，本轮未保存。",
                }
            )
            event.accept()  # Skip the LLM node

    return ApixEventHandler(
        core_func,
        name=name,
    )


def register_input_filter_plugin(
    namespace: str,
    input_filter: InputFilter,
) -> ApixEventHandler:
    """Register the input filter as an apixis plugin.

    This is the non-invasive way: we subscribe to the graph's dispatch event
    and use ``between_handlers`` to insert ourselves right before the graph's
    built-in dispatch handler, so the check runs before the LLM node executes.

    Args:
        namespace: The graph's namespace (e.g., "example-agent").
        input_filter: The InputFilter instance.

    Returns:
        The registered handler (for later unregistration if needed).
    """
    dispatch_name = get_graph_dispatch_name(namespace)
    handler = create_input_filter_handler(
        input_filter, name=f"{namespace}.input_filter"
    )
    subscribe(dispatch_name, between_handlers=(None, dispatch_name))(handler)
    return handler


def unregister_input_filter_plugin(handler: ApixEventHandler) -> None:
    """Unregister exactly the filter owned by this session."""
    unsubscribe(handler.name)


# ── Command guard ─────────────────────────────────────────────────────


@dataclass
class CommandCheckResult:
    """Result of checking a command for dangerous patterns."""

    ok: bool
    reason: str

    @property
    def message(self) -> str:
        if self.ok:
            return ""
        return f"命令被安全策略拒绝：{self.reason}"


class CommandGuard:
    """Reject dangerous commands before they reach the shell.

    The guard matches patterns, not exact commands, so `rm -rf /tmp` is as
    forbidden as `rm -rf /` — the risk is the recursive forced deletion, not
    the target path. Pipes and chains are checked by splitting on `;`, `|`,
    `&&`, `||` so `echo hi && rm -rf /` is still caught.
    """

    # Patterns that are always rejected, with a human-readable reason.
    # Order matters: more specific patterns first so the error message is clear.
    DANGEROUS_PATTERNS: list[tuple[re.Pattern, str]] = [
        # rm -rf variations (with or without flags, targeting dangerous paths)
        (
            re.compile(
                r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\b.*(/\s*$|/\s+|\.\s*$|/\.)"
            ),
            "递归强制删除系统路径",
        ),
        (
            re.compile(r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\s+/"),
            "递归强制删除根路径",
        ),
        (
            re.compile(
                r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\s+\.\s*$"
            ),
            "递归强制删除当前目录",
        ),
        # Simpler catch-all for rm -rf anywhere
        (re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f"), "rm -rf 被禁止"),
        (re.compile(r"\brm\s+-[a-zA-Z]*f[a-zA-Z]*r"), "rm -fr 被禁止"),
        # Format disk
        (re.compile(r"\bmkfs\b"), "格式化磁盘被禁止"),
        (re.compile(r"\bdd\s+.*of=/dev/"), "dd 写入设备被禁止"),
        # Overwrite everything
        (re.compile(r":\(\)\s*\{"), "fork bomb 被禁止"),
    ]

    def check(self, command: str) -> CommandCheckResult:
        """Check a command string for dangerous patterns.

        Splits on shell operators so `cmd1 && cmd2` is checked as two commands.
        Returns the first match, if any.
        """
        # Split on shell operators to check each command independently
        parts = re.split(r"\s*(?:&&|\|\||;|\|)\s*", command)
        for part in parts:
            part = part.strip()
            if not part:
                continue
            for pattern, reason in self.DANGEROUS_PATTERNS:
                if pattern.search(part):
                    return CommandCheckResult(ok=False, reason=reason)
        return CommandCheckResult(ok=True, reason="")


def check_tool_call_command_guard(
    arguments: str, guard: CommandGuard
) -> CommandCheckResult | None:
    """Check a tool call's command argument against the command guard.

    This is used by the interrupt hook to auto-reject dangerous commands.

    Args:
        arguments: JSON string of tool call arguments.
        guard: The CommandGuard instance.

    Returns:
        CommandCheckResult if the tool call has a command argument,
        None otherwise.
    """
    try:
        args_dict = json.loads(arguments)
        command = args_dict.get("command")
        if isinstance(command, str):
            return guard.check(command)
    except json.JSONDecodeError, AttributeError:
        pass
    return None


# ── Convenience instances ─────────────────────────────────────────────

input_filter = InputFilter()
command_guard = CommandGuard()


__all__ = [
    "InputFilter",
    "InputCheckResult",
    "CommandGuard",
    "CommandCheckResult",
    "input_filter",
    "command_guard",
    "create_input_filter_handler",
    "register_input_filter_plugin",
    "unregister_input_filter_plugin",
    "get_last_user_message",
    "check_tool_call_command_guard",
]
