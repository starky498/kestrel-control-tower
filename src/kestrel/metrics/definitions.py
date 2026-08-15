"""Load the human- and machine-readable metric registry."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class MetricDefinition:
    key: str
    version: str
    title: str
    formula: str
    grain: str
    date_basis: str
    eligible_population: str
    numerator: str
    denominator: str
    unit: str
    status: str
    warning: str
    allowed_dimensions: tuple[str, ...]
    exclusions: tuple[str, ...]
    source_models: tuple[str, ...]
    interpretation_limits: tuple[str, ...]


def load_metric_definitions(path: Path) -> dict[str, MetricDefinition]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    definitions: dict[str, MetricDefinition] = {}
    governance_fields = (
        "allowed_dimensions",
        "exclusions",
        "source_models",
        "interpretation_limits",
    )
    for key, raw_definition in payload.items():
        definition = dict(raw_definition)
        required_fields = tuple(
            field
            for field in MetricDefinition.__dataclass_fields__
            if field != "key"
        )
        missing = next((field for field in required_fields if field not in definition), None)
        if missing is not None:
            raise TypeError(f"MetricDefinition is missing required field: {missing}")
        for field in governance_fields:
            definition[field] = tuple(definition[field])
        definitions[key] = MetricDefinition(key=key, **definition)
    return definitions
