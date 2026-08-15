"""Persistence for external observations at their defensible native grains.

The analytical database is rebuilt atomically from the supplied operational source. External
syncs publish replaceable current tables and append-only observation history in one transaction.
This keeps a failed refresh from destroying either the last queryable snapshot or its audit trail.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from kestrel.ingestion.bazaarpulse import (
    Listing,
    ProductCandidate,
    ProductMatch,
    SourceDetailFailure,
    SourcePriceObservation,
)
from kestrel.ingestion.context import HolidayCache, WeatherCache
from kestrel.ingestion.freight import FreightInvoice, FreightSyncMetadata


@dataclass(frozen=True)
class IntegrationStoreSummary:
    """Outcome of publishing one external-source snapshot."""

    source_name: str
    record_count: int
    matched_count: int | None
    coverage_start: date | None
    coverage_end: date | None
    completed_at_utc: datetime


def load_product_candidates(database_path: Path) -> list[ProductCandidate]:
    """Load the minimal governed product-master projection used for entity resolution."""

    with duckdb.connect(str(database_path), read_only=True) as connection:
        rows = connection.execute(
            """
            SELECT product_id, sku_code, product_name, brand, category,
                   pack_size_value, pack_size_uom
            FROM dim_product
            ORDER BY product_id
            """
        ).fetchall()
    return [ProductCandidate(*row) for row in rows]


def _ensure_sync_table(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS external_sync_runs (
            sync_id VARCHAR PRIMARY KEY,
            source_name VARCHAR NOT NULL,
            status VARCHAR NOT NULL,
            record_count BIGINT NOT NULL,
            is_complete BOOLEAN NOT NULL,
            coverage_start DATE,
            coverage_end DATE,
            started_at_utc TIMESTAMPTZ NOT NULL,
            completed_at_utc TIMESTAMPTZ NOT NULL,
            details_json VARCHAR
        )
        """
    )


def _register_frame(
    connection: duckdb.DuckDBPyConnection, name: str, records: list[dict[str, Any]]
) -> None:
    frame = pd.DataFrame.from_records(records)
    connection.register(name, frame)


def _table_exists(connection: duckdb.DuckDBPyConnection, table_name: str) -> bool:
    return bool(
        connection.execute(
            """
            SELECT count(*) FROM information_schema.tables
            WHERE table_schema = 'main' AND table_name = ?
            """,
            [table_name],
        ).fetchone()[0]
    )


def _table_columns(
    connection: duckdb.DuckDBPyConnection, table_name: str
) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'main' AND table_name = ?
            """,
            [table_name],
        ).fetchall()
    }


def _legacy_history_expression(column: str, available: set[str]) -> str:
    """Map the former current-only schema into the new append-only history schema."""

    quoted = f'"{column}"'
    if column in available:
        return quoted
    legacy_sync = (
        "'legacy-bazaarpulse-' || "
        "coalesce(cast(collected_at_utc AS VARCHAR), 'unknown')"
    )
    if column == "sync_id":
        return legacy_sync
    if column == "observation_id":
        return f"{legacy_sync} || ':' || cast(listing_id AS VARCHAR)"
    if column == "suggested_product_id" and "product_id" in available:
        return "product_id"
    if column == "suggested_sku_code" and "sku_code" in available:
        return "sku_code"
    if column == "provenance" and "status" in available:
        return (
            "CASE WHEN status = 'matched' THEN 'automatic_high_confidence' "
            "ELSE 'automatic_quarantine' END"
        )
    if column == "algorithm_status" and "status" in available:
        return "status"
    if column == "algorithm_reason" and "reason" in available:
        return "reason"
    return "NULL"


def _seed_bazaarpulse_history_from_legacy_current(
    connection: duckdb.DuckDBPyConnection,
    history_table: str,
    current_table: str,
) -> None:
    """Preserve a pre-migration current snapshot once when history is first introduced."""

    if not _table_exists(connection, current_table):
        return
    available = _table_columns(connection, current_table)
    if not {"listing_id", "collected_at_utc"}.issubset(available):
        return
    target_columns = [
        str(row[0])
        for row in connection.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'main' AND table_name = ?
            ORDER BY ordinal_position
            """,
            [history_table],
        ).fetchall()
    ]
    projection = ", ".join(
        f"{_legacy_history_expression(column, available)} AS \"{column}\""
        for column in target_columns
    )
    connection.execute(
        f"INSERT INTO {history_table} BY NAME SELECT {projection} FROM {current_table}"
    )


