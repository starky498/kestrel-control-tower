CREATE TABLE fct_order_line AS
WITH normalized_orders AS (
    SELECT
        o.*,
        CASE o.source_system
            WHEN 'ERP_WEB' THEN try_strptime(o.created_at, '%d/%m/%Y %H:%M')
            WHEN 'SFA_MOBILE' THEN try_strptime(o.created_at, '%Y-%m-%d %H:%M:%S')
            WHEN 'PARTNER_API' THEN timezone(
                'Asia/Kolkata',
                timezone(
                    'UTC',
                    try_strptime(o.created_at, '%Y-%m-%dT%H:%M:%SZ')
                )
            )
        END AS created_at_ist
    FROM raw_orders o
), parsed_orders AS (
    SELECT
        normalized_orders.*,
        CASE
            WHEN created_at IS NULL OR trim(created_at) = '' THEN 'MISSING'
            WHEN source_system NOT IN ('ERP_WEB', 'SFA_MOBILE', 'PARTNER_API')
                THEN 'UNSUPPORTED_SOURCE'
            WHEN created_at_ist IS NULL THEN 'PARSE_FAILED'
            WHEN source_system = 'ERP_WEB' THEN 'PARSED_ERP_WEB_IST'
            WHEN source_system = 'SFA_MOBILE' THEN 'PARSED_SFA_MOBILE_IST'
            ELSE 'PARSED_PARTNER_API_UTC_TO_IST'
        END AS created_at_parse_status
    FROM normalized_orders
), promotion_lookup AS (
    -- Orders carry promo_code rather than promo_id. Collapse the source to the observed
    -- code before joining so a malformed duplicate code can never fan out order lines.
    SELECT
        promo_code,
        min(promo_name) AS promo_name,
        min(mechanic) AS promotion_mechanic
    FROM raw_promotions
    WHERE promo_code IS NOT NULL AND trim(promo_code) <> ''
    GROUP BY promo_code
)
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
    o.created_at AS created_at_raw,
    o.created_at_ist,
    o.created_at_parse_status,
    coalesce(nullif(trim(o.promo_code), ''), 'NO_PROMOTION') AS promotion_code,
    coalesce(promotion.promo_name, 'No promotion') AS promotion_name,
    coalesce(promotion.promotion_mechanic, 'No promotion') AS promotion_mechanic,
    (nullif(trim(o.promo_code), '') IS NOT NULL) AS promotion_applied,
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
    CASE WHEN ol.qty_uom = 'CASE'
         THEN least(ol.delivered_qty, ol.ordered_qty) * ol.case_pack_at_order
         ELSE least(ol.delivered_qty, ol.ordered_qty) END AS capped_delivered_eaches,
    CASE WHEN ol.qty_uom = 'CASE' THEN ol.ordered_qty
         ELSE ol.ordered_qty / nullif(ol.case_pack_at_order, 0) END AS ordered_case_equivalents,
    CASE WHEN ol.qty_uom = 'CASE' THEN ol.allocated_qty
         ELSE ol.allocated_qty / nullif(ol.case_pack_at_order, 0) END AS allocated_case_equivalents,
    CASE WHEN ol.qty_uom = 'CASE' THEN ol.delivered_qty
         ELSE ol.delivered_qty / nullif(ol.case_pack_at_order, 0) END AS delivered_case_equivalents,
    CASE WHEN ol.qty_uom = 'CASE' THEN least(ol.delivered_qty, ol.ordered_qty)
         ELSE least(ol.delivered_qty, ol.ordered_qty)
              / nullif(ol.case_pack_at_order, 0) END
         AS capped_delivered_case_equivalents,
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
    CASE WHEN ol.ordered_qty > 0
         THEN ol.line_value_inr * greatest(ol.ordered_qty - ol.delivered_qty, 0)
              / ol.ordered_qty
         ELSE 0 END AS short_delivery_value_exposure_inr,
    CASE WHEN ol.ordered_qty > 0
         THEN ol.line_value_inr * greatest(ol.ordered_qty - ol.allocated_qty, 0)
              / ol.ordered_qty
         ELSE 0 END AS allocation_short_value_exposure_inr,
    CASE WHEN ol.ordered_qty > 0
         THEN ol.line_value_inr * greatest(ol.allocated_qty - ol.delivered_qty, 0)
              / ol.ordered_qty
         ELSE 0 END AS post_allocation_short_value_exposure_inr,
    historical_price.mrp_inr AS historical_mrp_inr,
    historical_price.list_price_inr AS historical_list_price_inr,
    (product.discontinued_date IS NOT NULL
        AND CAST(o.order_date AS DATE) > product.discontinued_date) AS ordered_after_discontinued,
    (o.order_status IN ('DELIVERED', 'PARTIAL')
        AND outlet.is_eligible_service_outlet) AS is_eligible_service
