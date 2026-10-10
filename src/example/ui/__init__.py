"""UI layer: presentation.

Re-exports the main UI class and terminal utilities.
"""

from .components import AgentUI, arguments_renderable, result_renderable
from .terminal import (
    ACCENT,
    USER_COLOR,
    console,
    err_console,
    read_key,
    readline_prompt,
)

__all__ = [
    "AgentUI",
    "arguments_renderable",
    "result_renderable",
    "ACCENT",
    "USER_COLOR",
    "console",
    "err_console",
    "read_key",
    "readline_prompt",
]
