# Interview Demonstration Guide

## Demonstration goal

Show a reviewer that Kestrel is not a collection of charts: it is a governed decision system from
source contract through metric, evidence, question interface, and recovery. A complete route takes
about 12–15 minutes. If time is shorter, keep the Executive, Delivery, Market/Context, Ask, and
Trust moments; do not hide the strict-OTIF or attribution boundaries.

Use the live system as the source of truth. The reference figures below help detect a wrong filter
or stale build, but should not be recited without verifying the page scope.

## Before the meeting

1. Start from the exact repository state to be demonstrated.
2. Confirm the assignment pack is unpacked and not tracked.
3. Run the final release commands:

   ```bash
   make doctor
   make validate-data
   make build
   make scrape-prices
   make lint
   make test
   ```

   If the demonstration will include local paraphrase matching, install its optional public model
   once. This is not a core-app or release precondition:

   ```bash
   make setup-local-nlp
   ```

4. With the supplied freight mock server running and its key in local `.env`, refresh freight and
   context before the benchmark or if those evidence tracks will be shown:

   ```bash
   make sync-freight
   make sync-context
   ```

5. Run the current UI and governed-question release evidence:

   ```bash
   PYTHONPATH=src .venv/bin/python scripts/audit_ui.py \
     --expected-pages 8 \
     --threshold-ms 15000 \
     --output .kestrel/ui-audit.json
   PYTHONPATH=src .venv/bin/python scripts/benchmark_qa.py \
     --threshold-ms 5000 \
     --output .kestrel/qa-benchmark.json
   ```

6. In terminal 1, start the app without rebuilding:

   ```bash
   make run
   ```

   In terminal 2, optionally start the market API:

   ```bash
   PYTHONPATH=src .venv/bin/uvicorn kestrel.market_api:app \
     --host 127.0.0.1 --port 8089
   ```

