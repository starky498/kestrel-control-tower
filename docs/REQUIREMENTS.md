# Requirements and Traceability

## Purpose

This document converts the ambiguous Kestrel brief into testable delivery requirements. It distinguishes the assignment's hard submission contract from the client's desired business outcomes and from implementation decisions made to resolve ambiguity.

Source pack: `FDE_Assignment_Pack_Kestrel_v1.1.zip`; internal brief: *Forward Deployed Engineer: Take-Home Assignment*, version 1.0, August 2026.

### Requirement classes

- **Hard** — explicitly non-optional in the assignment.
- **Business** — requested by Divya or Rakesh; it may be scoped, but an omission must be deliberate and recorded in `DECISIONS.md`.
- **Quality** — needed for a trustworthy, defensible implementation.
- **Decision** — our governed resolution of an ambiguity or contradiction.

## Hard submission contract

| ID | Source | Requirement | Acceptance evidence |
|---|---|---|---|
| SUB-01 | Brief §4, §6 | Deliver one GitHub repository. The approved visibility is public. | Repository URL opens without a separate submission artifact. |
| SUB-02 | Brief §4 | Provide a working system a person can open and use from a clean checkout. A notebook, slide deck, or static design does not qualify. | A fresh clone starts successfully with one documented command and presents an interactive application. |
| SUB-03 | Brief §4, §6 | Include `README.md` with cold-start instructions and no reliance on tribal knowledge. | README states prerequisites, installation, data-path configuration, exact run and test commands, supported capabilities, and limitations. |
| SUB-04 | Brief §4 | Include `DECISIONS.md`, no more than one page. | It states what was built, what was deliberately omitted, assumptions, the next two weeks of work, and what fails first in production. |
| SUB-05 | Brief §6 | Include running source code and its dependency declaration. | A clean environment can install dependencies and start the system without an undisclosed service or paid account. |
| SUB-06 | Brief §6 | Commit as work progresses; reviewers inspect history. | History contains coherent, reviewable commits rather than a single bulk commit. |
| SUB-07 | Brief §6 | Do not commit `kestrel_ops.db` when the application needs it. | The database and generated caches are ignored; the supplied pack/database location is configurable and absence produces an actionable message. |
| SUB-08 | Brief §6 | Submit no video or slide deck. | The repository alone contains everything required for evaluation. |

## Safety and source constraints

| ID | Class | Source | Requirement | Acceptance evidence |
|---|---|---|---|---|
| CON-01 | Hard | Brief, “What you cannot do” | Never use a real client's data. | Only the supplied synthetic Kestrel/BazaarPulse data and permitted public enrichment are used. |
| CON-02 | Hard | Brief, “What you cannot do” | Do not scrape any site other than BazaarPulse shipped in the pack. | Scraper base URL is configurable but constrained to the supplied local site for this submission. |
| CON-03 | Quality | External Sources §1 | Treat `robots.txt` as on a live site. | The scraper observes the one-second crawl delay and never requests `/internal/` or `/admin/`. |
| CON-04 | Quality | Brief §3, §8 | Treat documentation as partial; where documentation and data disagree, data wins. | Important assumptions are backed by profiling checks and recorded in `DATA_QUALITY.md`. |
| CON-05 | Decision | External Sources §3 | Weather and public-holiday enrichment are optional, not default dependencies. | The core application remains usable without public network access; any enrichment is justified and cached. |

## Brief-to-feature matrix