def _assert_history_replay_is_identical(
    connection: duckdb.DuckDBPyConnection,
    incoming_relation: str,
    history_table: str,
) -> None:
    """Allow exact retry replay while refusing an observation-ID payload collision."""

    incoming_count = int(
        connection.execute(f"SELECT count(*) FROM {incoming_relation}").fetchone()[0]
    )
    existing_count = int(
        connection.execute(
            f"""
            SELECT count(*)
            FROM {history_table} history
            JOIN {incoming_relation} incoming USING (observation_id)
            """
        ).fetchone()[0]
    )
    if existing_count == 0:
        return
    if existing_count != incoming_count:
        raise ValueError(
            "BazaarPulse history contains only part of the incoming sync identity; "
            "refusing a non-atomic replay"
        )
    differences = int(
        connection.execute(
            f"""
            SELECT count(*) FROM (
                SELECT * FROM {incoming_relation}
                EXCEPT
                SELECT history.*
                FROM {history_table} history
                JOIN {incoming_relation} incoming USING (observation_id)
            ) changed
            """
        ).fetchone()[0]
    )
    if differences:
        raise ValueError(
            "BazaarPulse observation identity was replayed with a different payload; "
            "append-only history was left unchanged"
        )


def _assert_source_price_replay_is_identical(
    connection: duckdb.DuckDBPyConnection,
    incoming_relation: str,
    history_table: str,
) -> None:
    """Reject source-date identity collisions while accepting exact rediscovery."""

    differences = int(
        connection.execute(
            f"""
            SELECT count(*)
            FROM {incoming_relation} incoming
            JOIN {history_table} history USING (source_price_observation_id)
            WHERE incoming.listing_id IS DISTINCT FROM history.listing_id
               OR incoming.city IS DISTINCT FROM history.city
               OR incoming.retailer IS DISTINCT FROM history.retailer
               OR incoming.raw_title IS DISTINCT FROM history.raw_title
               OR incoming.category IS DISTINCT FROM history.category
               OR incoming.pack_value IS DISTINCT FROM history.pack_value
               OR incoming.pack_uom IS DISTINCT FROM history.pack_uom
               OR incoming.observed_on IS DISTINCT FROM history.observed_on
               OR incoming.price_inr IS DISTINCT FROM history.price_inr
               OR incoming.source_path IS DISTINCT FROM history.source_path
            """
        ).fetchone()[0]
    )
    if differences:
        raise ValueError(
            "BazaarPulse source price observation identity was rediscovered with a different "
            "payload; append-only source history was left unchanged"
        )


