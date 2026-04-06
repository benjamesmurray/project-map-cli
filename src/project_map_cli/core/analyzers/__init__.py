# utils/digest_tool_v3/analyzers/__init__.py
from __future__ import annotations

# Re-export analyzer submodules for convenient imports in orchestrator
from . import digest_top
from . import imports_repo_only
from . import ctor_signatures
from . import files_index
from . import fastapi_routes
from . import pydantic_models
from . import fe_calls
from . import db_schema
from . import api_clients_map

# v3 additions
from . import gradle_modules
from . import kotlin_symbols
from . import kafka_streams_eda

# v5 additions
from . import symbol_registry
from . import kotlin_calls
from . import inheritance

__all__ = [
    "digest_top",
    "imports_repo_only",
    "ctor_signatures",
    "files_index",
    "fastapi_routes",
    "pydantic_models",
    "fe_calls",
    "db_schema",
    "api_clients_map",
    # v3 additions
    "gradle_modules",
    "kotlin_symbols",
    "kafka_streams_eda",
    # v5 additions
    "symbol_registry",
    "kotlin_calls",
    "inheritance",
]
