# Requirements and Acceptance Contract

## Purpose

This document converts the Kestrel brief, stakeholder notes, supplied data, and approved expanded
plan into testable requirements. It separates the assignment's hard submission contract from
business outcomes, analytical decisions, and engineering quality controls.

Source pack: `FDE_Assignment_Pack_Kestrel_v1.1.zip`. Internal brief: *Forward Deployed Engineer:
Take-Home Assignment*, version 1.0, August 2026.

Requirement classes:

- **Hard** — explicitly non-optional in the assignment.
- **Business** — a requested decision capability; an omission must be deliberate and documented.
- **Decision** — the governed resolution of ambiguity or contradiction.
- **Quality** — needed for a trustworthy, reproducible, and defensible implementation.

Implementation evidence is mapped in `docs/FULL_SCOPE_TRACEABILITY.md`. The current local
eight-workspace and governed-question release gates passed on 15 August 2026; generated reports are
ignored and must be refreshed for a materially changed commit or data snapshot. Manual
accessibility remains separate and is not implied by the automated result.

## Hard submission contract

| ID | Source | Requirement | Acceptance evidence |
|---|---|---|---|
| SUB-01 | Brief §4, §6 | Deliver one GitHub repository with the approved public visibility. | Repository URL opens without a separate submission artifact. |
| SUB-02 | Brief §4 | Deliver a working interactive system, not a notebook, static mock-up, or slide deck. | Fresh checkout plus supplied pack opens the eight-workspace application with the documented command. |
| SUB-03 | Brief §4 | Include a complete `README.md`. | Prerequisites, pack layout, setup, run, test, integrations, limitations, recovery, and security boundaries are documented. |
| SUB-04 | Brief §4 | Include a concise `DECISIONS.md`, no more than one page. | It states the implemented scope, assumptions, deliberate boundaries, next production steps, and first scale limits. |
| SUB-05 | Brief §6 | Include running source and dependency declarations. | Python 3.11 can install the project; version-pinned runtime and development application-dependency locks are present. |
| SUB-06 | Brief §6 | Commit coherently as work progresses. | Git history contains reviewable logical changes rather than a single undifferentiated dump. |
| SUB-07 | Brief §6 | Do not commit the supplied operational database. | Source DB/CSVs, generated DuckDB/Parquet/cache/log/audit output, secrets, and local environment are ignored. |
| SUB-08 | Brief §6 | Do not submit a video or slide deck. | Repository documentation and the running system are sufficient for review. |

## Safety and source constraints

| ID | Class | Requirement | Acceptance evidence |
|---|---|---|---|
| CON-01 | Hard | Use only the supplied synthetic Kestrel/BazaarPulse data and permitted public context. | No real-client dataset or production credential is present. |
| CON-02 | Hard | Scrape no site other than the supplied BazaarPulse experience. | Local mode reads the bundled site; HTTP mode is constrained to configured BazaarPulse listing pages. |
| CON-03 | Quality | Treat BazaarPulse `robots.txt` as binding on a live site. | `/internal/` and `/admin/` are never requested; allowed HTTP traversal observes the crawl interval. |
| CON-04 | Quality | Treat documentation as partial and profile the data before deciding formulas. | Known conflicts, grains, counts, parity, and assumptions are recorded and checked. |
| CON-05 | Decision | Weather, holidays, freight, and competitor refreshes must not become hidden startup dependencies. | The core operational app opens from its warehouse without contacting an external endpoint. |
| CON-06 | Quality | Do not silently mutate supplied source values. | SQLite opens read-only; raw and normalized fields coexist; conflicts remain visible. |
| CON-07 | Quality | Keep secrets and generated evidence outside Git. | `.env`, databases, Parquet, `.kestrel/`, logs, caches, and audit artifacts are ignored. |

## Experience requirements

