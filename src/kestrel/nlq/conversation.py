"""Pure session memory and safe follow-up merging for Ask Kestrel.

The UI owns one :class:`ConversationMemory` instance per browser session.  This
module deliberately has no Streamlit dependency and never executes a metric.
Its conservative parser can reduce a clearly signalled short follow-up to an
allow-listed :class:`IntentPatch`; complete questions remain the router's job.

Resolution is non-mutating: callers validate and execute the returned
``QuestionIntent`` before committing it with ``remember_validated``.  This
keeps an ambiguous or failed follow-up from corrupting the last known-good
conversation context.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, fields, replace
from enum import StrEnum

from kestrel.metrics.periods import Period
from kestrel.metrics.service import QuantityBasis
from kestrel.nlq.router import (
    DimensionKey,
    IntentFilters,
    MetricKey,
    MetricService,
    QuestionIntent,
    Ranking,
    _available_date_range,
    _detect_dimensions,
    _detect_metrics,
    _extract_filters,
    _extract_limit,
    _extract_quantity_basis,
    _extract_ranking,
    _requests_leading_return_reason,
    _resolve_period,
    _unsupported_request_message,
    normalize_business_spelling,
)


class FollowUpRelation(StrEnum):
    """The upstream resolver's relationship assessment for a short question."""

    RELATED = "related"
    AMBIGUOUS = "ambiguous"
    UNRELATED = "unrelated"


class ConversationStatus(StrEnum):
    """Outcome of trying to apply a partial follow-up to session context."""

    READY = "ready"
    NO_CONTEXT = "no_context"
    AMBIGUOUS = "ambiguous"
    UNRELATED = "unrelated"


_FILTER_FIELD_NAMES = tuple(field.name for field in fields(IntentFilters))
_FILTER_FIELD_SET = frozenset(_FILTER_FIELD_NAMES)
_FOLLOW_UP_START = re.compile(
    r"^(?:and\b|also\b|but\b|how about\b|instead\b|now\b|same\b|then\b|"
    r"show all\b|what about\b|what changed\b|what if\b)"
)
_FRAGMENT_START = re.compile(
    r"^(?:across\b|best\b|bottom\b|by\b|for\b|in\b|last\b|latest\b|"
    r"overall\b|top\b|why\b|worst\b)"
)
_PERIOD_CUE = re.compile(
    r"\b(?:fy\s*\d{2,4}|q[1-4]|last|latest|today|all time|all available|full history|"
    r"january|february|march|april|may|june|july|august|september|october|"
    r"november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec|"
    r"\d{4}-\d{2}-\d{2})\b"
)
_RANKING_CUE = re.compile(
    r"\b(?:lowest|smallest|worst|bottom|poorest|highest|best|top|largest|most)\b"
)
_LIMIT_CUE = re.compile(
    r"\b(?:top|bottom|lowest|smallest|highest|largest|worst|best|which)\s+"
    r"(?:\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten|twenty|fifty|hundred)\b|"
    r"\b(?:\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten|twenty|fifty|hundred)\s+"
    r"(?:lowest|smallest|highest|largest|worst|best|bottom|top|poorest)\b"
)
_FOLLOW_UP_GRAMMAR = frozenset(
    {
        "a",
        "about",
        "across",
        "all",
        "also",
        "and",
        "basis",
        "best",
        "bottom",
        "breakdown",
        "but",
        "by",
        "case",
        "cases",
        "category",
        "categories",
        "channel",
        "channels",
        "changed",
        "cities",
        "city",
        "clear",
        "complete",
        "credit",
        "customer",
        "customers",
        "dc",
        "dcs",
        "declined",
        "day",
        "days",
        "dimension",
        "disposition",
        "dispositions",
        "distribution",
        "dropped",
        "eaches",
        "eight",
        "equivalent",
        "equivalents",
        "filter",
        "filters",
        "fifty",
        "five",
        "for",
        "four",
        "from",
        "highest",
        "history",
        "how",
        "in",
        "instead",
        "last",
        "latest",
        "limit",
        "lowest",
        "mechanic",
        "mechanics",
        "month",
        "monthly",
        "months",
        "most",
        "nine",
        "no",
        "north",
        "now",
        "of",
        "one",
        "order",
        "outlet",
        "outlets",
        "overall",
        "per",
        "promotion",
        "promotions",
        "quarter",
        "reason",
        "reasons",
        "region",
        "regions",
        "remove",
        "results",
        "route",
        "routes",
        "same",
        "sales",
        "seven",
        "show",
        "six",
        "smallest",
        "sku",
        "skus",
        "source",
        "sources",
        "status",
        "statuses",
        "store",
        "stores",
        "telematics",
        "ten",
        "the",
        "them",
        "then",
        "three",
        "time",
        "today",
        "top",
        "two",
        "twenty",
        "unit",
        "units",
        "using",
        "vendor",
        "vendors",
        "warehouse",
        "warehouses",
        "week",
        "weeks",
        "what",
        "which",
        "why",
        "with",
        "without",
        "worst",
        "available",
        "full",
        "hundred",
        "year",
    }
)


