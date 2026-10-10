"""Terminal I/O abstraction.

Low-level terminal operations: readline integration, key reading, ANSI handling.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import unicodedata

from rich.console import Console
from rich.text import Text

# Python does not import readline on its own, and without it input() falls back
# to the terminal's canonical mode: no arrow keys, no history, no editing.
try:
    import readline  # noqa: F401
except ImportError:  # pragma: no cover - Windows without pyreadline
    readline = None  # type: ignore[assignment]


console = Console()
err_console = Console(stderr=True)

# Rich markup palette
ACCENT = "bold cyan"
USER_COLOR = "bold green"

# How long to wait for more bytes before deciding a lone ESC was the Esc key
# rather than the start of an arrow-key sequence.
_ESCAPE_TIMEOUT = 0.03


# The ANSI escapes Rich wraps styled text in. readline has to be told these
# occupy no columns, but the visible glyphs between them must stay outside the
# markers — see :func:`readline_prompt`.
_ANSI = re.compile(
    r"\x1b(?:\[[0-9;?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])"
)


def plain_text(value: object) -> str:
    """Preserve visible text while discarding terminal control sequences."""
    text = Text.from_ansi(str(value)).plain
    return text.translate({code: None for code in range(32) if code not in (9, 10)})


def command_text(value: object) -> str:
    """Show hidden control characters literally in commands awaiting approval."""
    result = []
    for char in str(value):
        if char not in ("\n", "\t") and unicodedata.category(char) in ("Cc", "Cf"):
            code = ord(char)
            result.append(f"\\x{code:02x}" if code < 256 else f"\\u{code:04x}")
        else:
            result.append(char)
    return "".join(result)


def readline_prompt(markup: str) -> str:
    """Render a Rich markup prompt into a string readline can measure.

    readline works out where the cursor sits from the prompt's display width,
    so the escapes Rich emits around styled text must be wrapped in
    ``\\001``/``\\002`` (readline's "non-printing" markers). Only the escapes
    may be wrapped: hiding the visible glyphs as well makes readline believe
    the prompt is zero columns wide, and it then erases the prompt itself on
    the first backspace. Without readline those guards would be printed as
    literal control characters, so the plain text is used instead.
    """
    text = Text.from_markup(markup)
    if readline is None or not sys.stdin.isatty():
        return text.plain

    with console.capture() as capture:
        console.print(text, end="", soft_wrap=True)
    return _ANSI.sub(lambda match: f"\001{match.group(0)}\002", capture.get())


def _normalise_key(char: str) -> str:
    if char in ("\r", "\n"):
        return "enter"
    if char == "\x03":
        return "ctrl-c"
    if char in ("\x7f", "\b"):
        return "backspace"
    return char.lower()


async def read_key() -> str:
    """Await a single keypress, returning a normalised key name.

    Returns ``"enter"``, ``"esc"``, ``"ctrl-c"``, ``"backspace"``, ``"other"``,
    or the lowercase character that was typed. Reading happens through the
    event loop's reader callback rather than a thread, so Ctrl-C still reaches
    the main thread and the terminal is always restored.

    Anything typed before the call is discarded — see the ``tcflush`` below.
    """
    if not sys.stdin.isatty():
        return _read_key_line()

    try:
        import termios
        import tty
    except ImportError:  # pragma: no cover - Windows
        return _read_key_windows()

    fd = sys.stdin.fileno()
    loop = asyncio.get_running_loop()
    previous = termios.tcgetattr(fd)
    future: asyncio.Future[str] = loop.create_future()

    escaping = False
    seen: list[str] = []
    timer: asyncio.TimerHandle | None = None

    def finish(key: str) -> None:
        if not future.done():
            future.set_result(key)

    def arm() -> None:
        nonlocal timer
        if timer is not None:
            timer.cancel()
        timer = loop.call_later(_ESCAPE_TIMEOUT, sequence_done)

    def sequence_done() -> None:
        nonlocal escaping
        if not escaping:
            return
        # Silence after a lone ESC means the Esc key; a longer run of bytes is
        # an arrow or function key, reported as "other" for the caller to
        # ignore. Either way the whole sequence has been consumed, so nothing
        # leaks into the next read.
        escaping = False
        finish("esc" if not seen else "other")

    def on_readable() -> None:
        nonlocal escaping
        # Read the raw fd, not sys.stdin: the buffered wrapper would swallow
        # the rest of an escape sequence into Python's buffer where the
        # selector can no longer see it, so we would never read the follow-up
        # bytes and every arrow key would look like a lone Esc.
        data = os.read(fd, 1)
        if not data:  # EOF: stop waiting rather than spinning on a ready fd
            finish("ctrl-c")
            return
        char = data.decode("utf-8", errors="replace")
        if escaping:
            seen.append(char)
            arm()
            return
        if char == "\x1b":
            escaping = True
            arm()
            return
        finish(_normalise_key(char))

    tty.setcbreak(fd)
    # Drop anything typed before this point. A queued keystroke is not a
    # decision about the prompt the user has only just been shown — and worse,
    # a stray Enter left over from earlier could approve a command on its own.
    # The terminal also swallows partial lines while it is in canonical mode
    # between prompts, so clearing the queue is the only way to be sure the key
    # that answers this prompt was pressed for it.
    termios.tcflush(fd, termios.TCIFLUSH)
    loop.add_reader(fd, on_readable)
    try:
        return await future
    finally:
        loop.remove_reader(fd)
        if timer is not None:
            timer.cancel()
        termios.tcsetattr(fd, termios.TCSADRAIN, previous)


def _read_key_line() -> str:
    """Line-based fallback for piped stdin."""
    try:
        line = input().strip().lower()
    except EOFError:
        return "ctrl-c"
    if line in ("esc", "no", "n"):
        return "esc"
    if line in ("yes", "y"):
        return "y"
    return _normalise_key(line[0]) if line else "enter"


def _read_key_windows() -> str:  # pragma: no cover - not exercised on macOS
    import msvcrt

    char = msvcrt.getwch()
    if char in ("\x00", "\xe0"):  # arrow / function keys send a two-byte code
        msvcrt.getwch()
        return "other"
    if char == "\x1b":
        return "esc"
    return _normalise_key(char)
