from __future__ import annotations

from pathlib import Path

from pytest import MonkeyPatch

from kestrel.config import Settings


def _clear_path_overrides(monkeypatch: MonkeyPatch) -> None:
    for name in (
        "KESTREL_SOURCE_DB",
        "KESTREL_ANALYTICS_DB",
        "KESTREL_RUNTIME_DIR",
        "KESTREL_FREIGHT_CACHE",
        "KESTREL_COMPETITOR_CACHE",
        "KESTREL_BAZAARPULSE_SITE_ROOT",
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


def test_settings_honours_explicit_project_root(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    root = tmp_path / "external-checkout"
    root.mkdir()
    monkeypatch.setenv("KESTREL_PROJECT_ROOT", str(root))
    _clear_path_overrides(monkeypatch)

    settings = Settings.load()

    assert settings.project_root == root
