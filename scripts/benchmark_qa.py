#!/usr/bin/env python3
"""Benchmark canonical, paraphrased, ambiguous, and unsupported governed questions."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path

from kestrel.metrics.external import ExternalAnalyticsService
from kestrel.metrics.service import AnalyticsService
from kestrel.nlq import AnswerStatus, DimensionKey, MetricKey, QuestionRouter


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    name: str
    question: str
    status: AnswerStatus
    metric: MetricKey | None = None
    dimension: DimensionKey | None = None
    limit: int | None = None


CASES = (
    BenchmarkCase(
        "canonical_outlet_ranking",
        "Which five outlets had the lowest case fill rate last month, excluding closed and "
        "test outlets?",
        AnswerStatus.OK,
        MetricKey.FILL_RATE,
        DimensionKey.OUTLET,
        5,
    ),
    BenchmarkCase(
        "paraphrased_outlet_ranking",
        "Show me the five worst stores by case fill rate last month",
        AnswerStatus.OK,
        MetricKey.FILL_RATE,
        DimensionKey.OUTLET,
        5,
    ),
    BenchmarkCase(
        "strict_otif_by_customer_region",
        "What was OTIF by customer region for the last complete quarter?",
        AnswerStatus.OK,
        MetricKey.STRICT_OTIF,
        DimensionKey.CUSTOMER_REGION,
    ),
    BenchmarkCase(
        "ambiguous_region_lens",
        "What was fill rate in West region last month?",
        AnswerStatus.AMBIGUOUS,
    ),
    BenchmarkCase(
        "unsupported_sentiment",
        "How happy are our customers?",
        AnswerStatus.UNSUPPORTED,
    ),
    BenchmarkCase(
        "competitor_price_position",
        "For the top 20 SKUs by value, compare MRP with the lowest competitor price in Mumbai "
        "for Q1",
        AnswerStatus.OK,
        MetricKey.MARKET_PRICE_GAP,
        DimensionKey.SKU,
        20,
    ),
    BenchmarkCase(
        "freight_by_warehouse",
        "Freight cost per delivered case by warehouse last quarter",
        AnswerStatus.OK,
        MetricKey.FREIGHT_PER_CASE,
        DimensionKey.WAREHOUSE,
    ),
    BenchmarkCase(
        "discontinued_sku_evidence",
        "Which outlets ordered a discontinued SKU after its discontinuation date in Q1?",
        AnswerStatus.OK,
        MetricKey.DISCONTINUED_SKUS,
        DimensionKey.OUTLET,
        100,
    ),
)


def _case_failures(case: BenchmarkCase, answer: object) -> list[str]:
    failures: list[str] = []
    status = getattr(answer, "status", None)
    intent = getattr(answer, "intent", None)
    if status != case.status:
        failures.append(f"status={status}; expected={case.status}")
    if case.metric is not None and getattr(intent, "metric", None) != case.metric:
        failures.append(f"metric={getattr(intent, 'metric', None)}; expected={case.metric}")
    if case.dimension is not None and case.dimension not in getattr(intent, "dimensions", ()):
        failures.append(
            f"dimensions={getattr(intent, 'dimensions', None)}; expected {case.dimension}"
        )
    if case.limit is not None and getattr(intent, "limit", None) != case.limit:
        failures.append(f"limit={getattr(intent, 'limit', None)}; expected={case.limit}")
    return failures


def run_benchmark(database: Path, *, threshold_ms: float) -> dict[str, object]:
    router = QuestionRouter(
        AnalyticsService(database),
        ExternalAnalyticsService(database),
    )
    results: list[dict[str, object]] = []
    for case in CASES:
        started = time.perf_counter()
        answer = router.answer(case.question)
        elapsed_ms = (time.perf_counter() - started) * 1000
        failures = _case_failures(case, answer)
        if elapsed_ms > threshold_ms:
            failures.append(f"latency {elapsed_ms:.1f}ms exceeded {threshold_ms:.1f}ms")
        results.append(
            {
                "name": case.name,
                "question": case.question,
                "status": answer.status.value,
                "latency_ms": round(elapsed_ms, 3),
                "summary": answer.summary,
                "passed": not failures,
                "failures": failures,
            }
        )
    return {
        "schema_version": 1,
        "database": str(database),
        "case_count": len(results),
        "threshold_ms": threshold_ms,
        "passed": all(bool(result["passed"]) for result in results),
        "cases": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path(".kestrel/kestrel.duckdb"))
    parser.add_argument("--output", type=Path, default=Path(".kestrel/qa-benchmark.json"))
    parser.add_argument("--threshold-ms", type=float, default=5_000.0)
    arguments = parser.parse_args()
    report = run_benchmark(arguments.database.resolve(), threshold_ms=arguments.threshold_ms)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
