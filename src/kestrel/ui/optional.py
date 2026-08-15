"""Feature-detected adapters for independently delivered integrations."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any

from kestrel.config import Settings
from kestrel.metrics.definitions import MetricDefinition
from kestrel.metrics.service import AnalyticsService, FilterSet, QuantityBasis


@dataclass(frozen=True, slots=True)
class CapabilityResult:
    available: bool
    message: str
    payload: Any = None


def external_analytics(settings: Settings) -> CapabilityResult:
    """Construct the optional governed external-metrics boundary when installed."""

    try:
        module = importlib.import_module("kestrel.metrics.external")
        service_class = module.ExternalAnalyticsService
        service = service_class(settings.analytics_db)
    except (AttributeError, ImportError, OSError, TypeError, ValueError) as error:
        return CapabilityResult(
            False,
            f"The governed external-metrics service is unavailable: {error}",
        )
    return CapabilityResult(True, "Governed external-metrics service loaded.", service)


def call_external(
    service: Any,
    method_name: str,
    *args: Any,
    **kwargs: Any,
) -> CapabilityResult:
    """Invoke one allow-listed presentation method without risking the core dashboard."""

    method = getattr(service, method_name, None)
    if not callable(method):
        return CapabilityResult(
            False,
            f"The external-metrics service does not expose {method_name}.",
        )
    try:
        payload = method(*args, **kwargs)
    except Exception as error:  # Optional source failures must not hide core metrics.
        return CapabilityResult(False, f"External metric temporarily unavailable: {error}")
    return CapabilityResult(True, "External metric loaded.", payload)


def ask_kestrel(
    question: str,
    *,
    service: AnalyticsService,
    filters: FilterSet,
    basis: QuantityBasis,
    definitions: dict[str, MetricDefinition],
) -> CapabilityResult:
    """Call the optional governed NLQ router through a narrow, explicit contract."""

    try:
        module = importlib.import_module("kestrel.nlq")
    except ImportError:
        return CapabilityResult(
            False,
            "The governed question router is not installed in this build. No unrestricted "
            "text-to-SQL fallback was used.",
        )

    legacy_answer = getattr(module, "answer_question", None)
    try:
        if callable(legacy_answer):
            payload = legacy_answer(
                question=question,
                service=service,
                filters=filters,
                basis=basis,
                definitions=definitions,
            )
        else:
            router_class = getattr(module, "QuestionRouter", None)
            if not callable(router_class):
                return CapabilityResult(
                    False,
                    "The NLQ package is present but exposes no governed question router.",
                )
            payload = router_class(service).ask(question)
    except Exception as error:  # Keep optional capability failures isolated.
        return CapabilityResult(False, f"Ask Kestrel could not answer this question: {error}")
    return CapabilityResult(True, "Governed answer returned.", payload)
