"""Command-line entry points for validation, builds, and integrations."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from kestrel.config import ConfigurationError, Settings
from kestrel.contracts import validate_source
from kestrel.warehouse import build_warehouse

app = typer.Typer(no_args_is_help=True, help="Kestrel control-tower operations.")


def _settings() -> Settings:
    return Settings.load()


@app.command()
def doctor() -> None:
    """Check paths and report whether the project is ready to build and run."""

    settings = _settings()
    checks = {
        "project_root": str(settings.project_root),
        "source_db": str(settings.source_db),
        "source_db_exists": settings.source_db.is_file(),
        "analytics_db": str(settings.analytics_db),
        "analytics_db_exists": settings.analytics_db.is_file(),
        "freight_api_url": settings.freight_api_url,
        "freight_api_key_configured": bool(settings.freight_api_key),
        "bazaarpulse_site_root": (
            str(settings.bazaarpulse_site_root) if settings.bazaarpulse_site_root else None
        ),
    }
    typer.echo(json.dumps(checks, indent=2))
    if not settings.source_db.is_file():
        raise typer.Exit(code=1)


@app.command("validate-data")
def validate_data(json_output: bool = typer.Option(False, "--json")) -> None:
    """Run schema, grain, relationship, parity, and known-conflict checks."""

    settings = _settings()
    try:
        source = settings.require_source_db()
    except ConfigurationError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    results = validate_source(source)
    if json_output:
        typer.echo(json.dumps([result.to_dict() for result in results], indent=2))
    else:
        for result in results:
            marker = "PASS" if result.passed else result.severity
            typer.echo(f"[{marker:<8}] {result.check_id}: {result.observed_value}")
        typer.echo(f"\n{len(results)} checks completed.")
    if any(result.severity == "BLOCKING" and not result.passed for result in results):
        raise typer.Exit(code=1)


@app.command()
def build() -> None:
    """Build and atomically promote the local DuckDB analytical model."""

    try:
        summary = build_warehouse(_settings())
    except (ConfigurationError, RuntimeError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"Built {summary.analytics_db}")
    typer.echo(f"Raw rows: {summary.raw_rows:,}")
    for table, count in summary.model_rows.items():
        typer.echo(f"{table}: {count:,}")
    typer.echo(f"Source SHA-256: {summary.source_sha256}")


@app.command("clean-generated")
def clean_generated(confirm: bool = typer.Option(False, "--yes")) -> None:
    """Remove only generated runtime files under the configured `.kestrel` directory."""

    settings = _settings()
    runtime = settings.runtime_dir.resolve()
    project_runtime = (settings.project_root / ".kestrel").resolve()
    if runtime != project_runtime:
        typer.echo(
            "Refusing to clean a custom runtime directory. "
            "Remove it manually after verifying the path.",
            err=True,
        )
        raise typer.Exit(code=2)
    if not confirm:
        typer.echo("Re-run with --yes to remove generated files under .kestrel only.")
        raise typer.Exit(code=2)
    for path in (
        settings.analytics_db,
        settings.freight_cache,
        settings.competitor_cache,
    ):
        Path(path).unlink(missing_ok=True)
    typer.echo("Removed generated analytical and integration cache files.")


if __name__ == "__main__":
    app()
