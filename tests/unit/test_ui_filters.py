from datetime import date

from kestrel.metrics.periods import Period
from kestrel.metrics.service import FilterSet, QuantityBasis
from kestrel.ui.filters import (
    _FILTER_WIDGET_KEYS,
    FilterContext,
    _reset_filter_state,
    with_dates,
)


def test_with_dates_preserves_all_global_dimensions() -> None:
    filters = FilterSet(
        start_date=date(2026, 4, 1),
        end_date=date(2026, 6, 30),
        customer_regions=("West",),
        warehouse_regions=("South",),
        warehouse_codes=("WH01",),
        route_codes=("R01",),
        outlet_codes=("OUT01",),
        channels=("RETAIL",),
        promotion_codes=("PRM01",),
        order_sources=("SFA_MOBILE",),
    )

    shifted = with_dates(
        filters,
        Period(date(2026, 1, 1), date(2026, 3, 31), "Previous quarter"),
    )

    assert shifted.start_date == date(2026, 1, 1)
    assert shifted.end_date == date(2026, 3, 31)
    assert shifted.customer_regions == filters.customer_regions
    assert shifted.warehouse_regions == filters.warehouse_regions
    assert shifted.warehouse_codes == filters.warehouse_codes
    assert shifted.route_codes == filters.route_codes
    assert shifted.outlet_codes == filters.outlet_codes
    assert shifted.channels == filters.channels
    assert shifted.promotion_codes == filters.promotion_codes
    assert shifted.order_sources == filters.order_sources


def test_filter_context_chips_disclose_promotion_and_order_source_scope() -> None:
    context = FilterContext(
        filters=FilterSet(
            start_date=date(2026, 4, 1),
            end_date=date(2026, 6, 30),
            promotion_codes=("PRM01", "PRM02"),
            order_sources=("PARTNER_API",),
        ),
        basis=QuantityBasis.EACHES,
        period_label="Q2 FY2026",
        data_min_date=date(2024, 1, 1),
        data_max_date=date(2026, 6, 30),
    )

    assert "Promotion: 2 selected" in context.dimension_chips
    assert "Order source: PARTNER_API" in context.dimension_chips


def test_reset_filter_state_clears_every_global_widget(monkeypatch) -> None:
    state = {key: ["selected"] for key in _FILTER_WIDGET_KEYS}
    state["unrelated_page_state"] = "preserve"
    monkeypatch.setattr("kestrel.ui.filters.st.session_state", state)

    _reset_filter_state()

    assert all(key not in state for key in _FILTER_WIDGET_KEYS)
    assert state["unrelated_page_state"] == "preserve"
