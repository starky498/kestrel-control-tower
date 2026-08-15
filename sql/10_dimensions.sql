CREATE TABLE dim_region AS
SELECT
    CAST(region_id AS BIGINT) AS region_id,
    region_code,
    region_name,
    regional_manager,
    hq_city,
    CAST(active_from AS DATE) AS active_from,
    status
FROM raw_regions;

CREATE TABLE dim_warehouse AS
SELECT
    CAST(w.warehouse_id AS BIGINT) AS warehouse_id,
    w.warehouse_code,
    w.warehouse_name,
    w.city AS warehouse_city_raw,
    CASE lower(trim(w.city))
        WHEN 'bangalore' THEN 'Bengaluru'
        WHEN 'new delhi' THEN 'Delhi'
        WHEN 'delhi ncr' THEN 'Delhi'
        ELSE trim(w.city)
    END AS warehouse_city,
    CAST(w.region_id AS BIGINT) AS warehouse_region_id,
    r.region_code AS warehouse_region_code,
    r.region_name AS warehouse_region_name,
    w.capacity_pallets,
    w.chilled_capacity_pallets,
    w.dock_count,
    w.temp_monitoring,
    w.status
FROM raw_warehouses w
LEFT JOIN raw_regions r ON r.region_id = w.region_id;

CREATE TABLE dim_route AS
SELECT
    CAST(rt.route_id AS BIGINT) AS route_id,
    rt.route_code,
    rt.route_name,
    CAST(rt.warehouse_id AS BIGINT) AS route_warehouse_id,
    w.warehouse_code AS route_warehouse_code,
    CAST(rt.region_id AS BIGINT) AS route_region_id,
    r.region_name AS route_region_name,
    rt.planned_stops,
    rt.planned_km,
    rt.vehicle_type,
    CAST(rt.is_reefer AS BOOLEAN) AS is_reefer,
    rt.cost_per_km,
    rt.shift,
    rt.service_frequency,
    CAST(rt.active_from AS DATE) AS active_from,
    CAST(rt.active_to AS DATE) AS active_to,
    rt.status
FROM raw_routes rt
LEFT JOIN raw_warehouses w ON w.warehouse_id = rt.warehouse_id
LEFT JOIN raw_regions r ON r.region_id = rt.region_id;

CREATE TABLE dim_outlet AS
SELECT
    CAST(o.outlet_id AS BIGINT) AS outlet_id,
    o.outlet_code,
    o.outlet_name,
    o.legal_name,
    o.channel,
    o.outlet_format,
    o.city AS outlet_city_raw,
    CASE lower(trim(o.city))
        WHEN 'bangalore' THEN 'Bengaluru'
        WHEN 'new delhi' THEN 'Delhi'
        WHEN 'delhi ncr' THEN 'Delhi'
        ELSE trim(o.city)
    END AS outlet_city,
    o.state,
    CAST(o.region_id AS BIGINT) AS current_region_id,
    r.region_name AS current_region_name,
    CAST(o.route_id AS BIGINT) AS current_route_id,
    CAST(o.salesperson_id AS BIGINT) AS current_salesperson_id,
    o.storage_type,
    CAST(o.chiller_available AS BOOLEAN) AS chiller_available,
    o.risk_flag,
    o.status AS outlet_status,
    CAST(o.is_deleted AS BOOLEAN) AS is_deleted,
    (
        o.outlet_code LIKE 'TST%'
        OR lower(o.outlet_name) LIKE '%test%'
        OR lower(o.outlet_name) LIKE '%migration%'
        OR lower(o.outlet_name) LIKE '%dummy%'
        OR lower(o.outlet_name) LIKE '%do not use%'
    ) AS is_test_outlet,
    (
        o.status = 'ACTIVE'
        AND coalesce(o.is_deleted, 0) = 0
        AND NOT (
            o.outlet_code LIKE 'TST%'
            OR lower(o.outlet_name) LIKE '%test%'
            OR lower(o.outlet_name) LIKE '%migration%'
            OR lower(o.outlet_name) LIKE '%dummy%'
            OR lower(o.outlet_name) LIKE '%do not use%'
        )
    ) AS is_eligible_service_outlet
FROM raw_outlets o
LEFT JOIN raw_regions r ON r.region_id = o.region_id;

CREATE TABLE dim_product AS
SELECT
    CAST(product_id AS BIGINT) AS product_id,
    sku_code,
    product_name,
    brand,
    category,
    subcategory,
    pack_size_value,
    upper(pack_size_uom) AS pack_size_uom,
    case_pack,
    mrp_inr AS current_mrp_inr,
    list_price_inr AS current_list_price_inr,
    gst_rate_pct,
    shelf_life_days,
    storage_temp_band,
    CAST(is_chilled AS BOOLEAN) AS is_chilled,
    abc_class,
    CAST(launch_date AS DATE) AS launch_date,
    CAST(discontinued_date AS DATE) AS discontinued_date,
    supplier_name,
    status AS product_status
FROM raw_products;

CREATE TABLE dim_date AS
WITH bounds AS (
    SELECT min(CAST(order_date AS DATE)) AS min_date,
           max(CAST(order_date AS DATE)) AS max_date
    FROM raw_orders
), dates AS (
    SELECT unnest(generate_series(min_date, max_date, INTERVAL 1 DAY))::DATE AS calendar_date
    FROM bounds
)
SELECT
    calendar_date,
    year(calendar_date) AS calendar_year,
    month(calendar_date) AS calendar_month,
    strftime(calendar_date, '%Y-%m') AS calendar_month_key,
    date_trunc('month', calendar_date)::DATE AS month_start,
    date_trunc('week', calendar_date)::DATE AS week_start,
    CASE WHEN month(calendar_date) >= 4
         THEN year(calendar_date) ELSE year(calendar_date) - 1 END AS fiscal_year_start,
    printf(
        'FY %d-%02d',
        CASE WHEN month(calendar_date) >= 4
             THEN year(calendar_date) ELSE year(calendar_date) - 1 END,
        ((CASE WHEN month(calendar_date) >= 4
               THEN year(calendar_date) ELSE year(calendar_date) - 1 END) + 1) % 100
    ) AS fiscal_year_label,
    CAST(floor(((month(calendar_date) + 8) % 12) / 3) + 1 AS INTEGER) AS fiscal_quarter,
    printf(
        'FY %d-%02d Q%d',
        CASE WHEN month(calendar_date) >= 4
             THEN year(calendar_date) ELSE year(calendar_date) - 1 END,
        ((CASE WHEN month(calendar_date) >= 4
               THEN year(calendar_date) ELSE year(calendar_date) - 1 END) + 1) % 100,
        CAST(floor(((month(calendar_date) + 8) % 12) / 3) + 1 AS INTEGER)
    ) AS fiscal_quarter_label
FROM dates;