| ID | Class | Governed requirement | Acceptance criteria |
|---|---|---|---|
| UX-01 | Business | Provide Executive Command Center as the landing workspace. | Both eaches/case fill, service gates, strict OTIF explanation, backlog, cold risk, measured leakage, PAID settled freight/case, competitor coverage, exception inbox, and qualified worst/best/improved signals appear; active order-only filters trigger an explicit boundary on the snapshot-based near-expiry card. |
| UX-02 | Business | Provide Service & Fulfilment analysis. | Ordered → allocated → delivered → linked-return flow, eaches/case-equivalent selection, trends, promotion/order-source dimensions, shortage contributors, booked-value exposure, volumes, and order evidence use requested delivery date. |
| UX-03 | Business | Provide Delivery & Exception Drivers analysis. | Actual-delivery on-time, >2-hour late, POD, conflict, recorded-failure, trend, route/vendor, Pareto, and evidence views render with a configurable minimum-volume guard. |
| UX-04 | Business | Provide Cold Chain & Inventory Risk analysis. | Excursions per 100 chilled deliveries, monthly severity and maximum-temperature views by warehouse/route/category, latest-snapshot near-expiry and batch evidence, associated returns, snapshot date, denominator, and warning evidence are visible. |
| UX-05 | Business | Provide Commercial Leakage & Logistics Cost analysis. | Approved-credit and short-delivery booked-value exposures, recorded physical dispositions, PAID settled freight/case as primary, billed/pending/disputed evidence, DC/route rankings, carrier spend, freshness, ignored filters, and attribution boundaries are visible. |
| UX-06 | Business | Provide Market & External Context analysis. | Current and source-dated governed competitor evidence, retailer/listing/pack fields, effective-dated Kestrel MRP, 100G/100ML normalization, match/history coverage, service-price attention, and gated weather/holiday panels are visible. |
| UX-07 | Business + Quality | Provide Ask Kestrel. | A typed router extracts supported metric, period, quantity basis, grouping, rank, and filters; it calls governed services and never executes unrestricted generated SQL. |
| UX-08 | Quality | Provide Trust Center. | Metric version/definition/status, critical source boundaries, analytical snapshot time, dimension coverage, external readiness/sync history, and a bounded recent operation-event table are available. The source fingerprint remains in the build output/Parquet manifest. |
| UX-09 | Business | Distinguish customer region from origin/DC region. | Labels are explicit. An unqualified region question stops for clarification; explicit customer and DC geography produce separate governed results. |
| UX-10 | Business + Decision | Use FY 2026–27 Q1, 2026-04-01 through 2026-06-30, as the labelled default analytical period. | The dates and fiscal-year label are visible; custom periods remain selectable. |
| UX-11 | Quality | Propagate defensible filters consistently. | Date, customer region, DC region, DC, route, outlet, channel, category, recorded order source, and recorded promotion affect every compatible component; ignored filters are disclosed. |
| UX-12 | Quality | Treat absence and small denominators honestly. | Empty states are explanatory; rates expose counts; rankings apply a visible minimum volume where appropriate. |

## Service, delivery, and inventory requirements

| ID | Class | Governed requirement | Acceptance criteria |
|---|---|---|---|
| SVC-01 | Business + Decision | Publish allocation, post-allocation fulfilment, and fill as ratios of sums. | `sum(allocated)/sum(ordered)`, `sum(delivered)/sum(allocated)`, and `sum(min(delivered, ordered) per line)/sum(ordered)` use order-time case packs; subgroup rates are not averaged and oversupply cannot offset another line's shortage. |
| SVC-02 | Business + Decision | Eaches is the default quantity basis; case-equivalents remain selectable. | Labels, numerator, and denominator always identify the selected basis. |
| SVC-03 | Business + Decision | Publish strict order-grain OTIF without inventing tolerance. | In-full requires every line delivered at least ordered; on-time uses parsed arrival timestamps. Supplied strict OTIF remains 0% and explains the 511,516 short lines. |
| SVC-04 | Quality | Preserve both timestamp-derived and stored delay evidence. | Timestamp arrival is the primary KPI; raw delay and its reconciliation/classification conflicts remain queryable and disclosed. |
| SVC-05 | Business | Publish current-state overdue OPEN-order backlog. | Count uses eligible outlets and requested date through selected period end; wording disclaims historical status reconstruction. |
| SVC-06 | Business | Identify worst, best, and most-improved performers defensibly. | Rate, numerator, denominator, volume, comparison period, threshold, and deterministic tie behavior accompany rankings. |
| SVC-07 | Business + Quality | Preserve order-source, promotion, and short-delivery line evidence. | Recorded `order_source` and `promo_code` are exposed without inferring redemption/causality; raw/IST/parse-status creation fields, order-time quantities, and proportional short booked-value exposure remain auditable. |
| DEL-01 | Business | Publish delivery-cohort on-time and >2-hour-late rates. | Actual delivery date is used; eligible timestamp denominator and >120-minute threshold are explicit. |
| DEL-02 | Business | Publish POD coverage, delay conflict, and recorded failure-label rates. | Each rate uses distinct delivery grain and exposes numerator/denominator. |
| DEL-03 | Business + Quality | Provide route, telematics-vendor, Pareto, and row evidence. | Comparisons are volume-qualified and described as association, never responsibility or cause. |
| COLD-01 | Business | Publish excursions per 100 chilled deliveries. | A delivery counts once when any line is chilled; both numerator and denominator are chilled scoped; non-chilled flags remain quality evidence. |
| COLD-02 | Business + Decision | Publish near-expiry available cases at the latest eligible weekly snapshot. | Positive stock with 0–30 days remaining is summed; snapshot and threshold are visible; no current-clock comparison is used. |
| COLD-03 | Business | Show cold-chain-associated return evidence. | Exact originating order-line link, normalized quantity, status, disposition, and documented reason label are used without causal language. |
| COLD-04 | Business + Quality | Diagnose cold severity and inventory at native grains. | Monthly flag/severity trend and warehouse/route/category maximum temperature remain delivery-grain associations; at-risk inventory retains latest snapshot, SKU, batch, expiry, damage, and blocked status. |

