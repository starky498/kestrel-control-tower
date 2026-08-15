"""BazaarPulse listing collection and conservative product matching.

The supplied site deliberately uses two pagination layouts.  This module follows only
links inside a page's ``.pager`` element, refuses paths prohibited by the supplied
``robots.txt``, and supports either an HTTP origin or an unpacked local site directory.

Matching is intentionally allowed to fail.  A weak or ambiguous match is more damaging
to a price-position report than an explicit unmatched listing.
"""

from __future__ import annotations

import html as html_lib
import json
import os
import posixpath
import re
import tempfile
import time
import unicodedata
from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Literal, Protocol, Self
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup
from bs4.element import Tag

DEFAULT_ENTRY_PATHS: tuple[str, ...] = (
    "/city/mumbai/page/1.html",
    "/city/delhi/page/1.html",
    "/city/bengaluru/index.html",
    "/city/chennai/index.html",
)
DISALLOWED_PREFIXES: tuple[str, ...] = ("/internal", "/admin")

_LISTING_PAGE_RE = re.compile(
    r"^/city/[^/]+/(?:page/\d+\.html|index(?:_p\d+)?\.html)$",
    re.IGNORECASE,
)
_PRODUCT_DETAIL_RE = re.compile(r"^/product/(?P<listing_id>[^/]+)\.html$", re.IGNORECASE)
_PACK_RE = re.compile(
    r"(?P<value>\d+(?:[.,]\d+)?)\s*"
    r"(?P<uom>kilograms?|kgs?|kg|grams?|gms?|gm|g|millilit(?:er|re)s?|ml|"
    r"lit(?:er|re)s?|ltrs?|ltr|l)\b",
    re.IGNORECASE,
)
_MONEY_RE = re.compile(r"(?:₹|Rs\.?\s*)?([0-9][0-9,]*(?:\.\d+)?)", re.IGNORECASE)
_MRP_RE = re.compile(
    r"\bMRP\s*(?:₹|Rs\.?\s*)?([0-9][0-9,]*(?:\.\d+)?)",
    re.IGNORECASE,
)
_LAST_SEEN_RE = re.compile(r"\bLast\s+seen\s*:\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE)

_UOM_ALIASES: Mapping[str, str] = {
    "g": "G",
    "gm": "G",
    "gms": "G",
    "gram": "G",
    "grams": "G",
    "kg": "KG",
    "kgs": "KG",
    "kilogram": "KG",
    "kilograms": "KG",
    "ml": "ML",
    "milliliter": "ML",
    "milliliters": "ML",
    "millilitre": "ML",
    "millilitres": "ML",
    "l": "L",
    "ltr": "L",
    "ltrs": "L",
    "liter": "L",
    "liters": "L",
    "litre": "L",
    "litres": "L",
}

DEFAULT_BRAND_ALIASES: Mapping[str, str] = {
    "amrit valley": "Amrit",
    "amritvalley": "Amrit",
    "bluepeak": "Bluepeak",
    "coastline": "Coastline",
    "hillfare": "Hillfare",
    "kestrel select": "Kestrel",
    "kestrel sel": "Kestrel",
    "kestrel": "Kestrel",
    "marwar": "Marwar",
}

_CATEGORY_ALIASES: Mapping[str, str] = {
    "bakery": "Bakery",
    "beverages": "Beverages",
    "dairy": "Dairy",
    "frozen": "Frozen",
    "ready to eat": "Ready to Eat",
    "sauces": "Sauces",
    "snacks": "Snacks",
    "staples": "Staples",
}
_CITY_ALIASES: Mapping[str, str] = {
    "bangalore": "Bengaluru",
    "bengaluru": "Bengaluru",
    "delhi": "Delhi",
    "delhi ncr": "Delhi",
    "new delhi": "Delhi",
    "chennai": "Chennai",
    "mumbai": "Mumbai",
}


class DisallowedPathError(ValueError):
    """Raised before a source attempts to read a prohibited path."""


class CollectionLimitError(RuntimeError):
    """Raised if pagination exceeds the configured safety bound."""


@dataclass(frozen=True, slots=True)
class Listing:
    """One normalized BazaarPulse price listing."""

    listing_id: str
    city: str
    retailer: str
    raw_title: str
    normalized_title: str
    brand: str | None
    pack_value: float | None
    pack_uom: str | None
    category: str
    current_price_inr: float | None
    mrp_inr: float | None
    is_available: bool | None
    last_seen: date | None
    detail_path: str | None
    source_path: str

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible record."""

        record = asdict(self)
        record["last_seen"] = self.last_seen.isoformat() if self.last_seen else None
        return record

    @classmethod
    def from_dict(cls, record: Mapping[str, object]) -> Self:
        """Restore a listing from :func:`write_listings_cache` output."""

        values = dict(record)
        raw_date = values.get("last_seen")
        values["last_seen"] = date.fromisoformat(str(raw_date)) if raw_date else None
        return cls(**values)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class SourcePriceObservation:
    """One dated shelf-price observation published on a BazaarPulse detail page.

    This is source history at the business observation grain.  It is deliberately distinct
    from the append-only scrape/sync audit history, where the same current listing can be seen
    again on multiple collection runs.
    """

    listing_id: str
    city: str
    retailer: str
    raw_title: str
    category: str
    pack_value: float | None
    pack_uom: str | None
    observed_on: date
    price_inr: float
    source_path: str

    @property
    def source_price_observation_id(self) -> str:
        """Stable source identity: a listing has at most one quoted price per date."""

        return f"bazaarpulse-source:{self.listing_id}:{self.observed_on.isoformat()}"

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible record."""

        record = asdict(self)
        record["observed_on"] = self.observed_on.isoformat()
        return record

    @classmethod
    def from_dict(cls, record: Mapping[str, object]) -> Self:
        """Restore a source observation from the governed cache."""

        values = dict(record)
        values["observed_on"] = date.fromisoformat(str(values["observed_on"]))
        return cls(**values)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class SourceDetailFailure:
    """Structured evidence that one linked detail page could not be collected or parsed."""

    listing_id: str
    source_path: str
    failure_type: Literal["missing_detail_page", "http_404", "parse_error"]
    message: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, record: Mapping[str, object]) -> Self:
        return cls(**dict(record))  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class BazaarPulseSnapshot:
    """A complete current-listing crawl plus dated detail-page source history."""

    listings: tuple[Listing, ...]
    source_price_observations: tuple[SourcePriceObservation, ...]
    source_detail_failures: tuple[SourceDetailFailure, ...] = ()


