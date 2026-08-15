# Governed Metric Contracts

Every published result has a formula, grain, date basis, eligibility rule, numerator/denominator,
and evidence path. Dashboard cards and Ask Kestrel share these contracts from
`config/metrics.yml` and the metric services.

## Service

### Fill rate

```text
eaches fill = sum(delivered_eaches) / sum(ordered_eaches)
case-equivalent fill = sum(delivered_case_equivalents) / sum(ordered_case_equivalents)
```

Quantities are normalized per line with `case_pack_at_order`; subgroup percentages are never
averaged. The cohort uses requested delivery date. Eligible service includes `DELIVERED` and
`PARTIAL` orders for currently active, non-deleted, non-test outlets. Eaches is the default because
it is the later, commercially specific stakeholder request; case-equivalents remain selectable.

### Strict OTIF

```text
line_in_full  = delivered_eaches >= ordered_eaches
order_in_full = every line_in_full
on_time       = parsed actual_arrival <= parsed planned_arrival
strict_OTIF   = order_in_full AND on_time
```

The grain is order; the cohort is requested delivery date. All supplied lines are short, so strict
in-full and strict OTIF are 0%. Fill and on-time remain separate diagnostics. Stored
`delay_minutes` is preserved because it changes 25,734 classifications, but parsed event
timestamps are the chosen primary. No undocumented tolerance is calculated.

### Late over two hours

An eligible delivery is late over two hours when parsed `actual_arrival - planned_arrival > 120`
minutes. Route rates are late deliveries divided by eligible deliveries, with counts shown beside
rates.

## Cold chain and inventory

```text
excursions per 100 = 100 × flagged chilled deliveries / chilled deliveries
```

A delivery is chilled when any line contains a product with `is_chilled = true`; a delivery counts
once. Operational month uses actual delivery date. Excursion flags on non-chilled deliveries are a
quality exception, not part of the KPI.

Near-expiry stock is `available_cases` with `expiry_days` from 0 through the configured 30-day
window, using the latest weekly snapshot on or before the selected period end. The snapshot date is
part of the result.

## Measured leakage

```text
estimated dispatch value = line_value × min(delivered_qty / ordered_qty, 1)
approved credit leakage rate = approved credit-note value / estimated dispatch value
```

Returns use absolute normalized quantity and the originating line's order-time case pack. Approved
credit notes form the headline; pending and rejected remain separate. This is a gross measured
leakage estimate, not profit or cash collected.

## Freight

```text
billed freight = amount_paise / 100 + detention_charge_paise / 100
freight per delivered case = sum(billed freight) / sum(delivered case-equivalents)
```

The numerator and denominator are independently aggregated over freight service date / actual
delivery date and warehouse. The API supplies no order or delivery key, and most invoice
warehouse-route pairs conflict with the route master; no row-level lineage is invented. Paid,
pending, and disputed spend are shown separately. Carrier spend is valid, but carrier-attributed
delivered cases are not.

## Price position

```text
price gap = current Kestrel MRP - lowest available matched competitor price
MRP premium % = Kestrel MRP / competitor price - 1
```

The set is the top SKUs by eligible estimated dispatch value in the selected period. Competitor
prices are the latest collected listing-card observations for the chosen city. Only high-confidence,
non-ambiguous matches affect the comparison; unmatched top SKUs stay visible as missing coverage.
The result is not described as live or historical reconstruction.

## Period and filter rules

| Fact | Canonical date |
|---|---|
| Fill, OTIF, shortage cohort | Requested delivery date |
| Delivery lateness and cold chain | Actual delivery date |
| Return leakage | Return date |
| Dispatch-value denominator | Requested delivery date |
| Inventory | Latest snapshot on/before period end |
| Freight numerator | Carrier service date |
| Competitor observation | Listing `last_seen` |

Customer region, origin/DC region, warehouse, route, outlet, and channel are distinct allowlisted
filters. A filter that cannot apply symmetrically to freight numerator and denominator is ignored
there and reported rather than biasing one side.

## Evidence rules

1. A missing/zero denominator is “not available,” never silently zero.
2. Every rate exposes numerator, denominator, and record count.
3. Current-state master attributes are never presented as historical unless an effective-dated
   source exists.
4. “Why” answers describe measured contribution or association, not causality.
5. External metrics expose coverage/freshness and complete-cache state.