## Leakage, freight, market, and context requirements

| ID | Class | Governed requirement | Acceptance criteria |
|---|---|---|---|
| FIN-01 | Business | Publish approved credit-note value as a percentage of estimated delivered dispatch value. | Numerator uses APPROVED notes by return date; denominator uses capped delivered fraction of booked line value by requested date; pending/rejected remain separate. |
| FIN-02 | Business + Decision | Describe only measured leakage and billed spend. | No page claims accounting profit, margin, cash collection, or full loss because cost and collection inputs are absent. |
| FIN-03 | Business + Decision | Publish short-delivery booked-value and physical disposition evidence. | Proportional undelivered booked value is labelled exposure, while RESTOCK/SCRAP/VENDOR_RECOVERY remain recorded disposition quantities/values—not cash loss or recovery. |
| FRE-01 | Business | Synchronize the full supplied freight invoice history by default. | Default invoice request is 2025-01-01–2026-06-30; complete evidence contains 41,500 unique invoices, 208 pages, 237 requests, and 29 retries; service coverage may begin 2024-12-29. |
| FRE-02 | Quality | Make freight refresh complete, resumable, and last-good protected. | Cursor runs to null; typed records, paise conversion, `Retry-After`, bounded retry/backoff, checkpoint, compatible resume, atomic cache, and offline publication are tested. |
| FRE-03 | Business + Decision | Publish settled and billed freight per delivered case-equivalent without a false row link. | PAID settled freight/case is primary; all-status billed is secondary; both aggregate numerator/denominator independently by shared period × DC/route; asymmetric filters are ignored and reported. |
| FRE-04 | Business | Publish route/carrier and invoice-status evidence. | Best/worst/most-improved route comparisons use stated volume gates; billed, detention, paid, pending, and disputed values are labelled. No carrier-attributed case or carrier cost-per-case claim is made. |
| MKT-01 | Business | Collect BazaarPulse observations safely and compare current MRP with latest governed shelf price. | Allowed traversal, complete typed cache, city/category scope, observation date, availability, and coverage are visible. |
| MKT-02 | Quality | Quarantine uncertain entity matches. | Automatic matches clear confidence and ambiguity gates; weak/unmatched listings remain visible and cannot affect headline price gaps. |
| MKT-03 | Quality | Support accountable review without bypassing safety. | YAML match/reject decisions require reviewer/date/note; reviewed match still clears 0.80 candidate, brand, and pack gates. |
| MKT-04 | Quality | Retain current and append-only listing/match history. | `sync_id + listing_id` replay is idempotent; changed payload collision is rejected; automatic and final outcome provenance survive a warehouse rebuild. |
| MKT-05 | Business + Quality | Provide read-only review/history access. | Health, filtered review-queue, and per-listing history GET endpoints are bounded and perform no mutation. |
| MKT-06 | Business + Quality | Retain detail-page source-price history and compare it without look-ahead. | 6,804 immutable observations from 1,134 pages cover 2026-05-06–2026-06-30; missing IDs 387/458/777 are structured warnings; historical Kestrel MRP is effective-dated; comparable mass/volume packs expose 100G/100ML prices. |
| CTX-01 | Decision | Treat weather and national holidays as optional explanatory evidence. | Network failure does not block core pages; weather and holiday caches/syncs fail independently and preserve prior analytical snapshots. |
| CTX-02 | Quality | Publish context only through explicit gates. | Complete sync, selected-period coverage, ≤365-day age, full weather row/location coverage, ≥95% operational join, and ≥30 orders per cohort are required. |
| CTX-03 | Quality | Prevent causal overstatement. | Results say descriptive association only; weather is a warehouse-city-centroid proxy and holidays are a national calendar flag. |

