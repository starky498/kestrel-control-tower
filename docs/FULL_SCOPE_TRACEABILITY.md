# Full Expanded-Plan Traceability

## How to read this map

This document connects the original proposed work — value-chain mapping, source/header review,
cleaning and normalization, relationship modelling, feature engineering, filtered decision
workspaces, diagnostic analysis, and natural-language questions — to concrete repository artifacts
and acceptance evidence.

Status meanings:

- **Implemented** — the repository contains the scoped capability and focused tests or a verifiable
  command.
- **Verified local evidence** — a generated local result was observed during implementation; it is
  intentionally not committed and must be refreshed for final handoff.
- **Pending final verification** — implementation exists, but an environment-specific or external
  release check still requires handoff evidence.
- **Boundary** — deliberately excluded or reframed because the source cannot support the claim.

## Original idea to delivered system

| Original idea | Governed implementation | Status and evidence |
|---|---|---|
| Map Kestrel's end-to-end value chain | Order promise → allocation → post-allocation fulfilment → delivery/POD/cold chain → returns/credits/freight, with inventory and market/context evidence | **Implemented.** Business and runtime flows are in `docs/ARCHITECTURE.md`; eight workspaces follow the same decision chain. |
| Review every CSV and header | SQLite and all 13 CSV representations are declared through source contracts; schema, grain, primary key, foreign key, quantity, parity, and known-conflict checks run before build | **Implemented.** `src/kestrel/contracts.py`, `docs/DATA_QUALITY.md`, `make validate-data`; current pack has 93 checks. |
| Clean and normalize data | Raw tables are retained; semantic dimensions/facts normalize UOM, order-time pack, source-specific `created_at` to IST with parse status, delivery timestamps, booleans, geography, lifecycle, promotion/source, status, return signs, and display labels | **Implemented.** `sql/10_dimensions.sql`, `sql/20_facts.sql`, source-contract tests. No source record is rewritten, and no KPI uses creation time. |
| Build how values connect | Native relational facts and exact keys replace a fan-out graph/mega-join; fact-to-fact ratios aggregate first unless an exact key exists | **Implemented.** Grain/join table in `docs/ARCHITECTURE.md`; warehouse grain assertions and metric tests. Graph database is an explicit **boundary**. |
| Engineer decision features | Allocation/post-allocation short, dual-basis fill, strict line/order in-full, parsed delay, promotion/source evidence, short booked-value exposure, cold severity/batches, dispositions, settled/billed freight, effective-dated market history, and context gates | **Implemented.** Semantic SQL, integration store, metric services, and 24-definition registry. |
| Show an overall analysis with filters | Executive Command Center with global date, quantity, customer-region, DC-region, DC, route, outlet, channel, recorded order-source, and promotion scope | **Implemented.** `src/kestrel/ui/app.py`, `src/kestrel/ui/filters.py`, `src/kestrel/ui/pages.py`. |
| Add profit/loss-style diagnostic pages | Reframed as Service, Delivery Exceptions, Cold/Inventory Risk, and Commercial Leakage/Logistics Cost | **Implemented.** Measured credit/short exposures, recorded dispositions, PAID settled freight primary, and billed/status evidence are shown. Accounting profit/recovery is a **boundary** because cost, collection, and settlement inputs are absent. |
| Explain where service was gained/lost and what performed well | Three fulfilment gates, shortage contributors, worst groups, volume-qualified best and most-improved warehouses, delivery trends, exception rankings, and evidence | **Implemented.** Metric service, Executive/Service/Delivery pages, regression tests. “Driver” means association/contribution, not causal proof. |
| Let managers ask natural questions | Rules-first typed intent routing over the same metric services, conservative business-term spelling repair, session-scoped follow-ups, and optional local MiniLM paraphrase matching; ambiguity and unsupported requests fail safely | **Implemented.** `src/kestrel/nlq/router.py`, local semantic/conversation modules, curated intent catalogue, Ask Kestrel, and NLQ tests. The model can select only a finite intent; unrestricted text-to-SQL is a **boundary**. |
| Deliver a public repository with reviewable work | Repository includes source, locks, CI, container, README, concise decisions, requirements, architecture, runbook, demo, and trust docs; source/generated data are ignored | **Implemented.** Public URL and live CI are exposed through the README; final branch/commit status remains dynamic handoff evidence. |

