"""Reusable presentation components for the Streamlit control tower."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Literal

import pandas as pd
import streamlit as st

from kestrel.metrics.definitions import MetricDefinition
from kestrel.metrics.service import MetricValue

NAVY = "#102A43"
TEAL = "#167D76"
GOLD = "#D9A441"
RED = "#C94C4C"
INK = "#17212B"
MUTED = "#657383"
GRID = "#E4E9E5"
PAPER = "#FFFFFF"
CANVAS = "#F4F6F2"

PLOTLY_CONFIG: dict[str, object] = {
    "displayModeBar": False,
    "responsive": True,
}

Tone = Literal["neutral", "positive", "warning", "danger", "info"]


@dataclass(frozen=True, slots=True)
class MetricCard:
    label: str
    value: str
    detail: str
    delta: str | None = None
    tone: Tone = "neutral"


def inject_theme() -> None:
    """Apply the restrained Kestrel visual system without external assets."""

    st.markdown(
        f"""
        <style>
        :root {{
            --kp-navy: {NAVY};
            --kp-teal: {TEAL};
            --kp-gold: {GOLD};
            --kp-red: {RED};
            --kp-ink: {INK};
            --kp-muted: {MUTED};
            --kp-grid: {GRID};
            --kp-paper: {PAPER};
            --kp-canvas: {CANVAS};
        }}
        [data-testid="stAppViewContainer"] {{
            background:
                radial-gradient(circle at 88% 2%, rgba(22,125,118,.08), transparent 24rem),
                var(--kp-canvas);
        }}
        [data-testid="stHeader"] {{ background: rgba(244,246,242,.88); }}
        [data-testid="stSidebar"] {{
            background: #0d2638;
            border-right: 1px solid rgba(255,255,255,.08);
        }}
        [data-testid="stSidebar"] * {{ color: #edf4f2; }}
        [data-testid="stSidebar"] [data-baseweb="select"] > div,
        [data-testid="stSidebar"] [data-baseweb="input"] > div {{
            background: rgba(255,255,255,.08);
            border-color: rgba(255,255,255,.14);
        }}
        [data-testid="stSidebar"] hr {{ border-color: rgba(255,255,255,.12); }}
        .block-container {{ max-width: 1500px; padding-top: 2rem; padding-bottom: 4rem; }}
        h1, h2, h3 {{ color: var(--kp-navy); letter-spacing: -.025em; }}
        .kp-brand {{ padding: .2rem 0 .8rem; }}
        .kp-brand-mark {{
            display: inline-flex; align-items: center; justify-content: center;
            width: 2rem; height: 2rem; border-radius: .55rem;
            background: linear-gradient(145deg, #27a89d, #d9a441);
            color: #092434; font-weight: 900; margin-right: .6rem;
        }}
        .kp-brand-name {{ font-weight: 760; letter-spacing: .06em; font-size: .82rem; }}
        .kp-brand-sub {{ color: #9fb3bd !important; font-size: .72rem; margin: .3rem 0 0 2.65rem; }}
        .kp-eyebrow {{
            color: var(--kp-teal); font-size: .73rem; font-weight: 750;
            letter-spacing: .14em; text-transform: uppercase; margin-bottom: .35rem;
        }}
        .kp-title {{
            color: var(--kp-navy); font-size: clamp(1.9rem, 3vw, 3rem);
            font-weight: 760; line-height: 1.06; letter-spacing: -.045em; margin: 0;
        }}
        .kp-lede {{
            color: var(--kp-muted); max-width: 76ch; font-size: .98rem;
            line-height: 1.55; margin: .7rem 0 1rem;
        }}
        .kp-chip {{
            display: inline-block; color: #365264; background: rgba(255,255,255,.72);
            border: 1px solid var(--kp-grid); border-radius: 999px;
            padding: .28rem .65rem; margin: 0 .35rem .35rem 0;
            font-size: .72rem; font-weight: 650;
        }}
        .kp-section {{ margin: 1.8rem 0 .65rem; }}
        .kp-section h2 {{ font-size: 1.2rem; margin: 0; }}
        .kp-section p {{ color: var(--kp-muted); font-size: .82rem; margin: .28rem 0 0; }}
        .kp-card {{
            min-height: 8.5rem; background: rgba(255,255,255,.94);
            border: 1px solid var(--kp-grid); border-radius: 1rem;
            padding: 1.05rem 1.05rem .95rem; box-shadow: 0 7px 22px rgba(16,42,67,.055);
            position: relative; overflow: hidden;
        }}
        .kp-card::before {{
            content: ""; position: absolute; inset: 0 auto 0 0; width: .23rem;
            background: #a7b4bd;
        }}
        .kp-card--positive::before {{ background: var(--kp-teal); }}
        .kp-card--warning::before {{ background: var(--kp-gold); }}
        .kp-card--danger::before {{ background: var(--kp-red); }}
        .kp-card--info::before {{ background: #4A7DB6; }}
        .kp-card-label {{ color: var(--kp-muted); font-size: .72rem; font-weight: 700;
                         letter-spacing: .055em; text-transform: uppercase; }}
        .kp-card-value {{ color: var(--kp-navy); font-size: 1.72rem; font-weight: 760;
                         letter-spacing: -.04em; margin: .45rem 0 .25rem; }}
        .kp-card-detail {{ color: var(--kp-muted); font-size: .74rem; line-height: 1.35; }}
        .kp-card-delta {{ color: var(--kp-teal); font-size: .72rem; font-weight: 700;
                         margin-top: .42rem; }}
        .kp-callout {{
            border: 1px solid var(--kp-grid); border-left: .28rem solid var(--kp-teal);
            background: rgba(255,255,255,.88); border-radius: .75rem;
            padding: .9rem 1rem; color: var(--kp-ink); margin: .75rem 0 1rem;
        }}
        .kp-callout--danger {{ border-left-color: var(--kp-red); background: #fff8f6; }}
        .kp-callout--warning {{ border-left-color: var(--kp-gold); background: #fffaf0; }}
        .kp-callout-title {{ color: var(--kp-navy); font-weight: 760; font-size: .86rem; }}
        .kp-callout-body {{ color: var(--kp-muted); font-size: .79rem; line-height: 1.48;
                           margin-top: .25rem; }}
        .kp-empty {{
            text-align: center; border: 1px dashed #bac5c0; border-radius: .9rem;
            background: rgba(255,255,255,.62); padding: 2rem 1.25rem; margin: .5rem 0 1rem;
        }}
        .kp-empty-title {{ color: var(--kp-navy); font-weight: 740; }}
        .kp-empty-body {{ color: var(--kp-muted); font-size: .82rem; margin-top: .35rem; }}
        .kp-source {{ color: var(--kp-muted); font-size: .72rem; line-height: 1.45;
                     border-top: 1px solid var(--kp-grid); padding-top: .55rem; }}
        div[data-testid="stDataFrame"] {{
            border: 1px solid var(--kp-grid); border-radius: .8rem; overflow: hidden;
        }}
        div[data-testid="stPlotlyChart"] {{
            background: rgba(255,255,255,.82); border: 1px solid var(--kp-grid);
            border-radius: 1rem; padding: .35rem; box-shadow: 0 5px 18px rgba(16,42,67,.035);
        }}
        button[kind="primary"] {{ background: var(--kp-teal); border-color: var(--kp-teal); }}
        @media (max-width: 760px) {{
            .block-container {{ padding-top: 1rem; }}
            .kp-card {{ min-height: 7.5rem; }}
            .kp-title {{ font-size: 1.9rem; }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def sidebar_brand() -> None:
    st.sidebar.markdown(
        """
        <div class="kp-brand">
          <span class="kp-brand-mark">K</span><span class="kp-brand-name">KESTREL</span>
          <div class="kp-brand-sub">SUPPLY CHAIN CONTROL TOWER</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def page_header(
    title: str,
    description: str,
    *,
    period_label: str,
    chips: list[str] | None = None,
    eyebrow: str = "OPERATIONS INTELLIGENCE",
) -> None:
    chip_values = [period_label, *(chips or [])]
    chip_html = "".join(f'<span class="kp-chip">{escape(value)}</span>' for value in chip_values)
    st.markdown(
        f"""
        <div class="kp-eyebrow">{escape(eyebrow)}</div>
        <h1 class="kp-title">{escape(title)}</h1>
        <p class="kp-lede">{escape(description)}</p>
        <div>{chip_html}</div>
        """,
        unsafe_allow_html=True,
    )


def section_header(title: str, description: str | None = None) -> None:
    supporting = f"<p>{escape(description)}</p>" if description else ""
    st.markdown(
        f'<div class="kp-section"><h2>{escape(title)}</h2>{supporting}</div>',
        unsafe_allow_html=True,
    )


def render_callout(title: str, body: str, *, tone: Tone = "info") -> None:
    class_name = "kp-callout"
    if tone in {"danger", "warning"}:
        class_name += f" kp-callout--{tone}"
    st.markdown(
        f"""
        <div class="{class_name}">
          <div class="kp-callout-title">{escape(title)}</div>
          <div class="kp-callout-body">{escape(body)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_empty_state(title: str, body: str) -> None:
    st.markdown(
        f"""
        <div class="kp-empty">
          <div class="kp-empty-title">{escape(title)}</div>
          <div class="kp-empty-body">{escape(body)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def format_compact_number(value: float | int | None, *, decimals: int = 1) -> str:
    if value is None:
        return "Not available"
    absolute = abs(float(value))
    sign = "-" if float(value) < 0 else ""
    for threshold, suffix in ((10_000_000, "Cr"), (100_000, "L"), (1_000, "K")):
        if absolute >= threshold:
            scaled = absolute / threshold
            return f"{sign}{scaled:.{decimals}f}{suffix}"
    return f"{float(value):,.{decimals}f}" if not float(value).is_integer() else f"{int(value):,}"


def format_inr(value: float | int | None) -> str:
    if value is None:
        return "Not available"
    return f"₹{format_compact_number(value)}"


def format_metric_value(metric: MetricValue | None) -> str:
    if metric is None or metric.value is None:
        return "Not available"
    if metric.unit == "percent":
        return f"{metric.value * 100:.1f}%"
    if metric.unit == "per_100":
        return f"{metric.value:.1f}"
    if metric.unit == "cases":
        return format_compact_number(metric.value)
    if metric.unit == "INR":
        return format_inr(metric.value)
    if metric.unit == "INR_per_case":
        return f"₹{metric.value:,.2f}"
    return format_compact_number(metric.value)


def comparison_delta(
    current: MetricValue | None,
    previous: MetricValue | None,
) -> str | None:
    if current is None or previous is None or current.value is None or previous.value is None:
        return None
    difference = current.value - previous.value
    prefix = "+" if difference > 0 else ""
    if current.unit == "percent":
        return f"{prefix}{difference * 100:.1f} pp vs prior period"
    if current.unit == "per_100":
        return f"{prefix}{difference:.1f} per 100 vs prior period"
    return f"{prefix}{format_compact_number(difference)} vs prior period"


def render_metric_cards(cards: list[MetricCard], *, columns: int = 3) -> None:
    for start in range(0, len(cards), columns):
        row = st.columns(columns)
        for column, card in zip(row, cards[start : start + columns], strict=False):
            delta = f'<div class="kp-card-delta">{escape(card.delta)}</div>' if card.delta else ""
            with column:
                st.markdown(
                    f"""
                    <div class="kp-card kp-card--{card.tone}">
                      <div class="kp-card-label">{escape(card.label)}</div>
                      <div class="kp-card-value">{escape(card.value)}</div>
                      <div class="kp-card-detail">{escape(card.detail)}</div>
                      {delta}
                    </div>
                    """,
                    unsafe_allow_html=True,
                )


def render_source_note(text: str) -> None:
    st.markdown(f'<div class="kp-source">{escape(text)}</div>', unsafe_allow_html=True)


def render_definitions(
    definitions: dict[str, MetricDefinition],
    keys: list[str],
    *,
    label: str = "Definitions and source notes",
) -> None:
    available = [definitions[key] for key in keys if key in definitions]
    if not available:
        return
    with st.expander(label, expanded=False):
        for definition in available:
            st.markdown(f"**{definition.title}** · `{definition.status}`")
            st.caption(
                f"Formula: {definition.formula}  |  Grain: {definition.grain}  |  "
                f"Date basis: {definition.date_basis}"
            )
            if definition.warning:
                st.caption(f"Caution: {definition.warning}")


def dataframe_or_empty(
    frame: pd.DataFrame,
    *,
    empty_title: str,
    empty_body: str,
    column_config: dict[str, object] | None = None,
    max_rows: int | None = None,
) -> None:
    if frame.empty:
        render_empty_state(empty_title, empty_body)
        return
    display = frame.head(max_rows) if max_rows else frame
    st.dataframe(
        display,
        hide_index=True,
        width="stretch",
        column_config=column_config,
    )
