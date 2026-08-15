from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from kestrel.ingestion.bazaarpulse import (
    BazaarPulseCollector,
    DisallowedPathError,
    HttpSiteSource,
    Listing,
    LocalSiteSource,
    ProductCandidate,
    is_allowed_path,
    match_listing_to_products,
    normalize_title,
    parse_listing_page,
    read_listings_cache,
)

NUMBERED_PAGE_HTML = """
<!doctype html><html><body><div class="wrap">
  <p class="muted">Home / Mumbai / page 1 of 2</p>
  <div class="card product-item" data-listing-id="7">
    <a href="/product/7.html"><strong>Combo KESTREL SEL. JUICE 200ML (New)</strong></a>
    <div class="muted">FreshCart &middot; 200 ml &middot; Beverages</div>
    <span class="price">&#8377;1,234.50</span>
    <div class="muted">MRP &#8377;1,300 &middot; In stock &middot; rated 4.4</div>
    <div class="muted">Last seen: 2026-06-27</div>
  </div>
  <p class="pager"><b>1</b> <a href="/city/mumbai/page/2.html">2</a></p>
</div></body></html>
"""

INDEX_PAGE_HTML = """
<!doctype html><html><body><div class="wrap">
  <p class="muted">Home / Bangalore / page 1 of 2</p>
  <div class="card product-item" data-listing-id="8">
    <a href="/product/8.html"><strong>Pack of 1 AmritValley Cheese 0.5L</strong></a>
    <div class="muted">DailyKart &middot; 0.5 l &middot; Dairy</div>
    <span class="pricing-block" data-price-paise="26774" data-currency="INR">Price</span>
    <div class="muted">MRP &#8377;288 &middot; Currently unavailable</div>
    <div class="muted">Last seen: 2026-06-14</div>
  </div>
  <p class="pager"><b>1</b> <a href="/city/bengaluru/index.html?p=2">2</a></p>
</div></body></html>
"""


def _listing(
    *,
    listing_id: str = "42",
    title: str = "Kestrel Juice 200ml",
    brand: str | None = "Kestrel",
    pack_value: float | None = 200,
    pack_uom: str | None = "ML",
    category: str = "Beverages",
) -> Listing:
    return Listing(
        listing_id=listing_id,
        city="Mumbai",
        retailer="FreshCart",
        raw_title=title,
        normalized_title=normalize_title(title),
        brand=brand,
        pack_value=pack_value,
        pack_uom=pack_uom,
        category=category,
        current_price_inr=48.0,
        mrp_inr=58.0,
        is_available=True,
        last_seen=None,
        detail_path=f"/product/{listing_id}.html",
        source_path="/city/mumbai/page/1.html",
    )


def test_parses_numbered_pagination_and_normalizes_listing() -> None:
    page = parse_listing_page(NUMBERED_PAGE_HTML, "/city/mumbai/page/1.html")

    assert page.city == "Mumbai"
    assert page.pagination_paths == ("/city/mumbai/page/2.html",)
    assert len(page.listings) == 1
    listing = page.listings[0]
    assert listing.listing_id == "7"
    assert listing.normalized_title == "kestrel select juice"
    assert listing.brand == "Kestrel"
    assert (listing.pack_value, listing.pack_uom) == (200.0, "ML")
    assert listing.category == "Beverages"
    assert listing.current_price_inr == 1234.5
    assert listing.mrp_inr == 1300.0
    assert listing.is_available is True
    assert listing.last_seen is not None and listing.last_seen.isoformat() == "2026-06-27"


def test_parses_index_pagination_city_alias_and_unavailable_listing() -> None:
    page = parse_listing_page(INDEX_PAGE_HTML, "/city/bengaluru/index.html")

    assert page.city == "Bengaluru"
    assert page.pagination_paths == ("/city/bengaluru/index_p2.html",)
    listing = page.listings[0]
    assert listing.normalized_title == "amrit valley cheese"
    assert listing.brand == "Amrit"
    assert (listing.pack_value, listing.pack_uom) == (0.5, "L")
    assert listing.current_price_inr == 267.74
    assert listing.is_available is False


@pytest.mark.parametrize(
    ("price_html", "expected"),
    [
        ('<b class="sellingPrice">INR 229.86</b>', 229.86),
        ('<div class="amt"><em>Rs.</em> 88.68 <small>incl. taxes</small></div>', 88.68),
    ],
)
def test_parses_alternate_city_price_markup(price_html: str, expected: float) -> None:
    page_html = NUMBERED_PAGE_HTML.replace('<span class="price">&#8377;1,234.50</span>', price_html)

    page = parse_listing_page(page_html, "/city/delhi/page/1.html", default_city="Delhi")

    assert page.listings[0].current_price_inr == expected