@dataclass(frozen=True, slots=True)
class ParsedListingPage:
    """Listings and crawlable pagination links found on one city page."""

    city: str
    listings: tuple[Listing, ...]
    pagination_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ParsedProductDetail:
    """Identity fields and dated price rows parsed from one product detail page."""

    listing_id: str
    city: str
    retailer: str
    raw_title: str
    category: str
    pack_value: float | None
    pack_uom: str | None
    source_price_observations: tuple[SourcePriceObservation, ...]


@dataclass(frozen=True, slots=True)
class ProductCandidate:
    """Minimum product-master fields required for entity matching."""

    product_id: int | str
    sku_code: str
    product_name: str
    brand: str | None = None
    category: str | None = None
    pack_size_value: float | None = None
    pack_size_uom: str | None = None


MatchStatus = Literal[
    "matched",
    "ambiguous",
    "low_confidence",
    "no_candidates",
    "rejected",
]
MatchProvenance = Literal[
    "automatic",
    "automatic_high_confidence",
    "automatic_quarantine",
    "manual_match",
    "manual_rejection",
]


@dataclass(frozen=True, slots=True)
class ProductMatch:
    """Auditable outcome of matching a listing to the product master."""

    listing_id: str
    status: MatchStatus
    confidence: float
    runner_up_confidence: float | None
    product_id: int | str | None
    sku_code: str | None
    reason: str
    suggested_product_id: int | str | None = None
    suggested_sku_code: str | None = None
    provenance: MatchProvenance = "automatic"
    algorithm_status: MatchStatus | None = None
    algorithm_reason: str | None = None
    decision_source: str | None = None
    reviewer: str | None = None
    reviewed_on: date | None = None
    review_note: str | None = None

    @property
    def matched(self) -> bool:
        return self.status == "matched"


class PageSource(Protocol):
    """Minimal source interface used by the collector."""

    def read_text(self, path: str) -> str:
        """Read an allowed site path as text."""


def _collapse_space(value: str) -> str:
    return " ".join(value.split())


def _ascii_text(value: str) -> str:
    decoded = html_lib.unescape(value)
    normalized = unicodedata.normalize("NFKD", decoded)
    return "".join(character for character in normalized if not unicodedata.combining(character))