FROM raw_order_lines ol
JOIN parsed_orders o ON o.order_id = ol.order_id
JOIN dim_date dd ON dd.calendar_date = CAST(o.order_date AS DATE)
JOIN dim_outlet outlet ON outlet.outlet_id = o.outlet_id
JOIN dim_product product ON product.product_id = ol.product_id
LEFT JOIN dim_region customer_region ON customer_region.region_id = o.region_id
LEFT JOIN dim_warehouse warehouse ON warehouse.warehouse_id = o.warehouse_id
LEFT JOIN dim_route route ON route.route_id = o.route_id
LEFT JOIN dim_salesperson salesperson ON salesperson.salesperson_id = o.salesperson_id
LEFT JOIN promotion_lookup promotion ON promotion.promo_code = o.promo_code
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
    o.source_system,
    coalesce(nullif(trim(o.promo_code), ''), 'NO_PROMOTION') AS promotion_code,
    (nullif(trim(o.promo_code), '') IS NOT NULL) AS promotion_applied,
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
        any_value(created_at_raw) AS created_at_raw,
        any_value(created_at_ist) AS created_at_ist,
        any_value(created_at_parse_status) AS created_at_parse_status,
        any_value(promotion_code) AS promotion_code,
        any_value(promotion_name) AS promotion_name,
        any_value(promotion_mechanic) AS promotion_mechanic,
        any_value(promotion_applied) AS promotion_applied,
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
        sum(capped_delivered_eaches) AS capped_delivered_eaches,
        sum(allocation_short_eaches) AS allocation_short_eaches,
        sum(post_allocation_short_eaches) AS post_allocation_short_eaches,
        sum(short_eaches) AS short_eaches,
        sum(ordered_case_equivalents) AS ordered_case_equivalents,
        sum(allocated_case_equivalents) AS allocated_case_equivalents,
        sum(delivered_case_equivalents) AS delivered_case_equivalents,
        sum(capped_delivered_case_equivalents) AS capped_delivered_case_equivalents,
        sum(allocation_short_case_equivalents) AS allocation_short_case_equivalents,
        sum(post_allocation_short_case_equivalents) AS post_allocation_short_case_equivalents,
        sum(short_case_equivalents) AS short_case_equivalents,
        sum(line_value_inr) AS line_value_inr,
        sum(estimated_dispatch_value_inr) AS estimated_dispatch_value_inr,
        sum(short_delivery_value_exposure_inr) AS short_delivery_value_exposure_inr,
        sum(allocation_short_value_exposure_inr) AS allocation_short_value_exposure_inr,
        sum(post_allocation_short_value_exposure_inr)
            AS post_allocation_short_value_exposure_inr,
        bool_and(strict_line_in_full) AS strict_in_full,
        bool_or(is_chilled) AS has_chilled_product,
        bool_or(ordered_after_discontinued) AS has_discontinued_product_order
    FROM fct_order_line
    GROUP BY order_id
)
SELECT
    line_rollup.*,
    CASE WHEN ordered_eaches > 0
         THEN capped_delivered_eaches / ordered_eaches END AS fill_rate_eaches,
    CASE WHEN ordered_case_equivalents > 0
         THEN capped_delivered_case_equivalents / ordered_case_equivalents END
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
    line.source_system,
    line.promotion_code,
    line.promotion_name,
    line.promotion_mechanic,
    line.promotion_applied,
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
