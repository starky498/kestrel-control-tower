# Governed Metric Contracts

## Contract and registry

Every published metric has a stable key, semantic version, title, formula, grain, date basis,
eligible population, numerator, denominator, unit, status, and warning in
`config/metrics.yml`. The dashboard and Ask Kestrel load that registry and call the same
parameterized metric services. Presentation code does not redefine a formula.

The current registry contains 24 definitions: 22 at version `1.0.0` and the two freight metrics at
version `1.1.0` after adding explicit source-coverage intersection semantics:

| Family | Metric key | Formula summary | Grain and canonical date |
|---|---|---|---|
| Promise | `allocation_rate` | allocated quantity / ordered quantity | Order line ratio of sums; requested delivery date |
| Promise | `post_allocation_fulfilment` | delivered quantity / allocated quantity | Order line ratio of sums; requested delivery date |
| Promise | `fill_rate` | line-capped delivered quantity / ordered quantity | Order line ratio of sums; requested delivery date |
| Promise | `fill_rate_eaches` | line-capped delivered eaches / ordered eaches | Order line ratio of sums; requested delivery date |
| Promise | `fill_rate_case_equivalents` | line-capped delivered case-equivalents / ordered case-equivalents | Order line ratio of sums; requested delivery date |
| Exposure | `short_delivery_value_exposure_inr` | booked value associated proportionally with undelivered quantity | Eligible completed order line; requested delivery date |
| Promise | `strict_otif` | strict in-full **and** on-time orders / eligible completed orders | Order; requested delivery date |
| Promise | `on_time_rate` | timestamp-on-time orders / eligible completed orders | Delivery rolled to order service; requested delivery date |
| Promise | `late_over_2h_rate` | timestamp delay over 120 minutes / eligible completed orders | Delivery rolled to order service; requested delivery date |
| Backlog | `overdue_backlog_orders` | count of eligible OPEN orders due by reporting as-of date | Current order state; selected period end |
| Delivery | `delivery_on_time_rate` | timestamp-on-time deliveries / eligible deliveries | Delivery; actual delivery date |
| Delivery | `delivery_late_over_2h_rate` | timestamp delay over 120 minutes / eligible deliveries | Delivery; actual delivery date |
| Delivery | `pod_coverage_rate` | deliveries with POD captured / eligible deliveries | Delivery; actual delivery date |
| Delivery | `delay_source_conflict_rate` | deliveries with delay reconciliation conflict / eligible deliveries | Delivery; actual delivery date |
| Delivery | `recorded_failure_rate` | deliveries with a failure-reason label / eligible deliveries | Delivery; actual delivery date |
| Cold chain | `temperature_excursions_per_100` | 100 × flagged chilled deliveries / chilled deliveries | Distinct delivery; actual delivery date |
| Inventory | `near_expiry_cases` | available cases with 0–30 expiry days | DC × SKU × batch; latest eligible snapshot |
| Lifecycle | `orders_after_discontinuation` | order lines placed after the product discontinuation date | Order line; order date |
| Leakage | `approved_credit_note_value_inr` | sum of APPROVED credit-note value | Credit-note line; return date |
| Leakage | `approved_credit_note_rate` | approved credit value / estimated delivered dispatch value | Separately aggregated facts; return/requested dates |
| Freight | `freight_cost_per_case` | billed freight / delivered case-equivalents | Separately aggregated period × DC/route; service/actual dates |
| Freight | `settled_freight_cost_per_case` | PAID freight / delivered case-equivalents | Separately aggregated period × DC/route; service/actual dates |
| Market | `competitor_price_gap` | current Kestrel MRP − lowest governed observed competitor price | SKU × city; latest listing observation |
| Market | `competitor_match_coverage` | governed current matches / all current listings | Current listing snapshot; latest governed collection |

## Common eligibility and quantity rules

