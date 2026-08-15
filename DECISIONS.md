# Decisions

## What I built

An eight-workspace Streamlit control tower over a reproducible SQLite → DuckDB semantic build and
fingerprinted Parquet exports. It validates all 13 supplied tables and CSV parity, preserves native
fact grains, defaults to FY 2026–27 Q1, and serves 24 versioned `1.0.0` metric contracts to the UI
and governed question services. Ask Kestrel is rules-first, adds conservative spelling and
session-scoped follow-ups, and can optionally use a pinned keyless local MiniLM model to map
paraphrases into its finite intent catalogue; calculations remain behind typed metric services.

The full-history freight client is retryable, resumable, and last-good protected. BazaarPulse uses
allowed listing/detail pages, conservative automatic matching, reviewed YAML decisions,
current/append-only scrape evidence, immutable source-dated price history, a review queue, and a
read-only API. Optional weather and national holidays have isolated
caches and publication gates. Redacted JSONL operations, version-pinned package locks, CI, non-root
Docker/Compose, recovery documentation, and a repeatable eight-workspace audit complete the
delivery path.

## Judgments and assumptions

- Eaches is the default because Sales gave the later commercial requirement; order-time
  case-equivalents remain selectable.
- Completed service is `DELIVERED` or `PARTIAL` for currently active, non-deleted, non-test outlets.
- Fill/OTIF use requested date; delivery/cold chain use actual date; returns use return date;
  freight uses service date; inventory is snapshot-relative.
- In-full is strict on every line. All supplied lines are short, so strict OTIF is 0%; no tolerance
  is invented. Parsed timestamps define on-time, while conflicting stored delay remains evidence.
- Allocation, post-allocation fulfilment, backlog, failure labels, context, and “why” outputs are
  measured states or associations—not causation, responsibility, or blame.
- PAID invoice amount plus detention is the primary settled-freight view. All-status billed,
  pending, and disputed values remain explicit. Without a delivery key, invoice cost and delivered
  cases aggregate independently by shared period and DC or route; carrier case volume is not
  claimed.
- Approved credit notes are measured leakage, not profit. COGS, collections, labour, rent, tax,
  debt, and overhead are absent.
- Competitor observations are latest/source-dated, not live. Historical comparisons use the
  Kestrel MRP effective on the observation date and comparable packs normalize to 100G/100ML.
  Review cannot bypass minimum score, brand, or pack safety.

## Boundaries and next production steps

No graph database, universal flattened fact, cloud-LLM dependency, unrestricted text-to-SQL,
accounting-profit model, RBAC, or automated alert/approval workflow was added. City-centroid weather
and a national holiday calendar cannot establish route/outlet conditions or closure.

With two more weeks I would add incremental orchestration, effective-dated customer/product
assignments, role-scoped access, alert acknowledgement, secrets management, SLOs, and managed
deployment. At 100× volume, full single-node rebuilds and synchronous Streamlit queries fail first;
move partitioned raw data to object storage and marts to a managed warehouse while retaining the
same grains, metric versions, typed services, gates, and reconciliation tests.
