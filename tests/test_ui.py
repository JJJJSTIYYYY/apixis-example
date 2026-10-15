"""Check readable, safe terminal output without making any LLM requests."""

import io
import unittest
from unittest.mock import Mock, patch

import httpx
from my_llm import IncompleteStreamError, ResponseFailedError, StreamError
from openai import AuthenticationError
from rich.cells import cell_len
from rich.console import Console
from rich.markdown import Markdown

from example.ui import components, stream, terminal
from example.ui.errors import explain_error


class UITests(unittest.TestCase):
    def test_output_limit_can_switch_models_and_retry_without_restarting(self):
        message, hint = explain_error(
            StreamError("回复达到输出长度上限"), can_retry=True
        )
        self.assertIn("输出长度上限", message)
        self.assertIn("切换", hint)
        self.assertIn("/retry", hint)
        self.assertNotIn("重新启动", hint)

    def setUp(self):
        self.output = io.StringIO()
        self.console = Console(file=self.output, width=38, height=12, color_system=None)
        self.patches = [
            patch.object(module, "console", self.console)
            for module in (components, stream, terminal)
        ]
        for replacement in self.patches:
            replacement.start()
        self.addCleanup(
            lambda: [replacement.stop() for replacement in reversed(self.patches)]
        )
        self.ui = components.AgentUI(username="朋友[/]")

    def test_narrow_banner_and_help_preserve_literal_user_text(self):
        self.ui.banner("deepseek-chat")
        self.ui.help()
        text = self.output.getvalue()
        self.assertIn("朋友[/]", text)
        self.assertIn("运行诊断", text)
        self.assertIn("/output", text)
        self.assertIn("/transcript", text)
        self.assertTrue(all(cell_len(line) <= 38 for line in text.splitlines()))

    def test_input_name_and_conversation_label_are_literal(self):
        self.ui.set_conversation("测试[/]标题")
        with patch("builtins.input", return_value="hello") as read:
            self.assertEqual(self.ui.ask(), "hello")
        self.assertIn("朋友[/]", read.call_args.args[0])
        self.assertIn("测试[/]标题", self.output.getvalue())

    def test_username_commands_list_users_and_keep_prompting(self):
        list_users = Mock(side_effect=lambda: self.ui.list_users(["旧用户[/]"]))
        with patch(
            "builtins.input", side_effect=["/list-user", "/unknown", "", " 新用户 "]
        ):
            self.assertEqual(self.ui.ask_username(on_list_users=list_users), "新用户")
        list_users.assert_called_once_with()
        self.assertIn("旧用户[/]", self.output.getvalue())
        self.assertIn("未知命令", self.output.getvalue())

    def test_empty_user_list_explains_how_to_continue(self):
        self.ui.list_users([])
        self.assertIn("还没有已记录的用户", self.output.getvalue())

    def test_folded_output_can_be_expanded_without_markup_leaking(self):
        result = "\n".join(f"line-{i:02d}" for i in range(40))
        self.ui.tool_result(result)
        folded = self.output.getvalue()
        self.assertNotIn("line-39", folded)
        self.assertIn("/output", folded)
        self.assertNotIn("[dim]", folded)
        self.ui.output()
        self.assertIn("line-39", self.output.getvalue())
        self.ui.clear_output()
        self.ui.output()
        self.assertIn("还没有工具输出", self.output.getvalue())

    def test_nonzero_exit_is_failure_even_when_approved(self):
        self.ui.tool_result(
            '{"exit_code": 1, "output": "failed", "command": "false"}', ok=True
        )
        self.assertIn("执行未成功", self.output.getvalue())
        self.assertIn("退出码 1", self.output.getvalue())

    def test_rejection_is_a_skipped_tool_not_a_success(self):
        self.ui.tool_result('{"error": "User rejected this tool call"}', ok=False)
        self.assertIn("工具已跳过", self.output.getvalue())
        self.assertIn("你已拒绝", self.output.getvalue())

    def test_generic_json_results_remain_visible(self):
        body, _ = components.result_renderable('{"status": "ready"}', ok=True)
        self.assertIn('"status": "ready"', body.plain)

    def test_history_invalid_choice_reprompts(self):
        conversations = [
            {"id": 7, "title": "[/]测试", "time": "刚刚", "message_count": 2}
        ]
        with patch("builtins.input", side_effect=["abc", "-1", "1"]) as read:
            self.assertEqual(self.ui.history_picker(conversations), 7)
        self.assertEqual(read.call_count, 3)
        self.assertIn("[/]测试", self.output.getvalue())
        self.assertIn("请输入", self.output.getvalue())

    def test_history_preview_and_full_transcript_have_distinct_scopes(self):
        messages = []
        for i in range(5):
            messages.extend(
                [
                    {"role": "user", "content": f"USER_{i}"},
                    {"role": "assistant", "content": f"ANSWER_{i}"},
                ]
            )
        messages.append({"role": "tool", "content": "latest-tool-output"})
        self.ui.load_history_display("[/]标题", len(messages), messages)
        self.assertNotIn("USER_0", self.output.getvalue())
        self.assertIn("USER_2", self.output.getvalue())
        self.ui.output()
        self.assertIn("latest-tool-output", self.output.getvalue())
        self.ui.transcript(messages)
        self.assertIn("USER_0", self.output.getvalue())

    def test_untrusted_terminal_sequences_do_not_control_output(self):
        visible = terminal.plain_text("before\x1b[2Jafter\x1b]0;title\x07\x00")
        self.assertIn("before", visible)
        self.assertIn("after", visible)
        self.assertNotIn("\x1b", visible)
        self.assertNotIn("\x00", visible)

    def test_long_stream_removes_bounded_live_before_printing_final_markdown(self):
        instances = []

        class Live:
            def __init__(self, renderable, **kwargs):
                self.renderable = renderable
                self.transient = kwargs["transient"]
                self.updates = []
                instances.append(self)

            def start(self):
                pass

            def update(self, renderable):
                self.updates.append(renderable)

            def stop(self):
                pass

        with patch.object(stream, "Live", Live):
            writer = stream.MarkdownStream(enabled=True)
            writer.write("\n".join(f"ROW_{i:03d}  " for i in range(80)))
            writer.stop()
        self.assertTrue(instances[0].transient)
        self.assertTrue(
            all(
                isinstance(update, stream._TailLines) for update in instances[0].updates
            )
        )
        self.assertEqual(self.output.getvalue().count("ROW_079"), 1)

    def test_tiny_terminal_tail_is_clamped_to_available_height(self):
        console = Console(file=io.StringIO(), width=20, height=3, color_system=None)
        rendered = console.render_lines(stream._TailLines(Markdown("a  \nb  \nc  \nd")))
        self.assertLessEqual(len(rendered), 1)

    def test_authentication_error_gives_an_action_without_server_body(self):
        request = httpx.Request("POST", "https://llm.test")
        response = httpx.Response(401, request=request, text="PRIVATE SERVER BODY")
        error = AuthenticationError(
            "PRIVATE SERVER BODY", response=response, body={"error": "PRIVATE"}
        )
        message, hint = explain_error(error, can_retry=True)
        self.assertIn("凭证", message)
        self.assertIn("当前供应商的 API Key", hint)
        self.assertNotIn("PRIVATE", message + hint)
        self.assertNotIn("/retry", hint)

    def test_stream_errors_do_not_display_vendor_details(self):
        for error in (
            IncompleteStreamError("PRIVATE", details={"body": "PRIVATE"}),
            ResponseFailedError("PRIVATE", details={"body": "PRIVATE"}),
        ):
            message, hint = explain_error(error, can_retry=True)
            self.assertNotIn("PRIVATE", message + hint)
            self.assertIn("/retry", hint)

    def test_step_limit_does_not_offer_a_futile_retry(self):
        _, hint = explain_error(
            RuntimeError("Graph exceeded its maximum of 32 steps."), can_retry=True
        )
        self.assertIn("不会增加", hint)

    def test_history_delete_and_rename_cancel_cleanly(self):
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            self.assertFalse(self.ui.confirm_delete("标题[/]"))
            self.assertIsNone(self.ui.prompt_title("标题[/]"))

    def test_approval_command_does_not_hide_control_characters(self):
        import json

        display = components.arguments_renderable(
            json.dumps({"command": "printf before\x1b[2Jafter"})
        )
        self.assertIn(r"\x1b[2J", display.code)
        self.assertNotIn("\x1b", display.code)