Operational service metrics use `DELIVERED` and `PARTIAL` orders for outlets that are currently
active, not deleted, and not test outlets. OPEN orders are excluded from completed-service ratios
and enter only the governed backlog metric. Empty or zero denominators produce “not available,”
never a fabricated zero.

Each order line is normalized with its order-time pack:

```text
if qty_uom = CASE:
    eaches = quantity × case_pack_at_order
    case_equivalents = quantity
else:
    eaches = quantity
    case_equivalents = quantity / case_pack_at_order
```

Eaches is the default because it is the later, commercially specific stakeholder request.
Case-equivalents remain selectable, and the Executive workspace publishes both explicit
`fill_rate_eaches` and `fill_rate_case_equivalents` cards side by side. The basis-sensitive
`fill_rate` remains the reusable selector-backed contract. Every grouped percentage is a ratio of
summed numerators and denominators; line or subgroup percentages are never averaged.

## Promise, allocation, and service

```text
allocation rate             = sum(allocated) / sum(ordered)
post-allocation fulfilment  = sum(delivered) / sum(allocated)
fill rate                   = sum(min(delivered, ordered) per line) / sum(ordered)
```

Delivery is capped separately on every order line for fill only. This prevents an oversupplied
line from offsetting another line's shortage. Allocation and post-allocation fulfilment retain
their recorded quantities and their separate formulas.

The Service workspace also shows ordered → allocated → delivered quantities from the same
requested-delivery line cohort, followed by exactly linked physical return quantities observed
through the selected period end. Return quantity is not another fulfilment gate and is not cash or
recovery. Recorded `order_source` and `promo_code` (enriched from the promotion catalogue) can
segment supporting evidence, but association with a promotion is not redemption or causal lift.

The three gates identify where recorded loss occurs in the chain without asserting its cause.
Allocation shortfall is `max(ordered − allocated, 0)`; post-allocation shortfall is
`max(allocated − delivered, 0)`. Rankings carry volume and use an explicit minimum-volume guard
where the UI compares best, worst, or most-improved groups. Ties are deterministic.

### Strict OTIF

```text
line_in_full  = delivered_eaches >= ordered_eaches
order_in_full = every eligible line_in_full
on_time       = parsed actual_arrival <= parsed planned_arrival
strict_OTIF   = order_in_full AND on_time
```

OTIF is evaluated at order grain, not line grain. Every one of the 511,516 supplied order lines is
short-delivered, so the supplied data produces 0% strict in-full and strict OTIF. This is a data
finding under the documented strict definition, not an assertion that a tolerance-based business
SLA would also be zero. No undocumented 95%, 98%, or 99% tolerance is substituted.

Parsed timestamps are the primary on-time source. The raw stored delay is retained because it
differs from parsed timestamp delay on 67,211 deliveries and changes on-time classification on
25,734. These are reconciliation signals, not records to silently repair.

### Backlog

`overdue_backlog_orders` counts currently OPEN orders for eligible outlets whose requested
delivery date is on or before the selected period end. Source order status is current state, so a
historical period selection is an as-of reporting cut over current status, not a reconstruction of
what status was on that past date.

## Delivery and exception evidence

The Delivery & Exception Drivers workspace deliberately uses actual delivery date. Its five
versioned rates are:

```text
delivery on time       = actual_arrival <= planned_arrival
more than 2h late      = actual_arrival - planned_arrival > 120 minutes
POD coverage           = pod_captured deliveries / eligible deliveries
delay conflict         = reconciled-conflict deliveries / eligible deliveries
recorded failure       = deliveries with non-empty failure_reason_code / eligible deliveries
```

A delay conflict means the stored delay differs from the timestamp-derived delay by more than one
minute or the timestamps cannot support reconciliation. The workspace reports rates with counts,
trends, minimum-volume-qualified route/vendor comparisons, a Pareto of recorded failure labels,
and row evidence. A telematics vendor or failure label is an associated source attribute; it does
not establish responsibility, root cause, or blame.

