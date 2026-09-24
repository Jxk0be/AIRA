"""Settings, loaded from the repo-root .env (see .env.example)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]

# Also put the .env into the real process environment, not just into Settings:
# `secret_ref = "env:NAME"` resolves against os.environ, because in a hosted
# deploy that is where a secret manager will have put it.
load_dotenv(REPO_ROOT / ".env", override=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://postgres:postgres@127.0.0.1:54322/postgres"

    anthropic_api_key: str = ""
    agent_model: str = "claude-sonnet-5"
    utility_model: str = "claude-haiku-4-5-20251001"

    voyage_api_key: str = ""
    embedding_provider: str = "voyage"
    embedding_model: str = "voyage-4"
    embedding_dim: int = 1024
    # Requests per minute to hold the embedding provider to. 0 means no
    # pacing; set it when the account has a low ceiling, so a big ingest waits
    # its turn instead of burning its retries on 429s.
    embedding_max_rpm: int = 0

    api_host: str = "127.0.0.1"
    api_port: int = 8000
    cors_origins: str = "http://localhost:5173"
    # Where the shop owner's app lives. Every deep link and unsubscribe link in
    # an email is built from this, so it has to be the address they can
    # actually open, not the API's.
    app_base_url: str = "http://localhost:5173"

    # "console" prints every notification instead of sending it, whatever
    # channel was asked for. The default, because a dev machine and the test
    # suite must be able to run the whole pipeline — prefs, quiet hours,
    # logging — without anything leaving the building. Set "live" to send.
    notify_transport: str = "console"
    notify_from_email: str = ""
    notify_from_name: str = "AIRA"
    resend_api_key: str = ""
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""

    # How stale a sync has to be before we stop sending a digest built on it
    # and send "we could not reach your system" instead.
    digest_stale_hours: int = 48

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def sync_database_url(self) -> str:
        """The same database, on a driver Alembic can use synchronously."""
        return self.database_url.replace("+asyncpg", "")


@lru_cache
def get_settings() -> Settings:
    return Settings()
