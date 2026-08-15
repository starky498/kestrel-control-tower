CREATE TABLE fct_order_line AS
SELECT
    CAST(ol.order_line_id AS BIGINT) AS order_line_id,
    CAST(ol.order_id AS BIGINT) AS order_id,
    ol.line_number,
    CAST(o.order_date AS DATE) AS order_date,
    CAST(o.requested_delivery_date AS DATE) AS requested_delivery_date,
    dd.fiscal_year_label,
    dd.fiscal_quarter,
    dd.fiscal_quarter_label,
    o.order_number,
    o.order_status,
    o.source_system,
    o.channel,
    CAST(o.outlet_id AS BIGINT) AS outlet_id,
    outlet.outlet_code,
    outlet.outlet_name,
    outlet.outlet_city_raw,
    outlet.outlet_city,
    outlet.outlet_status,
    outlet.is_deleted,
    outlet.is_test_outlet,
    outlet.is_eligible_service_outlet,
    CAST(o.region_id AS BIGINT) AS customer_region_id,
    customer_region.region_code AS customer_region_code,
    customer_region.region_name AS customer_region_name,
    CAST(o.warehouse_id AS BIGINT) AS warehouse_id,
    warehouse.warehouse_code,
    warehouse.warehouse_name,
    warehouse.warehouse_city,
    warehouse.warehouse_region_id,
    warehouse.warehouse_region_name,
    (o.region_id <> warehouse.warehouse_region_id) AS cross_region_fulfilment,
    CAST(o.route_id AS BIGINT) AS route_id,
    route.route_code,
    route.route_name,
    CAST(o.salesperson_id AS BIGINT) AS salesperson_id,
    salesperson.employee_code AS salesperson_employee_code,
    salesperson.full_name AS salesperson_name,
    salesperson.designation AS salesperson_designation,
    salesperson.salesperson_region_name,
    CAST(ol.product_id AS BIGINT) AS product_id,
    product.sku_code,
    product.product_name,
    product.brand,
    product.category,
    product.subcategory,
    product.pack_size_value,
    product.pack_size_uom,
    product.is_chilled,
    product.storage_temp_band,
    product.product_status,
    product.discontinued_date,
    ol.qty_uom,
    ol.case_pack_at_order,
    ol.ordered_qty,
    ol.allocated_qty,
    ol.delivered_qty,
    CASE WHEN ol.qty_uom = 'CASE' THEN ol.ordered_qty * ol.case_pack_at_order
         ELSE ol.ordered_qty END AS ordered_eaches,
    CASE WHEN ol.qty_uom = 'CASE' THEN ol.allocated_qty * ol.case_pack_at_order
         ELSE ol.allocated_qty END AS allocated_eaches,
    CASE WHEN ol.qty_uom = 'CASE' THEN ol.delivered_qty * ol.case_pack_at_order
         ELSE ol.delivered_qty END AS delivered_eaches,
    CASE WHEN ol.qty_uom = 'CASE' THEN ol.ordered_qty
         ELSE ol.ordered_qty / nullif(ol.case_pack_at_order, 0) END AS ordered_case_equivalents,
    CASE WHEN ol.qty_uom = 'CASE' THEN ol.allocated_qty
         ELSE ol.allocated_qty / nullif(ol.case_pack_at_order, 0) END AS allocated_case_equivalents,
    CASE WHEN ol.qty_uom = 'CASE' THEN ol.delivered_qty
         ELSE ol.delivered_qty / nullif(ol.case_pack_at_order, 0) END AS delivered_case_equivalents,
    greatest(
        CASE WHEN ol.qty_uom = 'CASE' THEN (ol.ordered_qty - ol.allocated_qty) * ol.case_pack_at_order
             ELSE ol.ordered_qty - ol.allocated_qty END,
        0
    ) AS allocation_short_eaches,
    greatest(
        CASE WHEN ol.qty_uom = 'CASE' THEN (ol.allocated_qty - ol.delivered_qty) * ol.case_pack_at_order
             ELSE ol.allocated_qty - ol.delivered_qty END,
        0
    ) AS post_allocation_short_eaches,
    greatest(
        CASE WHEN ol.qty_uom = 'CASE' THEN ol.ordered_qty - ol.allocated_qty
             ELSE (ol.ordered_qty - ol.allocated_qty) / nullif(ol.case_pack_at_order, 0) END,
        0
    ) AS allocation_short_case_equivalents,
    greatest(
        CASE WHEN ol.qty_uom = 'CASE' THEN ol.allocated_qty - ol.delivered_qty
             ELSE (ol.allocated_qty - ol.delivered_qty) / nullif(ol.case_pack_at_order, 0) END,
        0
    ) AS post_allocation_short_case_equivalents,
    greatest(
        CASE WHEN ol.qty_uom = 'CASE' THEN (ol.ordered_qty - ol.delivered_qty) * ol.case_pack_at_order
             ELSE ol.ordered_qty - ol.delivered_qty END,
        0
    ) AS short_eaches,
    greatest(
        CASE WHEN ol.qty_uom = 'CASE' THEN ol.ordered_qty - ol.delivered_qty
             ELSE (ol.ordered_qty - ol.delivered_qty) / nullif(ol.case_pack_at_order, 0) END,
        0
    ) AS short_case_equivalents,
    CASE WHEN ol.ordered_qty > 0 THEN least(ol.delivered_qty / ol.ordered_qty, 1.0) END
        AS line_fill_rate,
    (ol.delivered_qty >= ol.ordered_qty) AS strict_line_in_full,
    ol.short_reason_code,
    CAST(ol.substitution_flag AS BOOLEAN) AS substitution_flag,
    ol.unit_price_inr,
    ol.line_discount_pct,
    ol.line_value_inr,
    CASE WHEN ol.ordered_qty > 0
         THEN ol.line_value_inr * least(ol.delivered_qty / ol.ordered_qty, 1.0)
         ELSE 0 END AS estimated_dispatch_value_inr,
    historical_price.mrp_inr AS historical_mrp_inr,
    historical_price.list_price_inr AS historical_list_price_inr,
    (product.discontinued_date IS NOT NULL
        AND CAST(o.order_date AS DATE) > product.discontinued_date) AS ordered_after_discontinued,
    (o.order_status IN ('DELIVERED', 'PARTIAL')
        AND outlet.is_eligible_service_outlet) AS is_eligible_service
