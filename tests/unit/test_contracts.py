import sqlite3

from kestrel.contracts import CONTRACTS, _count_delay_conflicts


def test_all_thirteen_source_tables_have_explicit_grain_and_headers() -> None:
    assert len(CONTRACTS) == 13
    assert CONTRACTS["order_lines"].primary_key == ("order_line_id",)
    assert "case_pack_at_order" in CONTRACTS["order_lines"].required_columns
    assert CONTRACTS["inventory_snapshots"].grain.startswith(
        "one row per warehouse"
    )


def test_delay_conflict_parser_handles_both_vendor_formats() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE deliveries (
            telematics_vendor TEXT,
            planned_arrival TEXT,
            actual_arrival TEXT,
            delay_minutes INTEGER
        )
        """
    )
    connection.executemany(
        "INSERT INTO deliveries VALUES (?, ?, ?, ?)",
        [
            ("TELEMATICS_A", "2026-06-01 10:00:00", "2026-06-01 10:30:00", 30),
            ("TELEMATICS_B", "2026-06-01 10:00:00", "01-Jun-2026 10:15 AM", 15),
            ("TELEMATICS_A", "2026-06-01 10:00:00", "2026-06-01 10:20:00", 0),
        ],
    )

    assert _count_delay_conflicts(connection) == 1
