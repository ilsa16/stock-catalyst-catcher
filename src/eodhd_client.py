from __future__ import annotations

import logging
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .db import Database

log = logging.getLogger(__name__)

BASE_URL = "https://eodhd.com/api"


class CreditCapExceeded(RuntimeError):
    """Raised when a paid call would push us past the configured daily cap."""


class EODHDClient:
    """
    Async EODHD wrapper.

    Tracks credit spend in `api_credits`. Callers can mark a call as `essential`
    (quotes, screener) — only those are allowed once we cross 80% of the cap;
    non-essential calls (news) are blocked there. Hard stop at 100%.
    """

    def __init__(
        self,
        http: httpx.AsyncClient,
        api_key: str,
        db: Database,
        daily_cap: int,
    ) -> None:
        self._http = http
        self._key = api_key
        self._db = db
        self._cap = daily_cap

    async def _check_budget(self, cost: int, *, essential: bool) -> None:
        used = await self._db.credits_used_today()
        if used + cost > self._cap:
            raise CreditCapExceeded(f"would exceed daily cap ({used}+{cost} > {self._cap})")
        if not essential and used + cost > int(self._cap * 0.8):
            raise CreditCapExceeded(
                f"non-essential call blocked above 80% of cap ({used}+{cost} > {int(self._cap * 0.8)})"
            )

    async def _request(
        self,
        path: str,
        *,
        params: dict[str, Any],
        cost: int,
        essential: bool,
    ) -> Any:
        await self._check_budget(cost, essential=essential)
        params = {**params, "api_token": self._key, "fmt": "json"}
        url = f"{BASE_URL}{path}"

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(4),
            wait=wait_exponential(multiplier=1, min=1, max=8),
            retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
            reraise=True,
        ):
            with attempt:
                resp = await self._http.get(url, params=params)
                if resp.status_code >= 500:
                    resp.raise_for_status()
                if resp.status_code == 429:
                    resp.raise_for_status()
                resp.raise_for_status()
                data = resp.json()

        await self._db.add_credits(cost)
        return data

    # ---------- public ----------

    async def screener(
        self,
        *,
        market_cap_min: float,
        price_min: float,
        avg_vol_min: float,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """
        EODHD screener. Returns the raw `data` list of dict rows.
        Filters: US exchange, MC, last close, ADV (5d).
        """
        filters = (
            f'[["exchange","=","us"],'
            f'["market_capitalization",">",{market_cap_min}],'
            f'["adjusted_close",">",{price_min}],'
            f'["avgvol_5d",">",{avg_vol_min}]]'
        )
        params = {
            "filters": filters,
            "limit": limit,
            "offset": offset,
        }
        payload = await self._request("/screener", params=params, cost=5, essential=True)
        if isinstance(payload, dict) and "data" in payload:
            return list(payload["data"])
        if isinstance(payload, list):
            return payload
        return []

    async def live_batch(self, tickers: list[str]) -> list[dict[str, Any]]:
        """
        Live v2 (US extended quotes) for a batch of US tickers.
        Endpoint: /api/us-quote-delayed?s=T1,T2,...

        The v2 envelope is `{"meta": ..., "data": {ticker: row, ...}, "links": ...}`.
        We unwrap to a flat list of row dicts, copying the dict key into a `code`
        field so downstream parsing has a canonical ticker.
        """
        if not tickers:
            return []
        payload = await self._request(
            "/us-quote-delayed",
            params={"s": ",".join(tickers)},
            cost=len(tickers),
            essential=True,
        )
        rows: list[dict[str, Any]] = []
        if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
            for key, row in payload["data"].items():
                if not isinstance(row, dict):
                    continue
                r = dict(row)
                r.setdefault("code", key)
                rows.append(r)
        elif isinstance(payload, list):
            rows = [r for r in payload if isinstance(r, dict)]
        elif isinstance(payload, dict):
            rows = [payload]
        return rows

    async def top_news(self, ticker: str) -> dict[str, Any] | None:
        """Single most recent news item for a ticker. Non-essential (gated above 80%)."""
        try:
            data = await self._request(
                "/news",
                params={"s": ticker, "limit": 1},
                cost=5,
                essential=False,
            )
        except CreditCapExceeded:
            log.info("news fetch skipped for %s: credit cap", ticker)
            return None
        if isinstance(data, list) and data:
            return data[0]
        return None
