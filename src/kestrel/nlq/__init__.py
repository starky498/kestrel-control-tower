"""Typed, deterministic natural-language access to governed metrics."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from kestrel.metrics.periods import Period
from kestrel.metrics.service import FilterSet, QuantityBasis
from kestrel.nlq.router import (
    AnswerStatus,
    DimensionKey,
    EvidenceBlock,
    ExternalMetricService,
    IntentFilters,
    MetricKey,
    MetricService,
    NLQRouter,
    ParsedQuestion,
    ParseStatus,
    QuestionAnswer,
    QuestionIntent,
    QuestionRouter,
    Ranking,
)


def _has_explicit_period(question: str) -> bool:
    month_names = (
        "january|february|march|april|may|june|july|august|september|october|"
        "november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec"
    )
    return bool(
        re.search(
            rf"\b(?:fy\s*\d{{2,4}}|q[1-4]|last|latest|today|all time|"
            rf"{month_names}|\d{{4}}-\d{{2}}-\d{{2}})\b",
            question.casefold(),
        )
    )


def _has_explicit_basis(question: str) -> bool:
    return bool(re.search(r"\b(?:eaches|units?|cases?|case equivalents?)\b", question.casefold()))


def _inherit_filters(parsed: IntentFilters, selected: FilterSet) -> IntentFilters:
    """Inherit dashboard scope only for fields the question did not state."""

    return IntentFilters(
        customer_regions=parsed.customer_regions or selected.customer_regions,
        warehouse_regions=parsed.warehouse_regions or selected.warehouse_regions,
        warehouse_codes=parsed.warehouse_codes or selected.warehouse_codes,
        route_codes=parsed.route_codes or selected.route_codes,
        outlet_codes=parsed.outlet_codes or selected.outlet_codes,
        channels=parsed.channels or selected.channels,
        cities=parsed.cities,
        categories=parsed.categories,
    )


def _external_service(service: MetricService) -> ExternalMetricService | None:
    database_path = getattr(service, "database_path", None)
    if database_path is None:
        return None
    try:
        from kestrel.metrics.external import ExternalAnalyticsService
    except ImportError:
        return None
    return ExternalAnalyticsService(database_path)


def _ui_payload(answer: QuestionAnswer) -> dict[str, object]:
    records: list[dict[str, object]] = []
    for block in answer.evidence:
        for row in block.rows:
            record = dict(zip(block.columns, row, strict=True))
            record = {"evidence": block.title, "source": block.source, **record}
            records.append(record)
    metadata: dict[str, object] = {
        "status": answer.status.value,
        "interpretation": answer.interpretation,
        "definition": answer.definition,
        "sources": ", ".join(answer.sources),
    }
    if answer.warnings:
        metadata["warning"] = " ".join(answer.warnings)
    return {"answer": answer.summary, "evidence": records, "metadata": metadata}


_DEFINITION_KEYS = {
    MetricKey.FILL_RATE: "fill_rate",
    MetricKey.STRICT_OTIF: "strict_otif",
    MetricKey.RETURNS: "approved_credit_note_rate",
    MetricKey.CHILLED_EXCURSIONS: "temperature_excursions_per_100",
    MetricKey.LATE_ROUTES: "late_over_2h_rate",
    MetricKey.MARKET_PRICE_GAP: "competitor_price_gap",
    MetricKey.FREIGHT_PER_CASE: "freight_cost_per_case",
}


def _with_registered_definition(
    answer: QuestionAnswer, definitions: dict[str, Any] | None
) -> QuestionAnswer:
    if answer.intent is None or not definitions:
        return answer
    definition = definitions.get(_DEFINITION_KEYS.get(answer.intent.metric, ""))
    if definition is None:
        return answer
    parts = [
        str(getattr(definition, "formula", answer.definition)),
        f"Grain: {getattr(definition, 'grain', 'not specified')}",
        f"Date basis: {getattr(definition, 'date_basis', 'not specified')}",
        f"Eligible population: {getattr(definition, 'eligible_population', 'not specified')}",
    ]
    warning = str(getattr(definition, "warning", "")).strip()
    warnings = answer.warnings
    if warning and warning not in warnings:
        warnings = (*warnings, warning)
    return replace(answer, definition=". ".join(parts), warnings=warnings)


def answer_question(
    *,
    question: str,
    service: MetricService,
    filters: FilterSet,
    basis: QuantityBasis,
    definitions: dict[str, Any] | None = None,
) -> dict[str, object]:
    """UI adapter that respects the current dashboard scope unless text overrides it."""

    router = QuestionRouter(service, _external_service(service))
    parsed = router.parse(question)
    if parsed.status != ParseStatus.READY or parsed.intent is None:
        return _ui_payload(router.answer(question))

    intent = parsed.intent
    period = intent.period
    if not _has_explicit_period(question):
        period = Period(filters.start_date, filters.end_date, "Selected dashboard period")
    quantity_basis = intent.quantity_basis if _has_explicit_basis(question) else basis
    scoped_intent = replace(
        intent,
        period=period,
        filters=_inherit_filters(intent.filters, filters),
        quantity_basis=quantity_basis,
    )
    answer = router.answer_intent(scoped_intent)
    return _ui_payload(_with_registered_definition(answer, definitions))


__all__ = [
    "AnswerStatus",
    "DimensionKey",
    "EvidenceBlock",
    "ExternalMetricService",
    "IntentFilters",
    "MetricKey",
    "MetricService",
    "NLQRouter",
    "ParseStatus",
    "ParsedQuestion",
    "QuestionAnswer",
    "QuestionIntent",
    "QuestionRouter",
    "Ranking",
    "answer_question",
]
