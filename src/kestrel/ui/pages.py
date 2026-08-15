"""Business pages for the Kestrel Streamlit control tower."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from kestrel.config import Settings
from kestrel.metrics.definitions import MetricDefinition
from kestrel.metrics.periods import Period, previous_period
from kestrel.metrics.service import AnalyticsService, MetricValue, QuantityBasis
from kestrel.ui.components import (
    GOLD,
    GRID,
    INK,
    NAVY,
    PAPER,
    PLOTLY_CONFIG,
    RED,
    TEAL,
    MetricCard,
    comparison_delta,
    dataframe_or_empty,
    format_compact_number,
    format_inr,
    format_metric_value,
    page_header,
    render_callout,
    render_definitions,
    render_empty_state,
    render_metric_cards,
    render_source_note,
    section_header,
)
from kestrel.ui.filters import FilterContext, with_dates
from kestrel.ui.optional import (
    ask_kestrel,
    call_external,
    external_analytics,
)

Definitions = dict[str, MetricDefinition]


def _figure_layout(figure: go.Figure, *, height: int = 340) -> go.Figure:
    figure.update_layout(
        height=height,
        margin={"l": 18, "r": 18, "t": 42, "b": 18},
        paper_bgcolor="rgba(255,255,255,0)",
        plot_bgcolor=PAPER,
        font={"family": "Inter, ui-sans-serif, system-ui", "color": INK, "size": 12},
        title_font={"color": NAVY, "size": 15},
        legend={"orientation": "h", "y": 1.08, "x": 0},
        hoverlabel={"bgcolor": NAVY, "font_color": "white"},
    )
    figure.update_xaxes(gridcolor=GRID, zeroline=False)
    figure.update_yaxes(gridcolor=GRID, zeroline=False)
    return figure


def _plot(figure: go.Figure) -> None:
    st.plotly_chart(figure, width="stretch", config=PLOTLY_CONFIG)


def _safe_dimension(frame: pd.DataFrame) -> pd.DataFrame:
    display = frame.copy()
    if "dimension_value" in display:
        display["dimension_value"] = display["dimension_value"].fillna("Unknown").astype(str)
    return display


def _percent(value: float | None) -> str:
    return "Not available" if value is None else f"{value * 100:.1f}%"


def _metric_detail(metric: MetricValue | None, noun: str) -> str:
    if metric is None or metric.denominator is None:
        return "No denominator is available for this scope."
    numerator = format_compact_number(metric.numerator)
    denominator = format_compact_number(metric.denominator)
    return f"{numerator} of {denominator} {noun}"


def _previous_summary(
    service: AnalyticsService,
    context: FilterContext,
) -> dict[str, MetricValue] | None:
    active_period = Period(
        context.filters.start_date,
        context.filters.end_date,
        context.period_label,
    )
    prior = previous_period(active_period)
    if prior.end < context.data_min_date:
        return None
    bounded = Period(max(prior.start, context.data_min_date), prior.end, prior.label)
    return service.executive_summary(with_dates(context.filters, bounded), context.basis)


def _rate_cards(
    summary: dict[str, MetricValue],
    previous: dict[str, MetricValue] | None = None,
) -> list[MetricCard]:
    prior = previous or {}
    return [
        MetricCard(
            "Fill rate",
            format_metric_value(summary.get("fill_rate")),
            _metric_detail(summary.get("fill_rate"), "ordered units delivered"),
            comparison_delta(summary.get("fill_rate"), prior.get("fill_rate")),
            "positive",
        ),
        MetricCard(
            "Strict OTIF",
            format_metric_value(summary.get("strict_otif")),
            _metric_detail(summary.get("strict_otif"), "eligible orders"),
            comparison_delta(summary.get("strict_otif"), prior.get("strict_otif")),
            "danger",
        ),
        MetricCard(
            "Timestamp-derived on time",
            format_metric_value(summary.get("on_time_rate")),
            _metric_detail(summary.get("on_time_rate"), "eligible orders"),
            comparison_delta(summary.get("on_time_rate"), prior.get("on_time_rate")),
            "info",
        ),
        MetricCard(
            "Excursions per 100 chilled",
            format_metric_value(summary.get("temperature_excursions_per_100")),
            _metric_detail(summary.get("temperature_excursions_per_100"), "chilled deliveries"),
            comparison_delta(
                summary.get("temperature_excursions_per_100"),
                prior.get("temperature_excursions_per_100"),
            ),
            "warning",
        ),
        MetricCard(
            "Near-expiry available stock",
            format_metric_value(summary.get("near_expiry_cases")),
            "Cases expiring within the governed snapshot-relative window",
            comparison_delta(summary.get("near_expiry_cases"), prior.get("near_expiry_cases")),
            "warning",
        ),
        MetricCard(
            "Approved credit-note leakage",
            format_metric_value(summary.get("approved_credit_note_rate")),
            _metric_detail(
                summary.get("approved_credit_note_rate"), "rupees of estimated dispatch value"
            ),
            comparison_delta(
                summary.get("approved_credit_note_rate"),
                prior.get("approved_credit_note_rate"),
            ),
            "danger",
        ),
    ]


def _render_service_trend(frame: pd.DataFrame, *, title: str) -> None:
    if frame.empty:
        render_empty_state(
            "No trend data",
            "No eligible completed orders match the selected reporting scope.",
        )
        return
    display = frame.copy()
    display["Fill rate"] = display["fill_rate"] * 100
    display["On time"] = display["on_time_rate"] * 100
    display["Strict OTIF"] = display["strict_otif_rate"] * 100
    long = display.melt(
        id_vars=["month"],
        value_vars=["Fill rate", "On time", "Strict OTIF"],
        var_name="Metric",
        value_name="Rate",
    )
    figure = px.line(
        long,
        x="month",
        y="Rate",
        color="Metric",
        markers=True,
        color_discrete_map={"Fill rate": TEAL, "On time": NAVY, "Strict OTIF": RED},
        title=title,
    )
    figure.update_yaxes(title="Percent", ticksuffix="%", rangemode="tozero")
    figure.update_xaxes(title=None)
    _plot(_figure_layout(figure))


def _render_worst_table(
    service: AnalyticsService,
    context: FilterContext,
    dimension: str,
    title: str,
) -> None:
    frame = _safe_dimension(
        service.service_by_dimension(
            context.filters,
            dimension,
            context.basis,
            limit=5,
            worst_first=True,
        )
    )
    st.markdown(f"**{title}**")
    if frame.empty:
        st.caption("No eligible records in this scope.")
        return
    display = frame[["dimension_value", "orders", "fill_rate", "short_quantity"]].copy()
    display["fill_rate"] = display["fill_rate"] * 100
    display.columns = ["Dimension", "Orders", "Fill rate", "Short quantity"]
    st.dataframe(
        display,
        hide_index=True,
        width="stretch",
        column_config={
            "Fill rate": st.column_config.NumberColumn(format="%.1f%%"),
            "Short quantity": st.column_config.NumberColumn(format="%.1f"),
        },
    )


def render_executive(
    service: AnalyticsService,
    context: FilterContext,
    definitions: Definitions,
    settings: Settings,
) -> None:
    del settings
    page_header(
        "Executive Control Tower",
        "One governed view of where customer service is being lost, where measured value "
        "is leaking, and which operating areas warrant attention first.",
        period_label=context.period_label,
        chips=context.dimension_chips,
    )
    summary = service.executive_summary(context.filters, context.basis)
    previous = _previous_summary(service, context)
    render_metric_cards(_rate_cards(summary, previous), columns=3)

    strict_otif = summary.get("strict_otif")
    eligible_orders = strict_otif.denominator if strict_otif else None
    render_callout(
        "Strict OTIF is correctly reported as 0%",
        "Every supplied order line is short-delivered, so no eligible order can be in full. "
        f"The current scope contains {format_compact_number(eligible_orders)} eligible orders. "
        "Fill rate and on-time performance remain informative; no tolerance has been invented.",
        tone="danger",
    )

    section_header(
        "Service trajectory",
        "Ratio-of-sums fill rate and order-level timing for the selected "
        "requested-delivery cohort.",
    )
    trend, flow = st.columns((1.65, 1))
    with trend:
        _render_service_trend(
            service.service_trend(context.filters, context.basis),
            title="Monthly service performance",
        )
    with flow:
        fill = summary.get("fill_rate")
        ordered = fill.denominator if fill else None
        delivered = fill.numerator if fill else None
        short = (ordered - delivered) if ordered is not None and delivered is not None else None
        st.markdown("**Fulfilment flow**")
        render_metric_cards(
            [
                MetricCard("Ordered", format_compact_number(ordered), context.quantity_label),
                MetricCard(
                    "Delivered",
                    format_compact_number(delivered),
                    "Delivered within the selected cohort",
                    tone="positive",
                ),
                MetricCard(
                    "Short",
                    format_compact_number(short),
                    "Ordered less delivered; association, not blame",
                    tone="danger",
                ),
            ],
            columns=1,
        )

    section_header(
        "Worst performers",
        "Lowest fill-rate groups are visible immediately; rankings use the active quantity basis.",
    )
    first_row = st.columns(2)
    with first_row[0]:
        _render_worst_table(service, context, "customer_region", "Customer regions")
    with first_row[1]:
        _render_worst_table(service, context, "warehouse", "Warehouses")
    second_row = st.columns(2)
    with second_row[0]:
        _render_worst_table(service, context, "route", "Routes")
    with second_row[1]:
        _render_worst_table(service, context, "outlet", "Outlets")

    section_header(
        "Largest shortage signals", "Associated operational drivers at order-line grain."
    )
    driver_columns = st.columns(2)
    for column, dimension, title in (
        (driver_columns[0], "short_reason", "Short quantity by recorded reason"),
        (driver_columns[1], "category", "Short quantity by category"),
    ):
        with column:
            frame = _safe_dimension(
                service.shortage_contributors(context.filters, dimension, limit=8)
            )
            if frame.empty:
                render_empty_state("No shortage rows", "No shortage evidence matched this scope.")
                continue
            amount_column = (
                "short_eaches"
                if context.basis == QuantityBasis.EACHES
                else "short_case_equivalents"
            )
            figure = px.bar(
                frame.sort_values(amount_column),
                x=amount_column,
                y="dimension_value",
                orientation="h",
                color_discrete_sequence=[GOLD],
                title=title,
            )
            figure.update_xaxes(title=context.quantity_label)
            figure.update_yaxes(title=None)
            _plot(_figure_layout(figure, height=330))

    render_definitions(
        definitions,
        [
            "fill_rate",
            "strict_otif",
            "on_time_rate",
            "temperature_excursions_per_100",
            "near_expiry_cases",
            "approved_credit_note_rate",
        ],
    )
    render_source_note(
        "Core source: governed DuckDB semantic facts built read-only from the supplied Kestrel "
        "operational database. Date basis varies by metric and is stated in the definitions."
    )


def render_service(
    service: AnalyticsService,
    context: FilterContext,
    definitions: Definitions,
    settings: Settings,
) -> None:
    del settings
    page_header(
        "Service & Fulfilment",
        "Trace quantity loss and delivery timing from the requested customer commitment to the "
        "warehouse, route, outlet, category, and recorded shortage signal.",
        period_label=context.period_label,
        chips=context.dimension_chips,
        eyebrow="CUSTOMER SERVICE",
    )
    summary = service.executive_summary(context.filters, context.basis)
    render_metric_cards(
        [
            _rate_cards(summary)[0],
            _rate_cards(summary)[2],
            MetricCard(
                "More than two hours late",
                format_metric_value(summary.get("late_over_2h_rate")),
                _metric_detail(summary.get("late_over_2h_rate"), "eligible orders"),
                tone="danger",
            ),
            _rate_cards(summary)[1],
        ],
        columns=4,
    )
    render_callout(
        "Two timing signals are retained",
        "Headline on-time and late-over-two-hours measures use parsed planned and actual arrival "
        "timestamps. The conflicting stored delay field remains available for audit, not "
        "silently mixed.",
        tone="warning",
    )

    section_header("Performance over time")
    _render_service_trend(
        service.service_trend(context.filters, context.basis),
        title=f"Monthly service · {context.quantity_label}",
    )

    section_header(
        "Rank and diagnose",
        "Choose an operating dimension; the chart and evidence remain on the governed scope.",
    )
    dimension_labels = {
        "customer_region": "Customer region",
        "warehouse_region": "DC region",
        "warehouse": "Warehouse",
        "route": "Route",
        "outlet": "Outlet",
        "channel": "Channel",
    }
    selected_dimension = st.selectbox(
        "Service dimension",
        tuple(dimension_labels),
        format_func=dimension_labels.get,
        key="kp_service_dimension",
    )
    frame = _safe_dimension(
        service.service_by_dimension(
            context.filters,
            selected_dimension,
            context.basis,
            limit=30,
            worst_first=True,
        )
    )
    if frame.empty:
        render_empty_state(
            "No service groups",
            "Broaden the date or dimension filters to restore eligible completed orders.",
        )
    else:
        display = frame.copy()
        display["fill_rate_pct"] = display["fill_rate"] * 100
        figure = px.bar(
            display.sort_values("fill_rate_pct", ascending=False),
            x="fill_rate_pct",
            y="dimension_value",
            orientation="h",
            color="fill_rate_pct",
            color_continuous_scale=[RED, GOLD, TEAL],
            range_color=(0, 100),
            title=f"Fill rate by {dimension_labels[selected_dimension].lower()}",
            hover_data={"orders": ":,", "short_quantity": ":,.1f"},
        )
        figure.update_xaxes(title="Fill rate", ticksuffix="%", range=[0, 100])
        figure.update_yaxes(title=None)
        figure.update_coloraxes(showscale=False)
        _plot(_figure_layout(figure, height=max(360, min(760, len(display) * 26))))

    contributors = {
        "short_reason": "Recorded short reason",
        "category": "Category",
        "warehouse": "Warehouse",
        "route": "Route",
        "sku": "SKU",
    }
    selected_contributor = st.selectbox(
        "Shortage contributor",
        tuple(contributors),
        format_func=contributors.get,
        key="kp_shortage_dimension",
    )
    shortage = _safe_dimension(
        service.shortage_contributors(context.filters, selected_contributor, limit=20)
    )
    amount_column = (
        "short_eaches" if context.basis == QuantityBasis.EACHES else "short_case_equivalents"
    )
    if shortage.empty:
        render_empty_state("No shortage evidence", "No short-delivered lines match this scope.")
    else:
        figure = px.bar(
            shortage.head(12).sort_values(amount_column),
            x=amount_column,
            y="dimension_value",
            orientation="h",
            color_discrete_sequence=[GOLD],
            title=f"Largest shortfalls by {contributors[selected_contributor].lower()}",
        )
        figure.update_xaxes(title=context.quantity_label)
        figure.update_yaxes(title=None)
        _plot(_figure_layout(figure, height=390))

    section_header(
        "Order evidence",
        "Lowest-fill eligible orders supporting the view; limited to 200 rows for review.",
    )
    evidence = service.service_evidence(context.filters, limit=200).copy()
    if not evidence.empty:
        evidence["fill_rate_eaches"] *= 100
        evidence["fill_rate_case_equivalents"] *= 100
    dataframe_or_empty(
        evidence,
        empty_title="No order evidence",
        empty_body="No eligible completed orders matched the active scope.",
        column_config={
            "fill_rate_eaches": st.column_config.NumberColumn("Each fill rate", format="%.1f%%"),
            "fill_rate_case_equivalents": st.column_config.NumberColumn(
                "Case-equivalent fill", format="%.1f%%"
            ),
            "derived_delay_minutes": st.column_config.NumberColumn(
                "Derived delay", format="%d min"
            ),
        },
    )
    render_definitions(
        definitions,
        ["fill_rate", "strict_otif", "on_time_rate", "late_over_2h_rate"],
    )


def render_cold_chain(
    service: AnalyticsService,
    context: FilterContext,
    definitions: Definitions,
    settings: Settings,
) -> None:
    del settings
    page_header(
        "Cold Chain & Inventory",
        "Monitor temperature-control exceptions, snapshot-relative expiry exposure, and approved "
        "cold-chain credit notes without implying unsupported causality.",
        period_label=context.period_label,
        chips=context.dimension_chips,
        eyebrow="PRODUCT INTEGRITY",
    )
    summary = service.executive_summary(context.filters, context.basis)
    excursion = summary.get("temperature_excursions_per_100")
    inventory = service.inventory_risk(context.filters, "warehouse")
    snapshot_date = inventory.attrs.get("snapshot_date")
    near_expiry_total = float(inventory["near_expiry_cases"].sum()) if not inventory.empty else None
    damaged_total = float(inventory["damaged_cases"].sum()) if not inventory.empty else None
    blocked_total = float(inventory["blocked_cases"].sum()) if not inventory.empty else None
    render_metric_cards(
        [
            MetricCard(
                "Excursions per 100 chilled",
                format_metric_value(excursion),
                _metric_detail(excursion, "chilled deliveries"),
                tone="danger",
            ),
            MetricCard(
                "Near-expiry available cases",
                format_compact_number(near_expiry_total),
                f"Snapshot as of {snapshot_date:%d %b %Y}"
                if snapshot_date
                else "No inventory snapshot in scope",
                tone="warning",
            ),
            MetricCard(
                "Damaged cases",
                format_compact_number(damaged_total),
                "Latest governed inventory snapshot",
                tone="danger",
            ),
            MetricCard(
                "Blocked cases",
                format_compact_number(blocked_total),
                "Latest governed inventory snapshot",
                tone="warning",
            ),
        ],
        columns=4,
    )
    render_callout(
        "Association is not causation",
        "A temperature flag, route, warehouse, and return can occur together without proving which "
        "party or event caused the loss. This page reports associated operating signals only.",
        tone="warning",
    )

    section_header("Temperature-control hotspots")
    cold_dimensions = {
        "warehouse": "Warehouse",
        "route": "Route",
        "customer_region": "Customer region",
        "warehouse_region": "DC region",
    }
    selected = st.selectbox(
        "Cold-chain dimension",
        tuple(cold_dimensions),
        format_func=cold_dimensions.get,
        key="kp_cold_dimension",
    )
    hotspots = _safe_dimension(service.cold_chain_by_dimension(context.filters, selected, limit=20))
    if hotspots.empty:
        render_empty_state(
            "No chilled deliveries",
            "No eligible chilled deliveries match the selected reporting scope.",
        )
    else:
        figure = px.bar(
            hotspots.sort_values("excursions_per_100"),
            x="excursions_per_100",
            y="dimension_value",
            orientation="h",
            color="excursions",
            color_continuous_scale=[GOLD, RED],
            title=f"Excursions per 100 by {cold_dimensions[selected].lower()}",
            hover_data={"chilled_deliveries": ":,", "excursions": ":,"},
        )
        figure.update_xaxes(title="Excursions per 100")
        figure.update_yaxes(title=None)
        figure.update_coloraxes(colorbar_title="Excursions")
        _plot(_figure_layout(figure, height=430))

    section_header(
        "Inventory risk",
        "Near-expiry means positive available stock with 0–30 days remaining by default.",
    )
    inventory_dimensions = {
        "warehouse": "Warehouse",
        "warehouse_region": "DC region",
        "category": "Category",
        "sku": "SKU",
    }
    inventory_dimension = st.selectbox(
        "Inventory dimension",
        tuple(inventory_dimensions),
        format_func=inventory_dimensions.get,
        key="kp_inventory_dimension",
    )
    inventory_view = _safe_dimension(service.inventory_risk(context.filters, inventory_dimension))
    if inventory_view.empty:
        render_empty_state(
            "No inventory snapshot",
            "No weekly snapshot exists on or before the selected period end for this DC scope.",
        )
    else:
        chart_frame = inventory_view.head(15).sort_values("near_expiry_cases")
        figure = px.bar(
            chart_frame,
            x=["near_expiry_cases", "damaged_cases", "blocked_cases"],
            y="dimension_value",
            orientation="h",
            barmode="group",
            color_discrete_sequence=[GOLD, RED, NAVY],
            title=f"Inventory exposure by {inventory_dimensions[inventory_dimension].lower()}",
            labels={"value": "Cases", "variable": "Exposure"},
        )
        figure.update_yaxes(title=None)
        _plot(_figure_layout(figure, height=430))

    approved_reasons = _safe_dimension(
        service.returns_by_dimension(
            context.filters,
            "reason",
            statuses=("APPROVED",),
            limit=30,
        )
    )
    cold_returns = (
        approved_reasons[
            approved_reasons["dimension_value"].str.contains("RT06", case=False, na=False)
        ]
        if not approved_reasons.empty
        else approved_reasons
    )
    section_header("Cold-chain return evidence")
    if cold_returns.empty:
        render_empty_state(
            "No approved cold-chain credit notes",
            "No RT06 approved credit-note lines match this reporting scope.",
        )
    else:
        value = float(cold_returns["credit_note_value_inr"].sum())
        lines = int(cold_returns["credit_note_lines"].sum())
        render_metric_cards(
            [
                MetricCard(
                    "Approved RT06 credit notes",
                    format_inr(value),
                    f"{lines:,} approved credit-note lines",
                    tone="danger",
                )
            ],
            columns=1,
        )
    render_definitions(
        definitions,
        ["temperature_excursions_per_100", "near_expiry_cases", "approved_credit_note_rate"],
    )


def _sum_column(frame: pd.DataFrame, column: str) -> float | None:
    if column not in frame:
        return None
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    return float(values.sum()) if not values.empty else None


def _render_freight_frames(
    warehouse: pd.DataFrame,
    carrier: pd.DataFrame,
) -> None:
    if warehouse.empty and carrier.empty:
        reason = warehouse.attrs.get("unavailable_reason") or carrier.attrs.get(
            "unavailable_reason"
        )
        render_empty_state(
            "No governed freight rows",
            str(reason or "No partner invoices match the active period and DC scope."),
        )
        return

    billed = _sum_column(warehouse, "freight_cost_inr")
    detention = _sum_column(warehouse, "detention_cost_inr")
    invoice_count = _sum_column(warehouse, "invoice_count")
    delivered_cases = _sum_column(warehouse, "delivered_case_equivalents")
    billed_per_case = (
        billed / delivered_cases
        if billed is not None and delivered_cases is not None and delivered_cases != 0.0
        else None
    )
    render_metric_cards(
        [
            MetricCard(
                "Billed freight",
                format_inr(billed),
                f"{format_compact_number(invoice_count)} partner invoices",
                tone="info",
            ),
            MetricCard(
                "Billed freight / delivered case",
                f"₹{billed_per_case:,.2f}" if billed_per_case is not None else "Not available",
                "Period × warehouse ratio of independently aggregated totals",
                tone="warning",
            ),
            MetricCard(
                "Detention charges",
                format_inr(detention),
                "Separately identified on partner invoices",
                tone="danger",
            ),
            MetricCard(
                "Delivered case-equivalents",
                format_compact_number(delivered_cases),
                "Operational denominator over the same period and warehouses",
                tone="positive",
            ),
        ],
        columns=4,
    )

    chart_columns = st.columns(2)
    with chart_columns[0]:
        valid_warehouse = warehouse.dropna(subset=["freight_cost_per_delivered_case_inr"]).copy()
        if valid_warehouse.empty:
            render_empty_state(
                "No warehouse ratio",
                "No warehouse has both billed freight and delivered cases in this scope.",
            )
        else:
            valid_warehouse["label"] = valid_warehouse["warehouse_name"].fillna(
                valid_warehouse["warehouse_code"]
            )
            figure = px.bar(
                valid_warehouse.sort_values("freight_cost_per_delivered_case_inr"),
                x="freight_cost_per_delivered_case_inr",
                y="label",
                orientation="h",
                color_discrete_sequence=[GOLD],
                title="Billed freight per delivered case-equivalent",
                hover_data={"freight_cost_inr": ":,.0f", "invoice_count": ":,.0f"},
            )
            figure.update_xaxes(title="INR per case-equivalent", tickprefix="₹")
            figure.update_yaxes(title=None)
            _plot(_figure_layout(figure, height=390))
    with chart_columns[1]:
        if carrier.empty:
            render_empty_state(
                "No carrier spend",
                "No carrier invoice rows match the active freight scope.",
            )
        else:
            figure = px.bar(
                carrier.sort_values("freight_cost_inr"),
                x="freight_cost_inr",
                y="carrier_name",
                orientation="h",
                color_discrete_sequence=[NAVY],
                title="Billed freight by carrier",
                hover_data={"invoice_count": ":,.0f", "detention_cost_inr": ":,.0f"},
            )
            figure.update_xaxes(title="Billed freight", tickprefix="₹")
            figure.update_yaxes(title=None)
            _plot(_figure_layout(figure, height=390))

    attribution = warehouse.attrs.get("attribution") or carrier.attrs.get("attribution")
    ignored = warehouse.attrs.get("ignored_filters") or carrier.attrs.get("ignored_filters")
    if attribution:
        render_source_note(str(attribution))
    if ignored:
        st.caption(
            "Freight source cannot apply these active filters: " + ", ".join(map(str, ignored))
        )


def render_leakage(
    service: AnalyticsService,
    context: FilterContext,
    definitions: Definitions,
    settings: Settings,
) -> None:
    page_header(
        "Measured Leakage & Freight",
        "Quantify approved credit-note leakage and billed logistics cost using the evidence "
        "available in the pack. This is not an accounting-profit statement.",
        period_label=context.period_label,
        chips=context.dimension_chips,
        eyebrow="VALUE PROTECTION",
    )
    summary = service.executive_summary(context.filters, context.basis)
    credit = summary.get("approved_credit_note_rate")
    render_metric_cards(
        [
            MetricCard(
                "Approved credit-note leakage",
                format_metric_value(credit),
                "Approved credit notes divided by estimated delivered line value",
                tone="danger",
            ),
            MetricCard(
                "Approved credit-note value",
                format_inr(credit.numerator if credit else None),
                f"{credit.records:,} approved credit-note lines" if credit else "No records",
                tone="danger",
            ),
            MetricCard(
                "Estimated dispatch value",
                format_inr(credit.denominator if credit else None),
                "Delivered proportion of booked net line value",
                tone="info",
            ),
        ],
        columns=3,
    )
    render_callout(
        "Measured leakage, not profit",
        "The supplied pack does not contain complete COGS, labour, overhead, collections, "
        "bad debt, "
        "or tax settlement. Credit notes and freight are therefore shown as measured components, "
        "never as net profit or cash retained.",
        tone="warning",
    )

    section_header("Credit-note status")
    status = service.credit_status_summary(context.filters)
    if status.empty:
        render_empty_state(
            "No credit-note activity",
            "No return-date records match the active period and dimensions.",
        )
    else:
        figure = px.bar(
            status,
            x="credit_note_status",
            y="credit_note_value_inr",
            color="credit_note_status",
            color_discrete_map={"APPROVED": TEAL, "PENDING": GOLD, "REJECTED": RED},
            title="Credit-note value by workflow status",
            text_auto=".3s",
        )
        figure.update_xaxes(title=None)
        figure.update_yaxes(title="Value (INR)", tickprefix="₹")
        figure.update_layout(showlegend=False)
        _plot(_figure_layout(figure))

    return_dimensions = {
        "category": "Category",
        "reason": "Reason",
        "warehouse": "Warehouse",
        "customer_region": "Customer region",
        "route": "Route",
        "outlet": "Outlet",
        "sku": "SKU",
    }
    selected = st.selectbox(
        "Approved leakage dimension",
        tuple(return_dimensions),
        format_func=return_dimensions.get,
        key="kp_return_dimension",
    )
    returns = _safe_dimension(
        service.returns_by_dimension(
            context.filters,
            selected,
            statuses=("APPROVED",),
            limit=20,
        )
    )
    if returns.empty:
        render_empty_state(
            "No approved leakage rows",
            "No approved credit-note lines match this scope.",
        )
    else:
        figure = px.bar(
            returns.sort_values("credit_note_value_inr"),
            x="credit_note_value_inr",
            y="dimension_value",
            orientation="h",
            color_discrete_sequence=[RED],
            title=f"Approved credit-note value by {return_dimensions[selected].lower()}",
        )
        figure.update_xaxes(title="Approved value", tickprefix="₹")
        figure.update_yaxes(title=None)
        _plot(_figure_layout(figure, height=430))

    section_header(
        "Freight",
        "Only partner invoices qualify as freight cost; driver-entered fuel is never substituted.",
    )
    external = external_analytics(settings)
    if not external.available:
        render_empty_state("Governed freight metric unavailable", external.message)
    else:
        warehouse_result = call_external(
            external.payload,
            "freight_by_warehouse",
            context.filters,
        )
        carrier_result = call_external(
            external.payload,
            "freight_by_carrier",
            context.filters,
        )
        if (
            warehouse_result.available
            and carrier_result.available
            and isinstance(warehouse_result.payload, pd.DataFrame)
            and isinstance(carrier_result.payload, pd.DataFrame)
        ):
            _render_freight_frames(
                warehouse_result.payload,
                carrier_result.payload,
            )
        else:
            messages = [
                result.message
                for result in (warehouse_result, carrier_result)
                if not result.available
            ]
            render_empty_state(
                "Freight temporarily unavailable",
                " ".join(messages) or "The governed freight result was not tabular.",
            )
    render_callout(
        "Freight attribution boundary",
        "The API has no order or delivery ID. Freight-per-case must aggregate numerator and "
        "denominator independently by period plus warehouse, or period plus route. Carrier-level "
        "delivered cases and invoice-to-delivery lineage are not claimed.",
        tone="warning",
    )
    render_definitions(
        definitions,
        ["approved_credit_note_rate", "freight_cost_per_case"],
    )


def render_market(
    service: AnalyticsService,
    context: FilterContext,
    definitions: Definitions,
    settings: Settings,
) -> None:
    del service
    page_header(
        "Market Position",
        "Compare current Kestrel MRP with the lowest latest-observed competitor shelf price. "
        "Gaps appear only after a high-confidence, non-ambiguous SKU match.",
        period_label=f"{context.period_label} order mix · latest observed prices",
        chips=context.dimension_chips,
        eyebrow="COMMERCIAL SIGNAL",
    )
    external = external_analytics(settings)
    if not external.available:
        render_empty_state("Competitor metrics unavailable", external.message)
        render_definitions(definitions, ["competitor_price_gap"])
        return

    quality_result = call_external(external.payload, "competitor_match_quality")
    quality = (
        quality_result.payload
        if quality_result.available and isinstance(quality_result.payload, pd.DataFrame)
        else pd.DataFrame()
    )
    city_options = (
        sorted(quality["city"].dropna().astype(str).unique()) if "city" in quality else []
    )
    if not city_options:
        city_options = ["Mumbai"]
    elif "Mumbai" in city_options:
        city_options = ["Mumbai", *(item for item in city_options if item != "Mumbai")]
    city = st.selectbox("Market city", city_options, key="kp_market_city")

    initial_result = call_external(
        external.payload,
        "competitor_price_gap",
        context.filters,
        city=city,
        top_n=100,
    )
    if not initial_result.available or not isinstance(initial_result.payload, pd.DataFrame):
        render_empty_state("Competitor metrics unavailable", initial_result.message)
        render_definitions(definitions, ["competitor_price_gap"])
        return
    initial = initial_result.payload
    categories = (
        sorted(initial["category"].dropna().astype(str).unique()) if "category" in initial else []
    )
    selected_category = st.selectbox(
        "Category",
        ["All categories", *categories],
        key="kp_market_category",
    )
    if selected_category == "All categories":
        frame = initial
    else:
        category_result = call_external(
            external.payload,
            "competitor_price_gap",
            context.filters,
            city=city,
            category=selected_category,
            top_n=100,
        )
        if not category_result.available or not isinstance(category_result.payload, pd.DataFrame):
            render_empty_state("Category price gap unavailable", category_result.message)
            return
        frame = category_result.payload

    if frame.empty:
        reason = frame.attrs.get("unavailable_reason")
        render_empty_state(
            "No price-position rows",
            str(reason or "No eligible high-value Kestrel SKUs match the active scope and market."),
        )
        render_definitions(definitions, ["competitor_price_gap"])
        return

    matched = frame.dropna(subset=["lowest_competitor_price_inr"]).copy()
    observation_dates = (
        pd.to_datetime(matched["latest_observation_date"], errors="coerce")
        if "latest_observation_date" in matched
        else pd.Series(dtype="datetime64[ns]")
    )
    latest_seen = observation_dates.max()
    average_premium = (
        float(pd.to_numeric(matched["mrp_premium_pct"], errors="coerce").mean())
        if not matched.empty
        else None
    )
    coverage_rate = len(matched) / len(frame) if len(frame) else None
    render_metric_cards(
        [
            MetricCard(
                "Top Kestrel SKUs reviewed",
                f"{len(frame):,}",
                "Ranked by eligible estimated dispatch value",
                tone="info",
            ),
            MetricCard(
                "High-confidence matched",
                f"{len(matched):,}",
                _percent(coverage_rate) + " of reviewed SKUs",
                tone="positive",
            ),
            MetricCard(
                "Average MRP premium",
                f"{average_premium:+.1f}%" if average_premium is not None else "Not available",
                "Versus the lowest qualifying observed shelf price",
                tone="warning",
            ),
            MetricCard(
                "Latest observation",
                latest_seen.strftime("%d %b %Y") if pd.notna(latest_seen) else "Unknown",
                f"BazaarPulse · {city} · not a live price",
                tone="warning",
            ),
        ],
        columns=4,
    )
    methodology = frame.attrs.get("methodology")
    render_callout(
        "Current observation, not historical reconstruction",
        str(
            methodology
            or "Only available, high-confidence matches qualify. Unmatched and ambiguous "
            "listings are retained as coverage evidence and never forced onto a Kestrel SKU."
        ),
        tone="warning",
    )

    section_header(
        "Price position",
        "Positive percentages mean Kestrel current MRP is above the qualifying observed price.",
    )
    if matched.empty:
        render_empty_state(
            "No matched price gaps",
            "Top Kestrel SKUs remain visible below, but none cleared the governed match rules.",
        )
    else:
        chart = matched.nlargest(20, "dispatch_value_inr").copy()
        chart["Position"] = chart["mrp_premium_pct"].apply(
            lambda value: "MRP above observed" if value >= 0 else "MRP below observed"
        )
        figure = px.bar(
            chart.sort_values("mrp_premium_pct"),
            x="mrp_premium_pct",
            y="product_name",
            orientation="h",
            color="Position",
            color_discrete_map={
                "MRP above observed": RED,
                "MRP below observed": TEAL,
            },
            title=f"Current Kestrel MRP premium vs observed shelf price · {city}",
            hover_data={
                "kestrel_mrp_inr": ":.2f",
                "lowest_competitor_price_inr": ":.2f",
                "matched_listings": ":,.0f",
            },
        )
        figure.update_xaxes(title="MRP premium", ticksuffix="%", zeroline=True)
        figure.update_yaxes(title=None)
        _plot(_figure_layout(figure, height=max(400, min(720, len(chart) * 30))))

    section_header(
        "SKU evidence",
        "Unmatched top SKUs remain explicit; a missing competitor price is not treated as zero.",
    )
    columns = [
        "sku_code",
        "product_name",
        "category",
        "dispatch_value_inr",
        "kestrel_mrp_inr",
        "lowest_competitor_price_inr",
        "price_gap_inr",
        "mrp_premium_pct",
        "matched_listings",
        "retailers",
        "latest_observation_date",
    ]
    dataframe_or_empty(
        frame[[column for column in columns if column in frame]],
        empty_title="No SKU rows",
        empty_body="No top-SKU evidence is available for this selection.",
        max_rows=100,
        column_config={
            "dispatch_value_inr": st.column_config.NumberColumn(
                "Estimated dispatch value", format="₹%.0f"
            ),
            "kestrel_mrp_inr": st.column_config.NumberColumn("Kestrel MRP", format="₹%.2f"),
            "lowest_competitor_price_inr": st.column_config.NumberColumn(
                "Lowest observed", format="₹%.2f"
            ),
            "price_gap_inr": st.column_config.NumberColumn("MRP gap", format="₹%.2f"),
            "mrp_premium_pct": st.column_config.NumberColumn("MRP premium", format="%.1f%%"),
            "latest_observation_date": st.column_config.DateColumn(
                "Latest observed", format="DD MMM YYYY"
            ),
        },
    )

    section_header("Match-quality coverage")
    quality_display = quality.copy()
    if "average_confidence" in quality_display:
        quality_display["average_confidence"] = (
            pd.to_numeric(quality_display["average_confidence"], errors="coerce") * 100
        )
    dataframe_or_empty(
        quality_display,
        empty_title="No match-quality evidence",
        empty_body="No competitor entity-resolution snapshot is available.",
        column_config={
            "average_confidence": st.column_config.NumberColumn(
                "Average confidence", format="%.1f%%"
            ),
        },
    )
    render_source_note(
        "Source: governed external snapshot of BazaarPulse observations and deterministic "
        "product-master matching. City and listing availability apply to the observation date."
    )
    render_definitions(definitions, ["competitor_price_gap"])


def _render_evidence_blocks(evidence: Any) -> None:
    if isinstance(evidence, pd.DataFrame):
        dataframe_or_empty(
            evidence,
            empty_title="No supporting rows",
            empty_body="The governed answer contains no supporting rows.",
        )
        return
    if not isinstance(evidence, (list, tuple)):
        return
    if evidence and all(isinstance(item, Mapping) and "rows" not in item for item in evidence):
        dataframe_or_empty(
            pd.DataFrame(evidence),
            empty_title="No supporting rows",
            empty_body="No governed rows matched the interpreted question.",
            max_rows=100,
        )
        return
    for block in evidence:
        if isinstance(block, Mapping):
            title = block.get("title", "Supporting evidence")
            source = block.get("source")
            columns = block.get("columns", ())
            rows = block.get("rows", ())
        else:
            title = getattr(block, "title", "Supporting evidence")
            source = getattr(block, "source", None)
            columns = getattr(block, "columns", ())
            rows = getattr(block, "rows", ())
        st.markdown(f"**{title}**")
        if source:
            st.caption(f"Governed source: {source}")
        frame = pd.DataFrame(rows, columns=columns)
        dataframe_or_empty(
            frame,
            empty_title="No supporting rows",
            empty_body="No governed rows matched the interpreted question.",
            max_rows=100,
        )


def _render_nlq_payload(payload: Any) -> None:
    if isinstance(payload, str):
        st.markdown(payload)
        return

    if isinstance(payload, Mapping):
        summary = payload.get("summary") or payload.get("answer") or payload.get("text")
        metadata_value = payload.get("metadata")
        metadata = metadata_value if isinstance(metadata_value, Mapping) else {}
        interpretation = payload.get("interpretation") or metadata.get("interpretation")
        definition = payload.get("definition") or metadata.get("definition")
        sources = payload.get("sources") or metadata.get("sources", ())
        warnings = payload.get("warnings", ())
        if not warnings and metadata.get("warning"):
            warnings = (metadata["warning"],)
        suggestions = payload.get("suggestions", ())
        evidence = payload.get("evidence")
        if evidence is None:
            evidence = payload.get("data")
    else:
        summary = getattr(payload, "summary", None) or getattr(payload, "answer", None)
        interpretation = getattr(payload, "interpretation", None)
        definition = getattr(payload, "definition", None)
        sources = getattr(payload, "sources", ())
        warnings = getattr(payload, "warnings", ())
        suggestions = getattr(payload, "suggestions", ())
        evidence = getattr(payload, "evidence", None)

    if not summary:
        st.caption("The governed router returned an unsupported presentation payload.")
        return
    st.markdown(str(summary))
    warning_values = (warnings,) if isinstance(warnings, str) else warnings or ()
    for warning in warning_values:
        st.warning(str(warning))
    _render_evidence_blocks(evidence)
    with st.expander("How this answer was governed", expanded=False):
        if interpretation:
            st.markdown(f"**Interpretation**  \n{interpretation}")
        if definition:
            st.markdown(f"**Definition**  \n{definition}")
        if sources:
            source_text = sources if isinstance(sources, str) else " · ".join(map(str, sources))
            st.markdown("**Sources**  \n" + source_text)
    if suggestions:
        suggestion_text = (
            suggestions if isinstance(suggestions, str) else " · ".join(map(str, suggestions))
        )
        st.caption("Try next: " + suggestion_text)


def _guided_diagnostic(
    service: AnalyticsService,
    context: FilterContext,
    diagnostic: str,
) -> pd.DataFrame:
    if diagnostic == "Lowest-fill warehouses":
        return service.service_by_dimension(
            context.filters, "warehouse", context.basis, limit=10, worst_first=True
        )
    if diagnostic == "Largest shortage reasons":
        return service.shortage_contributors(context.filters, "short_reason", limit=10)
    if diagnostic == "Cold-chain hotspots":
        return service.cold_chain_by_dimension(context.filters, "warehouse", limit=10)
    if diagnostic == "Approved credit-note drivers":
        return service.returns_by_dimension(
            context.filters, "reason", statuses=("APPROVED",), limit=10
        )
    return service.discontinued_order_evidence(context.filters, limit=50)


def render_ask(
    service: AnalyticsService,
    context: FilterContext,
    definitions: Definitions,
    settings: Settings,
) -> None:
    del settings
    page_header(
        "Ask Kestrel",
        "Questions are resolved through a governed intent router and the same metric service as "
        "the dashboard. Unrestricted text-to-SQL is never used as a fallback.",
        period_label=context.period_label,
        chips=context.dimension_chips,
        eyebrow="GOVERNED QUESTION INTERFACE",
    )
    examples = (
        "Why did fill rate drop in the West?",
        "Which five outlets had the lowest case fill rate?",
        "What was OTIF by customer region?",
        "Which categories drove approved credit notes?",
        "Which routes were more than two hours late most often?",
    )
    st.caption("Example questions: " + "  ·  ".join(examples))
    question = st.text_area(
        "Question",
        placeholder="Ask a service, cold-chain, leakage, freight, or price-position question…",
        key="kp_nlq_question",
        height=90,
    )
    asked = st.button("Ask with governed metrics", type="primary")
    if asked:
        if not question.strip():
            st.warning("Enter a question before submitting.")
        else:
            with st.spinner("Checking the governed metric catalogue…"):
                result = ask_kestrel(
                    question.strip(),
                    service=service,
                    filters=context.filters,
                    basis=context.basis,
                    definitions=definitions,
                )
            if result.available:
                section_header("Answer")
                _render_nlq_payload(result.payload)
            else:
                render_empty_state("Governed question router unavailable", result.message)

    section_header(
        "Guided diagnostics",
        "These deterministic views remain available even when the natural-language router "
        "is absent.",
    )
    diagnostic = st.selectbox(
        "Diagnostic",
        (
            "Lowest-fill warehouses",
            "Largest shortage reasons",
            "Cold-chain hotspots",
            "Approved credit-note drivers",
            "Orders after SKU discontinuation",
        ),
        key="kp_guided_diagnostic",
    )
    frame = _guided_diagnostic(service, context, diagnostic)
    dataframe_or_empty(
        frame,
        empty_title="No evidence for this diagnostic",
        empty_body="No governed rows match the active reporting scope.",
        max_rows=50,
    )
    render_source_note(
        "Answers must state the exact period, filters, metric definition, and supporting rows. "
        "Ambiguous questions should ask for clarification rather than invent a number."
    )


def render_data_trust(
    service: AnalyticsService,
    context: FilterContext,
    definitions: Definitions,
    settings: Settings,
) -> None:
    page_header(
        "Data Trust",
        "See what the control tower measures, which source boundaries remain unresolved, and how "
        "fresh the governed analytical snapshot is.",
        period_label=f"Data through {context.data_max_date:%d %b %Y}",
        chips=context.dimension_chips,
        eyebrow="GOVERNANCE & LINEAGE",
    )
    modified = (
        datetime.fromtimestamp(settings.analytics_db.stat().st_mtime).astimezone()
        if settings.analytics_db.exists()
        else None
    )
    options = service.filter_options()
    summary = service.executive_summary(context.filters, context.basis)
    strict = summary.get("strict_otif")
    render_metric_cards(
        [
            MetricCard(
                "Operational date range",
                f"{context.data_min_date:%d %b %y} – {context.data_max_date:%d %b %y}",
                "Requested-delivery service range",
                tone="info",
            ),
            MetricCard(
                "Analytical snapshot",
                modified.strftime("%d %b · %H:%M") if modified else "Unavailable",
                "Last local DuckDB modification time",
                tone="positive" if modified else "danger",
            ),
            MetricCard(
                "Eligible orders in scope",
                format_compact_number(strict.denominator if strict else None),
                "Delivered/partial orders for eligible outlets",
                tone="info",
            ),
            MetricCard(
                "Governed metrics",
                f"{len(definitions):,}",
                "Definitions loaded from the versioned registry",
                tone="positive",
            ),
        ],
        columns=4,
    )

    section_header("Known critical boundaries")
    render_callout(
        "Strict OTIF degeneracy",
        "All supplied order lines are short-delivered. Strict in-full and OTIF therefore "
        "remain 0%; "
        "the system reports this conflict instead of manufacturing a tolerance.",
        tone="danger",
    )
    render_callout(
        "Customer region differs from DC region",
        "Customer/sales geography and origin-warehouse geography are retained as separate fields. "
        "An unqualified region filter means customer region.",
        tone="warning",
    )
    render_callout(
        "External attribution is limited",
        "Freight has no delivery key and competitor listings have no Kestrel SKU key. Aggregation "
        "and high-confidence matching boundaries remain visible wherever those sources appear.",
        tone="warning",
    )

    section_header("Metric registry")
    registry = pd.DataFrame(
        [
            {
                "Metric": definition.title,
                "Status": definition.status,
                "Formula": definition.formula,
                "Grain": definition.grain,
                "Date basis": definition.date_basis,
                "Warning": definition.warning,
            }
            for definition in definitions.values()
        ]
    )
    dataframe_or_empty(
        registry,
        empty_title="Metric registry unavailable",
        empty_body="No metric definitions were loaded from the project configuration.",
    )

    section_header("Dimension coverage")
    coverage = pd.DataFrame(
        {
            "Dimension": [
                "Customer regions",
                "DC regions",
                "Warehouses",
                "Routes",
                "Outlets",
                "Channels",
            ],
            "Distinct values": [
                len(options["customer_regions"]),
                len(options["warehouse_regions"]),
                len(options["warehouse_codes"]),
                len(options["route_codes"]),
                len(options["outlet_codes"]),
                len(options["channels"]),
            ],
        }
    )
    st.dataframe(coverage, hide_index=True, width="stretch")

    section_header("External source readiness")
    external = external_analytics(settings)
    if not external.available:
        render_empty_state("External status unavailable", external.message)
    else:
        availability_result = call_external(external.payload, "availability")
        sync_result = call_external(external.payload, "sync_status")
        availability = (
            availability_result.payload
            if availability_result.available and isinstance(availability_result.payload, Mapping)
            else {}
        )
        sync_status = (
            sync_result.payload
            if sync_result.available and isinstance(sync_result.payload, pd.DataFrame)
            else pd.DataFrame()
        )
        readiness = pd.DataFrame(
            [
                {
                    "Source": "Freight partner API",
                    "Governed snapshot": "Available" if availability.get("freight") else "Missing",
                    "Rule": "Never substitute driver-entered fuel",
                },
                {
                    "Source": "BazaarPulse",
                    "Governed snapshot": "Available"
                    if availability.get("competitor")
                    else "Missing",
                    "Rule": "Latest observed; high-confidence matches only",
                },
            ]
        )
        st.dataframe(readiness, hide_index=True, width="stretch")
        if sync_status.empty:
            st.caption("No governed external sync history has been recorded.")
        else:
            dataframe_or_empty(
                sync_status,
                empty_title="No external sync history",
                empty_body="No completed external refresh has been recorded.",
                max_rows=10,
                column_config={
                    "completed_at_utc": st.column_config.DatetimeColumn(
                        "Completed (UTC)", format="DD MMM YYYY, HH:mm"
                    ),
                    "is_complete": st.column_config.CheckboxColumn("Complete"),
                },
            )
    render_source_note(
        "Core metrics come only from AnalyticsService; freight and market values come only from "
        "ExternalAnalyticsService. The UI does not query raw tables or reinterpret caches."
    )