The promise-cohort `on_time_rate` and `late_over_2h_rate` answer service-commitment questions by
requested delivery date. The delivery-cohort equivalents answer operational exception questions
by actual delivery date. They must not be presented as interchangeable.

Ask Kestrel's late-route question uses the actual-delivery cohort, the strict `>120 minutes` and
`>10%` thresholds, and a minimum of 25 actual-date deliveries per route. The floor is included in
the answer warning so a tiny route cannot enter the ranking silently.

## Cold chain and inventory

```text
temperature excursions per 100 =
    100 × distinct chilled deliveries flagged with an excursion
        / distinct eligible chilled deliveries
```

A delivery is chilled when any associated order line contains a product with `is_chilled = true`.
Each delivery counts once. The source excursion flag is governed because recorded maximum
temperature does not reliably explain it. An excursion flag on a non-chilled delivery is a data
quality exception and is excluded from this KPI.

Ranked warehouse, route, customer, channel, category, promotion, and order-source hotspot views
apply a minimum of 25 eligible chilled deliveries per group by default. The volume floor is applied
before ranking and row limits, the exact denominator remains visible, and changing the floor changes
inclusion only—not the source-flag formula. The monthly cold-chain trend is deliberately unfiltered
by this ranking floor so every month with an eligible chilled-delivery denominator remains visible.

Near-expiry inventory sums positive `available_cases` with expiry days from zero through the
configured window, 30 days by default, at the latest weekly snapshot on or before the selected
period end. The snapshot date and threshold travel with the result. Inventory is not interpolated
and is never compared with the computer's current date. Inventory can apply DC-region and DC
filters; it has no historical customer/order key, so customer region, route, outlet, and channel
are ignored for this component and disclosed when active on Executive or Cold Chain pages.

Cold-chain-associated returns use the documented source reason labels and exact original order-line
link. The evidence retains workflow status, raw signed quantity, sign-normalization flag, eaches,
case-equivalents, and disposition. The language remains “associated with,” not “caused by.”

## Measured leakage

```text
estimated line dispatch value =
    booked line_value_inr × min(delivered_qty / ordered_qty, 1)

approved credit-note leakage rate =
    sum(APPROVED credit_note_value_inr) / sum(estimated line dispatch value)

short-delivery booked-value exposure =
    sum(line_value_inr × max(ordered_qty − delivered_qty, 0) / ordered_qty)
```

The numerator includes every APPROVED source credit-note line matching the return-date and active
return dimensions; it does not inherit completed-service eligibility. The dispatch denominator is
limited to eligible completed-service lines and grouped by requested delivery date over the same
selected range. These are deliberately separate populations, not a same-order return ratio.
Pending and rejected values remain visible separately. Return quantities are normalized with the
originating line's order-time pack and sign rules.

The dispatch fraction is capped per line, so oversupply cannot inflate the denominator. Ask
Kestrel ranks only APPROVED value by the requested dimension and appends a separate
APPROVED/PENDING/REJECTED workflow-status evidence block. For the combined category-and-reason
question, category totals rank by approved value, then line count, then category name. The leading
reason within each category ranks by approved value, then line count, then reason code. This is a
deterministic nested breakdown, not two unrelated grouping queries.

The short-delivery calculation is a proportional booked-value exposure on eligible completed order
lines with positive ordered quantity. It is ranked by recorded dimensions including product,
customer/warehouse geography, order source, and promotion where supported. It is not an invoice,
credit, cash loss, or causal attribution.

Return disposition views retain the source labels `RESTOCK`, `SCRAP`, and `VENDOR_RECOVERY` with
their recorded quantities and credit-note values. A disposition label is physical/workflow evidence;
it does not prove resale, destruction, vendor settlement, or recovered cash.

These are measured commercial leakage/exposure estimates. They are not profit, margin, cash
received, or loss after cost. COGS, collections, labour, rent, tax, bad debt, and overhead are not
supplied.

## Freight

