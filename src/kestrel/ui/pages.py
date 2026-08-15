"""Business pages for the Kestrel Streamlit control tower."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from kestrel.config import Settings
from kestrel.metrics.context import ContextAnalyticsService, ContextAssociationResult
from kestrel.metrics.definitions import MetricDefinition
from kestrel.metrics.periods import Period, previous_period
from kestrel.metrics.service import AnalyticsService, MetricValue, QuantityBasis
from kestrel.observability import read_recent_events
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
    Tone,
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
    create_nlq_memory,
    external_analytics,
    install_nlq_model,
    nlq_model_status,
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


@dataclass(frozen=True, slots=True)
class _ExecutiveExternalSignals:
    paid_freight_per_case_inr: float | None
    freight_detail: str
    freight_lens: str
    competitor_coverage: float | None
    competitor_matched: int
    competitor_listings: int
    competitor_freshness: str
    freight_ignored_filters: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def _source_sync_detail(sync_status: pd.DataFrame, source_name: str) -> str:
    if sync_status.empty or "source_name" not in sync_status:
        return "No governed sync metadata"
    rows = sync_status.loc[sync_status["source_name"] == source_name]
    if rows.empty:
        return "No governed sync metadata"
    row = rows.iloc[0]
    coverage_end = row.get("coverage_end")
    completed = pd.to_datetime(row.get("completed_at_utc"), errors="coerce")
    parts = []
    if pd.notna(coverage_end):
        parts.append(f"coverage through {coverage_end}")
    if pd.notna(completed):
        parts.append(f"cached {completed.strftime('%d %b %Y')}")
    if not bool(row.get("is_complete")):
        parts.append("latest cache incomplete")
    return " · ".join(parts) or "Sync metadata present"


def _summarise_external_frames(
    freight: pd.DataFrame,
    match_quality: pd.DataFrame,
    sync_status: pd.DataFrame,
    *,
    freight_lens: str = "warehouse",
    warnings: tuple[str, ...] = (),
) -> _ExecutiveExternalSignals:
    paid_freight = _sum_column(freight, "paid_cost_inr")
    delivered_cases = _sum_column(freight, "delivered_case_equivalents")
    paid_freight_per_case = (
        paid_freight / delivered_cases
        if paid_freight is not None
        and delivered_cases is not None
        and delivered_cases > 0
        else None
    )
    freight_detail = _source_sync_detail(sync_status, "freight_api")

    listings = (
        int(pd.to_numeric(match_quality["listings"], errors="coerce").fillna(0).sum())
        if "listings" in match_quality
        else 0
    )
    matched = 0
    if {"match_status", "listings"}.issubset(match_quality.columns):
        matched = int(
            pd.to_numeric(
                match_quality.loc[
                    match_quality["match_status"].astype(str).str.casefold() == "matched",
                    "listings",
                ],
                errors="coerce",
            )
            .fillna(0)
            .sum()
        )
    coverage = matched / listings if listings else None
    return _ExecutiveExternalSignals(
        paid_freight_per_case_inr=paid_freight_per_case,
        freight_detail=freight_detail,
        freight_lens=freight_lens,
        competitor_coverage=coverage,
        competitor_matched=matched,
        competitor_listings=listings,
        competitor_freshness=_source_sync_detail(sync_status, "bazaarpulse"),
        freight_ignored_filters=tuple(freight.attrs.get("ignored_filters", ())),
        warnings=warnings,
    )


def _load_executive_external_signals(
    settings: Settings, context: FilterContext
) -> _ExecutiveExternalSignals:
    capability = external_analytics(settings)
    if not capability.available:
        return _summarise_external_frames(
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
            warnings=(capability.message,),
        )
    freight_lens = "route" if context.filters.route_codes else "warehouse"
    freight_method = (
        "freight_by_route" if freight_lens == "route" else "freight_by_warehouse"
    )
    freight_result = call_external(capability.payload, freight_method, context.filters)
    quality_result = call_external(capability.payload, "competitor_match_quality")
    sync_result = call_external(capability.payload, "sync_status")
    warnings = tuple(
        result.message
        for result in (freight_result, quality_result, sync_result)
        if not result.available
    )
    freight = (
        freight_result.payload
        if freight_result.available and isinstance(freight_result.payload, pd.DataFrame)
        else pd.DataFrame()
    )
    quality = (
        quality_result.payload
        if quality_result.available and isinstance(quality_result.payload, pd.DataFrame)
        else pd.DataFrame()
    )
    sync_status = (
        sync_result.payload
        if sync_result.available and isinstance(sync_result.payload, pd.DataFrame)
        else pd.DataFrame()
    )
    if freight.empty and freight.attrs.get("unavailable_reason"):
        warnings = (*warnings, str(freight.attrs["unavailable_reason"]))
    return _summarise_external_frames(
        freight,
        quality,
        sync_status,
        freight_lens=freight_lens,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _build_executive_inbox(
    service_summary: Mapping[str, MetricValue],
    delivery_summary: Mapping[str, MetricValue],
    external: _ExecutiveExternalSignals,
    failure_pareto: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []

    def add(
        signal: str,
        metric: MetricValue | None,
        review_rate: float | None,
        lens: str,
    ) -> None:
        if metric is None or review_rate is None:
            return
        rows.append(
            {
                "Signal": signal,
                "Review rate": 100 * review_rate,
                "Evidence": _metric_detail(metric, "eligible records"),
                "Lens": lens,
            }
        )

    strict = service_summary.get("strict_otif")
    add(
        "Strict OTIF gap",
        strict,
        1 - strict.value if strict and strict.value is not None else None,
        "Structural data finding",
    )
    delay_conflict = delivery_summary.get("delay_source_conflict_rate")
    add(
        "Delay-source disagreement",
        delay_conflict,
        delay_conflict.value if delay_conflict else None,
        "Data trust",
    )
    late = delivery_summary.get("delivery_late_over_2h_rate")
    add(
        "More than two hours late",
        late,
        late.value if late else None,
        "Delivery exception",
    )
    failure = delivery_summary.get("recorded_failure_rate")
    failure_lens = "Recorded delivery label"
    if not failure_pareto.empty and "failure_reason_code" in failure_pareto:
        failure_lens += f" · leading {failure_pareto.iloc[0]['failure_reason_code']}"
    add(
        "Recorded failure labels",
        failure,
        failure.value if failure else None,
        failure_lens,
    )
    pod = delivery_summary.get("pod_coverage_rate")
    add(
        "POD not captured",
        pod,
        1 - pod.value if pod and pod.value is not None else None,
        "Evidence completeness",
    )
    if external.competitor_coverage is not None:
        rows.append(
            {
                "Signal": "Competitor listings unmatched",
                "Review rate": 100 * (1 - external.competitor_coverage),
                "Evidence": (
                    f"{external.competitor_listings - external.competitor_matched:,} of "
                    f"{external.competitor_listings:,} current listings"
                ),
                "Lens": "Entity-resolution coverage",
            }
        )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame = frame.sort_values(
        ["Review rate", "Signal"], ascending=[False, True]
    ).reset_index(drop=True)
    frame.insert(0, "Rank", range(1, len(frame) + 1))
    return frame


def _metric_detail(metric: MetricValue | None, noun: str) -> str:
    if metric is None or metric.denominator is None:
        return "No denominator is available for this scope."
    numerator = format_compact_number(metric.numerator)
    denominator = format_compact_number(metric.denominator)
    return f"{numerator} of {denominator} {noun}"


def _previous_summary(
    service: AnalyticsService,
    context: FilterContext,
    basis: QuantityBasis | None = None,
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
    return service.executive_summary(
        with_dates(context.filters, bounded), basis or context.basis
    )


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


def _render_fulfilment_gate_trend(frame: pd.DataFrame, *, title: str) -> None:
    required = {
        "month",
        "allocation_rate",
        "post_allocation_fulfilment",
        "fill_rate",
    }
    if frame.empty or not required.issubset(frame.columns):
        render_empty_state(
            "No fulfilment-gate trend",
            "No eligible order cohort is available for allocation-to-delivery comparison.",
        )
        return
    display = frame.copy()
    labels = {
        "allocation_rate": "Allocated / ordered",
        "post_allocation_fulfilment": "Delivered / allocated",
        "fill_rate": "Delivered / ordered",
    }
    for column in labels:
        display[column] = pd.to_numeric(display[column], errors="coerce") * 100
    long = display.melt(
        id_vars=["month", "orders"],
        value_vars=list(labels),
        var_name="gate",
        value_name="rate",
    )
    long["gate"] = long["gate"].map(labels)
    figure = px.line(
        long,
        x="month",
        y="rate",
        color="gate",
        markers=True,
        hover_data={"orders": ":,", "gate": False},
        color_discrete_sequence=[NAVY, TEAL, GOLD],
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
    *,
    min_orders: int,
) -> None:
    frame = _safe_dimension(
        service.service_by_dimension(
            context.filters,
            dimension,
            context.basis,
            limit=5,
            worst_first=True,
            min_orders=min_orders,
        )
    )
    st.markdown(f"**{title}**")
    st.caption(f"Minimum {min_orders:,} eligible orders; ties end on dimension code/name.")
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


def _render_service_ranking(
    service: AnalyticsService,
    context: FilterContext,
    *,
    ranking: str,
    title: str,
    min_orders: int = 100,
) -> None:
    frame = service.service_rankings(
        context.filters,
        "warehouse",
        context.basis,
        ranking=ranking,
        limit=5,
        min_orders=min_orders,
    )
    st.markdown(f"**{title}**")
    st.caption(f"Only warehouses with at least {min_orders:,} orders in each required period.")
    if frame.empty:
        st.caption("No warehouse meets the governed comparison-volume threshold.")
        return
    display = frame.copy()
    display.attrs = {}
    display["current_fill_rate"] *= 100
    columns = ["dimension_value", "current_orders", "current_fill_rate"]
    column_config: dict[str, object] = {
        "dimension_value": "Warehouse",
        "current_orders": st.column_config.NumberColumn("Current orders", format="%d"),
        "current_fill_rate": st.column_config.NumberColumn(
            "Current fill rate", format="%.1f%%"
        ),
    }
    if ranking == "most_improved":
        display["previous_fill_rate"] *= 100
        columns.extend(
            ["previous_orders", "previous_fill_rate", "fill_rate_change_pp"]
        )
        column_config.update(
            {
                "previous_orders": st.column_config.NumberColumn(
                    "Prior orders", format="%d"
                ),
                "previous_fill_rate": st.column_config.NumberColumn(
                    "Prior fill rate", format="%.1f%%"
                ),
                "fill_rate_change_pp": st.column_config.NumberColumn(
                    "Change", format="%+.1f pp"
                ),
            }
        )
    st.dataframe(
        display[columns],
        hide_index=True,
        width="stretch",
        column_config=column_config,
    )


def render_executive(
    service: AnalyticsService,
    context: FilterContext,
    definitions: Definitions,
    settings: Settings,
) -> None:
    page_header(
        "Executive Command Center",
        "One governed view of where customer service is being lost, where measured value "
        "is leaking, and which operating areas warrant attention first.",
        period_label=context.period_label,
        chips=context.dimension_chips,
    )
    eaches_summary = service.executive_summary(
        context.filters, QuantityBasis.EACHES
    )
    cases_summary = service.executive_summary(
        context.filters, QuantityBasis.CASE_EQUIVALENTS
    )
    summary = (
        eaches_summary
        if context.basis == QuantityBasis.EACHES
        else cases_summary
    )
    previous_eaches = _previous_summary(service, context, QuantityBasis.EACHES)
    previous_cases = _previous_summary(
        service, context, QuantityBasis.CASE_EQUIVALENTS
    )
    delivery_summary = service.delivery_exception_summary(context.filters)
    failure_pareto = service.failure_reason_pareto(context.filters, limit=5)
    external_signals = _load_executive_external_signals(settings, context)
    credit = eaches_summary.get("approved_credit_note_rate")
    render_metric_cards(
        [
            MetricCard(
                "Eaches fill rate",
                format_metric_value(eaches_summary.get("fill_rate")),
                _metric_detail(
                    eaches_summary.get("fill_rate"), "ordered eaches delivered"
                ),
                comparison_delta(
                    eaches_summary.get("fill_rate"),
                    previous_eaches.get("fill_rate") if previous_eaches else None,
                ),
                "positive",
            ),
            MetricCard(
                "Case-equivalent fill rate",
                format_metric_value(cases_summary.get("fill_rate")),
                _metric_detail(
                    cases_summary.get("fill_rate"),
                    "ordered case-equivalents delivered",
                ),
                comparison_delta(
                    cases_summary.get("fill_rate"),
                    previous_cases.get("fill_rate") if previous_cases else None,
                ),
                "positive",
            ),
            MetricCard(
                "Allocation rate · eaches",
                format_metric_value(eaches_summary.get("allocation_rate")),
                _metric_detail(
                    eaches_summary.get("allocation_rate"), "ordered eaches allocated"
                ),
                comparison_delta(
                    eaches_summary.get("allocation_rate"),
                    previous_eaches.get("allocation_rate")
                    if previous_eaches
                    else None,
                ),
                "info",
            ),
            MetricCard(
                "Strict OTIF",
                format_metric_value(eaches_summary.get("strict_otif")),
                _metric_detail(eaches_summary.get("strict_otif"), "eligible orders"),
                comparison_delta(
                    eaches_summary.get("strict_otif"),
                    previous_eaches.get("strict_otif") if previous_eaches else None,
                ),
                "danger",
            ),
            MetricCard(
                "Timestamp-derived on time",
                format_metric_value(eaches_summary.get("on_time_rate")),
                _metric_detail(eaches_summary.get("on_time_rate"), "eligible orders"),
                comparison_delta(
                    eaches_summary.get("on_time_rate"),
                    previous_eaches.get("on_time_rate") if previous_eaches else None,
                ),
                "info",
            ),
            MetricCard(
                "Overdue open backlog",
                format_metric_value(eaches_summary.get("overdue_backlog_orders")),
                f"Current OPEN state through {context.filters.end_date:%d %b %Y}",
                tone="warning",
            ),
            MetricCard(
                "Excursions per 100 chilled",
                format_metric_value(
                    eaches_summary.get("temperature_excursions_per_100")
                ),
                _metric_detail(
                    eaches_summary.get("temperature_excursions_per_100"),
                    "chilled deliveries",
                ),
                tone="warning",
            ),
            MetricCard(
                "Near-expiry available stock",
                format_metric_value(eaches_summary.get("near_expiry_cases")),
                "Latest weekly snapshot; 0–30 days remaining",
                tone="warning",
            ),
            MetricCard(
                "Post-allocation fulfilment",
                format_metric_value(
                    eaches_summary.get("post_allocation_fulfilment")
                ),
                _metric_detail(
                    eaches_summary.get("post_allocation_fulfilment"),
                    "allocated eaches delivered",
                ),
                tone="positive",
            ),
            MetricCard(
                "Approved credit notes",
                format_inr(credit.numerator if credit else None),
                (
                    f"{format_metric_value(credit)} of estimated dispatch value"
                    if credit
                    else "No governed credit-note denominator"
                ),
                tone="danger",
            ),
            MetricCard(
                "Settled/paid freight / delivered case",
                (
                    f"₹{external_signals.paid_freight_per_case_inr:,.2f}"
                    if external_signals.paid_freight_per_case_inr is not None
                    else "Not available"
                ),
                f"PAID invoice amount ÷ delivered case-equivalents · period × "
                f"{external_signals.freight_lens} lens · "
                + external_signals.freight_detail,
                tone="warning",
            ),
            MetricCard(
                "Source-wide competitor match coverage",
                _percent(external_signals.competitor_coverage),
                (
                    f"{external_signals.competitor_matched:,} of "
                    f"{external_signals.competitor_listings:,} listings · "
                    "all cities/retailers · "
                    f"{external_signals.competitor_freshness}"
                ),
                tone=(
                    "positive"
                    if external_signals.competitor_coverage is not None
                    else "warning"
                ),
            ),
        ],
        columns=4,
    )
    if external_signals.warnings:
        render_callout(
            "Optional external snapshot boundary",
            "Core operational cards remain available. "
            + " ".join(external_signals.warnings),
            tone="warning",
        )
    if external_signals.freight_ignored_filters:
        render_callout(
            "Executive freight filter boundary",
            "Freight invoices have no customer, outlet, channel, promotion, or order-source "
            "keys. The settled freight card therefore cannot apply the active filter(s): "
            + ", ".join(external_signals.freight_ignored_filters)
            + ". Its period and available DC/route scope remain governed.",
            tone="warning",
        )

    ignored_inventory_filters = tuple(
        label
        for label, values in (
            ("customer region", context.filters.customer_regions),
            ("route", context.filters.route_codes),
            ("outlet", context.filters.outlet_codes),
            ("channel", context.filters.channels),
            ("promotion", context.filters.promotion_codes),
            ("order source", context.filters.order_sources),
        )
        if values
    )
    if ignored_inventory_filters:
        render_callout(
            "Executive inventory filter boundary",
            "The near-expiry card uses the latest weekly inventory snapshot, which has DC and "
            "product keys but no historical customer/order key. These active filters therefore "
            "do not change that one card: "
            + ", ".join(ignored_inventory_filters)
            + ". Operational service and leakage cards retain their documented filters; "
            "the external freight boundary is disclosed separately when applicable.",
            tone="warning",
        )

    strict_otif = summary.get("strict_otif")
    eligible_orders = strict_otif.denominator if strict_otif else None
    if eligible_orders is not None and eligible_orders > 0:
        render_callout(
            "Strict OTIF is correctly reported as 0%",
            "Every supplied order line is short-delivered, so no eligible order can be in full. "
            f"The current scope contains {format_compact_number(eligible_orders)} eligible "
            "orders. Fill rate and on-time performance remain informative; no tolerance has "
            "been invented.",
            tone="danger",
        )
    else:
        render_callout(
            "No eligible orders in this scope",
            "Strict OTIF and other order-service ratios remain not available because the active "
            "filters produce a zero denominator. Broaden the scope to restore evidence.",
            tone="warning",
        )

    section_header(
        "Ranked exception and trust inbox",
        "Rows are ordered by their descriptive exception/gap rate for triage. Rates are not "
        "additive, causal, or a common financial severity score.",
    )
    inbox = _build_executive_inbox(
        eaches_summary, delivery_summary, external_signals, failure_pareto
    )
    dataframe_or_empty(
        inbox,
        empty_title="No ranked signals",
        empty_body="No governed exception denominator is available for this scope.",
        max_rows=8,
        column_config={
            "Review rate": st.column_config.NumberColumn(format="%.1f%%"),
        },
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
        "Lowest fill-rate groups use explicit volume floors, show denominators, and apply a "
        "deterministic final dimension tie-break.",
    )
    first_row = st.columns(2)
    with first_row[0]:
        _render_worst_table(
            service, context, "customer_region", "Customer regions", min_orders=100
        )
    with first_row[1]:
        _render_worst_table(service, context, "warehouse", "Warehouses", min_orders=100)
    second_row = st.columns(2)
    with second_row[0]:
        _render_worst_table(service, context, "route", "Routes", min_orders=25)
    with second_row[1]:
        _render_worst_table(service, context, "outlet", "Outlets", min_orders=5)

    section_header(
        "Best and most improved",
        "Best uses current weighted fill rate. Most improved compares with the immediately "
        "preceding equal-length period; both enforce a minimum volume before ranking.",
    )
    ranking_columns = st.columns(2)
    with ranking_columns[0]:
        _render_service_ranking(
            service, context, ranking="best", title="Best current warehouses"
        )
    with ranking_columns[1]:
        _render_service_ranking(
            service,
            context,
            ranking="most_improved",
            title="Most-improved warehouses",
        )

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
            "allocation_rate",
            "post_allocation_fulfilment",
            "overdue_backlog_orders",
            "strict_otif",
            "on_time_rate",
            "temperature_excursions_per_100",
            "near_expiry_cases",
            "approved_credit_note_rate",
            "short_delivery_value_exposure_inr",
            "settled_freight_cost_per_case",
            "competitor_match_coverage",
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

    section_header(
        "Ordered → allocated → delivered → returned",
        "The first three stages retain one requested-delivery line cohort. Returns are linked "
        "physical-return signals observed through the selected end date.",
    )
    flow = service.fulfilment_flow(context.filters, context.basis)
    if flow.empty:
        render_empty_state(
            "No fulfilment flow",
            "No eligible order lines match the active requested-delivery scope.",
        )
    else:
        ordered = _sum_column(flow, "ordered_quantity")
        allocated = _sum_column(flow, "allocated_quantity")
        delivered = _sum_column(flow, "delivered_quantity")
        returned = _sum_column(flow, "returned_quantity")
        allocation_short = _sum_column(flow, "allocation_shortfall_quantity")
        post_allocation_short = _sum_column(
            flow, "post_allocation_shortfall_quantity"
        )
        eligible_lines = _sum_column(flow, "eligible_order_lines")
        render_metric_cards(
            [
                MetricCard(
                    "Ordered",
                    format_compact_number(ordered),
                    f"{format_compact_number(eligible_lines)} eligible order lines",
                    tone="info",
                ),
                MetricCard(
                    "Allocated",
                    format_compact_number(allocated),
                    "Same requested-delivery line cohort",
                    tone="info",
                ),
                MetricCard(
                    "Delivered",
                    format_compact_number(delivered),
                    "Same requested-delivery line cohort",
                    tone="positive",
                ),
                MetricCard(
                    "Linked returned",
                    format_compact_number(returned),
                    "All credit statuses observed through period end",
                    tone="warning",
                ),
                MetricCard(
                    "Allocation shortfall",
                    format_compact_number(allocation_short),
                    "Positive ordered less allocated quantity",
                    tone="danger",
                ),
                MetricCard(
                    "Post-allocation shortfall",
                    format_compact_number(post_allocation_short),
                    "Positive allocated less delivered quantity",
                    tone="danger",
                ),
            ],
            columns=3,
        )
        stages = pd.DataFrame(
            {
                "Stage": ["Ordered", "Allocated", "Delivered", "Linked returned"],
                "Quantity": [ordered, allocated, delivered, returned],
                "Signal": ["Fulfilment gate", "Fulfilment gate", "Fulfilment gate", "Return"],
            }
        ).dropna(subset=["Quantity"])
        if not stages.empty:
            figure = px.bar(
                stages,
                x="Stage",
                y="Quantity",
                color="Signal",
                color_discrete_map={"Fulfilment gate": NAVY, "Return": GOLD},
                title=f"Quantity gates · {context.quantity_label}",
                text_auto=".3s",
            )
            figure.update_xaxes(title=None)
            figure.update_yaxes(title=context.quantity_label)
            _plot(_figure_layout(figure, height=330))
        render_callout(
            "Cohort and return boundary",
            f"{flow.attrs.get('date_basis', 'Requested-delivery line cohort')}. "
            f"{flow.attrs.get('warning', 'Returns are not a recovery or cash measure.')}",
            tone="warning",
        )

    section_header("Performance over time")
    trend = service.service_trend(context.filters, context.basis)
    outcome_tab, gate_tab = st.tabs(["Customer outcome", "Fulfilment gates"])
    with outcome_tab:
        _render_service_trend(
            trend,
            title=f"Monthly service · {context.quantity_label}",
        )
    with gate_tab:
        _render_fulfilment_gate_trend(
            trend,
            title=f"Monthly allocation and fulfilment gates · {context.quantity_label}",
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
        "promotion": "Recorded promotion",
        "promotion_mechanic": "Promotion mechanic",
        "order_source": "Order source",
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
        "promotion": "Recorded promotion",
        "promotion_mechanic": "Promotion mechanic",
        "order_source": "Order source",
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
        "Customer and logistics drill paths",
        "Native order-line evidence retains order source, recorded promotion, product, quantity "
        "gates, linked returns, and shortage value exposure; limited to 200 rows.",
    )
    line_evidence = service.service_line_evidence(context.filters, limit=200)
    suffix = (
        "eaches"
        if context.basis == QuantityBasis.EACHES
        else "case_equivalents"
    )
    quantity_columns = [
        f"ordered_{suffix}",
        f"allocated_{suffix}",
        f"delivered_{suffix}",
        f"returned_{suffix}",
        f"short_{suffix}",
    ]
    customer_columns = [
        "order_number",
        "order_line_id",
        "requested_delivery_date",
        "customer_region_name",
        "outlet_code",
        "channel",
        "source_system",
        "promotion_code",
        "promotion_mechanic",
        "sku_code",
        "product_name",
        "category",
        *quantity_columns,
        "short_delivery_value_exposure_inr",
    ]
    logistics_columns = [
        "order_number",
        "order_line_id",
        "requested_delivery_date",
        "warehouse_code",
        "route_code",
        "source_system",
        "promotion_code",
        "sku_code",
        *quantity_columns,
        "short_reason_code",
        "short_delivery_value_exposure_inr",
    ]
    customer_tab, logistics_tab, order_tab = st.tabs(
        ["Customer path", "Logistics path", "Order outcome"]
    )
    evidence_config = {
        "requested_delivery_date": st.column_config.DateColumn(
            "Requested delivery", format="DD MMM YYYY"
        ),
        "short_delivery_value_exposure_inr": st.column_config.NumberColumn(
            "Short-value exposure", format="₹%.2f"
        ),
    }
    with customer_tab:
        dataframe_or_empty(
            line_evidence[
                [column for column in customer_columns if column in line_evidence]
            ],
            empty_title="No customer-path lines",
            empty_body="No eligible order lines match this requested-delivery scope.",
            max_rows=200,
            column_config=evidence_config,
        )
    with logistics_tab:
        dataframe_or_empty(
            line_evidence[
                [column for column in logistics_columns if column in line_evidence]
            ],
            empty_title="No logistics-path lines",
            empty_body="No eligible order lines match this requested-delivery scope.",
            max_rows=200,
            column_config=evidence_config,
        )
    with order_tab:
        evidence = service.service_evidence(context.filters, limit=200).copy()
        if not evidence.empty:
            evidence["fill_rate_eaches"] *= 100
            evidence["fill_rate_case_equivalents"] *= 100
        dataframe_or_empty(
            evidence,
            empty_title="No order evidence",
            empty_body="No eligible completed orders matched the active scope.",
            max_rows=200,
            column_config={
                "fill_rate_eaches": st.column_config.NumberColumn(
                    "Each fill rate", format="%.1f%%"
                ),
                "fill_rate_case_equivalents": st.column_config.NumberColumn(
                    "Case-equivalent fill", format="%.1f%%"
                ),
                "derived_delay_minutes": st.column_config.NumberColumn(
                    "Derived delay", format="%d min"
                ),
            },
        )
    render_source_note(
        str(line_evidence.attrs.get("promotion_definition", "Promotions are descriptive."))
        + " "
        + str(line_evidence.attrs.get("return_date_basis", ""))
    )

    section_header(
        "Orders recorded after product discontinuation",
        "An audit exception at order-line grain; this is not proof that the product was "
        "physically unavailable at the fulfilment location.",
    )
    discontinued = service.discontinued_order_evidence(context.filters, limit=100)
    dataframe_or_empty(
        discontinued,
        empty_title="No discontinued-product order exceptions",
        empty_body="No order lines recorded after their product discontinuation date match scope.",
        max_rows=100,
        column_config={
            "order_date": st.column_config.DateColumn("Order date", format="DD MMM YYYY"),
            "discontinued_date": st.column_config.DateColumn(
                "Discontinued", format="DD MMM YYYY"
            ),
            "line_value_inr": st.column_config.NumberColumn(
                "Booked line value", format="₹%.2f"
            ),
        },
    )
    st.caption(
        f"Showing {len(discontinued):,} most recent/high-value exception rows. This audit uses "
        "order date, while headline service uses requested delivery date."
    )
    render_definitions(
        definitions,
        [
            "fill_rate",
            "allocation_rate",
            "post_allocation_fulfilment",
            "strict_otif",
            "on_time_rate",
            "late_over_2h_rate",
            "short_delivery_value_exposure_inr",
            "orders_after_discontinuation",
        ],
    )


def render_delivery_exceptions(
    service: AnalyticsService,
    context: FilterContext,
    definitions: Definitions,
    settings: Settings,
) -> None:
    del settings
    page_header(
        "Delivery & Exception Drivers",
        "Review timestamp-derived lateness, POD coverage, recorded failure labels, telematics "
        "signals, and source conflicts at delivery grain without turning association into blame.",
        period_label=context.period_label,
        chips=context.dimension_chips,
        eyebrow="DELIVERY EVIDENCE",
    )
    summary = service.delivery_exception_summary(context.filters)
    render_metric_cards(
        [
            MetricCard(
                "Delivery-cohort on time",
                format_metric_value(summary.get("delivery_on_time_rate")),
                _metric_detail(
                    summary.get("delivery_on_time_rate"), "timestamp-eligible deliveries"
                ),
                tone="info",
            ),
            MetricCard(
                "More than two hours late",
                format_metric_value(summary.get("delivery_late_over_2h_rate")),
                _metric_detail(
                    summary.get("delivery_late_over_2h_rate"),
                    "timestamp-eligible deliveries",
                ),
                tone="danger",
            ),
            MetricCard(
                "POD coverage",
                format_metric_value(summary.get("pod_coverage_rate")),
                _metric_detail(summary.get("pod_coverage_rate"), "eligible deliveries"),
                tone="positive",
            ),
            MetricCard(
                "Delay-source conflicts",
                format_metric_value(summary.get("delay_source_conflict_rate")),
                _metric_detail(
                    summary.get("delay_source_conflict_rate"), "eligible deliveries"
                ),
                tone="warning",
            ),
            MetricCard(
                "Recorded failure signals",
                format_metric_value(summary.get("recorded_failure_rate")),
                _metric_detail(summary.get("recorded_failure_rate"), "eligible deliveries"),
                tone="danger",
            ),
        ],
        columns=3,
    )
    render_callout(
        "Exception signals support investigation, not blame",
        "A route, warehouse, telematics vendor, delay, or failure label occurring together does "
        "not prove root cause or responsibility. Stored and timestamp-derived delays remain "
        "visible as separate evidence fields.",
        tone="warning",
    )

    section_header(
        "Delivery trend",
        "This workspace uses actual delivery date; the executive service cohort uses requested "
        "delivery date. The two views should not be compared without that date-basis distinction.",
    )
    trend = service.delivery_exception_trend(context.filters)
    if trend.empty:
        render_empty_state(
            "No delivery trend",
            "No eligible deliveries match the selected actual-delivery date scope.",
        )
    else:
        trend_display = trend.copy()
        trend_labels = {
            "on_time_rate": "On time",
            "late_over_2h_rate": ">2h late",
            "pod_coverage_rate": "POD captured",
            "conflict_rate": "Delay conflict",
            "recorded_failure_rate": "Recorded failure",
        }
        for column in trend_labels:
            trend_display[column] *= 100
        trend_long = trend_display.melt(
            id_vars=["month", "deliveries"],
            value_vars=list(trend_labels),
            var_name="signal",
            value_name="rate",
        )
        trend_long["signal"] = trend_long["signal"].map(trend_labels)
        figure = px.line(
            trend_long,
            x="month",
            y="rate",
            color="signal",
            markers=True,
            hover_data={"deliveries": ":,", "signal": False},
            color_discrete_sequence=[TEAL, RED, NAVY, GOLD, INK],
            title="Monthly delivery evidence rates",
        )
        figure.update_xaxes(title=None)
        figure.update_yaxes(title="Percent", ticksuffix="%", rangemode="tozero")
        _plot(_figure_layout(figure, height=390))

    section_header(
        "Volume-qualified exception groups",
        "The threshold suppresses unstable small-volume rankings. It changes inclusion only, "
        "not the underlying delivery records or rates.",
    )
    min_deliveries = int(
        st.number_input(
            "Minimum deliveries per group",
            min_value=10,
            max_value=5_000,
            value=100,
            step=10,
            key="kp_exception_min_deliveries",
        )
    )
    route_frame = _safe_dimension(
        service.delivery_exceptions_by_dimension(
            context.filters,
            "route",
            min_deliveries=min_deliveries,
            limit=30,
        )
    )
    vendor_frame = _safe_dimension(
        service.delivery_exceptions_by_dimension(
            context.filters,
            "telematics_vendor",
            min_deliveries=min_deliveries,
            limit=30,
        )
    )
    route_column, vendor_column = st.columns((1.35, 1))
    with route_column:
        st.markdown("**Routes with timestamp-derived >2h exceptions**")
        qualifying_routes = route_frame.loc[
            route_frame["late_over_2h_rate"] > 0
        ].copy() if not route_frame.empty else route_frame
        if qualifying_routes.empty:
            st.caption("No route above the volume threshold has a >2h exception.")
        else:
            qualifying_routes["late_over_2h_pct"] = (
                qualifying_routes["late_over_2h_rate"] * 100
            )
            figure = px.bar(
                qualifying_routes.sort_values("late_over_2h_pct"),
                x="late_over_2h_pct",
                y="dimension_value",
                orientation="h",
                color="deliveries",
                color_continuous_scale=[GOLD, RED],
                hover_data={"deliveries": ":,", "late_over_2h_pct": ":.1f"},
                title=f"Routes · minimum {min_deliveries:,} deliveries",
            )
            figure.update_xaxes(title=">2h-late deliveries", ticksuffix="%")
            figure.update_yaxes(title=None)
            figure.update_coloraxes(colorbar_title="Deliveries")
            _plot(_figure_layout(figure, height=430))
    with vendor_column:
        st.markdown("**Telematics-vendor evidence**")
        if vendor_frame.empty:
            st.caption("No vendor meets the selected delivery-volume threshold.")
        else:
            vendor_display = vendor_frame.copy()
            for column in (
                "on_time_rate",
                "late_over_2h_rate",
                "pod_coverage_rate",
                "conflict_rate",
            ):
                vendor_display[column] *= 100
            st.dataframe(
                vendor_display,
                hide_index=True,
                width="stretch",
                column_config={
                    "dimension_value": "Telematics vendor",
                    "on_time_rate": st.column_config.NumberColumn("On time", format="%.1f%%"),
                    "late_over_2h_rate": st.column_config.NumberColumn(
                        ">2h late", format="%.1f%%"
                    ),
                    "pod_coverage_rate": st.column_config.NumberColumn(
                        "POD", format="%.1f%%"
                    ),
                    "conflict_rate": st.column_config.NumberColumn(
                        "Delay conflict", format="%.1f%%"
                    ),
                },
            )

    section_header(
        "Recorded failure-reason Pareto",
        "Share is calculated only among deliveries with a recorded failure label. Labels are "
        "source-system associations, not validated root causes.",
    )
    pareto = service.failure_reason_pareto(context.filters, limit=20)
    if pareto.empty:
        render_empty_state(
            "No recorded failure labels",
            "No eligible delivery in this scope carries a failure reason code.",
        )
    else:
        pareto_display = pareto.copy()
        pareto_display["cumulative_pct"] = pareto_display["cumulative_share"] * 100
        figure = go.Figure()
        figure.add_bar(
            x=pareto_display["failure_reason_code"],
            y=pareto_display["failure_deliveries"],
            name="Recorded failures",
            marker_color=GOLD,
        )
        figure.add_scatter(
            x=pareto_display["failure_reason_code"],
            y=pareto_display["cumulative_pct"],
            name="Cumulative share",
            mode="lines+markers",
            line={"color": RED, "width": 3},
            yaxis="y2",
        )
        figure.update_layout(
            title="Recorded failure-label concentration",
            yaxis={"title": "Deliveries"},
            yaxis2={
                "title": "Cumulative share",
                "overlaying": "y",
                "side": "right",
                "ticksuffix": "%",
                "range": [0, 105],
            },
        )
        figure.update_xaxes(title=None)
        _plot(_figure_layout(figure, height=390))

    section_header(
        "Delivery evidence drilldown",
        "Largest timestamp-derived delays first; limited to 200 delivery records.",
    )
    evidence = service.delivery_exception_evidence(context.filters, limit=200)
    dataframe_or_empty(
        evidence,
        empty_title="No delivery evidence",
        empty_body="No eligible deliveries matched the selected actual-delivery scope.",
        column_config={
            "stored_delay_minutes": st.column_config.NumberColumn(
                "Stored delay", format="%d min"
            ),
            "derived_delay_minutes": st.column_config.NumberColumn(
                "Derived delay", format="%d min"
            ),
            "on_time_by_timestamp": st.column_config.CheckboxColumn("On time"),
            "on_time_by_stored_delay": st.column_config.CheckboxColumn(
                "Stored-delay on time"
            ),
            "late_over_2h": st.column_config.CheckboxColumn(">2h late"),
            "delay_source_conflict": st.column_config.CheckboxColumn("Delay conflict"),
            "pod_captured": st.column_config.CheckboxColumn("POD captured"),
        },
    )
    render_definitions(
        definitions,
        [
            "delivery_on_time_rate",
            "delivery_late_over_2h_rate",
            "pod_coverage_rate",
            "delay_source_conflict_rate",
            "recorded_failure_rate",
        ],
    )
    render_source_note(
        "Source: fct_delivery at delivery grain. Timestamp-derived delay is primary for timing "
        "classification; stored delay remains visible as a conflicting audit signal."
    )


def render_cold_chain(
    service: AnalyticsService,
    context: FilterContext,
    definitions: Definitions,
    settings: Settings,
) -> None:
    del settings
    page_header(
        "Cold Chain & Inventory Risk",
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
    expired_total = (
        float(inventory["expired_available_cases"].sum())
        if not inventory.empty
        else None
    )
    damaged_total = float(inventory["damaged_cases"].sum()) if not inventory.empty else None
    blocked_total = float(inventory["blocked_cases"].sum()) if not inventory.empty else None
    ignored_inventory_filters = tuple(inventory.attrs.get("ignored_filters", ()))
    render_metric_cards(
        [
            MetricCard(
                "Eligible chilled deliveries",
                format_compact_number(excursion.denominator if excursion else None),
                "Distinct eligible delivery records with chilled product",
                tone="info",
            ),
            MetricCard(
                "Source-flagged excursions",
                format_compact_number(excursion.numerator if excursion else None),
                "Distinct chilled deliveries carrying the source flag",
                tone="danger",
            ),
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
                "Expired available cases",
                format_compact_number(expired_total),
                "Positive available stock with negative snapshot-relative expiry days",
                tone="danger",
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
    if ignored_inventory_filters:
        render_callout(
            "Inventory filter boundary",
            "Weekly inventory has DC and product keys, but no historical customer/order key. "
            "These active filters therefore do not change inventory cards or charts: "
            + ", ".join(map(str, ignored_inventory_filters))
            + ".",
            tone="warning",
        )

    section_header(
        "Cold-chain trend and descriptive severity",
        "Actual-delivery month; peak-temperature bands describe source-flagged records and "
        "are not validated food-safety thresholds.",
    )
    cold_trend = service.cold_chain_trend(context.filters)
    if cold_trend.empty:
        render_empty_state(
            "No cold-chain trend",
            "No eligible chilled deliveries match this actual-delivery scope.",
        )
    else:
        trend_columns = st.columns(2)
        with trend_columns[0]:
            figure = px.line(
                cold_trend,
                x="dimension_value",
                y="excursions_per_100",
                markers=True,
                color_discrete_sequence=[RED],
                title="Monthly excursions per 100 chilled deliveries",
                hover_data={
                    "chilled_deliveries": ":,",
                    "excursions": ":,",
                    "peak_excursion_max_temp_c": ":.1f",
                },
            )
            figure.update_xaxes(title=None)
            figure.update_yaxes(title="Excursions per 100", rangemode="tozero")
            _plot(_figure_layout(figure, height=360))
        with trend_columns[1]:
            severity_labels = {
                "flagged_peak_le_8c": "Flagged peak ≤8°C",
                "flagged_peak_8_to_12c": "Flagged peak >8–12°C",
                "flagged_peak_over_12c": "Flagged peak >12°C",
                "flagged_peak_unavailable": "Flagged peak unavailable",
            }
            severity = cold_trend.melt(
                id_vars=["dimension_value"],
                value_vars=list(severity_labels),
                var_name="band",
                value_name="deliveries",
            )
            severity["band"] = severity["band"].map(severity_labels)
            figure = px.bar(
                severity,
                x="dimension_value",
                y="deliveries",
                color="band",
                barmode="stack",
                color_discrete_sequence=[TEAL, GOLD, RED, NAVY],
                title="Source-flagged deliveries by recorded peak band",
            )
            figure.update_xaxes(title=None)
            figure.update_yaxes(title="Flagged deliveries")
            _plot(_figure_layout(figure, height=360))
        render_source_note(str(cold_trend.attrs.get("severity_definition", "")))

    section_header("Temperature-control hotspots")
    cold_dimensions = {
        "warehouse": "Warehouse",
        "route": "Route",
        "customer_region": "Customer region",
        "warehouse_region": "DC region",
        "outlet": "Outlet",
        "channel": "Channel",
        "category": "Chilled category",
        "promotion": "Recorded promotion",
        "order_source": "Order source",
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
            hover_data={
                "chilled_deliveries": ":,",
                "excursions": ":,",
                "peak_excursion_max_temp_c": ":.1f",
                "descriptive_peak_band": True,
            },
        )
        figure.update_xaxes(title="Excursions per 100")
        figure.update_yaxes(title=None)
        figure.update_coloraxes(colorbar_title="Excursions")
        _plot(_figure_layout(figure, height=430))
        warning = hotspots.attrs.get("warning")
        if warning:
            render_callout("Non-additive category boundary", str(warning), tone="warning")
        render_source_note(str(hotspots.attrs.get("severity_definition", "")))

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
        inventory_columns = [
            "dimension_value",
            "available_cases",
            "near_expiry_cases",
            "expired_available_cases",
            "damaged_cases",
            "blocked_cases",
        ]
        dataframe_or_empty(
            inventory_view[
                [column for column in inventory_columns if column in inventory_view]
            ],
            empty_title="No inventory evidence",
            empty_body="No inventory aggregates match this snapshot scope.",
            max_rows=100,
        )

    section_header(
        "Batch-level inventory evidence",
        "Latest snapshot on or before period end; risk-only rows are ordered with expired and "
        "near-expiry flags first.",
    )
    batch_evidence = service.inventory_batch_evidence(
        context.filters, risk_only=True, limit=200
    )
    batch_columns = [
        "snapshot_date",
        "warehouse_region_name",
        "warehouse_code",
        "sku_code",
        "product_name",
        "category",
        "is_chilled",
        "storage_temp_band",
        "batch_id",
        "available_cases",
        "expiry_date",
        "expiry_days",
        "damaged_cases",
        "blocked_cases",
        "storage_temp_celsius",
        "near_expiry_flag",
        "expired_stock_flag",
        "damaged_stock_flag",
        "blocked_stock_flag",
    ]
    dataframe_or_empty(
        batch_evidence[
            [column for column in batch_columns if column in batch_evidence]
        ],
        empty_title="No at-risk inventory batches",
        empty_body=(
            "No expired, near-expiry, damaged, or blocked batch rows match the latest scope."
        ),
        max_rows=200,
        column_config={
            "snapshot_date": st.column_config.DateColumn("Snapshot", format="DD MMM YYYY"),
            "expiry_date": st.column_config.DateColumn("Expiry", format="DD MMM YYYY"),
            "near_expiry_flag": st.column_config.CheckboxColumn("Near expiry"),
            "expired_stock_flag": st.column_config.CheckboxColumn("Expired"),
            "damaged_stock_flag": st.column_config.CheckboxColumn("Damaged"),
            "blocked_stock_flag": st.column_config.CheckboxColumn("Blocked"),
        },
    )
    render_source_note(
        str(batch_evidence.attrs.get("grain", "Latest inventory batch snapshot evidence"))
    )

    cold_returns = service.cold_chain_return_evidence(context.filters, limit=100)
    section_header(
        "Cold-chain return evidence",
        "Exact RT06 source lines retain order-line linkage, workflow status, raw sign, and "
        "case-pack-normalized physical quantity.",
    )
    if cold_returns.empty:
        render_empty_state(
            "No cold-chain credit notes",
            "No RT06 credit-note lines match this reporting scope and status contract.",
        )
    else:
        approved = cold_returns.loc[cold_returns["credit_note_status"] == "APPROVED"]
        value = float(approved["credit_note_value_inr"].sum())
        lines = len(approved)
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
        evidence_columns = [
            "credit_note_number",
            "return_date",
            "order_line_id",
            "outlet_code",
            "warehouse_code",
            "route_code",
            "sku_code",
            "qty_uom",
            "return_qty_raw",
            "return_qty_normalized",
            "return_sign_was_negative",
            "return_eaches",
            "return_case_equivalents",
            "return_reason_code",
            "credit_note_status",
            "credit_note_value_inr",
            "disposition",
        ]
        dataframe_or_empty(
            cold_returns[[column for column in evidence_columns if column in cold_returns]],
            empty_title="No RT06 evidence rows",
            empty_body="No exact credit-note line survived the governed evidence selection.",
            max_rows=100,
            column_config={
                "return_date": st.column_config.DateColumn("Return date", format="DD MMM YYYY"),
                "credit_note_value_inr": st.column_config.NumberColumn(
                    "Credit-note value", format="₹%.2f"
                ),
                "return_case_equivalents": st.column_config.NumberColumn(
                    "Returned cases", format="%.2f"
                ),
            },
        )
        render_source_note(
            str(cold_returns.attrs.get("grain", "One exact source credit-note line"))
            + ". "
            + str(cold_returns.attrs.get("quantity_rule", "Raw and normalized quantities shown."))
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


def _sampled_route_ratios(
    frame: pd.DataFrame, *, minimum_records: int = 25
) -> pd.DataFrame:
    required = {
        "route_code",
        "invoice_count",
        "delivered_orders",
        "freight_cost_per_delivered_case_inr",
    }
    if frame.empty or not required.issubset(frame.columns):
        return pd.DataFrame(columns=list(required))
    sampled = frame.dropna(subset=["freight_cost_per_delivered_case_inr"]).copy()
    invoice_volume = pd.to_numeric(sampled["invoice_count"], errors="coerce").fillna(0)
    delivery_volume = pd.to_numeric(
        sampled["delivered_orders"], errors="coerce"
    ).fillna(0)
    return sampled.loc[
        (invoice_volume >= minimum_records) & (delivery_volume >= minimum_records)
    ]


def _render_freight_frames(
    warehouse: pd.DataFrame,
    route: pd.DataFrame,
    carrier: pd.DataFrame,
    sync_status: pd.DataFrame | None = None,
    previous_route: pd.DataFrame | None = None,
    *,
    primary_lens: str = "warehouse",
) -> None:
    if warehouse.empty and route.empty and carrier.empty:
        reason = (
            warehouse.attrs.get("unavailable_reason")
            or route.attrs.get("unavailable_reason")
            or carrier.attrs.get("unavailable_reason")
        )
        render_empty_state(
            "No governed freight rows",
            str(reason or "No partner invoices match the active period and DC scope."),
        )
        return

    if primary_lens not in {"warehouse", "route"}:
        raise ValueError("primary_lens must be warehouse or route")
    primary = route if primary_lens == "route" else warehouse
    billed = _sum_column(primary, "freight_cost_inr")
    detention = _sum_column(primary, "detention_cost_inr")
    paid = _sum_column(primary, "paid_cost_inr")
    pending = _sum_column(primary, "pending_cost_inr")
    disputed = _sum_column(primary, "disputed_cost_inr")
    invoice_count = _sum_column(primary, "invoice_count")
    delivered_cases = _sum_column(primary, "delivered_case_equivalents")
    billed_per_case = (
        billed / delivered_cases
        if billed is not None and delivered_cases is not None and delivered_cases != 0.0
        else None
    )
    paid_per_case = (
        paid / delivered_cases
        if paid is not None and delivered_cases is not None and delivered_cases != 0.0
        else None
    )
    render_metric_cards(
        [
            MetricCard(
                "Settled/paid freight",
                format_inr(paid),
                f"Primary period × {primary_lens} settled-freight lens",
                tone="positive",
            ),
            MetricCard(
                "Settled/paid freight / delivered case",
                f"₹{paid_per_case:,.2f}" if paid_per_case is not None else "Not available",
                f"PAID invoices ÷ independently aggregated period × {primary_lens} cases",
                tone="positive",
            ),
            MetricCard(
                "Billed freight",
                format_inr(billed),
                f"{format_compact_number(invoice_count)} partner invoices · all statuses",
                tone="info",
            ),
            MetricCard(
                "Billed freight / delivered case",
                f"₹{billed_per_case:,.2f}" if billed_per_case is not None else "Not available",
                f"Period × {primary_lens} ratio of independently aggregated totals",
                tone="warning",
            ),
        ],
        columns=4,
    )
    render_metric_cards(
        [
            MetricCard(
                "Pending freight",
                format_inr(pending),
                "Billed but not marked paid",
                tone="warning",
            ),
            MetricCard(
                "Disputed freight",
                format_inr(disputed),
                "Requires invoice-resolution review",
                tone="danger",
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
                f"Operational denominator over the same period and {primary_lens}s",
                tone="info",
            ),
        ],
        columns=4,
    )

    if sync_status is not None and not sync_status.empty and "source_name" in sync_status:
        freight_status = sync_status.loc[sync_status["source_name"] == "freight_api"]
        if not freight_status.empty:
            latest = freight_status.iloc[0]
            completed = pd.to_datetime(latest.get("completed_at_utc"), errors="coerce")
            completed_label = (
                completed.strftime("%d %b %Y · %H:%M %Z")
                if pd.notna(completed)
                else "unknown"
            )
            render_callout(
                "Freight snapshot freshness",
                f"{int(latest.get('record_count', 0)):,} invoices; coverage "
                f"{latest.get('coverage_start')} to {latest.get('coverage_end')}; completed "
                f"{completed_label}; complete cache={bool(latest.get('is_complete'))}.",
                tone="info",
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
        minimum_route_records = 25
        valid_route = _sampled_route_ratios(
            route, minimum_records=minimum_route_records
        )
        if valid_route.empty:
            render_empty_state(
                "No sampled route ratio",
                "No route has at least 25 invoices and 25 eligible delivered orders in scope.",
            )
        else:
            valid_route = valid_route.nlargest(
                15, "freight_cost_per_delivered_case_inr"
            )
            valid_route["label"] = valid_route["route_name"].fillna(
                valid_route["route_code"]
            )
            figure = px.bar(
                valid_route.sort_values("freight_cost_per_delivered_case_inr"),
                x="freight_cost_per_delivered_case_inr",
                y="label",
                orientation="h",
                color_discrete_sequence=[TEAL],
                title="Highest route freight ratios · minimum-volume sample",
                hover_data={
                    "freight_cost_inr": ":,.0f",
                    "invoice_count": ":,.0f",
                    "delivered_orders": ":,.0f",
                    "delivered_case_equivalents": ":,.1f",
                },
            )
            figure.update_xaxes(title="INR per case-equivalent", tickprefix="₹")
            figure.update_yaxes(title=None)
            _plot(_figure_layout(figure, height=460))
            st.caption(
                "Ranked only among routes with at least 25 partner invoices and 25 eligible "
                "delivered orders; numerator and denominator remain independently aggregated."
            )

    sampled_current = _sampled_route_ratios(route)
    section_header(
        "Route cost rankings",
        "Best means the lowest current billed ratio among minimum-volume routes. "
        "Most improved compares the same ratio with the immediately preceding equal-length "
        "period; lower is better.",
    )
    ranking_columns = st.columns(2)
    with ranking_columns[0]:
        st.markdown("**Best current route ratios**")
        if sampled_current.empty:
            render_empty_state(
                "No qualified current routes",
                "No route clears the 25-invoice and 25-delivery sample floor.",
            )
        else:
            best = sampled_current.nsmallest(
                5, "freight_cost_per_delivered_case_inr"
            ).copy()
            best["route"] = best["route_name"].fillna(best["route_code"])
            dataframe_or_empty(
                best[
                    [
                        "route",
                        "freight_cost_per_delivered_case_inr",
                        "invoice_count",
                        "delivered_orders",
                    ]
                ],
                empty_title="No best-route rows",
                empty_body="No sampled route ratio is available.",
                max_rows=5,
                column_config={
                    "freight_cost_per_delivered_case_inr": st.column_config.NumberColumn(
                        "Billed freight / case", format="₹%.2f"
                    )
                },
            )
    with ranking_columns[1]:
        st.markdown("**Most-improved route ratios**")
        sampled_previous = _sampled_route_ratios(
            previous_route if previous_route is not None else pd.DataFrame()
        )
        if sampled_current.empty or sampled_previous.empty:
            render_empty_state(
                "No comparable prior route sample",
                "Both periods must clear the 25-invoice and 25-delivery floor per route.",
            )
        else:
            prior = sampled_previous[
                ["route_code", "freight_cost_per_delivered_case_inr"]
            ].rename(
                columns={
                    "freight_cost_per_delivered_case_inr": "prior_freight_per_case_inr"
                }
            )
            improved = sampled_current.merge(
                prior,
                on="route_code",
                how="inner",
                validate="one_to_one",
            )
            improved["change_inr_per_case"] = (
                improved["freight_cost_per_delivered_case_inr"]
                - improved["prior_freight_per_case_inr"]
            )
            improved["route"] = improved["route_name"].fillna(
                improved["route_code"]
            )
            improved = improved.nsmallest(5, "change_inr_per_case")
            dataframe_or_empty(
                improved[
                    [
                        "route",
                        "freight_cost_per_delivered_case_inr",
                        "prior_freight_per_case_inr",
                        "change_inr_per_case",
                        "invoice_count",
                        "delivered_orders",
                    ]
                ],
                empty_title="No comparable route rows",
                empty_body="No route appears in both qualified period samples.",
                max_rows=5,
                column_config={
                    "freight_cost_per_delivered_case_inr": st.column_config.NumberColumn(
                        "Current", format="₹%.2f"
                    ),
                    "prior_freight_per_case_inr": st.column_config.NumberColumn(
                        "Prior", format="₹%.2f"
                    ),
                    "change_inr_per_case": st.column_config.NumberColumn(
                        "Change", format="₹%+.2f"
                    ),
                },
            )

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
        _plot(_figure_layout(figure, height=360))

    attribution = primary.attrs.get("attribution") or carrier.attrs.get("attribution")
    ignored = tuple(
        dict.fromkeys(
            value
            for frame in (warehouse, route, carrier)
            for value in frame.attrs.get("ignored_filters", ())
        )
    )
    if attribution:
        render_source_note(str(attribution))
    if ignored:
        render_callout(
            "Freight filter boundary",
            "The invoice source lacks operational customer keys, and conflicting warehouse-route "
            "pairs make DC and route separate lenses. These active filters are therefore "
            "excluded from one or more displayed freight lenses: "
            + ", ".join(map(str, ignored))
            + ".",
            tone="warning",
        )


def render_leakage(
    service: AnalyticsService,
    context: FilterContext,
    definitions: Definitions,
    settings: Settings,
) -> None:
    page_header(
        "Commercial Leakage & Logistics Cost",
        "Quantify approved credit-note leakage and billed logistics cost using the evidence "
        "available in the pack. This is not an accounting-profit statement.",
        period_label=context.period_label,
        chips=context.dimension_chips,
        eyebrow="VALUE PROTECTION",
    )
    summary = service.executive_summary(context.filters, context.basis)
    credit = summary.get("approved_credit_note_rate")
    short_value = summary.get("short_delivery_value_exposure_inr")
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
            MetricCard(
                "Short-delivery value exposure",
                format_inr(short_value.value if short_value else None),
                (
                    f"{short_value.records:,} short-delivered order lines"
                    if short_value
                    else "No eligible short-delivered lines"
                ),
                tone="warning",
            ),
        ],
        columns=4,
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
        "disposition": "Disposition",
        "promotion": "Recorded promotion",
        "promotion_mechanic": "Promotion mechanic",
        "order_source": "Order source",
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
        "Physical disposition exposure",
        "RESTOCK, SCRAP, and VENDOR_RECOVERY are recorded dispositions. Values below are "
        "associated credit-note and physical-return exposure—not recovered cash.",
    )
    disposition = service.return_disposition_summary(context.filters)
    if disposition.empty:
        render_empty_state(
            "No disposition evidence",
            "No return-date credit-note lines match the active scope.",
        )
    else:
        quantity_column = (
            "return_eaches"
            if context.basis == QuantityBasis.EACHES
            else "return_case_equivalents"
        )

        def disposition_card(code: str, label: str, tone: Tone) -> MetricCard:
            row = disposition.loc[
                disposition["disposition"].astype(str).str.upper() == code
            ]
            if row.empty:
                return MetricCard(
                    label,
                    "Not available",
                    "No recorded disposition lines in scope",
                    tone=tone,
                )
            values = row.iloc[0]
            return MetricCard(
                label,
                format_inr(values.get("associated_credit_note_value_inr")),
                (
                    f"{format_compact_number(values.get(quantity_column))} "
                    f"{context.quantity_label} · {int(values.get('credit_note_lines', 0)):,} lines"
                ),
                tone=tone,
            )

        render_metric_cards(
            [
                disposition_card("RESTOCK", "Recorded restock exposure", "info"),
                disposition_card("SCRAP", "Recorded scrap exposure", "danger"),
                disposition_card(
                    "VENDOR_RECOVERY",
                    "Recorded vendor-return exposure",
                    "warning",
                ),
            ],
            columns=3,
        )
        disposition_columns = [
            "disposition",
            "credit_note_lines",
            "approved_lines",
            "pending_lines",
            "rejected_lines",
            quantity_column,
            "associated_credit_note_value_inr",
            "approved_credit_note_value_inr",
            "pending_credit_note_value_inr",
            "rejected_credit_note_value_inr",
        ]
        dataframe_or_empty(
            disposition[
                [column for column in disposition_columns if column in disposition]
            ],
            empty_title="No disposition rows",
            empty_body="No recorded disposition survived the active return-date scope.",
            max_rows=20,
            column_config={
                "associated_credit_note_value_inr": st.column_config.NumberColumn(
                    "Associated value", format="₹%.2f"
                ),
                "approved_credit_note_value_inr": st.column_config.NumberColumn(
                    "Approved value", format="₹%.2f"
                ),
                "pending_credit_note_value_inr": st.column_config.NumberColumn(
                    "Pending value", format="₹%.2f"
                ),
                "rejected_credit_note_value_inr": st.column_config.NumberColumn(
                    "Rejected value", format="₹%.2f"
                ),
            },
        )
        render_callout(
            "Disposition interpretation boundary",
            str(disposition.attrs.get("warning", "Recorded disposition is not recovery.")),
            tone="warning",
        )

    section_header(
        "Short-delivery commercial exposure",
        "Booked line value multiplied by the positive undelivered share. This prioritises "
        "commercial exposure; it is not accounting loss, profit, cash, or causal attribution.",
    )
    exposure_dimensions = {
        "category": "Category",
        "warehouse": "Warehouse",
        "route": "Route",
        "customer_region": "Customer region",
        "outlet": "Outlet",
        "sku": "SKU",
        "short_reason": "Recorded short reason",
        "promotion": "Recorded promotion",
        "order_source": "Order source",
    }
    exposure_dimension = st.selectbox(
        "Short-delivery exposure dimension",
        tuple(exposure_dimensions),
        format_func=exposure_dimensions.get,
        key="kp_short_value_dimension",
    )
    exposure = _safe_dimension(
        service.short_delivery_exposure(
            context.filters,
            exposure_dimension,
            context.basis,
            limit=20,
        )
    )
    if exposure.empty:
        render_empty_state(
            "No short-delivery exposure",
            "No eligible short-delivered lines match this requested-delivery scope.",
        )
    else:
        figure = px.bar(
            exposure.head(15).sort_values("short_delivery_value_exposure_inr"),
            x="short_delivery_value_exposure_inr",
            y="dimension_value",
            orientation="h",
            color_discrete_sequence=[GOLD],
            title=(
                "Short-delivery value exposure by "
                + exposure_dimensions[exposure_dimension].lower()
            ),
            hover_data={
                "affected_orders": ":,",
                "short_quantity": ":,.1f",
                "allocation_short_value_exposure_inr": ":,.0f",
                "post_allocation_short_value_exposure_inr": ":,.0f",
            },
        )
        figure.update_xaxes(title="Exposure (INR)", tickprefix="₹")
        figure.update_yaxes(title=None)
        _plot(_figure_layout(figure, height=430))
        render_source_note(str(exposure.attrs.get("value_definition", "")))

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
        route_result = call_external(
            external.payload,
            "freight_by_route",
            context.filters,
        )
        carrier_result = call_external(
            external.payload,
            "freight_by_carrier",
            context.filters,
        )
        active_period = Period(
            context.filters.start_date,
            context.filters.end_date,
            context.period_label,
        )
        prior_period = previous_period(active_period)
        previous_route = pd.DataFrame()
        if prior_period.end >= context.data_min_date:
            bounded_prior = Period(
                max(prior_period.start, context.data_min_date),
                prior_period.end,
                prior_period.label,
            )
            previous_route_result = call_external(
                external.payload,
                "freight_by_route",
                with_dates(context.filters, bounded_prior),
            )
            if previous_route_result.available and isinstance(
                previous_route_result.payload, pd.DataFrame
            ):
                previous_route = previous_route_result.payload
        sync_result = call_external(external.payload, "sync_status")
        sync_frame = (
            sync_result.payload
            if sync_result.available and isinstance(sync_result.payload, pd.DataFrame)
            else pd.DataFrame()
        )
        if (
            warehouse_result.available
            and route_result.available
            and carrier_result.available
            and isinstance(warehouse_result.payload, pd.DataFrame)
            and isinstance(route_result.payload, pd.DataFrame)
            and isinstance(carrier_result.payload, pd.DataFrame)
        ):
            _render_freight_frames(
                warehouse_result.payload,
                route_result.payload,
                carrier_result.payload,
                sync_frame,
                previous_route,
                primary_lens=("route" if context.filters.route_codes else "warehouse"),
            )
        else:
            messages = [
                result.message
                for result in (warehouse_result, route_result, carrier_result)
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
        "delivered cases and invoice-to-delivery lineage are not claimed. In the supplied full "
        "history, 36,370 of 41,500 invoice warehouse-route pairs conflict with the route master; "
        "DC and route lenses therefore remain independent and are never combined into a "
        "composite attribution.",
        tone="warning",
    )
    render_definitions(
        definitions,
        [
            "approved_credit_note_rate",
            "approved_credit_note_value_inr",
            "short_delivery_value_exposure_inr",
            "settled_freight_cost_per_case",
            "freight_cost_per_case",
        ],
    )


def _render_competitor_governance(
    external_service: Any,
    city: str,
    *,
    retailer: str | None = None,
    category: str | None = None,
    matched_listings: pd.DataFrame | None = None,
    source_coverage: pd.DataFrame | None = None,
) -> None:
    section_header(
        "Listing coverage, match review, and source history",
        "Unresolved listings stay outside headline price metrics. Reviewed YAML decisions carry "
        "reviewer, date, note, source, and algorithm provenance; source-dated prices remain "
        "evidence rather than a historical KPI headline.",
    )
    coverage = source_coverage if source_coverage is not None else pd.DataFrame()
    dataframe_or_empty(
        coverage,
        empty_title="No source-dated price coverage",
        empty_body="No allowed detail-page history matches this city and retailer scope.",
        max_rows=50,
        column_config={
            "coverage_start": st.column_config.DateColumn(
                "Coverage start", format="DD MMM YYYY"
            ),
            "coverage_end": st.column_config.DateColumn(
                "Coverage end", format="DD MMM YYYY"
            ),
        },
    )
    queue_result = call_external(
        external_service,
        "competitor_review_queue",
        city=city,
        limit=100,
    )
    if queue_result.available and isinstance(queue_result.payload, pd.DataFrame):
        queue = queue_result.payload
    else:
        queue = pd.DataFrame()
        render_empty_state("Review queue unavailable", queue_result.message)
    governance = queue.attrs.get("governance")
    if governance:
        render_callout("Governed exclusion boundary", str(governance), tone="warning")
    if retailer and "retailer" in queue:
        queue = queue.loc[queue["retailer"].astype(str) == retailer].copy()
    if category and "observed_category" in queue:
        queue = queue.loc[queue["observed_category"].astype(str) == category].copy()
    queue_columns = [
        "listing_id",
        "city",
        "retailer",
        "raw_title",
        "observed_brand",
        "observed_category",
        "observed_pack_value",
        "observed_pack_uom",
        "current_price_inr",
        "last_seen",
        "match_status",
        "match_confidence",
        "runner_up_confidence",
        "suggested_sku_code",
        "algorithm_reason",
    ]
    dataframe_or_empty(
        queue[[column for column in queue_columns if column in queue]],
        empty_title="Review queue is clear",
        empty_body="No unresolved current listings match this city.",
        max_rows=25,
        column_config={
            "current_price_inr": st.column_config.NumberColumn(
                "Observed price", format="₹%.2f"
            ),
            "last_seen": st.column_config.DateColumn("Last seen", format="DD MMM YYYY"),
            "match_confidence": st.column_config.NumberColumn(
                "Top confidence", format="%.3f"
            ),
            "runner_up_confidence": st.column_config.NumberColumn(
                "Runner-up", format="%.3f"
            ),
        },
    )

    catalog_parts: list[pd.DataFrame] = []
    matched_catalog_result = call_external(
        external_service,
        "competitor_matched_listing_catalog",
        city=city,
        retailer=retailer,
        category=category,
        limit=1_000,
    )
    if matched_catalog_result.available and isinstance(
        matched_catalog_result.payload, pd.DataFrame
    ):
        governed_catalog = matched_catalog_result.payload
        if not governed_catalog.empty:
            catalog_parts.append(governed_catalog.copy())
    elif matched_listings is not None and not matched_listings.empty:
        uses_current_listing_schema = "listing_id" in matched_listings
        matched_columns = (
            ["listing_id", "retailer", "raw_title", "sku_code"]
            if uses_current_listing_schema
            else [
                "competitor_listing_id",
                "competitor_retailer",
                "competitor_raw_title",
                "sku_code",
            ]
        )
        matched_catalog = matched_listings[
            [column for column in matched_columns if column in matched_listings]
        ].copy()
        matched_catalog = matched_catalog.rename(
            columns={
                "competitor_listing_id": "listing_id",
                "competitor_retailer": "retailer",
                "competitor_raw_title": "raw_title",
            }
        )
        matched_catalog["match_status"] = "matched"
        catalog_parts.append(matched_catalog)
    if not queue.empty and "listing_id" in queue:
        unresolved_columns = [
            "listing_id",
            "retailer",
            "raw_title",
            "suggested_sku_code",
            "match_status",
        ]
        unresolved_catalog = queue[
            [column for column in unresolved_columns if column in queue]
        ].copy()
        unresolved_catalog = unresolved_catalog.rename(
            columns={"suggested_sku_code": "sku_code"}
        )
        catalog_parts.append(unresolved_catalog)
    if not catalog_parts:
        render_empty_state(
            "No listing available for history",
            "Neither a retained matched listing nor an unresolved review listing is in scope.",
        )
        return
    listing_catalog = pd.concat(catalog_parts, ignore_index=True, sort=False)
    listing_catalog = listing_catalog.dropna(subset=["listing_id"]).copy()
    listing_catalog["listing_id"] = listing_catalog["listing_id"].astype(str)
    listing_catalog = listing_catalog.drop_duplicates("listing_id", keep="first")
    if listing_catalog.empty:
        render_empty_state(
            "No listing available for history",
            "The current evidence rows contain no listing identifier.",
        )
        return
    listing_labels: dict[str, str] = {}
    for _, row in listing_catalog.iterrows():
        listing_id = str(row["listing_id"])
        status = str(row.get("match_status") or "unknown")
        listing_retailer = str(row.get("retailer") or "Unknown retailer")
        title = str(row.get("raw_title") or "Untitled listing")
        listing_labels[listing_id] = (
            f"{status} · {listing_retailer} · {listing_id} · {title[:70]}"
        )
    listing_ids = list(listing_labels)
    selected_listing = st.selectbox(
        "Matched or unresolved listing history",
        listing_ids,
        format_func=listing_labels.get,
        key="kp_market_history_listing",
        help=(
            "Inspect source-published dated prices and the separate collection/match audit "
            "trail for one retained matched or unresolved listing."
        ),
    )
    source_history_result = call_external(
        external_service,
        "competitor_source_price_history",
        listing_id=selected_listing,
        limit=1_000,
    )
    audit_history_result = call_external(
        external_service,
        "competitor_observation_history",
        selected_listing,
        limit=100,
    )
    source_tab, audit_tab = st.tabs(
        ["Source-dated price evidence", "Collection and match audit"]
    )
    with source_tab:
        if source_history_result.available and isinstance(
            source_history_result.payload, pd.DataFrame
        ):
            source_history = source_history_result.payload
            source_columns = [
                "observed_on",
                "retailer",
                "raw_title",
                "observed_price_inr",
                "observed_pack_value",
                "observed_pack_uom",
                "sku_code",
                "product_name",
                "kestrel_pack_value",
                "kestrel_pack_uom",
                "pack_comparable",
                "unit_price_basis",
                "observed_unit_price_inr",
                "historical_kestrel_mrp_inr",
                "historical_kestrel_mrp_unit_inr",
                "historical_unit_price_gap_inr",
                "historical_mrp_premium_pct",
                "mrp_history_available",
                "mrp_effective_from",
                "mrp_effective_to",
                "match_status",
                "match_confidence",
                "match_provenance",
                "decision_source",
                "reviewer",
                "reviewed_on",
                "source_path",
            ]
            dataframe_or_empty(
                source_history[
                    [column for column in source_columns if column in source_history]
                ],
                empty_title="No source-dated price rows",
                empty_body=(
                    "The listing is selectable from current match evidence but has no parsed "
                    "source-published price history."
                ),
                max_rows=1_000,
                column_config={
                    "observed_on": st.column_config.DateColumn(
                        "Observed on", format="DD MMM YYYY"
                    ),
                    "observed_price_inr": st.column_config.NumberColumn(
                        "Observed pack price", format="₹%.2f"
                    ),
                    "observed_unit_price_inr": st.column_config.NumberColumn(
                        "Observed unit price", format="₹%.2f"
                    ),
                    "historical_kestrel_mrp_inr": st.column_config.NumberColumn(
                        "Effective Kestrel MRP", format="₹%.2f"
                    ),
                    "historical_kestrel_mrp_unit_inr": st.column_config.NumberColumn(
                        "Effective MRP / unit", format="₹%.2f"
                    ),
                    "historical_unit_price_gap_inr": st.column_config.NumberColumn(
                        "Historical unit gap", format="₹%.2f"
                    ),
                    "historical_mrp_premium_pct": st.column_config.NumberColumn(
                        "Historical premium", format="%.1f%%"
                    ),
                    "pack_comparable": st.column_config.CheckboxColumn(
                        "Comparable pack"
                    ),
                    "mrp_history_available": st.column_config.CheckboxColumn(
                        "Effective MRP found"
                    ),
                },
            )
            render_callout(
                "Historical evidence—not a KPI headline",
                f"{source_history.attrs.get('history_grain', '')} "
                f"{source_history.attrs.get('mrp_methodology', '')} "
                f"{source_history.attrs.get('pack_methodology', '')}",
                tone="warning",
            )
        else:
            render_empty_state(
                "Source-dated price history unavailable",
                source_history_result.message,
            )
    with audit_tab:
        if audit_history_result.available and isinstance(
            audit_history_result.payload, pd.DataFrame
        ):
            audit_history = audit_history_result.payload
            audit_columns = [
                "collected_at_utc",
                "current_price_inr",
                "is_available",
                "last_seen",
                "match_status",
                "sku_code",
                "match_provenance",
                "algorithm_status",
                "decision_source",
                "reviewer",
                "reviewed_on",
                "review_note",
                "sync_id",
                "observation_id",
            ]
            dataframe_or_empty(
                audit_history[
                    [column for column in audit_columns if column in audit_history]
                ],
                empty_title="No collection audit history",
                empty_body="No append-only scrape audit rows exist for this listing.",
                max_rows=100,
                column_config={
                    "current_price_inr": st.column_config.NumberColumn(
                        "Observed price", format="₹%.2f"
                    ),
                    "last_seen": st.column_config.DateColumn(
                        "Last seen", format="DD MMM YYYY"
                    ),
                    "reviewed_on": st.column_config.DateColumn(
                        "Reviewed", format="DD MMM YYYY"
                    ),
                },
            )
        else:
            render_empty_state(
                "Collection audit history unavailable",
                audit_history_result.message,
            )


def _render_context_result(title: str, result: ContextAssociationResult) -> None:
    gate = result.gate
    st.markdown(f"### {title}")
    if not gate.publishable:
        reasons = "; ".join(gate.reasons) or "The publication gate did not pass."
        render_callout(
            "Context withheld by publication gate",
            f"{reasons}. {gate.disclosure}",
            tone="warning",
        )
        return

    coverage = (
        f"{gate.coverage_start:%d %b %Y} – {gate.coverage_end:%d %b %Y}"
        if gate.coverage_start is not None and gate.coverage_end is not None
        else "not available"
    )
    join_coverage = (
        f"{gate.operational_join_ratio:.1%}"
        if gate.operational_join_ratio is not None
        else "not applicable"
    )
    render_callout(
        "Publication gate passed",
        f"Source coverage {coverage}; operational join coverage {join_coverage}; smallest "
        f"eligible cohort {gate.minimum_cohort_orders or 0:,} orders. {gate.disclosure}",
        tone="info",
    )
    display = result.frame.copy()
    display.attrs = {}
    if "context_group" in display:
        display["context_group"] = (
            display["context_group"].astype(str).str.replace("_", " ").str.title()
        )
    for column in (
        "observed_late_rate",
        "observed_temperature_excursion_rate",
        "observed_fill_rate_eaches",
    ):
        if column in display:
            display[column] = pd.to_numeric(display[column], errors="coerce") * 100
    dataframe_or_empty(
        display,
        empty_title="No publishable context rows",
        empty_body="The selected scope contains no cohorts that passed the context gate.",
        column_config={
            "observed_late_rate": st.column_config.NumberColumn(
                "Observed late rate", format="%.1f%%"
            ),
            "observed_temperature_excursion_rate": st.column_config.NumberColumn(
                "Observed excursion rate", format="%.1f%%"
            ),
            "observed_fill_rate_eaches": st.column_config.NumberColumn(
                "Observed fill rate", format="%.1f%%"
            ),
        },
    )


def _render_external_context(settings: Settings, context: FilterContext) -> None:
    section_header(
        "Weather and public-holiday context",
        "Optional cached sources are published only after completeness, period, freshness, "
        "join-coverage, and minimum-cohort gates pass.",
    )
    try:
        context_service = ContextAnalyticsService(settings.analytics_db)
        weather = context_service.weather_delivery_association(context.filters)
        holidays = context_service.holiday_service_association(context.filters)
    except (OSError, RuntimeError, ValueError) as error:
        render_empty_state(
            "External context unavailable",
            f"The core control tower remains available. Context query detail: {error}",
        )
        return
    _render_context_result("Weather-associated service", weather)
    _render_context_result("Public-holiday-associated service", holidays)
    render_source_note(
        "Context sources: cached Open-Meteo archive and India public-holiday calendar. Results "
        "are descriptive associations at governed date/location grains, never causal effects."
    )


def render_market(
    service: AnalyticsService,
    context: FilterContext,
    definitions: Definitions,
    settings: Settings,
) -> None:
    page_header(
        "Market & External Context",
        "Compare current Kestrel MRP with the lowest latest-observed competitor shelf price. "
        "Gaps appear only after a governed match; weather and holiday context is separately "
        "gated and never presented as causation.",
        period_label=f"{context.period_label} order mix · latest observed prices",
        chips=context.dimension_chips,
        eyebrow="COMMERCIAL SIGNAL",
    )
    external = external_analytics(settings)
    if not external.available:
        render_empty_state("Competitor metrics unavailable", external.message)
        _render_external_context(settings, context)
        render_definitions(definitions, ["competitor_price_gap"])
        return

    quality_result = call_external(external.payload, "competitor_match_quality")
    quality = (
        quality_result.payload
        if quality_result.available and isinstance(quality_result.payload, pd.DataFrame)
        else pd.DataFrame()
    )
    sync_result = call_external(external.payload, "sync_status")
    sync_status = (
        sync_result.payload
        if sync_result.available and isinstance(sync_result.payload, pd.DataFrame)
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
    source_coverage_result = call_external(
        external.payload,
        "competitor_source_price_coverage",
        city=city,
    )
    source_coverage = (
        source_coverage_result.payload
        if source_coverage_result.available
        and isinstance(source_coverage_result.payload, pd.DataFrame)
        else pd.DataFrame()
    )
    retailer_options = (
        sorted(source_coverage["retailer"].dropna().astype(str).unique())
        if "retailer" in source_coverage
        else []
    )
    selected_retailer = st.selectbox(
        "Retailer",
        ["All retailers", *retailer_options],
        key="kp_market_retailer",
        help=(
            "Retailers come from parsed source-dated detail-page coverage for the selected city."
        ),
    )
    retailer = None if selected_retailer == "All retailers" else selected_retailer
    market_scope = f"{city} · {retailer or 'all retailers'}"
    scoped_source_coverage = source_coverage
    if retailer and "retailer" in scoped_source_coverage:
        scoped_source_coverage = scoped_source_coverage.loc[
            scoped_source_coverage["retailer"].astype(str) == retailer
        ].copy()

    initial_result = call_external(
        external.payload,
        "competitor_price_gap",
        context.filters,
        city=city,
        retailer=retailer,
        top_n=100,
    )
    if not initial_result.available or not isinstance(initial_result.payload, pd.DataFrame):
        render_empty_state("Competitor metrics unavailable", initial_result.message)
        _render_competitor_governance(
            external.payload,
            city,
            retailer=retailer,
            source_coverage=scoped_source_coverage,
        )
        _render_external_context(settings, context)
        render_definitions(definitions, ["competitor_price_gap"])
        return
    initial = initial_result.payload
    all_matches_result = call_external(
        external.payload,
        "competitor_current_comparable_matches",
        city=city,
        retailer=retailer,
        limit=1000,
    )
    all_matches = (
        all_matches_result.payload
        if all_matches_result.available
        and isinstance(all_matches_result.payload, pd.DataFrame)
        else pd.DataFrame()
    )
    categories = sorted(
        {
            *(
                initial["category"].dropna().astype(str).tolist()
                if "category" in initial
                else []
            ),
            *(
                all_matches["category"].dropna().astype(str).tolist()
                if "category" in all_matches
                else []
            ),
        }
    )
    selected_category = st.selectbox(
        "Category",
        ["All categories", *categories],
        key="kp_market_category",
    )
    if selected_category == "All categories":
        frame = initial
        scoped_all_matches = all_matches
    else:
        category_result = call_external(
            external.payload,
            "competitor_price_gap",
            context.filters,
            city=city,
            category=selected_category,
            retailer=retailer,
            top_n=100,
        )
        if not category_result.available or not isinstance(category_result.payload, pd.DataFrame):
            render_empty_state("Category price gap unavailable", category_result.message)
            _render_competitor_governance(
                external.payload,
                city,
                retailer=retailer,
                category=selected_category,
                source_coverage=scoped_source_coverage,
            )
            _render_external_context(settings, context)
            return
        frame = category_result.payload
        scoped_matches_result = call_external(
            external.payload,
            "competitor_current_comparable_matches",
            city=city,
            retailer=retailer,
            category=selected_category,
            limit=1000,
        )
        scoped_all_matches = (
            scoped_matches_result.payload
            if scoped_matches_result.available
            and isinstance(scoped_matches_result.payload, pd.DataFrame)
            else pd.DataFrame()
        )

    if frame.empty and scoped_all_matches.empty:
        reason = frame.attrs.get("unavailable_reason")
        render_empty_state(
            "No price-position rows",
            str(reason or "No eligible high-value Kestrel SKUs match the active scope and market."),
        )
        _render_competitor_governance(
            external.payload,
            city,
            retailer=retailer,
            category=(
                None if selected_category == "All categories" else selected_category
            ),
            source_coverage=scoped_source_coverage,
        )
        _render_external_context(settings, context)
        render_definitions(definitions, ["competitor_price_gap"])
        return
    if frame.empty:
        render_callout(
            "No operational top-SKU cohort",
            "The active order filters contain no eligible dispatch-ranked SKU rows. Current "
            "qualifying source listings remain available below because they use the selected "
            "market city, retailer, and category rather than order dimensions.",
            tone="warning",
        )

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
    city_quality = (
        quality.loc[quality["city"].astype(str) == city].copy()
        if "city" in quality
        else pd.DataFrame()
    )
    city_listings = (
        int(pd.to_numeric(city_quality["listings"], errors="coerce").fillna(0).sum())
        if "listings" in city_quality
        else 0
    )
    city_matched = 0
    if {"match_status", "listings"}.issubset(city_quality.columns):
        city_matched = int(
            pd.to_numeric(
                city_quality.loc[
                    city_quality["match_status"].astype(str).str.casefold()
                    == "matched",
                    "listings",
                ],
                errors="coerce",
            )
            .fillna(0)
            .sum()
        )
    city_match_coverage = city_matched / city_listings if city_listings else None
    unmatched = frame.loc[frame["lowest_competitor_price_inr"].isna()]
    unmatched_dispatch_value = _sum_column(unmatched, "dispatch_value_inr")
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
                f"{city} listing match coverage",
                _percent(city_match_coverage),
                f"{city_matched:,} of {city_listings:,} current city-wide listings",
                tone="positive" if city_match_coverage is not None else "warning",
            ),
            MetricCard(
                "Unmatched top-SKU value",
                format_inr(unmatched_dispatch_value),
                f"{len(unmatched):,} of {len(frame):,} reviewed SKUs lack a qualifying price",
                tone="warning",
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
                f"BazaarPulse · {market_scope} · "
                f"{_source_sync_detail(sync_status, 'bazaarpulse')}",
                tone="warning",
            ),
        ],
        columns=3,
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
            title=f"Current comparable-pack MRP premium · {market_scope}",
            hover_data={
                "kestrel_mrp_inr": ":.2f",
                "lowest_competitor_price_inr": ":.2f",
                "competitor_retailer": True,
                "competitor_listing_id": True,
                "observed_pack_value": ":.2f",
                "observed_pack_uom": True,
                "kestrel_pack_value": ":.2f",
                "kestrel_pack_uom": True,
                "unit_price_basis": True,
                "lowest_competitor_unit_price_inr": ":.2f",
                "kestrel_mrp_unit_inr": ":.2f",
                "matched_listings": ":,.0f",
            },
        )
        figure.update_xaxes(title="MRP premium", ticksuffix="%", zeroline=True)
        figure.update_yaxes(title=None)
        _plot(_figure_layout(figure, height=max(400, min(720, len(chart) * 30))))

    section_header(
        "Service-price attention signals",
        "Period-specific line fill and short-delivery value exposure are paired by SKU with the "
        "latest source-dated price evidence. This is descriptive triage, not causation or margin.",
    )
    attention_result = call_external(
        external.payload,
        "competitor_service_price_attention",
        context.filters,
        city=city,
        retailer=retailer,
        category=(
            None if selected_category == "All categories" else selected_category
        ),
        top_n=100,
    )
    if not attention_result.available or not isinstance(
        attention_result.payload, pd.DataFrame
    ):
        render_empty_state(
            "Service-price attention unavailable",
            attention_result.message,
        )
    else:
        service_price = attention_result.payload
        attention_columns = [
            "sku_code",
            "product_name",
            "category",
            "eligible_orders",
            "line_fill_rate_pct",
            "short_delivery_value_exposure_inr",
            "short_case_equivalents",
            "estimated_dispatch_value_inr",
            "listing_id",
            "retailer",
            "observed_on",
            "observed_price_inr",
            "observed_pack_value",
            "observed_pack_uom",
            "kestrel_pack_value",
            "kestrel_pack_uom",
            "pack_comparable",
            "unit_price_basis",
            "observed_unit_price_inr",
            "historical_kestrel_mrp_unit_inr",
            "historical_unit_price_gap_inr",
            "historical_mrp_premium_pct",
            "price_evidence_status",
        ]
        dataframe_or_empty(
            service_price[
                [column for column in attention_columns if column in service_price]
            ],
            empty_title="No service-price attention rows",
            empty_body="No eligible service SKU matches this market evidence scope.",
            max_rows=100,
            column_config={
                "line_fill_rate_pct": st.column_config.NumberColumn(
                    "Line fill rate", format="%.1f%%"
                ),
                "observed_on": st.column_config.DateColumn(
                    "Observed on", format="DD MMM YYYY"
                ),
                "observed_price_inr": st.column_config.NumberColumn(
                    "Observed pack price", format="₹%.2f"
                ),
                "estimated_dispatch_value_inr": st.column_config.NumberColumn(
                    "Estimated dispatch value", format="₹%.0f"
                ),
                "short_delivery_value_exposure_inr": st.column_config.NumberColumn(
                    "Short-value exposure", format="₹%.0f"
                ),
                "pack_comparable": st.column_config.CheckboxColumn(
                    "Comparable pack"
                ),
                "observed_unit_price_inr": st.column_config.NumberColumn(
                    "Observed unit price", format="₹%.2f"
                ),
                "historical_kestrel_mrp_unit_inr": st.column_config.NumberColumn(
                    "Effective MRP / unit", format="₹%.2f"
                ),
                "historical_unit_price_gap_inr": st.column_config.NumberColumn(
                    "Effective unit gap", format="₹%.2f"
                ),
                "historical_mrp_premium_pct": st.column_config.NumberColumn(
                    "Effective premium", format="%.1f%%"
                ),
            },
        )
        render_callout(
            "Attention flag—not an explanation",
            str(
                service_price.attrs.get(
                    "methodology",
                    "Service and price evidence are independently selected by SKU; this is "
                    "not evidence that price caused fill loss.",
                )
            ),
            tone="warning",
        )

    section_header(
        "Current comparable-pack SKU evidence",
        "The retained lowest listing and retailer are explicit. Only exact normalized-quantity "
        "matches contribute; missing or non-comparable candidates remain null, never zero.",
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
        "competitor_listing_id",
        "competitor_retailer",
        "competitor_raw_title",
        "observed_pack_value",
        "observed_pack_uom",
        "kestrel_pack_value",
        "kestrel_pack_uom",
        "pack_comparable",
        "unit_price_basis",
        "lowest_competitor_unit_price_inr",
        "kestrel_mrp_unit_inr",
        "unit_price_gap_inr",
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
            "pack_comparable": st.column_config.CheckboxColumn(
                "Exact normalized pack"
            ),
            "lowest_competitor_unit_price_inr": st.column_config.NumberColumn(
                "Observed unit price", format="₹%.2f"
            ),
            "kestrel_mrp_unit_inr": st.column_config.NumberColumn(
                "Kestrel MRP / unit", format="₹%.2f"
            ),
            "unit_price_gap_inr": st.column_config.NumberColumn(
                "Unit-price gap", format="₹%.2f"
            ),
        },
    )

    section_header(
        "All current qualifying listing matches",
        "Complete selected-city listing evidence, independent of the top-SKU dispatch ranking. "
        "Every available governed match with an exact normalized pack is retained.",
    )
    all_match_columns = [
        "listing_id",
        "retailer",
        "raw_title",
        "sku_code",
        "product_name",
        "category",
        "match_confidence",
        "match_provenance",
        "current_price_inr",
        "kestrel_mrp_inr",
        "observed_pack_value",
        "observed_pack_uom",
        "kestrel_pack_value",
        "kestrel_pack_uom",
        "unit_price_basis",
        "competitor_unit_price_inr",
        "kestrel_mrp_unit_inr",
        "unit_price_gap_inr",
        "mrp_premium_pct",
        "last_seen",
    ]
    dataframe_or_empty(
        scoped_all_matches[
            [column for column in all_match_columns if column in scoped_all_matches]
        ],
        empty_title="No qualifying current matches",
        empty_body=(
            all_matches_result.message
            if not all_matches_result.available
            else "No current listing clears the selected city, retailer, category, and pack "
            "governance rules."
        ),
        max_rows=1000,
        column_config={
            "match_confidence": st.column_config.NumberColumn(
                "Match confidence", format="%.3f"
            ),
            "current_price_inr": st.column_config.NumberColumn(
                "Observed pack price", format="₹%.2f"
            ),
            "kestrel_mrp_inr": st.column_config.NumberColumn(
                "Kestrel MRP", format="₹%.2f"
            ),
            "competitor_unit_price_inr": st.column_config.NumberColumn(
                "Observed unit price", format="₹%.2f"
            ),
            "kestrel_mrp_unit_inr": st.column_config.NumberColumn(
                "Kestrel MRP / unit", format="₹%.2f"
            ),
            "unit_price_gap_inr": st.column_config.NumberColumn(
                "Unit-price gap", format="₹%.2f"
            ),
            "mrp_premium_pct": st.column_config.NumberColumn(
                "MRP premium", format="%.1f%%"
            ),
            "last_seen": st.column_config.DateColumn(
                "Latest observed", format="DD MMM YYYY"
            ),
        },
    )
    if not scoped_all_matches.empty:
        render_source_note(
            f"{len(scoped_all_matches):,} current qualifying listing match(es) in "
            f"{market_scope}; this table is not capped by dispatch-value rank."
        )

    section_header(
        "Match-quality coverage",
        "Every current listing remains visible by governed entity-resolution outcome; only "
        "matched listings contribute to headline price gaps.",
    )
    quality_display = quality.copy()
    if "average_confidence" in quality_display:
        quality_display["average_confidence"] = (
            pd.to_numeric(quality_display["average_confidence"], errors="coerce") * 100
        )
    quality_columns = st.columns((1.35, 1))
    with quality_columns[0]:
        if quality_display.empty:
            render_empty_state(
                "No match-quality chart",
                "No competitor entity-resolution snapshot is available.",
            )
        else:
            figure = px.bar(
                quality_display,
                x="city",
                y="listings",
                color="match_status",
                barmode="stack",
                color_discrete_map={
                    "matched": TEAL,
                    "ambiguous": GOLD,
                    "low_confidence": RED,
                    "unmatched": NAVY,
                },
                title="Current listings by city and match outcome",
            )
            figure.update_xaxes(title=None)
            figure.update_yaxes(title="Listings")
            _plot(_figure_layout(figure, height=370))
    with quality_columns[1]:
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
    _render_competitor_governance(
        external.payload,
        city,
        retailer=retailer,
        category=None if selected_category == "All categories" else selected_category,
        matched_listings=scoped_all_matches,
        source_coverage=scoped_source_coverage,
    )
    render_source_note(
        "Source: governed external snapshot of BazaarPulse observations and deterministic "
        "product-master matching. City and listing availability apply to the observation date."
    )
    render_definitions(
        definitions,
        ["competitor_price_gap", "competitor_match_coverage"],
    )
    _render_external_context(settings, context)


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
        resolver_provenance = metadata.get("resolver_provenance")
        matched_example = metadata.get("matched_example")
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
        resolver_provenance = None
        matched_example = None
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
        if resolver_provenance:
            st.markdown(f"**Resolver provenance**  \n{resolver_provenance}")
        if matched_example:
            st.markdown(f"**Nearest approved example**  \n{matched_example}")
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
    page_header(
        "Ask Kestrel",
        "Questions are resolved through a governed intent router and the same metric service as "
        "the dashboard. Unrestricted text-to-SQL is never used as a fallback.",
        period_label=context.period_label,
        chips=context.dimension_chips,
        eyebrow="GOVERNED QUESTION INTERFACE",
    )
    memory_key = "kp_nlq_conversation_memory"
    if memory_key not in st.session_state:
        memory_result = create_nlq_memory()
        if memory_result.available:
            st.session_state[memory_key] = memory_result.payload
    conversation_memory = st.session_state.get(memory_key)

    model_status = nlq_model_status(settings)
    controls = st.columns([4, 1])
    with controls[0]:
        if model_status.available:
            st.caption(
                "Local paraphrase understanding is ready. Metric calculation remains "
                "governed and deterministic."
            )
        elif settings.nlq_semantic_enabled:
            st.caption(
                "Exact rules are active. Install the optional keyless local model to "
                "understand more paraphrases (~23 MB on common CPUs)."
            )
            if st.button("Install local language model", key="kp_install_nlq_model"):
                with st.spinner("Downloading and verifying the pinned local model…"):
                    install_result = install_nlq_model(settings)
                if install_result.available:
                    st.success(install_result.message)
                    st.rerun()
                else:
                    st.error(install_result.message)
        else:
            st.caption("Local semantic matching is disabled; exact governed rules are active.")
    with controls[1]:
        if conversation_memory is not None and st.button(
            "New conversation", key="kp_clear_nlq_memory"
        ):
            conversation_memory.clear()
            st.success("Follow-up context cleared.")
    examples = (
        "Why did fill rate drop in the West customer region?",
        "Which five outlets had the lowest case fill rate?",
        "What was OTIF by customer region?",
        "Which warehouses have the largest overdue backlog?",
        "Where is allocation weakest by customer region?",
        "Which routes are missing proof of delivery?",
    )
    st.caption("Example questions: " + "  ·  ".join(examples))
    st.caption(
        "After an answer, try a follow-up such as “What about West customer region?”, "
        "“Now by warehouse”, or “And in cases?”."
    )
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
                    settings=settings,
                    conversation_memory=conversation_memory,
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
        "Trust Center",
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
        "Ask Kestrel requests clarification when a named region is not qualified as customer "
        "or DC/warehouse geography.",
        tone="warning",
    )
    render_callout(
        "External attribution is limited",
        "Freight has no delivery key and competitor listings have no Kestrel SKU key. Aggregation "
        "and high-confidence matching boundaries remain visible wherever those sources appear. "
        "For freight, 36,370 of 41,500 invoice warehouse-route pairs conflict with the route "
        "master, so DC and route ratios are governed as separate lenses.",
        tone="warning",
    )

    section_header("Metric registry")
    registry = pd.DataFrame(
        [
            {
                "Metric": definition.title,
                "Version": definition.version,
                "Status": definition.status,
                "Formula": definition.formula,
                "Grain": definition.grain,
                "Date basis": definition.date_basis,
                "Allowed dimensions": ", ".join(definition.allowed_dimensions),
                "Exclusions": "; ".join(definition.exclusions),
                "Source models": ", ".join(definition.source_models),
                "Warning": definition.warning,
                "Interpretation limits": "; ".join(
                    definition.interpretation_limits
                ),
            }
            for definition in definitions.values()
        ]
    )
    dataframe_or_empty(
        registry,
        empty_title="Metric registry unavailable",
        empty_body="No metric definitions were loaded from the project configuration.",
    )

    section_header(
        "Recent governed operations",
        "Bounded local run history; only allow-listed operational fields are displayed.",
    )
    try:
        recent_events = read_recent_events(settings.runtime_dir, limit=20)
    except OSError:
        recent_events = []
    operations = pd.DataFrame(
        [
            {
                "Recorded (UTC)": event.get("recorded_at_utc"),
                "Operation": event.get("operation"),
                "Status": event.get("status"),
                "Duration (ms)": event.get("duration_ms"),
                "Run": str(event.get("run_id", ""))[:12],
            }
            for event in reversed(recent_events)
        ]
    )
    dataframe_or_empty(
        operations,
        empty_title="No local run events yet",
        empty_body="Run a governed build or sync command to populate local operational history.",
        max_rows=20,
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

        def provider_for(source_name: str, fallback: str) -> str:
            if sync_status.empty or "source_name" not in sync_status:
                return fallback
            source_rows = sync_status.loc[sync_status["source_name"] == source_name]
            if source_rows.empty:
                return fallback
            try:
                details = json.loads(str(source_rows.iloc[0].get("details_json") or "{}"))
            except json.JSONDecodeError:
                return fallback
            return str(details.get("provider") or details.get("source_url") or fallback)

        readiness = pd.DataFrame(
            [
                {
                    "Source": "Freight partner API",
                    "Provider": "Supplied deterministic partner server",
                    "Governed snapshot": "Available" if availability.get("freight") else "Missing",
                    "Rule": "Never substitute driver-entered fuel",
                },
                {
                    "Source": "BazaarPulse",
                    "Provider": "Bundled BazaarPulse listing site",
                    "Governed snapshot": "Available"
                    if availability.get("competitor")
                    else "Missing",
                    "Rule": "Latest observed; high-confidence matches only",
                },
                {
                    "Source": "Open-Meteo weather",
                    "Provider": provider_for(
                        "open_meteo_weather", "Open-Meteo archive API"
                    ),
                    "Governed snapshot": "Available"
                    if availability.get("weather")
                    else "Missing",
                    "Rule": "Publication-gated descriptive association only",
                },
                {
                    "Source": "India public holidays",
                    "Provider": provider_for(
                        "india_public_holidays", "Provider unavailable in sync metadata"
                    ),
                    "Governed snapshot": "Available"
                    if availability.get("holidays")
                    else "Missing",
                    "Rule": "National calendar; state closures not inferred",
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
