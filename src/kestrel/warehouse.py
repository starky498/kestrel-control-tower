"""Atomic construction of the local DuckDB analytical warehouse."""

from __future__ import annotations

import json
import os
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
    source_sha256: str
    raw_rows: int
    model_rows: dict[str, int]
    quality_results: list[QualityResult]
    started_at_utc: str
    completed_at_utc: str


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
    expected_grains = {
        "fct_order_line": ("order_line_id", "raw_order_lines"),
        "fct_order_service": ("order_id", "raw_orders"),
        "fct_delivery": ("delivery_id", "raw_deliveries"),
        "fct_return_credit_note": ("return_id", "raw_returns_credit_notes"),
        "fct_inventory_snapshot": ("snapshot_id", "raw_inventory_snapshots"),
    }
    counts: dict[str, int] = {}
    for table, (key, source_table) in expected_grains.items():
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
    return counts


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
                warehouse.execute("CHECKPOINT")
            finally:
                warehouse.close()
        os.replace(temporary_path, settings.analytics_db)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise

    return BuildSummary(
        analytics_db=settings.analytics_db,
        source_sha256=fingerprint,
        raw_rows=raw_rows,
        model_rows=model_rows,
        quality_results=quality_results,
        started_at_utc=started_at,
        completed_at_utc=completed_at,
    )
