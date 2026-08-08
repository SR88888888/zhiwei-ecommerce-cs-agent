from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "PDD Customer Service Agent MVP"
    api_v1_prefix: str = "/api/v1"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"
    llm_timeout_seconds: float = 20.0
    llm_enabled: bool = True
    pdd_adapter_mode: str = "database"
    pdd_gateway_base_url: str = ""
    pdd_gateway_token: str = ""
    database_url: str = ""
    redis_url: str = ""
    memory_shop_id: str = "default"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()