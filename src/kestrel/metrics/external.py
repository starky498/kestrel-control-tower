"""Governed metrics over freight and competitor observations.

External sources are optional at application start.  Every method therefore reports absence with
an empty, schema-stable frame instead of making the main operational dashboard unavailable.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal

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
                "competitor_history": self._exists(
                    connection, "ext_bazaarpulse_listing_history"
                ),
                "competitor_source_price_history": self._exists(
                    connection, "vw_competitor_source_price_history"
                ),
                "competitor_source_detail_failures": self._exists(
                    connection, "ext_bazaarpulse_source_detail_failure_current"
                ),
                "competitor_review_queue": self._exists(
                    connection, "vw_competitor_match_review_queue"
                ),
                "weather": self._exists(connection, "ext_weather_daily_current"),
                "holidays": self._exists(connection, "ext_india_holiday_current"),
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
        include_warehouse: bool = True,
        include_route: bool = True,
    ) -> tuple[str, list[Any]]:
        conditions = [f"{date_column} BETWEEN ? AND ?"]
        parameters: list[Any] = [filters.start_date, filters.end_date]
        dimensions: list[tuple[str, Iterable[str]]] = []
        if include_warehouse:
            dimensions.extend(
                [
                    (
                        f"{warehouse_alias}.warehouse_region_name",
                        filters.warehouse_regions,
                    ),
                    (f"{warehouse_alias}.warehouse_code", filters.warehouse_codes),
                ]
            )
        if include_route:
            dimensions.append((route_column, filters.route_codes))
        for column, values_iterable in dimensions:
            values = tuple(values_iterable)
            if values:
                conditions.append(f"{column} IN ({', '.join('?' for _ in values)})")
                parameters.extend(values)
        return " AND ".join(conditions), parameters

    @staticmethod
    def _ignored_freight_filters(
        filters: FilterSet,
        *,
        lens: str,
    ) -> tuple[str, ...]:
        ignored = []
        if filters.customer_regions:
            ignored.append("customer region")
        if filters.outlet_codes:
            ignored.append("outlet")
        if filters.channels:
            ignored.append("channel")
        if filters.promotion_codes:
            ignored.append("promotion")
        if filters.order_sources:
            ignored.append("order source")
        if lens == "warehouse" and filters.route_codes:
            ignored.append("route (independent route lens)")
        if lens in {"route", "carrier_route"}:
            if filters.warehouse_regions:
                ignored.append("DC region (independent warehouse lens)")
            if filters.warehouse_codes:
                ignored.append("warehouse (independent warehouse lens)")
        if lens == "carrier_warehouse" and filters.route_codes:
            ignored.append("route (independent route lens)")
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
            "settled_freight_cost_per_delivered_case_inr",
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
                include_route=False,
            )
            delivery_where, delivery_parameters = self._shared_freight_filters(
                filters,
                date_column="service.delivery_date",
                warehouse_alias="warehouse",
                route_column="service.route_code",
                include_route=False,
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
                       freight.paid_cost_inr
                           / nullif(delivered.delivered_case_equivalents, 0)
                           AS settled_freight_cost_per_delivered_case_inr,
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
        frame.attrs["ignored_filters"] = self._ignored_freight_filters(
            filters, lens="warehouse"
        )
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
            route_lens = bool(filters.route_codes)
            where, parameters = self._shared_freight_filters(
                filters,
                date_column="invoice.service_date",
                warehouse_alias="warehouse",
                route_column="invoice.route_code",
                include_warehouse=not route_lens,
                include_route=route_lens,
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
        frame.attrs["ignored_filters"] = self._ignored_freight_filters(
            filters,
            lens="carrier_route" if route_lens else "carrier_warehouse",
        )
        frame.attrs["attribution"] = (
            "Carrier invoice spend is valid; cost per case by carrier is unavailable because "
            "operational deliveries contain no carrier key."
        )
        return frame

    def freight_by_route(self, filters: FilterSet) -> pd.DataFrame:
        """Billed freight per delivered case-equivalent at independently aligned route grain."""

        columns = [
            "route_code",
            "route_name",
            "invoice_count",
            "freight_cost_inr",
            "detention_cost_inr",
            "paid_cost_inr",
            "pending_cost_inr",
            "disputed_cost_inr",
            "delivered_case_equivalents",
            "delivered_orders",
            "settled_freight_cost_per_delivered_case_inr",
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
                include_warehouse=False,
            )
            delivery_where, delivery_parameters = self._shared_freight_filters(
                filters,
                date_column="service.delivery_date",
                warehouse_alias="warehouse",
                route_column="service.route_code",
                include_warehouse=False,
            )
            frame = connection.execute(
                f"""
                WITH freight AS (
                    SELECT invoice.route_code,
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
                    GROUP BY invoice.route_code
                ), delivered AS (
                    SELECT service.route_code,
                           sum(service.delivered_case_equivalents)
                               AS delivered_case_equivalents,
                           count(*) AS delivered_orders
                    FROM fct_order_service service
                    JOIN dim_warehouse warehouse
                      ON warehouse.warehouse_code = service.warehouse_code
                    WHERE service.is_eligible_service AND {delivery_where}
                    GROUP BY service.route_code
                )
                SELECT coalesce(freight.route_code, delivered.route_code) AS route_code,
                       route.route_name,
                       freight.invoice_count,
                       freight.freight_cost_inr,
                       freight.detention_cost_inr,
                       freight.paid_cost_inr,
                       freight.pending_cost_inr,
                       freight.disputed_cost_inr,
                       delivered.delivered_case_equivalents,
                       delivered.delivered_orders,
                       freight.paid_cost_inr
                           / nullif(delivered.delivered_case_equivalents, 0)
                           AS settled_freight_cost_per_delivered_case_inr,
                       freight.freight_cost_inr
                           / nullif(delivered.delivered_case_equivalents, 0)
                           AS freight_cost_per_delivered_case_inr
                FROM freight
                FULL OUTER JOIN delivered USING (route_code)
                LEFT JOIN dim_route route
                  ON route.route_code = coalesce(freight.route_code, delivered.route_code)
                ORDER BY freight_cost_per_delivered_case_inr DESC NULLS LAST, route_code
                """,
                [*freight_parameters, *delivery_parameters],
            ).fetchdf()
        frame.attrs["ignored_filters"] = self._ignored_freight_filters(
            filters, lens="route"
        )
        frame.attrs["attribution"] = (
            "Invoice numerator and delivered-case denominator are independently aggregated at "
            "service period × route; no invoice-to-delivery linkage is claimed."
        )
        return frame

    def freight_route_performance(
        self,
        filters: FilterSet,
        *,
        min_invoices: int = 5,
        min_deliveries: int = 5,
        ranking: Literal["best", "most_improved", "worst"] = "best",
        limit: int = 10,
    ) -> pd.DataFrame:
        """Compare route cost/case with the immediately prior equivalent-length period."""

        if min_invoices <= 0 or min_deliveries <= 0:
            raise ValueError("min_invoices and min_deliveries must be positive")
        if ranking not in {"best", "most_improved", "worst"}:
            raise ValueError("ranking must be best, most_improved, or worst")
        if limit <= 0 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        period_days = (filters.end_date - filters.start_date).days + 1
        if period_days <= 0:
            raise ValueError("filters.end_date must be on or after filters.start_date")
        prior_end = filters.start_date - timedelta(days=1)
        prior_start = prior_end - timedelta(days=period_days - 1)
        prior_filters = replace(filters, start_date=prior_start, end_date=prior_end)
        current = self.freight_by_route(filters)
        prior = self.freight_by_route(prior_filters)
        columns = [
            "route_code",
            "route_name",
            "current_invoice_count",
            "prior_invoice_count",
            "current_delivered_orders",
            "prior_delivered_orders",
            "current_delivered_case_equivalents",
            "prior_delivered_case_equivalents",
            "current_freight_cost_per_delivered_case_inr",
            "prior_freight_cost_per_delivered_case_inr",
            "cost_per_case_delta_inr",
            "cost_per_case_delta_pct",
        ]
        if current.empty or prior.empty:
            frame = pd.DataFrame(columns=columns)
            frame.attrs["unavailable_reason"] = current.attrs.get(
                "unavailable_reason",
                prior.attrs.get(
                    "unavailable_reason",
                    "No route has evidence in both equivalent periods.",
                ),
            )
            return frame
        current_fields = current[
            [
                "route_code",
                "route_name",
                "invoice_count",
                "delivered_orders",
                "delivered_case_equivalents",
                "freight_cost_per_delivered_case_inr",
            ]
        ].rename(
            columns={
                "invoice_count": "current_invoice_count",
                "delivered_orders": "current_delivered_orders",
                "delivered_case_equivalents": "current_delivered_case_equivalents",
                "freight_cost_per_delivered_case_inr": (
                    "current_freight_cost_per_delivered_case_inr"
                ),
            }
        )
        prior_fields = prior[
            [
                "route_code",
                "invoice_count",
                "delivered_orders",
                "delivered_case_equivalents",
                "freight_cost_per_delivered_case_inr",
            ]
        ].rename(
            columns={
                "invoice_count": "prior_invoice_count",
                "delivered_orders": "prior_delivered_orders",
                "delivered_case_equivalents": "prior_delivered_case_equivalents",
                "freight_cost_per_delivered_case_inr": (
                    "prior_freight_cost_per_delivered_case_inr"
                ),
            }
        )
        frame = current_fields.merge(prior_fields, on="route_code", how="inner")
        frame = frame[
            (frame["current_invoice_count"] >= min_invoices)
            & (frame["prior_invoice_count"] >= min_invoices)
            & (frame["current_delivered_orders"] >= min_deliveries)
            & (frame["prior_delivered_orders"] >= min_deliveries)
        ].copy()
        frame["cost_per_case_delta_inr"] = (
            frame["current_freight_cost_per_delivered_case_inr"]
            - frame["prior_freight_cost_per_delivered_case_inr"]
        )
        frame["cost_per_case_delta_pct"] = 100.0 * frame[
            "cost_per_case_delta_inr"
        ] / frame["prior_freight_cost_per_delivered_case_inr"].replace(0, pd.NA)
        order = {
            "best": ("current_freight_cost_per_delivered_case_inr", True),
            "most_improved": ("cost_per_case_delta_inr", True),
            "worst": ("current_freight_cost_per_delivered_case_inr", False),
        }
        order_column, ascending = order[ranking]
        frame = frame.sort_values(
            [order_column, "route_code"], ascending=[ascending, True], na_position="last"
        ).head(limit)
        frame = frame[columns].reset_index(drop=True)
        frame.attrs["ranking"] = ranking
        frame.attrs["current_period"] = (filters.start_date, filters.end_date)
        frame.attrs["prior_period"] = (prior_start, prior_end)
        frame.attrs["ignored_filters"] = self._ignored_freight_filters(
            filters, lens="route"
        )
        frame.attrs["attribution"] = (
            "Current and prior invoice numerators and delivery denominators are independently "
            "aggregated at period × route. Lower cost/case is better; no carrier attribution "
            "or causal claim is made."
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
            ("promotion_code", filters.promotion_codes),
            ("source_system", filters.order_sources),
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
        retailer: str | None = None,
        as_of: date | None = None,
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
            "competitor_listing_id",
            "competitor_retailer",
            "competitor_raw_title",
            "observed_pack_value",
            "observed_pack_uom",
            "kestrel_pack_value",
            "kestrel_pack_uom",
            "pack_comparable",
            "unit_price_basis",
            "lowest_competitor_unit_price_inr",
            "kestrel_mrp_unit_inr",
            "unit_price_gap_inr",
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
            parameters.append(top_n)
            competitor_conditions = [
                "current.match_status = 'matched'",
                "current.is_available",
                "current.current_price_inr > 0",
                "current.city = ?",
            ]
            parameters.append(city)
            if retailer:
                competitor_conditions.append("current.retailer = ?")
                parameters.append(retailer)
            if as_of:
                competitor_conditions.append("current.last_seen <= ?")
                parameters.append(as_of)
            frame = connection.execute(
                f"""
                WITH top_skus AS (
                    SELECT line.sku_code, line.product_id,
                           any_value(line.product_name) AS product_name,
                           any_value(line.category) AS category,
                           sum(line.estimated_dispatch_value_inr) AS dispatch_value_inr,
                           any_value(product.current_mrp_inr) AS kestrel_mrp_inr,
                           any_value(product.pack_size_value) AS kestrel_pack_value,
                           any_value(product.pack_size_uom) AS kestrel_pack_uom
                    FROM fct_order_line line
                    JOIN dim_product product ON product.product_id = line.product_id
                    WHERE line.is_eligible_service AND {where}{category_sql}
                    GROUP BY line.sku_code, line.product_id
                    ORDER BY dispatch_value_inr DESC
                    LIMIT ?
                ), normalized_competitor AS (
                    SELECT current.*,
                           product.pack_size_value AS kestrel_pack_value,
                           product.pack_size_uom AS kestrel_pack_uom,
                           CASE upper(current.observed_pack_uom)
                               WHEN 'KG' THEN current.observed_pack_value * 1000
                               WHEN 'L' THEN current.observed_pack_value * 1000
                               WHEN 'G' THEN current.observed_pack_value
                               WHEN 'ML' THEN current.observed_pack_value
                           END AS observed_base_quantity,
                           CASE upper(product.pack_size_uom)
                               WHEN 'KG' THEN product.pack_size_value * 1000
                               WHEN 'L' THEN product.pack_size_value * 1000
                               WHEN 'G' THEN product.pack_size_value
                               WHEN 'ML' THEN product.pack_size_value
                           END AS kestrel_base_quantity,
                           CASE
                               WHEN upper(current.observed_pack_uom) IN ('G', 'KG') THEN 'G'
                               WHEN upper(current.observed_pack_uom) IN ('ML', 'L') THEN 'ML'
                           END AS observed_base_uom,
                           CASE
                               WHEN upper(product.pack_size_uom) IN ('G', 'KG') THEN 'G'
                               WHEN upper(product.pack_size_uom) IN ('ML', 'L') THEN 'ML'
                           END AS kestrel_base_uom
                    FROM vw_competitor_price_current current
                    JOIN dim_product product ON product.product_id = current.product_id
                    WHERE {" AND ".join(competitor_conditions)}
                ), comparable_competitor AS (
                    SELECT *,
                           current_price_inr * 100.0 / observed_base_quantity
                               AS competitor_unit_price_inr,
                           count(*) OVER (PARTITION BY sku_code) AS matched_listings
                    FROM normalized_competitor
                    WHERE observed_base_quantity > 0
                      AND kestrel_base_quantity > 0
                      AND observed_base_uom = kestrel_base_uom
                      AND abs(observed_base_quantity - kestrel_base_quantity) < 1e-6
                ), retailer_counts AS (
                    SELECT sku_code, count(DISTINCT retailer) AS retailers
                    FROM comparable_competitor
                    GROUP BY sku_code
                ), competitor AS (
                    SELECT comparable.*, counts.retailers
                    FROM comparable_competitor comparable
                    JOIN retailer_counts counts USING (sku_code)
                    QUALIFY row_number() OVER (
                        PARTITION BY sku_code
                        ORDER BY competitor_unit_price_inr ASC,
                                 last_seen DESC,
                                 retailer,
                                 listing_id
                    ) = 1
                )
                SELECT top_skus.sku_code,
                       top_skus.product_name,
                       top_skus.category,
                       top_skus.dispatch_value_inr,
                       top_skus.kestrel_mrp_inr,
                       competitor.current_price_inr AS lowest_competitor_price_inr,
                       top_skus.kestrel_mrp_inr - competitor.current_price_inr
                           AS price_gap_inr,
                       100.0 * (
                           top_skus.kestrel_mrp_inr
                               / nullif(competitor.current_price_inr, 0) - 1
                       ) AS mrp_premium_pct,
                       competitor.matched_listings,
                       competitor.retailers,
                       competitor.last_seen AS latest_observation_date,
                       competitor.listing_id AS competitor_listing_id,
                       competitor.retailer AS competitor_retailer,
                       competitor.raw_title AS competitor_raw_title,
                       competitor.observed_pack_value,
                       competitor.observed_pack_uom,
                       top_skus.kestrel_pack_value,
                       top_skus.kestrel_pack_uom,
                       competitor.listing_id IS NOT NULL AS pack_comparable,
                       CASE WHEN competitor.listing_id IS NOT NULL
                            THEN '100 ' || competitor.observed_base_uom END
                           AS unit_price_basis,
                       competitor.competitor_unit_price_inr
                           AS lowest_competitor_unit_price_inr,
                       top_skus.kestrel_mrp_inr * 100.0
                           / nullif(competitor.kestrel_base_quantity, 0)
                           AS kestrel_mrp_unit_inr,
                       top_skus.kestrel_mrp_inr * 100.0
                           / nullif(competitor.kestrel_base_quantity, 0)
                           - competitor.competitor_unit_price_inr
                           AS unit_price_gap_inr
                FROM top_skus
                LEFT JOIN competitor USING (sku_code)
                ORDER BY dispatch_value_inr DESC
                """,
                parameters,
            ).fetchdf()
        frame.attrs["city"] = city
        frame.attrs["retailer"] = retailer
        frame.attrs["as_of"] = as_of
        frame.attrs["matched_top_skus"] = int(
            frame["lowest_competitor_price_inr"].notna().sum()
        )
        frame.attrs["methodology"] = (
            "Top SKUs use eligible estimated dispatch value in the selected period. Kestrel "
            "current MRP is compared with one retained lowest comparable-unit, available, "
            "high-confidence BazaarPulse listing. Both packs must resolve to the same base unit "
            "and exact normalized quantity; missing/non-comparable candidates remain unmatched. "
            "The retained listing and retailer provide row-level evidence. This is a current "
            "observation, not historical price reconstruction."
        )
        return frame

    def competitor_current_comparable_matches(
        self,
        *,
        city: str = "Mumbai",
        retailer: str | None = None,
        category: str | None = None,
        as_of: date | None = None,
        limit: int = 1_000,
    ) -> pd.DataFrame:
        """Return every qualifying current listing in a city, not only top dispatch SKUs."""

        self._validate_read_limit(limit)
        columns = [
            "listing_id",
            "city",
            "retailer",
            "raw_title",
            "sku_code",
            "product_name",
            "category",
            "match_confidence",
            "match_provenance",
            "current_price_inr",
            "kestrel_mrp_inr",
            "observed_pack_value",
            "observed_pack_uom",
            "kestrel_pack_value",
            "kestrel_pack_uom",
            "unit_price_basis",
            "competitor_unit_price_inr",
            "kestrel_mrp_unit_inr",
            "unit_price_gap_inr",
            "mrp_premium_pct",
            "last_seen",
        ]
        conditions = [
            "current.match_status = 'matched'",
            "current.is_available",
            "current.current_price_inr > 0",
            "current.city = ?",
        ]
        parameters: list[Any] = [city]
        if retailer:
            conditions.append("current.retailer = ?")
            parameters.append(retailer)
        if category:
            conditions.append("product.category = ?")
            parameters.append(category)
        if as_of:
            conditions.append("current.last_seen <= ?")
            parameters.append(as_of)
        parameters.append(limit)
        with self._connect() as connection:
            if not self._exists(connection, "ext_bazaarpulse_listing_current"):
                frame = pd.DataFrame(columns=columns)
                frame.attrs["unavailable_reason"] = "Run `make scrape-prices` first."
                return frame
            frame = connection.execute(
                f"""
                WITH normalized AS (
                    SELECT current.*,
                           product.product_name,
                           product.category,
                           product.current_mrp_inr AS kestrel_mrp_inr,
                           product.pack_size_value AS kestrel_pack_value,
                           product.pack_size_uom AS kestrel_pack_uom,
                           CASE upper(current.observed_pack_uom)
                               WHEN 'KG' THEN current.observed_pack_value * 1000
                               WHEN 'L' THEN current.observed_pack_value * 1000
                               WHEN 'G' THEN current.observed_pack_value
                               WHEN 'ML' THEN current.observed_pack_value
                           END AS observed_base_quantity,
                           CASE upper(product.pack_size_uom)
                               WHEN 'KG' THEN product.pack_size_value * 1000
                               WHEN 'L' THEN product.pack_size_value * 1000
                               WHEN 'G' THEN product.pack_size_value
                               WHEN 'ML' THEN product.pack_size_value
                           END AS kestrel_base_quantity,
                           CASE
                               WHEN upper(current.observed_pack_uom) IN ('G', 'KG') THEN 'G'
                               WHEN upper(current.observed_pack_uom) IN ('ML', 'L') THEN 'ML'
                           END AS observed_base_uom,
                           CASE
                               WHEN upper(product.pack_size_uom) IN ('G', 'KG') THEN 'G'
                               WHEN upper(product.pack_size_uom) IN ('ML', 'L') THEN 'ML'
                           END AS kestrel_base_uom
                    FROM vw_competitor_price_current current
                    JOIN dim_product product ON product.product_id = current.product_id
                    WHERE {" AND ".join(conditions)}
                )
                SELECT listing_id, city, retailer, raw_title, sku_code, product_name,
                       category, match_confidence, match_provenance, current_price_inr,
                       kestrel_mrp_inr, observed_pack_value, observed_pack_uom,
                       kestrel_pack_value, kestrel_pack_uom,
                       '100 ' || observed_base_uom AS unit_price_basis,
                       current_price_inr * 100.0 / observed_base_quantity
                           AS competitor_unit_price_inr,
                       kestrel_mrp_inr * 100.0 / kestrel_base_quantity
                           AS kestrel_mrp_unit_inr,
                       kestrel_mrp_inr * 100.0 / kestrel_base_quantity
                           - current_price_inr * 100.0 / observed_base_quantity
                           AS unit_price_gap_inr,
                       100.0 * (
                           (kestrel_mrp_inr / kestrel_base_quantity)
                               / nullif(current_price_inr / observed_base_quantity, 0) - 1
                       ) AS mrp_premium_pct,
                       last_seen
                FROM normalized
                WHERE observed_base_quantity > 0
                  AND kestrel_base_quantity > 0
                  AND observed_base_uom = kestrel_base_uom
                  AND abs(observed_base_quantity - kestrel_base_quantity) < 1e-6
                ORDER BY sku_code, competitor_unit_price_inr, retailer, listing_id
                LIMIT ?
                """,
                parameters,
            ).fetchdf()
        frame.attrs["scope"] = (
            "All current available governed matches in the selected city/retailer/category "
            "whose normalized pack quantity and unit family exactly match Kestrel."
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

    def competitor_source_price_coverage(
        self,
        *,
        city: str | None = None,
    ) -> pd.DataFrame:
        """Coverage of true source-dated history, separate from scrape replay audit."""

        columns = [
            "city",
            "retailer",
            "source_price_observations",
            "listings",
            "coverage_start",
            "coverage_end",
        ]
        with self._connect() as connection:
            if not self._exists(connection, "ext_bazaarpulse_source_price_observation"):
                frame = pd.DataFrame(columns=columns)
                frame.attrs["unavailable_reason"] = (
                    "No BazaarPulse detail-page source history is available."
                )
                return frame
            where = "WHERE city = ?" if city else ""
            parameters: list[Any] = [city] if city else []
            frame = connection.execute(
                f"""
                SELECT city, retailer, count(*) AS source_price_observations,
                       count(DISTINCT listing_id) AS listings,
                       min(observed_on) AS coverage_start,
                       max(observed_on) AS coverage_end
                FROM ext_bazaarpulse_source_price_observation
                {where}
                GROUP BY city, retailer
                ORDER BY city, retailer
                """,
                parameters,
            ).fetchdf()
        frame.attrs["history_grain"] = (
            "One source-published price per BazaarPulse listing and observed_on date; this is "
            "not the collection-run audit history."
        )
        return frame

    def competitor_source_detail_failures(self) -> pd.DataFrame:
        """Return structured current detail-page failures for visible coverage governance."""

        columns = [
            "listing_id",
            "source_path",
            "failure_type",
            "message",
            "collected_at_utc",
            "sync_id",
        ]
        with self._connect() as connection:
            if not self._exists(
                connection, "ext_bazaarpulse_source_detail_failure_current"
            ):
                return pd.DataFrame(columns=columns)
            return connection.execute(
                f"""
                SELECT {", ".join(columns)}
                FROM ext_bazaarpulse_source_detail_failure_current
                ORDER BY failure_type, listing_id
                """
            ).fetchdf()

    @staticmethod
    def _validate_read_limit(limit: int) -> None:
        if limit <= 0 or limit > 1_000:
            raise ValueError("limit must be between 1 and 1000")

    def competitor_review_queue(
        self,
        *,
        city: str | None = None,
        limit: int = 100,
    ) -> pd.DataFrame:
        """Return unresolved current listings without promoting them into price metrics."""

        self._validate_read_limit(limit)
        columns = [
            "listing_id",
            "city",
            "retailer",
            "raw_title",
            "observed_brand",
            "observed_category",
            "observed_pack_value",
            "observed_pack_uom",
            "current_price_inr",
            "last_seen",
            "collected_at_utc",
            "match_status",
            "match_confidence",
            "runner_up_confidence",
            "suggested_product_id",
            "suggested_sku_code",
            "algorithm_reason",
            "sync_id",
            "observation_id",
        ]
        with self._connect() as connection:
            if not self._exists(connection, "vw_competitor_match_review_queue"):
                frame = pd.DataFrame(columns=columns)
                frame.attrs["unavailable_reason"] = "Run `make scrape-prices` first."
                return frame
            city_sql = ""
            parameters: list[Any] = []
            if city:
                city_sql = "WHERE city = ?"
                parameters.append(city)
            parameters.append(limit)
            frame = connection.execute(
                f"""
                SELECT {", ".join(columns)}
                FROM vw_competitor_match_review_queue
                {city_sql}
                ORDER BY match_status, match_confidence DESC, listing_id
                LIMIT ?
                """,
                parameters,
            ).fetchdf()
        frame.attrs["governance"] = (
            "Only ambiguous, low-confidence or no-candidate current listings appear here; "
            "none contribute to competitor price metrics."
        )
        return frame

    def competitor_matched_listing_catalog(
        self,
        *,
        city: str | None = None,
        retailer: str | None = None,
        category: str | None = None,
        limit: int = 1_000,
    ) -> pd.DataFrame:
        """Return all governed current matches for complete history selection."""

        self._validate_read_limit(limit)
        columns = [
            "listing_id",
            "city",
            "retailer",
            "raw_title",
            "sku_code",
            "category",
            "match_status",
            "match_confidence",
            "match_provenance",
            "is_available",
            "last_seen",
        ]
        conditions = ["current.match_status = 'matched'"]
        parameters: list[Any] = []
        if city:
            conditions.append("current.city = ?")
            parameters.append(city)
        if retailer:
            conditions.append("current.retailer = ?")
            parameters.append(retailer)
        if category:
            conditions.append("product.category = ?")
            parameters.append(category)
        parameters.append(limit)
        with self._connect() as connection:
            if not self._exists(connection, "vw_competitor_price_current"):
                frame = pd.DataFrame(columns=columns)
                frame.attrs["unavailable_reason"] = "No competitor snapshot is available."
                return frame
            return connection.execute(
                f"""
                SELECT current.listing_id, current.city, current.retailer,
                       current.raw_title, current.sku_code, product.category,
                       current.match_status, current.match_confidence,
                       current.match_provenance, current.is_available, current.last_seen
                FROM vw_competitor_price_current current
                JOIN dim_product product ON product.product_id = current.product_id
                WHERE {" AND ".join(conditions)}
                ORDER BY current.retailer, current.listing_id
                LIMIT ?
                """,
                parameters,
            ).fetchdf()

    def competitor_observation_history(
        self,
        listing_id: str,
        *,
        limit: int = 100,
    ) -> pd.DataFrame:
        """Return provenance-rich observation and match history for one listing ID."""

        self._validate_read_limit(limit)
        if not listing_id.strip():
            raise ValueError("listing_id cannot be empty")
        columns = [
            "listing_id",
            "city",
            "retailer",
            "raw_title",
            "current_price_inr",
            "is_available",
            "last_seen",
            "collected_at_utc",
            "sync_id",
            "observation_id",
            "match_status",
            "match_confidence",
            "runner_up_confidence",
            "product_id",
            "sku_code",
            "suggested_product_id",
            "suggested_sku_code",
            "match_provenance",
            "algorithm_status",
            "algorithm_reason",
            "match_reason",
            "decision_source",
            "reviewer",
            "reviewed_on",
            "review_note",
        ]
        with self._connect() as connection:
            if not self._exists(connection, "vw_competitor_price_history"):
                frame = pd.DataFrame(columns=columns)
                frame.attrs["unavailable_reason"] = "No competitor history is available."
                return frame
            return connection.execute(
                f"""
                SELECT {", ".join(columns)}
                FROM vw_competitor_price_history
                WHERE listing_id = ?
                ORDER BY collected_at_utc DESC, observation_id DESC
                LIMIT ?
                """,
                [listing_id.strip(), limit],
            ).fetchdf()

    def competitor_source_price_history(
        self,
        *,
        city: str | None = None,
        retailer: str | None = None,
        listing_id: str | None = None,
        sku_code: str | None = None,
        observed_from: date | None = None,
        as_of: date | None = None,
        matched_only: bool = False,
        limit: int = 1_000,
    ) -> pd.DataFrame:
        """Return source-dated prices with effective MRP and match/pack evidence."""

        self._validate_read_limit(limit)
        if observed_from is not None and as_of is not None and observed_from > as_of:
            raise ValueError("observed_from cannot be after as_of")
        columns = [
            "source_price_observation_id",
            "listing_id",
            "city",
            "retailer",
            "raw_title",
            "observed_category",
            "observed_on",
            "observed_price_inr",
            "source_path",
            "observed_pack_value",
            "observed_pack_uom",
            "product_id",
            "sku_code",
            "product_name",
            "kestrel_category",
            "kestrel_pack_value",
            "kestrel_pack_uom",
            "pack_comparable",
            "unit_price_basis",
            "observed_unit_price_inr",
            "historical_kestrel_mrp_inr",
            "historical_kestrel_mrp_unit_inr",
            "historical_unit_price_gap_inr",
            "historical_mrp_premium_pct",
            "mrp_history_available",
            "price_history_id",
            "mrp_effective_from",
            "mrp_effective_to",
            "match_status",
            "match_confidence",
            "runner_up_confidence",
            "suggested_product_id",
            "suggested_sku_code",
            "match_provenance",
            "algorithm_status",
            "algorithm_reason",
            "decision_source",
            "reviewer",
            "reviewed_on",
            "review_note",
            "first_collected_at_utc",
            "first_sync_id",
        ]
        conditions: list[str] = []
        parameters: list[Any] = []
        filters = (
            ("city", city),
            ("retailer", retailer),
            ("listing_id", listing_id.strip() if listing_id else None),
            ("sku_code", sku_code.strip() if sku_code else None),
        )
        for column, value in filters:
            if value:
                conditions.append(f"{column} = ?")
                parameters.append(value)
        if observed_from is not None:
            conditions.append("observed_on >= ?")
            parameters.append(observed_from)
        if as_of is not None:
            conditions.append("observed_on <= ?")
            parameters.append(as_of)
        if matched_only:
            conditions.append("match_status = 'matched'")
        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        with self._connect() as connection:
            if not self._exists(connection, "vw_competitor_source_price_history"):
                frame = pd.DataFrame(columns=columns)
                frame.attrs["unavailable_reason"] = (
                    "Run `make scrape-prices` to collect allowed product detail pages."
                )
                return frame
            parameters.append(limit)
            frame = connection.execute(
                f"""
                SELECT {", ".join(columns)}
                FROM vw_competitor_source_price_history
                {where}
                ORDER BY observed_on DESC, city, retailer, listing_id
                LIMIT ?
                """,
                parameters,
            ).fetchdf()
        frame.attrs["history_grain"] = (
            "Source history is one dated shelf-price row per listing, not one row per scrape."
        )
        frame.attrs["mrp_methodology"] = (
            "Kestrel MRP is resolved from raw_product_price_history where the observation date "
            "falls inside the inclusive effective window. Missing windows remain null; current "
            "MRP is never substituted."
        )
        frame.attrs["pack_methodology"] = (
            "Price gaps use 100 G or 100 ML comparable-unit prices only when both pack sizes and "
            "unit families are known. Non-comparable rows remain explicit."
        )
        return frame

    def competitor_service_price_attention(
        self,
        filters: FilterSet,
        *,
        city: str = "Mumbai",
        retailer: str | None = None,
        as_of: date | None = None,
        category: str | None = None,
        top_n: int = 20,
    ) -> pd.DataFrame:
        """Place service exposure beside latest as-of price evidence without causal claims."""

        if top_n <= 0 or top_n > 100:
            raise ValueError("top_n must be between 1 and 100")
        columns = [
            "sku_code",
            "product_name",
            "category",
            "eligible_orders",
            "order_lines",
            "ordered_case_equivalents",
            "delivered_case_equivalents",
            "short_case_equivalents",
            "line_fill_rate_pct",
            "short_delivery_value_exposure_inr",
            "estimated_dispatch_value_inr",
            "listing_id",
            "retailer",
            "observed_on",
            "observed_price_inr",
            "observed_pack_value",
            "observed_pack_uom",
            "kestrel_pack_value",
            "kestrel_pack_uom",
            "pack_comparable",
            "unit_price_basis",
            "observed_unit_price_inr",
            "historical_kestrel_mrp_inr",
            "historical_kestrel_mrp_unit_inr",
            "historical_unit_price_gap_inr",
            "historical_mrp_premium_pct",
            "price_evidence_status",
        ]
        with self._connect() as connection:
            if not self._exists(connection, "vw_competitor_source_price_history"):
                frame = pd.DataFrame(columns=columns)
                frame.attrs["unavailable_reason"] = (
                    "No governed BazaarPulse source price history is available."
                )
                return frame
            where, parameters = self._order_filter_sql(filters)
            category_condition = ""
            if category:
                category_condition = " AND line.category = ?"
                parameters.append(category)
            source_conditions = ["match_status = 'matched'", "city = ?"]
            source_parameters: list[Any] = [city]
            if retailer:
                source_conditions.append("retailer = ?")
                source_parameters.append(retailer)
            if as_of:
                source_conditions.append("observed_on <= ?")
                source_parameters.append(as_of)
            frame = connection.execute(
                f"""
                WITH service AS (
                    SELECT line.sku_code,
                           any_value(line.product_name) AS product_name,
                           any_value(line.category) AS category,
                           count(DISTINCT line.order_id) AS eligible_orders,
                           count(*) AS order_lines,
                           sum(line.ordered_case_equivalents)
                               AS ordered_case_equivalents,
                           sum(line.delivered_case_equivalents)
                               AS delivered_case_equivalents,
                           sum(line.short_case_equivalents) AS short_case_equivalents,
                           100.0 * sum(least(
                               line.delivered_case_equivalents,
                               line.ordered_case_equivalents
                           ))
                               / nullif(sum(line.ordered_case_equivalents), 0)
                               AS line_fill_rate_pct,
                           sum(line.short_delivery_value_exposure_inr)
                               AS short_delivery_value_exposure_inr,
                           sum(line.estimated_dispatch_value_inr)
                               AS estimated_dispatch_value_inr
                    FROM fct_order_line line
                    WHERE line.is_eligible_service AND {where}{category_condition}
                    GROUP BY line.sku_code
                ), latest_per_listing AS (
                    SELECT *
                    FROM vw_competitor_source_price_history
                    WHERE {" AND ".join(source_conditions)}
                    QUALIFY row_number() OVER (
                        PARTITION BY listing_id
                        ORDER BY observed_on DESC, source_price_observation_id DESC
                    ) = 1
                ), best_evidence AS (
                    SELECT *
                    FROM latest_per_listing
                    QUALIFY row_number() OVER (
                        PARTITION BY sku_code
                        ORDER BY pack_comparable DESC,
                                 mrp_history_available DESC,
                                 observed_unit_price_inr ASC NULLS LAST,
                                 observed_on DESC,
                                 listing_id
                    ) = 1
                )
                SELECT service.*,
                       evidence.listing_id,
                       evidence.retailer,
                       evidence.observed_on,
                       evidence.observed_price_inr,
                       evidence.observed_pack_value,
                       evidence.observed_pack_uom,
                       evidence.kestrel_pack_value,
                       evidence.kestrel_pack_uom,
                       evidence.pack_comparable,
                       evidence.unit_price_basis,
                       evidence.observed_unit_price_inr,
                       evidence.historical_kestrel_mrp_inr,
                       evidence.historical_kestrel_mrp_unit_inr,
                       evidence.historical_unit_price_gap_inr,
                       evidence.historical_mrp_premium_pct,
                       CASE
                           WHEN evidence.listing_id IS NULL
                               THEN 'NO_MATCHED_SOURCE_OBSERVATION'
                           WHEN NOT evidence.pack_comparable
                               THEN 'PACK_NOT_COMPARABLE'
                           WHEN NOT evidence.mrp_history_available
                               THEN 'MISSING_EFFECTIVE_DATED_MRP'
                           ELSE 'COMPARABLE'
                       END AS price_evidence_status
                FROM service
                LEFT JOIN best_evidence evidence USING (sku_code)
                ORDER BY service.short_delivery_value_exposure_inr DESC,
                         service.short_case_equivalents DESC,
                         service.sku_code
                LIMIT ?
                """,
                [*parameters, *source_parameters, top_n],
            ).fetchdf()
        frame.attrs["city"] = city
        frame.attrs["retailer"] = retailer
        frame.attrs["as_of"] = as_of
        frame.attrs["methodology"] = (
            "Service and source-price evidence are independently aggregated/selected by SKU. "
            "The latest observation per listing at or before as_of is retained, then the most "
            "defensible comparable evidence is chosen. This is an attention queue, not evidence "
            "that price caused fill loss."
        )
        return frame
