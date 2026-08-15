#!/usr/bin/env python3
"""Run a repeatable Streamlit page, semantic-label, and latency audit."""

from __future__ import annotations

import argparse
import json
import re
import time
from html import unescape
from pathlib import Path
from typing import Any

from streamlit.testing.v1 import AppTest

INTERACTIVE_COLLECTIONS = (
    "button",
    "checkbox",
    "date_input",
    "multiselect",
    "number_input",
    "radio",
    "select_slider",
    "selectbox",
    "slider",
    "text_area",
    "text_input",
    "toggle",
)


def _message(element: Any) -> str:
    value = getattr(element, "value", element)
    return str(value)


def _unlabelled_controls(app: AppTest) -> list[str]:
    missing: list[str] = []
    for collection_name in INTERACTIVE_COLLECTIONS:
        for index, element in enumerate(getattr(app, collection_name)):
            label = getattr(element, "label", None)
            if not isinstance(label, str) or not label.strip():
                missing.append(f"{collection_name}[{index}]")
    return missing


def _page_headings(app: AppTest) -> tuple[str, ...]:
    rendered = "\n".join(_message(item) for item in app.markdown)
    matches = re.findall(
        r'<h1\s+class=["\'][^"\']*\bkp-title\b[^"\']*["\'][^>]*>(.*?)</h1>',
        rendered,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return tuple(
        unescape(re.sub(r"<[^>]+>", "", match)).strip()
        for match in matches
    )


def run_audit(
    app_path: Path,
    *,
    timeout: float,
    threshold_ms: float,
    expected_pages: int = 8,
) -> dict[str, Any]:
    app = AppTest.from_file(app_path, default_timeout=timeout)
    started = time.perf_counter()
    app.run(timeout=timeout)
    initial_ms = (time.perf_counter() - started) * 1000
    if not app.sidebar.radio:
        raise RuntimeError("Workspace navigation radio was not rendered")
    workspace = app.sidebar.radio[0]
    page_names = tuple(str(value) for value in workspace.options)
    results: list[dict[str, Any]] = []

    for page_name in page_names:
        started = time.perf_counter()
        app.sidebar.radio[0].set_value(page_name).run(timeout=timeout)
        elapsed_ms = (time.perf_counter() - started) * 1000
        headings = _page_headings(app)
        results.append(
            {
                "page": page_name,
                "render_ms": round(elapsed_ms, 3),
                "page_heading_visible": page_name in headings,
                "rendered_h1_headings": headings,
                "exceptions": [_message(item) for item in app.exception],
                "errors": [_message(item) for item in app.error],
                "unlabelled_controls": _unlabelled_controls(app),
            }
        )

    failures = [
        result
        for result in results
        if result["exceptions"]
        or result["errors"]
        or result["unlabelled_controls"]
        or not result["page_heading_visible"]
        or result["render_ms"] > threshold_ms
    ]
    page_count_passed = len(page_names) == expected_pages
    return {
        "schema_version": 1,
        "app_path": str(app_path),
        "initial_render_ms": round(initial_ms, 3),
        "page_count": len(page_names),
        "expected_page_count": expected_pages,
        "page_count_passed": page_count_passed,
        "threshold_ms": threshold_ms,
        "passed": not failures and page_count_passed,
        "pages": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", type=Path, default=Path("app.py"))
    parser.add_argument("--output", type=Path, default=Path(".kestrel/ui-audit.json"))
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--threshold-ms", type=float, default=15_000.0)
    parser.add_argument("--expected-pages", type=int, default=8)
    arguments = parser.parse_args()

    report = run_audit(
        arguments.app.resolve(),
        timeout=arguments.timeout,
        threshold_ms=arguments.threshold_ms,
        expected_pages=arguments.expected_pages,
    )
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
