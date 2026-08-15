# Data Quality Register and Metric Contracts

## Purpose and evidence standard

This register records issues that can materially change a Kestrel KPI. It is intended to be read in an interview alongside the code and tests: each issue states the evidence, business impact, and chosen handling rather than implying that the raw data was “fixed.”

Evidence was gathered read-only from the supplied SQLite database, CSV headers, the deterministic partner API implementation, BazaarPulse methodology, and `robots.txt`. The data dictionary is treated as a hypothesis; where it conflicts with the supplied data, the data wins.

### Severity

- **Critical** — can make a headline KPI materially false or financially mis-scaled.
- **High** — can substantially bias a major metric or dimension.
- **Medium** — affects a segment, drill-down, or interpretation but has a safe workaround.
- **Low** — documentation or maintainability risk with limited immediate KPI impact.

## Verified issue register

| ID | Severity | Verified evidence | Metric impact | Chosen handling |
|---|---|---|---|---|
| DQ-001 | Critical | `order_lines.qty_uom` contains 398,741 `CASE` and 112,775 `EACH` rows. All 511,516 rows have a valid positive `case_pack_at_order`. | Directly summing raw quantities produces meaningless fill, short, dispatch, return, and freight-per-case metrics. | Create ordered/delivered eaches and case-equivalents at line grain. For `CASE`, eaches = quantity × `case_pack_at_order`; for `EACH`, eaches = quantity and case-equivalents = quantity ÷ `case_pack_at_order`. Use order-time, never current, case pack. |
| DQ-002 | High | Every one of 511,516 lines has `delivered_qty < ordered_qty`; none is exactly or over delivered. Under the active-outlet delivered/partial screen, all 66,405 eligible orders also contain at least one short line. | Strict in-full is always false, so strict OTIF is 0% at every aggregation. A tolerance would change the business definition rather than repair data. | Preserve strict line and order logic; do not invent 95/98/99% tolerance. Show the 0% KPI with an explicit data caveat and show fill rate and on-time separately. See “OTIF conflict” below. |
| DQ-003 | High | Orders include 65,896 `DELIVERED`, 10,993 `PARTIAL`, 5,066 `CANCELLED`, and 1,716 `OPEN`. Outlets include 627 active, 55 closed, and 42 deleted rows, plus three active test/dummy outlets (`TST00001`–`TST00003`). | Open/cancelled demand and invalid customers can depress service metrics and dominate worst-performer lists. | Base completed-service KPIs on delivered/partial orders. Exclude deleted and test/dummy outlets. Current-performance views exclude currently closed outlets; historical views may include pre-closure orders only when the selected contract explicitly uses `closed_date`. Display the eligibility rule with the metric. |
| DQ-004 | Medium | A verified ownership-transfer duplicate exists for GST `26ABCDE3218F1Z2` (`OUT00018` and `OUT90118`); both rows are deleted. Three test rows also reuse another GST. Common outlet names are highly non-unique and are not reliable deduplication keys. | Naive name/GST deduplication can merge legitimate customers; failure to filter invalid rows can duplicate customer counts. | Retain `outlet_id` as the fact join key. Exclude invalid/test rows through the eligibility flag. Never deduplicate on display name alone; surface business-key collisions as quality exceptions. |
| DQ-005 | Medium | Outlet cities include `Bangalore` (16) and `Bengaluru` (28), plus `Delhi` (27) and `New Delhi` (11). | City filters and competitor matching fragment one market into multiple groups. | Preserve raw city and add a versioned canonical city mapping (`Bangalore → Bengaluru`, `New Delhi → Delhi`) used only for analysis/matching. |
| DQ-006 | High | `orders.created_at` has three source-specific encodings: ERP `DD/MM/YYYY HH:MM`, SFA `YYYY-MM-DD HH:MM:SS`, and partner `YYYY-MM-DDTHH:MM:SSZ`. | A generic parser can swap day/month or mix local and UTC times, corrupting latency and period assignment. | Parse by `source_system`; treat ERP/SFA offset-free values as IST and convert partner `Z` from UTC to IST. Preserve `created_at_raw`, `created_at_ist`, and `created_at_parse_status` together in order-line/service facts and assert their consistency. No published KPI uses creation time: typed `order_date` and `requested_delivery_date` remain the order/service cohort fields. |
| DQ-007 | High | `planned_arrival` is `YYYY-MM-DD HH:MM:SS`, while `actual_arrival` is `YYYY-MM-DD HH:MM:SS` for 50,082 Telematics A rows and `DD-Mon-YYYY HH:MM AM/PM` for 26,807 Telematics B rows. All 76,889 delivery rows also have `delay_minutes`; 67,211 differ materially from parsed actual-minus-planned and 25,734 change the on-time classification. | Either source can materially change on-time and late-route KPIs. | Parse both timestamp formats explicitly and use the direct timestamp comparison as the governed primary measure. Preserve stored delay and both classifications, flag every conflict, and make the choice visible rather than silently reconciling it. |
| DQ-008 | High | The stated operational range ends 30 Jun 2026, but actual arrivals extend to 4 Jul 2026. In the requested-delivery-date Q1 cohort, 12,894 orders have a delivery and one arrived after quarter end. | Filtering OTIF by arrival date can remove orders promised in the quarter and bias the board's service cohort. | Cohort fill rate and OTIF by `requested_delivery_date`; delivery-operation metrics by actual delivery date; returns by `return_date`; freight operations by `service_date`; inventory by `snapshot_date`. Always display the date basis. |
| DQ-009 | High | `orders.region_id` matches the customer/outlet region, but 65,925 of 83,671 orders differ from the origin warehouse/route region. Routes and warehouses agree with each other. | A generic “region” join can produce two different, internally consistent answers. | Model both roles: `customer_region` and `warehouse_region`. Dashboard labels never use an unqualified region, and Ask Kestrel stops an unqualified named-region question for customer-versus-DC clarification. Never overwrite one role with the other. |
| DQ-010 | High | Return quantities include 13,100 positive and 900 negative values. All 14,000 `approval_date` values are blank. Status remains populated: 6,935 approved, 3,509 pending, and 3,556 rejected. | Raw sums understate physical returns; filtering on approval date drops every record; mixing statuses overstates recognized leakage. | Normalize physical quantity with absolute value and convert through the original order line's `case_pack_at_order`. Use `status='APPROVED'` for the main credit-note leakage KPI and show pending/rejected separately. Do not infer status from `approval_date`. |
| DQ-011 | High | There is no explicit dispatch-value fact. Header gross value describes the order, including undelivered quantity; partial orders are common by construction. | Using full order value as dispatch value overstates the denominator and understates return/credit-note percentage. | Derive and label an estimated dispatch value at line grain: `line_value_inr × min(delivered_eaches / ordered_eaches, 1)`, guarded for zero quantity. The per-line cap prevents oversupply from inflating the denominator. Main leakage percentage is approved credit-note value divided by this documented estimate. |
| DQ-012 | Medium | The dictionary says one source system's order header gross does not reconcile to line values. In the supplied data, all 83,671 orders reconcile to `SUM(line_value_inr)` within ₹0.01 across ERP, SFA, and partner sources. | Blindly “correcting” the claimed mismatch would alter already-reconciled values. Future extracts could still regress. | Apply no correction to this extract. Keep an ingestion assertion and exception report; trust the data over the stale note. |
| DQ-013 | High | `products` is current state only, while `product_price_history` supplies validity windows. | Applying current MRP to historical orders creates look-ahead bias and incorrect price/value comparisons. | Resolve historical price with `order_date BETWEEN effective_from AND COALESCE(effective_to, infinity)`. Use current MRP only for explicitly current price-position views and label its as-of basis. |
| DQ-014 | Medium | Forty products have category `Frozen`, but all are encoded as `storage_temp_band='CHILLED'` and `is_chilled=1`; the product master contains no separate frozen temperature band. | A category-name rule can disagree with the available operational cold-chain flag. | Use `is_chilled`/`storage_temp_band` for temperature-controlled eligibility, preserve category for merchandising, and avoid inventing a separate frozen safe-temperature rule. |
| DQ-015 | High | Of 2,371 delivery excursion flags, 1,876 occur on orders containing a chilled product and 495 occur on orders with no chilled line. | Using all excursion flags in “per 100 chilled deliveries” inflates the numerator and mixes a source anomaly into the business KPI. | Define a chilled delivery as any order with at least one `is_chilled=1` line; count deliveries distinctly. Scope both numerator and denominator to chilled deliveries and report the 495 non-chilled flags as a separate data-quality exception. |
| DQ-016 | Medium | Inventory has 78 weekly snapshot dates from 6 Jan 2025 through 29 Jun 2026; not every SKU appears at every warehouse/week. The latest snapshot has 1,680 rows and no blank expiry or negative available cases. | Treating snapshots as daily or comparing them with the current computer date creates false stock and expiry claims. | Near-expiry is available cases expiring 0–30 days after the selected/latest snapshot date. Always expose the snapshot as-of date and never forward-fill absent SKU/warehouse combinations silently. |
| DQ-017 | Critical | Freight `amount` and `detention_charge` are in paise although currency is `INR`; operational `fuel_cost_inr` is driver-entered and explicitly unreconciled. | A missed conversion inflates freight 100×; using driver fuel reports the wrong cost concept. | Use partner invoices only and divide both paise fields by 100 exactly once into named INR fields. Main billed freight is base amount plus separately stated detention. Retain raw paise for audit and test a known conversion. Never use `fuel_cost_inr` as billed freight. |
| DQ-018 | Critical | The API has no order/delivery ID. In the deterministic 41,500-invoice population, every route code exists, but only 5,130 invoice warehouse-route pairs (12.36%) agree with the route master's warehouse; 36,370 conflict. | Joining on `warehouse + route + period` discards or misattributes most freight and can create null/zero denominators. Exact delivery or carrier-to-order attribution is impossible. | Reconcile warehouse views by `warehouse_code + service period` and route views independently by `route_code + service period`. Flag composite conflicts and exclude them from analyses requiring both keys. Use total/warehouse/route freight-per-case; do not claim invoice-to-delivery lineage or carrier-level delivered cases. |
| DQ-019 | High | The API generates 24,985 `PAID`, 8,263 `PENDING`, and 8,252 `DISPUTED` invoices over 1 Jan 2025–30 Jun 2026. It also deliberately returns `429`/`503`, uses cursor pagination, and emits UTC creation timestamps. | Silent status mixing makes “cost” ambiguous; incomplete cursor walks or transient failures undercount spend. | Preserve invoice status. Use **PAID settled freight per delivered case-equivalent** as the primary card; label all-status output as billed and expose pending/disputed components. Follow cursors to null, honor `Retry-After`, use bounded exponential backoff, convert UTC metadata to IST, and serve only freshness-labelled complete caches. |
| DQ-020 | Critical | BazaarPulse has no Kestrel product/SKU key. Product names and pack sizes are inconsistently structured; pagination differs by city, and detail pages `387`, `458`, and `777` are missing. | A forced fuzzy match or silently dropped detail page can publish a confident but incomplete price gap/history. | Parse brand/name/pack fields, normalize comparable mass/volume prices to 100G/100ML, score candidate matches, accept only a documented high-confidence threshold, and show ambiguous/unmatched listings separately. Retain retailer/listing/pack evidence and record missing detail pages as structured warnings. |
| DQ-021 | High | BazaarPulse observations refresh weekly; “last seen” is the freshness field. `robots.txt` imposes a one-second delay and disallows `/internal/` and `/admin/`. | Calling the result “today's” or “live” misstates freshness; scraping the bundled internal margin sheet violates the source contract. | Label “latest observed as of …”, retain observation date and stock state, cache results, obey the crawl delay, and never request or use disallowed paths. |
| DQ-022 | Critical | The pack has order/line values, credit notes, returns, and freight invoices but no complete COGS, labor, rent, tax settlement, collections, bad debt, or overhead facts. | “Profit,” “net cash,” or definitive gain/loss would be fabricated. | Use the terms measured financial leakage, derived dispatch value, credit-note value, and billed/paid freight. Never present accounting profit or causal financial attribution. |

