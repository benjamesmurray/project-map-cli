# Repo Summary Tool — v4

**Goal:** Emit compact, deterministic, shardable JSON describing a repo’s structure and key couplings — **no LLM calls during generation**.

- Small outputs (per-shard ≤ 2 MB)
- Deterministic (byte-identical for unchanged repos)
- Explicit FE↔API↔DB signals
- **Python/Vue/Timescale + Kotlin/Gradle/Kafka Streams** coverage
- One run per root; v4 only (no legacy)
- Isolated execution via dedicated virtual environment.

---

## Execution

The tool should be executed as a Python module:

```bash
python -m project_map_cli.core \
  --root /opt/project \
  --out-dir /opt/project/docs/repo_summary \
  --bundle-all \
  --exclude-spec /opt/project/utils/exclude_spec.py
```

### Manual Python Execution
If running directly via python, ensure `PYTHONPATH` includes the workspace root:

```bash
export PYTHONPATH="/opt/project"
python -m project_map_cli.core [ARGS]
```

---

## Modes

### Full mode
Use this for architecture/refactor planning, impact analysis, or when you need broad Kotlin symbol coverage.

```bash
# Example for full scan
python -m project_map_cli.core \
  --root /opt/project \
  --out-dir /opt/project/docs/repo_summary \
  --max-callsites 5 \
  --max-hotspots 10 \
  --max-entry-points 10 \
  --max-top-symbols 10 \
  --max-shard-mb 2 \
  --bundle-all \
  --exclude-spec /opt/project/utils/exclude_spec.py \
  --db-url-env TIMESCALE_DSN \
  --traceback
```

### Light mode

Use this for endpoint/UI-only work (FE → FastAPI glue, model field diffs). Kotlin analysis is **restricted to “hot” files** only (Kafka Streams imports or `.stream(` usage) and uses tighter caps.

```bash
python -m project_map_cli.core \
  --root /opt/project \
  --out-dir /opt/project/docs/repo_summary \
  --profile light
```

---

## Kotlin/Gradle/Kafka Streams support (v4)

v4 adds robust tree-sitter integration for Kotlin:

* `gradle.modules.json`: module graph + key build/dependency signals.
* `kotlin.symbols.json`: bounded Kotlin symbols (classes/functions/objects; annotations where relevant). Powered by `tree-sitter-kotlin`.
* `kafka.streams_eda.json`: Kafka Streams topology signals including topic refs and topology edges.

### Kotlin/Kafka caps

These are profile-defaulted but can be overridden via CLI flags:

* `--max-kotlin-files`
* `--kotlin-symbols-per-file`
* `--max-topics`
* `--max-edges`
* optional: `--no-kotlin` (disable Kotlin/Gradle/Kafka Streams analysis entirely)

---

## Dependencies (venv)

The tool runs in a dedicated virtual environment containing:
- `tree-sitter`
- `tree-sitter-kotlin`
- `pyyaml`
- `pytest` (for development)

---

## Outputs

Outputs land in `--out-dir`:

```
out/
  nav.json
  paths.json
  digest.top.json
  ...
  gradle.modules.json
  kotlin.symbols.json
  kafka.streams_eda.json
  all.json (bundled)
```

---

## Determinism rules

* Paths → PIDs assigned by sorted repo-relative POSIX paths.
* JSON serialization: sorted keys, minimal separators, UTF-8.
* No timestamps in shards (only `nav.run_id` derived from `paths.json` hash).
* All lists deterministically sorted.
