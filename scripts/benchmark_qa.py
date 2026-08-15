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
from kestrel.nlq.semantic import SemanticBackendUnavailable, build_local_semantic_resolver


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
        "returns_by_category_with_leading_reason",
        "Which categories drive the largest value of returns, and what is the leading reason "
        "code in Q1?",
        AnswerStatus.OK,
        MetricKey.RETURNS,
        DimensionKey.CATEGORY,
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
    BenchmarkCase(
        "allocation_by_warehouse",
        "Which five warehouses had the lowest allocation rate last month?",
        AnswerStatus.OK,
        MetricKey.ALLOCATION_RATE,
        DimensionKey.WAREHOUSE,
        5,
    ),
    BenchmarkCase(
        "post_allocation_by_warehouse",
        "Which five warehouses had the lowest post-allocation fulfilment last month?",
        AnswerStatus.OK,
        MetricKey.POST_ALLOCATION_FULFILMENT,
        DimensionKey.WAREHOUSE,
        5,
    ),
    BenchmarkCase(
        "requested_cohort_on_time",
        "Which five routes had the lowest on-time rate last quarter?",
        AnswerStatus.OK,
        MetricKey.ON_TIME_RATE,
        DimensionKey.ROUTE,
        5,
    ),
    BenchmarkCase(
        "actual_delivery_cohort_on_time",
        "Which five routes had the lowest actual-delivery-date on-time rate last quarter?",
        AnswerStatus.OK,
        MetricKey.DELIVERY_ON_TIME_RATE,
        DimensionKey.ROUTE,
        5,
    ),
    BenchmarkCase(
        "overdue_backlog_by_warehouse",
        "Which five warehouses have the largest overdue backlog last quarter?",
        AnswerStatus.OK,
        MetricKey.BACKLOG,
        DimensionKey.WAREHOUSE,
        5,
    ),
    BenchmarkCase(
        "short_delivery_exposure",
        "Which five warehouses have the largest short-delivery value exposure last quarter?",
        AnswerStatus.OK,
        MetricKey.SHORT_DELIVERY_EXPOSURE,
        DimensionKey.WAREHOUSE,
        5,
    ),
    BenchmarkCase(
        "near_expiry_inventory",
        "Which five warehouses have the worst inventory risk last quarter?",
        AnswerStatus.OK,
        MetricKey.INVENTORY_RISK,
        DimensionKey.WAREHOUSE,
        5,
    ),
    BenchmarkCase(
        "competitor_match_coverage",
        "What is competitor match coverage in Mumbai?",
        AnswerStatus.OK,
        MetricKey.COMPETITOR_COVERAGE,
    ),
    BenchmarkCase(
        "spelling_correction",
        "Which five warehoses had the worst fil rte last month?",
        AnswerStatus.OK,
        MetricKey.FILL_RATE,
        DimensionKey.WAREHOUSE,
        5,
    ),
)

SEMANTIC_CASES = (
    BenchmarkCase(
        "local_semantic_post_allocation_paraphrase",
        "Which depots keep falling short after stock has already been assigned?",
        AnswerStatus.OK,
        MetricKey.POST_ALLOCATION_FULFILMENT,
    ),
    BenchmarkCase(
        "local_semantic_inventory_paraphrase",
        "Where is stock close to going out of date?",
        AnswerStatus.OK,
        MetricKey.INVENTORY_RISK,
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


def run_benchmark(
    database: Path,
    *,
    threshold_ms: float,
    model_path: Path | None = None,
) -> dict[str, object]:
    semantic_resolver = None
    if model_path is not None and (model_path / "kestrel-model.json").is_file():
        try:
            semantic_resolver = build_local_semantic_resolver(model_path)
        except (OSError, ValueError, SemanticBackendUnavailable):
            # A marker alone is not evidence of a valid installation. Keep the benchmark in its
            # rules-only mode unless every manifest size/checksum check succeeds.
            semantic_resolver = None
    router = QuestionRouter(
        AnalyticsService(database),
        ExternalAnalyticsService(database),
        semantic_resolver=semantic_resolver,
    )
    results: list[dict[str, object]] = []
    cases = (*CASES, *SEMANTIC_CASES) if semantic_resolver is not None else CASES
    for case in cases:
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
        "local_semantic_model_enabled": semantic_resolver is not None,
        "passed": all(bool(result["passed"]) for result in results),
        "cases": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path(".kestrel/kestrel.duckdb"))
    parser.add_argument("--output", type=Path, default=Path(".kestrel/qa-benchmark.json"))
    parser.add_argument("--threshold-ms", type=float, default=5_000.0)
    parser.add_argument(
        "--model-path",
        type=Path,
        default=Path(".kestrel/models/all-MiniLM-L6-v2"),
    )
    arguments = parser.parse_args()
    report = run_benchmark(
        arguments.database.resolve(),
        threshold_ms=arguments.threshold_ms,
        model_path=arguments.model_path.resolve(),
    )
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
