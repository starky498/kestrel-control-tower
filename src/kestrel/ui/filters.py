"""Global dashboard filters and period selection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import streamlit as st

from kestrel.metrics.periods import (
    Period,
    last_complete_month,
    latest_complete_fiscal_quarter,
)
from kestrel.metrics.service import AnalyticsService, FilterSet, QuantityBasis

_FILTER_WIDGET_KEYS = (
    "kp_period_preset",
    "kp_custom_dates",
    "kp_quantity_basis",
    "kp_customer_regions",
    "kp_warehouse_regions",
    "kp_warehouse_codes",
    "kp_route_codes",
    "kp_outlet_codes",
    "kp_channels",
    "kp_promotion_codes",
    "kp_order_sources",
)


def _reset_filter_state() -> None:
    """Clear widget values in Streamlit's pre-rerun callback phase."""

    for key in _FILTER_WIDGET_KEYS:
        st.session_state.pop(key, None)


@dataclass(frozen=True, slots=True)
class FilterContext:
    filters: FilterSet
    basis: QuantityBasis
    period_label: str
    data_min_date: date
    data_max_date: date

    @property
    def quantity_label(self) -> str:
        return "Eaches" if self.basis == QuantityBasis.EACHES else "Case-equivalents"

    @property
    def dimension_chips(self) -> list[str]:
        chips: list[str] = [self.quantity_label]
        groups = (
            ("Customer region", self.filters.customer_regions),
            ("DC region", self.filters.warehouse_regions),
            ("Warehouse", self.filters.warehouse_codes),
            ("Route", self.filters.route_codes),
            ("Outlet", self.filters.outlet_codes),
            ("Channel", self.filters.channels),
            ("Promotion", self.filters.promotion_codes),
            ("Order source", self.filters.order_sources),
        )
        for label, values in groups:
            if len(values) == 1:
                chips.append(f"{label}: {values[0]}")
            elif len(values) > 1:
                chips.append(f"{label}: {len(values)} selected")
        return chips


def with_dates(filters: FilterSet, period: Period) -> FilterSet:
    """Copy an active dimensional scope onto a comparison period."""

    return FilterSet(
        start_date=period.start,
        end_date=period.end,
        customer_regions=filters.customer_regions,
        warehouse_regions=filters.warehouse_regions,
        warehouse_codes=filters.warehouse_codes,
        route_codes=filters.route_codes,
        outlet_codes=filters.outlet_codes,
        channels=filters.channels,
        promotion_codes=filters.promotion_codes,
        order_sources=filters.order_sources,
    )


def _clamp_period(period: Period, minimum: date, maximum: date) -> Period:
    start = max(period.start, minimum)
    end = min(period.end, maximum)
    if start > end:
        start, end = minimum, maximum
    return Period(start, end, period.label)


def _selected_period(minimum: date, maximum: date) -> Period:
    preset = st.sidebar.selectbox(
        "Reporting period",
        (
            "Latest complete fiscal quarter",
            "Last complete month",
            "Full history",
            "Custom range",
        ),
        key="kp_period_preset",
        help="Relative periods are anchored to the latest available Kestrel data.",
    )
    if preset == "Latest complete fiscal quarter":
        return _clamp_period(latest_complete_fiscal_quarter(maximum), minimum, maximum)
    if preset == "Last complete month":
        return _clamp_period(last_complete_month(maximum), minimum, maximum)
    if preset == "Full history":
        return Period(minimum, maximum, "Full available history")

    selected = st.sidebar.date_input(
        "Custom dates",
        value=(minimum, maximum),
        min_value=minimum,
        max_value=maximum,
        key="kp_custom_dates",
    )
    if isinstance(selected, tuple) and len(selected) == 2:
        start, end = selected
    else:
        start = end = selected if isinstance(selected, date) else maximum
    if start > end:
        start, end = end, start
    return Period(start, end, f"{start:%d %b %Y} – {end:%d %b %Y}")


def _multi_select(label: str, options: list[str], key: str, help_text: str) -> tuple[str, ...]:
    selected = st.sidebar.multiselect(label, options, key=key, help=help_text)
    return tuple(selected)


def render_global_filters(service: AnalyticsService) -> FilterContext:
    """Render the single global scope used by every governed page."""

    service_minimum, service_maximum = service.available_date_range()
    minimum, maximum = service.available_reporting_date_range()
    options = service.filter_options()

    st.sidebar.markdown("### Reporting scope")
    period = _selected_period(minimum, maximum)
    basis_value = st.sidebar.radio(
        "Quantity basis",
        options=(QuantityBasis.EACHES, QuantityBasis.CASE_EQUIVALENTS),
        format_func=lambda value: (
            "Eaches · commercial default"
            if value == QuantityBasis.EACHES
            else "Case-equivalents · alternate view"
        ),
        key="kp_quantity_basis",
        horizontal=False,
    )

    with st.sidebar.expander("Business dimensions", expanded=True):
        customer_regions = _multi_select(
            "Customer region",
            options["customer_regions"],
            "kp_customer_regions",
            "Sales/customer geography. Ask Kestrel requires this lens to be explicit.",
        )
        warehouse_regions = _multi_select(
            "DC region",
            options["warehouse_regions"],
            "kp_warehouse_regions",
            "Origin warehouse geography; kept separate from customer region.",
        )
        warehouse_codes = _multi_select(
            "Warehouse",
            options["warehouse_codes"],
            "kp_warehouse_codes",
            "Distribution centre captured on the order/delivery.",
        )
        route_codes = _multi_select(
            "Route",
            options["route_codes"],
            "kp_route_codes",
            "Historical route captured on the order, not the outlet's current route.",
        )
        channels = _multi_select(
            "Channel",
            options["channels"],
            "kp_channels",
            "Customer channel captured on the order.",
        )
        outlet_codes = _multi_select(
            "Outlet",
            options["outlet_codes"],
            "kp_outlet_codes",
            "Only eligible active, non-test outlets contribute to service KPIs.",
        )
        promotion_codes = _multi_select(
            "Recorded promotion",
            options.get("promotion_codes", []),
            "kp_promotion_codes",
            "Promotion code recorded on the order; association does not imply causal uplift.",
        )
        order_sources = _multi_select(
            "Order source",
            options.get("order_sources", []),
            "kp_order_sources",
            "Source system recorded on the order and retained through service evidence.",
        )

    st.sidebar.caption(
        f"Governed reporting dates: {minimum:%d %b %Y} – {maximum:%d %b %Y}. "
        f"Requested-delivery service dates: {service_minimum:%d %b %Y} – "
        f"{service_maximum:%d %b %Y}. Inventory and external sources retain their own "
        "declared date bases."
    )
    st.sidebar.button(
        "Reset all filters",
        width="stretch",
        on_click=_reset_filter_state,
    )

    return FilterContext(
        filters=FilterSet(
            start_date=period.start,
            end_date=period.end,
            customer_regions=customer_regions,
            warehouse_regions=warehouse_regions,
            warehouse_codes=warehouse_codes,
            route_codes=route_codes,
            outlet_codes=outlet_codes,
            channels=channels,
            promotion_codes=promotion_codes,
            order_sources=order_sources,
        ),
        basis=basis_value,
        period_label=period.label,
        data_min_date=minimum,
        data_max_date=maximum,
    )