## Explicit OTIF conflict

The business definition is not ambiguous:

```text
line_in_full  = delivered_eaches >= ordered_eaches
order_in_full = every eligible line_in_full
on_time       = parsed actual arrival <= parsed planned arrival
OTIF          = order_in_full AND on_time
```

The supplied data makes the result degenerate, not mathematically uncertain:

- 511,516 of 511,516 order lines are short-delivered.
- 0 lines are exactly or over delivered.
- Under the active-outlet delivered/partial screen, 0 of 66,405 orders are in full.
- 25,223 of those deliveries are timestamp-derived on time (37.98%), but none can be OTIF because none is in full.

The governed response is to report strict OTIF as **0%**, state why, and make the still-informative fill-rate and on-time measures prominent. A tolerance may be offered only as a clearly labeled scenario after stakeholder approval; it must never replace the strict KPI or be invented to make the number look plausible.

## Explicit timestamp and period contract

| Business concept | Canonical field | Parsing/time-zone rule | Reason |
|---|---|---|---|
| Service cohort, fill rate, strict OTIF | `orders.requested_delivery_date` | Local business date; no timestamp conversion | Measures promises due in the board's Q1 cohort even when the order was placed earlier or delivery occurs later. |
| Order creation timestamp | `created_at_raw`, `created_at_ist`, `created_at_parse_status` in semantic order facts; not a KPI | Parser is selected by `source_system`; explicit partner `Z` is UTC converted to IST; ERP/SFA offset-free values are IST | Preserves source text and parse evidence without allowing creation time to replace `order_date` or `requested_delivery_date` in a KPI. |
| Planned delivery | `deliveries.planned_arrival` | Parse `YYYY-MM-DD HH:MM:SS` as IST | Operational schedule is local. |
| Actual delivery | Normalized `deliveries.actual_arrival` | Vendor-specific parser; offset-free values are IST | Telematics A and B use incompatible text formats. |
| On-time classification | Parsed `actual_arrival - planned_arrival` | Vendor-specific actual parser; `actual <= planned` is on time | Uses the direct event timestamps. Stored delay is retained as a conflicting source measure because 25,734 rows change classification. |
| Cold-chain delivery month | Normalized actual-arrival date | IST after vendor-specific parsing | Excursion is an in-transit/delivery event. |
| Returns | `returns_credit_notes.return_date` | Local business date | Measures when leakage was recorded, not when the order was placed. |
| Near-expiry stock | `inventory_snapshots.snapshot_date` | Snapshot-relative | Historical inventory must not be compared with wall-clock “today.” |
| Freight operations | API `service_date` | Date supplied by carrier | Aligns expense to the movement period and delivered-case denominator. |
| Freight accounting | API `invoice_date` | Date supplied by carrier | Separate view for billing timing; do not silently substitute it for service date. |
| Competitor position | Listing `last_seen` | Observation date retained verbatim/canonically parsed | Makes weekly price freshness explicit. |
| Competitor source history | Detail-page `observed_on` | Source business date; historical Kestrel MRP resolved from its effective window on that date | Prevents current-MRP look-ahead in historical comparison. |

