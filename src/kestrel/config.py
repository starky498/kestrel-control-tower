"""Runtime configuration with safe, explicit local defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ConfigurationError(RuntimeError):
    """Raised when a required runtime setting is missing or invalid."""


def _path_from_env(name: str, default: Path) -> Path:
    raw = os.getenv(name)
    return Path(raw).expanduser().resolve() if raw else default.resolve()


@dataclass(frozen=True)
class Settings:
    """Application settings loaded from environment variables."""

    project_root: Path
    source_db: Path
    analytics_db: Path
    runtime_dir: Path
    freight_cache: Path
    competitor_cache: Path
    freight_api_url: str
    freight_api_key: str | None
    bazaarpulse_base_url: str
    bazaarpulse_site_root: Path | None
    near_expiry_days: int
    competitor_match_threshold: int

    @classmethod
    def load(cls) -> Settings:
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        runtime_dir = _path_from_env("KESTREL_RUNTIME_DIR", PROJECT_ROOT / ".kestrel")
        bundled_db = PROJECT_ROOT / "data" / "source" / "data" / "kestrel_ops.db"
        site_root_raw = os.getenv("KESTREL_BAZAARPULSE_SITE_ROOT")
        bundled_site = PROJECT_ROOT / "data" / "source" / "bazaarpulse_site"
        site_root = (
            Path(site_root_raw).expanduser().resolve()
            if site_root_raw
            else bundled_site.resolve() if bundled_site.exists() else None
        )
        return cls(
            project_root=PROJECT_ROOT,
            source_db=_path_from_env("KESTREL_SOURCE_DB", bundled_db),
            analytics_db=_path_from_env(
                "KESTREL_ANALYTICS_DB", runtime_dir / "kestrel.duckdb"
            ),
            runtime_dir=runtime_dir,
            freight_cache=_path_from_env(
                "KESTREL_FREIGHT_CACHE", runtime_dir / "freight_invoices.json"
            ),
            competitor_cache=_path_from_env(
                "KESTREL_COMPETITOR_CACHE", runtime_dir / "bazaarpulse_listings.json"
            ),
            freight_api_url=os.getenv(
                "KESTREL_FREIGHT_API_URL", "http://127.0.0.1:8088"
            ).rstrip("/"),
            freight_api_key=os.getenv("KESTREL_FREIGHT_API_KEY"),
            bazaarpulse_base_url=os.getenv(
                "KESTREL_BAZAARPULSE_BASE_URL", "http://127.0.0.1:8080"
            ).rstrip("/"),
            bazaarpulse_site_root=site_root,
            near_expiry_days=int(os.getenv("KESTREL_NEAR_EXPIRY_DAYS", "30")),
            competitor_match_threshold=int(
                os.getenv("KESTREL_COMPETITOR_MATCH_THRESHOLD", "86")
            ),
        )

    def require_source_db(self) -> Path:
        if not self.source_db.is_file():
            raise ConfigurationError(
                "Kestrel source database not found. Set KESTREL_SOURCE_DB in .env to the "
                "absolute path of the supplied kestrel_ops.db."
            )
        return self.source_db

    def require_analytics_db(self) -> Path:
        if not self.analytics_db.is_file():
            raise ConfigurationError(
                "Analytical database not found. Run `make build` before starting the app."
            )
        return self.analytics_db

    def ensure_runtime_dirs(self) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.analytics_db.parent.mkdir(parents=True, exist_ok=True)
        self.freight_cache.parent.mkdir(parents=True, exist_ok=True)
        self.competitor_cache.parent.mkdir(parents=True, exist_ok=True)
