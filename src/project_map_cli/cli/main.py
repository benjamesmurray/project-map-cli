import os
import click
from project_map_cli.core.query_engine import QueryEngine

def is_mcp_mode() -> bool:
    return os.environ.get("MCP_MODE") == "1"

@click.group()
def cli():
    """Agent-Native architectural awareness CLI."""
    pass

@cli.command()
@click.option('--query', required=True, help="Symbol to search for")
def find(query: str):
    """Find a symbol across the codebase."""
    engine = QueryEngine()
    try:
        matches = engine.search_symbols(query)
    except Exception as e:
        click.echo(f"Error: {e}")
        return

    # TOON formatting
    click.echo(f"Resource: Symbols | Query: {query}")
    click.echo(f"Matches Found: {len(matches)}")
    for m in matches[:10]: # limit to 10 for TOON output
        click.echo(f"- [pid: {m.get('pid', 'N/A')}] {m.get('path', 'unknown')} ({m.get('qname', m.get('name', 'unknown'))})")
    
    if len(matches) > 10:
        click.echo(f"... and {len(matches) - 10} more.")

    if matches:
        first_qname = matches[0].get('qname') or matches[0].get('name')
        if first_qname:
            if is_mcp_mode():
                click.echo(f"\nNext Step: Use the `pm_plan` tool with fqn: '{first_qname}' to analyze its impact.")
            else:
                click.echo(f"\nNext Step: Run `project-map impact --fqn {first_qname}` to analyze its impact.")

@cli.command()
@click.option('--path', required=True, help="Path to the file to inspect")
def context(path: str):
    """Get a dense contextual overview of a specific file."""
    engine = QueryEngine()
    try:
        pid = engine.get_pid_for_path(path)
        if pid is None:
            click.echo(f"Resource: FileContext | Path: {path}")
            click.echo("Status: Not found in project map index.")
            return

        outline = engine.get_file_outline(pid, path)
        deps = engine.get_shallow_dependencies(pid, path)
        
        click.echo(f"Resource: FileContext | Path: {path} | pid: {pid} | LOC: {outline.get('l', 'unknown')}")
        
        # Outline
        click.echo("\n--- File Outline ---")
        classes = outline.get("c", [])
        for c in classes:
            click.echo(f"- class {c['name']} (ln: {c['ln']})")
        
        functions = outline.get("f", [])
        for f in functions:
            click.echo(f"- function {f['name']} (ln: {f['ln']})")
        
        if not classes and not functions:
            click.echo("- (No classes or functions detected)")

        # Impact
        click.echo("\n--- External Impact ---")
        inbound = deps.get("inbound", [])
        if inbound:
            click.echo(f"Inbound Dependencies (Who imports this):")
            # Deduplicate by path
            all_inbound_paths = sorted(list({edge.get("path", "unknown") for edge in inbound}))
            for p in all_inbound_paths[:5]:
                # Find the first edge with this path to get line number
                edge = next(e for e in inbound if e.get("path") == p)
                click.echo(f"- {p} (ln: {edge.get('ln')})")
            
            if len(all_inbound_paths) > 5:
                click.echo(f"... and {len(all_inbound_paths) - 5} more.")
        else:
            click.echo("Inbound Dependencies: [None detected]")

        outbound = deps.get("outbound", [])
        if outbound:
            click.echo(f"\nOutbound Dependencies (What this file imports):")
            # Deduplicate by dst (module name)
            all_outbound_mods = sorted(list({edge.get("dst", "unknown") for edge in outbound}))
            for m in all_outbound_mods[:5]:
                click.echo(f"- {m}")
            
            if len(all_outbound_mods) > 5:
                click.echo(f"... and {len(all_outbound_mods) - 5} more.")
        else:
            click.echo("\nOutbound Dependencies: [None detected]")

    except Exception as e:
        click.echo(f"Error: {e}")

@cli.command()
@click.option('--fqn', required=True, help="Fully Qualified Name of the symbol")
def impact(fqn: str):
    """Analyze the architectural impact of a symbol."""
    engine = QueryEngine()
    try:
        result = engine.analyze_impact(fqn)
    except Exception as e:
        click.echo(f"Error: {e}")
        return

    # TOON formatting
    click.echo(f"Resource: Impact Analysis | Target: {fqn}")
    click.echo(f"Nodes Impacted: {result['impacted_nodes_count']}")
    if result['reached_cap']:
        click.echo("Warning: Fanout cap reached. Impact may be larger.")
    
    if is_mcp_mode():
        click.echo(f"\nNext Step: Use the `pm_status` tool for a workspace overview.")
    else:
        click.echo(f"\nNext Step: Run `project-map status` for workspace overview.")

@cli.command(
    add_help_option=False,
    context_settings=dict(
        ignore_unknown_options=True,
        allow_extra_args=True,
    )
)
@click.pass_context
def build(ctx: click.Context):
    """Build the project map digest (invokes the core generator)."""
    from project_map_cli.core.cli import main as core_main
    import sys
    
    # If the user passed no arguments, the core CLI will print an error because --root and --out-dir are required.
    # If they passed --help, it will be forwarded and the core CLI will print its help.
    sys.exit(core_main(ctx.args))

@cli.command(
    add_help_option=False,
    context_settings=dict(
        ignore_unknown_options=True,
        allow_extra_args=True,
    )
)
@click.pass_context
def refresh(ctx: click.Context):
    """Alias for build. Refresh the project map digest."""
    ctx.invoke(build)

@cli.command()
def status():
    """Returns current workspace context and available commands."""
    engine = QueryEngine()
    click.echo("Workspace: project-map-cli")
    
    try:
        meta = engine.get_metadata()
        status = meta.get("status", "unknown")
        generated_at = meta.get("generated_at", "unknown")
        
        click.echo(f"Phase: {status.capitalize()}")
        click.echo(f"Last Generated: {generated_at}")
        
        if status == "partial":
            errors = meta.get("errors", [])
            click.echo(f"Warnings: {len(errors)} analyzer(s) failed.")
    except Exception:
        click.echo("Phase: Discovery (No index found)")

    if is_mcp_mode():
        click.echo("Available Tools: pm_init, pm_query, pm_plan, pm_status")
        click.echo("\nNext Step: Use the `pm_query` tool with a 'query' to explore.")
    else:
        click.echo("Available Commands: build, refresh, find, impact, status")
        click.echo("\nNext Step: Run `project-map find --query <symbol>` to explore.")

if __name__ == '__main__':
    cli()
