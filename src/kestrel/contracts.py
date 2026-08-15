"""Source contracts and read-only data-quality validation."""

from __future__ import annotations

import csv
import hashlib
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

Severity = Literal["BLOCKING", "WARNING", "INFO"]


@dataclass(frozen=True)
class TableContract:
    grain: str
    primary_key: tuple[str, ...]
    required_columns: tuple[str, ...]


@dataclass(frozen=True)
class QualityResult:
    check_id: str
    severity: Severity
    passed: bool
    observed_value: str
    expected_value: str
    message: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


CONTRACTS: dict[str, TableContract] = {
    "regions": TableContract(
        "one row per sales region",
        ("region_id",),
        (
            "region_id",
            "region_code",
            "region_name",
            "regional_manager",
            "hq_city",
            "active_from",
            "status",
        ),
    ),
    "warehouses": TableContract(
        "one row per distribution centre",
        ("warehouse_id",),
        (
            "warehouse_id",
            "warehouse_code",
            "warehouse_name",
            "city",
            "region_id",
            "address_line",
            "pincode",
            "capacity_pallets",
            "chilled_capacity_pallets",
            "dock_count",
            "opened_date",
            "manager_name",
            "shift_pattern",
            "temp_monitoring",
            "wms_version",
            "status",
        ),
    ),
    "routes": TableContract(
        "one row per delivery route",
        ("route_id",),
        (
            "route_id",
            "route_code",
            "route_name",
            "warehouse_id",
            "planned_stops",
            "planned_km",
            "vehicle_type",
            "is_reefer",
            "cost_per_km",
            "shift",
            "service_frequency",
            "active_from",
            "active_to",
            "status",
            "region_id",
        ),
    ),
    "salespeople": TableContract(
        "one row per field sales employee",
        ("salesperson_id",),
        (
            "salesperson_id",
            "employee_code",
            "full_name",
            "region_id",
            "designation",
            "date_of_joining",
            "date_of_exit",
            "target_monthly_inr",
            "incentive_band",
            "reports_to",
            "status",
        ),
    ),
    "outlets": TableContract(
        "one row per outlet record (not necessarily one real-world customer)",
        ("outlet_id",),
        (
            "outlet_id",
            "outlet_code",
            "outlet_name",
            "legal_name",
            "channel",
            "outlet_format",
            "city",
            "state",
            "region_id",
            "pincode",
            "latitude",
            "longitude",
            "route_id",
            "salesperson_id",
            "onboarded_date",
            "credit_limit_inr",
            "credit_terms_days",
            "gst_number",
            "storage_type",
            "chiller_available",
            "avg_monthly_footfall",
            "contact_name",
            "contact_phone",
            "contact_email",
            "last_audit_date",
            "risk_flag",
            "status",
            "closed_date",
            "is_deleted",
            "created_at",
            "updated_at",
        ),
    ),
    "products": TableContract(
        "one row per current-state SKU",
        ("product_id",),
        (
            "product_id",
            "sku_code",
            "product_name",
            "brand",
            "category",
            "subcategory",
            "pack_size_value",
            "pack_size_uom",
            "case_pack",
            "mrp_inr",
            "list_price_inr",
            "gst_rate_pct",
            "hsn_code",
            "shelf_life_days",
            "storage_temp_band",
            "is_chilled",
            "abc_class",
            "min_order_qty_cases",
            "unit_weight_grams",
            "barcode_ean13",
            "launch_date",
            "discontinued_date",
            "supplier_name",
            "status",
            "created_at",
            "updated_at",
        ),
    ),
    "product_price_history": TableContract(
        "one row per SKU price-validity window",
        ("price_history_id",),
        (
            "price_history_id",
            "product_id",
            "effective_from",
            "effective_to",
            "mrp_inr",
            "list_price_inr",
            "change_reason",
            "approved_by",
        ),
    ),
    "promotions": TableContract(
        "one row per trade promotion",
        ("promo_id",),
        (
            "promo_id",
            "promo_code",
            "promo_name",
            "mechanic",
            "discount_pct",
            "category_scope",
            "channel_scope",
            "region_scope",
            "start_date",
            "end_date",
            "budget_inr",
            "owner",
            "status",
        ),
    ),
    "orders": TableContract(
        "one row per sales-order header",
        ("order_id",),
        (
            "order_id",
            "order_number",
            "outlet_id",
            "order_date",
            "requested_delivery_date",
            "channel",
            "region_id",
            "route_id",
            "warehouse_id",
            "salesperson_id",
            "order_status",
            "line_count",
            "order_value_gross_inr",
            "discount_amount_inr",
            "tax_amount_inr",
            "order_value_net_inr",
            "payment_terms_days",
            "promo_code",
            "priority_flag",
            "credit_hold_flag",
            "cancelled_reason_code",
            "source_system",
            "created_at",
        ),
    ),
    "order_lines": TableContract(
        "one row per sales-order product line",
        ("order_line_id",),
        (
            "order_line_id",
            "order_id",
            "line_number",
            "product_id",
            "ordered_qty",
            "qty_uom",
            "case_pack_at_order",
            "allocated_qty",
            "delivered_qty",
            "unit_price_inr",
            "line_discount_pct",
            "line_value_inr",
            "gst_rate_pct",
            "batch_id",
            "substitution_flag",
            "short_reason_code",
        ),
    ),
    "deliveries": TableContract(
        "one row per delivery note",
        ("delivery_id",),
        (
            "delivery_id",
            "order_id",
            "delivery_note_number",
            "route_id",
            "warehouse_id",
            "vehicle_registration",
            "driver_name",
            "dispatch_datetime",
            "planned_arrival",
            "actual_arrival",
            "telematics_vendor",
            "delay_minutes",
            "distance_km",
            "delivery_status",
            "pod_captured",
            "temperature_excursion_flag",
            "max_temp_celsius",
            "returned_cases",
            "failure_reason_code",
            "fuel_cost_inr",
            "created_at",
        ),
    ),
    "inventory_snapshots": TableContract(
        "one row per warehouse × SKU × batch × weekly snapshot",
        ("snapshot_id",),
        (
            "snapshot_id",
            "snapshot_date",
            "warehouse_id",
            "product_id",
            "batch_id",
            "on_hand_cases",
            "on_hand_eaches",
            "allocated_cases",
            "available_cases",
            "days_of_cover",
            "expiry_date",
            "ageing_bucket",
            "damaged_cases",
            "blocked_cases",
            "storage_temp_celsius",
        ),
    ),
    "returns_credit_notes": TableContract(
        "one row per return / credit-note product line",
        ("return_id",),
        (
            "return_id",
            "credit_note_number",
            "order_id",
            "order_line_id",
            "outlet_id",
            "product_id",
            "return_date",
            "return_qty",
            "qty_uom",
            "return_reason_code",
            "credit_note_value_inr",
            "approved_by",
            "approval_date",
            "disposition",
            "status",
        ),
    ),
}


