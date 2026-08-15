from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

from kestrel.ingestion.bazaarpulse import Listing, ProductMatch
from kestrel.integration_store import store_bazaarpulse_snapshot
from kestrel.market_api import create_market_api
from kestrel.metrics.external import ExternalAnalyticsService


def _database(path: Path) -> None:
    with duckdb.connect(str(path)) as connection:
        connection.execute(
            """
            CREATE TABLE dim_product (
                product_id BIGINT, sku_code VARCHAR, product_name VARCHAR,
                brand VARCHAR, category VARCHAR, current_mrp_inr DOUBLE
            );
            INSERT INTO dim_product VALUES
                (1, 'SKU-1', 'Kestrel Milk', 'Kestrel', 'Dairy', 75)
            """
        )


def _listing(price: float) -> Listing:
    return Listing(
        listing_id="101",
        city="Mumbai",
        retailer="ValueMart",
        raw_title="Kestrel Milk 1L",
        normalized_title="kestrel milk",
        brand="Kestrel",
        pack_value=1,
        pack_uom="L",
        category="Dairy",
        current_price_inr=price,
        mrp_inr=75,
        is_available=True,
        last_seen=date(2026, 6, 30),
        detail_path="/product/101.html",
        source_path="/city/mumbai/page/1.html",
    )


def _ambiguous() -> ProductMatch:
    return ProductMatch(
        listing_id="101",
        status="ambiguous",
        confidence=0.91,
        runner_up_confidence=0.89,
        product_id=None,
        sku_code=None,
        reason="Candidates are too close.",
        suggested_product_id=1,
        suggested_sku_code="SKU-1",
        provenance="automatic_quarantine",
        algorithm_status="ambiguous",
    )


def test_history_is_append_only_replay_safe_and_current_is_replaceable(
    tmp_path: Path,
) -> None:
    database = tmp_path / "market.duckdb"
    _database(database)
    first = datetime(2026, 7, 1, 8, tzinfo=UTC)
    second = datetime(2026, 7, 2, 8, tzinfo=UTC)

    store_bazaarpulse_snapshot(database, [_listing(69)], [_ambiguous()], completed_at_utc=first)
    store_bazaarpulse_snapshot(database, [_listing(68)], [_ambiguous()], completed_at_utc=second)
    store_bazaarpulse_snapshot(database, [_listing(68)], [_ambiguous()], completed_at_utc=second)

    with duckdb.connect(str(database), read_only=True) as connection:
        counts = connection.execute(
            """
            SELECT
                (SELECT count(*) FROM ext_bazaarpulse_listing_current),
                (SELECT count(*) FROM ext_bazaarpulse_listing_history),
                (SELECT count(*) FROM ext_bazaarpulse_match_history)
            """
        ).fetchone()
        prices = connection.execute(
            """
            SELECT current_price_inr FROM ext_bazaarpulse_listing_history
            ORDER BY collected_at_utc
            """
        ).fetchall()
    assert counts == (1, 2, 2)
    assert prices == [(69.0,), (68.0,)]


def test_same_observation_identity_cannot_be_replayed_with_changed_payload(
    tmp_path: Path,
) -> None:
    database = tmp_path / "market.duckdb"
    _database(database)
    completed = datetime(2026, 7, 1, 8, tzinfo=UTC)
    store_bazaarpulse_snapshot(
        database, [_listing(69)], [_ambiguous()], completed_at_utc=completed
    )

    with pytest.raises(ValueError, match="different payload"):
        store_bazaarpulse_snapshot(
            database, [_listing(68)], [_ambiguous()], completed_at_utc=completed
        )

    with duckdb.connect(str(database), read_only=True) as connection:
        state = connection.execute(
            """
            SELECT
                (SELECT current_price_inr FROM ext_bazaarpulse_listing_current),
                (SELECT count(*) FROM ext_bazaarpulse_listing_history),
                (SELECT count(*) FROM external_sync_runs WHERE source_name = 'bazaarpulse')
            """
        ).fetchone()
    assert state == (69.0, 1, 1)