def test_local_collector_follows_pages_deduplicates_and_writes_atomic_cache(
    tmp_path: Path,
) -> None:
    site_root = tmp_path / "site"
    page_dir = site_root / "city" / "mumbai" / "page"
    page_dir.mkdir(parents=True)
    page_dir.joinpath("1.html").write_text(NUMBERED_PAGE_HTML, encoding="utf-8")
    page_dir.joinpath("2.html").write_text(
        NUMBERED_PAGE_HTML.replace("page 1 of 2", "page 2 of 2")
        .replace('<p class="pager"><b>1</b> <a href="/city/mumbai/page/2.html">2</a></p>', "")
        .replace("2026-06-27", "2026-06-28")
        .replace("1,234.50", "1,200.00"),
        encoding="utf-8",
    )
    cache_path = tmp_path / "cache" / "bazaarpulse.json"

    collector = BazaarPulseCollector.from_local(site_root, cache_path=cache_path)
    listings = collector.collect(["/city/mumbai/page/1.html"])

    assert len(listings) == 1
    assert listings[0].current_price_inr == 1200.0
    assert listings[0].last_seen is not None
    assert listings[0].last_seen.isoformat() == "2026-06-28"
    assert read_listings_cache(cache_path) == listings
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    assert payload["count"] == 1
    assert not list(cache_path.parent.glob(".bazaarpulse.json.*.tmp"))


def test_disallowed_paths_are_rejected_before_local_or_http_access(tmp_path: Path) -> None:
    assert not is_allowed_path("/internal/margin-sheet.html")
    assert not is_allowed_path("/city/mumbai/../../admin/secrets.html")

    with pytest.raises(DisallowedPathError):
        LocalSiteSource(tmp_path).read_text("/internal/margin-sheet.html")

    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(200, text="ok")

    source = HttpSiteSource(
        "https://example.test",
        crawl_delay_seconds=0,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(DisallowedPathError):
        source.read_text("/admin/users.html")
    assert requests == []


def test_http_source_enforces_configured_delay_between_requests() -> None:
    requested: list[str] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        return httpx.Response(200, text="<html></html>")

    source = HttpSiteSource(
        "https://example.test",
        crawl_delay_seconds=0.25,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock_fn=lambda: 0.0,
        sleep_fn=sleeps.append,
    )
    source.read_text("/city/mumbai/page/1.html")
    source.read_text("/city/mumbai/page/2.html")

    assert requested == ["/city/mumbai/page/1.html", "/city/mumbai/page/2.html"]
    assert sleeps == [pytest.approx(0.25)]


def test_product_match_accepts_a_clear_high_confidence_candidate() -> None:
    listing = _listing(title="Combo Kestrel Select Juice 200ml")
    products = [
        ProductCandidate(
            1,
            "SKU001",
            "Kestrel Select Juice 200ml",
            "Kestrel",
            "Beverages",
            200,
            "ML",
        ),
        ProductCandidate(2, "SKU002", "Bluepeak Rice 200ml", "Bluepeak", "Staples", 200, "ML"),
    ]

    result = match_listing_to_products(listing, products)

    assert result.matched
    assert result.status == "matched"
    assert result.sku_code == "SKU001"
    assert result.confidence == pytest.approx(1.0)


def test_product_match_rejects_ambiguous_candidates() -> None:
    listing = _listing(title="Kestrel Juice 200ml")
    products = [
        ProductCandidate(
            1, "SKU-A", "Kestrel Apple Juice 200ml", "Kestrel", "Beverages", 200, "ML"
        ),
        ProductCandidate(
            2, "SKU-B", "Kestrel Mango Juice 200ml", "Kestrel", "Beverages", 200, "ML"
        ),
    ]

    result = match_listing_to_products(
        listing,
        products,
        minimum_confidence=0.80,
        ambiguity_margin=0.06,
    )

    assert not result.matched
    assert result.status == "ambiguous"
    assert result.product_id is None
    assert result.runner_up_confidence == pytest.approx(result.confidence)


def test_product_match_rejects_low_confidence_and_pack_conflicts() -> None:
    listing = _listing(title="Kestrel Juice 200ml")
    products = [ProductCandidate(9, "SKU-X", "Bluepeak Soap 2kg", "Bluepeak", "Dairy", 2, "KG")]

    result = match_listing_to_products(listing, products)

    assert not result.matched
    assert result.status == "low_confidence"
    assert result.confidence <= 0.70
    assert result.sku_code is None
