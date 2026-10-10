"""Event-driven tool worker and lightweight lifecycle observations."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any
from uuid import uuid4

from apixis import (
    ApixEventHandler,
    EventType,
    GraphContext,
    get_current_namespace,
    get_current_run_id,
    get_event_pipe,
    get_graph_context,
    subscribe,
    unsubscribe,
    wait_for_event,
)

from .tools import execute_tool


class AgentEvents:
    """Own a request handler and a wildcard observer in one private namespace."""

    def __init__(self, namespace: str) -> None:
        self.prefix = f"{namespace}.example"
        self.counts: Counter[str] = Counter()
        self.last_run_id: str | None = None
        self._handlers: list[str] = []

    def register(self) -> None:
        """Registration is explicit and cleanup removes only our own handlers."""
        worker = ApixEventHandler(
            self._execute_request,
            on_error=self._on_worker_error,
            name=f"{self.prefix}.worker",
        )
        observer = ApixEventHandler(
            self._observe,
            background=True,
            name=f"{self.prefix}.observer",
        )
        for pattern, handler in [
            (f"{self.prefix}.tool.request", worker),
            (f"{self.prefix}.*", observer),
        ]:
            subscribe(pattern, exist_ok=False)(handler)
            self._handlers.append(handler.name)

    def unregister(self) -> None:
        for name in reversed(self._handlers):
            unsubscribe(name)
        self._handlers.clear()

    async def emit(self, name: str, context: dict[str, Any]) -> None:
        await get_event_pipe().post_event(
            event_type=EventType.LIFECYCLE,
            event_name=f"{self.prefix}.{name}",
            context=context,
        )

    async def execute(self, tool_call: dict) -> str:
        """Wait for a correlated completion, lending the graph handler's permit.

        Context entry registers the reply subscription before publication;
        waiter.wait() lends the permit and context exit removes the subscription.
        Every request has a unique reply name, so concurrent runs cannot consume
        another run's result.
        """
        reply = f"{self.prefix}.tool.result.{uuid4().hex}"
        async with wait_for_event(reply, point="processed", time_out=40) as waiter:
            await get_event_pipe().post_event(
                event_type=EventType.WORKFLOW,
                event_name=f"{self.prefix}.tool.request",
                context={
                    "tool_call": tool_call,
                    "reply": reply,
                    "run_id": get_current_run_id(),
                    "namespace": get_current_namespace(),
                    "graph_context": get_graph_context(),
                },
            )
            event = await waiter.wait()
            return event.context["result"]

    async def _execute_request(self, event) -> None:
        request = event.context
        context: GraphContext = request["graph_context"]
        if not context.is_active:
            result = json.dumps({"error": "The originating run was aborted"})
        else:
            result = await execute_tool(request["tool_call"])
        await self._reply(request, result)

    async def _on_worker_error(self, event, error: Exception) -> None:
        """Reply on handler failure so the caller does not hang until timeout."""
        await self._reply(event.context, json.dumps({"error": str(error)}))

    async def _reply(self, request: dict, result: str) -> None:
        await get_event_pipe().post_event(
            event_type=EventType.INFO,
            event_name=request["reply"],
            context={"result": result, "run_id": request["run_id"]},
        )

    async def _observe(self, event) -> None:
        """Keep aggregate counts only; never retain messages or command output."""
        suffix = event.event_name.removeprefix(f"{self.prefix}.")
        if suffix.startswith("tool.result."):
            suffix = "tool.result"
        self.counts[suffix] += 1
        self.last_run_id = event.context.get("run_id")