## Source, semantic, and publication traceability

| Plan / requirement | Implementation artifacts | Tests or operational evidence | Status |
|---|---|---|---|
| Read operational source without mutation (`CON-06`, `DATA-01`) | `src/kestrel/config.py`, `src/kestrel/contracts.py`, `src/kestrel/warehouse.py` | SQLite URI uses read-only mode; `make doctor`; `make validate-data` | **Implemented** |
| Declare and validate all 13 source tables (`DATA-01`) | `CONTRACTS`, quality-result model, `docs/DATA_QUALITY.md` | 93 schema/grain/FK/parity/conflict checks on current pack | **Implemented; verified local evidence** |
| Preserve raw and canonical values (`CON-04`, `DATA-02`) | `raw_*`, dimensions/facts, conflict flags in governed SQL | Warehouse/source contract and metric unit tests | **Implemented** |
| Normalize mixed CASE/EACH UOM (`SVC-01`, `SVC-02`) | `fct_order_line` order-time eaches/case-equivalents | Ratio-of-sums and service tests; quantity selector | **Implemented** |
| Separate customer and origin/DC geography (`UX-09`) | Dimensions, `FilterSet`, global filters, NLQ geography parser | Explicit customer/DC questions and ambiguity tests | **Implemented** |
| Preserve timestamp conflict (`SVC-04`, `DEL-02`) | Parsed planned/actual timestamps, derived delay, stored delay, conflict flags | Data-quality evidence: 67,211 stored-delay differences; 25,734 classification differences | **Implemented; verified local evidence** |
| Keep facts at native grain (`DATA-02`) | `fct_order_line`, `fct_order_service`, `fct_delivery`, `fct_return_credit_note`, `fct_inventory_snapshot`, `ext_*` models | Warehouse grain/control assertions; metric query tests | **Implemented** |
| Stage and coordinate analytical publication (`DATA-03`) | Unique staged DuckDB, staged ZSTD Parquet, manifest, promotion/rollback helpers | Each artifact avoids partial writes; manifest/fingerprint is the cross-artifact boundary because a DB file and directory cannot share one filesystem transaction | **Verified locally:** 818,901 raw rows; 15 Parquet tables; source SHA-256 `23f1e55b8f992ece7bc777e1227bc9bdd5c27d24661fd23f2639d9775b44de17` |
| Preserve external state on rebuild (`DATA-04`) | `_preserve_external_objects` copies `external_sync_runs`, physical `ext_*`, and compatible views | Focused warehouse preservation/rollback tests | **Implemented** |
| Portable analytical release (`DATA-03`) | 15 exported tables plus `.kestrel/parquet/manifest.json` | Manifest schema, source SHA-256, completion, and per-table counts tested | **Implemented** |
| Normalize order creation (`DATA-05`) | ERP/SFA/partner parsers in `fct_order_line` and `fct_order_service` | Raw text + IST value + parse status; UTC `Z` conversion; warehouse consistency assertion; no KPI date-basis change | **Implemented** |
| Version every published metric (`MET-01`) | `config/metrics.yml`, `MetricDefinition` loader, Trust Center table | 24 definitions: 22 at `1.0.0` and two freight metrics at `1.1.0`; registry tests | **Implemented** |
| Single typed query boundary (`MET-02`) | `AnalyticsService`, `ExternalAnalyticsService`, `ContextAnalyticsService` | Service, external, context, NLQ, and UI registry tests | **Implemented** |

