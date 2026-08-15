"""Runtime configuration with safe, explicit local defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_MARKERS = (Path("pyproject.toml"), Path("config") / "metrics.yml")


class ConfigurationError(RuntimeError):
    """Raised when a required runtime setting is missing or invalid."""


def _is_project_root(path: Path) -> bool:
    return all((path / marker).is_file() for marker in PROJECT_MARKERS)


def _discover_project_root() -> Path:
    """Resolve the checkout root for source and non-editable wheel installations."""

    configured = os.getenv("KESTREL_PROJECT_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()

    origins = (Path.cwd().resolve(), Path(__file__).resolve().parent)
    visited: set[Path] = set()
    for origin in origins:
        for candidate in (origin, *origin.parents):
            if candidate in visited:
                continue
            visited.add(candidate)
            if _is_project_root(candidate):
                return candidate
    return Path.cwd().resolve()


def project_config_path(filename: str) -> Path:
    """Resolve a repository configuration file in source and installed runtimes.

    Docker and non-editable installations load Python modules from ``site-packages`` rather
    than ``src``. Resolving through the configured/discovered project root keeps the tracked
    configuration under ``<project>/config`` available in both layouts.
    """

    relative = Path(filename)
    if relative.is_absolute() or len(relative.parts) != 1 or relative.name != filename:
        raise ValueError("Configuration filename must be one plain relative filename")
    return _discover_project_root() / "config" / relative


def _path_from_env(name: str, default: Path) -> Path:
    raw = os.getenv(name)
    return Path(raw).expanduser().resolve() if raw else default.resolve()


def _bool_from_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be true or false")


def _bounded_float_from_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    try:
        value = default if raw is None else float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number") from exc
    if not 0 <= value <= 1:
        raise ConfigurationError(f"{name} must be between 0 and 1")
    return value


@dataclass(frozen=True)
class Settings:
    """Application settings loaded from environment variables."""

    project_root: Path
    source_db: Path
    analytics_db: Path
    runtime_dir: Path
    freight_cache: Path
    competitor_cache: Path
    weather_cache: Path
    holiday_cache: Path
    freight_api_url: str
    freight_api_key: str | None
    bazaarpulse_base_url: str
    weather_api_url: str
    holiday_api_url: str
    holiday_fallback_url: str
    bazaarpulse_site_root: Path | None
    near_expiry_days: int
    competitor_match_threshold: int
    nlq_semantic_enabled: bool
    nlq_model_path: Path
    nlq_min_confidence: float
    nlq_min_margin: float

    @classmethod
    def load(cls) -> Settings:
        project_root = _discover_project_root()
        load_dotenv(project_root / ".env", override=False)
        runtime_dir = _path_from_env("KESTREL_RUNTIME_DIR", project_root / ".kestrel")
        bundled_db = project_root / "data" / "source" / "data" / "kestrel_ops.db"
        site_root_raw = os.getenv("KESTREL_BAZAARPULSE_SITE_ROOT")
        bundled_site = project_root / "data" / "source" / "bazaarpulse_site"
        site_root = (
            Path(site_root_raw).expanduser().resolve()
            if site_root_raw
            else bundled_site.resolve() if bundled_site.exists() else None
        )
        return cls(
            project_root=project_root,
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
            weather_cache=_path_from_env(
                "KESTREL_WEATHER_CACHE", runtime_dir / "weather_daily.json"
            ),
            holiday_cache=_path_from_env(
                "KESTREL_HOLIDAY_CACHE", runtime_dir / "india_holidays.json"
            ),
            freight_api_url=os.getenv(
                "KESTREL_FREIGHT_API_URL", "http://127.0.0.1:8088"
            ).rstrip("/"),
            freight_api_key=os.getenv("KESTREL_FREIGHT_API_KEY"),
            bazaarpulse_base_url=os.getenv(
                "KESTREL_BAZAARPULSE_BASE_URL", "http://127.0.0.1:8080"
            ).rstrip("/"),
            weather_api_url=os.getenv(
                "KESTREL_WEATHER_API_URL",
                "https://archive-api.open-meteo.com/v1/archive",
            ).rstrip("/"),
            holiday_api_url=os.getenv(
                "KESTREL_HOLIDAY_API_URL",
                "https://date.nager.at/api/v3/PublicHolidays",
            ).rstrip("/"),
            holiday_fallback_url=os.getenv(
                "KESTREL_HOLIDAY_FALLBACK_URL",
                "https://calendar.google.com/calendar/ical/"
                "en.indian%23holiday%40group.v.calendar.google.com/public/basic.ics",
            ),
            bazaarpulse_site_root=site_root,
            near_expiry_days=int(os.getenv("KESTREL_NEAR_EXPIRY_DAYS", "30")),
            competitor_match_threshold=int(
                os.getenv("KESTREL_COMPETITOR_MATCH_THRESHOLD", "86")
            ),
            nlq_semantic_enabled=_bool_from_env("KESTREL_NLQ_SEMANTIC_ENABLED", True),
            nlq_model_path=_path_from_env(
                "KESTREL_NLQ_MODEL_PATH",
                runtime_dir / "models" / "all-MiniLM-L6-v2",
            ),
            nlq_min_confidence=_bounded_float_from_env(
                "KESTREL_NLQ_MIN_CONFIDENCE", 0.50
            ),
            nlq_min_margin=_bounded_float_from_env("KESTREL_NLQ_MIN_MARGIN", 0.08),
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
        self.weather_cache.parent.mkdir(parents=True, exist_ok=True)
        self.holiday_cache.parent.mkdir(parents=True, exist_ok=True)
        self.nlq_model_path.parent.mkdir(parents=True, exist_ok=True)
