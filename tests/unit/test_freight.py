from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from kestrel.ingestion.freight import (
    FreightClient,
    FreightInvoice,
    load_last_good_cache,
)


def _invoice(
    invoice_id: str,
    *,
    amount: int = 12_345,
    detention_charge: int = 250,
    invoice_date: str = "2026-06-15",
) -> dict[str, object]:
    return {
        "invoice_id": invoice_id,
        "carrier_id": "CR-101",
        "carrier_name": "Bluewheel Logistics",
        "warehouse_code": "WH01",
        "route_code": "RT0001",
        "invoice_date": invoice_date,
        "service_date": "2026-06-14",
        "amount": amount,
        "currency": "INR",
        "fuel_surcharge_pct": 8.25,
        "detention_charge": detention_charge,
        "distance_km": 42.5,
        "weight_kg": 812.4,
        "temperature_controlled": True,
        "status": "PAID",
        "created_at_utc": "2026-06-15T20:15:00Z",
    }


def _page(
    invoices: list[dict[str, object]],
    *,
    next_cursor: str | None,
) -> dict[str, object]:
    return {
        "data": invoices,
        "next_cursor": next_cursor,
        "page_size": len(invoices),
        "total_estimate": len(invoices),
    }


def _client(
    tmp_path: Path,
    handler: httpx.MockTransport,
    **kwargs: Any,
) -> FreightClient:
    return FreightClient(
        "https://freight.example.test",
        "test-key",
        tmp_path / "freight.json",
        client=httpx.Client(transport=handler),
        now_fn=lambda: datetime(2026, 7, 1, 0, 0, tzinfo=UTC),
        **kwargs,
    )


def test_invoice_validates_schema_converts_paise_and_normalizes_timestamp() -> None:
    invoice = FreightInvoice.model_validate(_invoice("FI-0000001"))

    assert invoice.amount_paise == 12_345
    assert invoice.amount_inr == Decimal("123.45")
    assert invoice.detention_charge_paise == 250
    assert invoice.detention_charge_inr == Decimal("2.50")
    assert invoice.created_at_utc == datetime(2026, 6, 15, 20, 15, tzinfo=UTC)
    assert invoice.created_at_ist.isoformat() == "2026-06-16T01:45:00+05:30"


def test_sync_follows_cursors_filters_dates_deduplicates_and_writes_cache(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        cursor = request.url.params.get("cursor")
        if cursor is None:
            return httpx.Response(
                200,
                json=_page(
                    [_invoice("FI-0000001"), _invoice("FI-0000002")],
                    next_cursor="cur_2",
                ),
            )
        assert cursor == "cur_2"
        return httpx.Response(
            200,
            json=_page(
                [
                    _invoice("FI-0000002", amount=99_999),
                    _invoice("FI-0000003"),
                ],
                next_cursor=None,
            ),
        )

    client = _client(
        tmp_path,
        httpx.MockTransport(handler),
        timeout_seconds=3.5,
    )
    result = client.sync(date_from=date(2026, 6, 1), date_to="2026-06-30")

    assert result.complete
    assert result.metadata.page_count == 2
    assert result.metadata.request_count == 2
    assert result.metadata.retry_count == 0
    assert result.metadata.fetched_count == 4
    assert result.metadata.record_count == 3
    assert [invoice.invoice_id for invoice in result.invoices] == [
        "FI-0000001",
        "FI-0000002",
        "FI-0000003",
    ]
    assert result.invoices[1].amount_inr == Decimal("999.99")

    assert len(requests) == 2
    for request in requests:
        assert request.headers["X-API-Key"] == "test-key"
        assert request.url.params["from"] == "2026-06-01"
        assert request.url.params["to"] == "2026-06-30"
        assert request.url.params["limit"] == "200"
        assert request.extensions["timeout"]["read"] == 3.5

    cache = load_last_good_cache(tmp_path / "freight.json")
    assert cache.metadata.complete
    assert cache.invoices == result.invoices
    assert not client.checkpoint_path.exists()
    assert not client.partial_path.exists()
    assert not list(tmp_path.glob(".freight.json.*.tmp"))


def test_retries_429_and_503_honors_retry_after_without_real_sleep(tmp_path: Path) -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        if calls == 2:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json=_page([_invoice("FI-0000001")], next_cursor=None),
        )

    client = _client(
        tmp_path,
        httpx.MockTransport(handler),
        max_retries=3,
        base_backoff_seconds=0.5,
        max_backoff_seconds=4.0,
        jitter_seconds=0.25,
        random_fn=lambda: 0.0,
        sleep_fn=sleeps.append,
    )
    result = client.sync()

    assert result.complete
    assert calls == 3
    assert sleeps == [2.0, 1.0]
    assert result.metadata.request_count == 3
    assert result.metadata.retry_count == 2


