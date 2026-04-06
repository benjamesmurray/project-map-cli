import os
from pathlib import Path
import pytest
from project_map_cli.core.query_engine import QueryEngine

@pytest.fixture
def test_engine():
    # Setup the WDE_ROOT to point to our test fixtures directory
    wde_root = str(Path(__file__).parent / "fixtures")
    return QueryEngine(wde_root=wde_root)

def test_resolve_pids(test_engine):
    pids = ["1", "3", "99"]
    result = test_engine.resolve_pids(pids)
    assert result["1"] == "src/main/kotlin/com/example/UserService.kt"
    assert result["3"] == "src/main/kotlin/com/example/UserRepository.kt"
    assert result["99"] == "unknown"

def test_search_symbols(test_engine):
    matches = test_engine.search_symbols("User")
    assert len(matches) == 3
    qnames = {m["qname"] for m in matches}
    assert "com.example.UserService" in qnames
    
    # Path should be resolved
    service = next(m for m in matches if m["qname"] == "com.example.UserService")
    assert service["path"] == "src/main/kotlin/com/example/UserService.kt"

def test_get_callers(test_engine):
    # UserController calls UserService
    callers = test_engine.get_callers("com.example.UserService")
    assert len(callers) == 1
    assert callers[0]["fqn"] == "com.example.UserController"
    assert callers[0]["pid"] == 2

def test_analyze_impact(test_engine):
    # UserService calls UserRepository and produces to Kafka topic
    # Target: UserRepository -> callers: UserService -> callers: UserController
    # The queue works by finding callers.
    impact = test_engine.analyze_impact("com.example.UserRepository")
    assert impact["target"] == "com.example.UserRepository"
    
    # Visited should include UserRepository, UserService, UserController
    assert impact["impacted_nodes_count"] == 3
