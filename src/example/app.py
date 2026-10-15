"""Application orchestration: chat, recovery commands, and history persistence.

The app coordinates core, UI, and plugins without embedding graph execution or
plugin registration logic in the conversation loop.
"""

from __future__ import annotations

import asyncio
from contextlib import aclosing
from pathlib import Path

from rich.table import Table

from . import commands
from .core import history, llm, settings
from .core.runtime import AgentRuntime
from .ui import AgentUI, console
from .ui.errors import explain_error


def build_system_prompt() -> str:
    """Compose the system prompt, including the workspace the agent runs in.

    run_command inherits this process's working directory, so relative paths
    refer to the same workspace described to the model.
    """
    return f"""你是一个智能助手，可以使用工具来帮助用户完成任务。
当前工作目录：{Path.cwd()}
run_command 在这个目录下执行，可以直接使用相对路径。
所有工具均由本应用提供，执行前需要用户批准。
仔细考虑用户需求，构建合适的命令，执行后解释结果。
必要时可以多次使用工具。请用中文回复，使用 Markdown 组织回答。"""


def show_checkpoints(runtime: AgentRuntime) -> None:
    context = runtime.last_context
    if context is None:
        console.print("[dim]暂无运行记录[/]")
        return
    statuses = {
        "pending": "等待执行",
        "running": "正在执行",
        "finished": "已完成",
        "failed": "未完成",
        "aborted": "已停止",
    }
    table = Table(title="最近运行：" + statuses.get(context.status, context.status))
    for name in ("版本", "步数", "恢复目标", "消息数"):
        table.add_column(name)
    for version, snapshot in enumerate(context.get_all_snapshots()):
        table.add_row(
            str(version),
            str(snapshot["steps"]),
            {
                "prepare": "准备对话",
                "workspace": "读取环境",
                "agent": "生成回复",
                "tools": "执行工具",
            }.get(str(snapshot["target_node_name"]), str(snapshot["target_node_name"])),
            str(len(snapshot["state"].get("messages", []))),
        )
    console.print(table)


def show_stats(runtime: AgentRuntime) -> None:
    table = Table(title="本次会话的事件计数")
    table.add_column("事件")
    table.add_column("次数")
    labels = {
        "run.started": "开始运行",
        "run.finished": "已完成",
        "run.failed": "运行失败",
        "run.aborted": "已停止",
        "tool.request": "工具请求",
        "tool.result": "工具回复",
    }
    for name, count in sorted(runtime.events.counts.items()):
        table.add_row(labels.get(name, name), str(count))
    if not runtime.events.counts:
        console.print("[dim]还没有运行记录。[/]")
        return
    console.print(table)


async def render_chunk(ui: AgentUI, chunk) -> None:
    if not isinstance(chunk, dict):
        return
    kind = chunk.get("type")
    if kind == "approval":
        decision = chunk["decision"]
        call = chunk["tool_call"]
        try:
            approved = await ui.confirm_command(
                call["function"]["arguments"], name=call["function"]["name"]
            )
        except BaseException:
            decision.cancel()
            raise
        if not decision.done():
            decision.set_result(approved)
    elif kind == "content":
        ui.content(chunk["text"])
    elif kind == "error":
        ui.error(chunk["message"], hint=chunk.get("hint"))
    elif kind == "tool_call":
        ui.tool_call(chunk["name"], chunk["arguments"])
    elif kind == "tool_start":
        ui.tool_start(chunk["name"])
    elif kind == "tool_end":
        ui.tool_end(chunk["name"])
    elif kind == "tool_result":
        ui.tool_result(
            chunk["content"], ok=chunk.get("ok", True), name=chunk.get("name")
        )