## Semantic and platform requirements

| ID | Class | Governed requirement | Acceptance criteria |
|---|---|---|---|
| DATA-01 | Quality | Validate source contracts before publication. | All 13 schemas, grains, relationships, quantities, parity, and known conflicts execute; blocking failure stops a build. |
| DATA-02 | Quality | Model facts at native grain and prevent fan-out. | Order line, order service, delivery, return, inventory batch, freight, listing/scrape/source-price history, weather, and holiday grains are documented; facts aggregate before cross-fact ratios unless an exact source key exists. |
| DATA-03 | Quality | Publish DuckDB and portable Parquet coherently. | Unique staged DB/directory, ZSTD exports, manifest schema/source SHA-256/completion/table counts, coordinated promotion, and handled-failure rollback avoid partial artifacts; fingerprint comparison detects a hard interruption between the two replacements. |
| DATA-04 | Quality | Preserve external evidence across operational rebuilds. | Physical `external_sync_runs`/`ext_*` data and compatible external views are copied into staging; a physical-copy error aborts promotion. |
| DATA-05 | Quality | Normalize mixed order-creation timestamps without changing KPI cohorts. | ERP/SFA/partner parsers preserve raw text, IST timestamp, and parse status; explicit `Z` converts UTC→IST; warehouse assertions check consistency; no KPI uses creation time. |
| MET-01 | Quality | Maintain a machine- and human-readable versioned metric registry. | Exactly 24 current definitions carry version `1.0.0` plus formula, grain, dates, eligibility, numerator, denominator, unit, status, and warning. |
| MET-02 | Quality | Keep computation behind a typed, allowlisted query boundary. | Dashboard and Ask use parameterized services; dimensions and intents are finite; no raw user SQL path exists. |
| OBS-01 | Quality | Record governed operation events locally. | Doctor, validation, build, freight, context, and market append STARTED then SUCCEEDED/FAILED JSONL events with run ID, UTC timestamp, and duration. Successful confirmed cleanup removes prior logs and leaves its final SUCCEEDED event in the recreated log. |
| OBS-02 | Quality | Redact sensitive structured fields. | Keys containing API key, password, secret, token, or credential are recursively replaced before log append; the UI displays a bounded recent result. |
| REP-01 | Quality | Provide a clean-start workflow. | `make start` creates the environment, installs dependencies, checks configuration, validates, builds, collects local market data, and starts Streamlit when the supplied pack is in place. |
| REP-02 | Quality | Provide repeatable dependency-version paths. | CI uses `requirements-dev.lock`; Docker uses `requirements.lock`; local pinned-lock commands and the unpinned interpreter/build-tool boundary are documented. |
| DEP-01 | Quality | Provide a safe container workflow. | Python 3.11 image runs non-root, source bind is read-only, generated state is writable/persistent, port 8501 has a health check, and optional syncs remain explicit. |
| CI-01 | Quality | Verify code quality and image construction on push and pull request. | GitHub Actions installs the pinned development application/tool set, runs Ruff, mypy, pytest, and builds the runtime image with read-only repository permission. |
| REC-01 | Quality | Provide bounded cleanup and recovery. | Clean requires `--yes`; rejects custom/symlinked runtime, symlink targets, and every configured target outside project `.kestrel/`; removes only validated DB/cache/Parquet/log targets documented in the runbook. |
| PERF-01 | Quality | Audit all eight workspaces repeatably. | Audit checks visible page heading, no Streamlit exception/error, no unlabelled interactive control, and each page render ≤15,000 ms; JSON evidence is generated outside Git. |
| A11Y-01 | Quality | Separate automated semantics from manual accessibility acceptance. | Keyboard, focus, contrast, zoom, and screen-reader checks are documented; automated AppTest is not represented as WCAG certification. |

## Source and integration contract