def normalize_title(value: str) -> str:
    """Normalize listing/product titles while removing non-identifying sales copy."""

    text = _ascii_text(value).casefold()
    text = re.sub(r"\bamrit\s*valley\b", "amrit valley", text)
    text = re.sub(r"\bkestrel\s+sel\.?\b", "kestrel select", text)
    marketing_patterns = (
        r"\bpack\s+of\s+\d+\b",
        r"\bcombo\b",
        r"\bfamily\s+pack\b",
        r"\bbest\s+before\s+\w+\b",
        r"\bnew\b",
    )
    for pattern in marketing_patterns:
        text = re.sub(pattern, " ", text)
    text = _PACK_RE.sub(" ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return _collapse_space(text)


def normalize_brand(value: str | None) -> str | None:
    """Canonicalize a product-master brand value."""

    if not value:
        return None
    normalized = normalize_title(value)
    for alias, canonical in sorted(
        DEFAULT_BRAND_ALIASES.items(), key=lambda item: len(item[0]), reverse=True
    ):
        if normalized == normalize_title(alias):
            return canonical
    return _collapse_space(_ascii_text(value)).title()


def infer_brand(
    title: str,
    aliases: Mapping[str, str] = DEFAULT_BRAND_ALIASES,
) -> str | None:
    """Infer a canonical brand from a normalized title using explicit aliases."""

    normalized = f" {normalize_title(title)} "
    ordered = sorted(aliases.items(), key=lambda item: len(normalize_title(item[0])), reverse=True)
    for alias, canonical in ordered:
        token = normalize_title(alias)
        if f" {token} " in normalized or normalized.strip().startswith(f"{token} "):
            return canonical
    return None


def normalize_category(value: str) -> str:
    """Canonicalize category whitespace and known capitalization."""

    cleaned = _collapse_space(_ascii_text(value)).casefold()
    return _CATEGORY_ALIASES.get(cleaned, cleaned.title())


def normalize_city(value: str) -> str:
    """Canonicalize city variants shared by the site and Kestrel master data."""

    cleaned = _collapse_space(_ascii_text(value)).casefold()
    return _CITY_ALIASES.get(cleaned, cleaned.title())


def normalize_uom(value: str | None) -> str | None:
    if not value:
        return None
    return _UOM_ALIASES.get(value.strip().casefold())


def parse_pack(value: str) -> tuple[float | None, str | None]:
    """Extract and canonicalize a pack value and unit from free text."""

    match = _PACK_RE.search(_ascii_text(value))
    if not match:
        return None, None
    number = float(match.group("value").replace(",", "."))
    return number, normalize_uom(match.group("uom"))


def normalize_availability(value: str) -> bool | None:
    """Map BazaarPulse availability wording to true, false, or unknown."""

    text = _collapse_space(_ascii_text(value)).casefold()
    if any(marker in text for marker in ("currently unavailable", "out of stock", "sold out")):
        return False
    if any(marker in text for marker in ("in stock", "available")):
        return True
    return None


def _parse_money(value: str) -> float | None:
    match = _MONEY_RE.search(_ascii_text(value))
    return float(match.group(1).replace(",", "")) if match else None


def _normalized_path(value: str) -> str:
    parsed = urlsplit(value)
    decoded = unquote(parsed.path)
    normalized = posixpath.normpath("/" + decoded.lstrip("/"))
    return normalized if normalized.startswith("/") else f"/{normalized}"


def is_allowed_path(path: str) -> bool:
    """Return false for paths disallowed by the supplied BazaarPulse robots policy."""

    normalized = _normalized_path(path).casefold()
    return not any(
        normalized == prefix or normalized.startswith(f"{prefix}/")
        for prefix in DISALLOWED_PREFIXES
    )


def assert_allowed_path(path: str) -> str:
    """Normalize a path and reject it before any filesystem or network access."""

    normalized = _normalized_path(path)
    if not is_allowed_path(normalized):
        raise DisallowedPathError(f"BazaarPulse path is disallowed by robots policy: {normalized}")
    return normalized


def _resolve_site_href(current_path: str, href: str) -> str | None:
    base = f"https://bazaarpulse.invalid{_normalized_path(current_path)}"
    resolved = urlsplit(urljoin(base, href))
    if resolved.netloc != "bazaarpulse.invalid":
        return None
    return _normalized_path(resolved.path)


def discover_pagination_paths(page_html: str, current_path: str) -> tuple[str, ...]:
    """Find both ``page/N.html`` and ``index_pN.html`` pagination conventions."""

    soup = BeautifulSoup(page_html, "html.parser")
    discovered: list[str] = []
    for anchor in soup.select(".pager a[href]"):
        href = str(anchor.get("href", ""))
        base = f"https://bazaarpulse.invalid{_normalized_path(current_path)}"
        resolved_url = urlsplit(urljoin(base, href))
        if resolved_url.netloc != "bazaarpulse.invalid":
            continue
        resolved = _normalized_path(resolved_url.path)

        # Bengaluru and Chennai advertise ``index.html?p=N`` but the static server
        # stores those pages as ``index_pN.html`` (documented in PAGINATION.txt).
        query_page = parse_qs(resolved_url.query).get("p", [None])[0]
        if resolved.casefold().endswith("/index.html") and query_page and query_page.isdigit():
            page_number = int(query_page)
            if page_number > 1:
                resolved = resolved[: -len("index.html")] + f"index_p{page_number}.html"
        if resolved and _LISTING_PAGE_RE.fullmatch(resolved) and is_allowed_path(resolved):
            discovered.append(resolved)
    return tuple(dict.fromkeys(discovered))


def _page_city(soup: BeautifulSoup, fallback: str | None) -> str:
    breadcrumb = soup.select_one(".wrap > p.muted")
    if breadcrumb:
        parts = [part.strip() for part in breadcrumb.get_text(" ", strip=True).split("/")]
        if len(parts) >= 2 and parts[1]:
            return normalize_city(parts[1])
    return normalize_city(fallback or "Unknown")


def _detail_path(card: Tag, current_path: str) -> str | None:
    anchor = card.find("a", href=True)
    if not anchor:
        return None
    resolved = _resolve_site_href(current_path, str(anchor.get("href")))
    return (
        resolved
        if resolved and _PRODUCT_DETAIL_RE.fullmatch(resolved) and is_allowed_path(resolved)
        else None
    )


def _listing_id(card: Tag, detail_path: str | None) -> str | None:
    explicit = card.get("data-listing-id")
    if explicit:
        return str(explicit).strip()
    if detail_path:
        match = re.search(r"/product/(\d+)\.html$", detail_path)
        if match:
            return match.group(1)
    return None


def _current_price(card: Tag) -> float | None:
    plain_price = card.select_one(".price, .sellingPrice, .amt")
    if plain_price:
        return _parse_money(plain_price.get_text(" ", strip=True))

    paise_price = card.select_one(".pricing-block[data-price-paise]")
    if paise_price:
        raw_paise = str(paise_price.get("data-price-paise", "")).replace(",", "")
        if raw_paise.isdigit():
            return int(raw_paise) / 100
    return None


def parse_listing_page(
    page_html: str,
    source_path: str,
    *,
    default_city: str | None = None,
    brand_aliases: Mapping[str, str] = DEFAULT_BRAND_ALIASES,
) -> ParsedListingPage:
    """Parse a BazaarPulse city page without following product-detail links."""

    normalized_source = assert_allowed_path(source_path)
    soup = BeautifulSoup(page_html, "html.parser")
    city = _page_city(soup, default_city)
    listings: list[Listing] = []

    for card in soup.select(".product-item"):
        detail_path = _detail_path(card, normalized_source)
        listing_id = _listing_id(card, detail_path)
        title_anchor = card.find("a", href=True)
        if not listing_id or not title_anchor:
            continue

        raw_title = _collapse_space(title_anchor.get_text(" ", strip=True))
        muted = [node.get_text(" ", strip=True) for node in card.select(".muted")]
        metadata = [part.strip() for part in muted[0].split("·")] if muted else []
        retailer = _collapse_space(metadata[0]) if metadata else "Unknown"
        pack_value, pack_uom = parse_pack(metadata[1] if len(metadata) > 1 else raw_title)
        category = normalize_category(metadata[2]) if len(metadata) > 2 else "Unknown"
        card_text = " ".join(muted)
        mrp_match = _MRP_RE.search(_ascii_text(card_text))
        last_seen_match = _LAST_SEEN_RE.search(_ascii_text(card_text))

        listings.append(
            Listing(
                listing_id=listing_id,
                city=city,
                retailer=retailer,
                raw_title=raw_title,
                normalized_title=normalize_title(raw_title),
                brand=infer_brand(raw_title, brand_aliases),
                pack_value=pack_value,
                pack_uom=pack_uom,
                category=category,
                current_price_inr=_current_price(card),
                mrp_inr=float(mrp_match.group(1).replace(",", "")) if mrp_match else None,
                is_available=normalize_availability(card_text),
                last_seen=date.fromisoformat(last_seen_match.group(1)) if last_seen_match else None,
                detail_path=detail_path,
                source_path=normalized_source,
            )
        )

    return ParsedListingPage(
        city=city,
        listings=tuple(listings),
        pagination_paths=discover_pagination_paths(page_html, normalized_source),
    )


def _detail_metadata(soup: BeautifulSoup) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for node in soup.select(".card p.muted"):
        for part in node.get_text(" ", strip=True).split("·"):
            label, separator, value = part.partition(":")
            if separator and label.strip() and value.strip():
                metadata[label.strip().casefold()] = _collapse_space(value)
    return metadata


def _detail_category(soup: BeautifulSoup) -> str:
    breadcrumb = soup.select_one(".wrap > p.muted")
    if not breadcrumb:
        return "Unknown"
    parts = [part.strip() for part in breadcrumb.get_text(" ", strip=True).split("/")]
    return normalize_category(parts[2]) if len(parts) >= 3 and parts[2] else "Unknown"


def _price_history_table(soup: BeautifulSoup) -> Tag | None:
    for heading in soup.find_all(["h2", "h3", "h4"]):
        if normalize_title(heading.get_text(" ", strip=True)) == "observed price history":
            table = heading.find_next_sibling("table") or heading.find_next("table")
            return table if isinstance(table, Tag) else None
    return None


def parse_product_detail(
    page_html: str,
    source_path: str,
    *,
    expected_listing: Listing | None = None,
) -> ParsedProductDetail:
    """Parse and validate weekly source history from one allowed product detail page."""

    normalized_source = assert_allowed_path(source_path)
    path_match = _PRODUCT_DETAIL_RE.fullmatch(normalized_source)
    if not path_match:
        raise ValueError(f"Not a BazaarPulse product detail path: {normalized_source}")
    listing_id = path_match.group("listing_id")
    soup = BeautifulSoup(page_html, "html.parser")
    title = soup.select_one(".card h2")
    if not title:
        raise ValueError(f"BazaarPulse detail page has no product title: {normalized_source}")
    raw_title = _collapse_space(title.get_text(" ", strip=True))
    metadata = _detail_metadata(soup)
    retailer = _collapse_space(metadata.get("retailer", ""))
    city = normalize_city(metadata.get("city", ""))
    category = _detail_category(soup)
    pack_value, pack_uom = parse_pack(metadata.get("pack", ""))
    if not retailer or not metadata.get("city"):
        raise ValueError(
            f"BazaarPulse detail page is missing retailer or city identity: {normalized_source}"
        )

    if expected_listing is not None:
        identity_errors: list[str] = []
        if listing_id != expected_listing.listing_id:
            identity_errors.append("listing ID")
        if city != expected_listing.city:
            identity_errors.append("city")
        if retailer.casefold() != expected_listing.retailer.casefold():
            identity_errors.append("retailer")
        if normalize_title(raw_title) != expected_listing.normalized_title:
            identity_errors.append("title")
        expected_pack = _base_pack(expected_listing.pack_value, expected_listing.pack_uom)
        detail_pack = _base_pack(pack_value, pack_uom)
        if expected_pack is not None and detail_pack is not None and expected_pack != detail_pack:
            identity_errors.append("pack")
        if identity_errors:
            raise ValueError(
                "BazaarPulse detail identity disagrees with its listing card "
                f"({', '.join(identity_errors)}): {normalized_source}"
            )

    history_table = _price_history_table(soup)
    if history_table is None:
        raise ValueError(
            f"BazaarPulse detail page has no observed-price history table: {normalized_source}"
        )
    rows = history_table.find_all("tr")
    if not rows:
        raise ValueError(f"BazaarPulse price-history table is empty: {normalized_source}")
    headers = [
        normalize_title(cell.get_text(" ", strip=True))
        for cell in rows[0].find_all(["th", "td"])
    ]
    try:
        observed_index = headers.index("observed on")
        price_index = headers.index("price")
    except ValueError as error:
        raise ValueError(
            f"BazaarPulse price-history headers are unsupported: {normalized_source}"
        ) from error

    by_date: dict[date, SourcePriceObservation] = {}
    for row in rows[1:]:
        cells = row.find_all("td")
        if not cells:
            continue
        if max(observed_index, price_index) >= len(cells):
            raise ValueError(f"Malformed BazaarPulse price-history row: {normalized_source}")
        try:
            observed_on = date.fromisoformat(cells[observed_index].get_text(" ", strip=True))
        except ValueError as error:
            raise ValueError(
                f"Invalid BazaarPulse observation date: {normalized_source}"
            ) from error
        price_inr = _parse_money(cells[price_index].get_text(" ", strip=True))
        if price_inr is None or price_inr <= 0:
            raise ValueError(f"Invalid BazaarPulse observed price: {normalized_source}")
        observation = SourcePriceObservation(
            listing_id=listing_id,
            city=city,
            retailer=retailer,
            raw_title=raw_title,
            category=category,
            pack_value=pack_value,
            pack_uom=pack_uom,
            observed_on=observed_on,
            price_inr=price_inr,
            source_path=normalized_source,
        )
        existing = by_date.get(observed_on)
        if existing is not None and existing != observation:
            raise ValueError(
                "BazaarPulse detail page contains conflicting prices for the same listing/date: "
                f"{normalized_source} {observed_on}"
            )
        by_date[observed_on] = observation

    return ParsedProductDetail(
        listing_id=listing_id,
        city=city,
        retailer=retailer,
        raw_title=raw_title,
        category=category,
        pack_value=pack_value,
        pack_uom=pack_uom,
        source_price_observations=tuple(by_date[item] for item in sorted(by_date)),
    )


@dataclass(slots=True)
class LocalSiteSource:
    """Read an unpacked BazaarPulse site without network access or artificial delays."""

    root: Path

    def __post_init__(self) -> None:
        self.root = self.root.expanduser().resolve()

    def read_text(self, path: str) -> str:
        normalized = assert_allowed_path(path)
        target = (self.root / normalized.lstrip("/")).resolve()
        if not target.is_relative_to(self.root):
            raise DisallowedPathError(f"Path escapes BazaarPulse site root: {path}")
        return target.read_text(encoding="utf-8")


@dataclass(slots=True)
class HttpSiteSource:
    """HTTP source that enforces a minimum interval between request starts."""

    base_url: str
    crawl_delay_seconds: float = 1.0
    client: httpx.Client | None = None
    sleep_fn: Callable[[float], None] = time.sleep
    clock_fn: Callable[[], float] = time.monotonic
    _last_request_at: float | None = field(default=None, init=False)
    _owns_client: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if self.crawl_delay_seconds < 0:
            raise ValueError("crawl_delay_seconds cannot be negative")
        self.base_url = self.base_url.rstrip("/") + "/"
        if self.client is None:
            self.client = httpx.Client(
                follow_redirects=True,
                timeout=httpx.Timeout(15.0),
                headers={"User-Agent": "KestrelControlTower/1.0 (+local assessment)"},
            )
            self._owns_client = True

    def _wait_for_crawl_slot(self) -> None:
        now = self.clock_fn()
        if self._last_request_at is not None:
            remaining = self.crawl_delay_seconds - (now - self._last_request_at)
            if remaining > 0:
                self.sleep_fn(remaining)
                now = self.clock_fn()
        self._last_request_at = now

    def read_text(self, path: str) -> str:
        normalized = assert_allowed_path(path)
        self._wait_for_crawl_slot()
        assert self.client is not None
        response = self.client.get(urljoin(self.base_url, normalized.lstrip("/")))
        response.raise_for_status()
        return response.text

    def close(self) -> None:
        if self._owns_client and self.client is not None:
            self.client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def deduplicate_listings(listings: Iterable[Listing]) -> list[Listing]:
    """Dedupe pagination overlap by ID, preferring the newest observation."""

    by_id: dict[str, Listing] = {}
    for listing in listings:
        existing = by_id.get(listing.listing_id)
        if existing is None:
            by_id[listing.listing_id] = listing
            continue
        existing_date = existing.last_seen or date.min
        candidate_date = listing.last_seen or date.min
        if candidate_date > existing_date:
            by_id[listing.listing_id] = listing

    def sort_key(item: Listing) -> tuple[int, int | str]:
        return (0, int(item.listing_id)) if item.listing_id.isdigit() else (1, item.listing_id)

    return sorted(by_id.values(), key=sort_key)


@dataclass(slots=True)
class BazaarPulseCollector:
    """Traverse allowed city/detail pages and optionally persist a last-good cache."""

    source: PageSource
    cache_path: Path | None = None
    max_pages: int = 500
    max_detail_pages: int = 2_000
    max_detail_failures: int = 25

    @classmethod
    def from_local(
        cls,
        site_root: str | Path,
        *,
        cache_path: str | Path | None = None,
        max_pages: int = 500,
        max_detail_pages: int = 2_000,
        max_detail_failures: int = 25,
    ) -> Self:
        return cls(
            source=LocalSiteSource(Path(site_root)),
            cache_path=Path(cache_path) if cache_path else None,
            max_pages=max_pages,
            max_detail_pages=max_detail_pages,
            max_detail_failures=max_detail_failures,
        )

    @classmethod
    def from_http(
        cls,
        base_url: str,
        *,
        crawl_delay_seconds: float = 1.0,
        cache_path: str | Path | None = None,
        max_pages: int = 500,
        max_detail_pages: int = 2_000,
        max_detail_failures: int = 25,
        client: httpx.Client | None = None,
    ) -> Self:
        return cls(
            source=HttpSiteSource(
                base_url=base_url,
                crawl_delay_seconds=crawl_delay_seconds,
                client=client,
            ),
            cache_path=Path(cache_path) if cache_path else None,
            max_pages=max_pages,
            max_detail_pages=max_detail_pages,
            max_detail_failures=max_detail_failures,
        )

    def _collect_listings(self, entry_paths: Sequence[str]) -> list[Listing]:
        queue = deque(assert_allowed_path(path) for path in entry_paths)
        queued = set(queue)
        visited: set[str] = set()
        collected: list[Listing] = []

        while queue:
            if len(visited) >= self.max_pages:
                raise CollectionLimitError(
                    f"BazaarPulse crawl exceeded the {self.max_pages}-page safety limit"
                )
            path = queue.popleft()
            if path in visited:
                continue
            visited.add(path)
            parsed = parse_listing_page(self.source.read_text(path), path)
            collected.extend(parsed.listings)
            for next_path in parsed.pagination_paths:
                if next_path not in visited and next_path not in queued:
                    queue.append(next_path)
                    queued.add(next_path)

        return deduplicate_listings(collected)

    def collect(self, entry_paths: Sequence[str] = DEFAULT_ENTRY_PATHS) -> list[Listing]:
        """Collect current listing pages only (the backward-compatible fast path)."""

        result = self._collect_listings(entry_paths)
        if self.cache_path:
            write_listings_cache(self.cache_path, result)
        return result

    def collect_snapshot(
        self,
        entry_paths: Sequence[str] = DEFAULT_ENTRY_PATHS,
    ) -> BazaarPulseSnapshot:
        """Collect current listings and all linked, allowed detail-page source history."""

        listings = self._collect_listings(entry_paths)
        detail_listings = [listing for listing in listings if listing.detail_path]
        if len(detail_listings) > self.max_detail_pages:
            raise CollectionLimitError(
                "BazaarPulse detail crawl exceeded the "
                f"{self.max_detail_pages}-page safety limit"
            )
        observations: list[SourcePriceObservation] = []
        failures: list[SourceDetailFailure] = []
        for listing in detail_listings:
            assert listing.detail_path is not None
            try:
                detail_html = self.source.read_text(listing.detail_path)
                parsed = parse_product_detail(
                    detail_html,
                    listing.detail_path,
                    expected_listing=listing,
                )
            except FileNotFoundError:
                failures.append(
                    SourceDetailFailure(
                        listing_id=listing.listing_id,
                        source_path=listing.detail_path,
                        failure_type="missing_detail_page",
                        message="Linked product detail page is absent from the supplied site.",
                    )
                )
                if len(failures) > self.max_detail_failures:
                    raise CollectionLimitError(
                        "BazaarPulse detail failures exceeded the "
                        f"{self.max_detail_failures}-page safety limit; latest source_path="
                        f"{listing.detail_path}"
                    ) from None
                continue
            except httpx.HTTPStatusError as error:
                if error.response.status_code != 404:
                    raise
                failures.append(
                    SourceDetailFailure(
                        listing_id=listing.listing_id,
                        source_path=listing.detail_path,
                        failure_type="http_404",
                        message="Linked product detail page returned HTTP 404.",
                    )
                )
                if len(failures) > self.max_detail_failures:
                    raise CollectionLimitError(
                        "BazaarPulse detail failures exceeded the "
                        f"{self.max_detail_failures}-page safety limit; latest source_path="
                        f"{listing.detail_path}"
                    ) from error
                continue
            except ValueError as error:
                failures.append(
                    SourceDetailFailure(
                        listing_id=listing.listing_id,
                        source_path=listing.detail_path,
                        failure_type="parse_error",
                        message=str(error),
                    )
                )
                if len(failures) > self.max_detail_failures:
                    raise CollectionLimitError(
                        "BazaarPulse detail failures exceeded the "
                        f"{self.max_detail_failures}-page safety limit; latest source_path="
                        f"{listing.detail_path}; parser_error={error}"
                    ) from error
                continue
            observations.extend(parsed.source_price_observations)
        snapshot = BazaarPulseSnapshot(
            tuple(listings),
            tuple(observations),
            tuple(failures),
        )
        if self.cache_path:
            write_bazaarpulse_cache(self.cache_path, snapshot)
        return snapshot


def _write_json_atomically(path: str | Path, payload: Mapping[str, object]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            json.dump(payload, temporary, ensure_ascii=False, indent=2, sort_keys=True)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, destination)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def write_listings_cache(path: str | Path, listings: Sequence[Listing]) -> None:
    """Atomically replace the JSON cache so a failed refresh preserves last-good data."""

    payload = {
        "schema_version": 1,
        "collected_at_utc": datetime.now(UTC).isoformat(),
        "count": len(listings),
        "listings": [listing.to_dict() for listing in listings],
    }
    _write_json_atomically(path, payload)


def write_bazaarpulse_cache(
    path: str | Path,
    snapshot: BazaarPulseSnapshot,
) -> None:
    """Atomically cache both current listings and source-dated price history."""

    payload = {
        "schema_version": 2,
        "collected_at_utc": datetime.now(UTC).isoformat(),
        "count": len(snapshot.listings),
        "listing_count": len(snapshot.listings),
        "source_price_observation_count": len(snapshot.source_price_observations),
        "source_detail_failure_count": len(snapshot.source_detail_failures),
        "listings": [listing.to_dict() for listing in snapshot.listings],
        "source_price_observations": [
            observation.to_dict() for observation in snapshot.source_price_observations
        ],
        "source_detail_failures": [
            failure.to_dict() for failure in snapshot.source_detail_failures
        ],
    }
    _write_json_atomically(path, payload)


def read_bazaarpulse_cache(path: str | Path) -> BazaarPulseSnapshot:
    """Read a full cache, accepting legacy listing-only schema version 1."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    schema_version = payload.get("schema_version")
    if schema_version not in (1, 2):
        raise ValueError("Unsupported BazaarPulse cache schema")
    listing_records = payload.get("listings")
    if not isinstance(listing_records, list):
        raise ValueError("BazaarPulse cache is missing its listings array")
    source_records = payload.get("source_price_observations", [])
    if not isinstance(source_records, list):
        raise ValueError("BazaarPulse cache has invalid source price observations")
    failure_records = payload.get("source_detail_failures", [])
    if not isinstance(failure_records, list):
        raise ValueError("BazaarPulse cache has invalid source detail failures")
    return BazaarPulseSnapshot(
        listings=tuple(Listing.from_dict(record) for record in listing_records),
        source_price_observations=tuple(
            SourcePriceObservation.from_dict(record) for record in source_records
        ),
        source_detail_failures=tuple(
            SourceDetailFailure.from_dict(record) for record in failure_records
        ),
    )


def read_listings_cache(path: str | Path) -> list[Listing]:
    """Read a cache created by :func:`write_listings_cache`."""

    return list(read_bazaarpulse_cache(path).listings)


def _token_similarity(left: str, right: str) -> float:
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if not left_tokens or not right_tokens:
        return 0.0
    jaccard = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    sequence = SequenceMatcher(None, left.replace(" ", ""), right.replace(" ", "")).ratio()
    return max(jaccard, sequence)


def _base_pack(value: float | None, uom: str | None) -> tuple[float, str] | None:
    normalized_uom = normalize_uom(uom)
    if value is None or normalized_uom is None:
        return None
    if normalized_uom == "KG":
        return value * 1000, "G"
    if normalized_uom == "L":
        return value * 1000, "ML"
    return value, normalized_uom


def _pack_matches(listing: Listing, product: ProductCandidate) -> bool | None:
    listing_pack = _base_pack(listing.pack_value, listing.pack_uom)
    product_pack = _base_pack(product.pack_size_value, product.pack_size_uom)
    if listing_pack is None or product_pack is None:
        return None
    return listing_pack[1] == product_pack[1] and abs(listing_pack[0] - product_pack[0]) < 1e-6


def product_identity_conflicts(
    listing: Listing, product: ProductCandidate
) -> tuple[str, ...]:
    """Return hard identity conflicts that a manual decision may not override.

    Product titles can be noisy, but a known brand or normalized pack disagreement is strong
    evidence that two records represent different products.  Keeping this guard separate from
    the fuzzy score ensures a reviewed mapping cannot silently force a weak entity link.
    """

    conflicts: list[str] = []
    listing_brand = normalize_brand(listing.brand)
    product_brand = normalize_brand(product.brand)
    if listing_brand and product_brand and listing_brand != product_brand:
        conflicts.append(f"brand differs ({listing_brand} vs {product_brand})")
    if _pack_matches(listing, product) is False:
        conflicts.append("normalized pack size or unit differs")
    return tuple(conflicts)


def score_product_candidate(listing: Listing, product: ProductCandidate) -> float:
    """Score title, brand, pack, and category agreement on a zero-to-one scale."""

    product_title = normalize_title(product.product_name)
    components: list[tuple[float, float]] = [
        (0.65, _token_similarity(listing.normalized_title, product_title))
    ]

    listing_brand = normalize_brand(listing.brand)
    product_brand = normalize_brand(product.brand)
    if listing_brand and product_brand:
        components.append((0.15, float(listing_brand == product_brand)))

    pack_match = _pack_matches(listing, product)
    if pack_match is not None:
        components.append((0.15, float(pack_match)))

    if listing.category and product.category:
        components.append(
            (
                0.05,
                float(normalize_category(listing.category) == normalize_category(product.category)),
            )
        )

    weighted_score = sum(weight * score for weight, score in components)
    available_weight = sum(weight for weight, _ in components)
    score = weighted_score / available_weight

    # Known pack disagreement is a strong entity-key conflict, irrespective of name similarity.
    if pack_match is False:
        score = min(score, 0.70)
    return round(max(0.0, min(1.0, score)), 6)


def match_listing_to_products(
    listing: Listing,
    products: Sequence[ProductCandidate],
    *,
    minimum_confidence: float = 0.86,
    ambiguity_margin: float = 0.06,
) -> ProductMatch:
    """Return a match only when the best candidate is strong and clearly separated."""

    if not 0 <= minimum_confidence <= 1:
        raise ValueError("minimum_confidence must be between zero and one")
    if not 0 <= ambiguity_margin <= 1:
        raise ValueError("ambiguity_margin must be between zero and one")
    if not products:
        return ProductMatch(
            listing_id=listing.listing_id,
            status="no_candidates",
            confidence=0.0,
            runner_up_confidence=None,
            product_id=None,
            sku_code=None,
            reason="No product candidates were supplied.",
            provenance="automatic_quarantine",
            algorithm_status="no_candidates",
        )

    ranked = sorted(
        ((score_product_candidate(listing, product), product) for product in products),
        key=lambda item: (-item[0], str(item[1].sku_code)),
    )
    confidence, best = ranked[0]
    runner_up = ranked[1][0] if len(ranked) > 1 else None

    if confidence < minimum_confidence:
        return ProductMatch(
            listing_id=listing.listing_id,
            status="low_confidence",
            confidence=confidence,
            runner_up_confidence=runner_up,
            product_id=None,
            sku_code=None,
            reason=(
                f"Best candidate {best.sku_code} scored {confidence:.3f}, below the "
                f"{minimum_confidence:.3f} acceptance threshold."
            ),
            suggested_product_id=best.product_id,
            suggested_sku_code=best.sku_code,
            provenance="automatic_quarantine",
            algorithm_status="low_confidence",
        )
    if runner_up is not None and confidence - runner_up < ambiguity_margin:
        return ProductMatch(
            listing_id=listing.listing_id,
            status="ambiguous",
            confidence=confidence,
            runner_up_confidence=runner_up,
            product_id=None,
            sku_code=None,
            reason=(
                f"Top candidates are separated by {confidence - runner_up:.3f}, below the "
                f"{ambiguity_margin:.3f} ambiguity margin."
            ),
            suggested_product_id=best.product_id,
            suggested_sku_code=best.sku_code,
            provenance="automatic_quarantine",
            algorithm_status="ambiguous",
        )
    return ProductMatch(
        listing_id=listing.listing_id,
        status="matched",
        confidence=confidence,
        runner_up_confidence=runner_up,
        product_id=best.product_id,
        sku_code=best.sku_code,
        reason="Best candidate cleared both confidence and ambiguity thresholds.",
        suggested_product_id=best.product_id,
        suggested_sku_code=best.sku_code,
        provenance="automatic_high_confidence",
        algorithm_status="matched",
    )


def match_listings_to_products(
    listings: Sequence[Listing],
    products: Sequence[ProductCandidate],
    *,
    minimum_confidence: float = 0.86,
    ambiguity_margin: float = 0.06,
) -> list[ProductMatch]:
    """Match a listing collection using a single governed threshold policy."""

    return [
        match_listing_to_products(
            listing,
            products,
            minimum_confidence=minimum_confidence,
            ambiguity_margin=ambiguity_margin,
        )
        for listing in listings
    ]
