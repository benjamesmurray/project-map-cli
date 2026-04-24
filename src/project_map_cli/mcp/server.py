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
        ),
        Tool(
            name="pm_fetch_symbol",
            description="Extracts the raw source code of a specific symbol (class/function/variable) from a file using AST parsing.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path to the file."
                    },
                    "symbol": {
                        "type": "string",
                        "description": "The name of the symbol to extract."
                    }
                },
                "required": ["path", "symbol"]
            }
        ),
        Tool(
            name="pm_check_blast_radius",
            description="Identifies all components and files that depend on or import a specific symbol. Useful for assessing the impact of a change.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path to the file containing the symbol."
                    },
                    "symbol": {
                        "type": "string",
                        "description": "The name of the symbol to analyze."
                    }
                },
                "required": ["path", "symbol"]
            }
        ),
        Tool(
            name="pm_semantic_search",
            description="Searches for code logic using natural language keywords across the indexed codebase.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The natural language search query (e.g., 'where are passwords hashed?')."
                    }
                },
                "required": ["query"]
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
                "desc": "Check workspace health and see which analyzers are active.",
                "usage": "Call with no arguments."
            },
            "pm_init": {
                "desc": "Refresh the map index after significant code changes to maintain discovery accuracy.",
                "usage": 'Call with {"profile": "full"} or {"profile": "light"}.'
            },
            "pm_query": {
                "desc": "Use for semantic search of symbols or to get a dense architectural summary of a specific file path.",
                "usage": 'Usage (symbol search): Call with {"query": "MyClassName"}\n  Usage (file context): Call with {"path": "src/main.py"}'
            },
            "pm_plan": {
                "desc": "Run this with the Fully Qualified Name (FQN) of a symbol before starting a refactor to identify downstream dependencies and impact.",
                "usage": 'Usage: Call with {"fqn": "com.example.MyClassName"}'
            },
            "pm_verify": {
                "desc": "Checks the health of the project map system and recent indexing status.",
                "usage": "Call with no arguments."
            },
            "pm_help": {
                "desc": "Returns detailed help text for a specific command or topic.",
                "usage": 'Call with no arguments or {"topic": "pm_query"}.'
            },
            "pm_fetch_symbol": {
                "desc": "Extracts the raw source code of a specific symbol (class/function/variable) from a file using AST parsing.",
                "usage": 'Usage: Call with {"path": "src/main.py", "symbol": "MyClass"}'
            },
            "pm_check_blast_radius": {
                "desc": "Identifies all components and files that depend on or import a specific symbol. Useful for assessing the impact of a change.",
                "usage": 'Usage: Call with {"path": "src/main.py", "symbol": "my_function"}'
            },
            "pm_semantic_search": {
                "desc": "Searches for code logic using natural language keywords across the indexed codebase.",
                "usage": 'Usage: Call with {"query": "how is X processed?"}'
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
            help_text += "You have access to the following 'pm_' native tools. Use the `map` shim for the most efficient command path (e.g., `map pm_query --query ...`).\n\n"
            help_text += "Pro-Tip: Use `pm_query` when you know the symbol name or file path; use `pm_semantic_search` when you're looking for a concept or logic (e.g., 'where are users saved?').\n\n"
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

    elif name == "pm_fetch_symbol":
        path = arguments.get("path")
        symbol = arguments.get("symbol")
        result = runner.invoke(main_cli, ["fetch", "--path", path, "--symbol", symbol])
        return [TextContent(type="text", text=result.output)]

    elif name == "pm_check_blast_radius":
        path = arguments.get("path")
        symbol = arguments.get("symbol")
        result = runner.invoke(main_cli, ["blast", "--path", path, "--symbol", symbol])
        return [TextContent(type="text", text=result.output)]

    elif name == "pm_semantic_search":
        query = arguments.get("query")
        result = runner.invoke(main_cli, ["search", query])
        return [TextContent(type="text", text=result.output)]

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
