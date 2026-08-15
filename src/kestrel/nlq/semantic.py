"""Optional local semantic intent matching for Ask Kestrel.

The semantic layer has one deliberately narrow responsibility: map a natural-language
question to an allow-listed intent key.  It does not create SQL, run queries, extract
filters, or calculate metrics.  Callers must validate the returned key and pass it to the
existing governed router before any analysis is executed.

The embedding backend is injected so the resolver is deterministic and network-free in
tests.  :class:`OnnxMiniLMBackend` is lazy and local-only: a missing model or optional
runtime results in ``UNAVAILABLE`` rather than a download or a guessed intent.
"""

from __future__ import annotations

import importlib
import math
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from threading import Lock
from typing import Any, Protocol, cast

import yaml

from kestrel.config import project_config_path
from kestrel.nlq.model_install import installed_model_metadata


class SemanticStatus(StrEnum):
    """Outcome of semantic intent resolution."""

    READY = "ready"
    AMBIGUOUS = "ambiguous"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ModelProvenance:
    """Identity of the embedding implementation used for an interpretation."""

    model_id: str
    revision: str
    runtime: str

    @property
    def label(self) -> str:
        return f"{self.model_id}@{self.revision} ({self.runtime})"


@dataclass(frozen=True)
class SemanticResolution:
    """Auditable result returned by every semantic resolver.

    ``confidence`` is a cosine-similarity score in the range 0..1, not a calibrated
    probability. ``margin`` is the difference from the best runner-up intent.
    """

    status: SemanticStatus
    intent_key: str | None
    confidence: float
    runner_up_intent_key: str | None
    runner_up_confidence: float
    margin: float
    matched_example: str | None
    runner_up_example: str | None
    provenance: ModelProvenance
    message: str

    @property
    def is_ready(self) -> bool:
        return self.status is SemanticStatus.READY and self.intent_key is not None


class SemanticIntentResolver(Protocol):
    """Injectable boundary used by the governed question router."""

    def resolve(
        self,
        question: str,
        *,
        allowed_intents: Collection[str] | None = None,
    ) -> SemanticResolution: ...


class EmbeddingBackend(Protocol):
    """Minimal embedding interface; implementations may remain fully local."""

    @property
    def provenance(self) -> ModelProvenance: ...

    def encode(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...


class SemanticBackendUnavailable(RuntimeError):
    """Raised internally when the optional local embedding backend cannot run."""


@dataclass(frozen=True)
class IntentDefinition:
    key: str
    label: str
    availability: str
    examples: tuple[str, ...]


@dataclass(frozen=True)
class IntentCatalog:
    version: str
    source: Path
    intents: tuple[IntentDefinition, ...]

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(intent.key for intent in self.intents)


def default_catalog_path() -> Path:
    """Return the project location of the governed intent catalogue."""

    return project_config_path("nlq_intents.yml")


def _required_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def load_intent_catalog(path: str | Path | None = None) -> IntentCatalog:
    """Load and validate the curated semantic catalogue.

    The catalogue is data, not an instruction source.  It can only provide intent keys and
    natural-language examples; no query text is accepted or returned.
    """

    source = Path(path) if path is not None else default_catalog_path()
    with source.open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)

    if not isinstance(payload, Mapping):
        raise ValueError("Semantic intent catalogue must be a mapping")
    version = _required_text(payload.get("version"), field="version")
    raw_intents = payload.get("intents")
    if not isinstance(raw_intents, Mapping) or not raw_intents:
        raise ValueError("Semantic intent catalogue must define at least one intent")

    definitions: list[IntentDefinition] = []
    seen_examples: dict[str, str] = {}
    for raw_key, raw_definition in raw_intents.items():
        key = _required_text(raw_key, field="intent key")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", key):
            raise ValueError(f"Invalid intent key: {key!r}")
        if not isinstance(raw_definition, Mapping):
            raise ValueError(f"Intent {key!r} must be a mapping")
        label = _required_text(raw_definition.get("label"), field=f"{key}.label")
        availability = _required_text(
            raw_definition.get("availability"), field=f"{key}.availability"
        )
        if availability not in {"active", "planned"}:
            raise ValueError(f"{key}.availability must be 'active' or 'planned'")
        raw_examples = raw_definition.get("examples")
        if not isinstance(raw_examples, list) or not raw_examples:
            raise ValueError(f"{key}.examples must be a non-empty list")
        examples = tuple(
            _required_text(example, field=f"{key}.examples") for example in raw_examples
        )
        for example in examples:
            normalized = _normalize_text(example)
            existing = seen_examples.get(normalized)
            if existing is not None:
                raise ValueError(
                    f"Duplicate semantic example across {existing!r} and {key!r}: {example!r}"
                )
            seen_examples[normalized] = key
        definitions.append(IntentDefinition(key, label, availability, examples))

    return IntentCatalog(version, source.resolve(), tuple(definitions))


