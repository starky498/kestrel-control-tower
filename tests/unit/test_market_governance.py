from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from kestrel.ingestion.bazaarpulse import (
    Listing,
    ProductCandidate,
    ProductMatch,
    normalize_title,
)
from kestrel.market_governance import (
    MatchGovernanceError,
    apply_match_decisions,
    load_match_decisions,
)


def _listing(*, brand: str = "Kestrel", pack_value: float = 200) -> Listing:
    return Listing(
        listing_id="42",
        city="Mumbai",
        retailer="FreshCart",
        raw_title="Kestrel Apple Juice 200ml",
        normalized_title=normalize_title("Kestrel Apple Juice 200ml"),
        brand=brand,
        pack_value=pack_value,
        pack_uom="ML",
        category="Beverages",
        current_price_inr=48,
        mrp_inr=58,
        is_available=True,
        last_seen=date(2026, 6, 30),
        detail_path="/product/42.html",
        source_path="/city/mumbai/page/1.html",
    )


def _products() -> list[ProductCandidate]:
    return [
        ProductCandidate(
            1,
            "SKU-APPLE",
            "Kestrel Apple Juice 200ml",
            "Kestrel",
            "Beverages",
            200,
            "ML",
        ),
        ProductCandidate(
            2,
            "SKU-MANGO",
            "Kestrel Mango Juice 200ml",
            "Kestrel",
            "Beverages",
            200,
            "ML",
        ),
    ]


def _automatic() -> ProductMatch:
    return ProductMatch(
        listing_id="42",
        status="ambiguous",
        confidence=0.94,
        runner_up_confidence=0.92,
        product_id=None,
        sku_code=None,
        reason="Candidates are too close.",
        suggested_product_id=1,
        suggested_sku_code="SKU-APPLE",
        provenance="automatic_quarantine",
        algorithm_status="ambiguous",
    )


def test_loads_attributable_match_and_reject_decisions(tmp_path: Path) -> None:
    registry = tmp_path / "decisions.yml"
    registry.write_text(
        """
schema_version: 1
decisions:
  - listing_id: "42"
    action: match
    sku_code: SKU-APPLE
    reviewer: A. Reviewer
    reviewed_on: 2026-08-15
    note: Detail page confirms the apple variant.
  - listing_id: "99"
    action: reject
    reviewer: A. Reviewer
    reviewed_on: "2026-08-14"
    note: Marketplace bundle has no sellable equivalent.
""",
        encoding="utf-8",
    )

    decisions = load_match_decisions(registry)

    assert decisions["42"].sku_code == "SKU-APPLE"
    assert decisions["42"].reviewed_on == date(2026, 8, 15)
    assert decisions["99"].action == "reject"


def test_reviewed_match_resolves_plausible_ambiguity_with_full_provenance(
    tmp_path: Path,
) -> None:
    registry = tmp_path / "decisions.yml"
    registry.write_text(
        """
schema_version: 1
decisions:
  - listing_id: "42"
    action: match
    sku_code: SKU-APPLE
    reviewer: A. Reviewer
    reviewed_on: 2026-08-15
    note: Detail page confirms the apple variant.
""",
        encoding="utf-8",
    )

    governed = apply_match_decisions(
        [_listing()],
        [_automatic()],
        _products(),
        load_match_decisions(registry),
        decision_source="config/competitor_match_decisions.yml",
    )[0]

    assert governed.matched
    assert governed.sku_code == "SKU-APPLE"
    assert governed.provenance == "manual_match"
    assert governed.algorithm_status == "ambiguous"
    assert governed.algorithm_reason == "Candidates are too close."
    assert governed.reviewer == "A. Reviewer"
    assert governed.review_note == "Detail page confirms the apple variant."


@pytest.mark.parametrize(
    ("listing", "sku_code", "message"),
    [
        (_listing(brand="Bluepeak"), "SKU-APPLE", "brand differs"),
        (_listing(pack_value=500), "SKU-APPLE", "pack size or unit differs"),
    ],
)
def test_review_cannot_force_a_hard_identity_conflict(
    listing: Listing,
    sku_code: str,
    message: str,
) -> None:
    from kestrel.market_governance import MatchDecision

    decision = MatchDecision(
        listing_id="42",
        action="match",
        sku_code=sku_code,
        reviewer="A. Reviewer",
        reviewed_on=date(2026, 8, 15),
        note="Attempted override.",
    )

    with pytest.raises(MatchGovernanceError, match=message):
        apply_match_decisions(
            [listing],
            [_automatic()],
            _products(),
            {"42": decision},
            decision_source="config/competitor_match_decisions.yml",
        )


def test_rejection_is_attributable_and_no_longer_matched() -> None:
    from kestrel.market_governance import MatchDecision

    automatic = ProductMatch(
        listing_id="42",
        status="matched",
        confidence=1,
        runner_up_confidence=0.4,
        product_id=1,
        sku_code="SKU-APPLE",
        reason="Automatic acceptance.",
    )
    decision = MatchDecision(
        listing_id="42",
        action="reject",
        sku_code=None,
        reviewer="A. Reviewer",
        reviewed_on=date(2026, 8, 15),
        note="Listing is a multipack, not a single SKU.",
    )

    governed = apply_match_decisions(
        [_listing()],
        [automatic],
        _products(),
        {"42": decision},
        decision_source="config/competitor_match_decisions.yml",
    )[0]

    assert governed.status == "rejected"
    assert not governed.matched
    assert governed.product_id is None
    assert governed.provenance == "manual_rejection"
    assert governed.algorithm_status == "matched"


def test_registry_refuses_duplicate_or_incomplete_decisions(tmp_path: Path) -> None:
    registry = tmp_path / "decisions.yml"
    registry.write_text(
        """
schema_version: 1
decisions:
  - listing_id: "42"
    action: match
    reviewer: A. Reviewer
    reviewed_on: 2026-08-15
    note: Missing target SKU.
""",
        encoding="utf-8",
    )

    with pytest.raises(MatchGovernanceError, match="requires `sku_code`"):
        load_match_decisions(registry)