## Metric and decision traceability

| Decision capability | Versioned metrics / evidence | Acceptance boundary | Status |
|---|---|---|---|
| Recorded allocation gate | `allocation_rate` | Requested-date eligible completed orders; ratio of sums | **Implemented** |
| Conversion after allocation | `post_allocation_fulfilment` | Association only; no DC/route cause inferred | **Implemented** |
| Customer fill | `fill_rate`, `fill_rate_eaches`, `fill_rate_case_equivalents` | Both fill bases explicit; selector defaults to eaches; order-time pack | **Implemented** |
| Short-delivery commercial exposure | `short_delivery_value_exposure_inr` | Proportional booked-value exposure; not credit, cash loss, profit, or cause | **Implemented** |
| Strict promise outcome | `strict_otif`, `on_time_rate`, `late_over_2h_rate` | Order-grain in-full; parsed timestamps; no tolerance | **Implemented** |
| Open demand requiring attention | `overdue_backlog_orders` | Current OPEN status as of selected period end, not historical reconstruction | **Implemented** |
| Actual-delivery operation | `delivery_on_time_rate`, `delivery_late_over_2h_rate` | Actual-date cohort is separate from promise cohort | **Implemented** |
| Evidence completeness/conflict | `pod_coverage_rate`, `delay_source_conflict_rate`, `recorded_failure_rate` | Recorded evidence signals do not prove responsibility | **Implemented** |
| Chilled-delivery risk | `temperature_excursions_per_100` | Distinct chilled delivery; source flag; non-chilled flags excluded; ranked dimensions default to a visible 25-delivery floor while monthly trend remains unfiltered | **Implemented** |
| Expiring inventory exposure | `near_expiry_cases` | Latest eligible weekly snapshot; 0–30 day configurable window | **Implemented** |
| Gross commercial leakage | `approved_credit_note_rate` | Approved value / estimated delivered dispatch value; not profit | **Implemented** |
| Logistics cost | `settled_freight_cost_per_case`, `freight_cost_per_case` | PAID settled primary and billed secondary; independent period × DC/route aggregation; no invoice-delivery key | **Implemented** |
| Observed shelf position | `competitor_price_gap` | Latest available final governed match; not live price | **Implemented** |
| Context evidence | Weather/holiday cohort tables and `PublicationGate` | Not a registry KPI or causal estimate; withhold when any gate fails | **Implemented** |

Full formulas, dates, populations, numerators, denominators, and version policy are in
`docs/METRICS.md`.

## External-source traceability

