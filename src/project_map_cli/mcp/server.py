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
    List available tools. We use the 'pm_' prefix for project-map-cli.
    """
    return [
        Tool(
            name="pm_status",
            description="Returns current workspace context, last generation time, and available commands.",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        Tool(
            name="pm_help",
            description="Returns detailed help text for a specific command or topic.",
            inputSchema={
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "The command or topic to get help for (e.g., 'find', 'impact', 'context')"
                    }
                }
            }
        ),
        Tool(
            name="pm_init",
            description="Initializes or refreshes the project map index. Use this after significant code changes.",
            inputSchema={
                "type": "object",
                "properties": {
                    "profile": {
                        "type": "string",
                        "enum": ["full", "light"],
                        "description": "The scan profile (full = deep analysis, light = fast scan). Defaults to full."
                    }
                }
            }
        ),
        Tool(
            name="pm_query",
            description="Search for symbols or get file context. Provide 'query' for symbol search or 'path' for file context.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Symbol name to search for across the codebase."
                    },
                    "path": {
                        "type": "string",
                        "description": "Relative file path to get dense architectural context for."
                    }
                }
            }
        ),
        Tool(
            name="pm_plan",
            description="Analyzes the architectural impact of a symbol. Useful for planning refactors or changes.",
            inputSchema={
                "type": "object",
                "properties": {
                    "fqn": {
                        "type": "string",
                        "description": "Fully Qualified Name (FQN) of the symbol to analyze."
                    }
                },
                "required": ["fqn"]
            }
        ),
        Tool(
            name="pm_verify",
            description="Checks the health of the project map system and recent indexing status.",
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
    os.environ["MCP_MODE"] = "1"
    runner = CliRunner()
    
    if name == "pm_status":
        result = runner.invoke(main_cli, ["status"])
        return [TextContent(type="text", text=result.output)]

    elif name == "pm_help":
        topic = (arguments or {}).get("topic", "").lower()
        
        # Tools documentation
        tools_docs = {
            "pm_status": {
                "desc": "Checks the current workspace context and indexing status.",
                "usage": "Call with no arguments."
            },
            "pm_init": {
                "desc": "Initializes or refreshes the project map index. Use this after significant code changes.",
                "usage": 'Call with {"profile": "full"} or {"profile": "light"}.'
            },
            "pm_query": {
                "desc": "Search for a symbol across the codebase or get architectural context for a specific file.",
                "usage": 'Usage (symbol search): Call with {"query": "MyClassName"}\n  Usage (file context): Call with {"path": "src/main.py"}'
            },
            "pm_plan": {
                "desc": "Analyze the architectural impact and dependencies of a fully qualified symbol.",
                "usage": 'Usage: Call with {"fqn": "com.example.MyClassName"}'
            },
            "pm_verify": {
                "desc": "Checks if the project map index exists and the system is healthy.",
                "usage": "Call with no arguments."
            },
            "pm_help": {
                "desc": "Returns detailed help text for a specific command or topic.",
                "usage": 'Call with no arguments or {"topic": "pm_query"}.'
            }
        }
        
        # Build help text
        if topic and topic in tools_docs:
            doc = tools_docs[topic]
            help_text = f"Help for {topic}:\n{doc['desc']}\n  Usage: {doc['usage']}"
        elif topic and f"pm_{topic}" in tools_docs:
            doc = tools_docs[f"pm_{topic}"]
            help_text = f"Help for pm_{topic}:\n{doc['desc']}\n  Usage: {doc['usage']}"
        else:
            help_text = "Project Map CLI - MCP Tools Help\n\n"
            help_text += "You have access to the following 'pm_' native tools. Use these tools by providing the specific JSON parameters, rather than passing raw CLI commands.\n\n"
            for t_name, t_info in tools_docs.items():
                help_text += f"* {t_name}\n  {t_info['desc']}\n  Usage: {t_info['usage']}\n\n"
        
        return [TextContent(type="text", text=help_text.strip())]

    elif name == "pm_init":
        profile = arguments.get("profile", "full")
        result = runner.invoke(main_cli, ["build", "--profile", profile])
        output = result.output
        if result.exception:
            output += f"\nException: {result.exception}\n{traceback.format_exc()}"
        return [TextContent(type="text", text=output)]

    elif name == "pm_query":
        query = arguments.get("query")
        path = arguments.get("path")
        
        if query:
            result = runner.invoke(main_cli, ["find", "--query", query])
        elif path:
            result = runner.invoke(main_cli, ["context", "--path", path])
        else:
            return [TextContent(type="text", text="Error: Either 'query' or 'path' must be provided to pm_query.")]
            
        output = result.output
        if result.exception:
            output += f"\nException: {result.exception}\n{traceback.format_exc()}"
        return [TextContent(type="text", text=output)]

    elif name == "pm_plan":
        fqn = arguments.get("fqn", "")
        result = runner.invoke(main_cli, ["impact", "--fqn", fqn])
        output = result.output
        if result.exception:
            output += f"\nException: {result.exception}\n{traceback.format_exc()}"
        return [TextContent(type="text", text=output)]

    elif name == "pm_verify":
        # We can implement a more robust verify by checking if the index files exist
        result = runner.invoke(main_cli, ["status"])
        if "Discovery (No index found)" in result.output:
            return [TextContent(type="text", text="Status: Index missing. Run pm_init to generate the project map.")]
        return [TextContent(type="text", text="Status: System healthy. Index is present and accessible.")]

    else:
        raise ValueError(f"Unknown tool: {name}")

async def run_server():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options()
        )

def main():
    import asyncio
    asyncio.run(run_server())

if __name__ == "__main__":
    main()
