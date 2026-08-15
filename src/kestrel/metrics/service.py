"""Deterministic queries used by both the dashboard and Ask Kestrel."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd


class QuantityBasis(StrEnum):
    EACHES = "eaches"
    CASE_EQUIVALENTS = "case_equivalents"


@dataclass(frozen=True)
class FilterSet:
    start_date: date
    end_date: date
    customer_regions: tuple[str, ...] = ()
    warehouse_regions: tuple[str, ...] = ()
    warehouse_codes: tuple[str, ...] = ()
    route_codes: tuple[str, ...] = ()
    outlet_codes: tuple[str, ...] = ()
    channels: tuple[str, ...] = ()
    promotion_codes: tuple[str, ...] = ()
    order_sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class MetricValue:
    key: str
    value: float | None
    numerator: float | None
    denominator: float | None
    unit: str
    records: int


ORDER_DIMENSIONS: dict[str, tuple[str, str]] = {
    "customer_region": ("customer_region_name", "Customer region"),
    "warehouse_region": ("warehouse_region_name", "DC region"),
    "warehouse": ("warehouse_code", "Warehouse"),
    "route": ("route_code", "Route"),
    "outlet": ("outlet_code", "Outlet"),
    "channel": ("channel", "Channel"),
    "promotion": ("promotion_code", "Recorded promotion code"),
    "promotion_mechanic": ("promotion_mechanic", "Recorded promotion mechanic"),
    "order_source": ("source_system", "Order source"),
}

LINE_DIMENSIONS: dict[str, tuple[str, str]] = {
    **ORDER_DIMENSIONS,
    "category": ("category", "Category"),
    "sku": ("sku_code", "SKU"),
    "short_reason": ("short_reason_code", "Short reason"),
}

RETURN_DIMENSIONS: dict[str, tuple[str, str]] = {
    "customer_region": ("customer_region_name", "Customer region"),
    "warehouse_region": ("warehouse_region_name", "DC region"),
    "warehouse": ("warehouse_code", "Warehouse"),
    "route": ("route_code", "Route"),
    "outlet": ("outlet_code", "Outlet"),
    "channel": ("channel", "Channel"),
    "category": ("category", "Category"),
    "sku": ("sku_code", "SKU"),
    "reason": ("return_reason_code", "Return reason"),
    "disposition": ("disposition", "Disposition"),
    "status": ("credit_note_status", "Credit status"),
    "promotion": ("promotion_code", "Recorded promotion code"),
    "promotion_mechanic": ("promotion_mechanic", "Recorded promotion mechanic"),
    "order_source": ("source_system", "Order source"),
}

DELIVERY_EXCEPTION_DIMENSIONS: dict[str, tuple[str, str]] = {
    "route": ("route_code", "Route"),
    "warehouse": ("warehouse_code", "Warehouse"),
    "warehouse_region": ("warehouse_region_name", "DC region"),
    "customer_region": ("customer_region_name", "Customer region"),
    "outlet": ("outlet_code", "Outlet"),
    "channel": ("channel", "Channel"),
    "telematics_vendor": ("telematics_vendor", "Telematics vendor"),
    "promotion": ("promotion_code", "Recorded promotion code"),
    "order_source": ("source_system", "Order source"),
}

COLD_CHAIN_DIMENSIONS: dict[str, tuple[str, str]] = {
    "customer_region": ("customer_region_name", "Customer region"),
    "warehouse_region": ("warehouse_region_name", "DC region"),
    "warehouse": ("warehouse_code", "Warehouse"),
    "route": ("route_code", "Route"),
    "outlet": ("outlet_code", "Outlet"),
    "channel": ("channel", "Channel"),
    "promotion": ("promotion_code", "Recorded promotion code"),
    "order_source": ("source_system", "Order source"),
    "category": ("category", "Chilled category"),
    "month": ("date_trunc('month', delivery_date)::DATE", "Delivery month"),
}

_DATE_RANGE_SOURCES: dict[str, tuple[str, str]] = {
    "requested_delivery": ("fct_order_service", "requested_delivery_date"),
    "actual_delivery": ("fct_delivery", "delivery_date"),
    "returns": ("fct_return_credit_note", "return_date"),
    "inventory": ("fct_inventory_snapshot", "snapshot_date"),
}


class AnalyticsService:
    """Read-only semantic service with allow-listed filters and dimensions."""

    def __init__(self, database_path: Path, *, near_expiry_days: int = 30) -> None:
        self.database_path = Path(database_path)
        self.near_expiry_days = near_expiry_days

    def _connect(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(str(self.database_path), read_only=True)

    @staticmethod
    def _number(value: Any) -> float | None:
        return float(value) if value is not None else None

    @staticmethod
    def _ratio(numerator: Any, denominator: Any) -> float | None:
        if numerator is None or denominator in (None, 0):
            return None
        return float(numerator) / float(denominator)

    @staticmethod
    def _filter_sql(
        filters: FilterSet,
        *,
        date_column: str,
        prefix: str = "",
        include_dimensions: bool = True,
    ) -> tuple[str, list[Any]]:
        qualifier = f"{prefix}." if prefix else ""
        conditions = [f"{qualifier}{date_column} BETWEEN ? AND ?"]
        parameters: list[Any] = [filters.start_date, filters.end_date]
        if include_dimensions:
            dimension_filters: tuple[tuple[str, Iterable[str]], ...] = (
                ("customer_region_name", filters.customer_regions),
                ("warehouse_region_name", filters.warehouse_regions),
                ("warehouse_code", filters.warehouse_codes),
                ("route_code", filters.route_codes),
                ("outlet_code", filters.outlet_codes),
                ("channel", filters.channels),
                ("promotion_code", filters.promotion_codes),
                ("source_system", filters.order_sources),
            )
            for column, values_iterable in dimension_filters:
                values = tuple(values_iterable)
                if values:
                    placeholders = ", ".join("?" for _ in values)
                    conditions.append(f"{qualifier}{column} IN ({placeholders})")
                    parameters.extend(values)
        return " AND ".join(conditions), parameters

    @staticmethod
    def _as_of_filter_sql(
        filters: FilterSet,
        *,
        date_column: str,
        prefix: str = "",
    ) -> tuple[str, list[Any]]:
        """Build a dimension-safe point-in-time filter ending on the selected date."""

        qualifier = f"{prefix}." if prefix else ""
        conditions = [f"{qualifier}{date_column} <= ?"]
        parameters: list[Any] = [filters.end_date]
        dimension_filters: tuple[tuple[str, Iterable[str]], ...] = (
            ("customer_region_name", filters.customer_regions),
            ("warehouse_region_name", filters.warehouse_regions),
            ("warehouse_code", filters.warehouse_codes),
            ("route_code", filters.route_codes),
            ("outlet_code", filters.outlet_codes),
            ("channel", filters.channels),
            ("promotion_code", filters.promotion_codes),
            ("source_system", filters.order_sources),
        )
        for column, values_iterable in dimension_filters:
            values = tuple(values_iterable)
            if values:
                placeholders = ", ".join("?" for _ in values)
                conditions.append(f"{qualifier}{column} IN ({placeholders})")
                parameters.extend(values)
        return " AND ".join(conditions), parameters

    def available_date_range(
        self, date_basis: str = "requested_delivery"
    ) -> tuple[date, date]:
        """Return the observed range for an allow-listed governed date basis."""

        try:
            table, date_column = _DATE_RANGE_SOURCES[date_basis]
        except KeyError:
            raise ValueError(f"Unsupported date basis: {date_basis}") from None
        with self._connect() as connection:
            minimum, maximum = connection.execute(
                f"SELECT min({date_column}), max({date_column}) FROM {table}"
            ).fetchone()
        return minimum, maximum

    def filter_options(self) -> dict[str, list[str]]:
        queries = {
            "customer_regions": ("fct_order_service", "customer_region_name"),
            "warehouse_regions": ("fct_order_service", "warehouse_region_name"),
            "warehouse_codes": ("fct_order_service", "warehouse_code"),
            "route_codes": ("fct_order_service", "route_code"),
            "outlet_codes": ("fct_order_service", "outlet_code"),
            "channels": ("fct_order_service", "channel"),
            "promotion_codes": ("fct_order_service", "promotion_code"),
            "order_sources": ("fct_order_service", "source_system"),
            "categories": ("fct_order_line", "category"),
        }
        options: dict[str, list[str]] = {}
        with self._connect() as connection:
            for key, (table, column) in queries.items():
                rows = connection.execute(
                    f"SELECT DISTINCT {column} FROM {table} "
                    f"WHERE {column} IS NOT NULL ORDER BY {column}"
                ).fetchall()
                options[key] = [row[0] for row in rows]
        return options

    def executive_summary(
        self, filters: FilterSet, basis: QuantityBasis = QuantityBasis.EACHES
    ) -> dict[str, MetricValue]:
        quantity_suffix = "eaches" if basis == QuantityBasis.EACHES else "case_equivalents"
        where, parameters = self._filter_sql(
            filters, date_column="requested_delivery_date"
        )
        service_sql = f"""
            SELECT
                count(*) AS records,
                sum(capped_delivered_{quantity_suffix}) AS capped_delivered_qty,
                sum(delivered_{quantity_suffix}) AS raw_delivered_qty,
                sum(allocated_{quantity_suffix}) AS allocated_qty,
                sum(ordered_{quantity_suffix}) AS ordered_qty,
                sum(capped_delivered_eaches) AS delivered_eaches,
                sum(ordered_eaches) AS ordered_eaches,
                sum(capped_delivered_case_equivalents) AS delivered_case_equivalents,
                sum(ordered_case_equivalents) AS ordered_case_equivalents,
                count(*) FILTER (
                    WHERE planned_arrival_ts IS NOT NULL
                      AND actual_arrival_ts IS NOT NULL
                ) AS timestamp_eligible_orders,
                count(*) FILTER (
                    WHERE planned_arrival_ts IS NOT NULL
                      AND actual_arrival_ts IS NOT NULL
                      AND on_time_by_timestamp
                ) AS on_time_orders,
                count(*) FILTER (WHERE strict_otif) AS otif_orders,
                count(*) FILTER (
                    WHERE planned_arrival_ts IS NOT NULL
                      AND actual_arrival_ts IS NOT NULL
                      AND late_over_2h
                ) AS late_over_2h_orders
            FROM fct_order_service
            WHERE is_eligible_service AND {where}
        """
        delivery_where, delivery_parameters = self._filter_sql(
            filters, date_column="delivery_date"
        )
        cold_sql = f"""
            SELECT count(*) AS chilled_deliveries,
                   count(*) FILTER (WHERE temperature_excursion_flag) AS excursions
            FROM fct_delivery
            WHERE is_eligible_service AND has_chilled_product AND {delivery_where}
        """
        line_where, line_parameters = self._filter_sql(
            filters, date_column="requested_delivery_date"
        )
        dispatch_sql = f"""
            SELECT sum(estimated_dispatch_value_inr),
                   sum(short_delivery_value_exposure_inr),
                   count(*) FILTER (WHERE short_eaches > 0)
            FROM fct_order_line
            WHERE is_eligible_service AND {line_where}
        """
        return_where, return_parameters = self._filter_sql(
            filters, date_column="return_date"
        )
        credit_sql = f"""
            SELECT sum(credit_note_value_inr) FILTER (WHERE credit_note_status = 'APPROVED'),
                   count(*) FILTER (WHERE credit_note_status = 'APPROVED')
            FROM fct_return_credit_note
            WHERE {return_where}
        """
        backlog_where, backlog_parameters = self._as_of_filter_sql(
            filters, date_column="requested_delivery_date"
        )
        backlog_sql = f"""
            SELECT count(*) AS overdue_orders
            FROM fct_order_service
            WHERE is_eligible_service_outlet
              AND order_status = 'OPEN'
              AND {backlog_where}
        """
        with self._connect() as connection:
            service = connection.execute(service_sql, parameters).fetchone()
            cold = connection.execute(cold_sql, delivery_parameters).fetchone()
            dispatch_value, short_value_exposure, short_lines = connection.execute(
                dispatch_sql, line_parameters
            ).fetchone()
            approved_credit, approved_records = connection.execute(
                credit_sql, return_parameters
            ).fetchone()
            overdue_orders = connection.execute(backlog_sql, backlog_parameters).fetchone()[0]
            snapshot_date = connection.execute(
                "SELECT max(snapshot_date) FROM fct_inventory_snapshot WHERE snapshot_date <= ?",
                [filters.end_date],
            ).fetchone()[0]
            near_expiry = None
            if snapshot_date is not None:
                inventory_conditions = [
                    "snapshot_date = ?",
                    "available_cases > 0",
                    "expiry_days BETWEEN 0 AND ?",
                ]
                inventory_parameters: list[Any] = [snapshot_date, self.near_expiry_days]
                for column, values in (
                    ("warehouse_region_name", filters.warehouse_regions),
                    ("warehouse_code", filters.warehouse_codes),
                ):
                    if values:
                        placeholders = ", ".join("?" for _ in values)
                        inventory_conditions.append(f"{column} IN ({placeholders})")
                        inventory_parameters.extend(values)
                near_expiry = connection.execute(
                    f"""
                    SELECT sum(available_cases)
                    FROM fct_inventory_snapshot
                    WHERE {' AND '.join(inventory_conditions)}
                    """,
                    inventory_parameters,
                ).fetchone()[0]

        (
            records,
            capped_delivered,
            raw_delivered,
            allocated,
            ordered,
            delivered_eaches,
            ordered_eaches,
            delivered_case_equivalents,
            ordered_case_equivalents,
            timestamp_eligible,
            on_time,
            otif,
            late_over_2h,
        ) = service
        chilled_deliveries, excursions = cold
        cold_rate = self._ratio(excursions, chilled_deliveries)
        return {
            "fill_rate": MetricValue(
                "fill_rate",
                self._ratio(capped_delivered, ordered),
                self._number(capped_delivered),
                self._number(ordered),
                "percent",
                records,
            ),
            "fill_rate_eaches": MetricValue(
                "fill_rate_eaches",
                self._ratio(delivered_eaches, ordered_eaches),
                self._number(delivered_eaches),
                self._number(ordered_eaches),
                "percent",
                records,
            ),
            "fill_rate_case_equivalents": MetricValue(
                "fill_rate_case_equivalents",
                self._ratio(delivered_case_equivalents, ordered_case_equivalents),
                self._number(delivered_case_equivalents),
                self._number(ordered_case_equivalents),
                "percent",
                records,
            ),
            "allocation_rate": MetricValue(
                "allocation_rate",
                self._ratio(allocated, ordered),
                self._number(allocated),
                self._number(ordered),
                "percent",
                records,
            ),
            "post_allocation_fulfilment": MetricValue(
                "post_allocation_fulfilment",
                self._ratio(raw_delivered, allocated),
                self._number(raw_delivered),
                self._number(allocated),
                "percent",
                records,
            ),
            "strict_otif": MetricValue(
                "strict_otif",
                self._ratio(otif, records),
                self._number(otif),
                self._number(records),
                "percent",
                records,
            ),
            "on_time_rate": MetricValue(
                "on_time_rate",
                self._ratio(on_time, timestamp_eligible),
                self._number(on_time),
                self._number(timestamp_eligible),
                "percent",
                records,
            ),
            "late_over_2h_rate": MetricValue(
                "late_over_2h_rate",
                self._ratio(late_over_2h, timestamp_eligible),
                self._number(late_over_2h),
                self._number(timestamp_eligible),
                "percent",
                records,
            ),
            "temperature_excursions_per_100": MetricValue(
                "temperature_excursions_per_100",
                100 * cold_rate if cold_rate is not None else None,
                self._number(excursions),
                self._number(chilled_deliveries),
                "per_100",
                chilled_deliveries,
            ),
            "near_expiry_cases": MetricValue(
                "near_expiry_cases",
                self._number(near_expiry),
                self._number(near_expiry),
                None,
                "cases",
                0,
            ),
            "approved_credit_note_rate": MetricValue(
                "approved_credit_note_rate",
                self._ratio(approved_credit, dispatch_value),
                self._number(approved_credit),
                self._number(dispatch_value),
                "percent",
                approved_records,
            ),
            "short_delivery_value_exposure_inr": MetricValue(
                "short_delivery_value_exposure_inr",
                self._number(short_value_exposure),
                self._number(short_value_exposure),
                None,
                "INR",
                int(short_lines or 0),
            ),
            "overdue_backlog_orders": MetricValue(
                "overdue_backlog_orders",
                self._number(overdue_orders),
                self._number(overdue_orders),
                None,
                "orders",
                int(overdue_orders or 0),
            ),
        }

    def service_by_dimension(
        self,
        filters: FilterSet,
        dimension: str,
        basis: QuantityBasis = QuantityBasis.EACHES,
        *,
        limit: int | None = None,
        worst_first: bool = True,
        min_orders: int = 1,
    ) -> pd.DataFrame:
        if dimension not in ORDER_DIMENSIONS:
            raise ValueError(f"Unsupported service dimension: {dimension}")
        if min_orders < 1:
            raise ValueError("min_orders must be positive")
        column, label = ORDER_DIMENSIONS[dimension]
        suffix = "eaches" if basis == QuantityBasis.EACHES else "case_equivalents"
        where, parameters = self._filter_sql(
            filters, date_column="requested_delivery_date"
        )
        order = "ASC" if worst_first else "DESC"
        limit_sql = " LIMIT ?" if limit is not None else ""
        parameters.append(min_orders)
        if limit is not None:
            parameters.append(limit)
        sql = f"""
            SELECT {column} AS dimension_value,
                   count(*) AS orders,
                   sum(ordered_{suffix}) AS ordered_quantity,
                   sum(allocated_{suffix}) AS allocated_quantity,
                   sum(delivered_{suffix}) AS delivered_quantity,
                   sum(short_{suffix}) AS short_quantity,
                   sum(allocated_{suffix}) / nullif(sum(ordered_{suffix}), 0)
                       AS allocation_rate,
                   sum(delivered_{suffix}) / nullif(sum(allocated_{suffix}), 0)
                       AS post_allocation_fulfilment,
                   sum(capped_delivered_{suffix})
                       / nullif(sum(ordered_{suffix}), 0) AS fill_rate,
                   avg(CAST(on_time_by_timestamp AS INTEGER)) AS on_time_rate,
                   avg(CAST(strict_otif AS INTEGER)) AS strict_otif_rate,
                   avg(CAST(late_over_2h AS INTEGER)) AS late_over_2h_rate
            FROM fct_order_service
            WHERE is_eligible_service AND {where}
            GROUP BY {column}
            HAVING count(*) >= ?
            ORDER BY fill_rate {order}, short_quantity DESC, dimension_value ASC
            {limit_sql}
        """
        with self._connect() as connection:
            frame = connection.execute(sql, parameters).fetchdf()
        frame.attrs["dimension_label"] = label
        frame.attrs["min_orders"] = min_orders
        return frame

    def service_trend(
        self, filters: FilterSet, basis: QuantityBasis = QuantityBasis.EACHES
    ) -> pd.DataFrame:
        suffix = "eaches" if basis == QuantityBasis.EACHES else "case_equivalents"
        where, parameters = self._filter_sql(
            filters, date_column="requested_delivery_date"
        )
        sql = f"""
            SELECT date_trunc('month', requested_delivery_date)::DATE AS month,
                   count(*) AS orders,
                   sum(allocated_{suffix}) / nullif(sum(ordered_{suffix}), 0)
                       AS allocation_rate,
                   sum(delivered_{suffix}) / nullif(sum(allocated_{suffix}), 0)
                       AS post_allocation_fulfilment,
                   sum(capped_delivered_{suffix})
                       / nullif(sum(ordered_{suffix}), 0) AS fill_rate,
                   avg(CAST(on_time_by_timestamp AS INTEGER)) AS on_time_rate,
                   avg(CAST(strict_otif AS INTEGER)) AS strict_otif_rate,
                   sum(short_{suffix}) AS short_quantity
            FROM fct_order_service
            WHERE is_eligible_service AND {where}
            GROUP BY month
            ORDER BY month
        """
        with self._connect() as connection:
            return connection.execute(sql, parameters).fetchdf()

    def backlog_by_dimension(
        self,
        filters: FilterSet,
        dimension: str,
        basis: QuantityBasis = QuantityBasis.EACHES,
        *,
        limit: int = 30,
    ) -> pd.DataFrame:
        """Rank the current overdue OPEN-order backlog at the selected as-of date.

        This is intentionally a source-state snapshot, not a reconstruction of the order
        status that existed historically on ``filters.end_date``.
        """

        if dimension not in ORDER_DIMENSIONS:
            raise ValueError(f"Unsupported backlog dimension: {dimension}")
        if limit < 1:
            raise ValueError("limit must be positive")
        column, label = ORDER_DIMENSIONS[dimension]
        suffix = "eaches" if basis == QuantityBasis.EACHES else "case_equivalents"
        where, where_parameters = self._as_of_filter_sql(
            filters, date_column="requested_delivery_date"
        )
        parameters: list[Any] = [filters.end_date, *where_parameters, limit]
        sql = f"""
            SELECT {column} AS dimension_value,
                   count(*) AS overdue_orders,
                   sum(ordered_{suffix}) AS overdue_ordered_quantity,
                   min(requested_delivery_date) AS oldest_requested_delivery_date,
                   date_diff(
                       'day', min(requested_delivery_date), ?
                   ) AS oldest_overdue_days
            FROM fct_order_service
            WHERE is_eligible_service_outlet
              AND order_status = 'OPEN'
              AND {column} IS NOT NULL
              AND {where}
            GROUP BY {column}
            ORDER BY overdue_orders DESC, overdue_ordered_quantity DESC, dimension_value
            LIMIT ?
        """
        with self._connect() as connection:
            frame = connection.execute(sql, parameters).fetchdf()
        frame.attrs.update(
            {
                "dimension_label": label,
                "as_of_date": filters.end_date,
                "quantity_basis": basis.value,
                "warning": (
                    "Backlog uses each order's current source status as observed now; it is "
                    "not a historical reconstruction of status on the selected date."
                ),
            }
        )
        return frame

    def fulfilment_flow(
        self,
        filters: FilterSet,
        basis: QuantityBasis = QuantityBasis.EACHES,
        *,
        return_statuses: tuple[str, ...] = ("APPROVED", "PENDING", "REJECTED"),
    ) -> pd.DataFrame:
        """Return a line-cohort Ordered→Allocated→Delivered→Returned flow.

        Order quantities use the requested-delivery cohort. Returns are independently
        aggregated to ``order_line_id`` and include only records observed on or before the
        selected end date, preventing a one-to-many return join from inflating the first three
        stages. Returned quantity is intentionally not capped at delivered quantity.
        """

        if not return_statuses:
            raise ValueError("At least one return status is required")
        suffix = "eaches" if basis == QuantityBasis.EACHES else "case_equivalents"
        where, parameters = self._filter_sql(
            filters, date_column="requested_delivery_date"
        )
        status_placeholders = ", ".join("?" for _ in return_statuses)
        parameters.extend([filters.end_date, *return_statuses])
        sql = f"""
            WITH selected_lines AS (
                SELECT order_line_id,
                       ordered_{suffix} AS ordered_quantity,
                       allocated_{suffix} AS allocated_quantity,
                       delivered_{suffix} AS delivered_quantity,
                       allocation_short_{suffix} AS allocation_shortfall_quantity,
                       post_allocation_short_{suffix}
                           AS post_allocation_shortfall_quantity
                FROM fct_order_line
                WHERE is_eligible_service AND {where}
            ), return_rollup AS (
                SELECT order_line_id,
                       sum(return_{suffix}) AS returned_quantity
                FROM fct_return_credit_note
                WHERE return_date <= ?
                  AND credit_note_status IN ({status_placeholders})
                GROUP BY order_line_id
            )
            SELECT count(*) AS eligible_order_lines,
                   sum(ordered_quantity) AS ordered_quantity,
                   sum(allocated_quantity) AS allocated_quantity,
                   sum(delivered_quantity) AS delivered_quantity,
                   coalesce(sum(returned_quantity), 0) AS returned_quantity,
                   sum(allocation_shortfall_quantity)
                       AS allocation_shortfall_quantity,
                   sum(post_allocation_shortfall_quantity)
                       AS post_allocation_shortfall_quantity
            FROM selected_lines
            LEFT JOIN return_rollup USING (order_line_id)
        """
        with self._connect() as connection:
            frame = connection.execute(sql, parameters).fetchdf()
        frame.attrs.update(
            {
                "grain": "One aggregate over a requested-delivery order-line cohort",
                "cohort": (
                    "Eligible DELIVERED/PARTIAL order lines for active, non-test outlets "
                    "within the selected requested-delivery dates and dimensions"
                ),
                "date_basis": (
                    "Ordered/allocated/delivered: requested delivery date; returned: "
                    "linked return records observed on or before selected end date"
                ),
                "return_statuses": return_statuses,
                "quantity_basis": basis.value,
                "warning": (
                    "Returned quantity is a linked physical-return signal across the selected "
                    "credit statuses; it is not capped and does not imply approved recovery."
                ),
            }
        )
        return frame

    def service_line_evidence(
        self, filters: FilterSet, *, limit: int = 200
    ) -> pd.DataFrame:
        """Return native order-line evidence, including recorded source and promotion."""

        if limit < 1 or limit > 1_000:
            raise ValueError("limit must be between 1 and 1000")
        where, parameters = self._filter_sql(
            filters, date_column="requested_delivery_date", prefix="line"
        )
        parameters.extend([filters.end_date, limit])
        sql = f"""
            WITH return_rollup AS (
                SELECT order_line_id,
                       sum(return_eaches) AS returned_eaches,
                       sum(return_case_equivalents) AS returned_case_equivalents
                FROM fct_return_credit_note
                WHERE return_date <= ?
                GROUP BY order_line_id
            )
            SELECT line.order_line_id, line.order_number,
                   line.requested_delivery_date, line.source_system,
                   line.created_at_raw, line.created_at_ist,
                   line.created_at_parse_status,
                   line.promotion_code, line.promotion_name,
                   line.promotion_mechanic, line.promotion_applied,
                   line.customer_region_name, line.warehouse_code,
                   line.route_code, line.outlet_code, line.channel,
                   line.sku_code, line.product_name, line.category,
                   line.ordered_eaches, line.allocated_eaches,
                   line.delivered_eaches,
                   coalesce(returns.returned_eaches, 0) AS returned_eaches,
                   line.ordered_case_equivalents,
                   line.allocated_case_equivalents,
                   line.delivered_case_equivalents,
                   coalesce(returns.returned_case_equivalents, 0)
                       AS returned_case_equivalents,
                   line.short_eaches, line.short_case_equivalents,
                   line.short_delivery_value_exposure_inr,
                   line.short_reason_code
            FROM fct_order_line line
            LEFT JOIN return_rollup returns USING (order_line_id)
            WHERE line.is_eligible_service AND {where}
            ORDER BY line.short_delivery_value_exposure_inr DESC,
                     line.short_eaches DESC, line.order_line_id
            LIMIT ?
        """
        query_parameters = [parameters[-2], *parameters[:-2], parameters[-1]]
        with self._connect() as connection:
            frame = connection.execute(sql, query_parameters).fetchdf()
        frame.attrs.update(
            {
                "grain": "One eligible order line",
                "promotion_definition": (
                    "Recorded order promo_code enriched from the promotion catalogue; this "
                    "shows association, not redemption effectiveness or causal uplift."
                ),
                "return_date_basis": "Linked returns observed on or before selected end date",
            }
        )
        return frame

    def service_rankings(
        self,
        filters: FilterSet,
        dimension: str,
        basis: QuantityBasis = QuantityBasis.EACHES,
        *,
        ranking: str = "best",
        limit: int = 5,
        min_orders: int = 50,
    ) -> pd.DataFrame:
        """Return volume-qualified best or period-over-period improved groups."""

        if ranking not in {"best", "most_improved"}:
            raise ValueError(f"Unsupported service ranking: {ranking}")
        if min_orders < 1:
            raise ValueError("min_orders must be positive")
        current = self.service_by_dimension(
            filters, dimension, basis, limit=None, worst_first=False
        )
        if current.empty:
            return current
        current = current.rename(
            columns={
                "orders": "current_orders",
                "fill_rate": "current_fill_rate",
                "short_quantity": "current_short_quantity",
            }
        )
        current = current.loc[current["current_orders"] >= min_orders]
        if ranking == "best":
            columns = [
                "dimension_value",
                "current_orders",
                "current_fill_rate",
                "current_short_quantity",
                "allocation_rate",
                "post_allocation_fulfilment",
            ]
            result = current.sort_values(
                ["current_fill_rate", "current_orders", "dimension_value"],
                ascending=[False, False, True],
            ).head(limit)[columns]
            result.attrs.update(
                {
                    "dimension_label": ORDER_DIMENSIONS[dimension][1],
                    "ranking": ranking,
                    "min_orders": min_orders,
                }
            )
            return result.reset_index(drop=True)

        duration_days = (filters.end_date - filters.start_date).days + 1
        previous_end = filters.start_date - timedelta(days=1)
        previous_filters = FilterSet(
            start_date=previous_end - timedelta(days=duration_days - 1),
            end_date=previous_end,
            customer_regions=filters.customer_regions,
            warehouse_regions=filters.warehouse_regions,
            warehouse_codes=filters.warehouse_codes,
            route_codes=filters.route_codes,
            outlet_codes=filters.outlet_codes,
            channels=filters.channels,
            promotion_codes=filters.promotion_codes,
            order_sources=filters.order_sources,
        )
        previous = self.service_by_dimension(
            previous_filters, dimension, basis, limit=None, worst_first=False
        ).rename(
            columns={
                "orders": "previous_orders",
                "fill_rate": "previous_fill_rate",
                "short_quantity": "previous_short_quantity",
            }
        )
        comparison = current.merge(
            previous[
                [
                    "dimension_value",
                    "previous_orders",
                    "previous_fill_rate",
                    "previous_short_quantity",
                ]
            ],
            on="dimension_value",
            how="inner",
        )
        comparison = comparison.loc[comparison["previous_orders"] >= min_orders].copy()
        comparison["fill_rate_change_pp"] = 100 * (
            comparison["current_fill_rate"] - comparison["previous_fill_rate"]
        )
        columns = [
            "dimension_value",
            "current_orders",
            "previous_orders",
            "current_fill_rate",
            "previous_fill_rate",
            "fill_rate_change_pp",
            "current_short_quantity",
        ]
        result = comparison.sort_values(
            ["fill_rate_change_pp", "current_orders", "dimension_value"],
            ascending=[False, False, True],
        ).head(limit)[columns]
        result.attrs.update(
            {
                "dimension_label": ORDER_DIMENSIONS[dimension][1],
                "ranking": ranking,
                "min_orders": min_orders,
                "previous_start_date": previous_filters.start_date,
                "previous_end_date": previous_filters.end_date,
            }
        )
        return result.reset_index(drop=True)

    def delivery_exception_summary(self, filters: FilterSet) -> dict[str, MetricValue]:
        """Summarise operational delivery exceptions on the actual-delivery cohort."""

        where, parameters = self._filter_sql(filters, date_column="delivery_date")
        sql = f"""
            SELECT count(*) AS deliveries,
                   count(*) FILTER (
                       WHERE planned_arrival_ts IS NOT NULL AND actual_arrival_ts IS NOT NULL
                   ) AS timestamp_eligible,
                   count(*) FILTER (WHERE on_time_by_timestamp) AS on_time_deliveries,
                   count(*) FILTER (WHERE late_over_2h) AS late_over_2h_deliveries,
                   count(*) FILTER (WHERE pod_captured) AS pod_deliveries,
                   count(*) FILTER (WHERE delay_source_conflict) AS conflict_deliveries,
                   count(*) FILTER (
                       WHERE failure_reason_code IS NOT NULL
                         AND trim(failure_reason_code) <> ''
                   ) AS failure_deliveries
            FROM fct_delivery
            WHERE is_eligible_service AND {where}
        """
        with self._connect() as connection:
            row = connection.execute(sql, parameters).fetchone()
        (
            deliveries,
            timestamp_eligible,
            on_time,
            late_over_2h,
            pod,
            conflicts,
            failures,
        ) = row
        return {
            "delivery_on_time_rate": MetricValue(
                "delivery_on_time_rate",
                self._ratio(on_time, timestamp_eligible),
                self._number(on_time),
                self._number(timestamp_eligible),
                "percent",
                deliveries,
            ),
            "delivery_late_over_2h_rate": MetricValue(
                "delivery_late_over_2h_rate",
                self._ratio(late_over_2h, timestamp_eligible),
                self._number(late_over_2h),
                self._number(timestamp_eligible),
                "percent",
                deliveries,
            ),
            "pod_coverage_rate": MetricValue(
                "pod_coverage_rate",
                self._ratio(pod, deliveries),
                self._number(pod),
                self._number(deliveries),
                "percent",
                deliveries,
            ),
            "delay_source_conflict_rate": MetricValue(
                "delay_source_conflict_rate",
                self._ratio(conflicts, deliveries),
                self._number(conflicts),
                self._number(deliveries),
                "percent",
                deliveries,
            ),
            "recorded_failure_rate": MetricValue(
                "recorded_failure_rate",
                self._ratio(failures, deliveries),
                self._number(failures),
                self._number(deliveries),
                "percent",
                deliveries,
            ),
        }

    def delivery_exception_trend(self, filters: FilterSet) -> pd.DataFrame:
        """Return month-level delivery timing, POD, failure, and conflict signals."""

        where, parameters = self._filter_sql(filters, date_column="delivery_date")
        sql = f"""
            SELECT date_trunc('month', delivery_date)::DATE AS month,
                   count(*) AS deliveries,
                   count(*) FILTER (WHERE on_time_by_timestamp)
                       / nullif(count(*) FILTER (
                           WHERE planned_arrival_ts IS NOT NULL
                             AND actual_arrival_ts IS NOT NULL
                       ), 0) AS on_time_rate,
                   count(*) FILTER (WHERE late_over_2h)
                       / nullif(count(*) FILTER (
                           WHERE planned_arrival_ts IS NOT NULL
                             AND actual_arrival_ts IS NOT NULL
                       ), 0) AS late_over_2h_rate,
                   avg(CAST(pod_captured AS INTEGER)) AS pod_coverage_rate,
                   avg(CAST(delay_source_conflict AS INTEGER)) AS conflict_rate,
                   avg(CAST(
                       failure_reason_code IS NOT NULL
                       AND trim(failure_reason_code) <> '' AS INTEGER
                   )) AS recorded_failure_rate
            FROM fct_delivery
            WHERE is_eligible_service AND {where}
            GROUP BY month
            ORDER BY month
        """
        with self._connect() as connection:
            return connection.execute(sql, parameters).fetchdf()

    def delivery_exceptions_by_dimension(
        self,
        filters: FilterSet,
        dimension: str,
        *,
        min_deliveries: int = 50,
        limit: int = 30,
    ) -> pd.DataFrame:
        """Rank volume-qualified delivery groups by the >2-hour exception rate."""

        if dimension not in DELIVERY_EXCEPTION_DIMENSIONS:
            raise ValueError(f"Unsupported delivery exception dimension: {dimension}")
        if min_deliveries < 1:
            raise ValueError("min_deliveries must be positive")
        column, label = DELIVERY_EXCEPTION_DIMENSIONS[dimension]
        where, parameters = self._filter_sql(filters, date_column="delivery_date")
        parameters.extend([min_deliveries, limit])
        sql = f"""
            SELECT {column} AS dimension_value,
                   count(*) AS deliveries,
                   count(*) FILTER (WHERE on_time_by_timestamp)
                       / nullif(count(*) FILTER (
                           WHERE planned_arrival_ts IS NOT NULL
                             AND actual_arrival_ts IS NOT NULL
                       ), 0) AS on_time_rate,
                   count(*) FILTER (WHERE late_over_2h)
                       / nullif(count(*) FILTER (
                           WHERE planned_arrival_ts IS NOT NULL
                             AND actual_arrival_ts IS NOT NULL
                       ), 0) AS late_over_2h_rate,
                   avg(CAST(pod_captured AS INTEGER)) AS pod_coverage_rate,
                   avg(CAST(delay_source_conflict AS INTEGER)) AS conflict_rate,
                   count(*) FILTER (
                       WHERE failure_reason_code IS NOT NULL
                         AND trim(failure_reason_code) <> ''
                   ) AS recorded_failures,
                   count(*) FILTER (
                       WHERE failure_reason_code IS NOT NULL
                         AND trim(failure_reason_code) <> ''
                   ) / nullif(count(*), 0) AS recorded_failure_rate
            FROM fct_delivery
            WHERE is_eligible_service AND {column} IS NOT NULL AND {where}
            GROUP BY {column}
            HAVING count(*) >= ?
            ORDER BY late_over_2h_rate DESC, deliveries DESC, dimension_value
            LIMIT ?
        """
        with self._connect() as connection:
            frame = connection.execute(sql, parameters).fetchdf()
        frame.attrs.update(
            {"dimension_label": label, "min_deliveries": min_deliveries}
        )
        return frame

    def failure_reason_pareto(self, filters: FilterSet, *, limit: int = 20) -> pd.DataFrame:
        """Return recorded failure labels and cumulative share, without causal claims."""

        where, parameters = self._filter_sql(filters, date_column="delivery_date")
        parameters.append(limit)
        sql = f"""
            WITH reason_counts AS (
                SELECT failure_reason_code,
                       count(*) AS failure_deliveries
                FROM fct_delivery
                WHERE is_eligible_service
                  AND failure_reason_code IS NOT NULL
                  AND trim(failure_reason_code) <> ''
                  AND {where}
                GROUP BY failure_reason_code
            ), ranked AS (
                SELECT failure_reason_code,
                       failure_deliveries,
                       failure_deliveries / nullif(sum(failure_deliveries) OVER (), 0)
                           AS failure_share,
                       sum(failure_deliveries) OVER (
                           ORDER BY failure_deliveries DESC, failure_reason_code
                           ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                       ) / nullif(sum(failure_deliveries) OVER (), 0) AS cumulative_share
                FROM reason_counts
            )
            SELECT * FROM ranked
            ORDER BY failure_deliveries DESC, failure_reason_code
            LIMIT ?
        """
        with self._connect() as connection:
            return connection.execute(sql, parameters).fetchdf()

    def delivery_exception_evidence(
        self, filters: FilterSet, *, limit: int = 200
    ) -> pd.DataFrame:
        """Return row evidence for the largest timestamp-derived delivery delays."""

        where, parameters = self._filter_sql(filters, date_column="delivery_date")
        parameters.append(limit)
        sql = f"""
            SELECT delivery_note_number, delivery_date, customer_region_name,
                   warehouse_code, route_code, outlet_code, telematics_vendor,
                   planned_arrival_ts, actual_arrival_ts,
                   stored_delay_minutes, derived_delay_minutes,
                   on_time_by_timestamp, on_time_by_stored_delay,
                   late_over_2h, delay_source_conflict,
                   pod_captured, delivery_status, failure_reason_code
            FROM fct_delivery
            WHERE is_eligible_service AND {where}
            ORDER BY late_over_2h DESC,
                     derived_delay_minutes DESC NULLS LAST,
                     delivery_note_number
            LIMIT ?
        """
        with self._connect() as connection:
            return connection.execute(sql, parameters).fetchdf()

    def shortage_contributors(
        self, filters: FilterSet, dimension: str, *, limit: int = 20
    ) -> pd.DataFrame:
        if dimension not in LINE_DIMENSIONS:
            raise ValueError(f"Unsupported shortage dimension: {dimension}")
        column, label = LINE_DIMENSIONS[dimension]
        where, parameters = self._filter_sql(
            filters, date_column="requested_delivery_date"
        )
        parameters.append(limit)
        sql = f"""
            SELECT {column} AS dimension_value,
                   count(*) AS lines,
                   sum(short_eaches) AS short_eaches,
                   sum(short_case_equivalents) AS short_case_equivalents,
                   sum(short_delivery_value_exposure_inr)
                       AS short_delivery_value_exposure_inr,
                   sum(estimated_dispatch_value_inr) AS estimated_dispatch_value_inr
            FROM fct_order_line
            WHERE is_eligible_service AND short_eaches > 0 AND {where}
            GROUP BY {column}
            ORDER BY short_eaches DESC
            LIMIT ?
        """
        with self._connect() as connection:
            frame = connection.execute(sql, parameters).fetchdf()
        frame.attrs["dimension_label"] = label
        return frame

    def short_delivery_exposure(
        self,
        filters: FilterSet,
        dimension: str,
        basis: QuantityBasis = QuantityBasis.EACHES,
        *,
        limit: int = 30,
    ) -> pd.DataFrame:
        """Aggregate booked line-value exposure from undelivered quantity at line grain."""

        if dimension not in LINE_DIMENSIONS:
            raise ValueError(f"Unsupported short-delivery dimension: {dimension}")
        if limit < 1:
            raise ValueError("limit must be positive")
        column, label = LINE_DIMENSIONS[dimension]
        suffix = "eaches" if basis == QuantityBasis.EACHES else "case_equivalents"
        where, parameters = self._filter_sql(
            filters, date_column="requested_delivery_date"
        )
        parameters.append(limit)
        sql = f"""
            SELECT {column} AS dimension_value,
                   count(*) AS short_lines,
                   count(DISTINCT order_id) AS affected_orders,
                   sum(short_{suffix}) AS short_quantity,
                   sum(line_value_inr) AS booked_line_value_inr,
                   sum(short_delivery_value_exposure_inr)
                       AS short_delivery_value_exposure_inr,
                   sum(short_delivery_value_exposure_inr)
                       / nullif(sum(line_value_inr), 0) AS exposure_share_of_booked_value,
                   sum(allocation_short_value_exposure_inr)
                       AS allocation_short_value_exposure_inr,
                   sum(post_allocation_short_value_exposure_inr)
                       AS post_allocation_short_value_exposure_inr
            FROM fct_order_line
            WHERE is_eligible_service AND short_{suffix} > 0
              AND {column} IS NOT NULL AND {where}
            GROUP BY {column}
            ORDER BY short_delivery_value_exposure_inr DESC,
                     short_quantity DESC, dimension_value
            LIMIT ?
        """
        with self._connect() as connection:
            frame = connection.execute(sql, parameters).fetchdf()
        frame.attrs.update(
            {
                "dimension_label": label,
                "grain": "Eligible order lines aggregated by the selected dimension",
                "quantity_basis": basis.value,
                "value_definition": (
                    "Booked line_value_inr multiplied by the positive undelivered share "
                    "(ordered_qty - delivered_qty) / ordered_qty. This is commercial "
                    "exposure, not accounting loss, profit, cash, or causal attribution."
                ),
            }
        )
        return frame

    def cold_chain_by_dimension(
        self, filters: FilterSet, dimension: str, *, limit: int = 20
    ) -> pd.DataFrame:
        """Aggregate source-flagged excursions with descriptive peak-temperature evidence."""

        if dimension not in COLD_CHAIN_DIMENSIONS:
            raise ValueError(f"Unsupported cold-chain dimension: {dimension}")
        if limit < 1:
            raise ValueError("limit must be positive")
        column, label = COLD_CHAIN_DIMENSIONS[dimension]
        where, parameters = self._filter_sql(filters, date_column="delivery_date")
        parameters.append(limit)
        relation = "fct_delivery"
        grain = "One delivery"
        non_additive_warning = ""
        if dimension == "category":
            relation = """
                fct_delivery
                JOIN (
                    SELECT DISTINCT order_id, category
                    FROM fct_order_line
                    WHERE is_chilled AND category IS NOT NULL
                ) chilled_category USING (order_id)
            """
            grain = "One delivery × chilled-category pair"
            non_additive_warning = (
                "A multi-category delivery appears once in each chilled category; category "
                "rows must not be summed to an all-delivery total."
            )
        sql = f"""
            SELECT {column} AS dimension_value,
                   count(*) AS chilled_deliveries,
                   count(*) FILTER (WHERE temperature_excursion_flag) AS excursions,
                   100.0 * count(*) FILTER (WHERE temperature_excursion_flag)
                       / nullif(count(*), 0) AS excursions_per_100,
                   avg(max_temp_celsius) AS avg_recorded_max_temp_c,
                   max(max_temp_celsius) AS peak_recorded_max_temp_c,
                   avg(max_temp_celsius) FILTER (WHERE temperature_excursion_flag)
                       AS avg_excursion_max_temp_c,
                   max(max_temp_celsius) FILTER (WHERE temperature_excursion_flag)
                       AS peak_excursion_max_temp_c,
                   count(*) FILTER (
                       WHERE temperature_excursion_flag AND max_temp_celsius <= 8
                   ) AS flagged_peak_le_8c,
                   count(*) FILTER (
                       WHERE temperature_excursion_flag
                         AND max_temp_celsius > 8 AND max_temp_celsius <= 12
                   ) AS flagged_peak_8_to_12c,
                   count(*) FILTER (
                       WHERE temperature_excursion_flag AND max_temp_celsius > 12
                   ) AS flagged_peak_over_12c,
                   count(*) FILTER (
                       WHERE temperature_excursion_flag AND max_temp_celsius IS NULL
                   ) AS flagged_peak_unavailable,
                   CASE
                       WHEN max(max_temp_celsius) FILTER (
                           WHERE temperature_excursion_flag
                       ) IS NULL THEN 'No flagged peak recorded'
                       WHEN max(max_temp_celsius) FILTER (
                           WHERE temperature_excursion_flag
                       ) > 12 THEN 'Flagged peak >12C'
                       WHEN max(max_temp_celsius) FILTER (
                           WHERE temperature_excursion_flag
                       ) > 8 THEN 'Flagged peak >8-12C'
                       ELSE 'Flagged peak <=8C'
                   END AS descriptive_peak_band
            FROM {relation}
            WHERE is_eligible_service AND has_chilled_product
              AND {column} IS NOT NULL AND {where}
            GROUP BY {column}
            ORDER BY excursions_per_100 DESC, excursions DESC,
                     peak_excursion_max_temp_c DESC NULLS LAST, dimension_value
            LIMIT ?
        """
        with self._connect() as connection:
            frame = connection.execute(sql, parameters).fetchdf()
        frame.attrs.update(
            {
                "dimension_label": label,
                "grain": grain,
                "severity_definition": (
                    "The <=8C, >8-12C, and >12C bands describe recorded peak "
                    "temperature among source-flagged deliveries only. They are not validated "
                    "food-safety limits and do not define or explain the source flag."
                ),
                "warning": non_additive_warning,
            }
        )
        return frame

    def cold_chain_trend(self, filters: FilterSet) -> pd.DataFrame:
        """Return the same governed excursion evidence at delivery-month grain."""

        frame = self.cold_chain_by_dimension(filters, "month", limit=120)
        frame.attrs["date_basis"] = "Actual delivery month"
        return frame.sort_values("dimension_value").reset_index(drop=True)

    def inventory_risk(
        self, filters: FilterSet, dimension: str = "warehouse"
    ) -> pd.DataFrame:
        dimensions = {
            "warehouse": ("warehouse_code", "Warehouse"),
            "warehouse_region": ("warehouse_region_name", "DC region"),
            "category": ("category", "Category"),
            "sku": ("sku_code", "SKU"),
        }
        if dimension not in dimensions:
            raise ValueError(f"Unsupported inventory dimension: {dimension}")
        column, label = dimensions[dimension]
        with self._connect() as connection:
            snapshot_date = connection.execute(
                "SELECT max(snapshot_date) FROM fct_inventory_snapshot WHERE snapshot_date <= ?",
                [filters.end_date],
            ).fetchone()[0]
            if snapshot_date is None:
                return pd.DataFrame()
            inventory_conditions = ["snapshot_date = ?"]
            inventory_parameters: list[Any] = [self.near_expiry_days, snapshot_date]
            for filter_column, values in (
                ("warehouse_region_name", filters.warehouse_regions),
                ("warehouse_code", filters.warehouse_codes),
            ):
                if values:
                    placeholders = ", ".join("?" for _ in values)
                    inventory_conditions.append(
                        f"{filter_column} IN ({placeholders})"
                    )
                    inventory_parameters.extend(values)
            frame = connection.execute(
                f"""
                SELECT {column} AS dimension_value,
                       sum(available_cases) AS available_cases,
                       sum(CASE WHEN expiry_days BETWEEN 0 AND ?
                                THEN available_cases ELSE 0 END) AS near_expiry_cases,
                       sum(CASE WHEN expiry_days < 0
                                THEN available_cases ELSE 0 END) AS expired_available_cases,
                       sum(damaged_cases) AS damaged_cases,
                       sum(blocked_cases) AS blocked_cases
                FROM fct_inventory_snapshot
                WHERE {' AND '.join(inventory_conditions)}
                GROUP BY {column}
                ORDER BY near_expiry_cases DESC
                """,
                inventory_parameters,
            ).fetchdf()
        frame.attrs["snapshot_date"] = snapshot_date
        frame.attrs["dimension_label"] = label
        ignored_filters: list[str] = []
        if filters.customer_regions:
            ignored_filters.append("customer region")
        if filters.route_codes:
            ignored_filters.append("route")
        if filters.outlet_codes:
            ignored_filters.append("outlet")
        if filters.channels:
            ignored_filters.append("channel")
        if filters.promotion_codes:
            ignored_filters.append("promotion")
        if filters.order_sources:
            ignored_filters.append("order source")
        frame.attrs["ignored_filters"] = tuple(ignored_filters)
        return frame

    def inventory_batch_evidence(
        self,
        filters: FilterSet,
        *,
        risk_only: bool = True,
        limit: int = 200,
    ) -> pd.DataFrame:
        """Return latest-as-of inventory evidence at source batch-snapshot grain."""

        if limit < 1 or limit > 1_000:
            raise ValueError("limit must be between 1 and 1000")
        with self._connect() as connection:
            snapshot_date = connection.execute(
                "SELECT max(snapshot_date) FROM fct_inventory_snapshot "
                "WHERE snapshot_date <= ?",
                [filters.end_date],
            ).fetchone()[0]
            if snapshot_date is None:
                frame = pd.DataFrame()
                frame.attrs["snapshot_date"] = None
                return frame

            conditions = ["snapshot_date = ?"]
            parameters: list[Any] = [snapshot_date]
            for column, values in (
                ("warehouse_region_name", filters.warehouse_regions),
                ("warehouse_code", filters.warehouse_codes),
            ):
                if values:
                    placeholders = ", ".join("?" for _ in values)
                    conditions.append(f"{column} IN ({placeholders})")
                    parameters.extend(values)
            if risk_only:
                conditions.append(
                    "((available_cases > 0 AND expiry_days BETWEEN 0 AND ?) "
                    "OR (available_cases > 0 AND expiry_days < 0) "
                    "OR damaged_cases > 0 OR blocked_cases > 0)"
                )
                parameters.append(self.near_expiry_days)
            parameters.append(limit)
            frame = connection.execute(
                f"""
                SELECT snapshot_id, snapshot_date, warehouse_region_name,
                       warehouse_code, warehouse_name, sku_code, product_name,
                       category, subcategory, is_chilled, storage_temp_band,
                       batch_id, on_hand_cases, on_hand_eaches, allocated_cases,
                       available_cases, days_of_cover, expiry_date, expiry_days,
                       ageing_bucket, damaged_cases, blocked_cases,
                       storage_temp_celsius,
                       (available_cases > 0 AND expiry_days BETWEEN 0 AND
                           {self.near_expiry_days}) AS near_expiry_flag,
                       (available_cases > 0 AND expiry_days < 0) AS expired_stock_flag,
                       (damaged_cases > 0) AS damaged_stock_flag,
                       (blocked_cases > 0) AS blocked_stock_flag
                FROM fct_inventory_snapshot
                WHERE {' AND '.join(conditions)}
                ORDER BY expired_stock_flag DESC, near_expiry_flag DESC,
                         damaged_cases DESC, blocked_cases DESC,
                         available_cases DESC, snapshot_id
                LIMIT ?
                """,
                parameters,
            ).fetchdf()

        ignored_filters: list[str] = []
        for label, values in (
            ("customer region", filters.customer_regions),
            ("route", filters.route_codes),
            ("outlet", filters.outlet_codes),
            ("channel", filters.channels),
            ("promotion", filters.promotion_codes),
            ("order source", filters.order_sources),
        ):
            if values:
                ignored_filters.append(label)
        frame.attrs.update(
            {
                "snapshot_date": snapshot_date,
                "grain": "One warehouse × SKU × batch × weekly source snapshot row",
                "risk_only": risk_only,
                "near_expiry_days": self.near_expiry_days,
                "ignored_filters": tuple(ignored_filters),
            }
        )
        return frame

    def returns_by_dimension(
        self,
        filters: FilterSet,
        dimension: str,
        *,
        statuses: tuple[str, ...] = ("APPROVED",),
        limit: int = 20,
    ) -> pd.DataFrame:
        if dimension not in RETURN_DIMENSIONS:
            raise ValueError(f"Unsupported return dimension: {dimension}")
        column, label = RETURN_DIMENSIONS[dimension]
        where, parameters = self._filter_sql(filters, date_column="return_date")
        status_placeholders = ", ".join("?" for _ in statuses)
        parameters.extend(statuses)
        parameters.append(limit)
        sql = f"""
            SELECT {column} AS dimension_value,
                   count(*) AS credit_note_lines,
                   sum(return_eaches) AS return_eaches,
                   sum(return_case_equivalents) AS return_case_equivalents,
                   sum(credit_note_value_inr) AS credit_note_value_inr
            FROM fct_return_credit_note
            WHERE {where} AND credit_note_status IN ({status_placeholders})
            GROUP BY {column}
            ORDER BY credit_note_value_inr DESC
            LIMIT ?
        """
        with self._connect() as connection:
            frame = connection.execute(sql, parameters).fetchdf()
        frame.attrs["dimension_label"] = label
        return frame

    def credit_status_summary(self, filters: FilterSet) -> pd.DataFrame:
        where, parameters = self._filter_sql(filters, date_column="return_date")
        sql = f"""
            SELECT credit_note_status,
                   count(*) AS credit_note_lines,
                   sum(credit_note_value_inr) AS credit_note_value_inr
            FROM fct_return_credit_note
            WHERE {where}
            GROUP BY credit_note_status
            ORDER BY credit_note_value_inr DESC
        """
        with self._connect() as connection:
            return connection.execute(sql, parameters).fetchdf()

    def return_disposition_summary(
        self,
        filters: FilterSet,
        *,
        statuses: tuple[str, ...] = ("APPROVED", "PENDING", "REJECTED"),
    ) -> pd.DataFrame:
        """Summarise physical returns and associated credit value by disposition."""

        if not statuses:
            raise ValueError("At least one credit-note status is required")
        where, parameters = self._filter_sql(filters, date_column="return_date")
        status_placeholders = ", ".join("?" for _ in statuses)
        parameters.extend(statuses)
        sql = f"""
            SELECT disposition,
                   count(*) AS credit_note_lines,
                   count(*) FILTER (WHERE credit_note_status = 'APPROVED')
                       AS approved_lines,
                   count(*) FILTER (WHERE credit_note_status = 'PENDING')
                       AS pending_lines,
                   count(*) FILTER (WHERE credit_note_status = 'REJECTED')
                       AS rejected_lines,
                   sum(return_eaches) AS return_eaches,
                   sum(return_case_equivalents) AS return_case_equivalents,
                   sum(credit_note_value_inr) AS associated_credit_note_value_inr,
                   sum(credit_note_value_inr) FILTER (
                       WHERE credit_note_status = 'APPROVED'
                   ) AS approved_credit_note_value_inr,
                   sum(credit_note_value_inr) FILTER (
                       WHERE credit_note_status = 'PENDING'
                   ) AS pending_credit_note_value_inr,
                   sum(credit_note_value_inr) FILTER (
                       WHERE credit_note_status = 'REJECTED'
                   ) AS rejected_credit_note_value_inr
            FROM fct_return_credit_note
            WHERE {where} AND credit_note_status IN ({status_placeholders})
            GROUP BY disposition
            ORDER BY associated_credit_note_value_inr DESC, disposition
        """
        with self._connect() as connection:
            frame = connection.execute(sql, parameters).fetchdf()
        frame.attrs.update(
            {
                "grain": "Return credit-note lines aggregated by source disposition",
                "statuses": statuses,
                "warning": (
                    "RESTOCK, SCRAP, and VENDOR_RECOVERY are recorded dispositions. "
                    "Associated or approved credit-note value is not proof of cash receipt, "
                    "inventory recovery, vendor reimbursement, profit, or causation."
                ),
            }
        )
        return frame

    def cold_chain_return_evidence(
        self,
        filters: FilterSet,
        *,
        statuses: tuple[str, ...] = ("APPROVED", "PENDING", "REJECTED"),
        limit: int = 100,
    ) -> pd.DataFrame:
        """Return exact RT06 credit-note lines with source and normalized quantities."""

        if not statuses:
            raise ValueError("At least one credit-note status is required")
        if limit < 1 or limit > 1_000:
            raise ValueError("limit must be between 1 and 1000")
        where, parameters = self._filter_sql(filters, date_column="return_date")
        status_placeholders = ", ".join("?" for _ in statuses)
        parameters.extend(statuses)
        parameters.append(limit)
        sql = f"""
            SELECT credit_note_number, return_date, order_id, order_line_id,
                   outlet_code, outlet_name, warehouse_code, route_code,
                   sku_code, product_name, qty_uom, return_qty_raw,
                   return_qty_normalized, return_sign_was_negative,
                   return_eaches, return_case_equivalents,
                   return_reason_code, credit_note_status,
                   credit_note_value_inr, disposition
            FROM fct_return_credit_note
            WHERE is_cold_chain_return
              AND {where}
              AND credit_note_status IN ({status_placeholders})
            ORDER BY return_date DESC, credit_note_value_inr DESC,
                     credit_note_number, order_line_id
            LIMIT ?
        """
        with self._connect() as connection:
            frame = connection.execute(sql, parameters).fetchdf()
        frame.attrs["grain"] = "One exact source credit-note line linked by order_line_id"
        frame.attrs["quantity_rule"] = (
            "Physical return quantity is absolute-valued and normalized through "
            "case_pack_at_order; raw sign remains visible."
        )
        return frame

    def discontinued_order_evidence(
        self, filters: FilterSet, *, limit: int = 100
    ) -> pd.DataFrame:
        where, parameters = self._filter_sql(filters, date_column="order_date")
        parameters.append(limit)
        sql = f"""
            SELECT order_number, order_date, outlet_code, outlet_name,
                   warehouse_code, route_code, sku_code, product_name,
                   discontinued_date, ordered_eaches, line_value_inr
            FROM fct_order_line
            WHERE ordered_after_discontinued AND {where}
            ORDER BY order_date DESC, line_value_inr DESC
            LIMIT ?
        """
        with self._connect() as connection:
            return connection.execute(sql, parameters).fetchdf()

    def service_evidence(self, filters: FilterSet, *, limit: int = 200) -> pd.DataFrame:
        where, parameters = self._filter_sql(
            filters, date_column="requested_delivery_date"
        )
        parameters.append(limit)
        sql = f"""
            SELECT order_number, requested_delivery_date, customer_region_name,
                   warehouse_code, route_code, outlet_code, outlet_name,
                   fill_rate_eaches, fill_rate_case_equivalents,
                   derived_delay_minutes, on_time_by_timestamp,
                   strict_in_full, strict_otif, short_eaches
            FROM fct_order_service
            WHERE is_eligible_service AND {where}
            ORDER BY fill_rate_eaches, short_eaches DESC
            LIMIT ?
        """
        with self._connect() as connection:
            return connection.execute(sql, parameters).fetchdf()
