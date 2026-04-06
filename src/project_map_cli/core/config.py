# utils/digest_tool_v3/config.py
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Pattern, Tuple


# Default glob excludes (expanded for Gradle/Kotlin build artefacts)
DEFAULT_EXCLUDE: Tuple[str, ...] = (
    ".git/**", ".hg/**", ".svn/**", ".idea/**", ".vscode/**", ".DS_Store",

    # Node / frontend
    "node_modules/**", "**/node_modules/**",
    "dist/**", "**/dist/**",
    ".parcel-cache/**", ".next/**", ".nuxt/**", ".svelte-kit/**",
    "yarn.lock", "package-lock.json", "pnpm-lock.yaml",

    # Python
    "__pycache__/**", ".pytest_cache/**", ".mypy_cache/**", ".ruff_cache/**",
    ".venv/**", "venv/**", "env/**", ".tox/**", ".eggs/**", "*.egg-info/**",
    "**/.venv/**", "**/site/**", "**/.cache/**",
    "pip-wheel-metadata/**", ".python_build/**", "build/lib/**",
    "_build/**", "docs/_site/**", "site/**",

    # Gradle / Kotlin / JVM
    ".gradle/**", "**/.gradle/**",
    "build/**", "**/build/**",
    "out/**", "**/out/**",
    "target/**", "**/target/**",
    ".kotlin/**", "**/.kotlin/**",

    # Common generated / static artefacts
    "public/**", "static/**",
    "**/*.parquet", "**/*.feather", "**/*.arrow", "**/*.avro",
    "**/*.csv", "**/*.tsv", "**/*.ndjson",
    "**/*.log", "**/*.sqlite-wal", "**/*.sqlite-shm",
)


