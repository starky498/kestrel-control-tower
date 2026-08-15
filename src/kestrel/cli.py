"""Command-line entry points for validation, builds, and integrations."""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path

import typer

from kestrel.config import ConfigurationError, Settings
from kestrel.contracts import validate_source
from kestrel.ingestion.bazaarpulse import (
    BazaarPulseCollector,
    HttpSiteSource,
    match_listings_to_products,
)
from kestrel.ingestion.freight import FreightClient, load_last_good_cache
from kestrel.integration_store import (
    load_product_candidates,
    store_bazaarpulse_snapshot,
    store_freight_snapshot,
)
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


@app.command("scrape-prices")
def scrape_prices(
    use_http: bool = typer.Option(
        False,
        "--http",
        help="Use the configured local HTTP server even when the unpacked site is available.",
    ),
) -> None:
    """Collect allowed BazaarPulse pages, match conservatively, and publish a snapshot."""

    settings = _settings()
    try:
        analytics_db = settings.require_analytics_db()
    except ConfigurationError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error

    settings.ensure_runtime_dirs()
    started = datetime.now(UTC)
    if settings.bazaarpulse_site_root is not None and not use_http:
        collector = BazaarPulseCollector.from_local(
            settings.bazaarpulse_site_root,
            cache_path=settings.competitor_cache,
        )
        source_description = str(settings.bazaarpulse_site_root)
    else:
        collector = BazaarPulseCollector.from_http(
            settings.bazaarpulse_base_url,
            cache_path=settings.competitor_cache,
        )
        source_description = settings.bazaarpulse_base_url

    try:
        listings = collector.collect()
    except Exception as error:
        typer.echo(
            f"BazaarPulse refresh failed; the last-good cache and warehouse snapshot were "
            f"preserved: {error}",
            err=True,
        )
        raise typer.Exit(code=1) from error
    finally:
        if isinstance(collector.source, HttpSiteSource):
            collector.source.close()

    products = load_product_candidates(analytics_db)
    threshold = settings.competitor_match_threshold / 100
    matches = match_listings_to_products(
        listings,
        products,
        minimum_confidence=threshold,
    )
    summary = store_bazaarpulse_snapshot(
        analytics_db,
        listings,
        matches,
        started_at_utc=started,
    )
    statuses = Counter(match.status for match in matches)
    typer.echo(f"Collected {summary.record_count:,} unique listings from {source_description}")
    typer.echo(
        "Match outcomes: "
        + ", ".join(f"{status}={count:,}" for status, count in sorted(statuses.items()))
    )
    typer.echo(
        f"Observation coverage: {summary.coverage_start} to {summary.coverage_end}; "
        f"cache={settings.competitor_cache}"
    )


@app.command("sync-freight")
def sync_freight(
    date_from: str = typer.Option(
        "2026-04-01",
        "--from",
        help="Inclusive invoice date (YYYY-MM-DD); defaults to FY 2026-27 Q1.",
    ),
    date_to: str = typer.Option(
        "2026-06-30",
        "--to",
        help="Inclusive invoice date (YYYY-MM-DD); defaults to FY 2026-27 Q1.",
    ),
    resume: bool = typer.Option(
        True,
        "--resume/--no-resume",
        help="Resume a compatible interrupted cursor walk.",
    ),
    offline_cache: bool = typer.Option(
        False,
        "--offline-cache",
        help="Publish the validated last-good cache without calling the API.",
    ),
) -> None:
    """Synchronize carrier invoices with retry, resume, and last-good protection."""

    settings = _settings()
    try:
        analytics_db = settings.require_analytics_db()
        parsed_from = date.fromisoformat(date_from)
        parsed_to = date.fromisoformat(date_to)
    except (ConfigurationError, ValueError) as error:
        typer.echo(f"Invalid freight sync configuration: {error}", err=True)
        raise typer.Exit(code=2) from error
    if offline_cache:
        try:
            cache = load_last_good_cache(settings.freight_cache)
            summary = store_freight_snapshot(analytics_db, cache.invoices, cache.metadata)
        except Exception as error:
            typer.echo(f"Could not publish freight cache: {error}", err=True)
            raise typer.Exit(code=1) from error
        typer.echo(
            f"Published {summary.record_count:,} validated cached invoices; last successful "
            f"sync={summary.completed_at_utc.isoformat()}."
        )
        return

    if not settings.freight_api_key:
        typer.echo(
            "KESTREL_FREIGHT_API_KEY is not configured. Copy the supplied mock-server key "
            "into your local .env; never commit it.",
            err=True,
        )
        raise typer.Exit(code=2)

    settings.ensure_runtime_dirs()
    with FreightClient(
        base_url=settings.freight_api_url,
        api_key=settings.freight_api_key,
        cache_path=settings.freight_cache,
    ) as client:
        result = client.sync(
            date_from=parsed_from,
            date_to=parsed_to,
            resume=resume,
        )

    if not result.complete:
        typer.echo(
            f"Freight sync incomplete after {result.metadata.page_count:,} pages and "
            f"{result.metadata.retry_count:,} retries: {result.metadata.error}",
            err=True,
        )
        if result.last_good_available:
            cache = load_last_good_cache(settings.freight_cache)
            stale = store_freight_snapshot(analytics_db, cache.invoices, cache.metadata)
            typer.echo(
                "Cursor checkpoint saved. Published the validated last-good snapshot from "
                f"{stale.completed_at_utc.isoformat()}; it is stale and labeled accordingly.",
                err=True,
            )
        else:
            typer.echo(
                "Cursor checkpoint saved; no complete cache is available. The previous "
                "analytical snapshot, if any, was preserved.",
                err=True,
            )
        raise typer.Exit(code=1)

    summary = store_freight_snapshot(analytics_db, result.invoices, result.metadata)
    typer.echo(
        f"Published {summary.record_count:,} freight invoices after "
        f"{result.metadata.page_count:,} pages, {result.metadata.request_count:,} requests, "
        f"and {result.metadata.retry_count:,} retries."
    )
    typer.echo(
        f"Service-date coverage: {summary.coverage_start} to {summary.coverage_end}; "
        f"cache={settings.freight_cache}"
    )


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
