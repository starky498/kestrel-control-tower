# Kestrel Supply Chain Control Tower

[![quality](https://github.com/starky498/kestrel-control-tower/actions/workflows/ci.yml/badge.svg)](https://github.com/starky498/kestrel-control-tower/actions/workflows/ci.yml)

An evidence-backed control tower for Kestrel Provisions. It validates the supplied operational
data, builds grain-safe DuckDB and Parquet analytical models, ingests resilient external
snapshots, and serves eight Streamlit decision workspaces plus governed natural-language
questions.

The supplied database, CSVs, static site, API key, caches, generated warehouse, Parquet files,
logs, and audit outputs are deliberately excluded from Git.

## Eight governed workspaces

1. **Executive Command Center** — FY 2026–27 Q1 by default; side-by-side eaches and
   case-equivalent fill, strict OTIF, allocation, post-allocation fulfilment, backlog, risk,
   settled freight, competitor coverage, leakage, and volume-qualified worst/best/improved groups,
   with an explicit inventory filter-boundary callout when order-only filters are active.
2. **Service & Fulfilment** — ordered → allocated → delivered → linked-return flow, eaches or
   case-equivalent trends, promotion/order-source views, dimensional rankings, shortage
   contributors, booked-value exposure, and native order-line evidence.
3. **Delivery & Exception Drivers** — actual-delivery-cohort on-time, more-than-two-hours-late,
   POD, delay-source conflict, recorded failure-label Pareto, volume guards, and evidence rows.
4. **Cold Chain & Inventory Risk** — unfiltered monthly excursion/severity trends, volume-qualified
   maximum-temperature hotspots by warehouse/route/category, near-expiry and batch evidence,
   inventory filter boundaries, and exact RT06 return lines with status/raw/normalized quantity
   evidence.
5. **Commercial Leakage & Logistics Cost** — approved-credit and short-delivery booked-value
   exposure, recorded restock/scrap/vendor-return dispositions, **PAID settled freight per delivered
   case-equivalent** as the primary cost card, plus billed/pending/disputed status and route/carrier
   evidence without claiming accounting profit or recovery.
6. **Market & External Context** — current and source-dated BazaarPulse prices, retailer/listing
   evidence, governed matching, pack-normalized 100G/100ML comparisons, effective-dated historical
   Kestrel MRP, service-price attention, and gated weather/holiday associations.
7. **Ask Kestrel** — a rules-first, typed intent router with conservative spelling repair,
   session-scoped follow-up memory, and optional keyless local MiniLM paraphrase matching. Every
   accepted intent still calls the same allowlisted metric services as the dashboard; the local
   model cannot write SQL, change a formula, or generate an unrestricted answer.
8. **Trust Center** — versioned definitions, data conflicts, freshness, sync history, and bounded
   redacted operation events.

Strict OTIF is intentionally **0%**: every one of the 511,516 supplied order lines is
short-delivered. The system does not invent a tolerance; it keeps fill, allocation,
post-allocation fulfilment, and timestamp-derived on-time service visible for diagnosis.

## Fastest clean start

Prerequisites are Python 3.11+, `make`, and the assignment pack. Clone the repository and unpack
the pack so these paths exist:

```text
data/source/data/kestrel_ops.db
data/source/bazaarpulse_site/
data/source/partner_api/server.py
```

Then run:

```bash
make start
```

This creates `.venv`, installs development dependencies, runs configuration checks and 93 source
quality checks, stages and promotes `.kestrel/kestrel.duckdb` plus fingerprinted Parquet exports,
collects the bundled BazaarPulse site, and opens
[http://localhost:8501](http://localhost:8501).

If the pack is elsewhere:

```bash
cp .env.example .env
```

Set `KESTREL_SOURCE_DB` and, if necessary, `KESTREL_BAZAARPULSE_SITE_ROOT` to absolute paths. Keep
the freight key only in local `.env`. `make doctor` reports missing paths without modifying the
source database.

### Version-pinned dependency installation

`make start` installs the version-pinned `requirements-dev.lock` application/tool package set, then
installs the project in editable mode with `--no-deps`. CI uses the same development lock; Docker
uses the pinned runtime lock. The equivalent non-editable local path is:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements-dev.lock
.venv/bin/pip install --no-deps .
make prepare
make run
```

`requirements.lock` pins runtime application dependencies; `requirements-dev.lock` adds pinned
lint, typing, test, coverage, and HTTP-mocking tools. They do not digest-pin the Python base image or
pin pip/setuptools, so this is dependency-version reproducibility rather than a bit-for-bit
environment guarantee.

### Optional local language understanding

Ask Kestrel works immediately in rules-only mode. To recognize a wider range of paraphrases, each
person who clones the repository can optionally run:

```bash
make setup-local-nlp
```

The same verified installation is available from the **Install local language model** button on
the Ask Kestrel page.

This downloads the pinned, SHA-256-verified `sentence-transformers/all-MiniLM-L6-v2` ONNX model
(`Apache-2.0`, recorded in the manifest) into the ignored `.kestrel/models/` directory. The
architecture-specific download is approximately
23 MB on common ARM64 and x86-64 computers (the portable fallback is approximately 46 MB). It needs
no API key, paid account, or secret. The one-time download uses the public model host; inference is
then local, and questions are not sent to an external AI service.

The ONNX/tokenizer Python runtime is installed with the normal project dependencies and used about
100 MB in the tested macOS virtual environment; exact installed size varies by operating system.
The separately downloaded model is the approximately 23 MB component described above.

The model has a deliberately narrow role: it maps a paraphrase to one of Kestrel's finite supported
metric intents. Exact rules run first, confidence and ambiguity gates can refuse uncertain matches,
and all calculations remain deterministic, parameterized calls to governed metric services.
Spelling repair is limited to business vocabulary, and short follow-up questions inherit only
allowlisted session context. Neither feature enables arbitrary questions or unrestricted
text-to-SQL.

The model binary is intentionally not committed, so another person cloning the repository must run
`make setup-local-nlp` to enable semantic paraphrases. If they skip it, are offline, or set
`KESTREL_NLQ_SEMANTIC_ENABLED=false` in local `.env`, the dashboard continues with its governed
rules, spelling support, and safe unsupported-question response. `make start` never downloads the
model or makes network access a startup dependency.

The installer follows `KESTREL_NLQ_MODEL_PATH` when configured; otherwise it uses the model
directory below `KESTREL_RUNTIME_DIR`. Keep a custom in-repository model path under `.kestrel/`, or
add that exact directory to the checkout's local `.git/info/exclude`. Only the default `.kestrel/`
location is automatically excluded by the committed `.gitignore`.

## Optional external evidence

Core workspaces start without a network, freight server, or external cache. Optional refreshes are
explicit and failure-isolated.

### Freight: complete history by default

Put the assignment-pack key in local `.env`, then start the supplied mock API in terminal 1:

```bash
.venv/bin/python data/source/partner_api/server.py
```

In terminal 2, run the refresh:

```bash
make sync-freight
```

The default request is the complete supplied invoice range, `2025-01-01` through `2026-06-30`.
Q1 remains selectable:

```bash
.venv/bin/kestrel sync-freight --from 2026-04-01 --to 2026-06-30
.venv/bin/kestrel sync-freight --offline-cache
```

The client follows cursors to null, retries `429`, `503`, and transport failures, honours
`Retry-After`, converts paise once, checkpoints interrupted walks, and promotes only a complete
cache. A verified full walk produced 41,500 invoices across 208 pages, 237 requests, and 29
retries. Invoice coverage is 2025-01-01–2026-06-30; service dates begin 2024-12-29.

### Weather and Indian public holidays

```bash
make sync-context
.venv/bin/kestrel sync-context --offline-cache
```

Open-Meteo weather is cached at warehouse-city-centroid × day. Indian holidays first request
Nager.Date and fall back to Google's public India calendar when `IN` is unsupported, retaining
only entries explicitly classified as public holidays. The verified caches contain 4,368
warehouse-days and 27 national public-holiday rows for 2025-01-01–2026-06-30.

Context comparisons are withheld unless coverage, freshness, join, and minimum-cohort gates pass.
They are descriptive associations, never causal claims. See
[docs/EXTERNAL_CONTEXT.md](docs/EXTERNAL_CONTEXT.md).

### Competitor matching, review, and history

```bash
make scrape-prices
PYTHONPATH=src .venv/bin/uvicorn kestrel.market_api:app --host 127.0.0.1 --port 8089
```

BazaarPulse collection respects allowed listing/detail paths and conservative confidence/ambiguity
gates. Unresolved listings enter a read-only review queue. Accountable overrides are
source-controlled in `config/competitor_match_decisions.yml`; reviewed matches still cannot bypass
the 0.80 candidate, brand, or pack safety gates. Current and append-only scrape history retain
automatic/final outcomes and review provenance. A separate immutable source-price history contains
6,804 source-dated observations from 1,134 detail pages covering 6 May–30 June 2026. Three missing
detail pages are recorded as structured warnings for listing IDs `387`, `458`, and `777`, rather
than silently fabricated.

The verified current snapshot has 1,137 listings: 1,088 governed matches and 49 review-queue rows.
Historical comparisons resolve the Kestrel MRP effective on each source observation date; comparable
mass/volume packs are also normalized to 100G/100ML. Raw shelf price, retailer, listing, pack, stock,
observation date, and match evidence remain visible.

Read-only endpoints:

- `GET /health`
- `GET /market/review-queue?city=Mumbai&limit=100`
- `GET /market/listings/{listing_id}/history?limit=100`

See [docs/COMPETITOR_MATCH_GOVERNANCE.md](docs/COMPETITOR_MATCH_GOVERNANCE.md).

## Common commands

```bash
make prepare          # doctor + validation + staged build + BazaarPulse snapshot
make setup-local-nlp  # optional keyless, verified ~23 MB local paraphrase model
make validate-data    # 93 schema, grain, FK, parity, and conflict checks
make build            # staged SQLite -> DuckDB + Parquet manifest with rollback
make scrape-prices    # allowed local scrape, matching, overrides, current + history
make sync-freight     # resilient complete-history carrier synchronization
make sync-context     # optional weather and holiday refreshes
make run              # start Streamlit without rebuilding
make test             # deterministic test suite
make lint             # Ruff + mypy
make audit            # eight-workspace label/error/control/15s audit
make benchmark        # 17 baseline cases; 19 with a verified model; external snapshots required
make quality          # lint + test + audit + governed-question benchmark
make clean            # confirmation guard; no deletion without the explicit --yes command
```

The repeatable page/accessibility/performance audit is:

```bash
PYTHONPATH=src .venv/bin/python scripts/audit_ui.py \
  --expected-pages 8 \
  --threshold-ms 15000 \
  --output .kestrel/ui-audit.json
```

The audit fails unless exactly eight workspaces are discovered. Each must render in no more than
15,000 ms with a visible heading, no Streamlit exception/error, and no unlabelled interactive
control. The 15 August 2026 local release run passed 239 tests and all eight workspaces: no
exceptions, errors, missing headings, or unlabelled controls; initial render was 1,334.872 ms and
the slowest measured page rerender was Executive Command Center at 421.756 ms. The governed-question
benchmark also passed all 19 applicable cases, including two installed-model paraphrases, with its
slowest case at 297.479 ms. Generated
JSON evidence remains ignored and should be refreshed on the review machine. This automated audit
does not establish WCAG conformance; manual keyboard, focus, contrast, zoom, and screen-reader
checks remain pending and separate.

## Docker

The container runs as non-root, uses `requirements.lock`, exposes port 8501, and has a Streamlit
health check. The supplied source is a read-only bind mount; generated `/app/.kestrel/` state uses
the Docker-managed `kestrel-runtime` named volume:

```bash
docker compose up --build
```

The entrypoint validates/builds/scrapes only when the analytical database is absent. Freight and
public context remain explicit optional syncs. The image does not download the optional MiniLM
model, so its default Ask Kestrel behavior is the rules-only fallback. See
[docs/RUNBOOK.md](docs/RUNBOOK.md) for container and recovery procedures.

## Metric and trust stance

The registry contains 22 definitions at semantic version `1.0.0` and two freight definitions at
`1.1.0` in `config/metrics.yml`. Dashboard
cards and Ask Kestrel call allowlisted metric services; they do not reinterpret raw tables.

| Metric family | Governed calculation / boundary |
|---|---|
| Fulfilment gates | allocation ÷ ordered; delivered ÷ allocated; line-capped delivered ÷ ordered; eaches and case-equivalent fill are explicit ratios of sums, so oversupply on one line cannot offset another line's shortage |
| Strict OTIF | every line full **and** parsed actual arrival on/before planned arrival, at order grain |
| Backlog | current-source-state open orders overdue as of the selected period end |
| Delivery exceptions | actual-delivery cohort; on-time, >2h late, POD, delay conflict, recorded failure labels |
| Cold chain | 100 × flagged chilled deliveries ÷ chilled deliveries; distinct delivery grain |
| Near expiry | positive available cases with 0–30 days remaining at the latest eligible weekly snapshot |
| Commercial exposure | approved credit-note ratio plus proportional short-delivery booked-value exposure; neither is profit/cash loss |
| Freight per case | primary PAID settled amount ÷ delivered case-equivalents; billed/pending/disputed remain explicit; independently aggregated |
| Market gap | current/effective-dated Kestrel MRP − governed competitor observation; exact pack or 100G/100ML comparison stated |

Facts remain at native grains; there is no fan-out mega-join. Customer region and origin/DC region
are separate. Freight has no delivery key, so numerator and denominator are independently
aggregated at shared period × DC or route. “Measured leakage” is not accounting profit: COGS,
collections, labour, rent, tax, bad debt, and overhead are absent.

Builds stage DuckDB and Parquet outputs before promotion. The Parquet manifest records schema
version, source SHA-256, completion time, and table row counts. Governed CLI operations emit local
JSONL start/success/failure events under `.kestrel/logs/kestrel.jsonl`; fields whose keys contain
API key, password, secret, token, or credential are redacted. Generated state and secrets stay
outside Git.

## Documentation

- [DECISIONS.md](DECISIONS.md) — concise scope, judgments, limits, and next steps.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — value chain, grains, reliability, and deployment.
- [docs/METRICS.md](docs/METRICS.md) — formulas, eligibility, dates, and publication rules.
- [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) — assignment and business requirements.
- [docs/FULL_SCOPE_TRACEABILITY.md](docs/FULL_SCOPE_TRACEABILITY.md) — expanded-plan coverage.
- [docs/RUNBOOK.md](docs/RUNBOOK.md) — operation and recovery.
- [docs/DEMO.md](docs/DEMO.md) — interview demonstration route.
- [docs/ACCESSIBILITY_PERFORMANCE.md](docs/ACCESSIBILITY_PERFORMANCE.md) — audit contract.
- [docs/DATA_QUALITY.md](docs/DATA_QUALITY.md) — verified issue register.

## Troubleshooting

- **Source missing:** unpack the pack at `data/source/` or set `KESTREL_SOURCE_DB`; run
  `make doctor`.
- **Freight unavailable:** start the supplied API, configure its key locally, then sync; core pages
  remain available.
- **Interrupted freight:** rerun the identical date window to resume its compatible checkpoint.
- **Context unavailable:** rerun `make sync-context`, or publish valid last-good caches offline.
- **Market snapshot unavailable:** verify `bazaarpulse_site/`, decision YAML, then rerun
  `make scrape-prices`.
- **Local NLP model unavailable:** Ask Kestrel continues in rules-only mode. When network access is
  available, rerun `make setup-local-nlp`; the installer reuses already verified files and rejects
  a wrong size or checksum.
- **Rebuild recovery:** staged writes prevent partial individual artifacts, and handled DB-promotion
  failure restores the prior Parquet directory. After a host/process interruption, compare the
  manifest fingerprint and rerun the build. See the runbook before deleting generated state.
