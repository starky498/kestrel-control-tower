"""Resilient, auditable external-source ingestion adapters."""

from kestrel.ingestion.bazaarpulse import (
    BazaarPulseCollector,
    Listing,
    ProductCandidate,
    ProductMatch,
    match_listings_to_products,
)
from kestrel.ingestion.freight import (
    FreightClient,
    FreightInvoice,
    FreightSyncMetadata,
    FreightSyncResult,
    load_last_good_cache,
)

__all__ = [
    "BazaarPulseCollector",
    "FreightClient",
    "FreightInvoice",
    "FreightSyncMetadata",
    "FreightSyncResult",
    "Listing",
    "ProductCandidate",
    "ProductMatch",
    "match_listings_to_products",
    "load_last_good_cache",
]