@dataclass(frozen=True)
class Config:
    # Required
    root: Path
    out_dir: Path
    ns_allow: Optional[str]
    ns_auto: bool = True

    # Output behavior
    timestamped_out: bool = True
    bundle_all: bool = False
    bundle_gzip: bool = False
    profile: str = "full"  # 'full' or 'light'

    # DB URL environment variable (for reflection)
    db_url_env: str = "PG_DSN"

    # User-defined excludes (all repo-relative)
    exclude_dirs: Tuple[str, ...] = field(default_factory=tuple)         # by directory *name* (component match)
    exclude_files_exact: Tuple[str, ...] = field(default_factory=tuple)  # exact repo-relative path match (POSIX)
    exclude_globs: Tuple[str, ...] = field(default_factory=tuple)        # glob/prefix patterns

    # Limits / caps (existing)
    max_callsites: int = 5
    max_hotspots: int = 10
    max_entry_points: int = 10
    max_top_symbols: int = 10
    max_shard_mb: int = 10

    # New caps for Kotlin/Gradle/Kafka Streams (profile-tuned if None)
    max_kotlin_files: Optional[int] = None
    max_kotlin_symbols_per_file: Optional[int] = None
    max_topics: Optional[int] = None
    max_edges: Optional[int] = None

    # Legacy default glob excludes (merged with exclude_globs in scan)
    excludes: Tuple[str, ...] = field(default_factory=lambda: DEFAULT_EXCLUDE)

    # Derived
    ns_allow_re: Optional[Pattern[str]] = field(init=False, repr=False)
    max_shard_bytes: int = field(init=False)

    # File type filters (existing)
    py_suffixes: Tuple[str, ...] = (".py",)
    vue_suffixes: Tuple[str, ...] = (".vue",)
    js_ts_suffixes: Tuple[str, ...] = (".ts", ".tsx", ".js", ".jsx")
    sql_suffixes: Tuple[str, ...] = (".sql", ".ddl")
    sqlite_suffixes: Tuple[str, ...] = (".db", ".sqlite")

    # New suffix buckets
    kotlin_suffixes: Tuple[str, ...] = (".kt", ".kts")
    go_suffixes: Tuple[str, ...] = (".go",)
    rust_suffixes: Tuple[str, ...] = (".rs",)

    config_suffixes: Tuple[str, ...] = (".properties", ".yaml", ".yml", ".json")

    # Gradle "known" files to prioritise for module/deps discovery
    gradle_known_files: Tuple[str, ...] = (
        "settings.gradle",
        "settings.gradle.kts",
        "build.gradle",
        "build.gradle.kts",
        "libs.versions.toml",
        "gradle.properties",
    )

    # Kotlin "hot file" heuristics (used in light profile to restrict symbol parsing)
    kotlin_hot_import_re: Pattern[str] = field(init=False, repr=False)
    kotlin_hot_usage_re: Pattern[str] = field(init=False, repr=False)

    # Derived behaviour flags
    kotlin_symbols_scope: str = field(init=False)  # "all" or "hot"

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", self.root.resolve())
        object.__setattr__(self, "out_dir", self.out_dir.resolve())

        if not self.root.exists() or not self.root.is_dir():
            raise ValueError(f"--root must be an existing directory: {self.root}")

        # Compile allow-list if provided
        if self.ns_allow:
            try:
                compiled = re.compile(self.ns_allow)
            except re.error as exc:
                raise ValueError(f"--ns-allow invalid regex: {exc}") from None
            object.__setattr__(self, "ns_allow_re", compiled)
        else:
            object.__setattr__(self, "ns_allow_re", None)

        # Validate profile
        if self.profile not in ("full", "light"):
            raise ValueError(f"profile must be 'full' or 'light' (got {self.profile!r})")

        # Validate numeric caps (existing)
        for name in ("max_callsites", "max_hotspots", "max_entry_points", "max_top_symbols", "max_shard_mb"):
            val = getattr(self, name)
            if not isinstance(val, int) or val <= 0:
                raise ValueError(f"{name} must be a positive integer (got {val!r})")

        # Profile-tuned defaults for new knobs (bounded for LLM-friendly all.json)
        # You can override any of these via CLI; None means "use profile default".
        if self.profile == "light":
            defaults = {
                "max_kotlin_files": 120,
                "max_kotlin_symbols_per_file": 120,
                "max_topics": 150,
                "max_edges": 150,
                "kotlin_symbols_scope": "hot",
            }
        else:
            defaults = {
                "max_kotlin_files": 500,
                "max_kotlin_symbols_per_file": 250,
                "max_topics": 300,
                "max_edges": 300,
                "kotlin_symbols_scope": "all",
            }

        for name, default in defaults.items():
            if name == "kotlin_symbols_scope":
                object.__setattr__(self, name, default)
                continue
            current = getattr(self, name)
            if current is None:
                object.__setattr__(self, name, int(default))
            else:
                if not isinstance(current, int) or current <= 0:
                    raise ValueError(f"{name} must be a positive integer (got {current!r})")

        # Byte cap
        object.__setattr__(self, "max_shard_bytes", self.max_shard_mb * 1024 * 1024)

        # Normalize exclude_files_exact to POSIX repo-relative strings without leading './'
        norm_files = tuple(
            str(Path(p).as_posix()).lstrip("./")
            for p in self.exclude_files_exact
        )
        object.__setattr__(self, "exclude_files_exact", norm_files)

        # Kotlin "hot" patterns (cheap text scan; tree-sitter comes later in analyzers)
        # - import org.apache.kafka.streams...
        # - usage: .stream(  (Kafka Streams DSL)
        object.__setattr__(
            self,
            "kotlin_hot_import_re",
            re.compile(r"^\s*import\s+org\.apache\.kafka\.streams\b", flags=re.MULTILINE),
        )
        object.__setattr__(
            self,
            "kotlin_hot_usage_re",
            re.compile(r"\.\s*stream\s*\(", flags=re.MULTILINE),
        )

        # Ensure out_dir exists
        self.out_dir.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------------
    # Helpers (used by scanner/analyzers to disambiguate .kts files)
    # ---------------------------------------------------------------------
    def is_gradle_file(self, path: Path) -> bool:
        """
        Return True if 'path' should be treated as a Gradle-related file.
        This is stricter than suffix matching, to avoid misclassifying generic .kts scripts.
        """
        name = path.name
        if name in self.gradle_known_files:
            return True
        # Gradle build scripts can exist outside the root as module build files
        if name.endswith(".gradle") or name.endswith(".gradle.kts"):
            return True
        return False

    def is_kotlin_file(self, path: Path) -> bool:
        """
        Return True if 'path' is Kotlin source/script and NOT a Gradle build script.
        """
        suf = path.suffix.lower()
        if suf not in self.kotlin_suffixes:
            return False
        # Treat Gradle scripts as Gradle, not Kotlin symbols
        if self.is_gradle_file(path):
            return False
        return True

    def is_go_file(self, path: Path) -> bool:
        return path.suffix.lower() in self.go_suffixes

    def is_rust_file(self, path: Path) -> bool:
        return path.suffix.lower() in self.rust_suffixes

    def is_config_file(self, path: Path) -> bool:
        return path.suffix.lower() in self.config_suffixes
