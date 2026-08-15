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
    "category": ("category", "Category"),
    "sku": ("sku_code", "SKU"),
    "reason": ("return_reason_code", "Return reason"),
    "disposition": ("disposition", "Disposition"),
    "status": ("credit_note_status", "Credit status"),
}

DELIVERY_EXCEPTION_DIMENSIONS: dict[str, tuple[str, str]] = {
    "route": ("route_code", "Route"),
    "warehouse": ("warehouse_code", "Warehouse"),
    "warehouse_region": ("warehouse_region_name", "DC region"),
    "customer_region": ("customer_region_name", "Customer region"),
    "channel": ("channel", "Channel"),
    "telematics_vendor": ("telematics_vendor", "Telematics vendor"),
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
        )
        for column, values_iterable in dimension_filters:
            values = tuple(values_iterable)
            if values:
                placeholders = ", ".join("?" for _ in values)
                conditions.append(f"{qualifier}{column} IN ({placeholders})")
                parameters.extend(values)
        return " AND ".join(conditions), parameters

    def available_date_range(self) -> tuple[date, date]:
        with self._connect() as connection:
            minimum, maximum = connection.execute(
                "SELECT min(requested_delivery_date), max(requested_delivery_date) "
                "FROM fct_order_service"
            ).fetchone()
        return minimum, maximum

    def filter_options(self) -> dict[str, list[str]]:
        queries = {
            "customer_regions": "customer_region_name",
            "warehouse_regions": "warehouse_region_name",
            "warehouse_codes": "warehouse_code",
            "route_codes": "route_code",
            "outlet_codes": "outlet_code",
            "channels": "channel",
        }
        options: dict[str, list[str]] = {}
        with self._connect() as connection:
            for key, column in queries.items():
                rows = connection.execute(
                    f"SELECT DISTINCT {column} FROM fct_order_service "
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
                sum(delivered_{quantity_suffix}) AS delivered_qty,
                sum(allocated_{quantity_suffix}) AS allocated_qty,
                sum(ordered_{quantity_suffix}) AS ordered_qty,
                count(*) FILTER (WHERE on_time_by_timestamp) AS on_time_orders,
                count(*) FILTER (WHERE strict_otif) AS otif_orders,
                count(*) FILTER (WHERE late_over_2h) AS late_over_2h_orders
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
            SELECT sum(estimated_dispatch_value_inr)
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
            dispatch_value = connection.execute(dispatch_sql, line_parameters).fetchone()[0]
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

        records, delivered, allocated, ordered, on_time, otif, late_over_2h = service
        chilled_deliveries, excursions = cold
        cold_rate = self._ratio(excursions, chilled_deliveries)
        return {
            "fill_rate": MetricValue(
                "fill_rate",
                self._ratio(delivered, ordered),
                self._number(delivered),
                self._number(ordered),
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
                self._ratio(delivered, allocated),
                self._number(delivered),
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
                self._ratio(on_time, records),
                self._number(on_time),
                self._number(records),
                "percent",
                records,
            ),
            "late_over_2h_rate": MetricValue(
                "late_over_2h_rate",
                self._ratio(late_over_2h, records),
                self._number(late_over_2h),
                self._number(records),
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
                   sum(delivered_{suffix}) / nullif(sum(ordered_{suffix}), 0) AS fill_rate,
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
                   sum(delivered_{suffix}) / nullif(sum(ordered_{suffix}), 0) AS fill_rate,
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
                   ) AS recorded_failures
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

    def cold_chain_by_dimension(
        self, filters: FilterSet, dimension: str, *, limit: int = 20
    ) -> pd.DataFrame:
        if dimension not in ORDER_DIMENSIONS:
            raise ValueError(f"Unsupported cold-chain dimension: {dimension}")
        column, label = ORDER_DIMENSIONS[dimension]
        where, parameters = self._filter_sql(filters, date_column="delivery_date")
        parameters.append(limit)
        sql = f"""
            SELECT {column} AS dimension_value,
                   count(*) AS chilled_deliveries,
                   count(*) FILTER (WHERE temperature_excursion_flag) AS excursions,
                   100.0 * count(*) FILTER (WHERE temperature_excursion_flag)
                       / nullif(count(*), 0) AS excursions_per_100
            FROM fct_delivery
            WHERE is_eligible_service AND has_chilled_product AND {where}
            GROUP BY {column}
            ORDER BY excursions_per_100 DESC, excursions DESC
            LIMIT ?
        """
        with self._connect() as connection:
            frame = connection.execute(sql, parameters).fetchdf()
        frame.attrs["dimension_label"] = label
        return frame

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
        frame.attrs["ignored_filters"] = tuple(ignored_filters)
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
