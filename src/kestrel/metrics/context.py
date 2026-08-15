"""Governed descriptive associations for optional weather and holiday context.

Results are withheld unless source completeness, selected-period coverage, join coverage, and
minimum cohort sizes pass.  The outputs describe co-occurrence only and must not be presented as
causal effects or route-level weather attribution.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from kestrel.metrics.service import FilterSet

ASSOCIATION_DISCLOSURE = (
    "Descriptive association only; no causal effect is estimated."
)
WEATHER_DISCLOSURE = (
    ASSOCIATION_DISCLOSURE
    + " Weather is observed at the warehouse-city centroid, not on the route or at the outlet."
)
HOLIDAY_DISCLOSURE = (
    ASSOCIATION_DISCLOSURE
    + " The national public-calendar flag does not represent state-specific closures."
)


@dataclass(frozen=True, slots=True)
class PublicationGate:
    """Auditable decision explaining whether contextual rows may be shown."""

    source_name: str
    publishable: bool
    reasons: tuple[str, ...]
    coverage_start: date | None = None
    coverage_end: date | None = None
    collected_at_utc: datetime | None = None
    cache_age_days: float | None = None
    row_coverage_ratio: float | None = None
    location_coverage_ratio: float | None = None
    operational_join_ratio: float | None = None
    minimum_cohort_orders: int | None = None
    disclosure: str = ASSOCIATION_DISCLOSURE


@dataclass(frozen=True)
class ContextAssociationResult:
    """A published frame, or an empty frame plus the failed publication gate."""

    frame: pd.DataFrame
    gate: PublicationGate


ASSOCIATION_COLUMNS = [
    "context_group",
    "order_count",
    "late_order_count",
    "observed_late_rate",
    "temperature_observed_order_count",
    "temperature_excursion_order_count",
    "observed_temperature_excursion_rate",
    "ordered_eaches",
    "delivered_eaches",
    "observed_fill_rate_eaches",
]


class ContextAnalyticsService:
    """Read-only, gated context queries over the analytical warehouse."""

    def __init__(
        self,
        database_path: Path,
        *,
        minimum_cohort_orders: int = 30,
        minimum_join_coverage: float = 0.95,
        maximum_cache_age: timedelta = timedelta(days=365),
        now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if minimum_cohort_orders <= 0:
            raise ValueError("minimum_cohort_orders must be positive")
        if not 0 < minimum_join_coverage <= 1:
            raise ValueError("minimum_join_coverage must be in (0, 1]")
        self.database_path = Path(database_path)
        self.minimum_cohort_orders = minimum_cohort_orders
        self.minimum_join_coverage = minimum_join_coverage
        self.maximum_cache_age = maximum_cache_age
        self.now_fn = now_fn

    def _connect(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(str(self.database_path), read_only=True)

    @staticmethod
    def _exists(connection: duckdb.DuckDBPyConnection, object_name: str) -> bool:
        return bool(
            connection.execute(
                """
                SELECT count(*) FROM information_schema.tables WHERE table_name = ?
                """,
                [object_name],
            ).fetchone()[0]
        )

    @staticmethod
    def _filter_sql(
        filters: FilterSet,
        *,
        date_column: str,
        prefix: str = "service",
    ) -> tuple[str, list[Any]]:
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
        conditions = [f"{prefix}.{date_column} BETWEEN ? AND ?"]
        parameters: list[Any] = [filters.start_date, filters.end_date]
        for column, values_iterable in dimensions:
            values = tuple(values_iterable)
            if values:
                conditions.append(
                    f"{prefix}.{column} IN ({', '.join('?' for _ in values)})"
                )
                parameters.extend(values)
        return " AND ".join(conditions), parameters

    def _source_gate(
        self,
        connection: duckdb.DuckDBPyConnection,
        *,
        source_name: str,
        table_name: str,
        filters: FilterSet,
    ) -> PublicationGate:
        reasons: list[str] = []
        if not self._exists(connection, table_name):
            reasons.append(f"{source_name} snapshot is not available")
        if not self._exists(connection, "external_sync_runs"):
            reasons.append("external sync metadata is not available")
            return PublicationGate(source_name, False, tuple(reasons))
        row = connection.execute(
            """
            SELECT status, is_complete, coverage_start, coverage_end,
                   completed_at_utc, details_json
            FROM external_sync_runs
            WHERE source_name = ?
            ORDER BY completed_at_utc DESC
            LIMIT 1
            """,
            [source_name],
        ).fetchone()
        if row is None:
            reasons.append(f"{source_name} has no successful sync metadata")
            return PublicationGate(source_name, False, tuple(reasons))
        status, complete, coverage_start, coverage_end, collected_at, details_json = row
        if status != "SUCCEEDED" or not complete:
            reasons.append(f"{source_name} latest sync is not complete")
        if coverage_start is None or coverage_end is None:
            reasons.append(f"{source_name} coverage metadata is missing")
        elif coverage_start > filters.start_date or coverage_end < filters.end_date:
            reasons.append(
                f"selected period {filters.start_date} to {filters.end_date} is outside "
                f"source coverage {coverage_start} to {coverage_end}"
            )
        now = self.now_fn()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now_fn must return a timezone-aware datetime")
        collected_utc = collected_at.astimezone(UTC) if collected_at is not None else None
        cache_age_days = (
            (now.astimezone(UTC) - collected_utc).total_seconds() / 86_400
            if collected_utc is not None
            else None
        )
        if cache_age_days is None:
            reasons.append(f"{source_name} freshness timestamp is missing")
        elif cache_age_days > self.maximum_cache_age.total_seconds() / 86_400:
            reasons.append(
                f"{source_name} cache age {cache_age_days:.1f} days exceeds the "
                f"{self.maximum_cache_age.days}-day publication limit"
            )
        details: dict[str, Any]
        try:
            details = json.loads(details_json or "{}")
        except json.JSONDecodeError:
            details = {}
            reasons.append(f"{source_name} sync details are invalid")
        row_ratio = details.get("row_coverage_ratio")
        location_ratio = details.get("location_coverage_ratio")
        if source_name == "open_meteo_weather":
            if row_ratio is None or float(row_ratio) < 1:
                reasons.append("weather cache is missing one or more expected warehouse-days")
            if location_ratio is None or float(location_ratio) < 1:
                reasons.append("weather cache is missing one or more expected warehouses")
        return PublicationGate(
            source_name=source_name,
            publishable=not reasons,
            reasons=tuple(reasons),
            coverage_start=coverage_start,
            coverage_end=coverage_end,
            collected_at_utc=collected_utc,
            cache_age_days=cache_age_days,
            row_coverage_ratio=float(row_ratio) if row_ratio is not None else None,
            location_coverage_ratio=(
                float(location_ratio) if location_ratio is not None else None
            ),
            disclosure=(
                WEATHER_DISCLOSURE
                if source_name == "open_meteo_weather"
                else HOLIDAY_DISCLOSURE
            ),
        )

    @staticmethod
    def _empty(gate: PublicationGate) -> ContextAssociationResult:
        frame = pd.DataFrame(columns=ASSOCIATION_COLUMNS)
        frame.attrs["publication_gate"] = gate
        frame.attrs["interpretation"] = gate.disclosure
        return ContextAssociationResult(frame=frame, gate=gate)

    def _finish_gate(
        self,
        gate: PublicationGate,
        frame: pd.DataFrame,
        *,
        required_groups: tuple[str, str],
        join_ratio: float,
    ) -> ContextAssociationResult:
        reasons = list(gate.reasons)
        if join_ratio < self.minimum_join_coverage:
            reasons.append(
                f"operational-to-context join coverage {join_ratio:.1%} is below "
                f"{self.minimum_join_coverage:.0%}"
            )
        counts = {
            str(row.context_group): int(row.order_count)
            for row in frame[["context_group", "order_count"]].itertuples(index=False)
        }
        for group in required_groups:
            if counts.get(group, 0) < self.minimum_cohort_orders:
                reasons.append(
                    f"{group} has {counts.get(group, 0):,} eligible orders; "
                    f"minimum is {self.minimum_cohort_orders:,}"
                )
        minimum = min((counts.get(group, 0) for group in required_groups), default=0)
        final_gate = replace(
            gate,
            publishable=not reasons,
            reasons=tuple(reasons),
            operational_join_ratio=join_ratio,
            minimum_cohort_orders=minimum,
        )
        if not final_gate.publishable:
            return self._empty(final_gate)
        frame.attrs["publication_gate"] = final_gate
        frame.attrs["interpretation"] = final_gate.disclosure
        return ContextAssociationResult(frame=frame, gate=final_gate)

    def weather_delivery_association(self, filters: FilterSet) -> ContextAssociationResult:
        """Compare observed service metrics on rainy versus little/no-rain delivery days."""

        with self._connect() as connection:
            gate = self._source_gate(
                connection,
                source_name="open_meteo_weather",
                table_name="ext_weather_daily_current",
                filters=filters,
            )
            if not gate.publishable:
                return self._empty(gate)
            where, parameters = self._filter_sql(filters, date_column="delivery_date")
            matched, total = connection.execute(
                f"""
                SELECT count(weather.observation_date), count(*)
                FROM fct_order_service service
                LEFT JOIN ext_weather_daily_current weather
                  ON weather.warehouse_code = service.warehouse_code
                 AND weather.observation_date = service.delivery_date
                WHERE service.is_eligible_service
                  AND service.delivery_date IS NOT NULL
                  AND {where}
                """,
                parameters,
            ).fetchone()
            join_ratio = float(matched) / float(total) if total else 0.0
            frame = connection.execute(
                f"""
                WITH associated AS (
                    SELECT service.*,
                           CASE WHEN weather.precipitation_sum_mm >= 1
                                THEN 'rainy_day'
                                ELSE 'little_or_no_rain'
                           END AS context_group
                    FROM fct_order_service service
                    JOIN ext_weather_daily_current weather
                      ON weather.warehouse_code = service.warehouse_code
                     AND weather.observation_date = service.delivery_date
                    WHERE service.is_eligible_service
                      AND service.delivery_date IS NOT NULL
                      AND {where}
                )
                SELECT context_group,
                       count(*) AS order_count,
                       count(*) FILTER (WHERE NOT on_time_by_timestamp) AS late_order_count,
                       count(*) FILTER (WHERE NOT on_time_by_timestamp)::DOUBLE
                           / nullif(count(*), 0) AS observed_late_rate,
                       count(*) FILTER (
                           WHERE has_chilled_product
                             AND temperature_excursion_flag IS NOT NULL
                       ) AS temperature_observed_order_count,
                       count(*) FILTER (
                           WHERE has_chilled_product AND temperature_excursion_flag
                       ) AS temperature_excursion_order_count,
                       count(*) FILTER (
                           WHERE has_chilled_product AND temperature_excursion_flag
                       )::DOUBLE / nullif(count(*) FILTER (
                           WHERE has_chilled_product
                             AND temperature_excursion_flag IS NOT NULL
                       ), 0) AS observed_temperature_excursion_rate,
                       sum(ordered_eaches) AS ordered_eaches,
                       sum(capped_delivered_eaches) AS delivered_eaches,
                       sum(capped_delivered_eaches) / nullif(sum(ordered_eaches), 0)
                           AS observed_fill_rate_eaches
                FROM associated
                GROUP BY context_group
                ORDER BY context_group
                """,
                parameters,
            ).fetchdf()
        return self._finish_gate(
            gate,
            frame,
            required_groups=("rainy_day", "little_or_no_rain"),
            join_ratio=join_ratio,
        )

    def holiday_service_association(self, filters: FilterSet) -> ContextAssociationResult:
        """Compare observed service metrics on Indian public holidays and other requested days."""

        with self._connect() as connection:
            gate = self._source_gate(
                connection,
                source_name="india_public_holidays",
                table_name="ext_india_holiday_current",
                filters=filters,
            )
            if not gate.publishable:
                return self._empty(gate)
            where, parameters = self._filter_sql(
                filters, date_column="requested_delivery_date"
            )
            frame = connection.execute(
                f"""
                WITH national_holidays AS (
                    SELECT DISTINCT holiday_date
                    FROM ext_india_holiday_current
                    WHERE global_holiday
                ), associated AS (
                    SELECT service.*,
                           CASE WHEN holiday.holiday_date IS NOT NULL
                                THEN 'public_holiday'
                                ELSE 'non_holiday'
                           END AS context_group
                    FROM fct_order_service service
                    LEFT JOIN national_holidays holiday
                      ON holiday.holiday_date = service.requested_delivery_date
                    WHERE service.is_eligible_service AND {where}
                )
                SELECT context_group,
                       count(*) AS order_count,
                       count(*) FILTER (WHERE NOT on_time_by_timestamp) AS late_order_count,
                       count(*) FILTER (WHERE NOT on_time_by_timestamp)::DOUBLE
                           / nullif(count(*), 0) AS observed_late_rate,
                       count(*) FILTER (
                           WHERE has_chilled_product
                             AND temperature_excursion_flag IS NOT NULL
                       ) AS temperature_observed_order_count,
                       count(*) FILTER (
                           WHERE has_chilled_product AND temperature_excursion_flag
                       ) AS temperature_excursion_order_count,
                       count(*) FILTER (
                           WHERE has_chilled_product AND temperature_excursion_flag
                       )::DOUBLE / nullif(count(*) FILTER (
                           WHERE has_chilled_product
                             AND temperature_excursion_flag IS NOT NULL
                       ), 0) AS observed_temperature_excursion_rate,
                       sum(ordered_eaches) AS ordered_eaches,
                       sum(capped_delivered_eaches) AS delivered_eaches,
                       sum(capped_delivered_eaches) / nullif(sum(ordered_eaches), 0)
                           AS observed_fill_rate_eaches
                FROM associated
                GROUP BY context_group
                ORDER BY context_group
                """,
                parameters,
            ).fetchdf()
        return self._finish_gate(
            gate,
            frame,
            required_groups=("public_holiday", "non_holiday"),
            join_ratio=1.0,
        )
