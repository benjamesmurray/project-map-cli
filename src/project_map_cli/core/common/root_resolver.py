# infra/digest_tool_v6/common/root_resolver.py
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Set

# Markers that indicate a project root
_ROOT_MARKERS = {
    ".git",
    "package.json",
    "pyproject.toml",
    "settings.gradle.kts",
    "settings.gradle",
    "go.mod",
    "Cargo.toml",
    "requirements.txt",
}

def find_project_root(start_path: Path) -> Path:
    """
    Search upwards from start_path to find the nearest project root.
    If no root is found, returns start_path (the provided scan root).
    """
    current = start_path.resolve()
    
    # Iterate up through parent directories
    # We check if any of the root markers exist in the current directory
    temp = current
    while True:
        try:
            # Check for markers
            for marker in _ROOT_MARKERS:
                if (temp / marker).exists():
                    return temp
        except Exception:
            pass
            
        # Move to parent
        parent = temp.parent
        if parent == temp:  # Hit the file system root
            break
        temp = parent
        
    return current

def normalize_qname(path: Path, root: Path, symbol_name: str) -> str:
    """
    Generate a normalized qualified name (qname) for a symbol relative to a root.
    Replaces path separators with dots and strips the file extension.
    """
    try:
        rel_path = path.relative_to(root)
    except ValueError:
        # If path is not under root, use its name as a fallback
        return symbol_name

    # Remove extension (e.g., .py, .ts, .kt)
    parts = list(rel_path.with_suffix("").parts)
    
    # Handle common patterns like 'src' or 'lib' if they are top-level under root
    # but we usually want to keep them if they are part of the package structure.
    
    if symbol_name:
        parts.append(symbol_name)
        
    return ".".join(parts)
