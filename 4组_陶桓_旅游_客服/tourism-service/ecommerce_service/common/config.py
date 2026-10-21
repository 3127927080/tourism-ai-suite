from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    ecom_database_url: str = (
        "postgresql+psycopg://customer_service:customer_service@192.168.200.170:5432/ecommerce"
    )
    jwt_secret: str = "replace-this-in-production"
    jwt_algorithm: str = "HS256"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
