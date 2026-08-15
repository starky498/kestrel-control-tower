from __future__ import annotations

import json
from pathlib import Path

import pytest

from kestrel.observability import observe_run, read_recent_events


def _events(runtime_dir: Path) -> list[dict[str, object]]:
    log = runtime_dir / "logs" / "kestrel.jsonl"
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_observed_run_records_success_and_redacts_credentials(tmp_path: Path) -> None:
    with observe_run(
        tmp_path,
        "sync-context",
        details={"api_key": "do-not-write", "coverage": {"cities": 8}},
    ) as trace:
        trace.annotate(records=42)

    events = _events(tmp_path)
    assert [event["status"] for event in events] == ["STARTED", "SUCCEEDED"]
    assert events[0]["details"] == {
        "api_key": "[REDACTED]",
        "coverage": {"cities": 8},
    }
    assert events[1]["details"]["records"] == 42  # type: ignore[index]


def test_observed_run_records_failure_without_swallowing_it(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="bad input"), observe_run(tmp_path, "build"):
        raise ValueError("bad input")

    events = _events(tmp_path)
    assert events[-1]["status"] == "FAILED"
    assert events[-1]["error_type"] == "ValueError"


def test_recent_event_reader_is_bounded_and_skips_bad_lines(tmp_path: Path) -> None:
    log = tmp_path / "logs" / "kestrel.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text('{"status":"OLD"}\nnot-json\n{"status":"NEW"}\n')

    assert read_recent_events(tmp_path, limit=2) == [{"status": "NEW"}]
    assert read_recent_events(tmp_path, limit=0) == []
