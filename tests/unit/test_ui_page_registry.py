from kestrel.ui.app import PAGES


def test_dashboard_exposes_eight_distinct_governed_workspaces() -> None:
    assert tuple(PAGES) == (
        "Executive Command Center",
        "Service & Fulfilment",
        "Delivery & Exception Drivers",
        "Cold Chain & Inventory Risk",
        "Commercial Leakage & Logistics Cost",
        "Market & External Context",
        "Ask Kestrel",
        "Trust Center",
    )
    assert len(set(PAGES.values())) == 8
