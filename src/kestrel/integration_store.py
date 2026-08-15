"""Persistence for external observations at their defensible native grains.

The analytical database is rebuilt atomically from the supplied operational source.  External
syncs run afterwards and replace only their own current-snapshot tables inside a transaction.
This keeps a failed refresh from destroying the last queryable snapshot.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from kestrel.ingestion.bazaarpulse import Listing, ProductCandidate, ProductMatch
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


def store_bazaarpulse_snapshot(
    database_path: Path,
    listings: list[Listing],
    matches: list[ProductMatch],
    *,
    started_at_utc: datetime | None = None,
    completed_at_utc: datetime | None = None,
) -> IntegrationStoreSummary:
    """Atomically publish a complete BazaarPulse listing and match snapshot."""

    if not listings:
        raise ValueError("Refusing to replace the last-good BazaarPulse snapshot with no rows")
    listing_ids = [listing.listing_id for listing in listings]
    match_ids = [match.listing_id for match in matches]
    if len(listing_ids) != len(set(listing_ids)):
        raise ValueError("BazaarPulse snapshot contains duplicate listing IDs")
    if set(listing_ids) != set(match_ids) or len(match_ids) != len(set(match_ids)):
        raise ValueError("BazaarPulse matches must contain exactly one outcome per listing")

    started = started_at_utc or datetime.now(UTC)
    completed = completed_at_utc or datetime.now(UTC)
    observed_dates = [listing.last_seen for listing in listings if listing.last_seen]
    coverage_start = min(observed_dates, default=None)
    coverage_end = max(observed_dates, default=None)
    matched_count = sum(match.matched for match in matches)

    listing_records = []
    for listing in listings:
        record = asdict(listing)
        record["last_seen"] = listing.last_seen
        record["collected_at_utc"] = completed
        listing_records.append(record)
    match_records = [
        {
            **asdict(match),
            "product_id": int(match.product_id) if match.product_id is not None else None,
            "matched": match.matched,
            "collected_at_utc": completed,
        }
        for match in matches
    ]

    connection = duckdb.connect(str(database_path))
    try:
        _register_frame(connection, "_bazaarpulse_listings", listing_records)
        _register_frame(connection, "_bazaarpulse_matches", match_records)
        connection.execute("BEGIN TRANSACTION")
        _ensure_sync_table(connection)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ext_bazaarpulse_listing_current AS
            SELECT * FROM _bazaarpulse_listings WHERE FALSE
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ext_bazaarpulse_match_current AS
            SELECT * FROM _bazaarpulse_matches WHERE FALSE
            """
        )
        connection.execute("DELETE FROM ext_bazaarpulse_listing_current")
        connection.execute("DELETE FROM ext_bazaarpulse_match_current")
        connection.execute(
            "INSERT INTO ext_bazaarpulse_listing_current SELECT * FROM _bazaarpulse_listings"
        )
        connection.execute(
            "INSERT INTO ext_bazaarpulse_match_current SELECT * FROM _bazaarpulse_matches"
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
                match.status AS match_status,
                match.confidence AS match_confidence,
                match.runner_up_confidence,
                match.reason AS match_reason,
                match.product_id,
                match.sku_code,
                product.product_name,
                product.brand AS kestrel_brand,
                product.category AS kestrel_category,
                product.current_mrp_inr AS kestrel_mrp_inr
            FROM ext_bazaarpulse_listing_current listing
            JOIN ext_bazaarpulse_match_current match USING (listing_id)
            LEFT JOIN dim_product product ON product.product_id = match.product_id
            """
        )
        details = json.dumps(
            {
                "matched": matched_count,
                "ambiguous": sum(match.status == "ambiguous" for match in matches),
                "low_confidence": sum(match.status == "low_confidence" for match in matches),
                "no_candidates": sum(match.status == "no_candidates" for match in matches),
            },
            sort_keys=True,
        )
        connection.execute(
            """
            INSERT INTO external_sync_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                f"bazaarpulse-{completed.isoformat()}",
                "bazaarpulse",
                "SUCCEEDED",
                len(listings),
                True,
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
                f"freight-{metadata.completed_at_utc.isoformat()}",
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