| Track | Implementation | Generated evidence / boundary | Status |
|---|---|---|---|
| Freight complete-history default (`FRE-01`) | CLI defaults 2025-01-01–2026-06-30; cursor client; typed cache/store | 41,500 invoices, 208 pages, 237 requests, 29 retries; service 2024-12-29–2026-06-30 | **Implemented; verified local evidence** |
| Freight resilience (`FRE-02`) | `429`/`503`/transport retry, `Retry-After`, checkpoint, compatible resume, atomic last-good, offline publish | Integration/unit coverage and reproducible commands in runbook | **Implemented** |
| Freight attribution (`FRE-03`, `FRE-04`) | Independent freight/delivered CTEs; ignored-filter metadata; carrier spend view | No row-level or carrier-case attribution; status and detention separate | **Implemented** |
| BazaarPulse safe collection (`MKT-01`) | Local/HTTP sources, allowed listing/detail traversal, crawl interval, typed cache | Supplied evidence is excluded from Git; known missing detail pages are structured warnings | **Implemented** |
| Automatic match quarantine (`MKT-02`) | Confidence plus ambiguity margin, normalized brand/pack/category/name | Weak/ambiguous/unmatched listings excluded from headline | **Implemented** |
| Reviewed decision governance (`MKT-03`) | `config/competitor_match_decisions.yml`, validator, reviewer/date/note, safety floors | Reviewed match cannot bypass 0.80 candidate or brand/pack conflict | **Implemented** |
| Market current/history (`MKT-04`, `MKT-06`) | Transactional current + append-only scrape listing/match history + immutable source-dated price observations | 1,137 current listings; 1,088 matches; 49 review; 6,804 observations from 1,134 detail pages over 2026-05-06–2026-06-30; structured missing IDs 387/458/777; effective-dated MRP and 100G/100ML evidence | **Implemented; verified local evidence** |
| Market API (`MKT-05`) | `src/kestrel/market_api.py` | GET health, bounded city review queue, bounded per-listing history; no mutation endpoint | **Implemented** |
| Weather (`CTX-01`, `CTX-02`) | Open-Meteo city-centroid client/cache/store and gated metric service | 4,368 rows = 8 DCs × 546 days for full range | **Implemented; verified local evidence** |
| India public holidays (`CTX-01`, `CTX-02`) | Nager.Date first, Google public India ICS fallback, explicit public-holiday filter | 27 national public holidays for full range; provider retained in metadata | **Implemented; verified local evidence** |
| Context publication (`CTX-02`, `CTX-03`) | Complete sync, period coverage, ≤365-day freshness, full weather grid, ≥95% join, ≥30 each cohort | Q1 local evidence: weather join 100%; smallest weather cohort 3,930; smallest holiday cohort 611 | **Implemented; verified local evidence** |

External generated counts are evidence from the 2026-08-15 local refresh described in
`docs/EXTERNAL_CONTEXT.md`; they are not committed fixtures. Final handoff should repeat or clearly
label their freshness.

## Eight-workspace traceability

| Workspace label in the current app | Requirements | Primary implementation and acceptance |
|---|---|---|
| Executive Command Center | `UX-01`, `SVC-01`–`SVC-06`, `FRE-03`, `MKT-01` | Eaches/case fill, service gates, strict-zero disclosure, backlog, settled freight/case, competitor coverage, exception inbox, qualified rankings, and explicit near-expiry ignored-filter boundary |
| Service & Fulfilment | `UX-02`, `SVC-01`–`SVC-07` | Ordered→allocated→delivered→linked-return flow, basis selector, requested-date trend, promotion/source dimensions, contributor analysis, short-value and raw/parsed evidence |
| Delivery & Exception Drivers | `UX-03`, `DEL-01`–`DEL-03` | Five delivery metrics, actual-date trend, threshold, route/vendor views, Pareto, evidence |
| Cold Chain & Inventory Risk | `UX-04`, `COLD-01`–`COLD-04` | Chilled excursion/severity trend, maximum temperature by warehouse/route/category, snapshot-relative near expiry/batch evidence, and exact RT06 return evidence |
| Commercial Leakage & Logistics Cost | `UX-05`, `FIN-01`–`FIN-03`, `FRE-03`–`FRE-04` | Credit/short booked-value exposure, recorded disposition evidence, PAID settled primary, billed/status/route/carrier evidence, freshness and attribution |
| Market & External Context | `UX-06`, `MKT-01`–`MKT-06`, `CTX-01`–`CTX-03` | Current and source-dated price position, retailer/listing/pack and 100G/100ML evidence, effective-dated MRP, service-price attention, review/history, gated associations |
| Ask Kestrel | `UX-07`, `UX-09`, `MET-02` | Supported dashboard metrics, exact-rules precedence, spelling support, local paraphrase matching when installed, session follow-ups, explicit interpretation/definition/evidence, and safe clarification/failure |
| Trust Center | `UX-08`, `MET-01`, `OBS-01`–`OBS-02` | Registry/version, critical boundaries, dimension coverage, sync state/history, bounded operation events |

The registry is defined in `src/kestrel/ui/app.py`. The release audit discovers these options rather
than hard-coding a second page list.

## Natural-language traceability

