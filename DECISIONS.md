# Decisions

## What I built

A working seven-page Streamlit control tower backed by an atomic, reproducible DuckDB build. It
validates all 13 supplied tables and CSV parity, preserves native fact grains, defaults the landing
page to FY 2026–27 Q1, and exposes governed service, cold-chain, inventory, credit-note, freight,
and price-position evidence. Freight ingestion handles cursor pagination, paise conversion,
429/503/timeouts, resume checkpoints, and complete last-good caches. BazaarPulse collection follows
only allowed listing pages and accepts product matches only above confidence and ambiguity gates.
Ask Kestrel maps supported language to typed metric intents and fixed service methods; it never
executes generated SQL. CI runs lint, type checks, and deterministic tests.

## Judgments and assumptions

- Eaches is the default because Sales gave the later, commercially specific requirement;
  case-equivalents using `case_pack_at_order` remain selectable.
- Completed service is `DELIVERED` or `PARTIAL`. Current closed/deleted and clearly named test
  outlets are excluded; this current-status limitation is visible.
- Fill and OTIF use requested delivery date. Delivery operations use actual arrival date, returns
  use return date, freight uses service date, and inventory is snapshot-relative.
- In-full is strict at every line. Because every supplied line is short, strict OTIF is 0%; I did
  not invent a 95/98/99% tolerance.
- Parsed planned/actual timestamps define on-time. Stored `delay_minutes` is retained as a
  conflicting source because 25,734 rows change classification.
- Near expiry means positive available stock expiring within 30 days of the latest snapshot on or
  before the period end. Approved credit notes form the main leakage numerator; return signs are
  normalized to absolute quantities.
- Billed freight is API `amount + detention_charge`, converted from paise. The API has no delivery
  key, so cost and delivered cases are aggregated independently by service period × warehouse (or
  route). Carrier-level delivered cases are not claimed.
- Competitor results use current Kestrel MRP and latest observed available listings, not live or
  historical prices. “Why” means measured contribution/association, not causation or blame.

## Deliberately not built

No graph database, universal flattened fact, unrestricted chatbot/text-to-SQL, authentication/RBAC,
alerting workflow, or accounting-profit calculation. Weather and holiday enrichment were deferred:
the core brief is answerable without adding an external correlation that could be mistaken for a
cause.

## Two more weeks / first production limits

I would add scheduled incremental orchestration, SCD customer/product assignments, reviewed match
overrides, regional access control, anomaly alerts, observability/SLOs, and deployment. At 100×
volume the full local rebuild and synchronous Streamlit query path fail first; move partitioned raw
data to object storage and marts to a managed warehouse while keeping the same grains, semantic
service, metric contracts, and reconciliation tests.
