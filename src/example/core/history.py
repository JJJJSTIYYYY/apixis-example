"""Conversation history, persisted to SQLite.

One file under ``~/.example/conversations.db``. User isolation is two-layered:
the file lives in the OS user's home, and rows inside are partitioned by the
display name configured in :mod:`example.core.settings`. Two different people on the
same machine, or two display names under one account, never see each other's
conversations.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterator

from . import settings

# ── connection plumbing ───────────────────────────────────────────────


def _db_path():
    return settings.SETTINGS_DIR / "conversations.db"


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    """Yield a connection with foreign keys on, commit on success, close after.

    The schema is created lazily on first use so a fresh install does not
    need a migration step: opening the database is the migration.
    """
    settings.SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_db_path())
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    _init_schema(conn)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _init_schema(conn: sqlite3.Connection) -> None:
    # IF NOT EXISTS so repeated opens are harmless.
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            username    TEXT NOT NULL,
            title       TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_conv_user
            ON conversations(username, updated_at DESC);

        CREATE TABLE IF NOT EXISTS messages (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER NOT NULL
                            REFERENCES conversations(id) ON DELETE CASCADE,
            role            TEXT NOT NULL,
            content         TEXT,
            tool_calls      TEXT,
            tool_call_id    TEXT,
            created_at      TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_msg_conv
            ON messages(conversation_id);
        """
    )


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── data model ────────────────────────────────────────────────────────


@dataclass
class Conversation:
    """A conversation row with its denormalised message count."""

    id: int
    username: str
    title: str
    created_at: str
    updated_at: str
    message_count: int = 0


# ── conversation CRUD ─────────────────────────────────────────────────


def list_conversations(username: str, limit: int = 50) -> list[Conversation]:
    """Return the user's conversations, most-recently-updated first.

    ``message_count`` is joined in via a LEFT JOIN + GROUP BY so the list view
    costs one query regardless of how many conversations exist.
    """
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.username, c.title, c.created_at, c.updated_at,
                   COUNT(m.id) AS message_count
            FROM conversations c
            LEFT JOIN messages m ON m.conversation_id = c.id
            WHERE c.username = ?
            GROUP BY c.id
            ORDER BY c.updated_at DESC
            LIMIT ?
            """,
            (username, limit),
        ).fetchall()
    return [Conversation(**dict(r)) for r in rows]


def create_conversation(username: str, title: str) -> Conversation:
    now = _now()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO conversations (username, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (username, title, now, now),
        )
    return Conversation(
        id=cur.lastrowid,
        username=username,
        title=title,
        created_at=now,
        updated_at=now,
    )


def get_conversation(conversation_id: int) -> Conversation | None:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT c.id, c.username, c.title, c.created_at, c.updated_at,
                   COUNT(m.id) AS message_count
            FROM conversations c
            LEFT JOIN messages m ON m.conversation_id = c.id
            WHERE c.id = ?
            GROUP BY c.id
            """,
            (conversation_id,),
        ).fetchone()
    return Conversation(**dict(row)) if row else None


def delete_conversation(conversation_id: int) -> bool:
    """Delete a conversation and its messages. Returns True if anything was removed."""
    with _connect() as conn:
        cur = conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
    return cur.rowcount > 0


def rename_conversation(conversation_id: int, title: str) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
            (title, _now(), conversation_id),
        )
    return cur.rowcount > 0


def touch_conversation(conversation_id: int) -> None:
    """Bump ``updated_at`` so the conversation sorts first after a rename, etc."""
    with _connect() as conn:
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (_now(), conversation_id),
        )


# ── messages ──────────────────────────────────────────────────────────


def append_messages(conversation_id: int, messages: list[dict]) -> None:
    """Persist a batch of messages and bump the conversation's updated_at.

    ``tool_calls`` is serialised as JSON so the model sees the exact structure
    on resume; content may be None (assistant messages carrying only tool
    calls), so the column is nullable.
    """
    if not messages:
        return
    now = _now()
    with _connect() as conn:
        conn.executemany(
            """
            INSERT INTO messages
                (conversation_id, role, content, tool_calls, tool_call_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    conversation_id,
                    m.get("role"),
                    m.get("content"),
                    json.dumps(m.get("tool_calls"), ensure_ascii=False)
                    if m.get("tool_calls")
                    else None,
                    m.get("tool_call_id"),
                    now,
                )
                for m in messages
            ],
        )
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (now, conversation_id),
        )


def load_messages(conversation_id: int) -> list[dict]:
    """Reconstruct the message list for a conversation, in order."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT role, content, tool_calls, tool_call_id "
            "FROM messages WHERE conversation_id = ? ORDER BY id",
            (conversation_id,),
        ).fetchall()

    out: list[dict[str, Any]] = []
    for r in rows:
        msg: dict[str, Any] = {"role": r["role"]}
        if r["content"] is not None:
            msg["content"] = r["content"]
        if r["tool_calls"] is not None:
            msg["tool_calls"] = json.loads(r["tool_calls"])
        if r["tool_call_id"] is not None:
            msg["tool_call_id"] = r["tool_call_id"]
        out.append(msg)
    return out


# ── formatting ────────────────────────────────────────────────────────


def format_relative_time(iso: str) -> str:
    """Turn an ISO timestamp into something like '3 分钟前' or '昨天'.

    Anything older than a month is shown as a date; within the last month the
    relative form is what the user actually wants when scanning a list.
    """
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    delta = datetime.now() - dt
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return f"{seconds // 60} 分钟前"
    if seconds < 86400:
        return f"{seconds // 3600} 小时前"
    if delta.days < 7:
        return f"{delta.days} 天前"
    if delta.days < 30:
        return f"{delta.days // 7} 周前"
    return dt.strftime("%Y-%m-%d")


def auto_title(first_user_message: str, max_chars: int = 30) -> str:
    """Derive a conversation title from the user's first message.

    A real title is nicer than 'Untitled' in the picker — the model may answer
    first, but the user asked the question, so the question summarises the
    conversation better than any one-line answer. Stripped to keep the picker
    from stretching the terminal when the user pasted a wall of text.
    """
    text = (first_user_message or "").strip()
    text = text.splitlines()[0] if text else ""
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    return text or "（无标题）"
