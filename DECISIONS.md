# Decisions

## What I built

An eight-workspace Streamlit control tower over reproducible SQLite-to-DuckDB/Parquet validates 13 tables, CSV headers, row-count parity, and native grains through 24 versioned metrics. Ask Kestrel combines deterministic rules, spelling/follow-ups, and optional MiniLM intent matching; typed services calculate. Freight and BazaarPulse are resumable, evidence-preserving integrations. Pinned dependencies, CI, non-root containers, recovery, and UI audits support clean checkout.

## Key judgments and boundaries

- Eaches is default; order-time cases remain selectable. Fill/OTIF use requested date, delivery/cold chain actual date, returns/freight event dates, and inventory its latest snapshot.
- In-full is strict. Every supplied line is short, so OTIF is 0%; no tolerance is invented. Parsed timestamps define on-time; conflicting stored delay remains evidence.
- PAID freight plus detention is settled freight; without a delivery key, cost and cases meet only at period and DC/route grains. Approved credits are leakage, not profit.
- Competitor evidence is source-dated; context and "why" are associations. Excluded: graph database, mega-fact, generative LLM, unrestricted text-to-SQL, profit model, RBAC, and automated actions.

## Two-week pilot and scale

Pilot operational-risk and commercial-prioritisation models using minimum ERP/WMS/TMS, CRM/POS, promotion-spend, or newly licensed intelligence. Point-in-time features and out-of-time tests produce separate risk and outlet-SKU queues with confidence, evidence, and human approval. Draft marketing cohorts/briefs require approval. An optional bring-your-own-key LLM outside Git may call allowlisted tools and cite redacted evidence, never generate SQL, change formulas, access unrestricted rows, or act.

At 100x, single-node rebuilds and synchronous queries fail first; move partitioned raw data to object storage and marts to a managed warehouse while retaining controls.
