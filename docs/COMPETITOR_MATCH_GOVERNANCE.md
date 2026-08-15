# Competitor match governance

BazaarPulse listings are matched automatically only when the best product clears both the
configured confidence threshold and the ambiguity margin. Every unresolved record remains
unmatched and appears in the read-only review queue.

## Recording a reviewed decision

Edit `config/competitor_match_decisions.yml` in a branch and add exactly one decision for the
listing ID. A decision requires an accountable reviewer, ISO review date and explanatory note.
Use `action: match` with an existing `sku_code`, or `action: reject` without a SKU. Submit the
change through normal source review so Git retains who changed the registry and why.

The ingestion validates the entire file before publishing. A reviewed match is still refused if
its candidate score is below 0.80 or if normalized brand or pack attributes conflict. This means
manual review can resolve plausible ambiguity but cannot force a weak entity link. A rejection
is terminal for the current mapping and is excluded from the unresolved queue; it remains visible
in match history and provenance.

## Audit and history

Each successful scrape writes a current snapshot and idempotently appends its scrape-run listing and
match outcomes using `sync_id + listing_id` identity. Replaying the same sync timestamp with an
identical payload does not duplicate history; a changed payload under the same identity is rejected.
It separately appends immutable, source-dated price observations from allowed detail pages under a
stable observation identity. Atomic warehouse rebuilds copy these external tables and compatible
audit views into the staged database before promotion, so both histories survive a rebuild.

The verified source snapshot contains 1,137 current listings, 1,088 governed matches, and 49 review
rows. It contains 6,804 source-price observations from 1,134 detail pages over 2026-05-06 through
2026-06-30. Listing IDs `387`, `458`, and `777` have no supplied detail page and are retained as
structured warnings. Historical comparison resolves the Kestrel MRP effective on each observation
date and exposes raw shelf price plus 100G/100ML normalized values only when mass/volume packs are
comparable; missing/non-comparable values remain null.

The current and historical views expose retailer/listing/pack evidence, automatic outcome,
suggested candidate, final outcome, provenance, decision-file path, reviewer, date, and note.

Read-only endpoints:

- `GET /health`
- `GET /market/review-queue?city=Mumbai&limit=100`
- `GET /market/listings/{listing_id}/history?limit=100`

Run the API from the project environment with:

```bash
uvicorn kestrel.market_api:app --host 127.0.0.1 --port 8089
```

The API performs no match mutations. YAML plus peer review is the only write path.
