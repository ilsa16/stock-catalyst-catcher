from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    eodhd_api_key: str = ""
    telegram_bot_token: str = ""

    eodhd_daily_credit_cap: int = 20000
    db_path: str = "catalyst.db"

    universe_market_cap_min: float = 1_000_000_000
    universe_price_min: float = 10.0
    universe_avg_vol_min: float = 100_000

    default_gap_threshold: float = 5.0

    scan_hour: int = 11
    scan_minute: int = 30
    scan_timezone: str = "Europe/Nicosia"

    override_universe: str = ""

    log_level: str = "INFO"

    @property
    def override_tickers(self) -> list[str]:
        if not self.override_universe.strip():
            return []
        return [t.strip().upper() for t in self.override_universe.split(",") if t.strip()]
