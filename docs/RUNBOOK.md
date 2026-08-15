# Operations and Recovery Runbook

## Supported operating model

Kestrel is a local-first Python 3.11 application. Its operational source is read-only. Generated
DuckDB, Parquet, external caches, the optional local intent model, operation logs, and UI-audit
output live under `.kestrel/` and are excluded from Git. The supported evaluation paths are:

- a local virtual environment, using `make start` or version-pinned application dependency locks;
  and
- Docker Compose, with the assignment pack mounted read-only and `.kestrel/` persisted.

External sources are explicit refresh jobs. Passive Streamlit rendering and normal startup do not
call the freight API, BazaarPulse, Open-Meteo, Nager.Date, Google Calendar, or a model host. The
explicit **Install local language model** button is the only UI action that contacts the model host.

## 1. Prepare a clean checkout

Prerequisites:

- Python 3.11 or newer;
- `make` for the documented convenience commands;
- the supplied assignment pack; and
- Docker with Compose only if using the container path.

Unpack the assignment pack so the repository contains:

```text
data/source/data/kestrel_ops.db
data/source/data/csv/
data/source/bazaarpulse_site/
data/source/partner_api/server.py
```

These paths are ignored by Git. If the operational database or site is elsewhere, copy the
environment template and edit only local values:

```bash
cp .env.example .env
```

Set `KESTREL_SOURCE_DB` to the absolute database path and optionally
`KESTREL_BAZAARPULSE_SITE_ROOT` to the unpacked site root. Add the supplied freight key only when
running the freight integration. Never put the key in a command, source file, Dockerfile, or
committed environment file.

Before installing or building, confirm that no supplied/generated artifacts are tracked:

```bash
git status --short
git check-ignore data/source/data/kestrel_ops.db .env .kestrel/kestrel.duckdb \
  .kestrel/models/all-MiniLM-L6-v2/onnx/model.onnx
```

## 2. Start locally

### Convenience path

```bash
make start
```

