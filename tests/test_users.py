"""User recording, legacy discovery, and username prompt recovery."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from example import commands
from example.core import history, settings


class UserTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.object(settings, "SETTINGS_DIR", Path(folder)))
        self.enterContext(
            patch.object(settings, "SETTINGS_FILE", Path(folder) / "settings.json")
        )

    def test_logout_preserves_legacy_user_and_unrelated_settings(self):
        settings.save_settings({"username": "旧用户", "theme": "dark"})
        settings.clear_username()
        self.assertIsNone(settings.get_username())
        self.assertEqual(settings.list_usernames(), ["旧用户"])
        self.assertEqual(settings.load_settings()["theme"], "dark")
        settings.set_username("新用户")
        settings.set_username("新用户")
        settings.clear_username()
        self.assertEqual(set(settings.list_usernames()), {"旧用户", "新用户"})

    def test_list_combines_history_and_users_without_conversations(self):
        settings.set_username("未聊天用户")
        history.create_conversation("历史用户", "one")
        history.create_conversation("历史用户", "two")
        ui = Mock()
        commands.handle_list_users(ui)
        self.assertEqual(
            set(ui.list_users.call_args.args[0]), {"历史用户", "未聊天用户"}
        )
        self.assertEqual(len(ui.list_users.call_args.args[0]), 2)

    def test_invalid_user_registry_is_ignored(self):
        settings.save_settings(
            {"username": "active", "usernames": [None, 3, "", "active", " other "]}
        )
        self.assertEqual(settings.list_usernames(), ["active", "other"])
        settings.save_settings({"username": "active", "usernames": "invalid"})
        self.assertEqual(settings.list_usernames(), ["active"])

    def test_user_list_database_error_is_reported(self):
        ui = Mock()
        with patch.object(
            history, "list_usernames", side_effect=sqlite3.OperationalError("locked")
        ):
            commands.handle_list_users(ui)
        ui.error.assert_called_once()
        ui.list_users.assert_not_called()