def store_bazaarpulse_snapshot(
    database_path: Path,
    listings: list[Listing],
    matches: list[ProductMatch],
    *,
    source_price_observations: Sequence[SourcePriceObservation] = (),
    source_detail_failures: Sequence[SourceDetailFailure] = (),
    started_at_utc: datetime | None = None,
    completed_at_utc: datetime | None = None,
) -> IntegrationStoreSummary:
    """Publish current rows, sync audit, and source-dated price history atomically."""

    if not listings:
        raise ValueError("Refusing to replace the last-good BazaarPulse snapshot with no rows")
    listing_ids = [listing.listing_id for listing in listings]
    match_ids = [match.listing_id for match in matches]
    if len(listing_ids) != len(set(listing_ids)):
        raise ValueError("BazaarPulse snapshot contains duplicate listing IDs")
    if set(listing_ids) != set(match_ids) or len(match_ids) != len(set(match_ids)):
        raise ValueError("BazaarPulse matches must contain exactly one outcome per listing")
    listing_by_id = {listing.listing_id: listing for listing in listings}
    source_observation_ids = [
        observation.source_price_observation_id
        for observation in source_price_observations
    ]
    if len(source_observation_ids) != len(set(source_observation_ids)):
        raise ValueError("BazaarPulse source price history contains duplicate listing/date rows")
    for observation in source_price_observations:
        listing = listing_by_id.get(observation.listing_id)
        if listing is None:
            raise ValueError(
                "BazaarPulse source price history contains an unknown current listing ID"
            )
        if (
            observation.city != listing.city
            or observation.retailer.casefold() != listing.retailer.casefold()
        ):
            raise ValueError(
                "BazaarPulse source price history identity disagrees with the current listing"
            )
    failure_ids = [failure.listing_id for failure in source_detail_failures]
    if len(failure_ids) != len(set(failure_ids)):
        raise ValueError("BazaarPulse source detail failures contain duplicate listing IDs")
    if not set(failure_ids).issubset(listing_by_id):
        raise ValueError("BazaarPulse source detail failures contain an unknown listing ID")

    started = started_at_utc or datetime.now(UTC)
    completed = completed_at_utc or datetime.now(UTC)
    observed_dates = [listing.last_seen for listing in listings if listing.last_seen]
    observed_dates.extend(
        observation.observed_on for observation in source_price_observations
    )
    coverage_start = min(observed_dates, default=None)
    coverage_end = max(observed_dates, default=None)
    matched_count = sum(match.matched for match in matches)
    sync_id = f"bazaarpulse-{completed.isoformat()}"

    listing_records = []
    for listing in listings:
        record = asdict(listing)
        record["last_seen"] = listing.last_seen
        record["collected_at_utc"] = completed
        record["sync_id"] = sync_id
        record["observation_id"] = f"{sync_id}:{listing.listing_id}"
        listing_records.append(record)
    match_records = []
    for match in matches:
        provenance = match.provenance
        if provenance == "automatic":
            provenance = (
                "automatic_high_confidence" if match.matched else "automatic_quarantine"
            )
        record = asdict(match)
        record.update(
            {
                "product_id": (
                    int(match.product_id) if match.product_id is not None else None
                ),
                "suggested_product_id": (
                    int(match.suggested_product_id)
                    if match.suggested_product_id is not None
                    else None
                ),
                "provenance": provenance,
                "algorithm_status": match.algorithm_status or match.status,
                "algorithm_reason": match.algorithm_reason or match.reason,
                "reviewed_on": match.reviewed_on,
                "matched": match.matched,
                "collected_at_utc": completed,
                "sync_id": sync_id,
                "observation_id": f"{sync_id}:{match.listing_id}",
            }
        )
        match_records.append(record)
    source_price_records = []
    for observation in source_price_observations:
        record = asdict(observation)
        record.update(
            {
                "source_price_observation_id": (
                    observation.source_price_observation_id
                ),
                "observed_on": observation.observed_on,
                "first_collected_at_utc": completed,
                "first_sync_id": sync_id,
            }
        )
        source_price_records.append(record)
    source_failure_records = []
    for failure in source_detail_failures:
        record = asdict(failure)
        record.update({"collected_at_utc": completed, "sync_id": sync_id})
        source_failure_records.append(record)

    connection = duckdb.connect(str(database_path))
    try:
        _register_frame(connection, "_bazaarpulse_listings", listing_records)
        _register_frame(connection, "_bazaarpulse_matches", match_records)
        source_price_columns = [
            "listing_id",
            "city",
            "retailer",
            "raw_title",
            "category",
            "pack_value",
            "pack_uom",
            "observed_on",
            "price_inr",
            "source_path",
            "source_price_observation_id",
            "first_collected_at_utc",
            "first_sync_id",
        ]
        connection.register(
            "_bazaarpulse_source_prices",
            pd.DataFrame.from_records(source_price_records, columns=source_price_columns),
        )
        source_failure_columns = [
            "listing_id",
            "source_path",
            "failure_type",
            "message",
            "collected_at_utc",
            "sync_id",
        ]
        connection.register(
            "_bazaarpulse_source_failures",
            pd.DataFrame.from_records(
                source_failure_records,
                columns=source_failure_columns,
            ),
        )
        connection.execute(
            """
            CREATE OR REPLACE TEMP VIEW _bazaarpulse_listings_typed AS
            SELECT
                cast(listing_id AS VARCHAR) AS listing_id,
                cast(city AS VARCHAR) AS city,
                cast(retailer AS VARCHAR) AS retailer,
                cast(raw_title AS VARCHAR) AS raw_title,
                cast(normalized_title AS VARCHAR) AS normalized_title,
                cast(brand AS VARCHAR) AS brand,
                cast(pack_value AS DOUBLE) AS pack_value,
                cast(pack_uom AS VARCHAR) AS pack_uom,
                cast(category AS VARCHAR) AS category,
                cast(current_price_inr AS DOUBLE) AS current_price_inr,
                cast(mrp_inr AS DOUBLE) AS mrp_inr,
                cast(is_available AS BOOLEAN) AS is_available,
                cast(last_seen AS DATE) AS last_seen,
                cast(detail_path AS VARCHAR) AS detail_path,
                cast(source_path AS VARCHAR) AS source_path,
                cast(collected_at_utc AS TIMESTAMPTZ) AS collected_at_utc,
                cast(sync_id AS VARCHAR) AS sync_id,
                cast(observation_id AS VARCHAR) AS observation_id
            FROM _bazaarpulse_listings
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE TEMP VIEW _bazaarpulse_source_failures_typed AS
            SELECT
                cast(listing_id AS VARCHAR) AS listing_id,
                cast(source_path AS VARCHAR) AS source_path,
                cast(failure_type AS VARCHAR) AS failure_type,
                cast(message AS VARCHAR) AS message,
                cast(collected_at_utc AS TIMESTAMPTZ) AS collected_at_utc,
                cast(sync_id AS VARCHAR) AS sync_id
            FROM _bazaarpulse_source_failures
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE TEMP VIEW _bazaarpulse_source_prices_typed AS
            SELECT
                cast(source_price_observation_id AS VARCHAR)
                    AS source_price_observation_id,
                cast(listing_id AS VARCHAR) AS listing_id,
                cast(city AS VARCHAR) AS city,
                cast(retailer AS VARCHAR) AS retailer,
                cast(raw_title AS VARCHAR) AS raw_title,
                cast(category AS VARCHAR) AS category,
                cast(pack_value AS DOUBLE) AS pack_value,
                cast(pack_uom AS VARCHAR) AS pack_uom,
                cast(observed_on AS DATE) AS observed_on,
                cast(price_inr AS DOUBLE) AS price_inr,
                cast(source_path AS VARCHAR) AS source_path,
                cast(first_collected_at_utc AS TIMESTAMPTZ) AS first_collected_at_utc,
                cast(first_sync_id AS VARCHAR) AS first_sync_id
            FROM _bazaarpulse_source_prices
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE TEMP VIEW _bazaarpulse_matches_typed AS
            SELECT
                cast(listing_id AS VARCHAR) AS listing_id,
                cast(status AS VARCHAR) AS status,
                cast(confidence AS DOUBLE) AS confidence,
                cast(runner_up_confidence AS DOUBLE) AS runner_up_confidence,
                cast(product_id AS BIGINT) AS product_id,
                cast(sku_code AS VARCHAR) AS sku_code,
                cast(reason AS VARCHAR) AS reason,
                cast(suggested_product_id AS BIGINT) AS suggested_product_id,
                cast(suggested_sku_code AS VARCHAR) AS suggested_sku_code,
                cast(provenance AS VARCHAR) AS provenance,
                cast(algorithm_status AS VARCHAR) AS algorithm_status,
                cast(algorithm_reason AS VARCHAR) AS algorithm_reason,
                cast(decision_source AS VARCHAR) AS decision_source,
                cast(reviewer AS VARCHAR) AS reviewer,
                cast(reviewed_on AS DATE) AS reviewed_on,
                cast(review_note AS VARCHAR) AS review_note,
                cast(matched AS BOOLEAN) AS matched,
                cast(collected_at_utc AS TIMESTAMPTZ) AS collected_at_utc,
                cast(sync_id AS VARCHAR) AS sync_id,
                cast(observation_id AS VARCHAR) AS observation_id
            FROM _bazaarpulse_matches
            """
        )
        connection.execute("BEGIN TRANSACTION")
        _ensure_sync_table(connection)
        connection.execute("DELETE FROM external_sync_runs WHERE sync_id = ?", [sync_id])
        listing_history_exists = _table_exists(
            connection, "ext_bazaarpulse_listing_history"
        )
        match_history_exists = _table_exists(connection, "ext_bazaarpulse_match_history")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ext_bazaarpulse_listing_history AS
            SELECT * FROM _bazaarpulse_listings_typed WHERE FALSE
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ext_bazaarpulse_match_history AS
            SELECT * FROM _bazaarpulse_matches_typed WHERE FALSE
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ext_bazaarpulse_source_price_observation AS
            SELECT * FROM _bazaarpulse_source_prices_typed WHERE FALSE
            """
        )
        if not listing_history_exists:
            _seed_bazaarpulse_history_from_legacy_current(
                connection,
                "ext_bazaarpulse_listing_history",
                "ext_bazaarpulse_listing_current",
            )
        if not match_history_exists:
            _seed_bazaarpulse_history_from_legacy_current(
                connection,
                "ext_bazaarpulse_match_history",
                "ext_bazaarpulse_match_current",
            )
        _assert_history_replay_is_identical(
            connection,
            "_bazaarpulse_listings_typed",
            "ext_bazaarpulse_listing_history",
        )
        _assert_source_price_replay_is_identical(
            connection,
            "_bazaarpulse_source_prices_typed",
            "ext_bazaarpulse_source_price_observation",
        )
        _assert_history_replay_is_identical(
            connection,
            "_bazaarpulse_matches_typed",
            "ext_bazaarpulse_match_history",
        )
        connection.execute(
            """
            INSERT INTO ext_bazaarpulse_listing_history BY NAME
            SELECT incoming.* FROM _bazaarpulse_listings_typed incoming
            WHERE NOT EXISTS (
                SELECT 1 FROM ext_bazaarpulse_listing_history history
                WHERE history.observation_id = incoming.observation_id
            )
            """
        )
        connection.execute(
            """
            INSERT INTO ext_bazaarpulse_source_price_observation BY NAME
            SELECT incoming.* FROM _bazaarpulse_source_prices_typed incoming
            WHERE NOT EXISTS (
                SELECT 1 FROM ext_bazaarpulse_source_price_observation history
                WHERE history.source_price_observation_id =
                      incoming.source_price_observation_id
            )
            """
        )
        connection.execute(
            """
            INSERT INTO ext_bazaarpulse_match_history BY NAME
            SELECT incoming.* FROM _bazaarpulse_matches_typed incoming
            WHERE NOT EXISTS (
                SELECT 1 FROM ext_bazaarpulse_match_history history
                WHERE history.observation_id = incoming.observation_id
            )
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE TABLE ext_bazaarpulse_listing_current AS
            SELECT * FROM _bazaarpulse_listings_typed
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE TABLE ext_bazaarpulse_match_current AS
            SELECT * FROM _bazaarpulse_matches_typed
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE TABLE ext_bazaarpulse_source_detail_failure_current AS
            SELECT * FROM _bazaarpulse_source_failures_typed
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE VIEW vw_competitor_price_current AS
            SELECT
                listing.listing_id,
                listing.city,
                listing.retailer,
                listing.raw_title,
                listing.brand AS observed_brand,
                listing.category AS observed_category,
                listing.pack_value AS observed_pack_value,
                listing.pack_uom AS observed_pack_uom,
                listing.current_price_inr,
                listing.mrp_inr AS observed_mrp_inr,
                listing.is_available,
                listing.last_seen,
                listing.collected_at_utc,
                listing.sync_id,
                listing.observation_id,
                match.status AS match_status,
                match.confidence AS match_confidence,
                match.runner_up_confidence,
                match.reason AS match_reason,
                match.product_id,
                match.sku_code,
                match.suggested_product_id,
                match.suggested_sku_code,
                match.provenance AS match_provenance,
                match.algorithm_status,
                match.algorithm_reason,
                match.decision_source,
                match.reviewer,
                match.reviewed_on,
                match.review_note,
                product.product_name,
                product.brand AS kestrel_brand,
                product.category AS kestrel_category,
                product.current_mrp_inr AS kestrel_mrp_inr
            FROM ext_bazaarpulse_listing_current listing
            JOIN ext_bazaarpulse_match_current match USING (listing_id)
            LEFT JOIN dim_product product ON product.product_id = match.product_id
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE VIEW vw_competitor_price_history AS
            SELECT
                listing.listing_id,
                listing.city,
                listing.retailer,
                listing.raw_title,
                listing.brand AS observed_brand,
                listing.category AS observed_category,
                listing.pack_value AS observed_pack_value,
                listing.pack_uom AS observed_pack_uom,
                listing.current_price_inr,
                listing.mrp_inr AS observed_mrp_inr,
                listing.is_available,
                listing.last_seen,
                listing.collected_at_utc,
                listing.sync_id,
                listing.observation_id,
                match.status AS match_status,
                match.confidence AS match_confidence,
                match.runner_up_confidence,
                match.reason AS match_reason,
                match.product_id,
                match.sku_code,
                match.suggested_product_id,
                match.suggested_sku_code,
                match.provenance AS match_provenance,
                match.algorithm_status,
                match.algorithm_reason,
                match.decision_source,
                match.reviewer,
                match.reviewed_on,
                match.review_note,
                product.product_name,
                product.brand AS kestrel_brand,
                product.category AS kestrel_category,
                product.current_mrp_inr AS kestrel_mrp_inr
            FROM ext_bazaarpulse_listing_history listing
            JOIN ext_bazaarpulse_match_history match USING (observation_id)
            LEFT JOIN dim_product product ON product.product_id = match.product_id
            """
        )
        product_columns = _table_columns(connection, "dim_product")
        product_pack_value = (
            "product.pack_size_value"
            if "pack_size_value" in product_columns
            else "NULL::DOUBLE"
        )
        product_pack_uom = (
            "product.pack_size_uom"
            if "pack_size_uom" in product_columns
            else "NULL::VARCHAR"
        )
        has_price_history = _table_exists(connection, "raw_product_price_history")
        if has_price_history:
            price_history_fields = """
                historical_price.price_history_id,
                historical_price.effective_from AS mrp_effective_from,
                historical_price.effective_to AS mrp_effective_to,
                historical_price.mrp_inr AS historical_kestrel_mrp_inr,
                historical_price.list_price_inr AS historical_kestrel_list_price_inr,
            """
            price_history_join = """
                LEFT JOIN LATERAL (
                    SELECT cast(price.price_history_id AS BIGINT) AS price_history_id,
                           cast(price.effective_from AS DATE) AS effective_from,
                           cast(price.effective_to AS DATE) AS effective_to,
                           cast(price.mrp_inr AS DOUBLE) AS mrp_inr,
                           cast(price.list_price_inr AS DOUBLE) AS list_price_inr
                    FROM raw_product_price_history price
                    WHERE price.product_id = match.product_id
                      AND source.observed_on >= cast(price.effective_from AS DATE)
                      AND (
                          price.effective_to IS NULL
                          OR source.observed_on <= cast(price.effective_to AS DATE)
                      )
                    ORDER BY cast(price.effective_from AS DATE) DESC,
                             cast(price.price_history_id AS BIGINT) DESC
                    LIMIT 1
                ) historical_price ON TRUE
            """
        else:
            price_history_fields = """
                NULL::BIGINT AS price_history_id,
                NULL::DATE AS mrp_effective_from,
                NULL::DATE AS mrp_effective_to,
                NULL::DOUBLE AS historical_kestrel_mrp_inr,
                NULL::DOUBLE AS historical_kestrel_list_price_inr,
            """
            price_history_join = ""
        connection.execute(
            f"""
            CREATE OR REPLACE VIEW vw_competitor_source_price_history AS
            WITH enriched AS (
                SELECT
                    source.source_price_observation_id,
                    source.listing_id,
                    source.city,
                    source.retailer,
                    source.raw_title,
                    source.category AS observed_category,
                    source.pack_value AS observed_pack_value,
                    source.pack_uom AS observed_pack_uom,
                    source.observed_on,
                    source.price_inr AS observed_price_inr,
                    source.source_path,
                    source.first_collected_at_utc,
                    source.first_sync_id,
                    match.status AS match_status,
                    match.confidence AS match_confidence,
                    match.runner_up_confidence,
                    match.reason AS match_reason,
                    match.product_id,
                    match.sku_code,
                    match.suggested_product_id,
                    match.suggested_sku_code,
                    match.provenance AS match_provenance,
                    match.algorithm_status,
                    match.algorithm_reason,
                    match.decision_source,
                    match.reviewer,
                    match.reviewed_on,
                    match.review_note,
                    product.product_name,
                    product.brand AS kestrel_brand,
                    product.category AS kestrel_category,
                    {product_pack_value} AS kestrel_pack_value,
                    {product_pack_uom} AS kestrel_pack_uom,
                    {price_history_fields}
                    CASE upper(source.pack_uom)
                        WHEN 'KG' THEN source.pack_value * 1000
                        WHEN 'L' THEN source.pack_value * 1000
                        WHEN 'G' THEN source.pack_value
                        WHEN 'ML' THEN source.pack_value
                    END AS observed_base_quantity,
                    CASE upper({product_pack_uom})
                        WHEN 'KG' THEN {product_pack_value} * 1000
                        WHEN 'L' THEN {product_pack_value} * 1000
                        WHEN 'G' THEN {product_pack_value}
                        WHEN 'ML' THEN {product_pack_value}
                    END AS kestrel_base_quantity,
                    CASE
                        WHEN upper(source.pack_uom) IN ('G', 'KG') THEN 'G'
                        WHEN upper(source.pack_uom) IN ('ML', 'L') THEN 'ML'
                    END AS observed_base_uom,
                    CASE
                        WHEN upper({product_pack_uom}) IN ('G', 'KG') THEN 'G'
                        WHEN upper({product_pack_uom}) IN ('ML', 'L') THEN 'ML'
                    END AS kestrel_base_uom
                FROM ext_bazaarpulse_source_price_observation source
                JOIN ext_bazaarpulse_match_current match USING (listing_id)
                LEFT JOIN dim_product product ON product.product_id = match.product_id
                {price_history_join}
            ), comparable AS (
                SELECT *,
                       coalesce(
                           observed_base_quantity > 0
                           AND kestrel_base_quantity > 0
                           AND observed_base_uom = kestrel_base_uom,
                           FALSE
                       ) AS pack_comparable
                FROM enriched
            )
            SELECT *,
                   CASE WHEN pack_comparable THEN '100 ' || observed_base_uom END
                       AS unit_price_basis,
                   CASE WHEN pack_comparable
                        THEN observed_price_inr * 100.0 / observed_base_quantity END
                       AS observed_unit_price_inr,
                   CASE WHEN pack_comparable AND historical_kestrel_mrp_inr IS NOT NULL
                        THEN historical_kestrel_mrp_inr * 100.0 / kestrel_base_quantity END
                       AS historical_kestrel_mrp_unit_inr,
                   CASE WHEN pack_comparable AND historical_kestrel_mrp_inr IS NOT NULL
                        THEN historical_kestrel_mrp_inr * 100.0 / kestrel_base_quantity
                           - observed_price_inr * 100.0 / observed_base_quantity END
                       AS historical_unit_price_gap_inr,
                   CASE WHEN pack_comparable AND historical_kestrel_mrp_inr IS NOT NULL
                                  AND observed_price_inr > 0
                        THEN 100.0 * (
                            (historical_kestrel_mrp_inr / kestrel_base_quantity)
                            / (observed_price_inr / observed_base_quantity) - 1
                        ) END AS historical_mrp_premium_pct,
                   historical_kestrel_mrp_inr IS NOT NULL AS mrp_history_available
            FROM comparable
            """
        )
        connection.execute(
            """
            CREATE OR REPLACE VIEW vw_competitor_match_review_queue AS
            SELECT listing_id, city, retailer, raw_title, observed_brand,
                   observed_category, observed_pack_value, observed_pack_uom,
                   current_price_inr, last_seen, collected_at_utc,
                   match_status, match_confidence, runner_up_confidence,
                   suggested_product_id, suggested_sku_code,
                   algorithm_reason, sync_id, observation_id
            FROM vw_competitor_price_current
            WHERE match_status IN ('ambiguous', 'low_confidence', 'no_candidates')
            """
        )
        details = json.dumps(
            {
                "matched": matched_count,
                "ambiguous": sum(match.status == "ambiguous" for match in matches),
                "low_confidence": sum(match.status == "low_confidence" for match in matches),
                "no_candidates": sum(match.status == "no_candidates" for match in matches),
                "rejected": sum(match.status == "rejected" for match in matches),
                "manual_matches": sum(
                    match.provenance == "manual_match" for match in matches
                ),
                "manual_rejections": sum(
                    match.provenance == "manual_rejection" for match in matches
                ),
                "source_price_observations": len(source_price_observations),
                "source_price_history_name": (
                    "ext_bazaarpulse_source_price_observation"
                ),
                "source_detail_failures": [
                    failure.to_dict() for failure in source_detail_failures
                ],
                "source_price_history_complete": not source_detail_failures,
            },
            sort_keys=True,
        )
        connection.execute(
            """
            INSERT INTO external_sync_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                sync_id,
                "bazaarpulse",
                "SUCCEEDED_WITH_WARNINGS" if source_detail_failures else "SUCCEEDED",
                len(listings),
                not source_detail_failures,
                coverage_start,
                coverage_end,
                started,
                completed,
                details,
            ],
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()

    return IntegrationStoreSummary(
        source_name="bazaarpulse",
        record_count=len(listings),
        matched_count=matched_count,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        completed_at_utc=completed,
    )


