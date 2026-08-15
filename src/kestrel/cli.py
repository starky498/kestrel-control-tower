"""Command-line entry points for validation, builds, and integrations."""

from __future__ import annotations

import json
import shutil
from collections import Counter
from collections.abc import Callable
from datetime import UTC, date, datetime
from functools import wraps
from inspect import signature
from pathlib import Path
from typing import ParamSpec, TypeVar

import typer

from kestrel.config import ConfigurationError, Settings
from kestrel.contracts import validate_source
from kestrel.ingestion.bazaarpulse import (
    BazaarPulseCollector,
    HttpSiteSource,
    match_listings_to_products,
)
from kestrel.ingestion.context import (
    ContextClient,
    load_holiday_cache,
    load_weather_cache,
)
from kestrel.ingestion.freight import FreightClient, load_last_good_cache
from kestrel.integration_store import (
    load_product_candidates,
    store_bazaarpulse_snapshot,
    store_freight_snapshot,
    store_holiday_snapshot,
    store_weather_snapshot,
)
from kestrel.market_governance import (
    MatchGovernanceError,
    apply_match_decisions,
    load_match_decisions,
)
from kestrel.observability import observe_run
from kestrel.warehouse import build_warehouse

app = typer.Typer(no_args_is_help=True, help="Kestrel control-tower operations.")
P = ParamSpec("P")
R = TypeVar("R")


def _settings() -> Settings:
    return Settings.load()


