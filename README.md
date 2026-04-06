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

## Command-Line Interface (CLI) Documentation

The `project-map` command provides several subcommands for repository exploration and indexing.

### `project-map build` / `project-map refresh`
Generates or updates the repository digest (the "Project Map").

*   **Syntax:** `project-map build --root <path> --out-dir <path>`
*   **Mandatory Arguments:**
    *   `--root`: The path to the repository root to analyze.
    *   `--out-dir`: The directory to contain the output.
*   **Key Options:**
    *   `--max-shard-mb`: Size cap per JSON shard (default: 10MB). Oversized shards are automatically split into `.partN.json` files.
    *   `--profile`: `full` (default) or `light` (faster, skips some detail).

### `project-map status`
Returns the current workspace context and indexing health.

*   **Syntax:** `project-map status`
*   **Output:** Reports the last generation timestamp, indexing status (`Success` or `Partial`), and any analyzer errors recorded in `metadata.json`.

### `project-map find`
Finds a symbol (class, function, etc.) across the codebase. Works across Python, TypeScript/JS, Kotlin, Go, and Rust.

*   **Syntax:** `project-map find --query <search_string>`
*   **Mandatory Arguments:**
    *   `--query`: The symbol name or part of the Fully Qualified Name (FQN) to search for.

### `project-map impact`
Analyzes the architectural impact of modifying a specific symbol.

*   **Syntax:** `project-map impact --fqn <fully_qualified_name>`
*   **Mandatory Arguments:**
    *   `--fqn`: The exact Fully Qualified Name of the target symbol.

## Input/Output Specifications

### Data Formats (Input)
The tool expects a structured repository digest at `.project-map/docs/repo_summary/latest` (configurable via `PROJECT_ROOT` environment variable). This digest consists of:
*   `nav.json`: The root index mapping keys to shard filenames (handles sharded lists).
*   `metadata.json`: Run info, capabilities, and the global symbol index (GSI).
*   `paths.json`: Map of project IDs (PIDs) to relative file paths.
*   `*.symbols.json`: Shards containing symbol definitions.
*   `edges_*.json`: Dependency graph shards mapping call relationships.

### Supported Languages
Deep, AST-level support provided for:
- **Python**: Symbols, Pydantic models, FastAPI routes.
- **TypeScript / JavaScript / Vue**: Symbols and axios call detection.
- **Kotlin / JVM**: Symbols, call graphs, Kafka Streams EDA.
- **Go**: Packages, functions, types, and methods.
- **Rust**: Structs, enums, functions, traits, modules, and impls.
- **SQL**: Database schema extraction.

### Console Output (Output)
`project-map-cli` uses **Token-Oriented Object Notation (TOON)** for its console output. TOON is a dense, Markdown-formatted text format designed to be highly readable for LLMs while minimizing token consumption.

*   **Headers:** Resource type and query parameters.
*   **Breadcrumbs:** `Next Step:` blocks that guide the agent's reasoning loop.
*   **Density:** Key-value pairs are often condensed to reduce vertical space.

## Technical Code Samples

### CLI Output Example (TOON)
```text
Resource: Symbols | Query: UserService
Matches Found: 3
- [pid: 1] src/main/kotlin/com/example/UserService.kt (com.example.UserService)
- [pid: 4] src/main/kotlin/com/example/InternalUserService.kt (com.example.InternalUserService)
- [pid: 9] src/main/kotlin/com/example/UserModule.kt (com.example.UserModule)

Next Step: Run `project-map impact --fqn com.example.UserService` to analyze its impact.
```

### JSON Shard Example (Internal Format)
```json
{
  "symbols": [
    {
      "pid": 1,
      "name": "UserService",
      "qname": "com.example.UserService",
      "kind": "class"
    }
  ]
}
```

### MCP Server Registration
To register the tool with Gemini CLI:
```bash
gemini mcp add project-map-cli command "/path/to/project-map-cli/venv/bin/python" "-m" "project_map_cli.mcp.server"
```

## Development and Contribution Guidelines

We welcome contributions! Follow these steps to set up your development environment.

1.  **Clone and Install:** (See Installation Procedures)
2.  **Install Test Dependencies:**
    ```bash
    pip install pytest
    ```
3.  **Running Tests:**
    *   **Unit Tests:** `pytest tests/` (uses mocked data in `tests/fixtures/`).
4.  **Code Style:** Adhere to existing patterns in `src/project_map_cli/core/analyzers/` for adding new language support.

## Legal Information

This software is distributed under the **MIT License**.

Copyright (c) 2026 Ben Murray

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