FOREIGN_KEYS: tuple[tuple[str, str, str, str], ...] = (
    ("warehouses", "region_id", "regions", "region_id"),
    ("routes", "warehouse_id", "warehouses", "warehouse_id"),
    ("routes", "region_id", "regions", "region_id"),
    ("salespeople", "region_id", "regions", "region_id"),
    ("outlets", "region_id", "regions", "region_id"),
    ("outlets", "route_id", "routes", "route_id"),
    ("outlets", "salesperson_id", "salespeople", "salesperson_id"),
    ("orders", "outlet_id", "outlets", "outlet_id"),
    ("orders", "region_id", "regions", "region_id"),
    ("orders", "warehouse_id", "warehouses", "warehouse_id"),
    ("orders", "route_id", "routes", "route_id"),
    ("orders", "salesperson_id", "salespeople", "salesperson_id"),
    ("order_lines", "order_id", "orders", "order_id"),
    ("order_lines", "product_id", "products", "product_id"),
    ("deliveries", "order_id", "orders", "order_id"),
    ("inventory_snapshots", "warehouse_id", "warehouses", "warehouse_id"),
    ("inventory_snapshots", "product_id", "products", "product_id"),
    ("returns_credit_notes", "order_id", "orders", "order_id"),
    ("returns_credit_notes", "order_line_id", "order_lines", "order_line_id"),
    ("returns_credit_notes", "outlet_id", "outlets", "outlet_id"),
    ("returns_credit_notes", "product_id", "products", "product_id"),
)


