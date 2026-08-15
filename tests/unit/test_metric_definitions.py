from pathlib import Path

import pytest

from kestrel.metrics.definitions import load_metric_definitions


def test_metric_registry_entries_have_explicit_semantic_versions() -> None:
    registry = load_metric_definitions(Path("config/metrics.yml"))

    assert len(registry) == 24
    versions = {key: definition.version for key, definition in registry.items()}
    assert sum(version == "1.0.0" for version in versions.values()) == 22
    assert versions["freight_cost_per_case"] == "1.1.0"
    assert versions["settled_freight_cost_per_case"] == "1.1.0"
    assert registry["allocation_rate"].unit == "percent"
    assert registry["overdue_backlog_orders"].grain.startswith("Order as-of")
    assert registry["fill_rate_eaches"].version == "1.0.0"
    assert registry["fill_rate_case_equivalents"].unit == "percent"
    assert registry["short_delivery_value_exposure_inr"].status == "estimated"
    assert registry["settled_freight_cost_per_case"].grain.startswith(
        "Independently aggregated"
    )
    assert "route" in registry["settled_freight_cost_per_case"].allowed_dimensions
    assert "ext_freight_invoice_current" in registry[
        "settled_freight_cost_per_case"
    ].source_models
    assert registry["competitor_match_coverage"].interpretation_limits
    assert {"customer_region", "outlet", "channel"}.issubset(
        registry["temperature_excursions_per_100"].allowed_dimensions
    )
    assert "promotion_mechanic" in registry[
        "approved_credit_note_value_inr"
    ].allowed_dimensions


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
