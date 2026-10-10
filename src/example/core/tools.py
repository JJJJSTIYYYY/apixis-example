"""Tool definitions and execution for the agent."""

import asyncio
import json
import os
import signal
from contextlib import suppress
from typing import Any

# Tool registry: name -> {description, parameters, func}
TOOLS: dict[str, dict[str, Any]] = {}


def tool(name: str, description: str, parameters: dict[str, Any]):
    """Decorator to register a function as an agent tool."""

    def decorator(func):
        TOOLS[name] = {
            "description": description,
            "parameters": parameters,
            "func": func,
        }
        return func

    return decorator


def get_tools_schema() -> list[dict[str, Any]]:
    """Return OpenAI-compatible tool definitions."""
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": spec["description"],
                "parameters": spec["parameters"],
            },
        }
        for name, spec in TOOLS.items()
    ]


async def execute_tool(tool_call: dict[str, Any]) -> str:
    """Execute a single tool call and return the result as a string."""
    func_name = tool_call["function"]["name"]
    if func_name not in TOOLS:
        return json.dumps({"error": f"Unknown tool: {func_name}"})

    try:
        arguments = json.loads(tool_call["function"]["arguments"])
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be a JSON object")
        result = await TOOLS[func_name]["func"](**arguments)
        # Return full result - AI gets untruncated output
        # UI layer handles display truncation separately
        return result if isinstance(result, str) else json.dumps(result)
    except Exception as e:
        return json.dumps({"error": str(e)})


# ─── Tool implementations ────────────────────────────────────────────


@tool(
    name="run_command",
    description="Execute a shell command on the local machine and return its output. "
    "Use for file operations, system info, git commands, etc.",
    parameters={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute.",
            },
        },
        "required": ["command"],
    },
)
async def run_command(command: str) -> str:
    """Run a shell command and reap subprocesses on timeout or cancellation."""
    if not isinstance(command, str) or not command.strip():
        return json.dumps({"error": "command must be a non-empty string"})
    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        # A separate POSIX process group also owns children of the shell.
        start_new_session=(os.name == "posix"),
    )
    communicated = False
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        communicated = True
        output_parts = []
        if stdout:
            output_parts.append(stdout.decode("utf-8", errors="replace"))
        if stderr:
            output_parts.append(stderr.decode("utf-8", errors="replace"))
        output = "\n".join(output_parts) if output_parts else "(no output)"
        # Return full output; display truncation remains the UI's responsibility.
        return json.dumps(
            {
                "command": command,
                "exit_code": proc.returncode,
                "output": output.strip(),
            },
            ensure_ascii=False,
        )
    except asyncio.TimeoutError:
        return json.dumps(
            {"command": command, "error": "Command timed out after 30 seconds"}
        )
    finally:
        if not communicated and os.name == "posix":
            # Kill descendants even if the shell has already exited.
            with suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
        elif not communicated and proc.returncode is None:
            with suppress(ProcessLookupError):
                proc.kill()
        await proc.wait()