def store_freight_snapshot(
    database_path: Path,
    invoices: tuple[FreightInvoice, ...],
    metadata: FreightSyncMetadata,
) -> IntegrationStoreSummary:
    """Atomically publish a schema-validated, complete carrier-billing snapshot."""

    if not metadata.complete or metadata.completed_at_utc is None:
        raise ValueError("Refusing to publish an incomplete freight sync")
    invoice_ids = [invoice.invoice_id for invoice in invoices]
    if len(invoice_ids) != len(set(invoice_ids)):
        raise ValueError("Freight snapshot contains duplicate invoice IDs")
    if metadata.record_count != len(invoices):
        raise ValueError("Freight metadata record count does not reconcile to invoices")
    if not invoices:
        raise ValueError("Refusing to replace the last-good freight snapshot with no rows")

    records = [
        {
            "invoice_id": invoice.invoice_id,
            "carrier_id": invoice.carrier_id,
            "carrier_name": invoice.carrier_name,
            "warehouse_code": invoice.warehouse_code,
            "route_code": invoice.route_code,
            "invoice_date": invoice.invoice_date,
            "service_date": invoice.service_date,
            "amount_paise": invoice.amount_paise,
            "amount_inr": invoice.amount_inr,
            "currency": invoice.currency,
            "fuel_surcharge_pct": invoice.fuel_surcharge_pct,
            "detention_charge_paise": invoice.detention_charge_paise,
            "detention_charge_inr": invoice.detention_charge_inr,
            "billed_freight_cost_inr": (
                invoice.amount_inr + invoice.detention_charge_inr
            ),
            "distance_km": invoice.distance_km,
            "weight_kg": invoice.weight_kg,
            "temperature_controlled": invoice.temperature_controlled,
            "invoice_status": invoice.status,
            "created_at_utc": invoice.created_at_utc,
            "created_at_ist": invoice.created_at_ist,
            "synced_at_utc": metadata.completed_at_utc,
        }
        for invoice in invoices
    ]
    coverage_start = min(invoice.service_date for invoice in invoices)
    coverage_end = max(invoice.service_date for invoice in invoices)
    details = json.dumps(
        {
            "cache_mode": metadata.cache_mode,
            "requested_date_from": (
                metadata.date_from.isoformat() if metadata.date_from else None
            ),
            "requested_date_to": metadata.date_to.isoformat() if metadata.date_to else None,
            "page_count": metadata.page_count,
            "request_count": metadata.request_count,
            "retry_count": metadata.retry_count,
            "fetched_count": metadata.fetched_count,
            "resumed": metadata.resumed,
        },
        sort_keys=True,
    )

    connection = duckdb.connect(str(database_path))
    try:
        _register_frame(connection, "_freight_invoices", records)
        connection.execute("BEGIN TRANSACTION")
        _ensure_sync_table(connection)
        sync_id = f"freight-{metadata.completed_at_utc.isoformat()}"
        connection.execute("DELETE FROM external_sync_runs WHERE sync_id = ?", [sync_id])
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ext_freight_invoice_current AS
            SELECT * FROM _freight_invoices WHERE FALSE
            """
        )
        connection.execute("DELETE FROM ext_freight_invoice_current")
        connection.execute(
            "INSERT INTO ext_freight_invoice_current SELECT * FROM _freight_invoices"
        )
        connection.execute(
            """
            INSERT INTO external_sync_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                sync_id,
                "freight_api",
                "SUCCEEDED",
                len(invoices),
                True,
                coverage_start,
                coverage_end,
                metadata.started_at_utc,
                metadata.completed_at_utc,
                details,
            ],
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()

    return IntegrationStoreSummary(
        source_name="freight_api",
        record_count=len(invoices),
        matched_count=None,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        completed_at_utc=metadata.completed_at_utc,
    )


