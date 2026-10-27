"""Render usage and retained checkpoint details without starting the event core."""

import io
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from my_llm import Usage
from rich.console import Console

from example import app


class UsageUITests(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        self.console = Console(file=self.output, width=160, color_system=None)
        replacement = patch.object(app, "console", self.console)
        replacement.start()
        self.addCleanup(replacement.stop)

    def data_row(self, label):
        line = next(
            line for line in self.output.getvalue().splitlines() if label in line
        )
        return [cell.strip() for cell in line.split("│")[1:-1]]

    def test_usage_without_records_explains_that_nothing_was_reported(self):
        ui = Mock()
        app.show_usage(ui, SimpleNamespace(usage=[]))
        ui.notice.assert_called_once_with("还没有已报告用量的完成请求。")
        self.assertEqual(self.output.getvalue(), "")

    def test_usage_distinguishes_unreported_counters_from_reported_zero(self):
        records = [
            {"provider": "deepseek", "model": "unknown-usage", "usage": Usage()},
            {
                "provider": "deepseek",
                "model": "partial-usage",
                "usage": Usage(input=11, output=0, cached=0, reasoning=None),
            },
        ]
        app.show_usage(Mock(), SimpleNamespace(usage=records))
        self.assertEqual(
            self.data_row("unknown-usage"),
            ["deepseek / unknown-usage", "—", "—", "—", "—", "—"],
        )
        self.assertEqual(
            self.data_row("partial-usage"),
            ["deepseek / partial-usage", "11", "0", "—", "0", "—"],
        )
        self.assertIn("—表示未报告", "".join(self.output.getvalue().split()))

    def test_usage_model_label_preserves_literal_markup(self):
        model = "model[bold]literal[/bold]"
        records = [
            {
                "provider": "deepseek",
                "model": model,
                "usage": Usage(input=1, output=2, total=3),
            }
        ]
        app.show_usage(Mock(), SimpleNamespace(usage=records))
        self.assertIn("deepseek / " + model, self.output.getvalue())

    def test_checkpoints_show_current_retained_indices_and_limit(self):
        snapshots = [
            {"steps": 29, "target_node_name": "tools", "state": {"messages": [1, 2]}},
            {
                "steps": 30,
                "target_node_name": "agent",
                "state": {"messages": [1, 2, 3]},
            },
        ]
        context = SimpleNamespace(
            status="failed",
            max_snapshots=4,
            get_all_snapshots=Mock(return_value=snapshots),
        )
        app.show_checkpoints(SimpleNamespace(last_context=context))
        context.get_all_snapshots.assert_called_once_with()
        self.assertIn("最近运行：未完成", self.output.getvalue())
        flattened = "".join(self.output.getvalue().split())
        self.assertIn("保留最近2/4个快照", flattened)
        self.assertIn("版本为当前保留范围内的索引", flattened)
        self.assertEqual(self.data_row("执行工具"), ["0", "29", "执行工具", "2"])
        self.assertEqual(self.data_row("生成回复"), ["1", "30", "生成回复", "3"])
