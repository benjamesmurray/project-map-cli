import pytest
from pathlib import Path
from .fixtures import MultiLanguageRepo
from .test_coverage_and_signals import run_tool

def test_kafka_streams(tmp_path):
    root = tmp_path / "repo"
    out = tmp_path / "out"
    repo = MultiLanguageRepo(root)
    repo.generate()
    
    verify = run_tool(root, out)
    
    # Check Kafka topics and roles
    verify.assert_kafka_topic_exists("users-topic", "consumer")
    verify.assert_kafka_topic_exists("processed-users", "producer")
