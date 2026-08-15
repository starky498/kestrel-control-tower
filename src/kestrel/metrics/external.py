"""Governed metrics over freight and competitor observations.

External sources are optional at application start.  Every method therefore reports absence with
an empty, schema-stable frame instead of making the main operational dashboard unavailable.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from kestrel.metrics.service import FilterSet


class ExternalAnalyticsService:
    """Read-only freight and price-position queries with explicit attribution limits."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)

    def _connect(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(str(self.database_path), read_only=True)

    @staticmethod
    def _exists(connection: duckdb.DuckDBPyConnection, object_name: str) -> bool:
        return bool(
            connection.execute(
                """
                SELECT count(*)
                FROM information_schema.tables
                WHERE table_name = ?
                """,
                [object_name],
            ).fetchone()[0]
        )

    def availability(self) -> dict[str, bool]:
        """Return which optional snapshots are currently queryable."""

        with self._connect() as connection:
            return {
                "freight": self._exists(connection, "ext_freight_invoice_current"),
                "competitor": self._exists(connection, "ext_bazaarpulse_listing_current"),
                "sync_history": self._exists(connection, "external_sync_runs"),
            }

    def sync_status(self) -> pd.DataFrame:
        """Latest successful run and coverage per external source."""

        columns = [
            "source_name",
            "status",
            "record_count",
            "is_complete",
            "coverage_start",
            "coverage_end",
            "completed_at_utc",
            "details_json",
        ]
        with self._connect() as connection:
            if not self._exists(connection, "external_sync_runs"):
                return pd.DataFrame(columns=columns)
            return connection.execute(
                """
                SELECT source_name, status, record_count, is_complete,
                       coverage_start, coverage_end, completed_at_utc, details_json
                FROM external_sync_runs
                QUALIFY row_number() OVER (
                    PARTITION BY source_name ORDER BY completed_at_utc DESC
                ) = 1
                ORDER BY source_name
                """
            ).fetchdf()

    @staticmethod
    def _shared_freight_filters(
        filters: FilterSet,
        *,
        date_column: str,
        warehouse_alias: str,
        route_column: str,
    ) -> tuple[str, list[Any]]:
        conditions = [f"{date_column} BETWEEN ? AND ?"]
        parameters: list[Any] = [filters.start_date, filters.end_date]
        dimensions: tuple[tuple[str, Iterable[str]], ...] = (
            (f"{warehouse_alias}.warehouse_region_name", filters.warehouse_regions),
            (f"{warehouse_alias}.warehouse_code", filters.warehouse_codes),
            (route_column, filters.route_codes),
        )
        for column, values_iterable in dimensions:
            values = tuple(values_iterable)
            if values:
                conditions.append(f"{column} IN ({', '.join('?' for _ in values)})")
                parameters.extend(values)
        return " AND ".join(conditions), parameters

    @staticmethod
    def _ignored_freight_filters(filters: FilterSet) -> tuple[str, ...]:
        ignored = []
        if filters.customer_regions:
            ignored.append("customer region")
        if filters.outlet_codes:
            ignored.append("outlet")
        if filters.channels:
            ignored.append("channel")
        return tuple(ignored)

    def freight_by_warehouse(self, filters: FilterSet) -> pd.DataFrame:
        """Billed freight per delivered case-equivalent, aggregated independently by warehouse.

        The API has no order or delivery key.  Freight and delivered cases are therefore summed
        separately over the same service period and warehouse, then divided.  No row-level join or
        carrier-level case attribution is claimed.
        """

        columns = [
            "warehouse_code",
            "warehouse_name",
            "warehouse_region_name",
            "invoice_count",
            "freight_cost_inr",
            "detention_cost_inr",
            "paid_cost_inr",
            "pending_cost_inr",
            "disputed_cost_inr",
            "delivered_case_equivalents",
            "delivered_orders",
            "freight_cost_per_delivered_case_inr",
        ]
        with self._connect() as connection:
            if not self._exists(connection, "ext_freight_invoice_current"):
                frame = pd.DataFrame(columns=columns)
                frame.attrs["unavailable_reason"] = "Run `make sync-freight` first."
                return frame

            freight_where, freight_parameters = self._shared_freight_filters(
                filters,
                date_column="invoice.service_date",
                warehouse_alias="warehouse",
                route_column="invoice.route_code",
            )
            delivery_where, delivery_parameters = self._shared_freight_filters(
                filters,
                date_column="service.delivery_date",
                warehouse_alias="warehouse",
                route_column="service.route_code",
            )
            frame = connection.execute(
                f"""
                WITH freight AS (
                    SELECT invoice.warehouse_code,
                           count(*) AS invoice_count,
                           sum(invoice.billed_freight_cost_inr) AS freight_cost_inr,
                           sum(invoice.detention_charge_inr) AS detention_cost_inr,
                           sum(invoice.billed_freight_cost_inr)
                               FILTER (WHERE invoice.invoice_status = 'PAID') AS paid_cost_inr,
                           sum(invoice.billed_freight_cost_inr)
                               FILTER (WHERE invoice.invoice_status = 'PENDING')
                               AS pending_cost_inr,
                           sum(invoice.billed_freight_cost_inr)
                               FILTER (WHERE invoice.invoice_status = 'DISPUTED')
                               AS disputed_cost_inr
                    FROM ext_freight_invoice_current invoice
                    JOIN dim_warehouse warehouse
                      ON warehouse.warehouse_code = invoice.warehouse_code
                    WHERE {freight_where}
                    GROUP BY invoice.warehouse_code
                ), delivered AS (
                    SELECT service.warehouse_code,
                           sum(service.delivered_case_equivalents)
                               AS delivered_case_equivalents,
                           count(*) AS delivered_orders
                    FROM fct_order_service service
                    JOIN dim_warehouse warehouse
                      ON warehouse.warehouse_code = service.warehouse_code
                    WHERE service.is_eligible_service AND {delivery_where}
                    GROUP BY service.warehouse_code
                )
                SELECT coalesce(freight.warehouse_code, delivered.warehouse_code)
                           AS warehouse_code,
                       warehouse.warehouse_name,
                       warehouse.warehouse_region_name,
                       freight.invoice_count,
                       freight.freight_cost_inr,
                       freight.detention_cost_inr,
                       freight.paid_cost_inr,
                       freight.pending_cost_inr,
                       freight.disputed_cost_inr,
                       delivered.delivered_case_equivalents,
                       delivered.delivered_orders,
                       freight.freight_cost_inr
                           / nullif(delivered.delivered_case_equivalents, 0)
                           AS freight_cost_per_delivered_case_inr
                FROM freight
                FULL OUTER JOIN delivered USING (warehouse_code)
                LEFT JOIN dim_warehouse warehouse
                  ON warehouse.warehouse_code = coalesce(
                      freight.warehouse_code, delivered.warehouse_code
                  )
                ORDER BY freight_cost_per_delivered_case_inr DESC NULLS LAST
                """,
                [*freight_parameters, *delivery_parameters],
            ).fetchdf()
        frame.attrs["ignored_filters"] = self._ignored_freight_filters(filters)
        frame.attrs["attribution"] = (
            "Invoice numerator and delivered-case denominator are independently aggregated at "
            "service period × warehouse; the source has no delivery key."
        )
        return frame

    def freight_by_carrier(self, filters: FilterSet) -> pd.DataFrame:
        """Billed spend by carrier, without inventing carrier-attributed case volume."""

        columns = [
            "carrier_id",
            "carrier_name",
            "invoice_count",
            "freight_cost_inr",
            "detention_cost_inr",
            "pending_cost_inr",
            "disputed_cost_inr",
        ]
        with self._connect() as connection:
            if not self._exists(connection, "ext_freight_invoice_current"):
                frame = pd.DataFrame(columns=columns)
                frame.attrs["unavailable_reason"] = "Run `make sync-freight` first."
                return frame
            where, parameters = self._shared_freight_filters(
                filters,
                date_column="invoice.service_date",
                warehouse_alias="warehouse",
                route_column="invoice.route_code",
            )
            frame = connection.execute(
                f"""
                SELECT invoice.carrier_id, invoice.carrier_name,
                       count(*) AS invoice_count,
                       sum(invoice.billed_freight_cost_inr) AS freight_cost_inr,
                       sum(invoice.detention_charge_inr) AS detention_cost_inr,
                       sum(invoice.billed_freight_cost_inr)
                           FILTER (WHERE invoice.invoice_status = 'PENDING') AS pending_cost_inr,
                       sum(invoice.billed_freight_cost_inr)
                           FILTER (WHERE invoice.invoice_status = 'DISPUTED') AS disputed_cost_inr
                FROM ext_freight_invoice_current invoice
                JOIN dim_warehouse warehouse
                  ON warehouse.warehouse_code = invoice.warehouse_code
                WHERE {where}
                GROUP BY invoice.carrier_id, invoice.carrier_name
                ORDER BY freight_cost_inr DESC
                """,
                parameters,
            ).fetchdf()
        frame.attrs["ignored_filters"] = self._ignored_freight_filters(filters)
        frame.attrs["attribution"] = (
            "Carrier invoice spend is valid; cost per case by carrier is unavailable because "
            "operational deliveries contain no carrier key."
        )
        return frame

    @staticmethod
    def _order_filter_sql(filters: FilterSet) -> tuple[str, list[Any]]:
        conditions = ["requested_delivery_date BETWEEN ? AND ?"]
        parameters: list[Any] = [filters.start_date, filters.end_date]
        dimensions: tuple[tuple[str, Iterable[str]], ...] = (
            ("customer_region_name", filters.customer_regions),
            ("warehouse_region_name", filters.warehouse_regions),
            ("warehouse_code", filters.warehouse_codes),
            ("route_code", filters.route_codes),
            ("outlet_code", filters.outlet_codes),
            ("channel", filters.channels),
        )
        for column, values_iterable in dimensions:
            values = tuple(values_iterable)
            if values:
                conditions.append(f"{column} IN ({', '.join('?' for _ in values)})")
                parameters.extend(values)
        return " AND ".join(conditions), parameters

    def competitor_price_gap(
        self,
        filters: FilterSet,
        *,
        city: str = "Mumbai",
        category: str | None = None,
        top_n: int = 20,
    ) -> pd.DataFrame:
        """Compare current Kestrel MRP with lowest matched, available observed shelf price."""

        if top_n <= 0 or top_n > 100:
            raise ValueError("top_n must be between 1 and 100")
        columns = [
            "sku_code",
            "product_name",
            "category",
            "dispatch_value_inr",
            "kestrel_mrp_inr",
            "lowest_competitor_price_inr",
            "price_gap_inr",
            "mrp_premium_pct",
            "matched_listings",
            "retailers",
            "latest_observation_date",
        ]
        with self._connect() as connection:
            if not self._exists(connection, "ext_bazaarpulse_listing_current"):
                frame = pd.DataFrame(columns=columns)
                frame.attrs["unavailable_reason"] = "Run `make scrape-prices` first."
                return frame
            where, parameters = self._order_filter_sql(filters)
            category_sql = ""
            if category:
                category_sql = " AND line.category = ?"
                parameters.append(category)
            parameters.extend([top_n, city])
            frame = connection.execute(
                f"""
                WITH top_skus AS (
                    SELECT line.sku_code,
                           any_value(line.product_name) AS product_name,
                           any_value(line.category) AS category,
                           sum(line.estimated_dispatch_value_inr) AS dispatch_value_inr,
                           any_value(product.current_mrp_inr) AS kestrel_mrp_inr
                    FROM fct_order_line line
                    JOIN dim_product product ON product.product_id = line.product_id
                    WHERE line.is_eligible_service AND {where}{category_sql}
                    GROUP BY line.sku_code
                    ORDER BY dispatch_value_inr DESC
                    LIMIT ?
                ), competitor AS (
                    SELECT sku_code,
                           min(current_price_inr) AS lowest_competitor_price_inr,
                           count(*) AS matched_listings,
                           count(DISTINCT retailer) AS retailers,
                           max(last_seen) AS latest_observation_date
                    FROM vw_competitor_price_current
                    WHERE match_status = 'matched'
                      AND is_available
                      AND current_price_inr > 0
                      AND city = ?
                    GROUP BY sku_code
                )
                SELECT top_skus.*,
                       competitor.lowest_competitor_price_inr,
                       top_skus.kestrel_mrp_inr - competitor.lowest_competitor_price_inr
                           AS price_gap_inr,
                       100.0 * (
                           top_skus.kestrel_mrp_inr
                               / nullif(competitor.lowest_competitor_price_inr, 0) - 1
                       ) AS mrp_premium_pct,
                       competitor.matched_listings,
                       competitor.retailers,
                       competitor.latest_observation_date
                FROM top_skus
                LEFT JOIN competitor USING (sku_code)
                ORDER BY dispatch_value_inr DESC
                """,
                parameters,
            ).fetchdf()
        frame.attrs["city"] = city
        frame.attrs["matched_top_skus"] = int(
            frame["lowest_competitor_price_inr"].notna().sum()
        )
        frame.attrs["methodology"] = (
            "Top SKUs use eligible estimated dispatch value in the selected period. Kestrel "
            "current MRP is compared with the lowest available, high-confidence BazaarPulse "
            "listing; this is a current observation, not historical price reconstruction."
        )
        return frame

    def competitor_match_quality(self) -> pd.DataFrame:
        """Entity-resolution coverage by city and governed outcome."""

        columns = ["city", "match_status", "listings", "average_confidence"]
        with self._connect() as connection:
            if not self._exists(connection, "ext_bazaarpulse_listing_current"):
                return pd.DataFrame(columns=columns)
            return connection.execute(
                """
                SELECT listing.city, match.status AS match_status,
                       count(*) AS listings, avg(match.confidence) AS average_confidence
                FROM ext_bazaarpulse_listing_current listing
                JOIN ext_bazaarpulse_match_current match USING (listing_id)
                GROUP BY listing.city, match.status
                ORDER BY listing.city, match.status
                """
            ).fetchdf()
