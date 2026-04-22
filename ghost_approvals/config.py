"""Runtime configuration loaded from environment / .env."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    telegram_bot_token: str = Field(..., alias="TELEGRAM_BOT_TOKEN")
    alchemy_api_key: str = Field(..., alias="ALCHEMY_API_KEY")
    etherscan_api_key: str = Field(..., alias="ETHERSCAN_API_KEY")
    groq_api_key: str = Field(..., alias="GROQ_API_KEY")

    goplus_api_key: str | None = Field(None, alias="GOPLUS_API_KEY")
    public_base_url: str = Field("https://ghostapprovals.xyz", alias="PUBLIC_BASE_URL")
    db_path: Path = Field(Path("./data/ghost.db"), alias="DB_PATH")
    log_level: str = Field("INFO", alias="LOG_LEVEL")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )


def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
