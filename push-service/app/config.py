"""Configuration loaded from environment / .env (see .env.example)."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # VERA-cloud
    vera_api_base: str = ""
    vera_api_key: str = ""

    # APNs
    dry_run: bool = True
    apns_use_sandbox: bool = True
    apns_team_id: str = ""
    apns_key_id: str = ""
    apns_bundle_id: str = "com.example.kura"
    apns_auth_key_path: str = ""

    # Storage
    device_store_path: str = ":memory:"  # legacy JSON store (unused once DB is on)
    # SQLAlchemy URL. Empty -> local SQLite file. Set to a Neon/Postgres URL in
    # production (e.g. postgresql://user:pass@host/db?sslmode=require).
    database_url: str = ""

    # Provider auth
    provider_api_key: str = ""

    @property
    def apns_host(self) -> str:
        return (
            "https://api.sandbox.push.apple.com"
            if self.apns_use_sandbox
            else "https://api.push.apple.com"
        )


_settings: Settings | None = None


def get_settings() -> Settings:
    """Cached settings accessor (also overridable in tests via dependency_overrides)."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