| ID | Class | Stakeholder need | Governed interpretation | Delivery target | Acceptance criteria |
|---|---|---|---|---|---|
| UX-01 | Business | “One screen” showing where service and money are being lost. | The landing page is an executive control tower, not a menu of disconnected reports. | Executive overview with service, cold-chain, freight, and credit-note signals plus visible exceptions. | A reviewer sees headline KPIs and the worst-performing dimensions without navigating through four screens. |
| UX-02 | Business | Regional managers need their own view. | “Region” means the customer/order region; origin warehouse/route region is labeled separately. | Global date, region, warehouse, route, outlet/channel, and category filters where applicable. | A filter changes KPI cards, trends, rankings, evidence tables, and question answers consistently. |
| UX-03 | Business | Q1 must be on the front page. | Kestrel's year is April–March; the latest complete Q1 in the pack is **FY 2026–27 Q1, 1 April–30 June 2026**. | Q1 is the default landing-page period and is labeled with dates. | No ambiguous label such as “Q1” appears without its fiscal year and boundaries. |
| SVC-01 | Business + Decision | Fill rate by region, warehouse, route, and outlet; Divya says cases while Rakesh later says eaches. | Eaches is the default because it is the later, commercially specific request; case-equivalents remain selectable. Both use `case_pack_at_order`. | Fill-rate card, trend, breakdown, worst-performer ranking, and unit selector. | `sum(delivered_normalized) / sum(ordered_normalized)` is used—not an unweighted average of line percentages—and the selected unit is visible. |
| SVC-02 | Business + Decision | OTIF by the same dimensions. | On-time means `delay_minutes <= 0`. In-full means every eligible line has delivered eaches greater than or equal to ordered eaches. OTIF is evaluated at order grain. | Strict OTIF, on-time, and in-full cards plus dimensional breakdowns. | Strict OTIF remains 0% for the supplied data and displays the reason; no undocumented tolerance is substituted. |
| SVC-03 | Business | Worst performers must be obvious. | Rankings need a minimum-volume guard or denominator alongside rates so tiny groups are not misleading. | Bottom-five regions/warehouses/routes/outlets with numerator, denominator, and volume context. | Closed/test/deleted customers follow the eligibility contract, ties are deterministic, and empty scopes return an explanation rather than an exception. |
| COLD-01 | Business | Temperature excursions. | A delivery is “chilled” when any order line references a product with `is_chilled = 1`; a delivery is counted once. | Excursions per 100 chilled deliveries, trend, and breakdown by warehouse/route/category. | Numerator and denominator are both chilled-delivery scoped; non-chilled excursion flags are surfaced as a quality warning, not mixed into the KPI. |
| COLD-02 | Business + Decision | Near-expiry stock. | Near expiry means available stock expiring within 30 days of the selected/latest inventory snapshot. | Near-expiry available cases by warehouse/category/SKU. | The as-of snapshot date and 30-day threshold are visible; calculation never compares historical stock with the computer's current date. |
| COLD-03 | Business | Returns caused by cold-chain failure. | Return reason `RT06` is the governed cold-chain breach code; `RT01` near expiry is reported separately. | Approved credit-note value and normalized returned quantity by reason/category/warehouse/route association. | Return status, sign, and UOM rules match the data-quality contract; wording describes association rather than proven blame. |
| FIN-01 | Business | Freight cost per delivered case. | Use carrier invoices, not driver-entered `fuel_cost_inr`. “Billed” and “paid” views are labeled separately. | Resilient freight ingestion, total/warehouse/route freight-per-case, status split, and cache metadata. | Amount is converted from paise, service dates align with denominator periods, retries handle `429`/`503`, and the app can use a valid cache when the API is temporarily unavailable. |
| FIN-02 | Business | Returns and credit notes as a percentage of dispatch value. | Main leakage uses `APPROVED` credit-note value. Dispatch value is a documented delivered-value estimate because no explicit dispatch-value fact exists. Pending and rejected amounts remain visible. | Credit-note percentage and value by category/reason/outlet and associated operational dimensions. | Numerator, denominator, status scope, and derivation are visible; zero denominators are safe. |
| FIN-03 | Business + Decision | Show where money is leaking by category and carrier. | Report measured leakage, invoice spend, and credit-note value—not accounting profit. Carrier spend is directly available; carrier-level returned-case attribution is not. | Leakage waterfall/components and carrier invoice-spend/status view. | No chart claims net profit, cash collected, or causal carrier responsibility from unavailable data. |
| PRICE-01 | Business | Compare Kestrel MRP with competitors' shelf price by city and category. | Compare against the latest observed, in-stock competitor price and disclose observation time and match confidence. | Robots-compliant BazaarPulse ingestion, normalized listing model, confidence-scored product matching, and city/category price-gap view. | Only defensible matches affect the headline; unmatched and ambiguous listings remain visible and no weekly observation is labeled “live.” |
| ASK-01 | Business + Quality | Let managers ask questions in plain English and receive the numbers behind the answer. | Use a governed intent router that extracts metric, dimensions, period, filters, unit, ranking, and comparison; it calls the same metric layer as the dashboard. | Supported-question interface with answer narrative and evidence table. | Every answer states period, filters, metric definition, result, and supporting breakdown. Unsupported or ambiguous questions fail safely; no unrestricted model-generated SQL is executed. |
| REL-01 | Quality | “Assume nothing about my patience for setup.” | Local dependencies and source availability are checked at startup with actionable guidance. | One-command launcher and visible source-health/freshness state. | Missing DB/API/site/cache conditions produce clear recovery instructions rather than a stack trace. |
| DQ-01 | Quality | Data is intentionally unclean and the reviewer expects it to be noticed. | Preserve raw values, create a canonical semantic layer, and attach data-quality flags rather than silently rewriting source records. | Reusable normalized order-line, order-service, cold-chain, return, freight, and price models. | Dashboard and Ask-anything use the same governed functions; quality checks cover the issues in `DATA_QUALITY.md`. |

## Source and integration contract