def open_source_read_only(db_path: Path) -> sqlite3.Connection:
    """Open the operational database with SQLite write operations disabled."""

    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def source_fingerprint(db_path: Path) -> str:
    digest = hashlib.sha256()
    with db_path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _result(
    check_id: str,
    severity: Severity,
    passed: bool,
    observed: object,
    expected: object,
    message: str,
) -> QualityResult:
    return QualityResult(check_id, severity, passed, str(observed), str(expected), message)


def _count_delay_conflicts(connection: sqlite3.Connection) -> int:
    rows = connection.execute(
        "SELECT telematics_vendor, planned_arrival, actual_arrival, delay_minutes FROM deliveries"
    ).fetchall()
    conflicts = 0
    for row in rows:
        try:
            planned = datetime.strptime(row["planned_arrival"], "%Y-%m-%d %H:%M:%S")
            actual_format = (
                "%Y-%m-%d %H:%M:%S"
                if row["telematics_vendor"] == "TELEMATICS_A"
                else "%d-%b-%Y %I:%M %p"
            )
            actual = datetime.strptime(row["actual_arrival"], actual_format)
            derived_delay = int((actual - planned).total_seconds() / 60)
            if abs(derived_delay - row["delay_minutes"]) > 1:
                conflicts += 1
        except (TypeError, ValueError):
            conflicts += 1
    return conflicts


def validate_source(db_path: Path, *, check_csv_parity: bool = True) -> list[QualityResult]:
    """Validate source contracts, keys, parity, and known metric-affecting conflicts."""

    results: list[QualityResult] = []
    with open_source_read_only(db_path) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        results.append(
            _result(
                "sqlite_integrity",
                "BLOCKING",
                integrity == "ok",
                integrity,
                "ok",
                "SQLite integrity check must pass.",
            )
        )

        actual_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for table, contract in CONTRACTS.items():
            table_present = table in actual_tables
            results.append(
                _result(
                    f"schema.table.{table}",
                    "BLOCKING",
                    table_present,
                    table_present,
                    True,
                    f"Required source table `{table}` must exist.",
                )
            )
            if not table_present:
                continue
            actual_columns = tuple(
                row[1]
                for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
            )
            missing = sorted(set(contract.required_columns) - set(actual_columns))
            results.append(
                _result(
                    f"schema.columns.{table}",
                    "BLOCKING",
                    not missing,
                    ", ".join(missing) if missing else "none",
                    "none missing",
                    f"All contracted headers for `{table}` must be present.",
                )
            )
            key_expr = ", ".join(f'"{key}"' for key in contract.primary_key)
            duplicates = connection.execute(
                f'SELECT COUNT(*) FROM (SELECT {key_expr}, COUNT(*) n FROM "{table}" '
                f"GROUP BY {key_expr} HAVING COUNT(*) > 1)"
            ).fetchone()[0]
            results.append(
                _result(
                    f"grain.unique.{table}",
                    "BLOCKING",
                    duplicates == 0,
                    duplicates,
                    0,
                    f"`{table}` must be unique at {contract.grain}.",
                )
            )

        for child, child_key, parent, parent_key in FOREIGN_KEYS:
            orphans = connection.execute(
                f'SELECT COUNT(*) FROM "{child}" c LEFT JOIN "{parent}" p '
                f'ON c."{child_key}" = p."{parent_key}" '
                f'WHERE c."{child_key}" IS NOT NULL AND p."{parent_key}" IS NULL'
            ).fetchone()[0]
            results.append(
                _result(
                    f"relationship.{child}.{child_key}",
                    "BLOCKING",
                    orphans == 0,
                    orphans,
                    0,
                    f"`{child}.{child_key}` must resolve to `{parent}.{parent_key}`.",
                )
            )

        total_lines, short_lines = connection.execute(
            "SELECT COUNT(*), SUM(delivered_qty < ordered_qty) FROM order_lines"
        ).fetchone()
        results.append(
            _result(
                "business.strict_in_full_coverage",
                "WARNING",
                short_lines < total_lines,
                f"{short_lines}/{total_lines} lines short",
                "at least one full line",
                "Every supplied order line is short, forcing strict in-full and OTIF to 0%.",
            )
        )

        delay_conflicts = _count_delay_conflicts(connection)
        results.append(
            _result(
                "business.delay_conflict",
                "WARNING",
                delay_conflicts == 0,
                delay_conflicts,
                0,
                "Stored delay often conflicts with planned/actual timestamps; "
                "parsed timestamps are primary.",
            )
        )

        negative_returns = connection.execute(
            "SELECT COUNT(*) FROM returns_credit_notes WHERE return_qty < 0"
        ).fetchone()[0]
        results.append(
            _result(
                "business.return_signs",
                "WARNING",
                negative_returns == 0,
                negative_returns,
                0,
                "Negative return quantities require absolute-quantity normalization.",
            )
        )

        missing_approval_dates = connection.execute(
            "SELECT COUNT(*) FROM returns_credit_notes "
            "WHERE approval_date IS NULL OR TRIM(approval_date) = ''"
        ).fetchone()[0]
        total_returns = connection.execute(
            "SELECT COUNT(*) FROM returns_credit_notes"
        ).fetchone()[0]
        results.append(
            _result(
                "business.approval_date_coverage",
                "WARNING",
                missing_approval_dates < total_returns,
                f"{missing_approval_dates}/{total_returns} missing",
                "not completely missing",
                "Approval status is usable, but approval_date is entirely unavailable.",
            )
        )

        test_outlets = connection.execute(
            """
            SELECT COUNT(*) FROM outlets
            WHERE outlet_code LIKE 'TST%'
               OR lower(outlet_name) LIKE '%test%'
               OR lower(outlet_name) LIKE '%migration%'
               OR lower(outlet_name) LIKE '%dummy%'
            """
        ).fetchone()[0]
        results.append(
            _result(
                "business.test_outlets",
                "WARNING",
                test_outlets == 0,
                test_outlets,
                0,
                "Test/migration outlets are excluded from service populations.",
            )
        )

        header_mismatches = connection.execute(
            """
            SELECT COUNT(*)
            FROM orders o
            JOIN (SELECT order_id, SUM(line_value_inr) line_sum
                  FROM order_lines GROUP BY order_id) l USING (order_id)
            WHERE ABS(o.order_value_gross_inr - l.line_sum) > 0.01
            """
        ).fetchone()[0]
        results.append(
            _result(
                "reconciliation.order_header_to_lines",
                "INFO",
                header_mismatches == 0,
                header_mismatches,
                0,
                "The documented header mismatch is not present in this supplied database.",
            )
        )

    if check_csv_parity:
        results.extend(validate_csv_parity(db_path.parent / "csv", db_path))
    return results


