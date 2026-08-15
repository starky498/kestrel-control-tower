"""Streamlit application orchestration."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import streamlit as st

from kestrel.config import ConfigurationError, Settings
from kestrel.metrics.definitions import MetricDefinition, load_metric_definitions
from kestrel.metrics.service import AnalyticsService
from kestrel.ui.components import inject_theme, render_callout, sidebar_brand
from kestrel.ui.filters import FilterContext, render_global_filters
from kestrel.ui.pages import (
    render_ask,
    render_cold_chain,
    render_data_trust,
    render_executive,
    render_leakage,
    render_market,
    render_service,
)

PageRenderer = Callable[
    [AnalyticsService, FilterContext, dict[str, MetricDefinition], Settings], None
]

PAGES: dict[str, PageRenderer] = {
    "Executive Control Tower": render_executive,
    "Service & Fulfilment": render_service,
    "Cold Chain & Inventory": render_cold_chain,
    "Measured Leakage & Freight": render_leakage,
    "Market Position": render_market,
    "Ask Kestrel": render_ask,
    "Data Trust": render_data_trust,
}


@st.cache_resource(show_spinner=False)
def _analytics_service(database_path: str, near_expiry_days: int) -> AnalyticsService:
    return AnalyticsService(Path(database_path), near_expiry_days=near_expiry_days)


@st.cache_data(show_spinner=False)
def _metric_definitions(path: str, modified_ns: int) -> dict[str, MetricDefinition]:
    del modified_ns
    return load_metric_definitions(Path(path))


def _render_setup_error(error: Exception, settings: Settings | None = None) -> None:
    st.markdown("## The control tower is not ready yet")
    st.error(str(error))
    if settings is not None:
        st.info(
            "Point `KESTREL_SOURCE_DB` at the supplied database, build the analytical "
            "warehouse, then reload this page. The source database itself is never modified."
        )
        with st.expander("Expected local paths"):
            st.code(
                f"Source: {settings.source_db}\nAnalytics: {settings.analytics_db}",
                language="text",
            )


def run_app() -> None:
    st.set_page_config(
        page_title="Kestrel Supply Chain Control Tower",
        page_icon="K",
        layout="wide",
        initial_sidebar_state="expanded",
        menu_items={
            "About": (
                "Kestrel Supply Chain Control Tower · governed service, cold-chain, "
                "measured-leakage, freight, and price-position evidence."
            )
        },
    )
    inject_theme()
    sidebar_brand()

    try:
        settings = Settings.load()
        analytics_path = settings.require_analytics_db()
    except (ConfigurationError, OSError, ValueError) as error:
        _render_setup_error(error, locals().get("settings"))
        st.stop()

    definitions_path = settings.project_root / "config" / "metrics.yml"
    try:
        definitions = _metric_definitions(
            str(definitions_path), definitions_path.stat().st_mtime_ns
        )
    except (OSError, TypeError, ValueError) as error:
        definitions = {}
        render_callout(
            "Metric registry could not be loaded",
            f"Headline values remain governed by AnalyticsService, but definition annotations "
            f"are unavailable: {error}",
            tone="warning",
        )

    service = _analytics_service(str(analytics_path), settings.near_expiry_days)
    page_name = st.sidebar.radio("Workspace", tuple(PAGES), key="kp_active_page")
    st.sidebar.divider()
    try:
        context = render_global_filters(service)
    except Exception as error:
        _render_setup_error(
            RuntimeError(f"Unable to load governed filter options: {error}"), settings
        )
        st.stop()

    st.sidebar.divider()
    st.sidebar.caption(
        "All core numbers come from the read-only semantic metrics layer. Missing values remain "
        "missing; no source is silently substituted."
    )

    try:
        PAGES[page_name](service, context, definitions, settings)
    except Exception as error:
        st.error("This page could not complete its governed query.")
        st.caption(
            "The selected filters remain intact. Try broadening the scope or reload after the "
            "analytical build completes."
        )
        with st.expander("Technical detail"):
            st.code(f"{type(error).__name__}: {error}", language="text")

    st.markdown("---")
    st.caption(
        "Kestrel Provisions · Decision support from supplied synthetic data · Associations are not "
        "causal attribution · Measured leakage is not accounting profit"
    )
