from datetime import date

from kestrel.metrics.periods import (
    fiscal_quarter_for,
    last_complete_month,
    latest_complete_fiscal_quarter,
    previous_period,
)


def test_fiscal_quarter_boundaries() -> None:
    assert fiscal_quarter_for(date(2026, 4, 1)).label == "FY 2026-27 Q1"
    assert fiscal_quarter_for(date(2026, 6, 30)).start == date(2026, 4, 1)
    assert fiscal_quarter_for(date(2026, 1, 15)).label == "FY 2025-26 Q4"
    assert fiscal_quarter_for(date(2025, 12, 31)).label == "FY 2025-26 Q3"


def test_latest_complete_quarter_uses_current_quarter_only_at_period_end() -> None:
    complete = latest_complete_fiscal_quarter(date(2026, 6, 30))
    assert complete.start == date(2026, 4, 1)
    assert complete.end == date(2026, 6, 30)

    incomplete = latest_complete_fiscal_quarter(date(2026, 6, 29))
    assert incomplete.start == date(2026, 1, 1)
    assert incomplete.end == date(2026, 3, 31)


def test_previous_period_has_same_number_of_days() -> None:
    current = fiscal_quarter_for(date(2026, 5, 10))
    prior = previous_period(current)
    assert prior.days == current.days
    assert prior.end == date(2026, 3, 31)


def test_last_complete_month_is_anchored_to_available_data() -> None:
    assert last_complete_month(date(2026, 6, 30)).label == "June 2026"
    assert last_complete_month(date(2026, 6, 29)).label == "May 2026"
