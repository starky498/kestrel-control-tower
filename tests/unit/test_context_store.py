from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb

from kestrel.ingestion.context import (
    ContextSource,
    ContextSyncMetadata,
    HolidayCache,
    HolidayObservation,
    WeatherCache,
    WeatherObservation,
)
from kestrel.integration_store import store_holiday_snapshot, store_weather_snapshot


def _metadata(source: ContextSource, count: int) -> ContextSyncMetadata:
    return ContextSyncMetadata(
        source_name=source,
        source_url=f"https://{source}.test",
        requested_date_from=date(2025, 1, 1),
        requested_date_to=date(2025, 1, 1),
        coverage_start=date(2025, 1, 1),
        coverage_end=date(2025, 1, 1),
        collected_at_utc=datetime(2026, 7, 1, tzinfo=UTC),
        record_count=count,
        expected_record_count=count,
        location_count=1,
        expected_location_count=1,
        request_count=1,
        retry_count=0,
    )


def test_context_snapshots_publish_native_grains_and_sync_metadata(tmp_path: Path) -> None:
    database = tmp_path / "analytics.duckdb"
    weather = WeatherCache(
        schema_version=1,
        metadata=_metadata("open_meteo_weather", 1),
        observations=(
            WeatherObservation(
                warehouse_code="WH01",
                warehouse_city="Mumbai",
                latitude=19.076,
                longitude=72.878,
                observation_date=date(2025, 1, 1),
                temperature_2m_max_c=31.2,
                precipitation_sum_mm=4.2,
                timezone="Asia/Kolkata",
                location_basis="warehouse_city_centroid",
            ),
        ),
    )
    holidays = HolidayCache(
        schema_version=1,
        metadata=_metadata("india_public_holidays", 1),
        observations=(
            HolidayObservation(
                holiday_date=date(2025, 1, 1),
                local_name="New Year's Day",
                name="New Year's Day",
                country_code="IN",
                fixed=False,
                global_holiday=True,
                types=("Public",),
            ),
        ),
    )

    weather_summary = store_weather_snapshot(database, weather)
    holiday_summary = store_holiday_snapshot(database, holidays)

    assert weather_summary.record_count == 1
    assert holiday_summary.record_count == 1
    with duckdb.connect(str(database), read_only=True) as connection:
        weather_row = connection.execute(
            """
            SELECT warehouse_code, observation_date, precipitation_sum_mm, location_basis
            FROM ext_weather_daily_current
            """
        ).fetchone()
        holiday_row = connection.execute(
            """
            SELECT holiday_date, country_code, global_holiday
            FROM ext_india_holiday_current
            """
        ).fetchone()
        sync_sources = connection.execute(
            "SELECT source_name FROM external_sync_runs ORDER BY source_name"
        ).fetchall()
        sync_details = {
            source: json.loads(details)
            for source, details in connection.execute(
                "SELECT source_name, details_json FROM external_sync_runs"
            ).fetchall()
        }
    assert weather_row == (
        "WH01",
        date(2025, 1, 1),
        4.2,
        "warehouse_city_centroid",
    )
    assert holiday_row == (date(2025, 1, 1), "IN", True)
    assert sync_sources == [
        ("india_public_holidays",),
        ("open_meteo_weather",),
    ]
    assert sync_details["open_meteo_weather"]["provider"] == "Open-Meteo archive API"
    assert sync_details["india_public_holidays"]["source_url"].endswith(".test")
    assert (
        sync_details["india_public_holidays"]["provider"]
        == "Nager.Date public-holiday API"
    )