def store_weather_snapshot(
    database_path: Path,
    cache: WeatherCache,
) -> IntegrationStoreSummary:
    """Atomically publish a complete warehouse-city-centroid weather snapshot."""

    metadata = cache.metadata
    if not cache.observations:
        raise ValueError("Refusing to replace the last-good weather snapshot with no rows")
    if metadata.record_count != metadata.expected_record_count:
        raise ValueError("Refusing to publish incomplete weather coverage")
    records = [
        {
            "warehouse_code": row.warehouse_code,
            "warehouse_city": row.warehouse_city,
            "observation_date": row.observation_date,
            "temperature_2m_max_c": row.temperature_2m_max_c,
            "precipitation_sum_mm": row.precipitation_sum_mm,
            "latitude": row.latitude,
            "longitude": row.longitude,
            "timezone": row.timezone,
            "location_basis": row.location_basis,
            "collected_at_utc": metadata.collected_at_utc,
        }
        for row in cache.observations
    ]
    details = json.dumps(
        {
            "expected_record_count": metadata.expected_record_count,
            "row_coverage_ratio": metadata.row_coverage_ratio,
            "location_count": metadata.location_count,
            "expected_location_count": metadata.expected_location_count,
            "location_coverage_ratio": metadata.location_coverage_ratio,
            "request_count": metadata.request_count,
            "retry_count": metadata.retry_count,
            "location_basis": "warehouse_city_centroid",
            "source_url": metadata.source_url,
            "provider": "Open-Meteo archive API",
        },
        sort_keys=True,
    )
    connection = duckdb.connect(str(database_path))
    try:
        _register_frame(connection, "_weather_daily", records)
        connection.execute("BEGIN TRANSACTION")
        _ensure_sync_table(connection)
        sync_id = f"weather-{metadata.collected_at_utc.isoformat()}"
        connection.execute("DELETE FROM external_sync_runs WHERE sync_id = ?", [sync_id])
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ext_weather_daily_current AS
            SELECT * FROM _weather_daily WHERE FALSE
            """
        )
        connection.execute("DELETE FROM ext_weather_daily_current")
        connection.execute("INSERT INTO ext_weather_daily_current SELECT * FROM _weather_daily")
        connection.execute(
            """INSERT INTO external_sync_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                sync_id,
                metadata.source_name,
                "SUCCEEDED",
                metadata.record_count,
                True,
                metadata.coverage_start,
                metadata.coverage_end,
                metadata.collected_at_utc,
                metadata.collected_at_utc,
                details,
            ],
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()
    return IntegrationStoreSummary(
        source_name=metadata.source_name,
        record_count=metadata.record_count,
        matched_count=None,
        coverage_start=metadata.coverage_start,
        coverage_end=metadata.coverage_end,
        completed_at_utc=metadata.collected_at_utc,
    )


