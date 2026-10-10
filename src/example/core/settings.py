"""Persistent user settings, stored under ``~/.example/settings.json``.

Kept deliberately forgiving: a missing, unreadable, or corrupt file is treated
as "no settings yet" rather than an error, because the only consequence is
re-prompting for a name the user can simply type again.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

SETTINGS_DIR = Path(
    os.environ.get("EXAMPLE_HOME", str(Path.home() / ".example"))
).expanduser()
SETTINGS_FILE = SETTINGS_DIR / "settings.json"


def load_settings() -> dict[str, Any]:
    """Return the stored settings, or an empty dict if there are none."""
    try:
        raw = SETTINGS_FILE.read_text(encoding="utf-8")
    except FileNotFoundError, OSError:
        return {}

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}

    return data if isinstance(data, dict) else {}


def save_settings(settings: dict[str, Any]) -> None:
    """Write settings to disk, creating ``~/.example`` if needed.

    Raises:
        OSError: if the directory or file cannot be written.
    """
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(
        json.dumps(settings, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def get_username() -> str | None:
    """Return the configured display name, or ``None`` if unset or blank."""
    name = load_settings().get("username")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def set_username(name: str) -> None:
    """Persist the display name, preserving any other stored settings."""
    settings = load_settings()
    settings["username"] = name
    save_settings(settings)
