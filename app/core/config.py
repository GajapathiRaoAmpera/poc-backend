from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    app_origin: str = "http://localhost:3000"
    environment: Literal["development", "test", "production"] = "development"

    @property
    def secure_cookies(self) -> bool:
        return self.environment not in ("development", "test")


@lru_cache
def get_settings() -> Settings:
    return Settings()
