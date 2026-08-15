# Decisions

## What I am building

A modular Streamlit control tower backed by a reproducible DuckDB analytical build. The front page
defaults to FY 2026–27 Q1 and exposes eaches fill rate, strict OTIF, timestamp-derived on-time rate,
cold-chain risk, approved credit-note leakage, freight cost, immediate worst performers, and
evidence drill-downs. Freight is ingested from the supplied API with pagination, retries, paise to
rupee conversion, caching, and freshness. BazaarPulse is scraped within `robots.txt`, matched with
pack/brand confidence, and labelled by observation date. Ask Kestrel maps supported questions to a
validated metric query and uses the same deterministic calculations as the dashboard.

## Key judgments and assumptions

- Eaches is the default because Sales gave the later, commercially specific requirement; users may
  switch to case-equivalents calculated with `case_pack_at_order`.
- Completed service includes `DELIVERED` and `PARTIAL`; `OPEN` is backlog and `CANCELLED` is excluded.
- Current closed/deleted and clearly named test outlets are excluded from service rankings, with the
  current-status limitation displayed.
- In-full is strict at every order line. Because every supplied line is short, strict OTIF is 0%; no
  undocumented tolerance is invented. Fill rate and on-time remain visible diagnostics.
- Parsed planned/actual timestamps define on-time. Stored `delay_minutes` is retained as a conflicting
  source signal because it often disagrees with the timestamps.
- Near expiry means available stock expiring within 30 days of the latest selected snapshot.
- Approved credit notes are measured leakage. Pending/rejected values are separate. Return quantity
  signs are normalised to absolute quantities.
- Freight is attributable only to service date × warehouse × route (and carrier where available), not
  to an individual order. Competitor prices are latest observed listings, not live prices.
- “Why” means measured contribution or association; the system does not claim causation or blame.

## Deliberately not built

No graph database, universal flattened fact, unrestricted text-to-SQL, authentication/RBAC, or
accounting-profit calculation. Weather and holiday enrichment are deferred unless they add a stable,
adequately sampled association after the core product is proven.

## With two more weeks / what breaks first

Add incremental orchestration, SCD customer/product assignments, reviewed competitor mappings,
role-based regional access, richer anomaly alerts, and monitored deployment. At 100× volume, the
single-node rebuild and synchronous Streamlit query path fail first; move partitioned raw data to
object storage and marts to a managed warehouse while retaining the semantic API and metric tests.
