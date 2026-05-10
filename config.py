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

    # Drop hits whose latest trade is older than this many hours.
    # 24h is tuned to keep "yesterday's post-market gappers that haven't traded
    # yet this pre-market" alive. At 04:30 ET pre-market, a stock whose only
    # recent print is yesterday's 16:14 ET post-market trade is ~12.25h old —
    # at the previous 12h default it was being silently dropped, which is why
    # pre-market scans came up empty even on days with obvious gappers.
    # The freshness is still surfaced per-hit via the `(HH:MM EDT)` suffix and
    # the "Markets closed" header note when applicable.
    max_hit_age_hours: int = 24

    # WebSocket trade-stream listening window for pre-/post-market scans.
    # 0 disables the WS overlay entirely (HTTP-only, current behavior).
    # 20–30 seconds is enough to capture live extended-hours prints on the
    # active names without slowing the scan to a crawl. The All-In-One plan
    # supports /ws/us; smaller plans should keep this at 0.
    ws_collect_seconds: int = 20

    scan_hour: int = 11
    scan_minute: int = 30
    scan_timezone: str = "Europe/Nicosia"

    # Post-market scan: ~23:30 Europe/Nicosia = ~16:30 ET, ~30 min after US close,
    # long enough for most after-hours earnings prints to land.
    post_scan_hour: int = 23
    post_scan_minute: int = 30

    override_universe: str = ""

    log_level: str = "INFO"

    @property
    def override_tickers(self) -> list[str]:
        if not self.override_universe.strip():
            return []
        return [t.strip().upper() for t in self.override_universe.split(",") if t.strip()]
