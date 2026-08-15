# Freight history and optional context

External sources are explicit refresh jobs. None is contacted while the dashboard starts, and a
missing cache leaves core operational metrics available.

## Freight history

`kestrel sync-freight` now requests the complete period supplied by the mock carrier service:
`2025-01-01` through `2026-06-30`. The existing date flags retain a smaller selectable refresh:

```bash
# Complete supplied invoice history (the default)
make sync-freight

# FY 2026-27 Q1 only; this range is upserted into the last-good cache
kestrel sync-freight --from 2026-04-01 --to 2026-06-30
```

The client follows every cursor, validates each typed invoice, retries `429`, `503`, and transport
failures, persists a resumable checkpoint, and promotes the cache only when the cursor terminates.
The requested range applies to `invoice_date`. A service date can precede it by up to three days.

Verification against the supplied server on 2026-08-15 produced 41,500 unique invoices across 208
pages, 237 HTTP requests, and 29 retries. Requested invoice coverage was 2025-01-01 through
2026-06-30; observed service-date coverage was 2024-12-29 through 2026-06-30. These generated
records remain under `.kestrel/` and are not committed.

The primary dashboard ratio uses PAID invoice amount plus detention as settled freight per delivered
case-equivalent. All-status billed, pending, and disputed values remain explicit secondary evidence.
Invoice numerators and delivered-case denominators aggregate independently by shared service/actual
period and DC or route because no invoice-to-delivery key exists.

## BazaarPulse current and source-dated price evidence

`kestrel scrape-prices` traverses the allowed current listing pages and supplied product-detail
pages. The verified 2026-08-15 snapshot contains 1,137 current listings: 1,088 governed matches and
49 review-queue rows. Detail pages contribute 6,804 immutable source-price observations for 1,134
listings covering 2026-05-06 through 2026-06-30. Missing detail pages for IDs `387`, `458`, and
`777` are recorded as structured warnings.

Current listing/match history records what a scrape published; source-dated detail history records
the business observation dates embedded in the supplied experience. Historical comparison resolves
the Kestrel MRP effective on each observation date. Raw shelf price and pack remain evidence, while
comparable mass/volume packs also expose 100G/100ML normalized values; non-comparable packs remain
null. These are weekly observations, not live prices.

## Optional weather and holidays

Run both isolated refreshes with:

```bash
kestrel sync-context

# Publish validated local caches without making a network request
kestrel sync-context --offline-cache
```

Weather comes from [Open-Meteo's historical archive](https://archive-api.open-meteo.com/). One
daily record is collected for each warehouse-city centroid. The coordinates are proxies for the
warehouse cities in the supplied master data; they are not observations along a route or at an
outlet.

Indian holidays first use the Nager.Date endpoint named in the assignment pack. At verification
time, Nager.Date returned HTTP `204` and did not list `IN` among its supported countries. The
adapter therefore falls back to Google's public "Holidays in India" iCalendar feed and retains
only events explicitly classified as `Public holiday`; observances are excluded. The actual
provider URL is recorded in cache metadata. This is a national context flag, not evidence that a
particular state, customer, or warehouse was closed.

The two sources have independent typed JSON caches:

- `.kestrel/weather_daily.json`
- `.kestrel/india_holidays.json`

A source is written atomically only after its full requested range validates. A weather error does
not block the holiday attempt, a holiday error does not block weather, and neither error modifies
the last-good analytical snapshot. Each cache records source URL, requested and observed coverage,
collection timestamp, record and expected-record counts, location coverage, request count, retry
count, and completeness.

The 2026-08-15 refresh produced 4,368 weather rows (8 warehouses × 546 days) and 27 nationally
classified public-holiday rows for 2025-01-01 through 2026-06-30.

## Association publication gate

`ContextAnalyticsService` exposes descriptive weather and holiday comparisons. It withholds the
result unless all applicable checks pass:

1. A typed snapshot and successful sync record both exist.
2. The selected analysis period is entirely inside source coverage.
3. The cache is no older than 365 days.
4. Weather contains every expected warehouse-day and every configured warehouse.
5. At least 95% of eligible operational rows join to weather at
   `delivery_date × warehouse_code`.
6. Both comparison cohorts contain at least 30 eligible orders.

The weather comparison uses `rainy_day` (`precipitation_sum_mm >= 1`) versus
`little_or_no_rain`. The holiday comparison uses national public-holiday versus non-holiday
requested-delivery dates. Both report observed late rate, chilled temperature-excursion rate, and
weighted eaches fill rate.

Every result carries this interpretation boundary: **descriptive association only; no causal
effect is estimated**. Weather is a city-centroid proxy, holidays are national calendar context,
and unobserved operational factors may explain any difference.
