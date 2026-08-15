"""Typed, deterministic natural-language access to governed metrics."""

from __future__ import annotations

import re
from dataclasses import replace
from functools import lru_cache
from typing import Any

from kestrel.config import Settings
from kestrel.metrics.periods import Period
from kestrel.metrics.service import FilterSet, QuantityBasis
from kestrel.nlq.conversation import (
    ConversationMemory,
    ConversationStatus,
    build_follow_up_patch,
    is_follow_up_candidate,
)
from kestrel.nlq.router import (
    AnswerStatus,
    ContextMetricService,
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
    normalize_business_spelling,
)
from kestrel.nlq.semantic import (
    SemanticBackendUnavailable,
    SemanticIntentResolver,
    build_local_semantic_resolver,
)


def _has_explicit_period(question: str) -> bool:
    month_names = (
        "january|february|march|april|may|june|july|august|september|october|"
        "november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec"
    )
    return bool(
        re.search(
            rf"\b(?:fy\s*\d{{2,4}}|q[1-4]|last|latest|today|all time|"
            rf"all available|full history|"
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
        promotion_codes=parsed.promotion_codes or selected.promotion_codes,
        order_sources=parsed.order_sources or selected.order_sources,
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


def _context_service(service: MetricService) -> ContextMetricService | None:
    database_path = getattr(service, "database_path", None)
    if database_path is None:
        return None
    try:
        from kestrel.metrics.context import ContextAnalyticsService
    except ImportError:
        return None
    return ContextAnalyticsService(database_path)


@lru_cache(maxsize=4)
def _cached_semantic_resolver(
    model_path: str,
    marker_version: int,
    min_confidence: float,
    min_margin: float,
) -> SemanticIntentResolver:
    del marker_version  # Included in the cache key so a verified reinstall reloads the model.
    return build_local_semantic_resolver(
        model_path,
        min_confidence=min_confidence,
        min_margin=min_margin,
    )


def _configured_semantic_resolver(settings: Settings | None) -> SemanticIntentResolver | None:
    if settings is None or not settings.nlq_semantic_enabled:
        return None
    marker = settings.nlq_model_path / "kestrel-model.json"
    if not marker.is_file():
        return None
    try:
        marker_version = marker.stat().st_mtime_ns
    except OSError:
        return None
    try:
        return _cached_semantic_resolver(
            str(settings.nlq_model_path),
            marker_version,
            settings.nlq_min_confidence,
            settings.nlq_min_margin,
        )
    except (OSError, ValueError, SemanticBackendUnavailable):
        # The optional resolver must never prevent exact governed rules from running.
        return None


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
    if answer.intent is not None:
        metadata["resolver"] = answer.intent.resolver
        if answer.intent.resolver_provenance:
            metadata["resolver_provenance"] = answer.intent.resolver_provenance
        if answer.intent.resolver_confidence is not None:
            metadata["semantic_similarity"] = answer.intent.resolver_confidence
        if answer.intent.matched_example:
            metadata["matched_example"] = answer.intent.matched_example
        if answer.intent.spelling_corrections:
            metadata["spelling_corrections"] = answer.intent.spelling_corrections
        if answer.intent.inherited_fields:
            metadata["inherited_fields"] = answer.intent.inherited_fields
    return {"answer": answer.summary, "evidence": records, "metadata": metadata}


_DEFINITION_KEYS = {
    MetricKey.FILL_RATE: "fill_rate",
    MetricKey.STRICT_OTIF: "strict_otif",
    MetricKey.RETURNS: "approved_credit_note_value_inr",
    MetricKey.CHILLED_EXCURSIONS: "temperature_excursions_per_100",
    MetricKey.LATE_ROUTES: "delivery_late_over_2h_rate",
    MetricKey.MARKET_PRICE_GAP: "competitor_price_gap",
    MetricKey.FREIGHT_PER_CASE: "settled_freight_cost_per_case",
    MetricKey.DISCONTINUED_SKUS: "orders_after_discontinuation",
    MetricKey.ALLOCATION_RATE: "allocation_rate",
    MetricKey.POST_ALLOCATION_FULFILMENT: "post_allocation_fulfilment",
    MetricKey.ON_TIME_RATE: "on_time_rate",
    MetricKey.DELIVERY_ON_TIME_RATE: "delivery_on_time_rate",
    MetricKey.POD_COVERAGE: "pod_coverage_rate",
    MetricKey.DELIVERY_FAILURES: "recorded_failure_rate",
    MetricKey.BACKLOG: "overdue_backlog_orders",
    MetricKey.SHORT_DELIVERY_EXPOSURE: "short_delivery_value_exposure_inr",
    MetricKey.INVENTORY_RISK: "near_expiry_cases",
    MetricKey.CREDIT_NOTE_LEAKAGE: "approved_credit_note_rate",
    MetricKey.COMPETITOR_COVERAGE: "competitor_match_coverage",
}


def _rememberable(answer: QuestionAnswer) -> bool:
    return answer.status in {AnswerStatus.OK, AnswerStatus.NO_DATA}


def _follow_up_failure(
    *,
    status: ConversationStatus,
    message: str,
) -> QuestionAnswer:
    answer_status = (
        AnswerStatus.AMBIGUOUS
        if status == ConversationStatus.AMBIGUOUS
        else AnswerStatus.UNSUPPORTED
    )
    return QuestionAnswer(
        status=answer_status,
        summary=message,
        interpretation="No metric query was executed.",
        definition="",
        sources=(),
        suggestions=(
            "Name the metric, period, geography role, and grouping explicitly.",
        ),
    )


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
    settings: Settings | None = None,
    conversation_memory: ConversationMemory | None = None,
    semantic_resolver: SemanticIntentResolver | None = None,
) -> dict[str, object]:
    """Answer through governed metrics with optional local semantics and session memory."""

    router = QuestionRouter(
        service,
        _external_service(service),
        _context_service(service),
        semantic_resolver or _configured_semantic_resolver(settings),
    )

    if (
        conversation_memory is not None
        and conversation_memory.last_intent is not None
        and is_follow_up_candidate(question)
    ):
        patch = build_follow_up_patch(
            question,
            service,
            conversation_memory.last_intent,
        )
        resolution = conversation_memory.resolve(patch)
        if resolution.status != ConversationStatus.READY or resolution.intent is None:
            return _ui_payload(
                _follow_up_failure(
                    status=resolution.status,
                    message=resolution.message,
                )
            )
        _, spelling_corrections = normalize_business_spelling(question)
        follow_up_intent = replace(
            resolution.intent,
            raw_question=question.strip(),
            resolver="conversation",
            resolver_provenance="session-scoped typed intent memory",
            resolver_confidence=None,
            matched_example=None,
            inherited_fields=resolution.inherited_fields,
            spelling_corrections=spelling_corrections,
        )
        follow_up_answer = router.answer_intent(follow_up_intent)
        follow_up_answer = _with_registered_definition(follow_up_answer, definitions)
        if _rememberable(follow_up_answer):
            conversation_memory.remember_validated(follow_up_intent)
        return _ui_payload(follow_up_answer)

    parsed = router.parse(question)
    if parsed.status != ParseStatus.READY or parsed.intent is None:
        return _ui_payload(router.answer_parsed(parsed))

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
    answer = _with_registered_definition(answer, definitions)
    if conversation_memory is not None and _rememberable(answer):
        conversation_memory.remember_validated(scoped_intent)
    return _ui_payload(answer)


__all__ = [
    "AnswerStatus",
    "ConversationMemory",
    "ConversationStatus",
    "ContextMetricService",
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
    "build_follow_up_patch",
    "is_follow_up_candidate",
]