def _normalize_text(text: str) -> str:
    return " ".join(text.casefold().split())


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("Embedding vectors must have the same non-zero dimension")
    left_values = tuple(float(value) for value in left)
    right_values = tuple(float(value) for value in right)
    if not all(math.isfinite(value) for value in (*left_values, *right_values)):
        raise ValueError("Embedding vectors must contain only finite values")
    numerator = math.fsum(a * b for a, b in zip(left_values, right_values, strict=True))
    left_norm = math.sqrt(math.fsum(value * value for value in left_values))
    right_norm = math.sqrt(math.fsum(value * value for value in right_values))
    if left_norm == 0.0 or right_norm == 0.0:
        raise ValueError("Embedding vectors must not be zero vectors")
    return max(0.0, min(1.0, numerator / (left_norm * right_norm)))


_SQL_SHAPED = re.compile(
    r"\b(?:select\s+.+\s+from|insert\s+into|update\s+.+\s+set|"
    r"delete\s+from|drop\s+(?:table|database)|alter\s+table|create\s+table)\b",
    re.IGNORECASE | re.DOTALL,
)


class EmbeddingSemanticResolver:
    """Resolve questions by nearest curated example with fail-closed thresholds."""

    def __init__(
        self,
        backend: EmbeddingBackend,
        catalog: IntentCatalog,
        *,
        min_confidence: float = 0.50,
        min_margin: float = 0.08,
    ) -> None:
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError("min_confidence must be between 0 and 1")
        if not 0.0 <= min_margin <= 1.0:
            raise ValueError("min_margin must be between 0 and 1")
        self._backend = backend
        self._catalog = catalog
        self._min_confidence = min_confidence
        self._min_margin = min_margin
        self._example_vectors: tuple[tuple[float, ...], ...] | None = None
        self._flat_examples: tuple[tuple[str, str], ...] | None = None
        self._cache_lock = Lock()

    @property
    def catalog(self) -> IntentCatalog:
        return self._catalog

    def _unavailable(self, message: str) -> SemanticResolution:
        return SemanticResolution(
            SemanticStatus.UNAVAILABLE,
            None,
            0.0,
            None,
            0.0,
            0.0,
            None,
            None,
            self._backend.provenance,
            message,
        )

    def _ensure_example_vectors(self) -> None:
        if self._example_vectors is not None:
            return
        with self._cache_lock:
            if self._example_vectors is not None:
                return
            flat_examples = tuple(
                (intent.key, example)
                for intent in self._catalog.intents
                for example in intent.examples
            )
            vectors = self._backend.encode([example for _, example in flat_examples])
            if len(vectors) != len(flat_examples):
                raise ValueError("Embedding backend returned an unexpected number of vectors")
            converted = tuple(tuple(float(value) for value in vector) for vector in vectors)
            if not converted:
                raise ValueError("Semantic catalogue produced no example embeddings")
            dimension = len(converted[0])
            if dimension == 0 or any(len(vector) != dimension for vector in converted):
                raise ValueError("Embedding backend returned inconsistent vector dimensions")
            self._flat_examples = flat_examples
            self._example_vectors = converted

    def resolve(
        self,
        question: str,
        *,
        allowed_intents: Collection[str] | None = None,
    ) -> SemanticResolution:
        normalized = _normalize_text(question)
        if not normalized:
            return SemanticResolution(
                SemanticStatus.UNSUPPORTED,
                None,
                0.0,
                None,
                0.0,
                0.0,
                None,
                None,
                self._backend.provenance,
                "Ask a non-empty business question.",
            )
        if _SQL_SHAPED.search(question):
            return SemanticResolution(
                SemanticStatus.UNSUPPORTED,
                None,
                0.0,
                None,
                0.0,
                0.0,
                None,
                None,
                self._backend.provenance,
                "SQL-shaped input is not accepted; ask a supported business question.",
            )

        known_keys = set(self._catalog.keys)
        permitted = (
            known_keys if allowed_intents is None else known_keys.intersection(allowed_intents)
        )
        if not permitted:
            return SemanticResolution(
                SemanticStatus.UNSUPPORTED,
                None,
                0.0,
                None,
                0.0,
                0.0,
                None,
                None,
                self._backend.provenance,
                "No allow-listed semantic intents are available.",
            )

        try:
            self._ensure_example_vectors()
            question_vectors = self._backend.encode([question])
            if len(question_vectors) != 1:
                raise ValueError("Embedding backend did not return one question vector")
            question_vector = question_vectors[0]
            assert self._flat_examples is not None
            assert self._example_vectors is not None
            best_by_intent: dict[str, tuple[float, str]] = {}
            for (candidate_key, example), example_vector in zip(
                self._flat_examples, self._example_vectors, strict=True
            ):
                if candidate_key not in permitted:
                    continue
                score = _cosine_similarity(question_vector, example_vector)
                previous = best_by_intent.get(candidate_key)
                if previous is None or score > previous[0]:
                    best_by_intent[candidate_key] = (score, example)
        except Exception as exc:  # optional local runtime must always fail closed
            return self._unavailable(f"Local semantic resolver unavailable: {exc}")

        ranked = sorted(
            best_by_intent.items(),
            key=lambda item: (-item[1][0], item[0]),
        )
        best_key, (best_score, best_example) = ranked[0]
        if len(ranked) > 1:
            runner_key, (runner_score, runner_example) = ranked[1]
            margin = max(0.0, best_score - runner_score)
        else:
            runner_key, runner_score, runner_example = None, 0.0, None
            margin = best_score

        intent_key: str | None
        if best_score < self._min_confidence:
            status = SemanticStatus.UNSUPPORTED
            intent_key = None
            message = (
                "No supported intent met the semantic confidence threshold; "
                "the governed router should ask for a clearer question."
            )
        elif margin < self._min_margin:
            status = SemanticStatus.AMBIGUOUS
            intent_key = None
            message = (
                f"The question is too close to both {best_key!r} and {runner_key!r}; "
                "clarification is required."
            )
        else:
            status = SemanticStatus.READY
            intent_key = best_key
            message = f"Matched allow-listed intent {best_key!r} using a curated example."

        return SemanticResolution(
            status,
            intent_key,
            best_score,
            runner_key,
            runner_score,
            margin,
            best_example,
            runner_example,
            self._backend.provenance,
            message,
        )


