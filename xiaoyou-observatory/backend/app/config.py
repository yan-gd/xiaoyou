from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OBSERVATORY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "小悠 · 命轨观测台"
    environment: Literal["development", "production", "test"] = "production"
    database_path: Path = Path("/opt/xiaoyou-observatory/data/observatory.db")
    app_secret: str = Field(min_length=32)
    allowed_hosts: str = "xiaoyou.yoyoyan.cn,127.0.0.1,localhost"
    cookie_name: str = "xiaoyou_observatory_session"
    cookie_secure: bool = True
    session_minutes: int = Field(default=30, ge=5, le=1440)
    controller_path: Path = Path("/usr/local/sbin/xiaoyou-ctl")
    controller_timeout_seconds: int = Field(default=45, ge=5, le=180)
    mock_mode: bool = False
    static_dir: Path | None = None
    status_poll_seconds: float = Field(default=2.5, ge=1.0, le=30.0)
    login_attempts_per_10_minutes: int = Field(default=6, ge=3, le=30)
    trusted_proxy: bool = True

    @field_validator("allowed_hosts")
    @classmethod
    def validate_allowed_hosts(cls, value: str) -> str:
        hosts = [item.strip() for item in value.split(",") if item.strip()]
        if not hosts:
            raise ValueError("at least one allowed host is required")
        if "*" in hosts:
            raise ValueError("wildcard allowed host is forbidden")
        return ",".join(hosts)

    @property
    def allowed_host_list(self) -> list[str]:
        return [item.strip() for item in self.allowed_hosts.split(",") if item.strip()]

    def ensure_runtime_directories(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_runtime_directories()
    return settings
