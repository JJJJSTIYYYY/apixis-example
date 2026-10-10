"""Main entry point for the Example agent."""

import argparse
import asyncio
import sys


async def _run(args: argparse.Namespace) -> None:
    from apixis import start_core

    from .core import llm
    from .core.runtime import shutdown_core

    await start_core()
    try:
        if args.check:
            from .core.workspace import build_workspace_graph

            graph = build_workspace_graph()
            try:
                result = await graph.invoke({"notes": []})
                print(result["summary"])
                print("Apixis concurrent workspace graph: OK")
            finally:
                graph.decompose()
        elif args.prompt is not None:
            from .app import run_prompt

            await run_prompt(
                args.prompt, approve_tools=args.approve_tools, max_steps=args.max_steps
            )
        else:
            from .app import chat_loop

            await chat_loop()
    finally:
        await llm.close_client()
        await shutdown_core()


def main() -> None:
    """Entry point for uv run example."""
    parser = argparse.ArgumentParser(
        description="Example：支持历史记录与工具审批的终端智能助手"
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--prompt", help="单次提问，不保存对话历史")
    modes.add_argument(
        "--check",
        action="store_true",
        help="检查本地图调度，不需要 API Key",
    )
    parser.add_argument(
        "--approve-tools",
        action="store_true",
        help="单次模式下批准通过命令守卫的工具",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=32,
        help="单次模式的执行步数上限（默认 32）",
    )
    args = parser.parse_args()
    if args.approve_tools and args.prompt is None:
        parser.error("--approve-tools requires --prompt")
    if args.max_steps <= 0:
        parser.error("--max-steps must be positive")
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        print("\n再见！")
    except Exception as exc:
        from .ui.errors import explain_error

        message, hint = explain_error(exc)
        print(f"提示：{message}\n{hint}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
