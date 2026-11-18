"""Application settings (loaded from .env, independent of the current working directory)."""
from __future__ import annotations

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
SQLITE_PREFIX = "sqlite+aiosqlite:///"
UNSAFE_API_KEYS = {"", "change-me", "changeme", "admin", "password", "secret"}
MIN_API_KEY_LENGTH = 16


class Settings(BaseSettings):
    bot_token: str = ""
    owner_id: int = 8399658960
    database_url: str = SQLITE_PREFIX + (BASE_DIR / "database" / "mega_market.db").as_posix()
    platform_name: str = "MEGA MARKET TG"
    default_currency: str = "DZD"
    commission_percent: float = 5.0
    support_chat_id: int | None = None
    channel_id: int | None = None
    admin_api_key: str = "change-me"
    web_host: str = "127.0.0.1"
    web_port: int = 8080

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_prefix="",
        extra="ignore",
        env_ignore_empty=True,
    )

    @field_validator("database_url")
    @classmethod
    def _make_sqlite_path_absolute(cls, value: str) -> str:
        """`./database/x.db` must always mean the project folder, never the CWD."""
        if value.startswith(SQLITE_PREFIX):
            raw = value[len(SQLITE_PREFIX):]
            if raw and raw != ":memory:" and not Path(raw).is_absolute() and not raw.startswith("/"):
                return SQLITE_PREFIX + (BASE_DIR / raw).resolve().as_posix()
        return value

    @property
    def admin_api_key_is_safe(self) -> bool:
        key = self.admin_api_key.strip()
        return key.lower() not in UNSAFE_API_KEYS and len(key) >= MIN_API_KEY_LENGTH


settings = Settings()
