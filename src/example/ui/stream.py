"""Streaming Markdown renderer.

Renders LLM output incrementally as it arrives, using Rich Live for smooth
updates. Handles the terminal redraw problem where long responses would
duplicate previous output.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.segment import Segment
from rich.text import Text

from .terminal import ACCENT, console, plain_text


class _TailLines:
    """Show only the last screenful of a renderable.

    Rich's Live erases its previous output by walking the cursor up one row per
    line it drew. A render taller than the terminal sends that walk off the top
    of the screen, where the erase silently does nothing and every repaint
    leaves a fresh copy of the whole answer in the scrollback. Clamping the live
    region to the screen height keeps the redraw sound, and shows the newest
    text rather than the oldest — which is what a streaming answer wants.

    Only the in-progress view is clamped. On stop, the bounded live region is
    removed and the complete answer is printed once into the scrollback.
    """

    def __init__(self, renderable: Any) -> None:
        self.renderable = renderable

    def __rich_console__(self, console: Console, options: Any):
        lines = console.render_lines(self.renderable, options, pad=False)
        limit = max(1, options.size.height - 2)
        if len(lines) > limit:
            lines = lines[-limit:]

        new_line = Segment.line()
        for index, line in enumerate(lines):
            if index:
                yield new_line
            yield from line


class MarkdownStream:
    """Render a Markdown stream incrementally in the terminal.

    Deltas are appended to a buffer and the whole buffer is re-rendered through
    Rich on every write. Re-parsing the full text each time is wasteful in
    theory but keeps the rendered output correct — Markdown constructs such as
    code fences and lists only make sense when parsed from the start — and Rich
    throttles the actual repaint via ``refresh_per_second``.

    Call :meth:`stop` to freeze the current render into the scrollback; the
    object can then be reused for a following stream.
    """

    def __init__(self, label: str = "Agent", *, enabled: bool | None = None) -> None:
        self.label = label
        # Live output needs a real terminal; when stdout is piped we fall back
        # to plain printing so the transcript stays readable.
        self.enabled = console.is_terminal if enabled is None else enabled
        self._buffer: list[str] = []
        self._live: Live | None = None
        self._open = False

    @property
    def text(self) -> str:
        """The text accumulated since the last :meth:`start`."""
        return "".join(self._buffer)

    def start(self) -> None:
        """Begin a new stream, discarding anything from the previous one."""
        if self._open:
            return
        self._buffer = []
        self._open = True

        console.print()
        console.print(Text(f"{self.label}", style=ACCENT))

        if not self.enabled:
            return

        self._live = Live(
            _TailLines(self._renderable()),
            console=console,
            refresh_per_second=12,
            transient=True,
        )
        self._live.start()

    def write(self, text: str) -> None:
        """Append a delta and repaint."""
        if not self._open:
            self.start()

        text = plain_text(text)
        self._buffer.append(text)

        if self._live is not None:
            self._live.update(_TailLines(self._renderable()))
        elif not self.enabled:
            console.print(text, end="", markup=False, highlight=False, soft_wrap=True)

    def stop(self) -> None:
        """Finish the stream, leaving the rendered Markdown on screen."""
        if not self._open:
            return

        if self._live is not None:
            # Erase only the bounded live region, then print the full answer once.
            # Expanding Live before stop would make tall answers duplicate again.
            self._live.stop()
            self._live = None
            console.print(self._renderable())
        else:
            console.print()

        self._open = False
        self._buffer = []

    def _renderable(self) -> Markdown:
        return Markdown(self.text)
