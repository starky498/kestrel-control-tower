"""Atomic construction of the local DuckDB analytical warehouse."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pandas as pd

from kestrel.config import Settings
from kestrel.contracts import CONTRACTS, QualityResult, source_fingerprint, validate_source


@dataclass(frozen=True)
class BuildSummary:
    analytics_db: Path
    parquet_dir: Path
    source_sha256: str
    raw_rows: int
    model_rows: dict[str, int]
    quality_results: list[QualityResult]
    started_at_utc: str
    completed_at_utc: str


@dataclass(frozen=True)
class ExternalPreservationSummary:
    """External objects copied from the prior warehouse into a staged rebuild."""

    tables: tuple[str, ...]
    views: tuple[str, ...]
    skipped_views: tuple[str, ...]


PARQUET_EXPORT_TABLES = (
    "dim_region",
    "dim_warehouse",
    "dim_route",
    "dim_salesperson",
    "dim_outlet",
    "dim_product",
    "dim_date",
    "fct_order_line",
    "fct_order_service",
    "fct_delivery",
    "fct_return_credit_note",
    "fct_inventory_snapshot",
    "data_quality_results",
    "source_snapshots",
    "pipeline_runs",
)


def _ingest_sqlite_table(
    source: sqlite3.Connection,
    warehouse: duckdb.DuckDBPyConnection,
    table: str,
    *,
    chunk_size: int = 100_000,
) -> int:
    raw_table = f"raw_{table}"
    total = 0
    first = True
    for frame in pd.read_sql_query(f'SELECT * FROM "{table}"', source, chunksize=chunk_size):
        warehouse.register("_source_chunk", frame)
        if first:
            warehouse.execute(f'CREATE TABLE "{raw_table}" AS SELECT * FROM _source_chunk')
            first = False
        else:
            warehouse.execute(f'INSERT INTO "{raw_table}" SELECT * FROM _source_chunk')
        warehouse.unregister("_source_chunk")
        total += len(frame)
    if first:
        warehouse.execute(f'CREATE TABLE "{raw_table}" AS SELECT NULL WHERE FALSE')
    return total


def _execute_sql_file(connection: duckdb.DuckDBPyConnection, sql_path: Path) -> None:
    connection.execute(sql_path.read_text(encoding="utf-8"))


def _persist_quality_results(
    connection: duckdb.DuckDBPyConnection, results: list[QualityResult]
) -> None:
    frame = pd.DataFrame([result.to_dict() for result in results])
    connection.register("_quality_results", frame)
    connection.execute(
        """
        CREATE TABLE data_quality_results AS
        SELECT *, current_timestamp AS recorded_at_utc
        FROM _quality_results
        """
    )
    connection.unregister("_quality_results")


def _assert_models(connection: duckdb.DuckDBPyConnection) -> dict[str, int]:
    expected_fact_grains = {
        "fct_order_line": ("order_line_id", "raw_order_lines"),
        "fct_order_service": ("order_id", "raw_orders"),
        "fct_delivery": ("delivery_id", "raw_deliveries"),
        "fct_return_credit_note": ("return_id", "raw_returns_credit_notes"),
        "fct_inventory_snapshot": ("snapshot_id", "raw_inventory_snapshots"),
    }
    counts: dict[str, int] = {}
    for table, (key, source_table) in expected_fact_grains.items():
        row_count, distinct_keys = connection.execute(
            f'SELECT COUNT(*), COUNT(DISTINCT "{key}") FROM "{table}"'
        ).fetchone()
        if row_count != distinct_keys:
            raise RuntimeError(
                f"Blocking grain violation: {table} has {row_count} rows but "
                f"{distinct_keys} distinct {key} values."
            )
        source_count = connection.execute(f'SELECT COUNT(*) FROM "{source_table}"').fetchone()[0]
        if row_count != source_count:
            raise RuntimeError(
                f"Blocking reconciliation failure: {table} has {row_count} rows; "
                f"its source {source_table} has {source_count}."
            )
        counts[table] = row_count

    expected_dimension_grains = {
        "dim_region": ("region_id", "raw_regions"),
        "dim_warehouse": ("warehouse_id", "raw_warehouses"),
        "dim_route": ("route_id", "raw_routes"),
        "dim_salesperson": ("salesperson_id", "raw_salespeople"),
        "dim_outlet": ("outlet_id", "raw_outlets"),
        "dim_product": ("product_id", "raw_products"),
    }
    for table, (key, source_table) in expected_dimension_grains.items():
        row_count, distinct_keys = connection.execute(
            f'SELECT COUNT(*), COUNT(DISTINCT "{key}") FROM "{table}"'
        ).fetchone()
        source_count = connection.execute(f'SELECT COUNT(*) FROM "{source_table}"').fetchone()[0]
        if row_count != distinct_keys or row_count != source_count:
            raise RuntimeError(
                f"Blocking dimension reconciliation failure: {table} has {row_count} rows, "
                f"{distinct_keys} distinct {key} values, and {source_table} has "
                f"{source_count} rows."
            )
        counts[table] = row_count

    date_rows, distinct_dates = connection.execute(
        "SELECT count(*), count(DISTINCT calendar_date) FROM dim_date"
    ).fetchone()
    if date_rows == 0 or date_rows != distinct_dates:
        raise RuntimeError(
            f"Blocking date dimension grain violation: {date_rows} rows and "
            f"{distinct_dates} distinct dates."
        )
    counts["dim_date"] = date_rows

    eligible, in_full, otif = connection.execute(
        """
        SELECT COUNT(*) FILTER (WHERE is_eligible_service),
               COUNT(*) FILTER (WHERE is_eligible_service AND strict_in_full),
               COUNT(*) FILTER (WHERE is_eligible_service AND strict_otif)
        FROM fct_order_service
        """
    ).fetchone()
    if eligible == 0 or in_full != 0 or otif != 0:
        raise RuntimeError(
            "Strict OTIF control failed: supplied data must retain a non-empty eligible "
            "population with zero strict in-full and OTIF orders."
        )

    timestamp_status_violations = connection.execute(
        """
        SELECT count(*)
        FROM fct_order_service
        WHERE created_at_parse_status IS NULL
           OR (created_at_parse_status LIKE 'PARSED_%' AND created_at_ist IS NULL)
           OR (created_at_parse_status NOT LIKE 'PARSED_%' AND created_at_ist IS NOT NULL)
        """
    ).fetchone()[0]
    if timestamp_status_violations:
        raise RuntimeError(
            "Order created_at normalization control failed: parse status and normalized "
            f"Asia/Kolkata timestamp disagree on {timestamp_status_violations} orders."
        )
    return counts


def _sql_literal(path: Path) -> str:
    return str(path).replace("'", "''")


def _quoted_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _restorable_external_name(value: str) -> bool:
    return value == "external_sync_runs" or value.startswith(("ext_", "vw_"))


def _preserve_external_objects(
    warehouse: duckdb.DuckDBPyConnection,
    prior_warehouse_path: Path,
) -> ExternalPreservationSummary:
    """Copy external physical state and rebuild compatible views in a staged warehouse.

    Physical tables are copied before any catalog view SQL is evaluated. View definitions are
    retried to accommodate dependencies between ``vw_`` objects; definitions whose dependencies
    do not exist in the rebuilt warehouse are explicitly skipped. Any failure to attach or copy a
    physical table propagates, so the caller cannot promote a partial staged warehouse.
    """

    if not prior_warehouse_path.is_file():
        return ExternalPreservationSummary((), (), ())

    catalog_alias = "previous_external"
    warehouse.execute(
        f"ATTACH '{_sql_literal(prior_warehouse_path)}' AS {catalog_alias} (READ_ONLY)"
    )
    copied_tables: list[str] = []
    view_definitions: list[tuple[str, str]] = []
    try:
        table_names = [
            str(row[0])
            for row in warehouse.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_catalog = ?
                  AND table_schema = 'main'
                  AND table_type = 'BASE TABLE'
                  AND (table_name = 'external_sync_runs' OR table_name LIKE 'ext\\_%' ESCAPE '\\')
                ORDER BY table_name
                """,
                [catalog_alias],
            ).fetchall()
        ]
        for table_name in table_names:
            if not _restorable_external_name(table_name):
                continue
            quoted = _quoted_identifier(table_name)
            warehouse.execute(
                f"CREATE TABLE {quoted} AS "
                f"SELECT * FROM {catalog_alias}.main.{quoted}"
            )
            copied_tables.append(table_name)

        view_definitions = [
            (str(row[0]), str(row[1]))
            for row in warehouse.execute(
                """
                SELECT table_name, view_definition
                FROM information_schema.views
                WHERE table_catalog = ?
                  AND table_schema = 'main'
                  AND (table_name LIKE 'ext\\_%' ESCAPE '\\'
                       OR table_name LIKE 'vw\\_%' ESCAPE '\\')
                ORDER BY table_name
                """,
                [catalog_alias],
            ).fetchall()
            if row[1]
        ]

        pending = list(view_definitions)
        restored_views: list[str] = []
        while pending:
            retry: list[tuple[str, str]] = []
            restored_this_pass = 0
            for view_name, definition in pending:
                create_sql = re.sub(
                    r"^\s*CREATE\s+VIEW\s+",
                    "CREATE OR REPLACE VIEW ",
                    definition,
                    count=1,
                    flags=re.IGNORECASE,
                )
                try:
                    warehouse.execute(create_sql)
                except duckdb.Error:
                    retry.append((view_name, definition))
                else:
                    restored_views.append(view_name)
                    restored_this_pass += 1
            if restored_this_pass == 0:
                pending = retry
                break
            pending = retry
    finally:
        warehouse.execute(f"DETACH {catalog_alias}")

    valid_views: list[str] = []
    invalid_after_detach: list[str] = []
    for view_name in restored_views:
        try:
            warehouse.execute(f"SELECT * FROM {_quoted_identifier(view_name)} LIMIT 0")
        except duckdb.Error:
            warehouse.execute(f"DROP VIEW IF EXISTS {_quoted_identifier(view_name)}")
            invalid_after_detach.append(view_name)
        else:
            valid_views.append(view_name)
    skipped = sorted({name for name, _ in pending} | set(invalid_after_detach))
    return ExternalPreservationSummary(
        tuple(copied_tables),
        tuple(valid_views),
        tuple(skipped),
    )


