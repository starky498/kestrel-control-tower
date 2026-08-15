"""Small, dependency-free structured run logging for local operations."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SENSITIVE_FRAGMENTS = ("api_key", "password", "secret", "token", "credential")


def _redact(value: object, *, key: str = "") -> object:
    if any(fragment in key.casefold() for fragment in _SENSITIVE_FRAGMENTS):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {str(item_key): _redact(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def write_event(runtime_dir: Path, event: Mapping[str, object]) -> Path:
    """Append one redacted JSON event to the generated local operations log."""

    log_path = runtime_dir / "logs" / "kestrel.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    safe_event = _redact(event)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(safe_event, sort_keys=True) + "\n")
    return log_path


def read_recent_events(runtime_dir: Path, *, limit: int = 50) -> list[dict[str, Any]]:
    """Read a bounded tail for Trust Center display; malformed lines remain isolated."""

    if limit < 1:
        return []
    log_path = runtime_dir / "logs" / "kestrel.jsonl"
    if not log_path.is_file():
        return []
    events: list[dict[str, Any]] = []
    for line in log_path.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


@dataclass
class RunTrace:
    run_id: str
    operation: str
    details: dict[str, object] = field(default_factory=dict)

    def annotate(self, **details: object) -> None:
        self.details.update(details)


@contextmanager
def observe_run(
    runtime_dir: Path,
    operation: str,
    *,
    details: Mapping[str, object] | None = None,
) -> Iterator[RunTrace]:
    """Record start, success/failure, duration, and safe operational context."""

    started_at = datetime.now(UTC)
    started_clock = time.perf_counter()
    trace = RunTrace(uuid.uuid4().hex, operation, dict(details or {}))
    write_event(
        runtime_dir,
        {
            "schema_version": 1,
            "run_id": trace.run_id,
            "operation": operation,
            "status": "STARTED",
            "recorded_at_utc": started_at.isoformat(),
            "details": trace.details,
        },
    )
    try:
        yield trace
    except Exception as error:
        write_event(
            runtime_dir,
            {
                "schema_version": 1,
                "run_id": trace.run_id,
                "operation": operation,
                "status": "FAILED",
                "recorded_at_utc": datetime.now(UTC).isoformat(),
                "duration_ms": round((time.perf_counter() - started_clock) * 1000, 3),
                "error_type": type(error).__name__,
                "error": str(error),
                "details": trace.details,
            },
        )
        raise
    else:
        write_event(
            runtime_dir,
            {
                "schema_version": 1,
                "run_id": trace.run_id,
                "operation": operation,
                "status": "SUCCEEDED",
                "recorded_at_utc": datetime.now(UTC).isoformat(),
                "duration_ms": round((time.perf_counter() - started_clock) * 1000, 3),
                "details": trace.details,
            },
        )
