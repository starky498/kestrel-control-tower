"""Deterministic queries used by both the dashboard and Ask Kestrel."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
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
        with self._connect() as connection:
            service = connection.execute(service_sql, parameters).fetchone()
            cold = connection.execute(cold_sql, delivery_parameters).fetchone()
            dispatch_value = connection.execute(dispatch_sql, line_parameters).fetchone()[0]
            approved_credit, approved_records = connection.execute(
                credit_sql, return_parameters
            ).fetchone()
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

        records, delivered, ordered, on_time, otif, late_over_2h = service
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
        }

    def service_by_dimension(
        self,
        filters: FilterSet,
        dimension: str,
        basis: QuantityBasis = QuantityBasis.EACHES,
        *,
        limit: int | None = None,
        worst_first: bool = True,
    ) -> pd.DataFrame:
        if dimension not in ORDER_DIMENSIONS:
            raise ValueError(f"Unsupported service dimension: {dimension}")
        column, label = ORDER_DIMENSIONS[dimension]
        suffix = "eaches" if basis == QuantityBasis.EACHES else "case_equivalents"
        where, parameters = self._filter_sql(
            filters, date_column="requested_delivery_date"
        )
        order = "ASC" if worst_first else "DESC"
        limit_sql = " LIMIT ?" if limit is not None else ""
        if limit is not None:
            parameters.append(limit)
        sql = f"""
            SELECT {column} AS dimension_value,
                   count(*) AS orders,
                   sum(ordered_{suffix}) AS ordered_quantity,
                   sum(delivered_{suffix}) AS delivered_quantity,
                   sum(short_{suffix}) AS short_quantity,
                   sum(delivered_{suffix}) / nullif(sum(ordered_{suffix}), 0) AS fill_rate,
                   avg(CAST(on_time_by_timestamp AS INTEGER)) AS on_time_rate,
                   avg(CAST(strict_otif AS INTEGER)) AS strict_otif_rate,
                   avg(CAST(late_over_2h AS INTEGER)) AS late_over_2h_rate
            FROM fct_order_service
            WHERE is_eligible_service AND {where}
            GROUP BY {column}
            ORDER BY fill_rate {order}, short_quantity DESC
            {limit_sql}
        """
        with self._connect() as connection:
            frame = connection.execute(sql, parameters).fetchdf()
        frame.attrs["dimension_label"] = label
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