async def chat_loop(*, provider: str = "deepseek", model: str | None = None) -> None:
    """Run the interactive chat loop; only completed turns enter history."""
    llm.get_provider(provider)
    if model is not None:
        llm.get_model(provider, model)
    ui = AgentUI()
    # Reuse the active name until the user explicitly logs out.
    username = settings.get_username()
    if username is None:
        try:
            username = commands.handle_login(ui)
        except EOFError, KeyboardInterrupt:
            ui.farewell()
            return
    ui.username = username

    async def approve(call: dict) -> bool:
        # Route approval through the same stream consumer as all other UI output.
        # Event hooks must not print prompts ahead of queued text or tool results.
        decision = asyncio.get_running_loop().create_future()
        runtime.last_context.stream_writer(
            {"type": "approval", "tool_call": call, "decision": decision}
        )
        return await decision

    runtime = AgentRuntime(approve, provider=provider, model=model)
    ui.banner(llm.model_label(runtime.provider, runtime.model))
    # Start a new conversation by default; /history resumes a saved one.
    current_conversation: history.Conversation | None = None
    messages: list[dict] = [{"role": "system", "content": build_system_prompt()}]
    pending_input: str | None = None

    try:
        while True:
            ui.set_conversation(
                current_conversation.title if current_conversation else None
            )
            try:
                user_input = ui.ask().strip()
            except EOFError, KeyboardInterrupt:
                ui.farewell()
                break
            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit", "q"):
                ui.farewell()
                break
            if user_input == "/help":
                ui.help()
                continue
            if user_input.split()[0] in ("/provider", "/model"):
                commands.handle_model_selection(ui, runtime, user_input)
                continue
            if user_input == "/logout":
                try:
                    settings.clear_username()
                except OSError as exc:
                    ui.error(
                        f"无法保存退出状态：{exc}",
                        hint="本次仍会返回用户名界面；下次启动可能自动使用原用户名。",
                    )
                current_conversation = None
                messages = [{"role": "system", "content": build_system_prompt()}]
                pending_input = None
                runtime.clear_attempt()
                runtime.events.counts.clear()
                ui.clear_output()
                ui.set_conversation(None)
                ui.username = "你"
                ui.notice("已退出当前用户。")
                try:
                    username = commands.handle_login(ui)
                except EOFError, KeyboardInterrupt:
                    ui.farewell()
                    break
                ui.username = username
                ui.banner(llm.model_label(runtime.provider, runtime.model))
                continue
            if user_input == "/output":
                ui.output()
                continue
            if user_input == "/transcript":
                ui.transcript(messages)
                continue
            if user_input == "/checkpoints":
                show_checkpoints(runtime)
                continue
            if user_input == "/stats":
                show_stats(runtime)
                continue
            if user_input == "/history":
                picked_id, loaded = commands.handle_history(ui, username)
                if picked_id is not None:
                    current_conversation = history.get_conversation(picked_id)
                    messages = [
                        {"role": "system", "content": build_system_prompt()}
                    ] + loaded
                    pending_input = None
                    runtime.clear_attempt()
                continue
            if user_input == "/new":
                current_conversation = None
                messages = [{"role": "system", "content": build_system_prompt()}]
                pending_input = None
                runtime.clear_attempt()
                ui.clear_output()
                ui.notice("已开始新对话。")
                continue
            if user_input == "/rename":
                if current_conversation is None:
                    ui.notice("请先完成一轮对话，再使用 /rename 重命名。")
                else:
                    title = commands.handle_rename(
                        ui, current_conversation.id, current_conversation.title
                    )
                    if title:
                        current_conversation.title = title
                continue
            if user_input == "/delete":
                if current_conversation is None:
                    ui.notice("当前还没有已保存的对话，无需删除。")
                elif commands.handle_delete(
                    ui, current_conversation.id, current_conversation.title
                ):
                    current_conversation = None
                    messages = [{"role": "system", "content": build_system_prompt()}]
                    pending_input = None
                    runtime.clear_attempt()
                    ui.clear_output()
                continue
            if user_input.startswith("/") and user_input != "/retry":
                ui.error("未知命令；输入 /help 查看可用命令")
                continue

            # Leave committed history untouched until the graph finishes.
            context = None
            if user_input == "/retry":
                try:
                    context = runtime.retry_context()
                except RuntimeError as exc:
                    ui.error(str(exc))
                    continue
            else:
                pending_input = user_input
            base_count = len(messages)
            turn_messages = messages + [{"role": "user", "content": pending_input}]
            stream_error = None
            try:
                ui.thinking()
                async with aclosing(
                    runtime.stream(None if context else turn_messages, context=context)
                ) as stream:
                    async for chunk in stream:
                        if isinstance(chunk, dict) and chunk.get("type") == "error":
                            stream_error = chunk
                        await render_chunk(ui, chunk)
                ui.end_turn()
            except Exception as exc:
                ui.end_turn()
                attempt = runtime.last_context
                can_retry = (
                    attempt is not None
                    and attempt.status == "failed"
                    and attempt.get_snapshot() is not None
                )
                message, hint = explain_error(exc, can_retry=can_retry)
                ui.error(message, hint=hint)
                continue

            context = runtime.last_context
            if stream_error is not None:
                pending_input = None
                continue
            if context.status != "finished":
                ui.error(
                    "本轮已停止，没有写入历史。",
                    hint="可以重新提问，之前完成的对话仍保留。",
                )
                continue

            # Save the entire new suffix once, including exactly one user message.
            # Tool messages and assistant tool_calls preserve the resume context.
            messages = context.state["messages"]
            try:
                if current_conversation is None:
                    title = history.auto_title(pending_input)
                    current_conversation = history.create_conversation(username, title)

                history.append_messages(current_conversation.id, messages[base_count:])
            except Exception:
                ui.error(
                    "回复已完成，但未能保存历史。",
                    hint="本次仍可继续对话；请检查保存目录的权限和磁盘空间，退出后未保存的消息会丢失。",
                )
            pending_input = None
    finally:
        ui.end_turn()
        runtime.close()


async def run_prompt(
    prompt: str,
    *,
    approve_tools: bool = False,
    max_steps: int = 32,
    provider: str = "deepseek",
    model: str | None = None,
) -> None:
    """Run one prompt without changing username settings or conversation history."""

    async def approve(call: dict) -> bool:
        return approve_tools

    runtime = AgentRuntime(approve, max_steps=max_steps, provider=provider, model=model)
    stream_error = None
    try:
        async with aclosing(
            runtime.stream(
                [
                    {"role": "system", "content": build_system_prompt()},
                    {"role": "user", "content": prompt},
                ]
            )
        ) as stream:
            async for chunk in stream:
                if not isinstance(chunk, dict):
                    continue
                if chunk.get("type") == "content":
                    print(chunk["text"], end="", flush=True)
                elif chunk.get("type") == "error":
                    stream_error = chunk
        print()
        if stream_error is not None:
            raise RuntimeError(stream_error["message"])
        context = runtime.last_context
        if context.status != "finished":
            raise RuntimeError(f"运行状态：{context.status}")
    finally:
        runtime.close()