```text
billed freight INR = amount_paise / 100 + detention_charge_paise / 100

settled freight per delivered case-equivalent =
    sum(billed freight INR where invoice_status = PAID)
        / sum(delivered case-equivalents)

billed freight per delivered case-equivalent =
    sum(billed freight INR) / sum(delivered case-equivalents)
```

The API provides no order or delivery key. Invoice numerator and delivered-case denominator are
therefore aggregated independently over the same freight service/actual delivery period and DC (or
route where defensible), then divided. There is no invoice-to-delivery row join. Customer region,
outlet, and channel filters cannot apply symmetrically and are ignored with disclosure rather than
being applied to one side.

That common period is the intersection of the requested calendar window and the observed minimum
and maximum `ext_freight_invoice_current.service_date`. The effective boundaries constrain both the
invoice `service_date` numerator and eligible `fct_order_service.delivery_date` denominator. A
partial overlap is calculated only over that disclosed intersection; a window wholly outside
observed freight coverage is reported as unavailable, not as zero. Carrier-spend views use the same
effective invoice period.

The primary card is `settled_freight_cost_per_case`: PAID invoice amount plus detention divided by
delivered case-equivalents. The all-status `freight_cost_per_case` remains an explicitly billed
secondary view, with pending and disputed components stated separately. Carrier invoice spend is
valid. Carrier-attributed delivered cases or carrier cost per case are not available because
operational deliveries contain no carrier key. Driver-entered fuel cost is not used as carrier
freight. Route tables can rank the lowest current billed ratio, worst ratio, and most improved
ratio only after the stated invoice/delivery volume gates. For a partially covered current window,
the prior comparison is the immediately preceding window with the same length as the effective
current period; if source coverage cannot contain that complete prior window, the comparison is
unavailable. This remains an aggregate comparison, not row-level attribution.

The optional full refresh requests invoice dates 2025-01-01 through 2026-06-30. Verified evidence
contains 41,500 invoices over 208 pages, 237 requests, and 29 retries. Observed service dates begin
2024-12-29 because service can precede invoice date.

## Market position

```text
price gap INR = current Kestrel MRP − lowest latest observed in-stock competitor price
MRP premium % = current Kestrel MRP / competitor price − 1
```

Top Kestrel SKUs are ranked by eligible estimated dispatch value for the selected period. The
current competitor side uses the lowest latest in-stock governed listing for the chosen city and
retains its retailer, listing ID, raw shelf price, pack, normalized unit, stock state, and observed
date. Exact normalized packs can be compared directly; comparable mass and volume packs also expose
100G/100ML unit prices. Non-comparable packs remain explicit nulls. Only a final matched, available
listing can affect the price comparison. Unmatched and ambiguous top SKUs stay visible as coverage
gaps.

Automatic matching combines normalized name, brand, pack, and category evidence and requires both
confidence and separation from the runner-up. Source-controlled reviewed match/reject decisions
retain reviewer, date, note, decision source, automatic outcome, and final outcome. A reviewed
match cannot bypass candidate score 0.80 or brand/pack conflicts. Current position, append-only
scrape audit history, and source-dated detail-page history are different evidence products. The
verified snapshot has 1,137 current listings, 1,088 governed matches, and 49 review-queue rows.
Detail pages supply 6,804 immutable observations for 1,134 listings over 6 May–30 June 2026; missing
detail pages for IDs `387`, `458`, and `777` are retained as structured warnings.

Historical comparisons resolve `product_price_history` on the source observation date, never by
substituting today's MRP, and retain both raw pack price and 100G/100ML normalized evidence where
units are comparable. The Market workspace also combines selected-period service/short-delivery
exposure with governed source-price position as an attention list; it is descriptive prioritization,
not evidence that competitor price caused service performance. No weekly observation is described
as live.

## Optional context associations

Weather and public holidays are contextual evidence, not registry KPIs and not inputs to service
scores. When published, each cohort reports observed late rate, chilled temperature-excursion rate,
and weighted eaches fill rate.