## Verified controls to preserve as regression tests

These checks passed in the supplied extract and should fail loudly if a future pack changes:

| Control | Verified result |
|---|---|
| Main fact-to-dimension key coverage | Zero orphans for orders → outlet/region/warehouse/route, order lines → order/product, deliveries → order, and returns → order line. |
| Delivery grain | 76,889 delivery rows for 76,889 distinct orders; no multiple delivery rows per order in this extract. |
| Order/line value reconciliation | Zero orders differ from rounded line-value sum by more than ₹0.01. |
| Order-time case pack | Zero missing/non-positive `case_pack_at_order` values. |
| Delivery timing fields | Zero null `planned_arrival`, `actual_arrival`, or `delay_minutes` values. |
| Latest inventory snapshot | 29 Jun 2026; 1,680 rows; zero blank expiry dates and zero negative available-case values. |

## Metric publication rules

1. Publish the formula, grain, period field, eligibility rule, and denominator with every headline KPI.
2. Use ratio-of-sums for fill rate and freight-per-case; never average subgroup percentages without appropriate weighting.
3. Preserve raw fields beside normalized fields and attach a parse/match/quality status.
4. Do not convert a missing denominator to zero; return “not available” with the reason.
5. Separate observed facts from derived estimates and from explanatory associations.
6. Never attribute causality or personal responsibility from a dimensional association alone.
7. Keep external-source freshness and cache completeness visible in the application.