class OnnxMiniLMBackend:
    """Lazy, local-only MiniLM backend using ONNX Runtime and Tokenizers directly.

    Avoiding the full PyTorch/Sentence Transformers stack keeps the clone lightweight. The
    constructor performs no import, model load, download, or network access; ``encode`` only
    opens files already present below ``model_path``.
    """

    def __init__(
        self,
        model_path: str | Path,
        *,
        model_id: str = "sentence-transformers/all-MiniLM-L6-v2",
        revision: str = "local",
        onnx_file: str = "onnx/model.onnx",
        max_length: int = 256,
    ) -> None:
        if max_length < 8:
            raise ValueError("max_length must be at least 8 tokens")
        self._model_path = Path(model_path)
        self._onnx_file = onnx_file
        self._max_length = max_length
        self._provenance = ModelProvenance(model_id, revision, "onnxruntime/tokenizers-cpu")
        self._model: Any | None = None
        self._load_lock = Lock()

    @property
    def provenance(self) -> ModelProvenance:
        return self._provenance

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        with self._load_lock:
            if self._model is not None:
                return self._model
            if not self._model_path.exists():
                raise SemanticBackendUnavailable(
                    f"local model path does not exist: {self._model_path}"
                )
            model_file = self._model_path / self._onnx_file
            tokenizer_file = self._model_path / "tokenizer.json"
            missing = [str(path) for path in (model_file, tokenizer_file) if not path.is_file()]
            if missing:
                raise SemanticBackendUnavailable(
                    "local model is incomplete; missing " + ", ".join(missing)
                )
            try:
                import onnxruntime as ort
                from tokenizers import Tokenizer
            except ImportError as exc:
                raise SemanticBackendUnavailable(
                    "optional onnxruntime/tokenizers dependencies are not installed"
                ) from exc
            try:
                tokenizer = Tokenizer.from_file(str(tokenizer_file))
                tokenizer.enable_truncation(max_length=self._max_length)
                tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
                session = ort.InferenceSession(
                    str(model_file),
                    providers=["CPUExecutionProvider"],
                )
            except Exception as exc:
                raise SemanticBackendUnavailable(f"could not load local ONNX model: {exc}") from exc
            self._model = (session, tokenizer)
            return self._model

    def encode(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        if not texts:
            return ()
        session, tokenizer = self._load()
        try:
            np = importlib.import_module("numpy")
            encoded = tokenizer.encode_batch(list(texts))
            input_ids = np.asarray([item.ids for item in encoded], dtype=np.int64)
            attention_mask = np.asarray(
                [item.attention_mask for item in encoded], dtype=np.int64
            )
            token_type_ids = np.asarray(
                [item.type_ids for item in encoded], dtype=np.int64
            )
            available = {item.name for item in session.get_inputs()}
            feeds: dict[str, Any] = {}
            for name, values in (
                ("input_ids", input_ids),
                ("attention_mask", attention_mask),
                ("token_type_ids", token_type_ids),
            ):
                if name in available:
                    feeds[name] = values
            if "input_ids" not in feeds or "attention_mask" not in feeds:
                raise ValueError(f"unexpected ONNX input names: {sorted(available)}")

            outputs = session.run(None, feeds)
            output_names = [item.name for item in session.get_outputs()]
            by_name = dict(zip(output_names, outputs, strict=True))
            sentence_vectors = by_name.get("sentence_embedding")
            if sentence_vectors is None:
                sentence_vectors = next(
                    (output for output in outputs if getattr(output, "ndim", 0) == 2),
                    None,
                )
            if sentence_vectors is None:
                token_vectors = next(
                    (output for output in outputs if getattr(output, "ndim", 0) == 3),
                    None,
                )
                if token_vectors is None:
                    raise ValueError(f"unexpected ONNX output names: {output_names}")
                expanded_mask = attention_mask[..., None].astype(np.float32)
                sentence_vectors = (token_vectors * expanded_mask).sum(axis=1) / np.clip(
                    expanded_mask.sum(axis=1), 1e-9, None
                )
            vectors = np.asarray(sentence_vectors, dtype=np.float32)
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            if np.any(norms == 0):
                raise ValueError("ONNX model returned a zero embedding")
            vectors = vectors / norms
        except Exception as exc:
            raise SemanticBackendUnavailable(f"local ONNX inference failed: {exc}") from exc
        return cast(Sequence[Sequence[float]], vectors.tolist())


def build_local_semantic_resolver(
    model_path: str | Path,
    *,
    catalog_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
    min_confidence: float = 0.50,
    min_margin: float = 0.08,
) -> EmbeddingSemanticResolver:
    """Build the optional resolver only from a checksum-verified installation."""

    root = Path(model_path)
    manifest = Path(manifest_path) if manifest_path is not None else None
    metadata = installed_model_metadata(root, manifest=manifest)
    if metadata is None:
        raise SemanticBackendUnavailable(
            "local model installation is absent or failed manifest checksum validation"
        )
    backend = OnnxMiniLMBackend(
        root,
        model_id=str(metadata["model_id"]),
        revision=str(metadata["revision"]),
        onnx_file=str(metadata["onnx_file"]),
    )
    return EmbeddingSemanticResolver(
        backend,
        load_intent_catalog(catalog_path),
        min_confidence=min_confidence,
        min_margin=min_margin,
    )


__all__ = [
    "EmbeddingBackend",
    "EmbeddingSemanticResolver",
    "IntentCatalog",
    "IntentDefinition",
    "ModelProvenance",
    "OnnxMiniLMBackend",
    "SemanticBackendUnavailable",
    "SemanticIntentResolver",
    "SemanticResolution",
    "SemanticStatus",
    "build_local_semantic_resolver",
    "default_catalog_path",
    "load_intent_catalog",
]
