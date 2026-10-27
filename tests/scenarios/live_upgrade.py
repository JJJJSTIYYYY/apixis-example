"""Opt-in paid smoke test; read credentials with hidden input, never save them.

Run from the project root: uv run python tests/scenarios/live_upgrade.py
Only the exact harmless printf command below can be approved.
"""

import argparse
import asyncio
import io
import json
import os
import shlex
from contextlib import aclosing, redirect_stderr, redirect_stdout
from dataclasses import replace
from getpass import getpass
from unittest.mock import patch

from apixis import get_event_pipe, start_core
from my_llm import Attribute, Config

from example.core import agent, llm
from example.core.runtime import AgentRuntime, shutdown_core

MARKER = "APIXIS_UPGRADE_OK"


async def exercise(selections: list[tuple[str, str]]) -> list[dict]:
    await start_core()
    reports = []
    try:
        for provider, model in selections:
            runtime = AgentRuntime(provider=provider, model=model, max_snapshots=2)
            attempts = 0
            approvals = 0
            original = agent.stream_reply

            async def reply(*args, **kwargs):
                nonlocal attempts
                attempts += 1
                if attempts == 2:
                    raise RuntimeError("Injected post-tool failure")
                if attempts > 3:
                    raise RuntimeError("Live request budget exhausted")
                return await original(*args, **kwargs)

            async def consume(*, messages=None, context=None):
                nonlocal approvals
                async with aclosing(
                    runtime.stream(messages, context=context)
                ) as stream:
                    async for chunk in stream:
                        if chunk.get("type") == "approval":
                            block = chunk["block"]
                            assert block.accepted and not block.done
                            call = chunk["tool_call"]["function"]
                            arguments = json.loads(call["arguments"])
                            allowed = call["name"] == "run_command" and shlex.split(
                                arguments["command"]
                            ) == ["printf", MARKER]
                            block.resolve(allowed)
                            assert allowed, "Unexpected command was rejected"
                            approvals += 1

            try:
                with patch.object(agent, "stream_reply", reply):
                    try:
                        await consume(
                            messages=[
                                {
                                    "role": "system",
                                    "content": "Use the requested tool exactly once, then answer briefly.",
                                },
                                {
                                    "role": "user",
                                    "content": f"Call run_command with command printf {MARKER}. After its result, reply with that marker only.",
                                },
                            ]
                        )
                    except RuntimeError as exc:
                        assert str(exc) == "Injected post-tool failure"
                    assert runtime.last_context.status == "failed"
                    assert approvals == 1
                    await consume(context=runtime.retry_context())
                context = runtime.last_context
                assert context.status == "finished"
                assert approvals == 1, "Retry must not repeat the committed tool"
                assert len(context.get_all_snapshots()) == 2
                assert len(runtime.usage) == 2
                assert MARKER in context.state["messages"][-1]["content"]
                results = [m for m in context.state["messages"] if m["role"] == "tool"]
                assert len(results) == 1
                result = json.loads(results[0]["content"])
                assert result["output"] == MARKER and result["exit_code"] == 0
                assert all("usage" not in m for m in context.state["messages"])
                reports.append(
                    {
                        "provider": provider,
                        "passed": True,
                        "model": model,
                        "completed_requests": len(runtime.usage),
                        "tool_executions": approvals,
                        "retained_snapshots": len(context.get_all_snapshots()),
                        "recovered_without_tool_replay": True,
                        "usage": [r["usage"].to_record() for r in runtime.usage],
                    }
                )
            except Exception as exc:
                reports.append(
                    {
                        "provider": provider,
                        "passed": False,
                        "error_type": type(exc).__name__,
                        "http_status": getattr(exc, "status_code", None),
                    }
                )
            finally:
                runtime.close()
    finally:
        await get_event_pipe().join()
        await shutdown_core()
    return reports


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--provider", choices=("deepseek", "kimi", "both"), default="both"
    )
    parser.add_argument("--kimi-region", choices=("global", "cn"), default="global")
    args = parser.parse_args()
    selections = [
        (provider, model)
        for provider, model in (("deepseek", "deepseek-flash"), ("kimi", "kimi-k2.6"))
        if args.provider in ("both", provider)
    ]
    if args.kimi_region == "cn":
        selections = [
            ("kimi-cn" if provider == "kimi" else provider, model)
            for provider, model in selections
        ]
    names = [
        "DEEPSEEK_API_KEY" if provider == "deepseek" else "MOONSHOT_API_KEY"
        for provider, _ in selections
    ]
    previous = {name: os.environ.get(name) for name in names}
    try:
        for name in names:
            os.environ[name] = getpass(f"{name} (hidden): ")
        # All configuration overrides and diagnostic output stay in memory.
        presets = {}
        for provider, model in selections:
            selected = replace(
                llm.get_model(provider, model),
                extra_body=Config(
                    default_value={"max_tokens": 256, "thinking": {"type": "disabled"}}
                ),
            )
            presets[provider] = replace(
                llm.get_provider(provider),
                models=Attribute(default_value=[selected]),
                timeout=Config(default_value=35.0),
            )
        with (
            patch.dict(llm.PROVIDERS, presets),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):

            async def bounded():
                async with asyncio.timeout(180):
                    return await exercise(selections)

            reports = asyncio.run(bounded())
        passed = all(report["passed"] for report in reports)
        print(json.dumps({"passed": passed, "scenarios": reports}, ensure_ascii=False))
        if not passed:
            raise SystemExit(1)
    except Exception as exc:
        print(json.dumps({"passed": False, "error_type": type(exc).__name__}))
        raise SystemExit(1) from None
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


if __name__ == "__main__":
    main()