def _unrecognized_follow_up_words(text: str, service: MetricService) -> tuple[str, ...]:
    """Find substantive words outside the deliberately small follow-up grammar."""

    allowed = set(_FOLLOW_UP_GRAMMAR)
    allowed.update(
        name.casefold()
        for name in (
            *calendar.month_name,
            *calendar.month_abbr,
        )
        if name
    )
    for values in service.filter_options().values():
        for value in values:
            allowed.update(re.findall(r"[a-z0-9]+", value.casefold()))
    unknown = []
    for token in re.findall(r"[a-z0-9]+", text):
        if token in allowed:
            continue
        if re.fullmatch(r"(?:q[1-4]|fy\d{2,4}|\d{1,4}|wh\d{2}|rt\d{4}|out\d{5}|tst\d{5})", token):
            continue
        unknown.append(token)
    return tuple(dict.fromkeys(unknown))


def is_follow_up_candidate(question: str) -> bool:
    """Return whether text is shaped like a context-dependent continuation."""

    text, _ = normalize_business_spelling(question)
    if not text:
        return False
    if _FOLLOW_UP_START.search(text):
        return True
    if _FRAGMENT_START.search(text) and not _detect_metrics(text):
        return True
    words = re.findall(r"\b\w+\b", text)
    return len(words) <= 4 and bool(
        _detect_metrics(text)
        or _detect_dimensions(text)
        or _PERIOD_CUE.search(text)
        or re.search(r"\b(?:cases?|eaches|units?|no filters?|clear filters?)\b", text)
    )


def _filter_patch_from_text(
    text: str,
    service: MetricService,
) -> tuple[FilterPatch | None, str | None]:
    options = service.filter_options()
    parsed, error = _extract_filters(text, options)
    if error:
        return None, error

    updates = {
        name: getattr(parsed, name)
        for name in _FILTER_FIELD_NAMES
        if getattr(parsed, name)
    }
    if re.search(r"\b(?:clear|remove|without|no)\s+(?:all\s+)?filters?\b", text):
        updates = {name: () for name in _FILTER_FIELD_NAMES}
    else:
        clear_patterns = {
            "customer_regions": r"\b(?:all|no) customer regions?\b",
            "warehouse_regions": r"\b(?:all|no) (?:dc|warehouse) regions?\b",
            "warehouse_codes": r"\b(?:all|no) warehouses?\b",
            "route_codes": r"\b(?:all|no) routes?\b",
            "outlet_codes": r"\b(?:all|no) (?:outlets?|stores?|customers?)\b",
            "channels": r"\b(?:all|no) channels?\b",
            "promotion_codes": r"\b(?:all|no) promotions?\b",
            "order_sources": r"\b(?:all|no) (?:order )?sources?\b",
            "cities": r"\b(?:all|no) cities\b",
            "categories": r"\b(?:all|no) categories\b",
        }
        for name, pattern in clear_patterns.items():
            if re.search(pattern, text):
                updates[name] = ()
    return (FilterPatch.from_updates(**updates) if updates else None), None