FROM raw_order_lines ol
JOIN raw_orders o ON o.order_id = ol.order_id
JOIN dim_date dd ON dd.calendar_date = CAST(o.order_date AS DATE)
JOIN dim_outlet outlet ON outlet.outlet_id = o.outlet_id
JOIN dim_product product ON product.product_id = ol.product_id
LEFT JOIN dim_region customer_region ON customer_region.region_id = o.region_id
LEFT JOIN dim_warehouse warehouse ON warehouse.warehouse_id = o.warehouse_id
LEFT JOIN dim_route route ON route.route_id = o.route_id
LEFT JOIN dim_salesperson salesperson ON salesperson.salesperson_id = o.salesperson_id
LEFT JOIN raw_product_price_history historical_price
    ON historical_price.product_id = ol.product_id
   AND CAST(o.order_date AS DATE) >= CAST(historical_price.effective_from AS DATE)
   AND (
        historical_price.effective_to IS NULL
        OR CAST(o.order_date AS DATE) <= CAST(historical_price.effective_to AS DATE)
   );

CREATE TABLE fct_delivery AS
WITH parsed AS (
    SELECT
        d.*,
        try_strptime(d.planned_arrival, '%Y-%m-%d %H:%M:%S') AS planned_arrival_ts,
        coalesce(
            try_strptime(d.actual_arrival, '%Y-%m-%d %H:%M:%S'),
            try_strptime(d.actual_arrival, '%d-%b-%Y %I:%M %p')
        ) AS actual_arrival_ts
    FROM raw_deliveries d
), chilled AS (
    SELECT order_id, bool_or(is_chilled) AS has_chilled_product
    FROM fct_order_line
    GROUP BY order_id
)
SELECT
    CAST(p.delivery_id AS BIGINT) AS delivery_id,
    CAST(p.order_id AS BIGINT) AS order_id,
    p.delivery_note_number,
    CAST(o.order_date AS DATE) AS order_date,
    CAST(p.planned_arrival_ts AS DATE) AS planned_delivery_date,
    CAST(p.actual_arrival_ts AS DATE) AS delivery_date,
    o.order_status,
    o.channel,
    CAST(o.outlet_id AS BIGINT) AS outlet_id,
    outlet.outlet_code,
    outlet.outlet_name,
    outlet.outlet_city,
    outlet.is_eligible_service_outlet,
    CAST(o.region_id AS BIGINT) AS customer_region_id,
    customer_region.region_name AS customer_region_name,
    CAST(p.warehouse_id AS BIGINT) AS warehouse_id,
    warehouse.warehouse_code,
    warehouse.warehouse_name,
    warehouse.warehouse_region_id,
    warehouse.warehouse_region_name,
    (o.region_id <> warehouse.warehouse_region_id) AS cross_region_fulfilment,
    CAST(p.route_id AS BIGINT) AS route_id,
    route.route_code,
    route.route_name,
    p.vehicle_registration,
    p.driver_name,
    p.telematics_vendor,
    p.planned_arrival AS planned_arrival_raw,
    p.actual_arrival AS actual_arrival_raw,
    p.planned_arrival_ts,
    p.actual_arrival_ts,
    p.delay_minutes AS stored_delay_minutes,
    date_diff('minute', p.planned_arrival_ts, p.actual_arrival_ts) AS derived_delay_minutes,
    (
        p.planned_arrival_ts IS NOT NULL
        AND p.actual_arrival_ts IS NOT NULL
        AND p.actual_arrival_ts <= p.planned_arrival_ts
    ) AS on_time_by_timestamp,
    (p.delay_minutes <= 0) AS on_time_by_stored_delay,
    (
        p.planned_arrival_ts IS NULL OR p.actual_arrival_ts IS NULL
        OR abs(date_diff('minute', p.planned_arrival_ts, p.actual_arrival_ts) - p.delay_minutes) > 1
    ) AS delay_source_conflict,
    (
        p.planned_arrival_ts IS NOT NULL
        AND p.actual_arrival_ts IS NOT NULL
        AND date_diff('minute', p.planned_arrival_ts, p.actual_arrival_ts) > 120
    ) AS late_over_2h,
    p.distance_km,
    p.delivery_status,
    CAST(p.pod_captured AS BOOLEAN) AS pod_captured,
    coalesce(chilled.has_chilled_product, FALSE) AS has_chilled_product,
    CAST(p.temperature_excursion_flag AS BOOLEAN) AS temperature_excursion_flag,
    p.max_temp_celsius,
    p.returned_cases,
    p.failure_reason_code,
    p.fuel_cost_inr AS driver_entered_fuel_cost_inr,
    (
        o.order_status IN ('DELIVERED', 'PARTIAL')
        AND outlet.is_eligible_service_outlet
    ) AS is_eligible_service
