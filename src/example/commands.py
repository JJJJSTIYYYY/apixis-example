"""Conversation commands, delegating prompts and presentation to the UI."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from my_llm import ConfigurationError

from .core import history, llm, settings
from .ui import AgentUI

if TYPE_CHECKING:
    from .core.runtime import AgentRuntime


def handle_model_selection(ui: AgentUI, runtime: AgentRuntime, command: str) -> None:
    """Switch only runtime dependencies; retain messages, tool output and snapshots."""
    parts = command.split()
    try:
        if parts[0] == "/provider":
            if len(parts) == 1:
                ui.notice("当前：" + llm.model_label(runtime.provider, runtime.model))
                ui.notice("可用供应商：" + ", ".join(llm.PROVIDERS))
                ui.notice("用法：/provider <供应商> [模型ID]")
                return
            if len(parts) > 3:
                raise ConfigurationError("用法：/provider <供应商> [模型ID]")
            provider = parts[1].lower()
            model = (
                parts[2]
                if len(parts) == 3
                else (runtime.model if provider == runtime.provider else None)
            )
        else:
            if len(parts) == 1:
                ui.notice("当前：" + llm.model_label(runtime.provider, runtime.model))
                models = llm.get_provider(runtime.provider).list_models()
                ui.notice(
                    "预设模型：" + ", ".join(model.model for model in models)
                    if models
                    else "当前供应商没有预设模型。"
                )
                ui.notice("用法：/model <模型ID>；也可输入预设列表之外的模型 ID。")
                return
            if len(parts) != 2:
                raise ConfigurationError("用法：/model <模型ID>")
            provider, model = runtime.provider, parts[1]
        runtime.select_model(provider, model)
    except ConfigurationError as exc:
        ui.error(str(exc))
        return
    ui.notice(
        "已切换为：" + llm.model_label(runtime.provider, runtime.model), style="green"
    )


def handle_list_users(ui: AgentUI) -> None:
    try:
        names = sorted(set(settings.list_usernames()) | set(history.list_usernames()))
    except OSError, sqlite3.Error:
        ui.error("暂时无法读取用户列表。", hint="请检查保存目录权限和磁盘空间。")
        return
    ui.list_users(names)


def handle_login(ui: AgentUI) -> str:
    """Prompt for a name, allowing user listing without leaving the prompt."""
    username = ui.ask_username(on_list_users=lambda: handle_list_users(ui))
    try:
        settings.set_username(username)
    except OSError as exc:
        ui.error(f"无法写入 {settings.SETTINGS_FILE}: {exc}")
        ui.notice("本次会话仍会使用该用户名")
    return username


def handle_history(ui: AgentUI, username: str) -> tuple[int | None, list[dict] | None]:
    """Return a selected conversation and its complete model context."""
    try:
        conversations = history.list_conversations(username)
        # Format for UI display without exposing the history model to components.
        conv_data = [
            {
                "id": c.id,
                "title": c.title,
                "time": history.format_relative_time(c.updated_at),
                "message_count": c.message_count,
            }
            for c in conversations
        ]
        picked_id = ui.history_picker(conv_data)
        if picked_id is None:
            return None, None
        conv = history.get_conversation(picked_id)
        if conv is None:
            ui.error("这段对话已不存在。", hint="请重新打开 /history 选择。")
            return None, None
        loaded = history.load_messages(picked_id)
    except OSError, sqlite3.Error:
        ui.error(
            "暂时无法读取历史记录。",
            hint="请检查保存目录权限和磁盘空间；当前对话仍可继续。",
        )
        return None, None
    ui.clear_output()
    ui.load_history_display(conv.title, conv.message_count, loaded)
    return picked_id, loaded


def handle_rename(ui: AgentUI, conversation_id: int, current_title: str) -> str | None:
    new_title = ui.prompt_title(current_title)
    if not new_title:
        ui.notice("已取消重命名。")
        return None
    try:
        renamed = history.rename_conversation(conversation_id, new_title)
    except OSError, sqlite3.Error:
        ui.error("未能保存新标题。", hint="原来的标题未更改，请检查保存目录。")
        return None
    if not renamed:
        ui.error("这段对话已不存在。")
        return None
    ui.notice("已重命名为：" + new_title, style="green")
    return new_title


def handle_delete(ui: AgentUI, conversation_id: int, current_title: str) -> bool:
    if not ui.confirm_delete(current_title):
        ui.notice("已取消删除。")
        return False
    try:
        deleted = history.delete_conversation(conversation_id)
    except OSError, sqlite3.Error:
        ui.error("未能删除这段对话。", hint="请检查保存目录后再试。")
        return False
    if not deleted:
        ui.error("这段对话已不存在。")
        return False
    ui.notice("对话已删除，已回到新对话。", style="green")
    return True
