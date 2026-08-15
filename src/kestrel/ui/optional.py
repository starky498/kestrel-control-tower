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
    settings: Settings | None = None,
    conversation_memory: Any = None,
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
                settings=settings,
                conversation_memory=conversation_memory,
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


def create_nlq_memory() -> CapabilityResult:
    """Create one optional conversation-memory object for a UI session."""

    try:
        module = importlib.import_module("kestrel.nlq")
        memory_class = module.ConversationMemory
        memory = memory_class()
    except (AttributeError, ImportError, TypeError) as error:
        return CapabilityResult(False, f"Follow-up memory is unavailable: {error}")
    return CapabilityResult(True, "Session follow-up memory ready.", memory)


def nlq_model_status(settings: Settings) -> CapabilityResult:
    """Inspect the optional local model without importing its inference runtime."""

    if not settings.nlq_semantic_enabled:
        return CapabilityResult(False, "Local semantic matching is disabled in configuration.")
    try:
        module = importlib.import_module("kestrel.nlq.model_install")
        metadata = module.installed_model_metadata(settings.nlq_model_path)
    except (AttributeError, ImportError, OSError, TypeError, ValueError) as error:
        return CapabilityResult(False, f"Local model status is unavailable: {error}")
    if metadata is None:
        return CapabilityResult(
            False,
            "The optional local language model is not installed; exact rules remain active.",
        )
    return CapabilityResult(True, "Verified local language model ready.", metadata)


def install_nlq_model(settings: Settings) -> CapabilityResult:
    """Download the checksum-pinned public model into the configured local path."""

    try:
        module = importlib.import_module("kestrel.nlq.model_install")
        metadata = module.install_model(target=settings.nlq_model_path)
    except Exception as error:  # Network and filesystem failures must not break the app.
        return CapabilityResult(False, f"Local model installation failed: {error}")
    return CapabilityResult(True, "Verified local language model installed.", metadata)