This creates `.venv`, installs version-pinned `requirements-dev.lock` packages plus the local
project wheel with `--no-deps`, runs doctor and source validation, stages DuckDB and Parquet,
promotes each without exposing a partial artifact, collects the bundled BazaarPulse site, then starts
Streamlit at
[http://localhost:8501](http://localhost:8501).

`make start` does not synchronize freight, weather, or holidays. Those are optional, explicit
operations.

### Version-pinned dependency path

Use this path to mirror CI's declared Python application/tool package versions:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements-dev.lock
.venv/bin/pip install --no-deps .
make prepare
make run
```

`requirements-dev.lock` pins runtime and quality-tool application packages. The container uses the
smaller pinned `requirements.lock` and installs the project with `--no-deps` after that lock. The
Python base tag, interpreter patch, pip, and setuptools are not all digest/version pinned; these
commands do not promise a bit-for-bit identical environment.

### Start without rebuilding

If `.kestrel/kestrel.duckdb` already exists and no source/configuration change requires a rebuild:

```bash
make run
```

### Optional local semantic intent matcher

The rules-first Ask Kestrel experience needs no AI key or model download. To add local paraphrase
matching on a development machine, run:

```bash
make setup-local-nlp
```

Operators can trigger the same checksum-verified installer from the **Install local language
model** button on the Ask Kestrel page.

This target first ensures the local virtual environment is installed, then selects the pinned ONNX
file for the host architecture, downloads it from the public model repository, and verifies its
declared size and SHA-256. It follows `KESTREL_NLQ_MODEL_PATH` when set; otherwise it writes below
the model directory in `KESTREL_RUNTIME_DIR`, normally
`.kestrel/models/all-MiniLM-L6-v2/`. Common ARM64 and x86-64 hosts download approximately 23 MB of
model data plus a small tokenizer/configuration; the portable fallback is approximately 46 MB.
The default generated path is ignored and therefore is not included when the repository is cloned.
Each user who wants paraphrase support installs their own copy.

If a custom model path is inside the checkout, keep it under `.kestrel/` or add that exact directory
to the checkout's uncommitted `.git/info/exclude`; do not add downloaded model artifacts to Git.
The ONNX/tokenizer runtime is part of the pinned Python environment and used about 100 MB in the
tested macOS virtual environment; installed size varies by platform.

The download needs temporary network access but no API key, paid account, or shared credential.
Inference is local. Exact rules retain precedence; the model can only suggest a finite configured
intent and must pass confidence and ambiguity gates. Spelling repair and follow-up context are also
local, and the resolved intent still executes parameterized governed metric services. There is no
model-generated SQL, formula, filter, or answer.

If the model is absent, incomplete, disabled with `KESTREL_NLQ_SEMANTIC_ENABLED=false`, or cannot
load, Ask Kestrel continues in rules-only mode. `make start` deliberately does not depend on this
target, so model hosting and network availability cannot block startup. Rerunning
`make setup-local-nlp` reuses files that already match the manifest and safely replaces only an
incomplete download. The standard container image likewise starts in rules-only mode and does not
download a model during build or startup.

## 3. Baseline operational checks

Run configuration diagnostics:

```bash
make doctor
```

The JSON output should show the source DB path and `source_db_exists: true`. The command also shows
whether the analytical DB and optional context caches exist and whether a freight key is configured;
it never prints the key.

Run source contracts independently:

```bash
make validate-data
.venv/bin/kestrel validate-data --json
```

The current pack executes 93 checks. A blocking failure prevents warehouse publication. A known
non-blocking conflict is evidence to disclose, not a reason to modify the source.

Build the analytical release:

```bash
make build
```

Successful output identifies the DuckDB path, Parquet directory, 818,901 raw rows for the supplied
pack, governed model counts, and source SHA-256. Check the portable manifest:

```bash
sed -n '1,240p' .kestrel/parquet/manifest.json
```

The manifest must contain `schema_version`, `source_sha256`, `completed_at_utc`, and row counts for
every exported table. Do not edit generated DuckDB, Parquet, manifest, or cache files by hand.

## 4. Refresh optional evidence

### BazaarPulse market observations

Local collection from the bundled site is the default:

```bash
make scrape-prices
```

The operation validates the current listing set, traverses allowed product-detail pages, applies
automatic match gates and the reviewed decision registry, then transactionally publishes current
tables, idempotent scrape-run listing/match history, and immutable source-dated price observations.
A collection, governance, or publication failure leaves the preceding current/history state intact.

For the supplied site, verify 1,137 current listings, 1,088 final matches, 49 review rows, and 6,804
source observations from 1,134 detail pages covering 2026-05-06 through 2026-06-30. Detail pages for
listing IDs `387`, `458`, and `777` are absent by source design and must appear as three structured
warnings—not as fabricated price rows. Historical views resolve Kestrel MRP from the effective
window on each observation date and expose 100G/100ML normalized prices only for comparable packs.

Use HTTP mode only when intentionally testing the supplied locally hosted experience:

```bash
.venv/bin/kestrel scrape-prices --http
```

Start the read-only review/history API separately:

```bash
PYTHONPATH=src .venv/bin/uvicorn kestrel.market_api:app \
  --host 127.0.0.1 --port 8089
```

Health and bounded evidence checks:

```bash
curl --fail http://127.0.0.1:8089/health
curl --fail 'http://127.0.0.1:8089/market/review-queue?city=Mumbai&limit=10'
```

Reviewed decisions are made only through `config/competitor_match_decisions.yml` in a reviewed Git
change. The API has no write endpoint.

### Full freight history

In a second terminal, start the supplied mock API:

```bash
.venv/bin/python data/source/partner_api/server.py
```

Put its supplied key in `.env`, then run the complete default range:

```bash
make sync-freight
```

The requested invoice period is 2025-01-01 through 2026-06-30. A verified complete walk contains
41,500 unique invoices, 208 pages, 237 requests, and 29 retries; service-date coverage begins
2024-12-29. Validate all four counts plus completion and observed coverage, not record count alone.

For a deliberate smaller refresh or offline re-publication:

```bash
.venv/bin/kestrel sync-freight --from 2026-04-01 --to 2026-06-30
.venv/bin/kestrel sync-freight --offline-cache
```

If a cursor walk is interrupted, repeat the same date range with the default resume behavior. Do
not change the range while expecting checkpoint compatibility. An incomplete run retains its
checkpoint and either publishes a validated last-good cache as stale or leaves the previous
analytical snapshot untouched. `--no-resume` starts a fresh walk without making a partial result
publishable.

### Weather and public holidays

```bash
make sync-context
.venv/bin/kestrel sync-context --offline-cache
```

Weather and holiday refreshes have separate typed caches and publications. A failure in one does
not roll back a successful publication of the other, although the combined command exits non-zero
when either source fails. A verified complete range contains 4,368 warehouse-day weather rows and
27 national public holidays for 2025-01-01 through 2026-06-30.

Nager.Date is attempted first. When it reports that India is unsupported, the adapter uses the
public Google India iCalendar feed and retains only entries explicitly classified as public
holidays. Inspect Trust Center or `.kestrel/india_holidays.json` cache metadata for the actual
provider; sync metadata also retains provider/source URL, coverage, completion, and freshness.
Context associations remain withheld until all publication gates pass.

## 5. Health, quality, and release verification

### Application health

With Streamlit running:

```bash
curl --fail http://127.0.0.1:8501/_stcore/health
```

The endpoint confirms the process is healthy; it does not replace page-level semantic checks.

### Code and test checks

```bash
make lint
make test
git diff --check
```

`make lint` runs Ruff and mypy. `make test` runs the complete deterministic pytest suite, which
passes on the current repository state. Generated external data is not required for unit tests.

### Eight-workspace audit

```bash
PYTHONPATH=src .venv/bin/python scripts/audit_ui.py \
  --expected-pages 8 \
  --threshold-ms 15000 \
  --output .kestrel/ui-audit.json
```

The command exits non-zero unless it discovers the expected eight workspaces, or when any workspace
lacks its page heading, raises a Streamlit exception or error, contains an unlabelled interactive
control, or exceeds 15,000 ms for the measured page render. Inspect `passed`,
`page_count_passed`, and each page record in the generated JSON. Initial render
time is recorded for diagnosis; the implemented threshold gate applies to each workspace render.

The automated audit is not WCAG conformance evidence. Complete the manual checks in
`docs/ACCESSIBILITY_PERFORMANCE.md` separately. The 16 August 2026 local release run passed all
eight workspaces; initial render was 1,749.788 ms and its slowest measured page rerender was
Executive Command Center at 648.406 ms, with no missing heading, exception/error, or unlabelled
control. Refresh the ignored JSON report after any material change.

### Governed-question benchmark

After publishing both market and freight snapshots, exercise the canonical, paraphrased,
ambiguous, unsupported, market, freight, and lifecycle question paths:

```bash
PYTHONPATH=src .venv/bin/python scripts/benchmark_qa.py \
  --threshold-ms 5000 \
  --output .kestrel/qa-benchmark.json
```

The baseline cases verify expected answer status and, where applicable, metric, dimension, result
limit, spelling repair, timing cohort, and a 5,000 ms per-question bound. Market and freight cases
require their governed analytical snapshots. If the optional model passes its manifest checksum
validation, two additional local-semantic paraphrase cases run. The generated report is local
evidence and is excluded from Git. Treat a missing optional snapshot as a failed benchmark
precondition, not permission to weaken the expected answer contract.

The 16 August 2026 release benchmark passed 20/20 applicable cases with the local model installed;
its slowest case was 379.274 ms.

### Operation log

Governed CLI operations append local events here:

```text
.kestrel/logs/kestrel.jsonl
```

Doctor, validation, build, freight, context, and market operations write STARTED and SUCCEEDED or
FAILED with run ID, UTC time, and duration. A successful confirmed cleanup removes the prior log
directory during its scope, then its wrapper leaves the final SUCCEEDED event in the recreated log.
Fields whose keys contain `api_key`, `password`, `secret`, `token`, or `credential` are redacted
before append. Trust Center displays only a bounded recent event set and skips malformed individual
lines.

Inspect locally without committing the file:

```bash
tail -n 20 .kestrel/logs/kestrel.jsonl
git check-ignore .kestrel/logs/kestrel.jsonl
```

## 6. Docker Compose

The Compose service builds a runtime image with version-pinned Python application dependencies,
exposes 8501, bind-mounts
`data/source` read-only, and persists `/app/.kestrel/` in the Docker-managed `kestrel-runtime`
named volume:

```bash
docker compose up --build
```

On first container start, the entrypoint runs doctor, validation, build, and local BazaarPulse
collection when the analytical DB is absent. On later starts with the DB present it starts
Streamlit directly. Freight and public context remain explicit optional jobs.

Inspect state and health:

```bash
docker compose ps
docker compose logs control-tower
curl --fail http://127.0.0.1:8501/_stcore/health
```

Stop without deleting persisted state:

```bash
docker compose down
```

The image runs as UID 10001 and initializes the runtime path for that user. If deployment replaces
the named volume with a host bind, make only that narrow runtime directory writable according to
local policy; never make the source mount writable. The freight key is read from the host
environment at Compose runtime and is not baked into the image.

## 7. Failure and recovery matrix

| Symptom | Likely boundary | Safe recovery |
|---|---|---|
| `doctor` says source missing | Pack path or `KESTREL_SOURCE_DB` is wrong | Verify the unpacked file, update local `.env`, rerun `make doctor`; do not copy the DB into Git history. |
| Validation has a blocking failure | Source schema/grain/relationship contract is not satisfied | Preserve the failing output, confirm the expected pack/version, and investigate; do not edit source records to make checks pass. |
| Build fails before promotion | Validation, SQL, reconciliation, external preservation, or Parquet export failed | Fix the code/config/source-path issue and rerun. Temporary artifacts are cleaned and the preceding DuckDB/Parquet release remains available. |
| Build fails during DB promotion | Filesystem replacement failed | The build restores the preceding Parquet directory and leaves the preceding DB; resolve permissions/disk state and rerun. |
| Host/process stops between Parquet and DB replacement | Cross-artifact publication was interrupted | Compare `.kestrel/parquet/manifest.json` source SHA/completion with the DuckDB source snapshot, then rerun `make build`; the two filesystem objects do not share one transaction. |
| Build succeeds but optional evidence seems absent | No prior external tables existed or refresh has not run | Run the relevant explicit sync. A normal rebuild preserves existing external physical state but cannot create evidence never collected. |
| Freight exits incomplete | API/key/server, transient response, or interrupted cursor | Keep the checkpoint, restore API availability, rerun the identical range. Use `--offline-cache` only when a complete cache exists. |
| One context source fails | Public endpoint or cache validation issue | Review the source-specific message. Rerun online, or publish its valid last-good cache offline; the other source and core app remain usable. |
| BazaarPulse publication fails | Current listing incompleteness, unexpected parser failure, unsafe decision, or history collision | Correct the page/config/decision evidence and rerun; preceding current/history tables remain intact. The three known missing detail IDs remain structured warnings. Never weaken gates merely to force a match. |
| Market API health says `unavailable` or an evidence route fails | Analytics DB is absent/unreadable | `/health` reports unavailable JSON when the file is absent. Run doctor/build and restart the API; it performs no recovery mutation itself. |
| Streamlit page is empty | Optional source missing, filter denominator zero, or unsupported scope | Read the page disclosure/Trust Center, clear filters, and run only the missing explicit sync. Do not substitute zero. |
| Local semantic matching is unavailable | Optional model was not installed, is incomplete, or fails manifest/runtime validation | Continue with rules-only Ask Kestrel. When network access is available, rerun `make setup-local-nlp`; do not add an API key or commit `.kestrel/models/`. |
| A paraphrase or follow-up is refused | Confidence/margin gate, ambiguous context, or unsupported metric boundary | Rephrase with the metric, geography role, period, and grouping explicitly. Do not bypass the finite intent boundary or lower gates merely to force an answer. |
| UI audit exceeds 15 seconds | Query/render regression or cold local environment | Inspect the per-page report, rerun once in a stable environment, then profile the named workspace; do not raise the threshold without documenting a requirement change. |
| JSONL contains a malformed line | Interrupted/manual log alteration | Trust Center isolates the line. Preserve evidence, inspect filesystem stability, and let the next governed operation append normally. |
| Container is unhealthy | First build still running, bad mount, source missing, or write permission | Check Compose logs, source/read-only mount, `.kestrel` write permission, then restart after correcting the host configuration. |

## 8. Rebuild and cleanup policy

A normal `make build` is the preferred refresh. It preserves prior physical external tables and
compatible external views while replacing the operational semantic models and Parquet release.
Do not delete the warehouse merely because the operational source changed.

`make clean` is intentionally a confirmation guard:

```bash
make clean
```

It exits without deletion and tells the operator to provide explicit confirmation. To remove the
bounded generated analytical/cache files from the default project runtime:

```bash
.venv/bin/kestrel clean-generated --yes
```

Safety behavior:

- it refuses a custom or symlinked runtime directory;
- it resolves every configured DB/cache target and refuses a target outside the project
  `.kestrel/` directory or any target that is itself a symlink;
- it removes the configured analytical DuckDB and the freight, competitor, weather, and holiday
  JSON caches, plus the generated Parquet and log directories;
- it does **not** remove the supplied source, `.env`, UI/QA audit JSON files, or unrelated
  `.kestrel/` paths; and
- after the log directory is removed, the observability wrapper writes the final cleanup result to
  a newly created `.kestrel/logs/kestrel.jsonl`.

Archive any required DuckDB, caches, Parquet, or logs before confirmed cleanup. Do not use a broad
recursive deletion command. Rebuild and rerun the required explicit integrations after cleanup.

## 9. CI and handoff evidence

GitHub Actions runs on pushes and pull requests with read-only repository permission:

1. version-pinned `requirements-dev.lock` install plus `--no-deps` project install;
2. Ruff;
3. mypy;
4. pytest; and
5. a separate image build with pinned Python application dependencies.

Before handoff, record the commit SHA and verify:

```bash
git status --short
git diff --check
git ls-files | rg '(^data/source/|^\.kestrel/|\.db$|\.parquet$|^\.env$)'
```

The final command should not list a supplied database, cache, Parquet export, `.env`, operation log,
or audit report. Keep release evidence in the repository documentation or CI, not by committing the
generated client data.