def _export_parquet_models(
    connection: duckdb.DuckDBPyConnection,
    destination: Path,
    *,
    source_sha256: str,
    completed_at_utc: str,
) -> dict[str, int]:
    """Export governed models plus a fingerprinted manifest to a staging directory."""

    destination.mkdir(parents=True, exist_ok=False)
    counts: dict[str, int] = {}
    for table in PARQUET_EXPORT_TABLES:
        exists = connection.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [table]
        ).fetchone()[0]
        if not exists:
            raise RuntimeError(f"Required Parquet export model is missing: {table}")
        counts[table] = int(connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0])
        output = destination / f"{table}.parquet"
        connection.execute(
            f"COPY (SELECT * FROM \"{table}\") TO '{_sql_literal(output)}' "
            "(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
    manifest = {
        "schema_version": 1,
        "source_sha256": source_sha256,
        "completed_at_utc": completed_at_utc,
        "tables": counts,
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return counts


def _stage_directory_promotion(staged: Path, destination: Path) -> Path | None:
    """Promote a generated directory while retaining a rollback copy until DB promotion."""

    backup: Path | None = None
    if destination.exists():
        backup = destination.with_name(f"{destination.name}.backup-{uuid.uuid4().hex}")
        os.replace(destination, backup)
    try:
        os.replace(staged, destination)
    except Exception:
        if backup is not None:
            os.replace(backup, destination)
        raise
    return backup


def _rollback_directory_promotion(destination: Path, backup: Path | None) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    if backup is not None and backup.exists():
        os.replace(backup, destination)


def build_warehouse(settings: Settings) -> BuildSummary:
    """Build every analytical model into a temporary DuckDB file, then promote atomically."""

    source_path = settings.require_source_db()
    settings.ensure_runtime_dirs()
    started_at = datetime.now(UTC).isoformat()
    fingerprint = source_fingerprint(source_path)
    quality_results = validate_source(source_path)
    blocking_failures = [
        result for result in quality_results if result.severity == "BLOCKING" and not result.passed
    ]
    if blocking_failures:
        detail = "; ".join(f"{item.check_id}: {item.message}" for item in blocking_failures)
        raise RuntimeError(f"Source failed blocking quality gates: {detail}")

    temporary_path = settings.analytics_db.with_name(
        f"{settings.analytics_db.name}.tmp-{uuid.uuid4().hex}"
    )
    parquet_dir = settings.runtime_dir / "parquet"
    temporary_parquet_dir = settings.runtime_dir / f"parquet.tmp-{uuid.uuid4().hex}"
    parquet_backup: Path | None = None
    raw_rows = 0
    try:
        with sqlite3.connect(f"file:{source_path}?mode=ro", uri=True) as source:
            warehouse = duckdb.connect(str(temporary_path))
            try:
                warehouse.execute("SET TimeZone='Asia/Kolkata'")
                for table in CONTRACTS:
                    raw_rows += _ingest_sqlite_table(source, warehouse, table)
                _persist_quality_results(warehouse, quality_results)
                _execute_sql_file(warehouse, settings.project_root / "sql" / "10_dimensions.sql")
                _execute_sql_file(warehouse, settings.project_root / "sql" / "20_facts.sql")
                model_rows = _assert_models(warehouse)
                completed_at = datetime.now(UTC).isoformat()
                warehouse.execute(
                    """
                    CREATE TABLE source_snapshots (
                        source_name VARCHAR,
                        source_path VARCHAR,
                        source_sha256 VARCHAR,
                        row_count BIGINT,
                        captured_at_utc TIMESTAMPTZ
                    )
                    """
                )
                warehouse.execute(
                    "INSERT INTO source_snapshots VALUES (?, ?, ?, ?, ?)",
                    ["kestrel_ops.db", str(source_path), fingerprint, raw_rows, completed_at],
                )
                warehouse.execute(
                    """
                    CREATE TABLE pipeline_runs (
                        run_id VARCHAR,
                        started_at_utc TIMESTAMPTZ,
                        completed_at_utc TIMESTAMPTZ,
                        status VARCHAR,
                        source_sha256 VARCHAR,
                        model_row_counts_json VARCHAR
                    )
                    """
                )
                warehouse.execute(
                    "INSERT INTO pipeline_runs VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        uuid.uuid4().hex,
                        started_at,
                        completed_at,
                        "SUCCEEDED",
                        fingerprint,
                        json.dumps(model_rows, sort_keys=True),
                    ],
                )
                _preserve_external_objects(warehouse, settings.analytics_db)
                _export_parquet_models(
                    warehouse,
                    temporary_parquet_dir,
                    source_sha256=fingerprint,
                    completed_at_utc=completed_at,
                )
                warehouse.execute("CHECKPOINT")
            finally:
                warehouse.close()
        parquet_backup = _stage_directory_promotion(temporary_parquet_dir, parquet_dir)
        try:
            os.replace(temporary_path, settings.analytics_db)
        except Exception:
            _rollback_directory_promotion(parquet_dir, parquet_backup)
            raise
        if parquet_backup is not None:
            shutil.rmtree(parquet_backup)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        if temporary_parquet_dir.exists():
            shutil.rmtree(temporary_parquet_dir)
        raise

    return BuildSummary(
        analytics_db=settings.analytics_db,
        parquet_dir=parquet_dir,
        source_sha256=fingerprint,
        raw_rows=raw_rows,
        model_rows=model_rows,
        quality_results=quality_results,
        started_at_utc=started_at,
        completed_at_utc=completed_at,
    )
