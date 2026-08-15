from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from kestrel.nlq.semantic import (
    EmbeddingSemanticResolver,
    IntentCatalog,
    IntentDefinition,
    ModelProvenance,
    OnnxMiniLMBackend,
    SemanticBackendUnavailable,
    SemanticStatus,
    build_local_semantic_resolver,
    load_intent_catalog,
)


class FakeEmbeddingBackend:
    """Small deterministic vocabulary embedding; no model or network is required."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    @property
    def provenance(self) -> ModelProvenance:
        return ModelProvenance("fake-vocabulary", "test", "python")

    def encode(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        self.calls.append(tuple(texts))
        vectors: list[list[float]] = []
        for text in texts:
            lowered = text.casefold()
            vectors.append(
                [
                    float(any(word in lowered for word in ("supply", "fill", "ordered"))),
                    float(any(word in lowered for word in ("freight", "transport", "case"))),
                    float(any(word in lowered for word in ("cold", "temperature", "chilled"))),
                ]
            )
        return vectors


class BrokenBackend(FakeEmbeddingBackend):
    def encode(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        raise RuntimeError("runtime missing")


def _catalog(tmp_path: Path) -> IntentCatalog:
    return IntentCatalog(
        version="test",
        source=tmp_path / "catalog.yml",
        intents=(
            IntentDefinition("fill_rate", "Fill rate", "active", ("supply ordered quantity",)),
            IntentDefinition(
                "freight_per_case", "Freight per case", "active", ("transport cost per case",)
            ),
            IntentDefinition(
                "chilled_excursions", "Cold chain", "active", ("chilled temperature issue",)
            ),
        ),
    )


def test_resolves_allowlisted_intent_with_provenance_and_runner_up(tmp_path: Path) -> None:
    backend = FakeEmbeddingBackend()
    resolver = EmbeddingSemanticResolver(
        backend,
        _catalog(tmp_path),
        min_confidence=0.7,
        min_margin=0.2,
    )

    result = resolver.resolve("Which depots have expensive transport cost per case?")

    assert result.status is SemanticStatus.READY
    assert result.intent_key == "freight_per_case"
    assert result.confidence == pytest.approx(1.0)
    assert result.runner_up_intent_key in {"chilled_excursions", "fill_rate"}
    assert result.margin == pytest.approx(1.0)
    assert result.matched_example == "transport cost per case"
    assert result.provenance.label == "fake-vocabulary@test (python)"


def test_allowed_intents_prevent_planned_or_unimplemented_dispatch(tmp_path: Path) -> None:
    resolver = EmbeddingSemanticResolver(
        FakeEmbeddingBackend(), _catalog(tmp_path), min_confidence=0.7, min_margin=0.2
    )

    result = resolver.resolve(
        "Which depots have expensive transport cost per case?",
        allowed_intents={"fill_rate", "chilled_excursions"},
    )

    assert result.status is SemanticStatus.UNSUPPORTED
    assert result.intent_key is None


def test_close_candidates_are_ambiguous_and_never_guessed(tmp_path: Path) -> None:
    resolver = EmbeddingSemanticResolver(
        FakeEmbeddingBackend(), _catalog(tmp_path), min_confidence=0.5, min_margin=0.25
    )

    result = resolver.resolve("Compare supply cases with transport performance")

    assert result.status is SemanticStatus.AMBIGUOUS
    assert result.intent_key is None
    assert result.confidence > 0.5
    assert result.margin < 0.25
    assert result.runner_up_intent_key is not None


def test_backend_failure_is_reported_as_unavailable(tmp_path: Path) -> None:
    resolver = EmbeddingSemanticResolver(BrokenBackend(), _catalog(tmp_path))

    result = resolver.resolve("Where is supply below ordered quantity?")

    assert result.status is SemanticStatus.UNAVAILABLE
    assert result.intent_key is None
    assert "runtime missing" in result.message


def test_sql_shaped_input_is_rejected_without_calling_backend(tmp_path: Path) -> None:
    backend = FakeEmbeddingBackend()
    resolver = EmbeddingSemanticResolver(backend, _catalog(tmp_path))

    result = resolver.resolve("SELECT * FROM order_lines")

    assert result.status is SemanticStatus.UNSUPPORTED
    assert result.intent_key is None
    assert "SQL-shaped" in result.message
    assert backend.calls == []


def test_example_embeddings_are_cached_between_questions(tmp_path: Path) -> None:
    backend = FakeEmbeddingBackend()
    resolver = EmbeddingSemanticResolver(backend, _catalog(tmp_path))

    resolver.resolve("Where is supply below ordered quantity?")
    resolver.resolve("Which route has expensive freight per case?")

    assert len(backend.calls) == 3
    assert len(backend.calls[0]) == 3
    assert len(backend.calls[1]) == 1
    assert len(backend.calls[2]) == 1


def test_missing_local_model_fails_closed_without_import_or_download(tmp_path: Path) -> None:
    backend = OnnxMiniLMBackend(tmp_path / "missing-model")
    resolver = EmbeddingSemanticResolver(backend, _catalog(tmp_path))

    result = resolver.resolve("Where is supply below ordered quantity?")

    assert result.status is SemanticStatus.UNAVAILABLE
    assert result.intent_key is None
    assert "does not exist" in result.message


def test_resolver_builder_rejects_marker_without_verified_model_files(tmp_path: Path) -> None:
    model_path = tmp_path / "model"
    model_path.mkdir()
    (model_path / "kestrel-model.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(SemanticBackendUnavailable, match="checksum validation"):
        build_local_semantic_resolver(model_path)


def test_repository_catalog_covers_all_active_governed_metrics() -> None:
    catalog = load_intent_catalog()

    active = {intent.key for intent in catalog.intents if intent.availability == "active"}
    planned = {intent.key for intent in catalog.intents if intent.availability == "planned"}
    assert active == {
        "allocation_rate",
        "backlog",
        "chilled_excursions",
        "competitor_coverage",
        "credit_note_leakage",
        "delivery_failures",
        "delivery_on_time_rate",
        "discontinued_skus",
        "fill_rate",
        "freight_per_case",
        "holiday_association",
        "inventory_risk",
        "late_routes",
        "market_price_gap",
        "on_time_rate",
        "pod_coverage",
        "post_allocation_fulfilment",
        "returns",
        "short_delivery_exposure",
        "strict_otif",
        "weather_association",
    }
    assert planned == set()
    assert all(len(intent.examples) >= 4 for intent in catalog.intents)
