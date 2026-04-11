from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Для локальной разработки: .env.development
    # На Railway: переменные из Dashboard (файл не нужен)
    model_config = SettingsConfigDict(
        env_file=(".env.development", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Cargo Bots"
    env: Literal["development", "staging", "production"] = "development"
    debug: bool = False
    auto_create_db: bool = True

    database_url: str = "postgresql+asyncpg://cargo:cargo@localhost:5432/cargo"
    redis_url: str = "redis://localhost:6379/0"

    @field_validator("database_url", mode="before")
    @classmethod
    def replace_postgres_scheme(cls, v: str) -> str:
        if isinstance(v, str) and v.startswith("postgresql://"):
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v

    admin_bot_token: str = ""
    client_bot_token: str = ""
    admin_secret_token: str = ""
    client_secret_token: str = ""
    admin_ids: list[int] = Field(default_factory=list)
    webhook_base_url: str | None = None

    storage_backend: Literal["local", "s3"] = "local"
    storage_bucket: str = "cargo-bots"
    storage_prefix: str = "imports"
    local_storage_path: str = "./storage"
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None
    aws_region: str = "ap-east-1"
    aws_s3_endpoint_url: str | None = None

    address_template_path: str = "./example_adress.txt"
    supplier_template_path: str | None = None

    sentry_dsn: str | None = None
    metrics_enabled: bool = True
    bot_message_rate_limit_per_second: float = 25.0
    task_always_eager: bool = False

    @field_validator("admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value: object) -> list[int]:
        if value is None or value == "":
            return []
        if isinstance(value, list):
            return [int(item) for item in value]
        if isinstance(value, str):
            return [int(item.strip()) for item in value.split(",") if item.strip()]
        raise TypeError("ADMIN_IDS must be a comma-separated string or a list.")

    @property
    def admin_webhook_url(self) -> str | None:
        return self._build_webhook("webhook/admin")

    @property
    def client_webhook_url(self) -> str | None:
        return self._build_webhook("webhook/client")

    def _build_webhook(self, suffix: str) -> str | None:
        if not self.webhook_base_url:
            return None
        return f"{self.webhook_base_url.rstrip('/')}/{suffix.lstrip('/')}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

