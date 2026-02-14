from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    app_env: str = "development"
    log_level: str = "INFO"
    ors_api_key: str = ""
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_key: str = ""
    upstash_redis_url: str = ""
    sampling_interval_km: int = 50
    max_forecast_hours: int = 72
    cache_ttl_route: int = 86400
    cache_ttl_weather: int = 3600

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
