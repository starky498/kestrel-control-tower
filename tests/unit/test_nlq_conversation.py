from __future__ import annotations

from datetime import date

import pytest

from kestrel.metrics.periods import Period
from kestrel.metrics.service import QuantityBasis
from kestrel.nlq.conversation import (
    ConversationMemory,
    ConversationStatus,
    FilterPatch,
    FollowUpRelation,
    IntentPatch,
    build_follow_up_patch,
    is_follow_up_candidate,
)
from kestrel.nlq.router import (
    DimensionKey,
    IntentFilters,
    MetricKey,
    QuestionIntent,
    Ranking,
)


def _intent() -> QuestionIntent:
    return QuestionIntent(
        raw_question="Which warehouses had the lowest fill rate in June?",
        metric=MetricKey.FILL_RATE,
        dimensions=(DimensionKey.WAREHOUSE,),
        period=Period(date(2026, 6, 1), date(2026, 6, 30), "June 2026"),
        filters=IntentFilters(
            customer_regions=("West",),
            warehouse_regions=("Central",),
            channels=("GT",),
        ),
        quantity_basis=QuantityBasis.CASE_EQUIVALENTS,
        ranking=Ranking.WORST,
        limit=10,
    )


class _PatchService:
    def available_date_range(self) -> tuple[date, date]:
        return date(2025, 1, 1), date(2026, 6, 30)

    def filter_options(self) -> dict[str, list[str]]:
        return {
            "customer_regions": ["North", "South", "West"],
            "warehouse_regions": ["North", "South", "West"],
            "warehouse_codes": ["WH01", "WH02"],
            "route_codes": ["RT0001", "RT0002"],
            "outlet_codes": ["OUT00001"],
            "channels": ["GT", "MT"],
        }


def test_follow_up_overrides_explicit_fields_and_records_inheritance() -> None:
    original = _intent()
    memory = ConversationMemory(original)

    result = memory.resolve(
        IntentPatch(
            raw_question="What about the North DC region, worst five?",
            filters=FilterPatch.from_updates(warehouse_regions=("North",)),
            ranking=Ranking.WORST,
            limit=5,
        )
    )

    assert result.status == ConversationStatus.READY
    assert result.intent is not None
    assert result.intent.raw_question == "What about the North DC region, worst five?"
    assert result.intent.metric == MetricKey.FILL_RATE
    assert result.intent.period == original.period
    assert result.intent.dimensions == (DimensionKey.WAREHOUSE,)
    assert result.intent.filters.warehouse_regions == ("North",)
    assert result.intent.filters.customer_regions == ("West",)
    assert result.intent.filters.channels == ("GT",)
    assert result.intent.limit == 5
    assert result.explicit_fields == (
        "filters.warehouse_regions",
        "ranking",
        "limit",
    )
    assert "metric" in result.inherited_fields
    assert "period" in result.inherited_fields
    assert "dimensions" in result.inherited_fields
    assert "filters.customer_regions" in result.inherited_fields
    assert "filters.channels" in result.inherited_fields
    assert "filters.warehouse_regions" not in result.inherited_fields
    assert result.used_context

    # Resolution is a proposal. The last validated context is not changed yet.
    assert memory.last_intent == original
    assert memory.last_intent.filters.warehouse_regions == ("Central",)


def test_explicit_metric_period_and_dimensions_replace_previous_values() -> None:
    memory = ConversationMemory(_intent())
    july = Period(date(2026, 7, 1), date(2026, 7, 31), "July 2026")

    result = memory.resolve(
        IntentPatch(
            raw_question="Instead show overall OTIF for July.",
            metric=MetricKey.STRICT_OTIF,
            dimensions=(),
            period=july,
            quantity_basis=QuantityBasis.EACHES,
        )
    )

    assert result.status == ConversationStatus.READY
    assert result.intent is not None
    assert result.intent.metric == MetricKey.STRICT_OTIF
    assert result.intent.dimensions == ()
    assert result.intent.period == july
    assert result.intent.quantity_basis == QuantityBasis.EACHES
    assert result.intent.filters == _intent().filters
    assert result.explicit_fields == (
        "metric",
        "dimensions",
        "period",
        "quantity_basis",
    )
    assert "filters" in result.inherited_fields


def test_filter_patch_can_clear_one_filter_without_clearing_others() -> None:
    memory = ConversationMemory(_intent())

    result = memory.resolve(
        IntentPatch(
            raw_question="Remove the customer-region filter.",
            filters=FilterPatch.from_updates(customer_regions=()),
        )
    )

    assert result.status == ConversationStatus.READY
    assert result.intent is not None
    assert result.intent.filters.customer_regions == ()
    assert result.intent.filters.warehouse_regions == ("Central",)
    assert result.intent.filters.channels == ("GT",)
    assert result.explicit_fields == ("filters.customer_regions",)
    assert "filters.warehouse_regions" in result.inherited_fields
    assert "filters.channels" in result.inherited_fields


def test_show_all_can_explicitly_clear_an_inherited_limit() -> None:
    memory = ConversationMemory(_intent())

    result = memory.resolve(IntentPatch(raw_question="Show all of them.", clear_limit=True))

    assert result.status == ConversationStatus.READY
    assert result.intent is not None
    assert result.intent.limit is None
    assert result.explicit_fields == ("limit",)
    assert "limit" not in result.inherited_fields


def test_follow_up_without_context_fails_closed() -> None:
    memory = ConversationMemory()

    result = memory.resolve(
        IntentPatch(raw_question="What about North?", dimensions=(DimensionKey.WAREHOUSE,))
    )

    assert result.status == ConversationStatus.NO_CONTEXT
    assert result.intent is None
    assert not result.used_context
    assert not memory.has_context


