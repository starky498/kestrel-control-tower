from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest

from kestrel.ingestion.context import (
    ContextClient,
    ContextIngestionError,
    WeatherLocation,
    load_holiday_cache,
    load_weather_cache,
)


def _weather_payload() -> dict[str, object]:
    return {
        "latitude": 19.076,
        "longitude": 72.878,
        "timezone": "Asia/Kolkata",
        "daily": {
            "time": ["2025-01-01", "2025-01-02"],
            "temperature_2m_max": [31.2, 30.8],
            "precipitation_sum": [0.0, 4.2],
        },
    }


def _holiday_payload() -> list[dict[str, object]]:
    return [
        {
            "date": "2025-01-01",
            "localName": "New Year's Day",
            "name": "New Year's Day",
            "countryCode": "IN",
            "fixed": False,
            "global": True,
            "counties": None,
            "launchYear": None,
            "types": ["Public"],
        }
    ]


def _client(tmp_path: Path, handler: httpx.MockTransport, **kwargs: object) -> ContextClient:
    return ContextClient(
        weather_cache_path=tmp_path / "weather.json",
        holiday_cache_path=tmp_path / "holidays.json",
        weather_url="https://weather.test/archive",
        holiday_url="https://holiday.test/PublicHolidays",
        client=httpx.Client(transport=handler),
        now_fn=lambda: datetime(2026, 7, 1, tzinfo=UTC),
        sleep_fn=lambda _: None,
        random_fn=lambda: 0,
        **kwargs,
    )


def test_context_sources_validate_and_promote_separate_caches(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "weather.test":
            return httpx.Response(200, json=_weather_payload())
        return httpx.Response(200, json=_holiday_payload())

    client = _client(tmp_path, httpx.MockTransport(handler))
    weather = client.sync_weather(
        date_from=date(2025, 1, 1),
        date_to=date(2025, 1, 2),
        locations=(
            WeatherLocation(
                warehouse_code="WH01",
                warehouse_city="Mumbai",
                latitude=19.076,
                longitude=72.878,
            ),
        ),
    )
    holidays = client.sync_holidays(
        date_from=date(2025, 1, 1),
        date_to=date(2025, 1, 2),
    )

    assert weather.metadata.record_count == 2
    assert weather.metadata.row_coverage_ratio == 1
    assert weather.observations[1].precipitation_sum_mm == 4.2
    assert holidays.metadata.record_count == 1
    assert holidays.observations[0].country_code == "IN"
    assert load_weather_cache(tmp_path / "weather.json") == weather
    assert load_holiday_cache(tmp_path / "holidays.json") == holidays
    weather_request = requests[0]
    assert weather_request.url.params["timezone"] == "Asia/Kolkata"
    assert weather_request.url.params["daily"] == ("temperature_2m_max,precipitation_sum")


def test_source_failure_isolated_and_last_good_file_untouched(tmp_path: Path) -> None:
    weather_path = tmp_path / "weather.json"
    weather_path.write_text("previous-last-good", encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "weather.test":
            return httpx.Response(503)
        return httpx.Response(200, json=_holiday_payload())

    outcomes = _client(
        tmp_path,
        httpx.MockTransport(handler),
        max_retries=0,
    ).sync_all(date_from=date(2025, 1, 1), date_to=date(2025, 1, 2))

    assert not outcomes[0].complete
    assert outcomes[1].complete
    assert weather_path.read_text(encoding="utf-8") == "previous-last-good"
    assert load_holiday_cache(tmp_path / "holidays.json").metadata.complete


def test_incomplete_weather_days_are_never_promoted(tmp_path: Path) -> None:
    handler = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            json={
                **_weather_payload(),
                "daily": {
                    "time": ["2025-01-01"],
                    "temperature_2m_max": [31.2],
                    "precipitation_sum": [0.0],
                },
            },
        )
    )
    client = _client(tmp_path, handler)

    with pytest.raises(ContextIngestionError, match="incomplete dates"):
        client.sync_weather(
            date_from=date(2025, 1, 1),
            date_to=date(2025, 1, 2),
            locations=(
                WeatherLocation(
                    warehouse_code="WH01",
                    warehouse_city="Mumbai",
                    latitude=19.076,
                    longitude=72.878,
                ),
            ),
        )

    assert not (tmp_path / "weather.json").exists()


def test_holiday_sync_falls_back_to_typed_public_calendar_when_india_unsupported(
    tmp_path: Path,
) -> None:
    calendar = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;VALUE=DATE:20250126
SUMMARY:Republic Day
DESCRIPTION:Public holiday
END:VEVENT
BEGIN:VEVENT
DTSTART;VALUE=DATE:20250219
SUMMARY:Shivaji Jayanti
DESCRIPTION:Observance\\nTo hide observances
END:VEVENT
END:VCALENDAR
"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "holiday.test":
            return httpx.Response(204)
        return httpx.Response(200, text=calendar)

    client = _client(tmp_path, httpx.MockTransport(handler))
    client.holiday_fallback_url = "https://calendar.test/india.ics"
    cache = client.sync_holidays(
        date_from=date(2025, 1, 1),
        date_to=date(2025, 12, 31),
    )

    assert cache.metadata.source_name == "india_public_holidays"
    assert cache.metadata.source_url == "https://calendar.test/india.ics"
    assert [row.name for row in cache.observations] == ["Republic Day"]
    assert cache.observations[0].global_holiday