FROM parsed p
JOIN raw_orders o ON o.order_id = p.order_id
JOIN dim_outlet outlet ON outlet.outlet_id = o.outlet_id
LEFT JOIN dim_region customer_region ON customer_region.region_id = o.region_id
LEFT JOIN dim_warehouse warehouse ON warehouse.warehouse_id = p.warehouse_id
LEFT JOIN dim_route route ON route.route_id = p.route_id
LEFT JOIN chilled ON chilled.order_id = p.order_id;

CREATE TABLE fct_order_service AS
WITH line_rollup AS (
    SELECT
        order_id,
        any_value(order_number) AS order_number,
        any_value(order_date) AS order_date,
        any_value(requested_delivery_date) AS requested_delivery_date,
        any_value(fiscal_year_label) AS fiscal_year_label,
        any_value(fiscal_quarter) AS fiscal_quarter,
        any_value(fiscal_quarter_label) AS fiscal_quarter_label,
        any_value(order_status) AS order_status,
        any_value(source_system) AS source_system,
        any_value(channel) AS channel,
        any_value(outlet_id) AS outlet_id,
        any_value(outlet_code) AS outlet_code,
        any_value(outlet_name) AS outlet_name,
        any_value(outlet_city) AS outlet_city,
        any_value(customer_region_id) AS customer_region_id,
        any_value(customer_region_code) AS customer_region_code,
        any_value(customer_region_name) AS customer_region_name,
        any_value(warehouse_id) AS warehouse_id,
        any_value(warehouse_code) AS warehouse_code,
        any_value(warehouse_name) AS warehouse_name,
        any_value(warehouse_region_id) AS warehouse_region_id,
        any_value(warehouse_region_name) AS warehouse_region_name,
        any_value(cross_region_fulfilment) AS cross_region_fulfilment,
        any_value(route_id) AS route_id,
        any_value(route_code) AS route_code,
        any_value(route_name) AS route_name,
        any_value(salesperson_id) AS salesperson_id,
        any_value(salesperson_employee_code) AS salesperson_employee_code,
        any_value(salesperson_name) AS salesperson_name,
        any_value(salesperson_designation) AS salesperson_designation,
        any_value(salesperson_region_name) AS salesperson_region_name,
        any_value(is_eligible_service_outlet) AS is_eligible_service_outlet,
        any_value(is_eligible_service) AS is_eligible_service,
        count(*) AS line_count,
        sum(ordered_eaches) AS ordered_eaches,
        sum(allocated_eaches) AS allocated_eaches,
        sum(delivered_eaches) AS delivered_eaches,
        sum(allocation_short_eaches) AS allocation_short_eaches,
        sum(post_allocation_short_eaches) AS post_allocation_short_eaches,
        sum(short_eaches) AS short_eaches,
        sum(ordered_case_equivalents) AS ordered_case_equivalents,
        sum(allocated_case_equivalents) AS allocated_case_equivalents,
        sum(delivered_case_equivalents) AS delivered_case_equivalents,
        sum(allocation_short_case_equivalents) AS allocation_short_case_equivalents,
        sum(post_allocation_short_case_equivalents) AS post_allocation_short_case_equivalents,
        sum(short_case_equivalents) AS short_case_equivalents,
        sum(line_value_inr) AS line_value_inr,
        sum(estimated_dispatch_value_inr) AS estimated_dispatch_value_inr,
        bool_and(strict_line_in_full) AS strict_in_full,
        bool_or(is_chilled) AS has_chilled_product,
        bool_or(ordered_after_discontinued) AS has_discontinued_product_order
    FROM fct_order_line
    GROUP BY order_id
)
SELECT
    line_rollup.*,
    CASE WHEN ordered_eaches > 0
         THEN least(delivered_eaches / ordered_eaches, 1.0) END AS fill_rate_eaches,
    CASE WHEN ordered_case_equivalents > 0
         THEN least(delivered_case_equivalents / ordered_case_equivalents, 1.0) END
         AS fill_rate_case_equivalents,
    delivery.delivery_id,
    delivery.delivery_date,
    delivery.planned_arrival_ts,
    delivery.actual_arrival_ts,
    delivery.stored_delay_minutes,
    delivery.derived_delay_minutes,
    delivery.on_time_by_timestamp,
    delivery.on_time_by_stored_delay,
    delivery.delay_source_conflict,
    delivery.late_over_2h,
    delivery.temperature_excursion_flag,
    delivery.max_temp_celsius,
    delivery.failure_reason_code,
    (
        line_rollup.strict_in_full
        AND coalesce(delivery.on_time_by_timestamp, FALSE)
    ) AS strict_otif
