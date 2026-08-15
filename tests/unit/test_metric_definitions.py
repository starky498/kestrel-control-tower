from pathlib import Path

import pytest

from kestrel.metrics.definitions import load_metric_definitions


def test_metric_registry_entries_have_explicit_semantic_versions() -> None:
    registry = load_metric_definitions(Path("config/metrics.yml"))

    assert len(registry) >= 17
    assert all(definition.version == "1.0.0" for definition in registry.values())
    assert registry["allocation_rate"].unit == "percent"
    assert registry["overdue_backlog_orders"].grain.startswith("Order as-of")


def test_metric_registry_rejects_an_entry_without_a_version(tmp_path: Path) -> None:
    path = tmp_path / "metrics.yml"
    path.write_text(
        """
metric_without_version:
  title: Example
  formula: count(*)
  grain: Row
  date_basis: Date
  eligible_population: All
  numerator: Rows
  denominator: Not applicable
  unit: rows
  status: verified
  warning: None
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(TypeError, match="version"):
        load_metric_definitions(path)
