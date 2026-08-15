from __future__ import annotations

import json
import os
from pathlib import Path

import duckdb
import pytest

from kestrel.warehouse import (
    PARQUET_EXPORT_TABLES,
    _export_parquet_models,
    _preserve_external_objects,
    _rollback_directory_promotion,
    _stage_directory_promotion,
)


def test_parquet_export_is_complete_and_fingerprinted(tmp_path: Path) -> None:
    destination = tmp_path / "parquet-staged"
    with duckdb.connect() as connection:
        for table in PARQUET_EXPORT_TABLES:
            connection.execute(f'CREATE TABLE "{table}" AS SELECT 1 AS row_id')

        counts = _export_parquet_models(
            connection,
            destination,
            source_sha256="abc123",
            completed_at_utc="2026-08-15T00:00:00+00:00",
        )

    assert counts == {table: 1 for table in PARQUET_EXPORT_TABLES}
    manifest = json.loads((destination / "manifest.json").read_text())
    assert manifest["schema_version"] == 1
    assert manifest["source_sha256"] == "abc123"
    assert manifest["tables"] == counts
    with duckdb.connect() as connection:
        exported = connection.execute(
            "SELECT row_id FROM read_parquet(?)",
            [str(destination / "fct_order_line.parquet")],
        ).fetchone()
    assert exported == (1,)


def test_parquet_directory_promotion_can_be_rolled_back(tmp_path: Path) -> None:
    destination = tmp_path / "parquet"
    destination.mkdir()
    (destination / "manifest.json").write_text("old")
    staged = tmp_path / "parquet-staged"
    staged.mkdir()
    (staged / "manifest.json").write_text("new")

    backup = _stage_directory_promotion(staged, destination)

    assert (destination / "manifest.json").read_text() == "new"
    assert backup is not None and backup.exists()
    _rollback_directory_promotion(destination, backup)
    assert (destination / "manifest.json").read_text() == "old"


def test_external_market_freight_and_context_survive_staged_rebuild(
    tmp_path: Path,
) -> None:
    analytics = tmp_path / "analytics.duckdb"
    staged = tmp_path / "analytics.staged.duckdb"
    with duckdb.connect(str(analytics)) as previous:
        previous.execute(
            """
            CREATE TABLE external_sync_runs AS
            SELECT 'market-1'::VARCHAR AS sync_id, 'bazaarpulse'::VARCHAR AS source_name;

            CREATE TABLE ext_bazaarpulse_listing_history AS
            SELECT * FROM (VALUES
                ('obs-1', '101', 69.0),
                ('obs-2', '101', 68.0)
            ) history(observation_id, listing_id, current_price_inr);
            CREATE TABLE ext_bazaarpulse_match_history AS
            SELECT * FROM (VALUES
                ('obs-1', 'automatic_high_confidence'),
                ('obs-2', 'manual_match')
            ) history(observation_id, provenance);
            CREATE TABLE ext_bazaarpulse_source_price_observation AS
            SELECT 'source-101-2026-06-20'::VARCHAR AS source_price_observation_id,
                   '101'::VARCHAR AS listing_id, DATE '2026-06-20' AS observed_on,
                   67.0::DOUBLE AS price_inr;
            CREATE TABLE ext_freight_invoice_current AS
            SELECT 'FI-1'::VARCHAR AS invoice_id, 125.50::DOUBLE AS amount_inr;
            CREATE TABLE ext_weather_daily_current AS
            SELECT DATE '2026-06-30' AS observation_date, 37.2::DOUBLE AS max_c;
            CREATE TABLE ext_india_holiday_current AS
            SELECT DATE '2026-01-26' AS holiday_date, 'Republic Day'::VARCHAR AS name;

            CREATE VIEW vw_competitor_price_history AS
            SELECT listing.observation_id, listing.listing_id,
                   listing.current_price_inr, match.provenance
            FROM ext_bazaarpulse_listing_history listing
            JOIN ext_bazaarpulse_match_history match USING (observation_id);
            CREATE VIEW vw_market_latest AS
            SELECT * FROM vw_competitor_price_history
            QUALIFY row_number() OVER (
                PARTITION BY listing_id ORDER BY observation_id DESC
            ) = 1;
            CREATE VIEW vw_competitor_source_price_history AS
            SELECT source_price_observation_id, listing_id, observed_on, price_inr
            FROM ext_bazaarpulse_source_price_observation;

            CREATE TABLE old_core_only AS SELECT 1 AS value;
            CREATE VIEW vw_incompatible_legacy AS SELECT * FROM old_core_only;
            """
        )
    with duckdb.connect(str(staged)) as warehouse:
        warehouse.execute("CREATE TABLE new_core_model AS SELECT 2 AS value")
        summary = _preserve_external_objects(warehouse, analytics)

    assert set(summary.tables) == {
        "external_sync_runs",
        "ext_bazaarpulse_listing_history",
        "ext_bazaarpulse_match_history",
        "ext_bazaarpulse_source_price_observation",
        "ext_freight_invoice_current",
        "ext_india_holiday_current",
        "ext_weather_daily_current",
    }
    assert set(summary.views) == {
        "vw_competitor_price_history",
        "vw_competitor_source_price_history",
        "vw_market_latest",
    }
    assert summary.skipped_views == ("vw_incompatible_legacy",)

    os.replace(staged, analytics)
    with duckdb.connect(str(analytics), read_only=True) as rebuilt:
        state = rebuilt.execute(
            """
            SELECT
                (SELECT count(*) FROM ext_bazaarpulse_listing_history),
                (SELECT count(*) FROM ext_bazaarpulse_match_history),
                (SELECT count(*) FROM vw_competitor_price_history),
                (SELECT count(*) FROM vw_competitor_source_price_history),
                (SELECT provenance FROM vw_market_latest),
                (SELECT count(*) FROM ext_freight_invoice_current),
                (SELECT count(*) FROM ext_weather_daily_current),
                (SELECT count(*) FROM ext_india_holiday_current),
                (SELECT count(*) FROM external_sync_runs),
                (SELECT value FROM new_core_model)
            """
        ).fetchone()
        old_core_exists = rebuilt.execute(
            """
            SELECT count(*) FROM information_schema.tables
            WHERE table_name = 'old_core_only'
            """
        ).fetchone()[0]
    assert state == (2, 2, 2, 1, "manual_match", 1, 1, 1, 1, 2)
    assert old_core_exists == 0


def test_external_preservation_is_noop_without_prior_warehouse(tmp_path: Path) -> None:
    with duckdb.connect() as warehouse:
        summary = _preserve_external_objects(warehouse, tmp_path / "missing.duckdb")

    assert summary.tables == ()
    assert summary.views == ()
    assert summary.skipped_views == ()


def test_external_table_copy_failure_does_not_change_prior_warehouse(
    tmp_path: Path,
) -> None:
    analytics = tmp_path / "analytics.duckdb"
    with duckdb.connect(str(analytics)) as previous:
        previous.execute(
            "CREATE TABLE ext_freight_invoice_current AS SELECT 'old' AS invoice_id"
        )
    with duckdb.connect() as staged:
        staged.execute(
            "CREATE TABLE ext_freight_invoice_current AS SELECT 'staged' AS invoice_id"
        )
        with pytest.raises(duckdb.Error):
            _preserve_external_objects(staged, analytics)
        assert staged.execute(
            "SELECT invoice_id FROM ext_freight_invoice_current"
        ).fetchone() == ("staged",)

    with duckdb.connect(str(analytics), read_only=True) as previous:
        assert previous.execute(
            "SELECT invoice_id FROM ext_freight_invoice_current"
        ).fetchone() == ("old",)
