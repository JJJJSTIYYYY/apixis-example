"""Collect bounded workspace context through a reusable, concurrent subgraph."""

from __future__ import annotations

import asyncio
import os
import platform
from pathlib import Path
from typing import Annotated, TypedDict

from apixis import AutoMerge, Command, GraphManager, NodeGraph


class WorkspaceState(TypedDict):
    notes: Annotated[list[str], AutoMerge()]
    summary: str


async def directory_node(state: dict) -> Command:
    """List names only; never read project files or environment secrets."""

    def describe() -> str:
        cwd = Path.cwd()
        # Bound traversal and output, including unusually large folders.
        names = []
        with os.scandir(cwd) as entries:
            for index, entry in enumerate(entries):
                if index == 100:
                    break
                if not entry.name.startswith("."):
                    names.append(entry.name[:120] + ("/" if entry.is_dir() else ""))
                if len(names) == 30:
                    break
        return f"工作目录：{cwd}\n部分可见条目：{', '.join(sorted(names)) or '（空）'}"

    try:
        note = await asyncio.to_thread(describe)
    except OSError as exc:
        note = f"工作目录不可读取：{type(exc).__name__}"
    return Command(update={"notes": [note]}, goto="summary")


async def environment_node(state: dict) -> Command:
    """Describe the execution environment without another LLM request."""
    note = f"系统：{platform.system()} {platform.machine()}；Python：{platform.python_version()}"
    return Command(update={"notes": [note]}, goto="summary")


def summary_node(state: dict) -> Command:
    return Command(update={"summary": "\n".join(state["notes"])})


def build_workspace_graph() -> NodeGraph:
    """Fan out with Command targets and join in deterministic node order."""
    manager = GraphManager(state_schema=WorkspaceState)
    manager.add_node(directory_node, "directory", timeout=5)
    manager.add_node(environment_node, "environment")
    manager.add_node(summary_node, "summary")
    # Both branches route to summary; Apixis joins the batch before dispatching it.
    # This transient metadata is collected again each turn, never restored.
    return manager.compile_graph(
        entry_point=["directory", "environment"], no_snapshot=True
    )
