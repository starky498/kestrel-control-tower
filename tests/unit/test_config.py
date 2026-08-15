from __future__ import annotations

from pathlib import Path

import pytest
from pytest import MonkeyPatch

from kestrel.config import ConfigurationError, Settings, project_config_path


def _clear_path_overrides(monkeypatch: MonkeyPatch) -> None:
    for name in (
        "KESTREL_SOURCE_DB",
        "KESTREL_ANALYTICS_DB",
        "KESTREL_RUNTIME_DIR",
        "KESTREL_FREIGHT_CACHE",
        "KESTREL_COMPETITOR_CACHE",
        "KESTREL_WEATHER_CACHE",
        "KESTREL_HOLIDAY_CACHE",
        "KESTREL_BAZAARPULSE_SITE_ROOT",
        "KESTREL_NLQ_SEMANTIC_ENABLED",
        "KESTREL_NLQ_MODEL_PATH",
        "KESTREL_NLQ_MIN_CONFIDENCE",
        "KESTREL_NLQ_MIN_MARGIN",
    ):
        monkeypatch.delenv(name, raising=False)


def test_settings_discovers_checkout_from_working_directory(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    root = tmp_path / "checkout"
    nested = root / "scripts" / "nested"
    (root / "config").mkdir(parents=True)
    nested.mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='kestrel'\n")
    (root / "config" / "metrics.yml").write_text("metrics: {}\n")
    monkeypatch.delenv("KESTREL_PROJECT_ROOT", raising=False)
    _clear_path_overrides(monkeypatch)
    monkeypatch.chdir(nested)

    settings = Settings.load()

    assert settings.project_root == root
    assert settings.source_db == root / "data" / "source" / "data" / "kestrel_ops.db"
    assert settings.analytics_db == root / ".kestrel" / "kestrel.duckdb"
    assert settings.nlq_semantic_enabled
    assert settings.nlq_model_path == root / ".kestrel" / "models" / "all-MiniLM-L6-v2"
    assert settings.nlq_min_confidence == 0.50
    assert settings.nlq_min_margin == 0.08


def test_settings_honours_explicit_project_root(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    root = tmp_path / "external-checkout"
    root.mkdir()
    monkeypatch.setenv("KESTREL_PROJECT_ROOT", str(root))
    _clear_path_overrides(monkeypatch)

    settings = Settings.load()

    assert settings.project_root == root
    assert project_config_path("nlq_model.yml") == root / "config" / "nlq_model.yml"


def test_settings_honours_local_semantic_configuration(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    model = tmp_path / "models" / "mini"
    monkeypatch.setenv("KESTREL_PROJECT_ROOT", str(root))
    _clear_path_overrides(monkeypatch)
    monkeypatch.setenv("KESTREL_NLQ_SEMANTIC_ENABLED", "false")
    monkeypatch.setenv("KESTREL_NLQ_MODEL_PATH", str(model))
    monkeypatch.setenv("KESTREL_NLQ_MIN_CONFIDENCE", "0.6")
    monkeypatch.setenv("KESTREL_NLQ_MIN_MARGIN", "0.12")

    settings = Settings.load()

    assert not settings.nlq_semantic_enabled
    assert settings.nlq_model_path == model
    assert settings.nlq_min_confidence == 0.6
    assert settings.nlq_min_margin == 0.12


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("KESTREL_NLQ_SEMANTIC_ENABLED", "perhaps", "true or false"),
        ("KESTREL_NLQ_MIN_CONFIDENCE", "1.5", "between 0 and 1"),
        ("KESTREL_NLQ_MIN_MARGIN", "not-a-number", "must be a number"),
    ],
)
def test_invalid_local_semantic_configuration_fails_closed(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    name: str,
    value: str,
    message: str,
) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    monkeypatch.setenv("KESTREL_PROJECT_ROOT", str(root))
    _clear_path_overrides(monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises(ConfigurationError, match=message):
        Settings.load()