| Requirement | Router behavior | Evidence |
|---|---|---|
| Use the dashboard's definitions | Calls `AnalyticsService`/external service through allowed metric intents | `src/kestrel/nlq/router.py`, metric-definition injection, NLQ unit tests |
| Parse periods and ranks | Supports governed date phrases, rank direction/count, and comparison intent | Parser/result tests and Ask examples |
| Parse quantity basis | Eaches/case-equivalents recognized; contradictory dual basis rejected | NLQ tests |
| Parse dimensional filters | Allowlisted customer region, DC region, DC, route, outlet, channel and supported entities | Filter parsing tests |
| Avoid geography ambiguity | Unqualified named region returns a customer-versus-DC clarification | Ambiguity regression test and Ask demonstration |
| Recognize paraphrases locally | Exact rules run first; an optional pinned MiniLM ONNX model maps similar wording only to the curated finite intent catalogue and must clear confidence/margin gates | Semantic resolver tests, checksum manifest, and `make setup-local-nlp`; no API key required |
| Repair spelling conservatively | Corrections are bounded to known business vocabulary rather than silently rewriting outlet, route, warehouse, or other identifiers | Typo and unknown-entity regression tests |
| Carry safe follow-up context | Session memory may inherit or replace only typed intent fields from the last successful question; ambiguous/failed follow-ups do not corrupt that context | Pure conversation-memory and UI orchestration tests |
| Prevent metric invention | Unsupported, uncertain-semantic, and multiple-metric questions fail with guidance | Finite `MetricName`, semantic thresholds, and failure-path tests |
| Prevent unsafe queries | No unrestricted SQL or generative answer fallback; the local model has no execution authority | Static typed route and parameterized service boundary |
| Preserve evidence | Answer includes interpretation, definition/sources/warnings, and bounded supporting rows | Ask presentation layer and answer-contract tests |

## Reliability, observability, and deployment traceability

| Plan / requirement | Artifact | Acceptance evidence | Status |
|---|---|---|---|
| One-command clean start (`REP-01`) | `Makefile`, `README.md` | `make start` creates env, doctor, validate, build, scrape, run | **Implemented; clean-checkout replay remains a handoff gate** |
| Optional local intent setup | `Makefile`, `config/nlq_model.yml`, model downloader | `make setup-local-nlp` architecture-selects and SHA-256-verifies the public ONNX artifact under ignored `.kestrel/models/`; normal startup remains rules-only when absent | **Implemented; model download is optional and keyless** |
| Version-pinned application/tool dependencies (`REP-02`) | `requirements.lock`, `requirements-dev.lock` | Docker and CI install locks then project `--no-deps`; base interpreter/build tooling is not bit-for-bit pinned | **Implemented** |
| Local operation history (`OBS-01`) | `src/kestrel/observability.py`, CLI decorators | Paired STARTED + SUCCEEDED/FAILED JSONL for normal operations; successful cleanup clears prior logs and retains its final SUCCEEDED event | **Implemented; local doctor event verified** |
| Secret redaction (`OBS-02`) | Recursive sensitive-key redactor | Observability unit tests; generated log ignored | **Implemented** |
| Bounded destructive behavior (`REC-01`) | Per-target containment/symlink checks and `--yes` guard | Removes only validated DB/four caches/Parquet/log targets inside project `.kestrel`; external-file regression test | **Implemented** |
| Non-root container (`DEP-01`) | `Dockerfile`, `compose.yaml`, entrypoint | Python 3.11, UID 10001, pinned application lock, port/health, read-only source, persisted runtime | **Implemented; Docker build is the remote CI acceptance gate** |
| Continuous quality (`CI-01`) | `.github/workflows/ci.yml` | Read-only permission; pinned dependency install; Ruff, mypy, pytest; separate Docker build | **Implemented; live status is exposed by the README badge** |
| UI regression/performance (`PERF-01`) | `scripts/audit_ui.py` | Eight discovered pages, heading/error/label checks, ≤15,000 ms, JSON | **Verified locally:** 8/8 passed; initial 1,334.872 ms; slowest rerender 421.756 ms (Executive Command Center) |
| Governed-question regression/performance | `scripts/benchmark_qa.py` | Baseline finite cases plus two optional-model paraphrases verify status/intent/result limit and ≤5,000 ms with required external snapshots | **Verified locally:** 19/19 applicable cases passed with local model; slowest case 297.479 ms |
| Manual accessibility (`A11Y-01`) | `docs/ACCESSIBILITY_PERFORMANCE.md` | Keyboard/focus/contrast/zoom/screen-reader record | **Pending manual verification; no WCAG claim** |

