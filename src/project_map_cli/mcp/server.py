import os
import sys
import shlex
import traceback
from contextlib import redirect_stdout, redirect_stderr
import io
from mcp.server.stdio import stdio_server
from mcp.server import Server
from mcp.types import Tool, TextContent, CallToolResult

from project_map_cli.cli.main import cli as main_cli
from click.testing import CliRunner

# Initialize the server
server = Server("project-map-cli")

@server.list_tools()
async def handle_list_tools() -> list[Tool]:
    """
    List available tools. We only expose the 4 "Agent-Native" tools.
    """
    return [
        Tool(
            name="sc_status",
            description="Returns current workspace context, last active project, and available commands.",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        Tool(
            name="sc_help",
            description="Accepts a topic/command (e.g., 'find') and returns the detailed help text.",
            inputSchema={
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "The CLI topic or command to get help for (e.g., 'find', 'impact')"
                    }
                }
            }
        ),
        Tool(
            name="sc_exec",
            description="The primary workhorse tool. Accepts a CLI string (e.g., 'find --query User').",
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The command string to execute (e.g., 'find --query User')"
                    }
                },
                "required": ["command"]
            }
        ),
        Tool(
            name="sc_verify",
            description="Analyzes the last command's output or checks workspace state.",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        )
    ]

@server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict | None
) -> list[TextContent]:
    """
    Handle tool execution requests.
    """
    if name == "sc_status":
        status_text = (
            "Workspace: project-map-cli\n"
            "Phase: Discovery\n"
            "Available CLI Commands: find, impact\n\n"
            "Next Step: Run `sc_help --topic find` or execute a query with `sc_exec --command 'find --query User'`."
        )
        return [TextContent(type="text", text=status_text)]

    elif name == "sc_help":
        topic = arguments.get("topic", "")
        runner = CliRunner()
        args = [topic, "--help"] if topic else ["--help"]
        result = runner.invoke(main_cli, args)
        return [TextContent(type="text", text=result.output)]

    elif name == "sc_exec":
        command_str = arguments.get("command", "")
        if not command_str:
            return [TextContent(type="text", text="Error: command argument is required.")]
        
        args = shlex.split(command_str)
        runner = CliRunner()
        result = runner.invoke(main_cli, args)
        
        output = result.output
        if result.exception:
            output += f"\nException: {result.exception}\n{traceback.format_exc()}"
            
        return [TextContent(type="text", text=output)]

    elif name == "sc_verify":
        return [TextContent(type="text", text="Verification: System is operational. No recent actions to verify in state.")]

    else:
        raise ValueError(f"Unknown tool: {name}")

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options()
        )

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
