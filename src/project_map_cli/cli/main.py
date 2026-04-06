import click
from project_map_cli.core.query_engine import QueryEngine

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
            click.echo(f"\nNext Step: Run `sc_exec impact --fqn {first_qname}` to analyze its impact.")

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
    
    click.echo(f"\nNext Step: Run `sc_exec status` for workspace overview.")

if __name__ == '__main__':
    cli()
