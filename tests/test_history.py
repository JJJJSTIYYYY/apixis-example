"""Preserve complete model messages and migrate existing conversation stores."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from example.core import history, settings


class HistoryTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.directory = Path(folder)
        self.enterContext(patch.object(settings, "SETTINGS_DIR", self.directory))

    def test_full_message_round_trip_preserves_native_metadata(self):
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "你好"}]},
            {
                "role": "assistant",
                "content": None,
                "reasoning_content": "思考",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "run_command",
                            "arguments": '{"command":"pwd"}',
                        },
                    }
                ],
                "response_id": "resp_1",
                "response_status": "completed",
                "response_output": [
                    {
                        "type": "reasoning",
                        "id": "reason_1",
                        "encrypted_content": "opaque",
                    },
                    {"type": "thinking", "thinking": "thought", "signature": "signed"},
                ],
                "finish_reason": "tool_calls",
                "usage": {"input_tokens": 8},
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "done"},
        ]
        conversation = history.create_conversation("tester", "metadata")
        history.append_messages(conversation.id, messages)
        self.assertEqual(history.load_messages(conversation.id), messages)
        self.assertEqual(history.list_conversations("tester")[0].message_count, 3)

    def test_old_database_and_new_messages_can_be_loaded_together(self):
        with closing(sqlite3.connect(self.directory / "conversations.db")) as conn:
            conn.executescript("""
                CREATE TABLE conversations (
                    id INTEGER PRIMARY KEY, username TEXT NOT NULL, title TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY, conversation_id INTEGER NOT NULL,
                    role TEXT NOT NULL, content TEXT, tool_calls TEXT, tool_call_id TEXT,
                    created_at TEXT NOT NULL
                );
                INSERT INTO conversations VALUES (1, 'tester', 'legacy', 'old', 'old');
                INSERT INTO messages VALUES (1, 1, 'user', 'legacy message', NULL, NULL, 'old');
            """)
        legacy = {"role": "user", "content": "legacy message"}
        self.assertEqual(history.load_messages(1), [legacy])
        new = {"role": "assistant", "content": "new", "reasoning_content": "preserved"}
        history.append_messages(1, [new])
        self.assertEqual(history.load_messages(1), [legacy, new])
        # Reopening the migrated schema remains safe.
        self.assertEqual(history.load_messages(1), [legacy, new])