| Source | Role | Grain and join contract | Reliability contract |
|---|---|---|---|
| `data/kestrel_ops.db` | Authoritative operational source | 13 tables, 818,901 rows, 1 Jan 2025–30 Jun 2026 by order date. Facts join on documented surrogate keys; historical order assignments come from `orders`. | Open read-only, validate schema/row-level invariants, and provide a configurable path. |
| `data/csv/*.csv` | Alternate representation of the same operational data | Same 13 tables as SQLite. It is not an additional source. | Do not load both SQLite and CSV copies into the same model. |
| Partner freight API | Only source of actual carrier invoices | About 41,500 invoices. Warehouse and route codes can each be aligned to operations at period grain; there is no order/delivery key and the composite warehouse-route pair is often inconsistent. | Authenticate, follow cursors to null, honor `Retry-After`, retry bounded `503`s with backoff, convert paise to INR, cache atomically, and expose freshness. |
| BazaarPulse | Competitor shelf-price observations | 1,137 listings across four cities and five retailers; no Kestrel SKU key. Match using normalized brand/name/pack size with confidence. | Honor robots/crawl delay, support city-specific pagination, tolerate unreachable/malformed pages, and cache observations. |
| Open-Meteo / Nager.Date | Optional explanatory context | Join by governed location/date mappings only. | Never block the core app; cache and label external context as association, not causation. |

## Illustrative-question traceability

The brief says these questions are examples, not a specification. Implement reusable metrics rather than hard-coded answers.

| ID | Illustrative question | Required sources | Reusable capability exercised | Acceptance check |
|---|---|---|---|---|
| Q-01 | Which five outlets had the lowest case fill rate last month, excluding closed and test outlets? | Orders, order lines, outlets | Fiscal/calendar period resolver, outlet eligibility, case-equivalent normalization, bottom-N ranking | Returns five or fewer valid outlets with ordered and delivered case-equivalents and the exact month. |
| Q-02 | What was OTIF by region for the last complete quarter? | Orders, order lines, deliveries, outlets/regions | Order-grain strict in-full, on-time, quarter resolver, region grouping | Labels the quarter, shows the strict 0% result and its denominator, and explains the data finding. |
| Q-03 | Which categories drive the largest value of returns, and what is the leading reason code? | Returns, order lines, products | Approved-value aggregation, reason/category ranking | Uses approved credit notes for the main value, shows pending/rejected separately, and does not sum signed return quantity raw. |
| Q-04 | Temperature excursions per hundred chilled deliveries, by month. | Deliveries, order lines, products | Chilled-order bridge and distinct-delivery aggregation | Counts each delivery once and scopes both numerator and denominator to chilled deliveries. |
| Q-05 | Which routes are more than two hours late on more than one delivery in ten? | Deliveries, routes | `delay_minutes > 120`, route denominator, threshold filter | Shows late count, total deliveries, and percentage; requires rate greater than 10%. |
| Q-06 | For the top twenty SKUs by value, how does MRP compare with the lowest observed competitor price in Mumbai? | Orders/lines, price history/products, BazaarPulse | SKU-value ranking, as-of MRP, entity matching, city filter | Shows no more than 20 SKUs, observation dates, confidence, and unmatched products; does not force weak matches. |
| Q-07 | Freight cost per delivered case, by warehouse, for the last quarter. | Freight API, orders/lines, warehouses | Service-period freight aggregation and delivered-case denominator | Converts paise, aligns periods, labels billed/paid scope, and does not use `fuel_cost_inr`. |
| Q-08 | Which outlets ordered a discontinued SKU after its discontinuation date? | Orders, order lines, products, outlets | Order-date versus lifecycle-date comparison | Returns order/outlet/SKU evidence and excludes products without a discontinuation date. |

## Explicit boundaries and non-goals

- No claim of accounting profit, final cash received, or product margin: COGS, labor, rent, collections, bad debt, and other operating costs are absent.
- No graph database: the value-chain graph is a business view over a relational semantic model.
- No unrestricted text-to-SQL or chatbot that can invent metrics.
- No forced competitor match and no use of BazaarPulse's disallowed `/internal/` material.
- No silent OTIF tolerance invented to make the score non-zero.
- No person or carrier is labeled as having “caused” a failure unless the data establishes causality; use “associated with” or “likely driver.”
- No attempt to repair every dirty source record. The system normalizes fields that affect delivered metrics and reports the rest.

## Definition of done

The submission is ready for interview defence when:

1. A fresh clone plus the supplied pack/database path starts with the README's single command.
2. FY 2026–27 Q1 opens by default and all global filters propagate consistently.
3. Dashboard and Ask-anything results come from the same tested metric contracts.
4. Strict OTIF, mixed UOM, outlet eligibility, return signs/statuses, timestamp parsing, freight units/retries, and weak competitor matches have regression tests.
5. Every external result shows source freshness and degrades clearly when unavailable.
6. `DECISIONS.md` remains within one page and honestly records omissions and production limits.
7. No supplied database, generated cache, credential, or disallowed scrape output is committed.