def test_later_reviewed_match_preserves_original_automatic_outcome(tmp_path: Path) -> None:
    database = tmp_path / "market.duckdb"
    _database(database)
    automatic = _ambiguous()
    reviewed = ProductMatch(
        listing_id="101",
        status="matched",
        confidence=0.93,
        runner_up_confidence=0.89,
        product_id=1,
        sku_code="SKU-1",
        reason="Reviewer verified the exact pack.",
        suggested_product_id=1,
        suggested_sku_code="SKU-1",
        provenance="manual_match",
        algorithm_status="ambiguous",
        algorithm_reason="Candidates are too close.",
        decision_source="config/competitor_match_decisions.yml",
        reviewer="A. Reviewer",
        reviewed_on=date(2026, 8, 15),
        review_note="Exact variant confirmed.",
    )
    store_bazaarpulse_snapshot(
        database,
        [_listing(69)],
        [automatic],
        completed_at_utc=datetime(2026, 7, 1, 8, tzinfo=UTC),
    )
    store_bazaarpulse_snapshot(
        database,
        [_listing(68)],
        [reviewed],
        completed_at_utc=datetime(2026, 8, 16, 8, tzinfo=UTC),
    )

    history = ExternalAnalyticsService(database).competitor_observation_history("101")

    assert list(history["match_status"]) == ["matched", "ambiguous"]
    assert history.iloc[0]["match_provenance"] == "manual_match"
    assert history.iloc[0]["algorithm_status"] == "ambiguous"
    assert history.iloc[0]["reviewer"] == "A. Reviewer"
    assert history.iloc[0]["reviewed_on"].date() == date(2026, 8, 15)


def test_read_service_and_api_expose_queue_and_listing_audit(tmp_path: Path) -> None:
    database = tmp_path / "market.duckdb"
    _database(database)
    store_bazaarpulse_snapshot(
        database,
        [_listing(69)],
        [_ambiguous()],
        completed_at_utc=datetime(2026, 7, 1, 8, tzinfo=UTC),
    )

    service = ExternalAnalyticsService(database)
    queue = service.competitor_review_queue(city="Mumbai")
    history = service.competitor_observation_history("101")
    client = TestClient(create_market_api(database))
    queue_response = client.get("/market/review-queue", params={"city": "Mumbai"})
    history_response = client.get("/market/listings/101/history")

    assert list(queue["listing_id"]) == ["101"]
    assert queue.iloc[0]["suggested_sku_code"] == "SKU-1"
    assert history.iloc[0]["match_provenance"] == "automatic_quarantine"
    assert queue_response.status_code == 200
    assert queue_response.json()["items"][0]["listing_id"] == "101"
    assert history_response.status_code == 200
    assert history_response.json()["items"][0]["algorithm_status"] == "ambiguous"


def test_first_governed_sync_migrates_legacy_current_snapshot_into_history(
    tmp_path: Path,
) -> None:
    database = tmp_path / "market.duckdb"
    _database(database)
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            """
            CREATE TABLE ext_bazaarpulse_listing_current AS
            SELECT
                '101'::VARCHAR AS listing_id, 'Mumbai'::VARCHAR AS city,
                'ValueMart'::VARCHAR AS retailer, 'Kestrel Milk 1L'::VARCHAR AS raw_title,
                'kestrel milk'::VARCHAR AS normalized_title, 'Kestrel'::VARCHAR AS brand,
                1.0::DOUBLE AS pack_value, 'L'::VARCHAR AS pack_uom,
                'Dairy'::VARCHAR AS category, 70.0::DOUBLE AS current_price_inr,
                75.0::DOUBLE AS mrp_inr, TRUE AS is_available,
                DATE '2026-06-29' AS last_seen, '/product/101.html'::VARCHAR AS detail_path,
                '/city/mumbai/page/1.html'::VARCHAR AS source_path,
                TIMESTAMPTZ '2026-06-30 08:00:00+00' AS collected_at_utc;

            CREATE TABLE ext_bazaarpulse_match_current AS
            SELECT
                '101'::VARCHAR AS listing_id, 'matched'::VARCHAR AS status,
                0.98::DOUBLE AS confidence, 0.30::DOUBLE AS runner_up_confidence,
                1::BIGINT AS product_id, 'SKU-1'::VARCHAR AS sku_code,
                'Legacy automatic match.'::VARCHAR AS reason, TRUE AS matched,
                TIMESTAMPTZ '2026-06-30 08:00:00+00' AS collected_at_utc;
            """
        )

    store_bazaarpulse_snapshot(
        database,
        [_listing(69)],
        [_ambiguous()],
        completed_at_utc=datetime(2026, 7, 1, 8, tzinfo=UTC),
    )

    with duckdb.connect(str(database), read_only=True) as connection:
        history = connection.execute(
            """
            SELECT match_status, match_provenance, current_price_inr
            FROM vw_competitor_price_history
            ORDER BY collected_at_utc
            """
        ).fetchall()
    assert history == [
        ("matched", "automatic_high_confidence", 70.0),
        ("ambiguous", "automatic_quarantine", 69.0),
    ]
