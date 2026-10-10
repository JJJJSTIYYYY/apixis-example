"""Run more parent graphs than permits against a small bounded event queue."""

import asyncio
from unittest.mock import patch

from apixis import get_event_pipe, start_core
from apixis.core.config.core_config import EVENT_LOOP_BACKPRESSURE, EVENT_PIPE_MAX_LEN

from example.core import agent
from example.core.runtime import AgentRuntime, shutdown_core


async def main():
    assert EVENT_LOOP_BACKPRESSURE == 128
    assert EVENT_PIPE_MAX_LEN == 8
    await start_core()

    async def approve(call):
        return True

    async def reply(messages, tools, on_text):
        if messages[-1]["role"] == "tool":
            return {"role": "assistant", "content": messages[-1]["content"]}
        identity = next(m["content"] for m in messages if m["role"] == "user")
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": identity,
                    "type": "function",
                    "function": {
                        "name": "run_command",
                        "arguments": '{"command":"printf safe"}',
                    },
                }
            ],
        }

    async def execute(call):
        return call["id"]

    runtime = AgentRuntime(approve)
    try:
        contexts = [
            runtime.graph.create_context(
                {
                    "messages": [
                        {"role": "system", "content": "Test"},
                        {"role": "user", "content": str(i)},
                    ]
                }
            )
            for i in range(160)
        ]
        with (
            patch.object(agent, "stream_deepseek", reply),
            patch("example.core.events.execute_tool", execute),
        ):
            results = await asyncio.wait_for(
                asyncio.gather(
                    *(
                        runtime.graph.invoke(graph_context=context)
                        for context in contexts
                    )
                ),
                timeout=15,
            )
        assert [r["messages"][-1]["content"] for r in results] == [
            str(i) for i in range(160)
        ]
        assert all(c.status == "finished" for c in contexts)
        await get_event_pipe().join()
        print("160 concurrent turns passed; backpressure=128; queue=8; no API requests")
    finally:
        runtime.close()
        await shutdown_core()


asyncio.run(main())
