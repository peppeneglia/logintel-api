"""Application settings, loaded from environment variables (and a local .env file)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Runtime
    app_env: str = "development"
    log_level: str = "INFO"

    # External services and credentials
    ors_api_key: str = ""
    ors_base_url: str = "https://api.openrouteservice.org"
    open_meteo_base_url: str = "https://api.open-meteo.com"
    open_elevation_base_url: str = "https://api.open-elevation.com"
    overpass_base_url: str = "https://overpass-api.de"
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_key: str = ""
    supabase_jwt_secret: str = ""
    upstash_redis_url: str = ""

    # Prediction engine
    sampling_interval_km: int = 50
    max_forecast_hours: int = 72
    alternative_delay_threshold_minutes: int = 15
    enable_special_elements: bool = True

    # Cache TTLs (seconds)
    cache_ttl_route: int = 86400  # 24 hours
    cache_ttl_weather: int = 3600  # 1 hour
    cache_ttl_osm: int = 604800  # 7 days

    # Resilience and alerting
    cb_failure_threshold: int = 5
    cb_recovery_timeout: int = 30
    alert_error_rate_pct: float = 10.0
    alert_latency_p95_ms: float = 3000.0
    alert_cache_rate_pct: float = 30.0
    alert_cb_open_seconds: int = 300


@lru_cache
def get_settings() -> Settings:
    return Settings()
