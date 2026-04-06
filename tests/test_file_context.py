import os
from pathlib import Path
import pytest
from click.testing import CliRunner
from project_map_cli.core.query_engine import QueryEngine
from project_map_cli.cli.main import cli

@pytest.fixture
def test_engine(monkeypatch):
    fixture_root = str(Path(__file__).parent / "fixtures")
    monkeypatch.setenv("PROJECT_MAP_DIR", fixture_root + "/docs/repo_summary/latest")
    return QueryEngine(project_root=fixture_root)

def test_get_pid_for_path(test_engine):
    pid = test_engine.get_pid_for_path("src/main/kotlin/com/example/UserService.kt")
    assert pid == 1

def test_get_file_outline(test_engine):
    outline = test_engine.get_file_outline(1, "src/main/kotlin/com/example/UserService.kt")
    assert outline.get("p") == 1
    assert outline.get("l") == 50
    assert len(outline.get("c")) == 1
    assert outline.get("c")[0]["name"] == "UserService"

def test_get_shallow_dependencies(test_engine):
    # Inbound to UserService: UserController (pid 2) + Extra1-5
    deps = test_engine.get_shallow_dependencies(1, "src/main/kotlin/com/example/UserService.kt")
    
    assert "inbound" in deps
    # UserController + Extra1-5 = 6 inbound edges
    assert len(deps["inbound"]) == 6
    
    # Outbound from UserService: UserRepository + Dep1-5
    assert "outbound" in deps
    assert len(deps["outbound"]) == 6

def test_context_command(monkeypatch):
    fixture_root = str(Path(__file__).parent / "fixtures")
    monkeypatch.setenv("PROJECT_MAP_DIR", fixture_root + "/docs/repo_summary/latest")
    
    runner = CliRunner()
    result = runner.invoke(cli, ["context", "--path", "src/main/kotlin/com/example/UserService.kt"])
    
    assert result.exit_code == 0
    assert "Resource: FileContext | Path: src/main/kotlin/com/example/UserService.kt | pid: 1 | LOC: 50" in result.output
    assert "--- File Outline ---" in result.output
    assert "- class UserService (ln: 10)" in result.output
    assert "- function process (ln: 25)" in result.output
    assert "Inbound Dependencies (Who imports this):" in result.output
    # Should be truncated at 5
    assert result.output.count("- src/main/kotlin/com/example/") == 5
    assert "... and 1 more." in result.output
    
    assert "Outbound Dependencies (What this file imports):" in result.output
    assert result.output.count("- src.main.kotlin.com.example.") == 5
    assert "... and 1 more." in result.output

def test_context_command_not_found(monkeypatch):
    fixture_root = str(Path(__file__).parent / "fixtures")
    monkeypatch.setenv("PROJECT_MAP_DIR", fixture_root + "/docs/repo_summary/latest")
    
    runner = CliRunner()
    result = runner.invoke(cli, ["context", "--path", "non_existent.py"])
    
    assert result.exit_code == 0
    assert "Status: Not found in project map index." in result.output

def test_context_command_empty_file(monkeypatch):
    fixture_root = str(Path(__file__).parent / "fixtures")
    monkeypatch.setenv("PROJECT_MAP_DIR", fixture_root + "/docs/repo_summary/latest")
    
    runner = CliRunner()
    result = runner.invoke(cli, ["context", "--path", "src/empty.py"])
    
    assert result.exit_code == 0
    assert "LOC: 0" in result.output
    assert "- (No classes or functions detected)" in result.output