def store_holiday_snapshot(
    database_path: Path,
    cache: HolidayCache,
) -> IntegrationStoreSummary:
    """Atomically publish a complete Indian public-holiday snapshot."""

    metadata = cache.metadata
    if not cache.observations:
        raise ValueError("Refusing to replace the last-good holiday snapshot with no rows")
    records = [
        {
            "holiday_date": row.holiday_date,
            "local_name": row.local_name,
            "holiday_name": row.name,
            "country_code": row.country_code,
            "fixed": row.fixed,
            "global_holiday": row.global_holiday,
            "counties_json": json.dumps(row.counties) if row.counties is not None else None,
            "launch_year": row.launch_year,
            "types_json": json.dumps(row.types),
            "collected_at_utc": metadata.collected_at_utc,
        }
        for row in cache.observations
    ]
    details = json.dumps(
        {
            "requested_years": list(
                range(metadata.requested_date_from.year, metadata.requested_date_to.year + 1)
            ),
            "request_count": metadata.request_count,
            "retry_count": metadata.retry_count,
            "national_and_subdivision_rows": metadata.record_count,
            "source_url": metadata.source_url,
            "provider": (
                "Google India public holiday ICS fallback"
                if "calendar.google.com" in metadata.source_url
                else "Nager.Date public-holiday API"
            ),
        },
        sort_keys=True,
    )
    connection = duckdb.connect(str(database_path))
    try:
        _register_frame(connection, "_india_holidays", records)
        connection.execute("BEGIN TRANSACTION")
        _ensure_sync_table(connection)
        sync_id = f"holidays-{metadata.collected_at_utc.isoformat()}"
        connection.execute("DELETE FROM external_sync_runs WHERE sync_id = ?", [sync_id])
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ext_india_holiday_current AS
            SELECT * FROM _india_holidays WHERE FALSE
            """
        )
        connection.execute("DELETE FROM ext_india_holiday_current")
        connection.execute("INSERT INTO ext_india_holiday_current SELECT * FROM _india_holidays")
        connection.execute(
            """INSERT INTO external_sync_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                sync_id,
                metadata.source_name,
                "SUCCEEDED",
                metadata.record_count,
                True,
                metadata.coverage_start,
                metadata.coverage_end,
                metadata.collected_at_utc,
                metadata.collected_at_utc,
                details,
            ],
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()
    return IntegrationStoreSummary(
        source_name=metadata.source_name,
        record_count=metadata.record_count,
        matched_count=None,
        coverage_start=metadata.coverage_start,
        coverage_end=metadata.coverage_end,
        completed_at_utc=metadata.collected_at_utc,
    )
