from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb

from kestrel.metrics.context import WEATHER_DISCLOSURE, ContextAnalyticsService
from kestrel.metrics.service import FilterSet


def _database(path: Path, *, orders_per_group: int = 40) -> None:
    with duckdb.connect(str(path)) as connection:
        connection.execute(
            """
            CREATE TABLE fct_order_service (
                order_id BIGINT,
                requested_delivery_date DATE,
                delivery_date DATE,
                customer_region_name VARCHAR,
                warehouse_region_name VARCHAR,
                warehouse_code VARCHAR,
                route_code VARCHAR,
                outlet_code VARCHAR,
                channel VARCHAR,
                is_eligible_service BOOLEAN,
                on_time_by_timestamp BOOLEAN,
                has_chilled_product BOOLEAN,
                temperature_excursion_flag BOOLEAN,
                ordered_eaches DOUBLE,
                delivered_eaches DOUBLE
            );
            CREATE TABLE ext_weather_daily_current (
                warehouse_code VARCHAR,
                observation_date DATE,
                precipitation_sum_mm DOUBLE
            );
            INSERT INTO ext_weather_daily_current VALUES
                ('WH01', DATE '2026-06-01', 12.0),
                ('WH01', DATE '2026-06-02', 0.0);
            CREATE TABLE ext_india_holiday_current (
                holiday_date DATE,
                global_holiday BOOLEAN,
                counties_json VARCHAR
            );
            INSERT INTO ext_india_holiday_current VALUES
                (DATE '2026-06-01', TRUE, NULL);
            CREATE TABLE external_sync_runs (
                sync_id VARCHAR,
                source_name VARCHAR,
                status VARCHAR,
                record_count BIGINT,
                is_complete BOOLEAN,
                coverage_start DATE,
                coverage_end DATE,
                started_at_utc TIMESTAMPTZ,
                completed_at_utc TIMESTAMPTZ,
                details_json VARCHAR
            );
            """
        )
        completed = datetime(2026, 7, 1, tzinfo=UTC)
        connection.execute(
            """INSERT INTO external_sync_runs VALUES
               (?, 'open_meteo_weather', 'SUCCEEDED', 2, TRUE,
                DATE '2026-06-01', DATE '2026-06-30', ?, ?, ?),
               (?, 'india_public_holidays', 'SUCCEEDED', 1, TRUE,
                DATE '2026-06-01', DATE '2026-06-30', ?, ?, '{}')""",
            [
                "weather",
                completed,
                completed,
                json.dumps({"row_coverage_ratio": 1.0, "location_coverage_ratio": 1.0}),
                "holiday",
                completed,
                completed,
            ],
        )
        rows = []
        order_id = 0
        for observed_date, rainy in (
            (date(2026, 6, 1), True),
            (date(2026, 6, 2), False),
        ):
            for _ in range(orders_per_group):
                order_id += 1
                rows.append(
                    (
                        order_id,
                        observed_date,
                        observed_date,
                        "West",
                        "West",
                        "WH01",
                        "RT0001",
                        "OUT001",
                        "GT",
                        True,
                        not rainy,
                        True,
                        rainy,
                        100.0,
                        90.0 if rainy else 98.0,
                    )
                )
        connection.executemany(
            "INSERT INTO fct_order_service VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )


def _service(database: Path, *, minimum: int = 30) -> ContextAnalyticsService:
    return ContextAnalyticsService(
        database,
        minimum_cohort_orders=minimum,
        now_fn=lambda: datetime(2026, 8, 1, tzinfo=UTC),
    )


def test_weather_and_holiday_associations_pass_governed_gate(tmp_path: Path) -> None:
    database = tmp_path / "analytics.duckdb"
    _database(database)
    filters = FilterSet(date(2026, 6, 1), date(2026, 6, 2))

    weather = _service(database).weather_delivery_association(filters)
    holiday = _service(database).holiday_service_association(filters)

    assert weather.gate.publishable
    assert weather.gate.operational_join_ratio == 1
    assert set(weather.frame["context_group"]) == {"rainy_day", "little_or_no_rain"}
    rainy = weather.frame.set_index("context_group").loc["rainy_day"]
    assert rainy["observed_late_rate"] == 1
    assert rainy["observed_temperature_excursion_rate"] == 1
    assert rainy["observed_fill_rate_eaches"] == 0.9
    assert holiday.gate.publishable
    assert set(holiday.frame["context_group"]) == {"public_holiday", "non_holiday"}
    assert weather.frame.attrs["interpretation"] == WEATHER_DISCLOSURE
    assert "causal" in weather.gate.disclosure.lower()


def test_publication_gate_withholds_small_or_out_of_coverage_cohorts(tmp_path: Path) -> None:
    database = tmp_path / "analytics.duckdb"
    _database(database, orders_per_group=5)

    small = _service(database, minimum=30).weather_delivery_association(
        FilterSet(date(2026, 6, 1), date(2026, 6, 2))
    )
    outside = _service(database).holiday_service_association(
        FilterSet(date(2026, 5, 31), date(2026, 6, 2))
    )

    assert not small.gate.publishable
    assert small.frame.empty
    assert any("minimum is 30" in reason for reason in small.gate.reasons)
    assert not outside.gate.publishable
    assert outside.frame.empty
    assert any("outside source coverage" in reason for reason in outside.gate.reasons)