def test_retries_transient_transport_timeout(tmp_path: Path) -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("slow upstream", request=request)
        return httpx.Response(
            200,
            json=_page([_invoice("FI-0000001")], next_cursor=None),
        )

    result = _client(
        tmp_path,
        httpx.MockTransport(handler),
        max_retries=2,
        base_backoff_seconds=0.5,
        jitter_seconds=0,
        sleep_fn=sleeps.append,
    ).sync()

    assert result.complete
    assert calls == 2
    assert sleeps == [0.5]
    assert result.metadata.retry_count == 1


def test_incomplete_sync_preserves_last_good_and_resumes_from_checkpoint(
    tmp_path: Path,
) -> None:
    initial = _client(
        tmp_path,
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json=_page([_invoice("FI-OLD")], next_cursor=None),
            )
        ),
    )
    assert initial.sync().complete
    old_cache_text = initial.cache_path.read_text(encoding="utf-8")

    def broken_handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("cursor") is None:
            return httpx.Response(
                200,
                json=_page([_invoice("FI-NEW-1")], next_cursor="cur_1"),
            )
        return httpx.Response(
            200,
            json={
                "data": [{"invoice_id": "missing-required-fields"}],
                "next_cursor": None,
                "page_size": 1,
                "total_estimate": 2,
            },
        )

    broken = _client(tmp_path, httpx.MockTransport(broken_handler))
    incomplete = broken.sync()

    assert not incomplete.complete
    assert incomplete.metadata.page_count == 1
    assert incomplete.metadata.next_cursor == "cur_1"
    assert incomplete.metadata.error is not None
    assert incomplete.last_good_available
    assert broken.cache_path.read_text(encoding="utf-8") == old_cache_text
    assert [item.invoice_id for item in load_last_good_cache(broken.cache_path).invoices] == [
        "FI-OLD"
    ]
    checkpoint = json.loads(broken.checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["complete"] is False
    assert checkpoint["next_cursor"] == "cur_1"

    resumed_requests: list[str | None] = []

    def resumed_handler(request: httpx.Request) -> httpx.Response:
        resumed_requests.append(request.url.params.get("cursor"))
        return httpx.Response(
            200,
            json=_page([_invoice("FI-NEW-2")], next_cursor=None),
        )

    resumed = _client(tmp_path, httpx.MockTransport(resumed_handler)).sync()

    assert resumed.complete
    assert resumed.metadata.resumed
    assert resumed_requests == ["cur_1"]
    assert [item.invoice_id for item in resumed.invoices] == ["FI-NEW-1", "FI-NEW-2"]


def test_filtered_sync_upserts_existing_cache_idempotently(tmp_path: Path) -> None:
    initial_handler = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            json=_page(
                [_invoice("FI-0000001"), _invoice("FI-0000002")],
                next_cursor=None,
            ),
        )
    )
    assert _client(tmp_path, initial_handler).sync().complete

    update_handler = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            json=_page(
                [
                    _invoice("FI-0000001", amount=77_700),
                    _invoice("FI-0000003"),
                ],
                next_cursor=None,
            ),
        )
    )
    first_update = _client(tmp_path, update_handler).sync(
        date_from="2026-06-01",
        date_to="2026-06-30",
    )
    second_update = _client(tmp_path, update_handler).sync(
        date_from="2026-06-01",
        date_to="2026-06-30",
    )

    assert first_update.metadata.cache_mode == "range_upsert"
    assert second_update.complete
    assert len(second_update.invoices) == 3
    by_id = {invoice.invoice_id: invoice for invoice in second_update.invoices}
    assert by_id["FI-0000001"].amount_inr == Decimal("777.00")
    assert by_id["FI-0000002"].amount_inr == Decimal("123.45")