def build_follow_up_patch(
    question: str,
    service: MetricService,
    previous_intent: QuestionIntent,
) -> IntentPatch:
    """Parse only explicit changes from a clearly signalled follow-up."""

    text, _ = normalize_business_spelling(question)
    unsupported_message = _unsupported_request_message(text)
    if unsupported_message is not None:
        return IntentPatch(
            raw_question=question,
            relation=FollowUpRelation.UNRELATED,
            relation_reason=unsupported_message,
        )
    metrics = _detect_metrics(text)
    if len(metrics) > 1:
        return IntentPatch(
            raw_question=question,
            relation=FollowUpRelation.AMBIGUOUS,
            relation_reason="The follow-up names more than one metric; choose one.",
        )
    metric = metrics[0] if metrics else None
    if metric is None:
        unknown_words = _unrecognized_follow_up_words(text, service)
        if unknown_words:
            return IntentPatch(
                raw_question=question,
                relation=FollowUpRelation.UNRELATED,
                relation_reason=(
                    "The follow-up contains an unsupported topic or change "
                    f"({', '.join(unknown_words)}); no metric query was run."
                ),
            )

    period = None
    if _PERIOD_CUE.search(text):
        minimum, maximum = _available_date_range(
            service, metric or previous_intent.metric
        )
        period, period_error = _resolve_period(text, minimum, maximum)
        if period_error or period is None:
            return IntentPatch(
                raw_question=question,
                relation=FollowUpRelation.AMBIGUOUS,
                relation_reason=period_error or "The follow-up period is unclear.",
            )

    filter_patch, filter_error = _filter_patch_from_text(text, service)
    if filter_error:
        return IntentPatch(
            raw_question=question,
            relation=FollowUpRelation.AMBIGUOUS,
            relation_reason=filter_error,
        )

    detected_dimensions = _detect_dimensions(text, metric or previous_intent.metric)
    dimensions: tuple[DimensionKey, ...] | None = detected_dimensions or None
    if re.search(r"\b(?:overall|without (?:a )?breakdown|no breakdown)\b", text):
        dimensions = ()
    leading_return_reason = None
    effective_metric = metric or previous_intent.metric
    effective_dimensions = (
        dimensions if dimensions is not None else previous_intent.dimensions
    )
    if (
        (metric is not None or dimensions is not None)
        and (
            previous_intent.leading_return_reason
            or (
                effective_metric == MetricKey.RETURNS
                and set(effective_dimensions)
                == {DimensionKey.CATEGORY, DimensionKey.RETURN_REASON}
            )
        )
    ):
        leading_return_reason = _requests_leading_return_reason(
            text,
            effective_metric,
            effective_dimensions,
        )

    quantity_basis = None
    if re.search(r"\b(?:eaches|units?|cases?|case equivalents?)\b", text):
        quantity_basis, basis_error = _extract_quantity_basis(
            text, metric or previous_intent.metric
        )
        if basis_error:
            return IntentPatch(
                raw_question=question,
                relation=FollowUpRelation.AMBIGUOUS,
                relation_reason=basis_error,
            )

    ranking = (
        _extract_ranking(text, metric or previous_intent.metric)
        if _RANKING_CUE.search(text)
        else None
    )

    clear_limit = bool(re.search(r"\b(?:show all|all results|no limit)\b", text))
    limit = (
        _extract_limit(text, metric or previous_intent.metric)
        if _LIMIT_CUE.search(text)
        else None
    )
    explain_change = (
        True if re.search(r"\b(?:why|dropped|changed|declined)\b", text) else None
    )
    return IntentPatch(
        raw_question=question,
        metric=metric,
        dimensions=dimensions,
        period=period,
        filters=filter_patch,
        quantity_basis=quantity_basis,
        ranking=ranking,
        limit=limit,
        clear_limit=clear_limit,
        explain_change=explain_change,
        leading_return_reason=leading_return_reason,
    )