| Source | Role | Native grain and alignment | Reliability contract |
|---|---|---|---|
| `data/source/data/kestrel_ops.db` | Authoritative operational source | 13 tables, 818,901 rows. Exact source surrogate keys; historical order assignments come from orders. | Open read-only, fingerprint, validate, stream, reconcile, and never edit. |
| `data/source/data/csv/*.csv` | Alternate representation of the same data | Same 13 logical tables; not an additional fact source. | Validate parity; never double-load SQLite and CSV copies. |
| Partner freight API | Actual carrier invoices | Invoice; period × DC or route only. No order/delivery key. | Authenticate locally, complete cursor, retry, checkpoint/resume, validate, atomically cache/publish, expose coverage and freshness. |
| BazaarPulse | Current shelf cards and source-dated detail observations | Listing/retailer and listing × observed date. Kestrel SKU only through governed match. | Allowed listing/detail paths only, crawl delay in HTTP mode, structured parser warnings, complete current cache, append-only source history, quarantine ambiguity, accountable override. |
| Open-Meteo | Optional weather context | Warehouse-city centroid × date. | Complete all warehouse-days, isolated atomic cache, freshness/coverage/join/cohort gate. |
| Nager.Date, then Google India public calendar | Optional national holiday context | National public-holiday date. | Nager first; Google ICS fallback when India is unsupported; retain only explicit public holidays; record provider. |

## Illustrative-question traceability

The brief's questions are examples, so implementation must compose reusable metric services rather
than hard-coded answers.

| ID | Question | Capability and acceptance |
|---|---|---|
| Q-01 | Which outlets had the lowest case fill last month? | Month resolver, eligible outlets, case-equivalent ratio of sums with a per-line delivered cap, bottom-N, ordered/delivered denominators. |
| Q-02 | What was OTIF by region last complete quarter? | Explicit customer/DC geography, quarter label, order-grain strict OTIF, zero result and short-delivery explanation. |
| Q-03 | Which categories drive approved return value? | Return-date filter, APPROVED headline, category/reason ranking, pending/rejected evidence, no raw signed-quantity sum. |
| Q-04 | Excursions per hundred chilled deliveries by month? | Actual delivery month, chilled bridge, distinct-delivery numerator/denominator. |
| Q-05 | Which routes are over two hours late on more than one in ten? | Parsed delay >120 minutes, actual-date cohort, route denominator, >10% threshold, and minimum 25 actual-date deliveries. |
| Q-06 | How do top-value SKUs compare with Mumbai competitor prices? | Eligible dispatch ranking, current/effective-dated MRP, retailer/listing evidence, latest/source-dated final matches, pack comparability, 100G/100ML values, and unmatched coverage. |
| Q-07 | Freight per delivered case by DC last quarter? | Complete freight snapshot, PAID settled primary, billed/status components, paise conversion, independent service-period/actual-date aggregation, freshness/ignored-filter disclosure. |
| Q-08 | Which outlets ordered discontinued SKUs after discontinuation? | Order date versus effective lifecycle date with outlet/order/SKU evidence. |

## Explicit boundaries and non-goals

- No claim of profit, product margin, final cash, or complete loss.
- No graph database; the value-chain diagram describes a relational semantic model.
- No unrestricted text-to-SQL or generative answer path.
- No forced competitor match, mutable market API, or disallowed BazaarPulse traversal.
- No invented OTIF tolerance to turn a data finding into a conventional target.
- No route-level weather, outlet weather, state-specific closure, or causal context estimate.
- No person, vendor, route, DC, or carrier is labelled the cause of an outcome from association.
- No historical reconstruction from current-only order/master status.
- No hidden dependency on public network access, the freight mock server, or an optional cache.
- No claim that an automated UI audit alone establishes WCAG compliance.

## Definition of done and final release gate

The implementation is ready for submission only when all of the following evidence is current:

1. A clean checkout plus the supplied pack starts through the documented clean-start path.
2. Source validation and the staged warehouse/Parquet build succeed, reconcile, and share the
   expected source fingerprint.
3. Ruff, mypy, and the complete deterministic pytest suite pass using the repository state.
4. Docker image construction succeeds; Compose health and source-mount behavior match the runbook.
5. Full freight, optional context, and market current/history evidence are either verified or clearly
   labelled unavailable without breaking the core app.
6. All eight workspaces pass the repeatable audit command with the 15,000 ms per-page threshold,
   visible headings, no errors/exceptions, and no unlabelled controls.
7. Manual keyboard, focus, contrast, zoom, and screen-reader checks are recorded separately.
8. `DECISIONS.md` remains concise and generated data, credentials, logs, caches, and audit output
   remain outside Git.

The command for item 6 uses `--expected-pages 8` and is documented in
`docs/ACCESSIBILITY_PERFORMANCE.md`. The 15 August 2026 local release run passed exactly eight
workspaces with the required headings, no reported errors/exceptions or unlabelled controls, and a
slowest page rerender of 2,593.651 ms on Executive Command Center against the 15,000 ms gate. This
is automated regression evidence, not WCAG conformance or a substitute for the manual checks in
that document.
