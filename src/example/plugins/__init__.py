"""Plugins layer: extensions.

Re-exports the plugin system and built-in plugins.
"""

from .plugins import (
    CommandCheckResult,
    CommandGuard,
    InputCheckResult,
    InputFilter,
    check_tool_call_command_guard,
    command_guard,
    create_input_filter_handler,
    input_filter,
    register_input_filter_plugin,
    unregister_input_filter_plugin,
)

__all__ = [
    "CommandGuard",
    "CommandCheckResult",
    "InputFilter",
    "InputCheckResult",
    "check_tool_call_command_guard",
    "command_guard",
    "create_input_filter_handler",
    "input_filter",
    "register_input_filter_plugin",
    "unregister_input_filter_plugin",
]