@pytest.mark.parametrize(
    ("relation", "expected_status"),
    [
        (FollowUpRelation.AMBIGUOUS, ConversationStatus.AMBIGUOUS),
        (FollowUpRelation.UNRELATED, ConversationStatus.UNRELATED),
    ],
)
def test_ambiguous_or_unrelated_follow_up_never_merges(
    relation: FollowUpRelation,
    expected_status: ConversationStatus,
) -> None:
    original = _intent()
    memory = ConversationMemory(original)

    result = memory.resolve(
        IntentPatch(
            raw_question="What about West?",
            relation=relation,
            relation_reason="West could mean customer region or DC region.",
            filters=FilterPatch.from_updates(customer_regions=("West",)),
        )
    )

    assert result.status == expected_status
    assert result.intent is None
    assert result.message == "West could mean customer region or DC region."
    assert memory.last_intent == original


def test_follow_up_with_no_explicit_supported_change_is_unrelated() -> None:
    original = _intent()
    memory = ConversationMemory(original)

    result = memory.resolve(IntentPatch(raw_question="Thanks, that is helpful."))

    assert result.status == ConversationStatus.UNRELATED
    assert result.intent is None
    assert memory.last_intent == original


def test_caller_commits_only_a_validated_resolution() -> None:
    memory = ConversationMemory(_intent())
    result = memory.resolve(
        IntentPatch(
            raw_question="Now show South.",
            filters=FilterPatch.from_updates(customer_regions=("South",)),
        )
    )
    assert result.intent is not None

    memory.remember_validated(result.intent)

    assert memory.last_intent == result.intent
    assert memory.last_intent.filters.customer_regions == ("South",)
    memory.clear()
    assert memory.last_intent is None
    assert not memory.has_context


def test_conversation_memory_is_isolated_per_instance() -> None:
    first = ConversationMemory(_intent())
    second = ConversationMemory()

    first.clear()

    assert not first.has_context
    assert not second.has_context


def test_filter_patch_rejects_unknown_or_unmarked_values() -> None:
    with pytest.raises(ValueError, match="Unknown intent filter"):
        FilterPatch.from_updates(unknown_codes=("X",))

    with pytest.raises(ValueError, match="not marked explicit"):
        FilterPatch(
            values=IntentFilters(customer_regions=("West",)),
            explicit_fields=frozenset({"warehouse_regions"}),
        )


def test_invalid_partial_values_fail_before_resolution() -> None:
    with pytest.raises(ValueError, match="positive"):
        IntentPatch(raw_question="Show none.", limit=0)

    with pytest.raises(ValueError, match="end before"):
        IntentPatch(
            raw_question="Use this invalid period.",
            period=Period(date(2026, 7, 2), date(2026, 7, 1), "Invalid"),
        )

    with pytest.raises(ValueError, match="set and clear"):
        IntentPatch(raw_question="Conflicting limit.", limit=5, clear_limit=True)


def test_conservative_follow_up_parser_extracts_only_explicit_changes() -> None:
    previous = _intent()

    patch = build_follow_up_patch(
        "Now show the worst five by route in cases for last month",
        _PatchService(),  # type: ignore[arg-type]
        previous,
    )

    assert patch.metric is None
    assert patch.dimensions == (DimensionKey.ROUTE,)
    assert patch.period == Period(date(2026, 6, 1), date(2026, 6, 30), "June 2026")
    assert patch.quantity_basis == QuantityBasis.CASE_EQUIVALENTS
    assert patch.ranking == Ranking.WORST
    assert patch.limit == 5
    assert patch.filters is None


def test_follow_up_parser_preserves_ambiguous_region_as_clarification() -> None:
    patch = build_follow_up_patch(
        "What about West?",
        _PatchService(),  # type: ignore[arg-type]
        _intent(),
    )

    assert patch.relation == FollowUpRelation.AMBIGUOUS
    assert "customer region or DC/warehouse region" in str(patch.relation_reason)


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Now by route", True),
        ("And in cases?", True),
        ("OTIF?", True),
        ("Q1?", True),
        ("Why?", True),
        ("Show all", True),
        ("Please explain our complete supply-chain strategy", False),
    ],
)
def test_follow_up_candidate_detection_is_conservative(
    question: str, expected: bool
) -> None:
    assert is_follow_up_candidate(question) is expected


@pytest.mark.parametrize(
    "question",
    [
        "Now forecast fill rate next month",
        "What about profit by warehouse?",
        "And revenue from returns?",
        "Who is responsible for delivery failures?",
        "What about damaged stock by warehouse?",
        "What about customer satisfaction by warehouse?",
    ],
)
def test_follow_up_parser_rejects_unsupported_or_unrecognized_topics(question: str) -> None:
    patch = build_follow_up_patch(
        question,
        _PatchService(),  # type: ignore[arg-type]
        _intent(),
    )

    assert patch.relation == FollowUpRelation.UNRELATED
    assert patch.is_empty


@pytest.mark.parametrize(
    "question",
    [
        "And last week?",
        "And last 30 days?",
        "And today?",
        "And all time?",
        "And all available?",
        "Show all results",
        "No limit",
        "Without a breakdown",
        "And the smallest five by warehouse?",
        "And no cities",
    ],
)
def test_supported_follow_up_vocabulary_is_not_rejected_as_unknown(question: str) -> None:
    patch = build_follow_up_patch(
        question,
        _PatchService(),  # type: ignore[arg-type]
        _intent(),
    )

    assert patch.relation == FollowUpRelation.RELATED


def test_complete_metric_question_starting_with_fragment_word_is_not_a_follow_up() -> None:
    assert not is_follow_up_candidate("In Q1 show OTIF by warehouse")