@dataclass(frozen=True)
class FilterPatch:
    """A field-aware update to ``IntentFilters``.

    ``IntentFilters`` uses empty tuples as valid values, so a separate set of
    explicit fields is required to distinguish "not mentioned" from "clear
    this filter".  Values in fields not named as explicit must remain empty;
    this prevents a caller typo from silently discarding an intended update.
    """

    values: IntentFilters
    explicit_fields: frozenset[str]

    def __post_init__(self) -> None:
        explicit_fields = frozenset(self.explicit_fields)
        object.__setattr__(self, "explicit_fields", explicit_fields)

        unknown = explicit_fields - _FILTER_FIELD_SET
        if unknown:
            names = ", ".join(sorted(unknown))
            raise ValueError(f"Unknown intent filter field(s): {names}.")
        if not explicit_fields:
            raise ValueError("A filter patch must name at least one explicit field.")

        for name in _FILTER_FIELD_NAMES:
            value = getattr(self.values, name)
            if not isinstance(value, tuple):
                raise TypeError(f"Intent filter '{name}' must be a tuple.")
            if any(not isinstance(item, str) or not item.strip() for item in value):
                raise ValueError(f"Intent filter '{name}' contains an invalid value.")
            if name not in explicit_fields and value:
                raise ValueError(
                    f"Intent filter '{name}' has a value but is not marked explicit."
                )

    @classmethod
    def from_updates(cls, **updates: tuple[str, ...]) -> FilterPatch:
        """Build a patch from named ``IntentFilters`` fields.

        Passing an empty tuple is an explicit request to clear only that filter.
        For example, ``FilterPatch.from_updates(customer_regions=())`` clears
        customer-region filtering while preserving all other session filters.
        """

        unknown = set(updates) - _FILTER_FIELD_SET
        if unknown:
            names = ", ".join(sorted(unknown))
            raise ValueError(f"Unknown intent filter field(s): {names}.")
        if not updates:
            raise ValueError("A filter patch must contain at least one update.")
        normalized: dict[str, tuple[str, ...]] = {}
        for name, value in updates.items():
            if not isinstance(value, tuple):
                raise TypeError(f"Intent filter '{name}' must be a tuple.")
            normalized[name] = value
        return cls(
            values=IntentFilters(**normalized),
            explicit_fields=frozenset(normalized),
        )

    def apply(self, current: IntentFilters) -> IntentFilters:
        """Return a new filter value without mutating the stored context."""

        updates = {name: getattr(self.values, name) for name in self.explicit_fields}
        return replace(current, **updates)


