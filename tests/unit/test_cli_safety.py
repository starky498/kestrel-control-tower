from __future__ import annotations

from pathlib import Path

import pytest

from kestrel.cli import _validated_cleanup_target
from kestrel.config import ConfigurationError


def test_cleanup_target_must_remain_below_runtime(tmp_path: Path) -> None:
    runtime = tmp_path / ".kestrel"
    runtime.mkdir()

    assert _validated_cleanup_target(runtime / "cache.json", runtime) == (
        runtime / "cache.json"
    )
    with pytest.raises(ConfigurationError, match="outside the project runtime"):
        _validated_cleanup_target(tmp_path / "source.db", runtime)


def test_cleanup_target_rejects_symlink_indirection(tmp_path: Path) -> None:
    runtime = tmp_path / ".kestrel"
    runtime.mkdir()
    external = tmp_path / "external.db"
    external.write_text("preserve me", encoding="utf-8")
    link = runtime / "analytics.db"
    link.symlink_to(external)

    with pytest.raises(ConfigurationError, match="symlinked generated path"):
        _validated_cleanup_target(link, runtime)
    assert external.read_text(encoding="utf-8") == "preserve me"
