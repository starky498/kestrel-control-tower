from datetime import date

import pandas as pd
import pytest

from kestrel.metrics.service import MetricValue
from kestrel.ui.pages import (
    _build_executive_inbox,
    _ExecutiveExternalSignals,
    _sampled_route_ratios,
    _summarise_external_frames,
)


def _rate(key: str, value: float, numerator: float, denominator: float) -> MetricValue:
    return MetricValue(key, value, numerator, denominator, "percent", int(denominator))


def test_executive_external_summary_uses_paid_freight_and_sourcewide_match_coverage() -> None:
    freight = pd.DataFrame(
        {
            "freight_cost_inr": [1_000.0, 2_000.0],
            "paid_cost_inr": [80.0, 40.0],
            "delivered_case_equivalents": [10.0, 10.0],
        }
    )
    quality = pd.DataFrame(
        {
            "city": ["Mumbai", "Mumbai", "Delhi"],
            "match_status": ["matched", "ambiguous", "matched"],
            "listings": [80, 20, 50],
        }
    )
    sync_status = pd.DataFrame(
        {
            "source_name": ["freight_api", "bazaarpulse"],
            "coverage_end": [date(2026, 6, 30), date(2026, 6, 29)],
            "completed_at_utc": [
                "2026-08-15T06:00:00Z",
                "2026-08-15T07:00:00Z",
            ],
            "is_complete": [True, True],
        }
    )

    signals = _summarise_external_frames(freight, quality, sync_status)

    assert signals.paid_freight_per_case_inr == pytest.approx(6.0)
    assert signals.competitor_matched == 130
    assert signals.competitor_listings == 150
    assert signals.competitor_coverage == pytest.approx(130 / 150)
    assert "coverage through 2026-06-30" in signals.freight_detail
    assert "coverage through 2026-06-29" in signals.competitor_freshness


def test_executive_external_summary_does_not_substitute_billed_for_paid() -> None:
    signals = _summarise_external_frames(
        pd.DataFrame(
            {
                "freight_cost_inr": [500.0],
                "delivered_case_equivalents": [10.0],
            }
        ),
        pd.DataFrame(),
        pd.DataFrame(),
    )

    assert signals.paid_freight_per_case_inr is None


def test_executive_inbox_ranks_rates_and_retains_failure_reason_context() -> None:
    service_summary = {"strict_otif": _rate("strict_otif", 0.0, 0, 100)}
    delivery_summary = {
        "delay_source_conflict_rate": _rate("delay_source_conflict_rate", 0.10, 10, 100),
        "delivery_late_over_2h_rate": _rate("delivery_late_over_2h_rate", 0.30, 30, 100),
        "recorded_failure_rate": _rate("recorded_failure_rate", 0.05, 5, 100),
        "pod_coverage_rate": _rate("pod_coverage_rate", 0.90, 90, 100),
    }
    external = _ExecutiveExternalSignals(
        paid_freight_per_case_inr=6.0,
        freight_detail="fresh",
        freight_lens="warehouse",
        competitor_coverage=0.80,
        competitor_matched=80,
        competitor_listings=100,
        competitor_freshness="fresh",
    )
    failure_pareto = pd.DataFrame(
        {"failure_reason_code": ["CUSTOMER_CLOSED"], "failure_deliveries": [5]}
    )

    inbox = _build_executive_inbox(
        service_summary,
        delivery_summary,
        external,
        failure_pareto,
    )

    assert inbox.iloc[0]["Signal"] == "Strict OTIF gap"
    assert inbox.iloc[0]["Review rate"] == pytest.approx(100.0)
    assert inbox["Rank"].tolist() == list(range(1, len(inbox) + 1))
    failure_lens = inbox.loc[
        inbox["Signal"] == "Recorded failure labels", "Lens"
    ].item()
    assert "CUSTOMER_CLOSED" in failure_lens


def test_route_ratio_sample_enforces_both_invoice_and_delivery_floors() -> None:
    routes = pd.DataFrame(
        {
            "route_code": ["R1", "R2", "R3", "R4"],
            "invoice_count": [25, 24, 25, 100],
            "delivered_orders": [25, 100, 24, 100],
            "freight_cost_per_delivered_case_inr": [4.0, 1.0, 2.0, None],
        }
    )

    sampled = _sampled_route_ratios(routes)

    assert sampled["route_code"].tolist() == ["R1"]
