# Project Map CLI & MCP Server

![Version](https://img.shields.io/badge/version-0.1.0-blue)
![Build Status](https://img.shields.io/badge/build-passing-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)

**Agent-Native architectural awareness and token-efficient repository exploration.**

`project-map-cli` is a unified Python toolkit designed to help AI agents (like Claude Code, Cursor, Windsurf, and Gemini CLI) navigate massive codebases efficiently. It combines the heavy lifting of a repository parser with a blazing fast, token-optimized MCP server. Built from the ground up on the "Agent-Native" philosophy, it prioritizes latent knowledge alignment, a pull-based discovery model, and Token-Oriented Object Notation (TOON) to reduce context usage by 40-60%.

## Installation Procedures

Follow these steps to add `project-map-cli` to your local environment.

### Using Pip (Recommended)

1. **Clone the repository:**
   ```bash
   git clone https://github.com/your-org/project-map-cli.git
   cd project-map-cli
   ```

2. **Create and activate a virtual environment:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Install the package in editable mode:**
   ```bash
   pip install -e .
   ```

### Requirements
- Python 3.10 or higher
- tree-sitter-language-pack (included in dependencies)

## MCP Server Documentation

The `project-map-cli` MCP server exposes several semantic tools designed for agent use. These tools use the **`pm_`** prefix to avoid conflicts with other "Spec CLI" style servers.

### `pm_init`
Initializes or refreshes the repository digest (the "Project Map").

*   **Arguments:**
    *   `profile`: `full` (default) or `light` (faster, skips some detail).

### `pm_status`
Returns the current workspace context and indexing health.

### `pm_query`
Finds a symbol or gets dense file context.

*   **Arguments (provide one):**
    *   `query`: The symbol name or part of the Fully Qualified Name (FQN) to search for.
    *   `path`: The relative path to a file to inspect for AST outline and dependencies.

### `pm_plan`
Analyzes the architectural impact of modifying a specific symbol.

*   **Arguments:**
    *   `fqn`: The exact Fully Qualified Name of the target symbol.

### `pm_help` / `pm_verify`
Provides usage guidance and system health verification.

---

## Command-Line Interface (CLI) Documentation

For manual use, the `project-map` command provides several subcommands.

### `project-map build` / `project-map refresh`
*   **Syntax:** `project-map build --root <path> --out-dir <path>`
*   **Mandatory Arguments:**
    *   `--root`: The path to the repository root to analyze.
    *   `--out-dir`: The directory to contain the output.
*   **Key Options:**
    *   `--max-shard-mb`: Size cap per JSON shard (default: 10MB).
    *   `--profile`: `full` (default) or `light`.

### `project-map status`
Reports the last generation timestamp and indexing status.

### `project-map find`
*   **Syntax:** `project-map find --query <search_string>`

### `project-map context`
*   **Syntax:** `project-map context --path <file_path>`

### `project-map impact`
*   **Syntax:** `project-map impact --fqn <fully_qualified_name>`

## Input/Output Specifications

### Supported Languages
- **Python**: Symbols, Pydantic models, FastAPI routes.
- **TypeScript / JavaScript / Vue**: Symbols and axios call detection.
- **Kotlin / JVM**: Symbols, call graphs, Kafka Streams EDA.
- **Go**: Packages, functions, types, and methods.
- **Rust**: Structs, enums, functions, traits, modules, and impls.
- **SQL**: Database schema extraction.

### Console Output (TOON)
`project-map-cli` uses **Token-Oriented Object Notation (TOON)**. TOON is a dense, Markdown-formatted text format designed to be highly readable for LLMs while minimizing token consumption.

## Technical Code Samples

### MCP Output Example (TOON)
When running in an MCP context, the tool provides specific guidance for the agent:
```text
Resource: Symbols | Query: UserService
Matches Found: 3
- [pid: 1] src/main/kotlin/com/example/UserService.kt (com.example.UserService)
- [pid: 4] src/main/kotlin/com/example/InternalUserService.kt (com.example.InternalUserService)
- [pid: 9] src/main/kotlin/com/example/UserModule.kt (com.example.UserModule)

Next Step: Use the `pm_plan` tool with fqn: 'com.example.UserService' to analyze its impact.
```

### MCP Server Registration
To register the tool with Gemini CLI:
```bash
gemini mcp add project-map-cli command "/path/to/project-map-cli/venv/bin/python" "-m" "project_map_cli.mcp.server"
```

## Legal Information

This software is distributed under the **MIT License**.
Copyright (c) 2026 Ben Murray