FROM line_rollup
LEFT JOIN fct_delivery delivery ON delivery.order_id = line_rollup.order_id;

CREATE TABLE fct_return_credit_note AS
SELECT
    CAST(r.return_id AS BIGINT) AS return_id,
    r.credit_note_number,
    CAST(r.order_id AS BIGINT) AS order_id,
    CAST(r.order_line_id AS BIGINT) AS order_line_id,
    CAST(r.return_date AS DATE) AS return_date,
    line.channel,
    CAST(r.outlet_id AS BIGINT) AS outlet_id,
    line.outlet_code,
    line.outlet_name,
    line.outlet_city,
    line.customer_region_id,
    line.customer_region_name,
    line.warehouse_id,
    line.warehouse_code,
    line.warehouse_name,
    line.warehouse_region_id,
    line.warehouse_region_name,
    line.route_id,
    line.route_code,
    line.route_name,
    CAST(r.product_id AS BIGINT) AS product_id,
    line.sku_code,
    line.product_name,
    line.brand,
    line.category,
    line.subcategory,
    line.case_pack_at_order,
    r.qty_uom,
    r.return_qty AS return_qty_raw,
    abs(r.return_qty) AS return_qty_normalized,
    (r.return_qty < 0) AS return_sign_was_negative,
    CASE WHEN r.qty_uom = 'CASE' THEN abs(r.return_qty) * line.case_pack_at_order
         ELSE abs(r.return_qty) END AS return_eaches,
    CASE WHEN r.qty_uom = 'CASE' THEN abs(r.return_qty)
         ELSE abs(r.return_qty) / nullif(line.case_pack_at_order, 0) END
         AS return_case_equivalents,
    r.return_reason_code,
    (r.return_reason_code = 'RT06_COLD_CHAIN_BREACH') AS is_cold_chain_return,
    r.credit_note_value_inr,
    r.disposition,
    r.status AS credit_note_status,
    r.approved_by,
    CAST(r.approval_date AS DATE) AS approval_date,
    line.is_eligible_service_outlet,
    line.is_eligible_service