def validate_csv_parity(csv_dir: Path, db_path: Path) -> list[QualityResult]:
    """Confirm optional CSV copies have the same headers and row counts as SQLite."""

    if not csv_dir.is_dir():
        return [
            _result(
                "csv.parity.available",
                "INFO",
                True,
                "not supplied",
                "optional",
                "CSV parity was skipped because the optional CSV directory is absent.",
            )
        ]

    results: list[QualityResult] = []
    with open_source_read_only(db_path) as connection:
        for table, contract in CONTRACTS.items():
            csv_path = csv_dir / f"{table}.csv"
            if not csv_path.is_file():
                results.append(
                    _result(
                        f"csv.file.{table}",
                        "WARNING",
                        False,
                        "missing",
                        "present when CSV pack is supplied",
                        f"Optional CSV copy for `{table}` is missing.",
                    )
                )
                continue
            with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.reader(handle)
                header = tuple(next(reader))
                csv_rows = sum(1 for _ in reader)
            sqlite_rows = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            results.append(
                _result(
                    f"csv.headers.{table}",
                    "WARNING",
                    header == contract.required_columns,
                    ",".join(header),
                    ",".join(contract.required_columns),
                    f"CSV headers for `{table}` must match the contracted order exactly.",
                )
            )
            results.append(
                _result(
                    f"csv.rows.{table}",
                    "WARNING",
                    csv_rows == sqlite_rows,
                    csv_rows,
                    sqlite_rows,
                    f"CSV and SQLite control totals for `{table}` should reconcile.",
                )
            )
    return results
