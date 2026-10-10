"""Conversation commands, delegating prompts and presentation to the UI."""

from __future__ import annotations

import sqlite3

from .core import history
from .ui import AgentUI


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
