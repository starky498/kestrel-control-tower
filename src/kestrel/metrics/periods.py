"""Fiscal and relative-period helpers anchored to available data."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class Period:
    start: date
    end: date
    label: str

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


def _add_months(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 + months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def fiscal_quarter_for(value: date) -> Period:
    month_start = value.replace(day=1)
    months_into_quarter = ((value.month - 4) % 12) % 3
    start = _add_months(month_start, -months_into_quarter)
    end = _add_months(start, 3) - timedelta(days=1)
    fiscal_year_start = start.year if start.month >= 4 else start.year - 1
    quarter = ((start.month - 4) % 12) // 3 + 1
    label = f"FY {fiscal_year_start}-{str(fiscal_year_start + 1)[-2:]} Q{quarter}"
    return Period(start, end, label)


def latest_complete_fiscal_quarter(max_available_date: date) -> Period:
    candidate = fiscal_quarter_for(max_available_date)
    if max_available_date >= candidate.end:
        return candidate
    prior_end = candidate.start - timedelta(days=1)
    return fiscal_quarter_for(prior_end)


def previous_period(period: Period) -> Period:
    end = period.start - timedelta(days=1)
    start = end - timedelta(days=period.days - 1)
    return Period(start, end, f"Previous {period.days} days")


def last_complete_month(max_available_date: date) -> Period:
    this_month_start = max_available_date.replace(day=1)
    if max_available_date.day == calendar.monthrange(
        max_available_date.year, max_available_date.month
    )[1]:
        start = this_month_start
    else:
        start = _add_months(this_month_start, -1)
    end = _add_months(start, 1) - timedelta(days=1)
    return Period(start, end, start.strftime("%B %Y"))