@dataclass(frozen=True)
class IntentPatch:
    """Allow-listed fields explicitly extracted from one short follow-up.

    ``None`` means a field was not mentioned and should be inherited.  Empty
    dimensions are meaningful (overall rather than grouped), and ``clear_limit``
    distinguishes "show all" from an omitted result limit.
    """

    raw_question: str
    relation: FollowUpRelation = FollowUpRelation.RELATED
    relation_reason: str | None = None
    metric: MetricKey | None = None
    dimensions: tuple[DimensionKey, ...] | None = None
    period: Period | None = None
    filters: FilterPatch | None = None
    quantity_basis: QuantityBasis | None = None
    ranking: Ranking | None = None
    limit: int | None = None
    clear_limit: bool = False
    late_threshold_minutes: int | None = None
    rate_threshold: float | None = None
    explain_change: bool | None = None
    leading_return_reason: bool | None = None

    def __post_init__(self) -> None:
        if not self.raw_question.strip():
            raise ValueError("A follow-up question cannot be empty.")
        if self.period is not None and self.period.start > self.period.end:
            raise ValueError("A follow-up period cannot end before it starts.")
        if self.dimensions is not None and len(set(self.dimensions)) != len(self.dimensions):
            raise ValueError("A follow-up cannot repeat a dimension.")
        if self.limit is not None and self.limit <= 0:
            raise ValueError("A follow-up result limit must be positive.")
        if self.clear_limit and self.limit is not None:
            raise ValueError("A follow-up cannot set and clear its result limit.")
        if self.late_threshold_minutes is not None and self.late_threshold_minutes <= 0:
            raise ValueError("The late threshold must be positive.")
        if self.rate_threshold is not None and not 0 <= self.rate_threshold <= 1:
            raise ValueError("The rate threshold must be between zero and one.")

    @property
    def is_empty(self) -> bool:
        """Whether the follow-up carries no explicit, allow-listed intent change."""

        return not self.explicit_fields

    @property
    def explicit_fields(self) -> tuple[str, ...]:
        """Stable provenance labels for fields supplied by the follow-up."""

        explicit: list[str] = []
        for name in ("metric", "dimensions", "period"):
            if getattr(self, name) is not None:
                explicit.append(name)
        if self.filters is not None:
            explicit.extend(
                f"filters.{name}"
                for name in _FILTER_FIELD_NAMES
                if name in self.filters.explicit_fields
            )
        for name in (
            "quantity_basis",
            "ranking",
            "late_threshold_minutes",
            "rate_threshold",
            "explain_change",
            "leading_return_reason",
        ):
            if getattr(self, name) is not None:
                explicit.append(name)
        if self.limit is not None or self.clear_limit:
            explicit.append("limit")
        return tuple(explicit)


@dataclass(frozen=True)
class ConversationResolution:
    """Pure result of resolving a short follow-up against session context."""

    status: ConversationStatus
    intent: QuestionIntent | None
    message: str
    explicit_fields: tuple[str, ...] = ()
    inherited_fields: tuple[str, ...] = ()

    @property
    def used_context(self) -> bool:
        return self.status == ConversationStatus.READY and bool(self.inherited_fields)


class ConversationMemory:
    """Hold the last validated intent for exactly one caller-owned session."""

    def __init__(self, initial_intent: QuestionIntent | None = None) -> None:
        self._last_intent: QuestionIntent | None = None
        if initial_intent is not None:
            self.remember_validated(initial_intent)

    @property
    def has_context(self) -> bool:
        return self._last_intent is not None

    @property
    def last_intent(self) -> QuestionIntent | None:
        return self._last_intent

    def remember_validated(self, intent: QuestionIntent) -> None:
        """Commit an intent only after the caller has validated it successfully."""

        _check_intent_invariants(intent)
        self._last_intent = intent

    def clear(self) -> None:
        """Start a new conversation without affecting any other session."""

        self._last_intent = None

    def resolve(self, patch: IntentPatch) -> ConversationResolution:
        """Safely merge a typed follow-up without changing stored context."""

        if patch.relation == FollowUpRelation.AMBIGUOUS:
            return ConversationResolution(
                status=ConversationStatus.AMBIGUOUS,
                intent=None,
                message=patch.relation_reason
                or "The follow-up could refer to more than one interpretation.",
                explicit_fields=patch.explicit_fields,
            )
        if patch.relation == FollowUpRelation.UNRELATED:
            return ConversationResolution(
                status=ConversationStatus.UNRELATED,
                intent=None,
                message=patch.relation_reason
                or "The question is not a safe follow-up to the current analysis.",
                explicit_fields=patch.explicit_fields,
            )
        if patch.is_empty:
            return ConversationResolution(
                status=ConversationStatus.UNRELATED,
                intent=None,
                message="The follow-up contains no supported change to the current analysis.",
            )
        if self._last_intent is None:
            return ConversationResolution(
                status=ConversationStatus.NO_CONTEXT,
                intent=None,
                message="There is no earlier validated question to continue.",
                explicit_fields=patch.explicit_fields,
            )

        current = self._last_intent
        explicit = patch.explicit_fields
        inherited = _inherited_fields(current, patch)
        resolved = replace(
            current,
            raw_question=patch.raw_question.strip(),
            metric=patch.metric if patch.metric is not None else current.metric,
            dimensions=(
                patch.dimensions if patch.dimensions is not None else current.dimensions
            ),
            period=patch.period if patch.period is not None else current.period,
            filters=(
                patch.filters.apply(current.filters)
                if patch.filters is not None
                else current.filters
            ),
            quantity_basis=(
                patch.quantity_basis
                if patch.quantity_basis is not None
                else current.quantity_basis
            ),
            ranking=patch.ranking if patch.ranking is not None else current.ranking,
            limit=(
                None
                if patch.clear_limit
                else patch.limit
                if patch.limit is not None
                else current.limit
            ),
            late_threshold_minutes=(
                patch.late_threshold_minutes
                if patch.late_threshold_minutes is not None
                else current.late_threshold_minutes
            ),
            rate_threshold=(
                patch.rate_threshold
                if patch.rate_threshold is not None
                else current.rate_threshold
            ),
            explain_change=(
                patch.explain_change
                if patch.explain_change is not None
                else current.explain_change
            ),
            leading_return_reason=(
                patch.leading_return_reason
                if patch.leading_return_reason is not None
                else current.leading_return_reason
            ),
        )
        _check_intent_invariants(resolved)
        return ConversationResolution(
            status=ConversationStatus.READY,
            intent=resolved,
            message="Follow-up merged with the last validated question.",
            explicit_fields=explicit,
            inherited_fields=inherited,
        )