FROM raw_returns_credit_notes r
JOIN fct_order_line line ON line.order_line_id = r.order_line_id;

CREATE TABLE fct_inventory_snapshot AS
SELECT
    CAST(i.snapshot_id AS BIGINT) AS snapshot_id,
    CAST(i.snapshot_date AS DATE) AS snapshot_date,
    CAST(i.warehouse_id AS BIGINT) AS warehouse_id,
    warehouse.warehouse_code,
    warehouse.warehouse_name,
    warehouse.warehouse_city,
    warehouse.warehouse_region_id,
    warehouse.warehouse_region_name,
    CAST(i.product_id AS BIGINT) AS product_id,
    product.sku_code,
    product.product_name,
    product.brand,
    product.category,
    product.subcategory,
    product.is_chilled,
    product.storage_temp_band,
    i.batch_id,
    i.on_hand_cases,
    i.on_hand_eaches,
    i.allocated_cases,
    i.available_cases,
    i.days_of_cover,
    CAST(i.expiry_date AS DATE) AS expiry_date,
    date_diff('day', CAST(i.snapshot_date AS DATE), CAST(i.expiry_date AS DATE)) AS expiry_days,
    i.ageing_bucket,
    i.damaged_cases,
    i.blocked_cases,
    i.storage_temp_celsius
FROM raw_inventory_snapshots i
JOIN dim_warehouse warehouse ON warehouse.warehouse_id = i.warehouse_id
JOIN dim_product product ON product.product_id = i.product_id;
