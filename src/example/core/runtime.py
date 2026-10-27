"""Own graphs, plugins, execution attempts, and recovery within one session."""

from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing
from typing import Any, TypedDict

from apixis import (
    Block,
    GraphContext,
    get_event_loop,
    get_event_pipe,
    get_stream_writer,
)
from my_llm import Usage

from ..plugins import (
    check_tool_call_command_guard,
    command_guard,
    input_filter,
    register_input_filter_plugin,
    unregister_input_filter_plugin,
)
from . import agent, llm
from .events import AgentEvents
from .workspace import build_workspace_graph


class UsageRecord(TypedDict):
    provider: str
    model: str | None
    usage: Usage


class AgentRuntime:
    """Own only session resources; the CLI owns the process-global event core."""

    def __init__(
        self,
        approve: Callable[[dict], Awaitable[bool]] | None = None,
        *,
        max_steps: int = 32,
        max_snapshots: int = 16,
        provider: str = "deepseek",
        model: str | None = None,
    ) -> None:
        if (
            isinstance(max_steps, bool)
            or not isinstance(max_steps, int)
            or max_steps <= 0
        ):
            raise ValueError("max_steps must be a positive integer")
        if (
            isinstance(max_snapshots, bool)
            or not isinstance(max_snapshots, int)
            or max_snapshots <= 0
        ):
            raise ValueError("max_snapshots must be a positive integer")
        self.provider = provider
        self.model: str | None = None
        self.select_model(provider, model)
        self.workspace_graph = build_workspace_graph()
        self.graph = agent.build_agent(
            self.workspace_graph, self._execute_tool, generate=self._generate
        ).compile_graph("prepare", max_steps=max_steps, max_snapshots=max_snapshots)
        self.usage: deque[UsageRecord] = deque(maxlen=20)
        self.events = AgentEvents(self.graph.namespace)
        self.last_context: GraphContext | None = None
        self._filter = None
        try:
            self.events.register()
            self._filter = register_input_filter_plugin(
                self.graph.namespace, input_filter
            )

            async def approve_tool(block: Block) -> None:
                call = (block.with_data or {}).get("tool_call", {})
                arguments = call.get("function", {}).get("arguments", "{}")
                guard = check_tool_call_command_guard(arguments, command_guard)
                if guard is not None and not guard.ok:
                    block.with_data["rejection_reason"] = guard.message
                    block.resolve(False)
                elif approve is None:
                    # Hand the existing interruption to the stream consumer;
                    # Apixis cancels it automatically when this run ends.
                    block.accept()
                    block.with_data["stream_writer"](
                        {"type": "approval", "tool_call": call, "block": block}
                    )
                else:
                    block.resolve(bool(await approve(call)))

            self.graph.add_interrupted_hook(approve_tool)
        except BaseException:
            self.close()
            raise

    def select_model(self, provider: str, model: str | None = None) -> None:
        """Change the next LLM call, including retries, without changing graph state."""
        preset = llm.get_provider(provider)
        if model is not None or preset.list_models():
            model = llm.get_model(provider, model).model.default_value
        self.provider = provider
        self.model = model

    async def _generate(self, messages, tools, on_text) -> dict:
        provider, model = self.provider, self.model
        writer = get_stream_writer()

        def record_usage(usage: Usage) -> None:
            record = UsageRecord(provider=provider, model=model, usage=usage)
            self.usage.append(record)
            writer({"type": "usage", **record})

        return await agent.stream_reply(
            messages,
            tools,
            on_text,
            provider=provider,
            model=model,
            on_usage=record_usage,
        )

    async def _execute_tool(self, call: dict) -> str:
        return await self.events.execute(call)

    def close(self) -> None:
        """Decompose owned graphs and remove session subscriptions."""
        self.graph.decompose()
        self.workspace_graph.decompose()
        if self._filter is not None:
            unregister_input_filter_plugin(self._filter)
            self._filter = None
        self.events.unregister()

    def clear_attempt(self) -> None:
        """Do not offer a retry after the user switches conversations."""
        self.last_context = None

    def retry_context(self) -> GraphContext:
        """Restore the latest failed checkpoint into a new pending attempt."""
        context = self.last_context
        if context is None or context.status != "failed":
            raise RuntimeError("没有可重试的失败运行")
        snapshot = context.get_snapshot()
        if snapshot is None:
            raise RuntimeError("本次失败没有可恢复的快照")
        return self.graph.restore_context(snapshot)

    async def abort(self) -> None:
        """Abort the current run at its saved state; old work may still finish."""
        if self.last_context is not None:
            await self.graph.abort(self.last_context)

    async def stream(
        self,
        messages: list[dict] | None = None,
        *,
        context: GraphContext | None = None,
    ) -> AsyncIterator[Any]:
        """Stream a new turn or a recovered attempt, recording lifecycle events."""
        if context is None:
            if messages is None:
                raise TypeError("messages are required for a new turn")
            context = self.graph.create_context(
                {"messages": messages, "tool_calls": [], "workspace_notes": []}
            )
        elif messages is not None:
            raise TypeError("Pass messages or context, not both")
        self.last_context = context
        await self.events.emit("run.started", {"run_id": None})
        try:
            # Closing the wrapper also closes Apixis's stream and aborts its attempt.
            async with aclosing(self.graph.stream(graph_context=context)) as stream:
                async for chunk in stream:
                    yield chunk
        finally:
            await self.events.emit(f"run.{context.status}", {"run_id": context.run_id})


async def shutdown_core() -> None:
    """The standalone CLI stops its core after all session resources are closed."""
    await get_event_loop().stop()
    await get_event_pipe().stop()