def _inherited_fields(current: QuestionIntent, patch: IntentPatch) -> tuple[str, ...]:
    inherited: list[str] = []
    for name in ("metric", "dimensions", "period"):
        if getattr(patch, name) is None:
            inherited.append(name)

    if patch.filters is None:
        inherited.append("filters")
    else:
        inherited.extend(
            f"filters.{name}"
            for name in _FILTER_FIELD_NAMES
            if name not in patch.filters.explicit_fields and getattr(current.filters, name)
        )

    for name in (
        "quantity_basis",
        "ranking",
        "late_threshold_minutes",
        "rate_threshold",
        "explain_change",
        "leading_return_reason",
    ):
        if getattr(patch, name) is None:
            inherited.append(name)
    if patch.limit is None and not patch.clear_limit:
        inherited.append("limit")
    return tuple(inherited)


def _check_intent_invariants(intent: QuestionIntent) -> None:
    """Check structural invariants without claiming metric-policy validation."""

    if not isinstance(intent, QuestionIntent):
        raise TypeError("Conversation context must be a QuestionIntent.")
    if not intent.raw_question.strip():
        raise ValueError("A validated question cannot be empty.")
    if intent.period.start > intent.period.end:
        raise ValueError("A validated question period cannot end before it starts.")
    if len(set(intent.dimensions)) != len(intent.dimensions):
        raise ValueError("A validated question cannot repeat a dimension.")
    if intent.limit is not None and intent.limit <= 0:
        raise ValueError("A validated question result limit must be positive.")
    if intent.late_threshold_minutes <= 0:
        raise ValueError("A validated late threshold must be positive.")
    if not 0 <= intent.rate_threshold <= 1:
        raise ValueError("A validated rate threshold must be between zero and one.")
    if intent.leading_return_reason and not (
        intent.metric == MetricKey.RETURNS
        and len(intent.dimensions) == 2
        and set(intent.dimensions) == {DimensionKey.CATEGORY, DimensionKey.RETURN_REASON}
    ):
        raise ValueError(
            "Leading return-reason selection requires returns grouped by category and reason."
        )


__all__ = [
    "build_follow_up_patch",
    "ConversationMemory",
    "ConversationResolution",
    "ConversationStatus",
    "FilterPatch",
    "FollowUpRelation",
    "IntentPatch",
    "is_follow_up_candidate",
]
