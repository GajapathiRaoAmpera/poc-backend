from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mongodb_uri: str
    mongodb_db: str = "ampera"
    app_origin: str = "http://localhost:3000"
    # Fail closed: an unset ENVIRONMENT means production (Secure cookies, no reset links in logs).
    environment: Literal["development", "test", "production"] = "production"
    # Comma-separated IPs of reverse proxies (e.g. the Next.js server) allowed to set
    # X-Forwarded-For. Empty = trust nobody; client IP is the TCP peer.
    trusted_proxy_ips: str = ""

    @property
    def trusted_proxies(self) -> list[str]:
        return [ip.strip() for ip in self.trusted_proxy_ips.split(",") if ip.strip()]

    @property
    def secure_cookies(self) -> bool:
        return self.environment not in ("development", "test")


@lru_cache
def get_settings() -> Settings:
    return Settings()