## Documentation and interview traceability

| Artifact | Purpose | Status |
|---|---|---|
| `README.md` | Outcome, eight workspaces, clean start, pinned locks, integrations, Docker, commands, trust/boundaries | **Implemented** |
| `DECISIONS.md` | Concise approved scope, judgments, boundaries, next production steps | **Implemented; keep within one-page intent** |
| `docs/ARCHITECTURE.md` | Business value chain, runtime, staged/coordinated publication, grains, external/reliability/deployment | **Implemented** |
| `docs/METRICS.md` | All 24 contracts, dates, gates, context rules, evidence and version policy | **Implemented** |
| `docs/REQUIREMENTS.md` | Hard, business, decision, quality, platform, and release acceptance | **Implemented** |
| `docs/RUNBOOK.md` | Clean start, reproducible commands, refreshes, health, recovery, cleanup, Docker, CI | **Implemented** |
| `docs/DEMO.md` | Eight-workspace interview route, expected boundaries, likely questions | **Implemented** |
| `docs/ACCESSIBILITY_PERFORMANCE.md` | Automated and manual audit contract with current automated evidence and explicit manual boundary | **Implemented** |
| `docs/EXTERNAL_CONTEXT.md` | Full freight and optional context evidence/gates | **Implemented** |
| `docs/COMPETITOR_MATCH_GOVERNANCE.md` | Reviewed decision, history, and read-only API procedure | **Implemented** |
| `docs/DATA_QUALITY.md` | Source issue/assumption register | **Implemented** |

## Final verification ledger

These rows prevent “implemented” from being mistaken for “release evidence accepted”:

| Final gate | Required command/evidence | Current claim |
|---|---|---|
| Source/warehouse release | `make validate-data` and `make build`; manifest/source fingerprint reconciled | Passed locally: 93 checks, zero blocking; 818,901 raw rows; fingerprint recorded above |
| Static quality | `make lint` and `git diff --check` | Passed locally: Ruff and mypy clean; diff check clean |
| Full tests | `make test` | Passed locally: 239 tests |
| Eight-page UI gate | `scripts/audit_ui.py --expected-pages 8 --threshold-ms 15000`; report `passed: true` and `page_count_passed: true` | Passed locally: 8/8; initial 1,334.872 ms; slowest 421.756 ms (Executive Command Center) |
| Governed-question gate | `scripts/benchmark_qa.py --threshold-ms 5000`; report `passed: true` | Passed locally: 19/19 applicable cases with local model; slowest 297.479 ms |
| Manual accessibility | Completed checklist with reviewer/environment | **Pending; no WCAG claim** |
| Container | `docker build` and/or `docker compose up --build`, health check | Docker build delegated to remote CI; local Docker unavailable |
| Repository safety | Clean/known status; no source DB/cache/secret/generated artifact tracked | Passed local tracked-file and secret scans; recheck after final commit |
| Public delivery | Public repository URL, coherent commits, remote CI state | Public remote and logical commits present; live CI badge is the dynamic authority |

Generated evidence is deliberately excluded from Git and must be refreshed after material code,
dependency, or data changes. A failing gate is not made acceptable by deleting its row, weakening a
metric, increasing a threshold, or committing generated client data.
