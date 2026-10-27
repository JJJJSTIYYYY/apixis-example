"""UI components: readable conversation, history, streaming, and tool approval."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from rich import box
from rich.console import Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from .stream import MarkdownStream
from .terminal import (
    ACCENT,
    USER_COLOR,
    command_text,
    console,
    plain_text,
    read_key,
    readline_prompt,
)

# ── rendering helpers ────────────────────────────────────────────────


def _parse_result(result: str) -> dict | None:
    try:
        parsed = json.loads(result)
    except json.JSONDecodeError, TypeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _collapse(
    text: str, max_lines: int = 20, max_chars: int = 4000, *, ok: bool = True
) -> tuple[str, bool]:
    """Fold only terminal output; the full content remains available to the AI."""
    text = plain_text(text)
    lines = text.splitlines()
    visible = "\n".join(lines[:max_lines])[:max_chars]
    collapsed = len(lines) > max_lines or len(visible) < len(text)
    if collapsed:
        # Plain Text must not contain Rich markup tags in its folding notice.
        visible += "\n\n… 内容已折叠，输入 /output 查看完整输出。"
    return visible, collapsed


def result_renderable(
    result: str, *, ok: bool, expanded: bool = False
) -> tuple[Text, bool]:
    """Surface command output and keep metadata in a dim footer.

    Shell-shaped results contain command, exit_code and output. Other JSON
    objects remain visible instead of being mistaken for empty shell output.
    The caller can request expanded output for the pager.
    """
    parsed = _parse_result(result)
    if parsed is None:
        output = plain_text(result).strip()
    elif parsed.get("error"):
        error = parsed["error"]
        if error == "Command timed out after 30 seconds":
            error = "命令执行超过 30 秒，已停止。"
        if error == "User rejected this tool call":
            error = "你已拒绝本次执行，助手会继续处理。"
        output = plain_text(error)
    elif "output" in parsed:
        output = plain_text(parsed["output"]).strip()
    else:
        output = json.dumps(parsed, indent=2, ensure_ascii=False)
    collapsed = False
    if not expanded:
        output, collapsed = _collapse(output)
    body = Text(output or "（没有输出）")
    if not ok or (parsed and (parsed.get("error") or parsed.get("exit_code", 0) != 0)):
        body.stylize("red")
    if parsed:
        footer = []
        if parsed.get("command"):
            footer.append("$ " + command_text(parsed["command"]))
        if "exit_code" in parsed:
            footer.append(f"退出码 {parsed['exit_code']}")
        if footer:
            body.append("\n\n" + " · ".join(footer), style="dim")
    return body, collapsed


def arguments_renderable(arguments: str) -> Syntax:
    """Highlight shell commands directly rather than escaped JSON strings."""
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError, TypeError:
        return Syntax(
            plain_text(arguments), "text", background_color="default", word_wrap=True
        )
    if isinstance(parsed, dict) and isinstance(parsed.get("command"), str):
        return Syntax(
            command_text(parsed["command"]),
            "bash",
            background_color="default",
            word_wrap=True,
        )
    return Syntax(
        plain_text(json.dumps(parsed, indent=2, ensure_ascii=False)),
        "json",
        background_color="default",
        word_wrap=True,
    )


# ── AgentUI ──────────────────────────────────────────────────────────


class AgentUI:
    """Keep ordinary conversation controls separate from optional diagnostics."""

    def __init__(self, label: str = "助手", username: str | None = None) -> None:
        self.stream = MarkdownStream(label)
        self.username = username or "你"
        self.conversation_title: str | None = None
        self._status: Any = None
        self._last_output: tuple[str, bool] | None = None

    # ── session chrome ───────────────────────────────────────────────

    def banner(self, model: str) -> None:
        body = Text(f"欢迎，{plain_text(self.username)}\n", style="bold")
        body.append(f"模型：{plain_text(model)}\n", style="dim")
        body.append("直接输入问题开始对话。\n")
        body.append("/help 帮助  ·  /history 历史  ·  /new 新对话\n", style="dim")
        body.append("/provider 切换供应商  ·  /model 切换模型\n", style="dim")
        body.append("/logout 切换用户  ·  quit 退出\n", style="dim")
        body.append("Ctrl-C 退出", style="dim")
        console.print(
            Panel(
                body,
                title="[bold]Apixis Example · 智能助手[/]",
                border_style="cyan",
                padding=(0, 1),
            )
        )

    def help(self) -> None:
        """Present everyday commands first, with diagnostics in their own group."""
        groups = (
            (
                "对话与历史",
                (
                    ("/help", "显示帮助"),
                    ("/provider [供应商] [模型ID]", "查看或切换供应商，保留对话和重试"),
                    ("/model [模型ID]", "查看或切换模型，保留对话和重试"),
                    ("/history", "选择历史对话继续聊天"),
                    ("/new", "开始新对话"),
                    ("/rename", "重命名当前对话"),
                    ("/delete", "删除当前对话（需要确认）"),
                    ("/transcript", "查看当前对话的完整记录"),
                    ("/output", "查看最近一次工具的完整输出"),
                    ("/usage", "查看最近 20 次完成请求的 token 用量"),
                    ("/retry", "失败后继续本轮，保留已提交的工具结果"),
                    ("/logout", "退出当前用户，重新输入用户名"),
                    ("/list-user", "在用户名输入界面列出已记录的用户"),
                    ("quit / exit / q", "退出程序"),
                ),
            ),
            (
                "运行诊断（可选）",
                (
                    ("/checkpoints", "查看快照与恢复位置"),
                    ("/stats", "查看会话事件计数"),
                ),
            ),
        )
        for title, rows in groups:
            table = Table(
                title=title, box=box.SIMPLE_HEAD, show_edge=False, padding=(0, 1)
            )
            table.add_column("命令", style="cyan")
            table.add_column("说明", ratio=1)
            for command, description in rows:
                table.add_row(command, description)
            console.print(table)
        console.print(
            Text(
                "工具审批：回车或 y 批准，Esc 或 n 拒绝。查看长记录时按 q 退出分页。",
                style="dim",
            )
        )

    def set_conversation(self, title: str | None) -> None:
        self.conversation_title = title

    def clear_output(self) -> None:
        self._last_output = None

    def notice(self, message: str, *, style: str = "dim") -> None:
        console.print(Text(plain_text(message), style=style))

    def farewell(self, message: str = "再见！") -> None:
        self.notice("\n" + message)

    def error(self, message: str, *, hint: str | None = None) -> None:
        self.end_turn()
        body = Text(plain_text(message), style="red")
        if hint:
            body.append("\n" + plain_text(hint), style="dim")
        console.print(
            Panel(body, title="[red]提示[/]", border_style="red", padding=(0, 1))
        )

    # ── turn lifecycle ───────────────────────────────────────────────

    def ask(self) -> str:
        """Place the display name beside the input, aligned with the transcript."""
        from rich.markup import escape

        console.print()
        title = (
            plain_text(self.conversation_title) if self.conversation_title else "新对话"
        )
        console.print(
            Text("对话：" + title, style="dim"), overflow="ellipsis", no_wrap=True
        )
        return input(
            readline_prompt(
                f"[{USER_COLOR}]{escape(plain_text(self.username))}[/] [dim]›[/] "
            )
        )

    def ask_username(self, *, on_list_users: Callable[[], None]) -> str:
        """Ask for a display name without leading with an internal storage path."""
        console.print(
            Panel(
                Text(
                    "你希望助手如何称呼你？\n显示名称会记住，下次无需重复填写。\n"
                    "输入 /list-user 查看已记录的用户。"
                ),
                title="欢迎使用 Apixis Example",
                border_style="cyan",
                padding=(0, 1),
            )
        )
        while True:
            name = input(readline_prompt("[bold green]你的名字[/] [dim]›[/] ")).strip()
            if name == "/list-user":
                on_list_users()
                continue
            if name.startswith("/"):
                self.error("未知命令；输入 /list-user 查看已记录的用户，或输入用户名。")
                continue
            if name:
                return name
            self.notice("请输入一个显示名称。", style="yellow")

    def list_users(self, names: list[str]) -> None:
        """Display recorded names literally, then let the user type a name."""
        if not names:
            self.notice("还没有已记录的用户，请输入一个用户名。")
            return
        table = Table(
            title="已记录的用户", box=box.SIMPLE_HEAD, show_edge=False, min_width=16
        )
        table.add_column("用户名", style="green")
        for name in names:
            table.add_row(Text(plain_text(name)))
        console.print(table)
        self.notice("输入上面的用户名继续，或输入新用户名。")

    def thinking(self, message: str = "正在思考…") -> None:
        """Show a spinner until text, an approval, or an error replaces it."""
        self._stop_thinking()
        if console.is_terminal:
            self._status = console.status(Text(message, style="dim"), spinner="dots")
            self._status.start()

    def _stop_thinking(self) -> None:
        if self._status is not None:
            self._status.stop()
            self._status = None

    def content(self, text: str) -> None:
        self._stop_thinking()
        self.stream.write(text)

    def end_turn(self) -> None:
        self._stop_thinking()
        self.stream.stop()

    # ── history display ──────────────────────────────────────────────

    def history_picker(self, conversations: list[dict]) -> int | None:
        """Retry invalid selections and show useful conversation metadata."""
        if not conversations:
            self.notice("还没有历史对话。完成一次聊天后会自动保存。")
            return None
        table = Table(title="历史对话", box=box.SIMPLE_HEAD, show_edge=False)
        table.add_column("序号", style="cyan", no_wrap=True)
        table.add_column("标题", ratio=1)
        table.add_column("更新", style="dim")
        if console.width >= 60:
            table.add_column("消息", justify="right", style="dim")
        for number, conversation in enumerate(conversations, 1):
            cells = [
                str(number),
                Text(plain_text(conversation["title"])),
                str(conversation["time"]),
            ]
            if console.width >= 60:
                cells.append(str(conversation["message_count"]))
            table.add_row(*cells)
        console.print(table)
        self.notice("输入序号打开；回车或 0 返回。")
        while True:
            try:
                choice = input(readline_prompt("[dim]选择[/] › ")).strip()
                if not choice or choice == "0":
                    return None
                number = int(choice)
                if 1 <= number <= len(conversations):
                    return conversations[number - 1]["id"]
            except ValueError:
                pass
            except KeyboardInterrupt, EOFError:
                self.notice("已返回当前对话。")
                return None
            self.notice(
                f"请输入 1–{len(conversations)} 之间的序号，或回车返回。",
                style="yellow",
            )

    def _print_messages(self, messages: list[dict], *, expanded: bool = False) -> None:
        """Render untrusted labels literally and retain meaningful tool results."""
        for message in messages:
            role, content = message.get("role"), message.get("content") or ""
            if role == "user":
                console.print(Text("\n" + plain_text(self.username), style=USER_COLOR))
                console.print(Text(plain_text(content)))
            elif role == "assistant":
                if content:
                    console.print(Text("\n助手", style=ACCENT))
                    text = plain_text(content)
                    if not expanded:
                        lines = text.splitlines()
                        preview = "\n".join(lines[:12])[:600]
                        console.print(Markdown(preview))
                        if len(lines) > 12 or len(preview) < len(text):
                            self.notice("… 回复已折叠；/transcript 查看完整记录。")
                    else:
                        console.print(Markdown(text))
                elif message.get("tool_calls"):
                    self.notice("\n助手 · 请求使用工具")
            elif role == "tool":
                body, collapsed = result_renderable(content, ok=True, expanded=expanded)
                if collapsed:
                    body = Text(
                        body.plain.replace(
                            "输入 /output 查看完整输出", "输入 /transcript 查看完整记录"
                        )
                    )
                console.print(
                    Panel(body, title="工具记录", border_style="dim", padding=(0, 1))
                )

    def load_history_display(
        self, title: str, message_count: int, messages: list[dict]
    ) -> None:
        """Preview recent turns while preserving the full history for the model."""
        self.notice(f"\n已打开：{title} · {message_count} 条消息", style="cyan")
        starts = [
            i for i, message in enumerate(messages) if message.get("role") == "user"
        ]
        start = starts[-3] if len(starts) > 3 else 0
        for message in reversed(messages):
            if message.get("role") == "tool":
                self._last_output = (message.get("content") or "", True)
                break
        self._print_messages(messages[start:])
        if start:
            self.notice("已显示最近 3 轮；输入 /transcript 查看完整对话。")
        self.notice("可以直接输入问题继续聊天。")

    def _page(self, render) -> None:
        self.end_turn()
        if console.is_terminal:
            with console.pager(styles=True):
                render()
        else:
            render()

    def transcript(self, messages: list[dict]) -> None:
        if not any(message.get("role") == "user" for message in messages):
            self.notice("当前对话还没有消息。")
            return
        self._page(lambda: self._print_messages(messages, expanded=True))

    # ── tool calls ───────────────────────────────────────────────────

    def tool_call(self, name: str, arguments: str) -> None:
        # The approval displays the actual call once, even for multi-tool replies.
        self.end_turn()

    def tool_start(self, name: str) -> None:
        self.thinking(
            "正在执行终端命令…"
            if name == "run_command"
            else f"正在执行 {plain_text(name)}…"
        )

    def tool_end(self, name: str) -> None:
        self._stop_thinking()

    def tool_result(
        self, result: str, *, ok: bool = True, name: str | None = None
    ) -> None:
        """Use execution outcome, not approval alone, to determine result styling."""
        self.end_turn()
        self._last_output = (result, ok)
        parsed = _parse_result(result)
        failed = bool(
            parsed and (parsed.get("error") or parsed.get("exit_code", 0) != 0)
        )
        title, color = (
            ("工具已跳过", "yellow")
            if not ok
            else (("执行未成功", "red") if failed else ("执行结果", "green"))
        )
        body, collapsed = result_renderable(result, ok=ok)
        if collapsed:
            title += " · 已折叠"
        console.print(
            Panel(
                body, title=Text(title, style=color), border_style=color, padding=(0, 1)
            )
        )
        self.thinking("正在整理结果…")

    def output(self) -> None:
        if self._last_output is None:
            self.notice("当前对话还没有工具输出。")
            return
        result, ok = self._last_output
        body, _ = result_renderable(result, ok=ok, expanded=True)
        self._page(lambda: console.print(body))

    async def confirm_command(
        self, arguments: str, *, name: str = "run_command"
    ) -> bool:
        """Approve with Enter/y or decline with Esc/n, with command and cwd visible."""
        self.end_turn()
        label = (
            "执行终端命令" if name == "run_command" else "使用工具：" + plain_text(name)
        )
        body = Group(
            arguments_renderable(arguments),
            Text("工作目录：" + plain_text(Path.cwd()), style="dim"),
        )
        console.print(
            Panel(
                body,
                title=Text(label, style="yellow"),
                border_style="yellow",
                padding=(0, 1),
            )
        )
        prompt = (
            "允许执行？回车 / y 批准 · Esc / n 拒绝："
            if console.is_terminal
            else "允许执行？输入 y 批准、n 拒绝（回车批准）："
        )
        console.print(Text(prompt, style="yellow"), end="")
        console.file.flush()
        while True:
            key = await read_key()
            if key in ("enter", "y"):
                self.notice("已批准。", style="green")
                return True
            if key in ("esc", "n", "ctrl-c"):
                self.notice("已拒绝，助手会继续处理。", style="yellow")
                return False

    def prompt_title(self, current_title: str) -> str | None:
        self.notice("当前标题：" + current_title)
        try:
            title = input(readline_prompt("[dim]新标题（回车取消）[/] › ")).strip()
        except EOFError, KeyboardInterrupt:
            return None
        return title or None

    def confirm_delete(self, title: str) -> bool:
        self.notice("即将删除：" + title, style="yellow")
        self.notice("删除后无法恢复这段对话。")
        try:
            return (
                input(readline_prompt("[dim]输入 y 确认删除，回车取消[/] › "))
                .strip()
                .lower()
                == "y"
            )
        except EOFError, KeyboardInterrupt:
            return False
