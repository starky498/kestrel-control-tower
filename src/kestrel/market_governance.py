"""Governed human decisions for conservative BazaarPulse entity resolution.

The YAML decision file is intentionally source controlled and read-only at runtime.  It can
resolve an ambiguous but plausible candidate or reject a listing; it cannot weaken the automatic
identity guardrails by joining records with a low score or a known brand/pack conflict.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Literal

import yaml

from kestrel.ingestion.bazaarpulse import (
    Listing,
    ProductCandidate,
    ProductMatch,
    product_identity_conflicts,
    score_product_candidate,
)

DecisionAction = Literal["match", "reject"]
_DECISION_KEYS = {
    "listing_id",
    "action",
    "sku_code",
    "reviewer",
    "reviewed_on",
    "note",
}


class MatchGovernanceError(ValueError):
    """Raised when a reviewed decision is incomplete, contradictory, or unsafe."""


@dataclass(frozen=True, slots=True)
class MatchDecision:
    """One attributable match or rejection decision loaded from governed YAML."""

    listing_id: str
    action: DecisionAction
    sku_code: str | None
    reviewer: str
    reviewed_on: date
    note: str


def _required_text(record: dict[str, object], key: str, index: int) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise MatchGovernanceError(f"Decision {index} requires a non-empty `{key}` field")
    return value.strip()


def _review_date(value: object, index: int) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as error:
            raise MatchGovernanceError(
                f"Decision {index} `reviewed_on` must use YYYY-MM-DD"
            ) from error
    raise MatchGovernanceError(f"Decision {index} requires a `reviewed_on` date")


def load_match_decisions(path: str | Path) -> dict[str, MatchDecision]:
    """Load and strictly validate a source-controlled match decision registry."""

    source = Path(path)
    if not source.is_file():
        raise MatchGovernanceError(f"Competitor match decision file not found: {source}")
    payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise MatchGovernanceError("Competitor match decisions require `schema_version: 1`")
    records = payload.get("decisions")
    if not isinstance(records, list):
        raise MatchGovernanceError("Competitor match decisions require a `decisions` list")

    decisions: dict[str, MatchDecision] = {}
    for index, raw_record in enumerate(records, start=1):
        if not isinstance(raw_record, dict):
            raise MatchGovernanceError(f"Decision {index} must be a YAML mapping")
        record = {str(key): value for key, value in raw_record.items()}
        unknown = set(record) - _DECISION_KEYS
        if unknown:
            raise MatchGovernanceError(
                f"Decision {index} has unsupported fields: {', '.join(sorted(unknown))}"
            )
        listing_id = _required_text(record, "listing_id", index)
        if listing_id in decisions:
            raise MatchGovernanceError(f"Duplicate decision for listing {listing_id}")
        action = _required_text(record, "action", index)
        if action not in ("match", "reject"):
            raise MatchGovernanceError(
                f"Decision {index} action must be either `match` or `reject`"
            )
        sku_value = record.get("sku_code")
        sku_code = sku_value.strip() if isinstance(sku_value, str) and sku_value.strip() else None
        if action == "match" and sku_code is None:
            raise MatchGovernanceError(f"Match decision {index} requires `sku_code`")
        if action == "reject" and sku_code is not None:
            raise MatchGovernanceError(f"Reject decision {index} must not set `sku_code`")
        decisions[listing_id] = MatchDecision(
            listing_id=listing_id,
            action=action,  # type: ignore[arg-type]
            sku_code=sku_code,
            reviewer=_required_text(record, "reviewer", index),
            reviewed_on=_review_date(record.get("reviewed_on"), index),
            note=_required_text(record, "note", index),
        )
    return decisions


def apply_match_decisions(
    listings: list[Listing],
    automatic_matches: list[ProductMatch],
    products: list[ProductCandidate],
    decisions: dict[str, MatchDecision],
    *,
    decision_source: str,
    minimum_manual_confidence: float = 0.80,
) -> list[ProductMatch]:
    """Apply reviewed decisions without bypassing weak-match and identity gates.

    Decisions for listings absent from the current crawl remain in the registry for history but
    are not applied.  A target SKU must exist, score at least ``minimum_manual_confidence``, and
    have no known brand or pack conflict.
    """

    if not 0 <= minimum_manual_confidence <= 1:
        raise ValueError("minimum_manual_confidence must be between zero and one")
    listing_by_id = {listing.listing_id: listing for listing in listings}
    match_by_id = {match.listing_id: match for match in automatic_matches}
    if len(listing_by_id) != len(listings):
        raise MatchGovernanceError("Current listings contain duplicate listing IDs")
    if set(listing_by_id) != set(match_by_id) or len(match_by_id) != len(automatic_matches):
        raise MatchGovernanceError("Automatic matches must contain one outcome per listing")
    product_by_sku = {str(product.sku_code): product for product in products}
    if len(product_by_sku) != len(products):
        raise MatchGovernanceError("Product candidates contain duplicate SKU codes")

    governed: list[ProductMatch] = []
    for listing in listings:
        automatic = match_by_id[listing.listing_id]
        decision = decisions.get(listing.listing_id)
        if decision is None:
            governed.append(automatic)
            continue

        algorithm_status = automatic.algorithm_status or automatic.status
        algorithm_reason = automatic.algorithm_reason or automatic.reason
        if decision.action == "reject":
            governed.append(
                replace(
                    automatic,
                    status="rejected",
                    product_id=None,
                    sku_code=None,
                    reason=f"Listing rejected by {decision.reviewer}: {decision.note}",
                    provenance="manual_rejection",
                    algorithm_status=algorithm_status,
                    algorithm_reason=algorithm_reason,
                    decision_source=decision_source,
                    reviewer=decision.reviewer,
                    reviewed_on=decision.reviewed_on,
                    review_note=decision.note,
                )
            )
            continue

        assert decision.sku_code is not None
        product = product_by_sku.get(decision.sku_code)
        if product is None:
            raise MatchGovernanceError(
                f"Listing {listing.listing_id} decision references unknown SKU "
                f"{decision.sku_code}"
            )
        confidence = score_product_candidate(listing, product)
        conflicts = product_identity_conflicts(listing, product)
        if conflicts:
            raise MatchGovernanceError(
                f"Listing {listing.listing_id} cannot be matched to {decision.sku_code}: "
                + "; ".join(conflicts)
            )
        if confidence < minimum_manual_confidence:
            raise MatchGovernanceError(
                f"Listing {listing.listing_id} cannot be matched to {decision.sku_code}: "
                f"reviewed candidate score {confidence:.3f} is below the "
                f"{minimum_manual_confidence:.3f} safety threshold"
            )
        governed.append(
            replace(
                automatic,
                status="matched",
                confidence=confidence,
                product_id=product.product_id,
                sku_code=product.sku_code,
                reason=(
                    f"Reviewed match approved by {decision.reviewer} at score "
                    f"{confidence:.3f}: {decision.note}"
                ),
                provenance="manual_match",
                algorithm_status=algorithm_status,
                algorithm_reason=algorithm_reason,
                decision_source=decision_source,
                reviewer=decision.reviewer,
                reviewed_on=decision.reviewed_on,
                review_note=decision.note,
            )
        )
    return governed