def _observed_cli(operation: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Wrap a CLI operation in a redacted local run trace without changing its signature."""

    def decorate(command: Callable[P, R]) -> Callable[P, R]:
        @wraps(command)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            settings = _settings()
            bound = signature(command).bind_partial(*args, **kwargs)
            with observe_run(
                settings.runtime_dir,
                operation,
                details={"arguments": dict(bound.arguments)},
            ):
                return command(*args, **kwargs)

        return wrapped

    return decorate


def _validated_cleanup_target(path: Path, runtime: Path) -> Path:
    """Resolve one generated target and reject escapes or symlink indirection."""

    if path.is_symlink():
        raise ConfigurationError(f"Refusing to clean symlinked generated path: {path}")
    resolved = path.resolve()
    if resolved == runtime or not resolved.is_relative_to(runtime):
        raise ConfigurationError(
            f"Refusing to clean path outside the project runtime directory: {resolved}"
        )
    return resolved


@app.command()
@_observed_cli("doctor")
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
        "weather_cache_exists": settings.weather_cache.is_file(),
        "holiday_cache_exists": settings.holiday_cache.is_file(),
        "nlq_semantic_enabled": settings.nlq_semantic_enabled,
        "nlq_model_path": str(settings.nlq_model_path),
        "nlq_model_marker_exists": (
            settings.nlq_model_path / "kestrel-model.json"
        ).is_file(),
        "bazaarpulse_site_root": (
            str(settings.bazaarpulse_site_root) if settings.bazaarpulse_site_root else None
        ),
    }
    typer.echo(json.dumps(checks, indent=2))
    if not settings.source_db.is_file():
        raise typer.Exit(code=1)


@app.command("validate-data")
@_observed_cli("validate_data")
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
@_observed_cli("build_warehouse")
def build() -> None:
    """Build and atomically promote the local DuckDB analytical model."""

    try:
        summary = build_warehouse(_settings())
    except (ConfigurationError, RuntimeError) as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"Built {summary.analytics_db}")
    typer.echo(f"Exported compressed Parquet snapshot to {summary.parquet_dir}")
    typer.echo(f"Raw rows: {summary.raw_rows:,}")
    for table, count in summary.model_rows.items():
        typer.echo(f"{table}: {count:,}")
    typer.echo(f"Source SHA-256: {summary.source_sha256}")


@app.command("scrape-prices")
@_observed_cli("scrape_prices")
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
        snapshot = collector.collect_snapshot()
        listings = list(snapshot.listings)
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
    automatic_matches = match_listings_to_products(
        listings,
        products,
        minimum_confidence=threshold,
    )
    decision_path = settings.project_root / "config" / "competitor_match_decisions.yml"
    try:
        decisions = load_match_decisions(decision_path)
        matches = apply_match_decisions(
            listings,
            automatic_matches,
            products,
            decisions,
            decision_source="config/competitor_match_decisions.yml",
        )
    except MatchGovernanceError as error:
        typer.echo(
            f"Competitor match governance failed; no snapshot was published: {error}",
            err=True,
        )
        raise typer.Exit(code=1) from error
    summary = store_bazaarpulse_snapshot(
        analytics_db,
        listings,
        matches,
        source_price_observations=snapshot.source_price_observations,
        source_detail_failures=snapshot.source_detail_failures,
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
    typer.echo(
        "Source-dated detail-page prices: "
        f"{len(snapshot.source_price_observations):,} observations"
    )
    if snapshot.source_detail_failures:
        typer.echo(
            "Detail-page collection warnings: "
            f"{len(snapshot.source_detail_failures):,} structured failure(s); "
            "see external sync details and the current failure table."
        )


@app.command("sync-freight")
@_observed_cli("sync_freight")
def sync_freight(
    date_from: str = typer.Option(
        "2025-01-01",
        "--from",
        help="Inclusive invoice date (YYYY-MM-DD); default is the full supplied history.",
    ),
    date_to: str = typer.Option(
        "2026-06-30",
        "--to",
        help="Inclusive invoice date (YYYY-MM-DD); default is the full supplied history.",
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


@app.command("sync-context")
@_observed_cli("sync_context")
def sync_context(
    date_from: str = typer.Option(
        "2025-01-01",
        "--from",
        help="Inclusive context date (YYYY-MM-DD).",
    ),
    date_to: str = typer.Option(
        "2026-06-30",
        "--to",
        help="Inclusive context date (YYYY-MM-DD).",
    ),
    offline_cache: bool = typer.Option(
        False,
        "--offline-cache",
        help="Publish any validated context caches without network requests.",
    ),
) -> None:
    """Refresh optional weather and holiday context with source-level isolation."""

    settings = _settings()
    try:
        analytics_db = settings.require_analytics_db()
        parsed_from = date.fromisoformat(date_from)
        parsed_to = date.fromisoformat(date_to)
        if parsed_from > parsed_to:
            raise ValueError("--from cannot be after --to")
    except (ConfigurationError, ValueError) as error:
        typer.echo(f"Invalid context sync configuration: {error}", err=True)
        raise typer.Exit(code=2) from error

    settings.ensure_runtime_dirs()
    failures: list[str] = []
    if offline_cache:
        for label, loader, path, publisher in (
            ("weather", load_weather_cache, settings.weather_cache, store_weather_snapshot),
            ("holidays", load_holiday_cache, settings.holiday_cache, store_holiday_snapshot),
        ):
            try:
                summary = publisher(analytics_db, loader(path))
                typer.echo(
                    f"Published {summary.record_count:,} cached {label} rows; "
                    f"coverage={summary.coverage_start} to {summary.coverage_end}."
                )
            except Exception as error:
                failures.append(f"{label}: {error}")
                typer.echo(f"Skipped {label} cache: {error}", err=True)
    else:
        with ContextClient(
            weather_cache_path=settings.weather_cache,
            holiday_cache_path=settings.holiday_cache,
            weather_url=settings.weather_api_url,
            holiday_url=settings.holiday_api_url,
            holiday_fallback_url=settings.holiday_fallback_url,
        ) as client:
            outcomes = client.sync_all(date_from=parsed_from, date_to=parsed_to)
        for outcome in outcomes:
            label = (
                "weather" if outcome.source_name == "open_meteo_weather" else "holidays"
            )
            if not outcome.complete:
                failures.append(f"{label}: {outcome.error}")
                typer.echo(
                    f"{label.title()} refresh failed; its last-good cache and analytical "
                    f"snapshot were preserved: {outcome.error}",
                    err=True,
                )
                continue
            try:
                if outcome.source_name == "open_meteo_weather":
                    cache = load_weather_cache(outcome.cache_path)
                    summary = store_weather_snapshot(analytics_db, cache)
                else:
                    cache = load_holiday_cache(outcome.cache_path)
                    summary = store_holiday_snapshot(analytics_db, cache)
            except Exception as error:
                failures.append(f"{label}: {error}")
                typer.echo(
                    f"{label.title()} publication failed; its prior analytical snapshot "
                    f"was preserved: {error}",
                    err=True,
                )
                continue
            typer.echo(
                f"Published {summary.record_count:,} {label} rows; "
                f"coverage={summary.coverage_start} to {summary.coverage_end}; "
                f"cache={outcome.cache_path}"
            )
    if failures:
        raise typer.Exit(code=1)


@app.command("clean-generated")
@_observed_cli("clean_generated")
def clean_generated(confirm: bool = typer.Option(False, "--yes")) -> None:
    """Remove only generated runtime files under the configured `.kestrel` directory."""

    settings = _settings()
    configured_runtime = settings.runtime_dir
    expected_runtime = settings.project_root / ".kestrel"
    runtime = configured_runtime.resolve()
    project_runtime = expected_runtime.resolve()
    if (
        configured_runtime.is_symlink()
        or expected_runtime.is_symlink()
        or runtime != project_runtime
    ):
        typer.echo(
            "Refusing to clean a custom or symlinked runtime directory. Remove it manually "
            "after verifying the path.",
            err=True,
        )
        raise typer.Exit(code=2)
    if not confirm:
        typer.echo("Re-run with --yes to remove generated files under .kestrel only.")
        raise typer.Exit(code=2)
    try:
        targets = tuple(
            _validated_cleanup_target(Path(path), runtime)
            for path in (
                settings.analytics_db,
                settings.freight_cache,
                settings.competitor_cache,
                settings.weather_cache,
                settings.holiday_cache,
                runtime / "parquet",
                runtime / "logs",
            )
        )
    except ConfigurationError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    for target in targets:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink(missing_ok=True)
    typer.echo("Removed generated analytical, cache, Parquet, and log files under .kestrel.")


if __name__ == "__main__":
    app()
