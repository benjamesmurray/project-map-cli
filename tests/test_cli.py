import os
from pathlib import Path
import pytest
from click.testing import CliRunner
from project_map_cli.cli.main import cli

@pytest.fixture(autouse=True)
def set_env(monkeypatch):
    # Point the CLI to use our fixtures
    fixture_root = str(Path(__file__).parent / "fixtures")
    monkeypatch.setenv("PROJECT_ROOT", fixture_root)
    monkeypatch.setenv("PROJECT_MAP_DIR", fixture_root + "/docs/repo_summary/latest")

def test_find_command():
    runner = CliRunner()
    result = runner.invoke(cli, ["find", "--query", "UserService"])
    assert result.exit_code == 0
    assert "Resource: Symbols | Query: UserService" in result.output
    assert "Matches Found: 1" in result.output
    assert "- [pid: 1] src/main/kotlin/com/example/UserService.kt (com.example.UserService)" in result.output
    assert "Next Step: Run `project-map impact" in result.output

def test_impact_command():
    runner = CliRunner()
    result = runner.invoke(cli, ["impact", "--fqn", "com.example.UserRepository"])
    assert result.exit_code == 0
    assert "Resource: Impact Analysis | Target: com.example.UserRepository" in result.output
    assert "Nodes Impacted: 3" in result.output
    assert "Next Step: Run `project-map status` for workspace overview." in result.output

def test_status_command():
    runner = CliRunner()
    result = runner.invoke(cli, ["status"])
    assert result.exit_code == 0
    assert "Workspace: project-map-cli" in result.output
    assert "Available Commands: build, refresh, find, context, impact, status" in result.output

def test_mcp_mode_advice(monkeypatch):
    monkeypatch.setenv("MCP_MODE", "1")
    runner = CliRunner()
    
    # Test find advice
    result = runner.invoke(cli, ["find", "--query", "UserService"])
    assert "Next Step: Run `map pm_plan --fqn com.example.UserService` to analyze its impact." in result.output
    
    # Test impact advice
    result = runner.invoke(cli, ["impact", "--fqn", "com.example.UserRepository"])
    assert "Next Step: Run `map pm_status` for a workspace overview." in result.output
    
    # Test status advice
    result = runner.invoke(cli, ["status"])
    assert "Available Tools: pm_init, pm_query, pm_plan, pm_status, pm_verify, pm_help" in result.output
    assert "Next Step: Run `map pm_query --query <query>` to explore." in result.output

def test_mcp_help_command(monkeypatch):
    monkeypatch.setenv("MCP_MODE", "1")
    runner = CliRunner()
    result = runner.invoke(cli, ["help"])
    assert result.exit_code == 0
    assert "Project Map CLI - Agent Mode" in result.output
    assert "Use the `map` shim for efficient tool calls:" in result.output
    assert "map pm_query --query \"MySymbol\"" in result.output


