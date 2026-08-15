# Decisions

## What I built

An eight-workspace Streamlit control tower over a reproducible SQLite → DuckDB semantic build and
fingerprinted Parquet exports. It validates all 13 supplied tables and CSV parity, preserves native
fact grains, defaults to FY 2026–27 Q1, and serves 24 versioned metric contracts—22 at `1.0.0`
and two freight contracts at `1.1.0`—to the UI and governed question services. Ask Kestrel is
rules-first, adds conservative spelling and session-scoped follow-ups, and can optionally use a
pinned keyless local MiniLM model to map
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

No graph database, universal flattened fact, generative-LLM dependency, unrestricted text-to-SQL,
accounting-profit model, RBAC, or automated alert/approval workflow was added. The optional MiniLM
component recognises finite paraphrases; it does not generate analysis. City-centroid weather and a
national holiday calendar cannot establish route/outlet conditions or closure.

## Two-week governed pilot

The two-week goal would be a governed **decision-prioritisation pilot**, not a fully autonomous
forecasting or marketing platform:

1. Select one operational-risk use case and one commercial-prioritisation use case, then connect
   only the one or two priority sources needed from ERP/WMS/TMS, inventory, returns, CRM/POS,
   promotion-spend, or a licensed market-intelligence feed. Use versioned contracts, freshness
   checks, last-good snapshots, cost limits, and secrets outside Git; the other connectors remain a
   post-pilot roadmap.
2. Build point-in-time baseline models at defensible grains—for example, service-failure risk at
   order/delivery or route grain and commercial opportunity at outlet × SKU grain. Publish separate
   priority queues that say **where to focus**, estimated risk/exposure or opportunity under stated
   assumptions, confidence, contributing evidence, and a recommended human action. A portfolio
   view may compare queues but must not force them into an invented universal row-level join.
3. Produce target cohorts and draft marketing briefs/materials using stock availability, service
   capacity, promotion history, approved product facts, and governed competitor/advertising
   intelligence. A person must approve targeting, claims, creative, budget, price, and campaign
   launch; observed association is not presented as incremental marketing lift. BazaarPulse is
   currently price/listing evidence; advertising intelligence would be a new licensed feed.
4. Add an optional bring-your-own-key LLM adapter through environment/managed secrets. It may
   interpret questions, call only allowlisted metric/prediction tools, explain scores, cite evidence,
   and draft approved-template briefs. The pilot exposes only redacted aggregate evidence to the
   LLM; source applications enforce row-level access. It cannot write arbitrary SQL, redefine
   formulas, or execute operational/marketing actions.

Week one would select the two use cases, connect the minimum required sources, build point-in-time
feature/label snapshots, and establish simple-rule baselines with out-of-time validation. Week two
would ship use-case-specific action queues, a redacted allowlisted LLM/brief prototype, explicit
human approval and audit logging, freshness/drift checks, and a limited deployed pilot. Acceptance
requires predictive performance above the rule baseline, calibration where probabilities are
shown, traceable evidence, fail-closed behaviour, and measured operating/API cost. Weak data
produces “insufficient evidence,” not a fabricated prediction. Causal or incremental marketing lift
requires a later controlled experiment.

After validation, production work would expand connectors and models and add effective-dated master
data, full role-scoped access, alert acknowledgement, secrets management, SLOs, and managed
deployment.

At 100× volume, full single-node rebuilds and synchronous Streamlit queries fail first; move
partitioned raw data to object storage and marts to a managed warehouse while retaining the same
grains, metric versions, typed services, gates, and reconciliation tests.