7. Confirm [http://localhost:8501](http://localhost:8501), the Streamlit health endpoint, and the
   current audit JSON. Keep terminal font large enough for the reviewer.

**Do not say the final UI audit passed unless the current repository-state JSON says `passed: true`
and the root verification has accepted it.** Automated audit evidence is not WCAG certification.

## Opening statement — 30 seconds

> Kestrel follows the supply-chain value chain from order promise through allocation, fulfilment,
> delivery, cold-chain/inventory risk, returns, freight, and market position. The system keeps each
> fact at its native grain, publishes 24 versioned metric contracts to eight workspaces and the same
> governed question service, and exposes the data conflicts and attribution limits alongside the
> numbers.

Then point out the global period and geography filters. State that the default is FY 2026–27 Q1,
1 April through 30 June 2026; customer region and DC region are deliberately separate.

## 1. Executive Command Center — 2 minutes

Actions:

1. Leave the default Q1 period and eaches basis selected.
2. Contrast the explicit eaches and case-equivalent fill cards, then point to allocation,
   post-allocation fulfilment, timestamp on-time, strict OTIF, and open backlog.
3. Show PAID settled freight/case, competitor coverage, the ranked exception inbox, fulfilment flow,
   worst performers, best/most-improved warehouses, and recorded shortage signals.
4. If an order-only customer/route/outlet/channel filter is active, point out that the inventory
   callout identifies the one near-expiry card that cannot use it.

Narrative:

- The service chain is decomposed into `allocated / ordered`, `delivered / allocated`, and
  `line-capped delivered / ordered`. The fill cap is applied per line, so one oversupplied line
  cannot offset another line's shortage. This separates measured gates without claiming why a
  shortfall occurred.
- Strict OTIF is 0% because every supplied order line is short-delivered. The system preserves that
  finding and keeps fill and on-time visible instead of inventing an in-full tolerance.
- Rankings show volume and best/most-improved require volume in the relevant periods; a tiny group
  cannot win solely through an unstable percentage.
- Backlog is current OPEN-order state as of the selected period end, not reconstructed historical
  status.

Q1 orientation values from the verified expanded implementation were approximately 88.43%
allocation, 96.86% post-allocation fulfilment, and 1,480 overdue eligible OPEN orders. Verify the
live scope before quoting them.

## 2. Service & Fulfilment — 1.5 minutes

Actions:

1. Show ordered → allocated → delivered → linked returned, then toggle eaches to case-equivalents.
2. Choose customer/DC region, warehouse, route, outlet, recorded promotion/mechanic, or order source.
3. Choose a shortage contributor and open bounded evidence containing raw/IST/parse-status creation
   fields and proportional short-delivery booked-value exposure.

Narrative:

- Mixed UOM is normalized with `case_pack_at_order`; published percentages are ratios of sums, not
  averages of line percentages.
- Requested delivery date defines this customer-promise cohort.
- Promotion and order source are recorded associations; the app does not infer redemption, lift, or
  cause. Order creation is parsed by source and retained for evidence, but no KPI uses it.
- Parsed planned/actual timestamps are the governed timing signal. Stored delay remains beside it
  because the two sources conflict materially; raw conflict evidence is not silently overwritten.

## 3. Delivery & Exception Drivers — 2 minutes

Actions:

1. Contrast its actual-delivery date basis with the requested-date service workspace.
2. Show on-time, >2-hour-late, POD coverage, delay conflicts, and recorded failure labels.
3. Change the minimum deliveries threshold and show route/vendor comparisons.
4. Show the recorded-failure Pareto and row-level timestamp/stored-delay evidence.

Narrative:

- The inclusion threshold changes ranking stability, not underlying records or formulas.
- Q1 orientation values were approximately 38.50% delivery-cohort on time, 44.06% more than two
  hours late, 94.01% POD coverage, 87.39% delay-source conflicts, and 25.18% recorded failure
  labels. Verify the current page first.
- A route, warehouse, telematics vendor, or reason label is associated evidence. It is not proof of
  root cause, responsibility, or blame.

## 4. Cold Chain & Inventory Risk — 1 minute

Actions:

1. Show excursions per 100 chilled deliveries, its distinct-delivery denominator, the unfiltered
   monthly severity trend, and the default 25-delivery floor on ranked warehouse/route/category
   hotspots.
2. Show near-expiry cases, selected snapshot date, default 30-day window, and at-risk batch rows.
3. Show exact RT06 lines, including order-line key, reason, workflow status, raw sign, normalized
   eaches/case-equivalents, value, and disposition.

Narrative:

- A delivery is chilled when any associated order line contains a chilled SKU and counts once.
- The hotspot floor changes which groups qualify, not the source-flag formula; Ask Kestrel uses the
  same minimum 25 for ranked cold-chain dimensions.
- Inventory is the latest weekly snapshot on or before period end; it is not interpolated or
  compared to today's clock.
- The source excursion flag is retained because temperature maxima do not reliably reproduce it.

## 5. Commercial Leakage & Logistics Cost — 1.5 minutes

Actions:

1. Show approved-credit and proportional short-delivery booked-value exposure, then recorded
   RESTOCK/SCRAP/VENDOR_RECOVERY disposition evidence.
2. Show freight availability/freshness, the primary PAID settled cost per delivered case-equivalent,
   all-status billed/pending/disputed values, DC/route rankings, and carrier spend.
3. Point to ignored-filter and attribution disclosures if a customer-only filter is active.

Narrative:

- The credit denominator is an estimated, per-line-capped delivered fraction of booked line value.
  It is measured gross commercial leakage, not profit, margin, or collected cash.
- PAID invoice amount including detention is the primary settled view. All-status billed, pending,
  and disputed values remain explicitly separate.
- Short-delivery and disposition values are exposure/workflow evidence, not cash loss or recovery.
- The API has no order/delivery key. Freight and delivered cases are aggregated independently at
  shared period × DC or route; carrier spend is valid, but carrier-attributed case volume is not.

If asked about resilience, cite the complete full-history walk: 41,500 invoices, 208 pages, 237
requests, 29 retries, with cursor termination, retry/checkpoint/resume, atomic cache, and last-good
protection.

## 6. Market & External Context — 2 minutes

Actions:

1. Select Mumbai and show top-value Kestrel SKUs, retailer/listing evidence, current raw and
   100G/100ML price gaps, observation dates, and unmatched coverage.
2. Show match quality, the unresolved review queue, source-history coverage, one listing's
   source-dated history with effective-dated Kestrel MRP, and the service-price attention table.
3. Scroll to weather and holiday association panels. Show either a passed gate with coverage/cohort
   evidence or a withheld result with its exact reasons.

Narrative:

- Only final high-confidence, non-ambiguous, available listings enter headline price gaps. A
  reviewed match still cannot bypass a 0.80 candidate score or brand/pack conflicts.
- Review decisions are accountable YAML changes with reviewer, date, and note. The API is read-only;
  observation and match history is append-only/idempotent.
- Current cards and source-dated detail history are separate: 6,804 source observations cover 1,134
  detail pages from 6 May through 30 June 2026. Missing IDs 387/458/777 are structured warnings;
  historical comparisons use the MRP effective on the observation date.
- Weather is a warehouse-city-centroid proxy; holiday is a national public-calendar flag. Results
  require completeness, period, freshness, join, and 30-order-per-cohort gates and are descriptive
  associations only.

Verified optional-source orientation is 1,137 current competitor listings (1,088 matches and 49
review rows), 6,804 source-dated price observations, 4,368 weather rows, and 27 national
public-holiday rows. Use Trust Center to confirm the live snapshot rather than treating these as
permanent constants.

Optional API proof in a terminal:

```bash
curl --fail 'http://127.0.0.1:8089/market/review-queue?city=Mumbai&limit=5'
```

Explain that GET endpoints expose evidence and do not mutate matches.

## 7. Ask Kestrel — 1.5 minutes

First demonstrate a governed answer:

```text
Which five outlets had the lowest case fill rate?
```

Show the interpreted period/basis, definition, warning, and supporting rows. Then demonstrate that
the system refuses to guess:

```text
What was fill rate in the West region?
```

It should ask whether “West” means customer or DC/warehouse region. Resolve it explicitly:

```text
What was fill rate in the West customer region?
What was fill rate in the West DC region?
```

Show a conservative spelling repair and a session-scoped follow-up:

```text
Which five outltes had the lowest case fill rte?
What about the North customer region?
```

The follow-up may inherit only the preceding supported metric fields and should expose that
inheritance in its interpretation. A failed or ambiguous follow-up must leave the last successful
context unchanged. If `make setup-local-nlp` was run, also show a wording that does not contain the
canonical metric phrase:

```text
Where are customers receiving the smallest share of what they asked for?
```

Then show the repaired evidence boundaries:

```text
Which routes are more than two hours late on more than one delivery in ten?
Which categories drive the largest value of returns, and what is the leading reason code?
```

Narrative:

- This is a rules-first, finite-intent question interface, not a general chatbot. Exact rules,
  spelling repair, and follow-up memory operate locally.
- The optional MiniLM embedding model is keyless and local. It only maps paraphrases to a supported
  intent, with confidence and ambiguity gates; it does not generate the answer.
- Every accepted question calls the same deterministic metric services as the pages and cannot
  execute unrestricted model-generated SQL or change a governed formula.
- The late-route answer uses actual delivery date and excludes routes below 25 deliveries. The
  return answer ranks categories by APPROVED credit-note value, reports the leading APPROVED reason
  within every returned category, applies documented deterministic tie-breaks, and appends
  PENDING/REJECTED workflow-status evidence.
- A missing model falls back gracefully to exact rules. Unsupported, uncertain-semantic,
  multi-metric, contradictory-basis, or ambiguous-geography questions fail safely and offer
  guidance.

## 8. Trust Center — 1.5 minutes

Actions:

1. Show operational range, analytical snapshot time, eligible records, and the 24-entry registry
   with 22 definitions at `1.0.0` and the two freight definitions at `1.1.0`.
2. Show strict OTIF, geography, delay, freight-key, and competitor-key boundaries.
3. Show the bounded recent operation events and external source readiness/sync history.
4. In a terminal, open `.kestrel/parquet/manifest.json` and one redacted JSONL event if useful.

Narrative:

- A build stages and reconciles DuckDB and compressed Parquet, fingerprints the source, writes a
  manifest, and coordinates promotion/rollback. Existing external physical state and compatible
  views survive the operational rebuild.
- Governed CLI operations record STARTED then SUCCEEDED/FAILED events with run ID and duration.
  Sensitive-key fields are recursively redacted; generated logs stay outside Git.
- Definitions, formula warnings, freshness, and quality conflicts are part of the product, not
  hidden implementation notes.

## Closing statement — 30 seconds

> The system answers where the measured service and commercial signals occur, preserves evidence
> when source definitions conflict, and refuses unsupported precision. The next production step is
> incremental orchestration and durable object/warehouse storage while retaining these grains,
> versions, publication gates, and typed query boundaries.

## Likely reviewer questions

**Why is OTIF zero?**

Strict in-full means every line delivered at least ordered. All 511,516 supplied lines are short,
so no order qualifies. The UI says this is the supplied-data result under the strict definition;
it does not invent a tolerance.

**Why not join freight invoices to deliveries?**

No delivery/order key exists. Warehouse-route pairs also cannot justify a unique row link.
Independent period × DC or route aggregation is defensible; row-level or carrier-level attribution
is not.

**Why not call this profit?**

The pack lacks COGS, collection, labour, rent, tax, bad debt, and overhead. The product reports
approved credit leakage and billed freight, not accounting profit.

**Why no unrestricted natural-language SQL?**

It could change eligibility, grain, date basis, or joins invisibly. The typed router keeps answers
inside the same tested contracts as the dashboard.

**Why preserve both stored and parsed delay?**

They conflict materially. Parsed events are the chosen KPI basis, while the stored value remains
audit evidence and a data-quality signal.

**Does weather explain delay?**

No. It is a warehouse-city daily association published only through coverage and cohort gates. The
system estimates no causal effect.

**Can a reviewer force a competitor match?**

No. Accountable review may resolve plausible ambiguity, but it cannot bypass the candidate-score,
brand, or pack safety gates.

**What happens if a rebuild fails?**

The build uses unique staging paths. It promotes only after validation/reconciliation/export and
restores the former Parquet release on a handled DB-promotion failure. A host/process interruption
between the two replacements requires a manifest/fingerprint check and a rerun; the files do not
share one transaction.
