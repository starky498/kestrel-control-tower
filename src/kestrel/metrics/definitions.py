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


def load_metric_definitions(path: Path) -> dict[str, MetricDefinition]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {
        key: MetricDefinition(key=key, **definition)
        for key, definition in payload.items()
    }
