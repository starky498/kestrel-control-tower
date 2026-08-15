"""Read-only API for competitor match review and observation audit."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import FastAPI, HTTPException, Query

from kestrel.config import Settings
from kestrel.metrics.external import ExternalAnalyticsService


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Convert pandas extension values and dates to stable JSON-compatible records."""

    return json.loads(frame.to_json(orient="records", date_format="iso"))


def create_market_api(database_path: str | Path) -> FastAPI:
    """Create a read-only market-governance API bound to an analytical database."""

    database = Path(database_path)
    service = ExternalAnalyticsService(database)
    api = FastAPI(
        title="Kestrel competitor governance API",
        version="1.0.0",
        description="Read-only match review queue and append-only observation audit.",
    )

    @api.get("/health")
    def health() -> dict[str, object]:
        if not database.is_file():
            return {"status": "unavailable", "database": str(database)}
        return {"status": "ok", "sources": service.availability()}

    @api.get("/market/review-queue")
    def review_queue(
        city: str | None = None,
        limit: int = Query(default=100, ge=1, le=1_000),
    ) -> dict[str, object]:
        try:
            frame = service.competitor_review_queue(city=city, limit=limit)
        except (OSError, RuntimeError) as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        return {
            "count": len(frame),
            "items": _records(frame),
            "governance": frame.attrs.get("governance"),
            "unavailable_reason": frame.attrs.get("unavailable_reason"),
        }

    @api.get("/market/listings/{listing_id}/history")
    def listing_history(
        listing_id: str,
        limit: int = Query(default=100, ge=1, le=1_000),
    ) -> dict[str, object]:
        try:
            frame = service.competitor_observation_history(listing_id, limit=limit)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except (OSError, RuntimeError) as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        return {
            "listing_id": listing_id,
            "count": len(frame),
            "items": _records(frame),
            "unavailable_reason": frame.attrs.get("unavailable_reason"),
        }

    return api


app = create_market_api(Settings.load().analytics_db)