A context result is withheld unless all applicable gates pass:

1. A typed snapshot and successful, complete sync record both exist.
2. The entire selected period lies inside observed source coverage.
3. Cache age is no more than 365 days.
4. Weather has every expected warehouse-day and all configured warehouses.
5. At least 95% of eligible operational rows join by `delivery_date × warehouse_code` for weather.
6. Each comparison cohort contains at least 30 eligible orders.

Weather compares `rainy_day` (`precipitation_sum_mm >= 1`) with `little_or_no_rain` at the
warehouse-city centroid. Holidays compare national public-holiday with non-holiday requested dates.
Every result says: **descriptive association only; no causal effect is estimated**. Weather is not
route or outlet observation, and the national calendar is not a state-specific closure schedule.

## Date and filter semantics

| Fact or decision | Canonical date |
|---|---|
| Allocation, post-allocation, fill, strict OTIF, promise on-time, shortage | Requested delivery date |
| Overdue OPEN backlog | Requested delivery date, measured against selected period end |
| Delivery exceptions and cold chain | Actual delivery date |
| Credit-note numerator | Return date |
| Dispatch-value denominator | Requested delivery date |
| Inventory | Latest snapshot on or before selected period end |
| Freight numerator | Carrier service date |
| Freight delivered-case denominator | Actual delivery date |
| Competitor position | Latest listing `last_seen` observation |
| Competitor source history | Detail-page source observation date; Kestrel MRP effective on that date |
| Weather association | Actual delivery date × warehouse code |
| Holiday association | Requested delivery date |

The global **Full history** preset spans the union of requested-delivery, actual-delivery, return,
inventory, and available freight-service dates. This prevents a fact near the edge of its own
history from being silently excluded merely because the requested-delivery fact starts later or
ends sooner. Each metric still filters on its canonical date above, so different pages can
legitimately have different record counts inside the same selected calendar window.

Customer region, origin/DC region, DC, route, outlet, channel, category, recorded order source, and
recorded promotion are distinct allowlisted dimensions where the underlying fact has those keys. A
page or query applies only filters that are defensible for all components and discloses any ignored
filter. Ask Kestrel does not guess whether an unqualified “region” means customer or origin/DC
geography.

`orders.created_at` is normalized by source system: ERP `DD/MM/YYYY HH:MM`, SFA
`YYYY-MM-DD HH:MM:SS`, and partner ISO `...Z`, with explicit UTC-to-IST conversion for the latter.
The raw value, `created_at_ist`, and parse status remain together in semantic evidence. No published
KPI uses order creation time; service and delivery cohorts continue to use their canonical dates.

## Evidence and ranking rules

1. Every rate exposes numerator, denominator, and relevant record count.
2. A missing or zero denominator is unavailable, not zero.
3. Rankings show volume and apply a minimum-volume threshold when small groups could dominate.
4. Period, date basis, quantity basis, applied filters, ignored filters, definition, version, and
   relevant freshness/coverage accompany the result.
5. Current-state master attributes are not described as effective-dated history.
6. Raw conflict fields and the chosen normalized interpretation coexist.
7. “Why,” “driver,” and “performer” outputs mean measured contribution or association unless a
   causal design exists. This system contains no causal design.
8. Row-level evidence tables may use a disclosed display limit for usability. Their captions show
   displayed versus matching rows, while headline cards and aggregate charts are computed from the
   complete filtered population rather than the displayed sample.

## Semantic version policy

Increase a metric version when a change alters formula, native grain, eligible population,
numerator, denominator, date basis, unit conversion, filter behavior, threshold, or source-of-truth
choice. A backwards-incompatible change requires a major version; a compatible addition to the
evidence contract requires a minor version; a wording correction that cannot change a number may
use a patch version. Presentation-only color, layout, or chart changes do not change the metric
version.

Changing the YAML label alone must never be used to conceal changed computation. Registry changes,
service code, tests, and release notes should move together.
